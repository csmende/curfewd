"""D-Bus-free stateful enforcement runtime for curfewd (Spec 005)."""

import hmac
import logging
import os
import secrets
import time
from collections.abc import Callable
from datetime import datetime
from typing import Any

from curfewd.engine.applimits import AppLimitsMonitor
from curfewd.engine.evaluator import (
    EvaluationResult,
    apply_app_grant,
    apply_grant,
    apply_today_override,
    build_user_status_dict,
    evaluate_tick,
)
from curfewd.engine.sessions import (
    LogindSessionMonitor,
    discover_desktop_users,
    lookup_user_record,
)
from curfewd.history import HistoryStore
from curfewd.models import (
    DaemonConfig,
    PauseState,
    UserAccountingState,
    UserPolicy,
)
from curfewd.parsing import validate_username
from curfewd.storage import AtomicJsonStore

logger = logging.getLogger("curfewd.runtime")

# Re-challenge desktop tray screensaver state every N seconds
_CHALLENGE_INTERVAL_SEC = 15.0
# Minimum interval between repetitive lock retries while locked_out and active
_LOCK_RETRY_INTERVAL_SEC = 3.0


class UserNotFoundLookupError(LookupError):
    """Raised when a requested Linux user neither exists in passwd nor has a saved policy."""


class EnforcementRuntime:
    """Owns in-memory policy/state caches, single-pass tick accounting, and enforcement logic."""

    def __init__(
        self,
        store: AtomicJsonStore,
        history: HistoryStore,
        session_monitor: LogindSessionMonitor | None = None,
        app_limits_monitor: AppLimitsMonitor | None = None,
        now_fn: Callable[[], datetime] | None = None,
        on_status_changed: Callable[[str, dict[str, Any]], None] | None = None,
        on_policy_changed: Callable[[str], None] | None = None,
        on_client_notification: Callable[[str, dict[str, Any]], None] | None = None,
        on_screensaver_challenge: Callable[[str, str], None] | None = None,
    ) -> None:
        self._store = store
        self._history = history
        self._sessions = session_monitor or LogindSessionMonitor()
        self._app_limits = app_limits_monitor or AppLimitsMonitor()
        self._now_fn = now_fn or (lambda: datetime.now().astimezone())
        self._on_status_changed = on_status_changed
        self._on_policy_changed = on_policy_changed
        self._on_client_notification = on_client_notification
        self._on_screensaver_challenge = on_screensaver_challenge

        self._store.ensure_dirs()
        self._config: DaemonConfig = self._store.load_daemon_config()
        if not self._store.daemon_config_path or not os.path.isfile(self._store.daemon_config_path):
            self._store.save_daemon_config(self._config)

        # In-memory caches
        self._policies: dict[str, UserPolicy] = {}
        self._states: dict[str, UserAccountingState] = {}
        self._last_eval: dict[str, EvaluationResult] = {}
        self._last_sessions: dict[str, list[dict[str, Any]]] = {}
        self._active_limited_apps: dict[str, list[str]] = {}
        self._active_limited_patterns: dict[str, list[str]] = {}

        # Client screensaver challenge/response state
        self._pending_nonces: dict[str, str] = {}
        self._last_challenge_mono: dict[str, float] = {}
        self._tray_screensaver_active: dict[str, bool] = {}

        # Lockout countdown & save timers
        self._countdowns: dict[str, int] = {}
        self._last_lock_attempt_mono: dict[str, float] = {}
        self._last_tick_mono: float | None = None
        self._last_save_mono: float = time.monotonic()

    @property
    def config(self) -> DaemonConfig:
        return self._config

    @property
    def store(self) -> AtomicJsonStore:
        return self._store

    @property
    def history(self) -> HistoryStore:
        return self._history

    @property
    def sessions(self) -> LogindSessionMonitor:
        return self._sessions

    def now_local(self) -> datetime:
        return self._now_fn()

    def last_sessions_for(self, username: str) -> list[dict[str, Any]]:
        return list(self._last_sessions.get(username, []))

    def _emit_status(self, username: str, status: dict[str, Any]) -> None:
        if self._on_status_changed is not None:
            self._on_status_changed(username, status)

    def _emit_policy_changed(self, username: str) -> None:
        if self._on_policy_changed is not None:
            self._on_policy_changed(username)

    def _emit_notification(self, username: str, payload: dict[str, Any]) -> None:
        if self._on_client_notification is not None:
            self._on_client_notification(username, payload)

    def _emit_challenge(self, username: str, nonce: str) -> None:
        if self._on_screensaver_challenge is not None:
            self._on_screensaver_challenge(username, nonce)

    def get_policy(self, username: str) -> UserPolicy:
        safe = validate_username(username)
        if safe not in self._policies:
            self._policies[safe] = self._store.load_user_policy(safe)
        return self._policies[safe]

    def get_state(self, username: str) -> UserAccountingState:
        safe = validate_username(username)
        if safe not in self._states:
            self._states[safe] = self._store.load_user_state(safe)
        return self._states[safe]

    def ensure_known_user(self, username: str) -> tuple[str, dict[str, Any] | None]:
        safe = validate_username(username)
        rec = lookup_user_record(safe)
        if rec is None and not self._store.has_user_policy(safe):
            raise UserNotFoundLookupError(f"User not found: {safe}")
        return safe, rec

    def _session_flags(
        self,
        safe: str,
        sessions: list[dict[str, Any]],
    ) -> tuple[bool, bool]:
        any_active = any(s.get("active", False) for s in sessions)
        locked_hint = (
            any(s.get("locked", False) for s in sessions) if self._config.use_locked_hint else False
        )
        screen_locked = locked_hint or self._tray_screensaver_active.get(safe, False)
        return any_active, screen_locked

    def build_status_from_eval(
        self,
        safe: str,
        policy: UserPolicy,
        state: UserAccountingState,
        eval_res: EvaluationResult,
        sessions: list[dict[str, Any]],
        any_active: bool,
        screen_locked: bool,
        limited_apps: list[str],
        user_record: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Assemble UserStatus dict directly from an existing EvaluationResult (no re-evaluation)."""
        rec = user_record if user_record is not None else (lookup_user_record(safe) or {})
        return build_user_status_dict(
            policy=policy,
            state=state,
            eval_res=eval_res,
            uid=rec.get("uid"),
            full_name=rec.get("full_name", safe),
            logged_in=len(sessions) > 0,
            active=any_active,
            screen_locked=screen_locked,
            sessions=self._sessions.summarize_sessions(sessions),
            active_limited_apps=limited_apps,
        )

    def compute_status(self, username: str) -> dict[str, Any]:
        safe = validate_username(username)
        policy = self.get_policy(safe)
        state = self.get_state(safe)
        sessions = self._last_sessions.get(safe, [])
        any_active, screen_locked = self._session_flags(safe, sessions)
        limited_apps = self._active_limited_apps.get(safe, [])
        limited_patterns = self._active_limited_patterns.get(safe, [])

        eval_res = evaluate_tick(
            policy=policy,
            state=state,
            config=self._config,
            now_local=self._now_fn(),
            elapsed_sec=0,
            is_active=any_active,
            screen_locked=screen_locked,
            app_limits_active=bool(limited_apps),
            running_app_patterns=limited_patterns,
        )
        self._last_eval[safe] = eval_res
        return self.build_status_from_eval(
            safe=safe,
            policy=policy,
            state=state,
            eval_res=eval_res,
            sessions=sessions,
            any_active=any_active,
            screen_locked=screen_locked,
            limited_apps=limited_apps,
        )

    # ---------------- Periodic Enforcement Tick ---------------- #

    def tick(self) -> bool:
        """Run one accounting & enforcement cycle across all active desktop users."""
        now_mono = time.monotonic()
        elapsed = round(now_mono - self._last_tick_mono) if self._last_tick_mono is not None else 0
        self._last_tick_mono = now_mono
        now_local = self._now_fn()
        date_str = now_local.date().isoformat()

        sessions_by_user = self._sessions.list_user_sessions(self._config.tracked_session_types)
        self._last_sessions = sessions_by_user
        excluded = set(self._config.excluded_users)

        for username, sessions in sessions_by_user.items():
            if username in excluded:
                continue
            try:
                safe = validate_username(username)
            except ValueError:
                continue
            self._tick_user(safe, sessions, now_local, now_mono, elapsed, date_str)

        if (now_mono - self._last_save_mono) >= self._config.save_interval_sec:
            self.flush_states()
            self._last_save_mono = now_mono

        return True

    def _tick_user(
        self,
        safe: str,
        sessions: list[dict[str, Any]],
        now_local: datetime,
        now_mono: float,
        elapsed: int,
        date_str: str,
    ) -> None:
        policy = self.get_policy(safe)
        state = self.get_state(safe)
        uid = int(sessions[0]["uid"])
        any_active, screen_locked = self._session_flags(safe, sessions)

        app_matches: list[dict[str, Any]] = []
        if (
            self._config.app_limits_enabled
            and policy.app_limits.enabled
            and any_active
            and not screen_locked
        ):
            app_matches = self._app_limits.find_matching_processes(uid, policy.app_limits.apps)
        limited_apps = sorted({m["label"] for m in app_matches})
        running_patterns = sorted({m["pattern"] for m in app_matches})
        self._active_limited_apps[safe] = limited_apps
        self._active_limited_patterns[safe] = running_patterns

        eval_res = evaluate_tick(
            policy=policy,
            state=state,
            config=self._config,
            now_local=now_local,
            elapsed_sec=elapsed,
            is_active=any_active,
            screen_locked=screen_locked,
            app_limits_active=bool(app_matches),
            running_app_patterns=running_patterns,
        )
        self._last_eval[safe] = eval_res

        if eval_res.pause_expired:
            self._store.save_user_policy(policy)
            self._history.record_event(
                safe, "resume", actor="daemon", detail={"reason": "timed_pause_expired"}
            )
            self._emit_policy_changed(safe)

        if eval_res.active_delta or eval_res.inactive_delta or eval_res.app_limits_delta:
            self._history.record_daily_delta(
                safe,
                date_str,
                active_sec=eval_res.active_delta,
                inactive_sec=eval_res.inactive_delta,
                app_limits_sec=eval_res.app_limits_delta,
            )

        for thresh in eval_res.crossed_warnings:
            self._emit_notification(
                safe,
                {
                    "kind": "warning",
                    "seconds_left": eval_res.time_left_continuous,
                    "threshold_sec": thresh,
                },
            )

        if eval_res.expired_app_patterns:
            self._enforce_expired_apps(
                safe, uid, policy, eval_res.expired_app_patterns, app_matches
            )

        self._enforce_session_lockout(
            safe,
            uid,
            policy,
            eval_res,
            sessions,
            any_active,
            screen_locked,
            now_mono,
            elapsed,
            date_str,
        )
        self._maybe_send_challenge(safe, now_mono)

        status = self.build_status_from_eval(
            safe=safe,
            policy=policy,
            state=state,
            eval_res=eval_res,
            sessions=sessions,
            any_active=any_active,
            screen_locked=screen_locked,
            limited_apps=limited_apps,
        )
        self._emit_status(safe, status)

    def _enforce_expired_apps(
        self,
        safe: str,
        uid: int,
        policy: UserPolicy,
        expired_patterns: list[str],
        app_matches: list[dict[str, Any]],
    ) -> None:
        expired_set = set(expired_patterns)
        expired_apps = [a for a in policy.app_limits.apps if a.pattern in expired_set]
        expired_matches = [m for m in app_matches if m.get("pattern") in expired_set]
        killed = self._app_limits.enforce_kill(uid, expired_apps, prematched=expired_matches)
        if killed:
            expired_labels = [a.label for a in expired_apps]
            self._history.record_event(
                safe,
                "app_limit_kill",
                actor="daemon",
                detail={"pids": killed, "apps": expired_labels},
            )
            self._emit_notification(safe, {"kind": "app_limit_reached", "apps": expired_labels})

    def _enforce_session_lockout(
        self,
        safe: str,
        uid: int,
        policy: UserPolicy,
        eval_res: EvaluationResult,
        sessions: list[dict[str, Any]],
        any_active: bool,
        screen_locked: bool,
        now_mono: float,
        elapsed: int,
        date_str: str,
    ) -> None:
        if not (eval_res.should_enforce and any_active and not screen_locked):
            self._countdowns.pop(safe, None)
            return

        rem = self._countdowns.get(safe)
        if rem is None:
            rem = max(0, min(eval_res.time_left_continuous, self._config.final_countdown_sec))
        else:
            rem = max(0, rem - max(1, elapsed))
        self._countdowns[safe] = rem

        if rem > 0:
            self._emit_notification(
                safe,
                {
                    "kind": "countdown",
                    "seconds_left": rem,
                    "action": policy.lockout_action,
                },
            )
            return

        last_try = self._last_lock_attempt_mono.get(safe, 0.0)
        if (now_mono - last_try) >= _LOCK_RETRY_INTERVAL_SEC:
            self._last_lock_attempt_mono[safe] = now_mono
            logger.warning(
                "time expired for user %r; enforcing lockout action=%s",
                safe,
                policy.lockout_action,
            )
            self._sessions.enforce_lockout_action(
                policy.lockout_action, safe, uid, sessions, self._config
            )
            self._history.record_daily_delta(safe, date_str, lockouts=1)
            self._history.record_event(
                safe,
                "lockout",
                actor="daemon",
                detail={"action": policy.lockout_action},
            )

    def _maybe_send_challenge(self, safe: str, now_mono: float) -> None:
        if (now_mono - self._last_challenge_mono.get(safe, 0.0)) >= _CHALLENGE_INTERVAL_SEC:
            self._last_challenge_mono[safe] = now_mono
            nonce = secrets.token_urlsafe(16)
            self._pending_nonces[safe] = nonce
            self._emit_challenge(safe, nonce)

    def flush_states(self) -> None:
        """Persist all cached accounting states atomically to disk."""
        for state in self._states.values():
            try:
                self._store.save_user_state(state)
            except OSError as exc:
                logger.error("failed to save state for %r: %s", state.username, exc)

    # ---------------- Admin & Client Business Mutations ---------------- #

    def list_users(self) -> list[dict[str, Any]]:
        discovered = {u["username"]: u for u in discover_desktop_users(self._config.excluded_users)}
        configured = set(self._store.list_configured_users())
        all_names = sorted(set(discovered.keys()) | configured | set(self._last_sessions.keys()))
        summaries = []
        for uname in all_names:
            if uname in self._config.excluded_users:
                continue
            status = self.compute_status(uname)
            summaries.append(
                {
                    "username": status["username"],
                    "uid": status["uid"],
                    "full_name": status["full_name"],
                    "configured": uname in configured,
                    "logged_in": status["logged_in"],
                    "active": status["active"],
                    "screen_locked": status["screen_locked"],
                    "enforcement_state": status["enforcement_state"],
                    "time_left_today": status["time_left_today"],
                    "time_left_continuous": status["time_left_continuous"],
                    "time_spent_today": status["time_spent_today"],
                    "daily_limit_today": status["daily_limit_today"],
                    "lockout_action": status["policy"]["lockout_action"],
                    "pause": status["pause"],
                }
            )
        return summaries

    def set_user_policy(self, safe: str, raw: dict[str, Any], actor: str) -> dict[str, Any]:
        payload = dict(raw)
        payload["username"] = safe
        policy = UserPolicy.from_dict(payload)
        self._policies[safe] = policy
        self._store.save_user_policy(policy)
        self._countdowns.pop(safe, None)
        self._history.record_event(
            safe, "policy_update", actor=actor, detail={"lockout_action": policy.lockout_action}
        )
        self._emit_policy_changed(safe)
        status = self.compute_status(safe)
        self._emit_status(safe, status)
        self._emit_notification(safe, {"kind": "policy_changed"})
        return status

    def grant_time(
        self,
        safe: str,
        delta_seconds: int,
        mode: str,
        actor: str,
    ) -> dict[str, Any]:
        policy = self.get_policy(safe)
        state = self.get_state(safe)
        now_local = self._now_fn()
        applied = apply_grant(policy, state, int(delta_seconds), str(mode), now_local)
        self._store.save_user_state(state)
        self._countdowns.pop(safe, None)
        self._history.record_daily_delta(safe, now_local.date().isoformat(), bonus_sec=applied)
        self._history.record_event(
            safe,
            "grant",
            actor=actor,
            detail={"mode": str(mode), "requested_sec": int(delta_seconds), "applied_sec": applied},
        )
        status = self.compute_status(safe)
        self._emit_status(safe, status)
        self._emit_notification(safe, {"kind": "time_granted", "applied_sec": applied})
        return {"applied_sec": applied, "status": status}

    def pause_user(
        self,
        safe: str,
        until_utc: str,
        reason: str,
        hide_tray: bool,
        actor: str,
    ) -> dict[str, Any]:
        policy = self.get_policy(safe)
        until_clean = str(until_utc).strip()
        policy.pause = PauseState(
            active=True,
            mode="until" if until_clean else "indefinite",
            until_utc=until_clean or None,
            reason=str(reason or ""),
            hide_tray_while_paused=bool(hide_tray),
        )
        self._store.save_user_policy(policy)
        self._countdowns.pop(safe, None)
        self._history.record_event(safe, "pause", actor=actor, detail=policy.pause.to_dict())
        self._emit_policy_changed(safe)
        status = self.compute_status(safe)
        self._emit_status(safe, status)
        self._emit_notification(safe, {"kind": "paused", "pause": policy.pause.to_dict()})
        return status

    def resume_user(self, safe: str, actor: str) -> dict[str, Any]:
        policy = self.get_policy(safe)
        policy.pause = PauseState(active=False)
        self._store.save_user_policy(policy)
        self._history.record_event(safe, "resume", actor=actor, detail={"manual": True})
        self._emit_policy_changed(safe)
        status = self.compute_status(safe)
        self._emit_status(safe, status)
        self._emit_notification(safe, {"kind": "resumed"})
        return status

    def set_today_override(
        self,
        safe: str,
        raw: dict[str, Any],
        actor: str,
    ) -> dict[str, Any]:
        state = self.get_state(safe)
        now_local = self._now_fn()
        res = apply_today_override(
            state=state,
            now_local=now_local,
            limit_sec=raw.get("limit_sec"),
            windows=raw.get("windows"),
            reset=bool(raw.get("reset", False)),
        )
        self._store.save_user_state(state)
        self._countdowns.pop(safe, None)
        self._history.record_event(safe, "today_override", actor=actor, detail=res)
        status = self.compute_status(safe)
        self._emit_status(safe, status)
        self._emit_notification(safe, {"kind": "policy_changed"})
        return status

    def grant_app_time(
        self,
        safe: str,
        pattern: str,
        delta_seconds: int,
        actor: str,
    ) -> dict[str, Any]:
        policy = self.get_policy(safe)
        state = self.get_state(safe)
        now_local = self._now_fn()
        applied = apply_app_grant(policy, state, str(pattern), int(delta_seconds), now_local)
        app = next((a for a in policy.app_limits.apps if a.pattern == str(pattern).strip()), None)
        label = app.label if app else str(pattern)
        self._store.save_user_state(state)
        self._history.record_event(
            safe,
            "app_grant",
            actor=actor,
            detail={"pattern": str(pattern), "label": label, "applied_sec": applied},
        )
        status = self.compute_status(safe)
        self._emit_status(safe, status)
        self._emit_notification(
            safe,
            {
                "kind": "app_time_granted",
                "pattern": str(pattern),
                "label": label,
                "applied_sec": applied,
            },
        )
        return {"applied_sec": applied, "status": status}

    def set_daemon_config(self, raw: dict[str, Any]) -> DaemonConfig:
        cfg = DaemonConfig.from_dict(raw)
        self._config = cfg
        self._store.save_daemon_config(cfg)
        return cfg

    def verify_screensaver_state(self, caller_user: str, nonce: str, active: bool) -> bool:
        expected = self._pending_nonces.pop(caller_user, None)
        if not expected or not nonce:
            return False
        if not hmac.compare_digest(str(nonce), str(expected)):
            return False
        self._tray_screensaver_active[caller_user] = bool(active)
        return True
