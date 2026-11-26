"""org.curfewd.Daemon1 D-Bus service and enforcement runtime."""

from datetime import datetime
import hmac
import json
import logging
import secrets
import time
from typing import Any, Callable

import dbus
import dbus.service

from curfewd import (
    DBUS_ADMIN_INTERFACE,
    DBUS_BUS_NAME,
    DBUS_CLIENT_INTERFACE,
    DBUS_EVENTS_INTERFACE,
    DBUS_OBJECT_PATH,
    POLKIT_ACTION_LOCK,
    POLKIT_ACTION_MANAGE,
    POLKIT_ACTION_READ,
)
from curfewd.dbus.polkit import AuthorizationError, PolkitAuthorizer
from curfewd.engine.doctor import apply_dconf_lock_fix, run_doctor
from curfewd.engine.evaluator import (
    EvaluationResult,
    apply_app_grant,
    apply_grant,
    apply_today_override,
    build_user_status_dict,
    evaluate_tick,
)
from curfewd.engine.playtime import PlayTimeMonitor
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

logger = logging.getLogger("curfewd.service")

DBUS_ERR_PERMISSION = "org.curfewd.Daemon1.Error.PermissionDenied"
DBUS_ERR_NOT_FOUND = "org.curfewd.Daemon1.Error.UserNotFound"
DBUS_ERR_VALIDATION = "org.curfewd.Daemon1.Error.ValidationError"
DBUS_ERR_INTERNAL = "org.curfewd.Daemon1.Error.InternalError"

# Re-challenge desktop tray screensaver state every N seconds
_CHALLENGE_INTERVAL_SEC = 15.0
# Minimum interval between repetitive lock retries while locked_out and active
_LOCK_RETRY_INTERVAL_SEC = 3.0


class _DBusTypedError(dbus.DBusException):
    def __init__(self, dbus_name: str, message: str) -> None:
        self._dbus_error_name = dbus_name
        super().__init__(message)


class CurfewDaemonService(dbus.service.Object):
    """Exports org.curfewd.Daemon1 Admin, Client, and Events interfaces."""

    def __init__(
        self,
        bus,
        store: AtomicJsonStore,
        history: HistoryStore,
        session_monitor: LogindSessionMonitor | None = None,
        playtime_monitor: PlayTimeMonitor | None = None,
        authorizer: PolkitAuthorizer | None = None,
        now_fn: Callable[[], datetime] | None = None,
        doctor_fixer: Callable[[], dict[str, Any]] | None = None,
        bus_name_str: str = DBUS_BUS_NAME,
        own_name: bool = True,
    ) -> None:
        self._bus = bus
        self._store = store
        self._history = history
        self._sessions = session_monitor or LogindSessionMonitor(system_bus=bus)
        self._playtime = playtime_monitor or PlayTimeMonitor()
        self._authorizer = authorizer or PolkitAuthorizer(bus)
        self._now_fn = now_fn or (lambda: datetime.now().astimezone())
        self._doctor_fixer = doctor_fixer or apply_dconf_lock_fix

        self._store.ensure_dirs()
        self._config: DaemonConfig = self._store.load_daemon_config()
        if not self._store.daemon_config_path or not self._file_exists(self._store.daemon_config_path):
            self._store.save_daemon_config(self._config)

        # In-memory caches
        self._policies: dict[str, UserPolicy] = {}
        self._states: dict[str, UserAccountingState] = {}
        self._last_eval: dict[str, EvaluationResult] = {}
        self._last_sessions: dict[str, list[dict[str, Any]]] = {}
        self._active_playtime_apps: dict[str, list[str]] = {}
        self._active_playtime_patterns: dict[str, list[str]] = {}

        # Client screensaver challenge/response state
        self._pending_nonces: dict[str, str] = {}
        self._last_challenge_mono: dict[str, float] = {}
        self._tray_screensaver_active: dict[str, bool] = {}

        # Lockout countdown & save timers
        self._countdowns: dict[str, int] = {}
        self._last_lock_attempt_mono: dict[str, float] = {}
        self._last_tick_mono: float | None = None
        self._last_save_mono: float = time.monotonic()

        if own_name:
            self._bus_name = dbus.service.BusName(bus_name_str, bus=bus, replace_existing=True)
            super().__init__(self._bus_name, DBUS_OBJECT_PATH)
        else:
            self._bus_name = None
            super().__init__(bus, DBUS_OBJECT_PATH)

        logger.info("org.curfewd.Daemon1 initialized (object=%s)", DBUS_OBJECT_PATH)

    @staticmethod
    def _file_exists(path: str) -> bool:
        import os

        return os.path.isfile(path)

    def _get_policy(self, username: str) -> UserPolicy:
        safe = validate_username(username)
        if safe not in self._policies:
            self._policies[safe] = self._store.load_user_policy(safe)
        return self._policies[safe]

    def _get_state(self, username: str) -> UserAccountingState:
        safe = validate_username(username)
        if safe not in self._states:
            self._states[safe] = self._store.load_user_state(safe)
        return self._states[safe]

    def _require_auth(self, sender: str, action_id: str) -> tuple[int, str]:
        try:
            return self._authorizer.authorize(sender, action_id)
        except AuthorizationError as exc:
            raise _DBusTypedError(DBUS_ERR_PERMISSION, str(exc)) from exc

    def _ensure_known_user(self, username: str) -> tuple[str, dict[str, Any] | None]:
        try:
            safe = validate_username(username)
        except ValueError as exc:
            raise _DBusTypedError(DBUS_ERR_VALIDATION, str(exc)) from exc
        rec = lookup_user_record(safe)
        if rec is None and not self._store.has_user_policy(safe):
            raise _DBusTypedError(DBUS_ERR_NOT_FOUND, f"User not found: {safe}")
        return safe, rec

    def _compute_status(self, username: str) -> dict[str, Any]:
        safe = validate_username(username)
        policy = self._get_policy(safe)
        state = self._get_state(safe)
        now_local = self._now_fn()
        sessions = self._last_sessions.get(safe, [])
        logged_in = len(sessions) > 0
        any_active = any(s.get("active", False) for s in sessions)
        locked_hint = any(s.get("locked", False) for s in sessions) if self._config.use_locked_hint else False
        screen_locked = locked_hint or self._tray_screensaver_active.get(safe, False)
        pt_apps = self._active_playtime_apps.get(safe, [])
        pt_patterns = self._active_playtime_patterns.get(safe, [])

        eval_res = evaluate_tick(
            policy=policy,
            state=state,
            config=self._config,
            now_local=now_local,
            elapsed_sec=0,
            is_active=any_active,
            screen_locked=screen_locked,
            playtime_active=bool(pt_apps),
            running_app_patterns=pt_patterns,
        )
        self._last_eval[safe] = eval_res
        rec = lookup_user_record(safe) or {}
        return build_user_status_dict(
            policy=policy,
            state=state,
            eval_res=eval_res,
            uid=rec.get("uid"),
            full_name=rec.get("full_name", safe),
            logged_in=logged_in,
            active=any_active,
            screen_locked=screen_locked,
            sessions=self._sessions.summarize_sessions(sessions),
            active_playtime_apps=pt_apps,
        )

    # ---------------- Periodic Enforcement Tick (GLib main thread) ---------------- #

    def tick(self) -> bool:
        """Run one accounting & enforcement cycle (called on GLib main loop)."""
        now_mono = time.monotonic()
        elapsed = int(round(now_mono - self._last_tick_mono)) if self._last_tick_mono is not None else 0
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

            policy = self._get_policy(safe)
            state = self._get_state(safe)
            uid = int(sessions[0]["uid"])
            any_active = any(s.get("active", False) for s in sessions)
            locked_hint = any(s.get("locked", False) for s in sessions) if self._config.use_locked_hint else False
            screen_locked = locked_hint or self._tray_screensaver_active.get(safe, False)

            # App Limits process check (individual per-app tracking)
            pt_matches = []
            if self._config.playtime_enabled and policy.playtime.enabled and any_active and not screen_locked:
                pt_matches = self._playtime.find_matching_processes(uid, policy.playtime.apps)
            self._active_playtime_apps[safe] = sorted({m["label"] for m in pt_matches})
            running_patterns = sorted({m["pattern"] for m in pt_matches})
            self._active_playtime_patterns[safe] = running_patterns

            eval_res = evaluate_tick(
                policy=policy,
                state=state,
                config=self._config,
                now_local=now_local,
                elapsed_sec=elapsed,
                is_active=any_active,
                screen_locked=screen_locked,
                playtime_active=bool(pt_matches),
                running_app_patterns=running_patterns,
            )
            self._last_eval[safe] = eval_res

            # Persist timed pause expiry
            if eval_res.pause_expired:
                self._store.save_user_policy(policy)
                self._history.record_event(safe, "resume", actor="daemon", detail={"reason": "timed_pause_expired"})
                self.PolicyChanged(safe)

            # Record usage deltas to SQLite history
            if eval_res.active_delta or eval_res.inactive_delta or eval_res.playtime_delta:
                self._history.record_daily_delta(
                    safe,
                    date_str,
                    active_sec=eval_res.active_delta,
                    inactive_sec=eval_res.inactive_delta,
                    playtime_sec=eval_res.playtime_delta,
                )

            # Emit threshold warnings
            for thresh in eval_res.crossed_warnings:
                self.ClientNotification(
                    safe,
                    json.dumps(
                        {
                            "kind": "warning",
                            "seconds_left": eval_res.time_left_continuous,
                            "threshold_sec": thresh,
                        }
                    ),
                )

            # Enforce individual per-app limits (terminate ONLY expired apps)
            if eval_res.expired_app_patterns:
                expired_set = set(eval_res.expired_app_patterns)
                expired_apps = [a for a in policy.playtime.apps if a.pattern in expired_set]
                killed = self._playtime.enforce_kill(uid, expired_apps)
                if killed:
                    expired_labels = [a.label for a in expired_apps]
                    self._history.record_event(
                        safe,
                        "playtime_kill",
                        actor="daemon",
                        detail={"pids": killed, "apps": expired_labels},
                    )
                    self.ClientNotification(
                        safe,
                        json.dumps({"kind": "app_limit_reached", "apps": expired_labels}),
                    )

            # Enforce main session lockout
            if eval_res.should_enforce and any_active and not screen_locked:
                rem = self._countdowns.get(safe)
                if rem is None:
                    rem = max(0, min(eval_res.time_left_continuous, self._config.final_countdown_sec))
                else:
                    rem = max(0, rem - max(1, elapsed))
                self._countdowns[safe] = rem

                if rem > 0:
                    self.ClientNotification(
                        safe,
                        json.dumps(
                            {
                                "kind": "countdown",
                                "seconds_left": rem,
                                "action": policy.lockout_action,
                            }
                        ),
                    )
                else:
                    last_try = self._last_lock_attempt_mono.get(safe, 0.0)
                    if (now_mono - last_try) >= _LOCK_RETRY_INTERVAL_SEC:
                        self._last_lock_attempt_mono[safe] = now_mono
                        logger.warning(
                            "time expired for user %r; enforcing lockout action=%s",
                            safe, policy.lockout_action,
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
            else:
                self._countdowns.pop(safe, None)

            # Periodic CSPRNG screensaver verification challenge to tray client
            if (now_mono - self._last_challenge_mono.get(safe, 0.0)) >= _CHALLENGE_INTERVAL_SEC:
                self._last_challenge_mono[safe] = now_mono
                nonce = secrets.token_urlsafe(16)
                self._pending_nonces[safe] = nonce
                self.ScreensaverChallenge(safe, nonce)

            # Broadcast live status update for Material UI & tray
            status = self._compute_status(safe)
            self.UserStatusChanged(safe, json.dumps(status))

        # Periodic disk flush of accounting state
        if (now_mono - self._last_save_mono) >= self._config.save_interval_sec:
            self.flush_states()
            self._last_save_mono = now_mono

        return True

    def flush_states(self) -> None:
        """Persist all cached accounting states atomically to disk."""
        for state in self._states.values():
            try:
                self._store.save_user_state(state)
            except OSError as exc:
                logger.error("failed to save state for %r: %s", state.username, exc)

    # ---------------- org.curfewd.Daemon1.Admin ---------------- #

    @dbus.service.method(DBUS_ADMIN_INTERFACE, in_signature="", out_signature="s", sender_keyword="sender")
    def ListUsers(self, sender: str = "") -> str:
        self._require_auth(sender, POLKIT_ACTION_READ)
        discovered = {u["username"]: u for u in discover_desktop_users(self._config.excluded_users)}
        configured = set(self._store.list_configured_users())
        all_names = sorted(set(discovered.keys()) | configured | set(self._last_sessions.keys()))
        summaries = []
        for uname in all_names:
            if uname in self._config.excluded_users:
                continue
            status = self._compute_status(uname)
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
        return json.dumps({"users": summaries})

    @dbus.service.method(DBUS_ADMIN_INTERFACE, in_signature="s", out_signature="s", sender_keyword="sender")
    def GetUserStatus(self, username: str, sender: str = "") -> str:
        self._require_auth(sender, POLKIT_ACTION_READ)
        safe, _ = self._ensure_known_user(str(username))
        return json.dumps(self._compute_status(safe))

    @dbus.service.method(DBUS_ADMIN_INTERFACE, in_signature="ss", out_signature="s", sender_keyword="sender")
    def SetUserPolicy(self, username: str, policy_json: str, sender: str = "") -> str:
        _, actor = self._require_auth(sender, POLKIT_ACTION_MANAGE)
        safe, _ = self._ensure_known_user(str(username))
        try:
            raw = json.loads(str(policy_json))
            if not isinstance(raw, dict):
                raise ValueError("Policy payload must be a JSON object")
            raw["username"] = safe
            policy = UserPolicy.from_dict(raw)
        except (ValueError, KeyError, TypeError) as exc:
            raise _DBusTypedError(DBUS_ERR_VALIDATION, f"Invalid policy: {exc}") from exc

        self._policies[safe] = policy
        self._store.save_user_policy(policy)
        self._countdowns.pop(safe, None)
        self._history.record_event(safe, "policy_update", actor=actor, detail={"lockout_action": policy.lockout_action})
        self.PolicyChanged(safe)
        status = self._compute_status(safe)
        self.UserStatusChanged(safe, json.dumps(status))
        self.ClientNotification(safe, json.dumps({"kind": "policy_changed"}))
        return json.dumps(status)

    @dbus.service.method(DBUS_ADMIN_INTERFACE, in_signature="sis", out_signature="s", sender_keyword="sender")
    def GrantTime(self, username: str, delta_seconds: int, mode: str, sender: str = "") -> str:
        _, actor = self._require_auth(sender, POLKIT_ACTION_MANAGE)
        safe, _ = self._ensure_known_user(str(username))
        policy = self._get_policy(safe)
        state = self._get_state(safe)
        now_local = self._now_fn()
        try:
            applied = apply_grant(policy, state, int(delta_seconds), str(mode), now_local)
        except ValueError as exc:
            raise _DBusTypedError(DBUS_ERR_VALIDATION, str(exc)) from exc

        self._store.save_user_state(state)
        self._countdowns.pop(safe, None)
        self._history.record_daily_delta(safe, now_local.date().isoformat(), bonus_sec=applied)
        self._history.record_event(
            safe, "grant", actor=actor, detail={"mode": str(mode), "requested_sec": int(delta_seconds), "applied_sec": applied}
        )
        status = self._compute_status(safe)
        self.UserStatusChanged(safe, json.dumps(status))
        self.ClientNotification(safe, json.dumps({"kind": "time_granted", "applied_sec": applied}))
        return json.dumps({"applied_sec": applied, "status": status})

    @dbus.service.method(DBUS_ADMIN_INTERFACE, in_signature="sssb", out_signature="s", sender_keyword="sender")
    def PauseUser(self, username: str, until_utc: str, reason: str, hide_tray: bool, sender: str = "") -> str:
        _, actor = self._require_auth(sender, POLKIT_ACTION_MANAGE)
        safe, _ = self._ensure_known_user(str(username))
        policy = self._get_policy(safe)
        until_clean = str(until_utc).strip()
        try:
            policy.pause = PauseState(
                active=True,
                mode="until" if until_clean else "indefinite",
                until_utc=until_clean or None,
                reason=str(reason or ""),
                hide_tray_while_paused=bool(hide_tray),
            )
        except ValueError as exc:
            raise _DBusTypedError(DBUS_ERR_VALIDATION, str(exc)) from exc

        self._store.save_user_policy(policy)
        self._countdowns.pop(safe, None)
        self._history.record_event(safe, "pause", actor=actor, detail=policy.pause.to_dict())
        self.PolicyChanged(safe)
        status = self._compute_status(safe)
        self.UserStatusChanged(safe, json.dumps(status))
        self.ClientNotification(safe, json.dumps({"kind": "paused", "pause": policy.pause.to_dict()}))
        return json.dumps(status)

    @dbus.service.method(DBUS_ADMIN_INTERFACE, in_signature="s", out_signature="s", sender_keyword="sender")
    def ResumeUser(self, username: str, sender: str = "") -> str:
        _, actor = self._require_auth(sender, POLKIT_ACTION_MANAGE)
        safe, _ = self._ensure_known_user(str(username))
        policy = self._get_policy(safe)
        policy.pause = PauseState(active=False)
        self._store.save_user_policy(policy)
        self._history.record_event(safe, "resume", actor=actor, detail={"manual": True})
        self.PolicyChanged(safe)
        status = self._compute_status(safe)
        self.UserStatusChanged(safe, json.dumps(status))
        self.ClientNotification(safe, json.dumps({"kind": "resumed"}))
        return json.dumps(status)

    @dbus.service.method(DBUS_ADMIN_INTERFACE, in_signature="s", out_signature="s", sender_keyword="sender")
    def LockUser(self, username: str, sender: str = "") -> str:
        _, actor = self._require_auth(sender, POLKIT_ACTION_LOCK)
        safe, _ = self._ensure_known_user(str(username))
        sessions_by_user = self._sessions.list_user_sessions(self._config.tracked_session_types)
        sessions = sessions_by_user.get(safe, self._last_sessions.get(safe, []))
        if not sessions:
            return json.dumps({"username": safe, "locked": False, "sessions": [], "detail": "No active graphical sessions"})

        results = self._sessions.lock_user_sessions(sessions, self._config, async_mode=False)
        payload = {
            "username": safe,
            "locked": any(r.locked for r in results),
            "verified": all(r.verified for r in results) if results else False,
            "sessions": [r.to_dict() for r in results],
        }
        self._history.record_event(safe, "lock_manual", actor=actor, detail=payload)
        return json.dumps(payload)

    @dbus.service.method(DBUS_ADMIN_INTERFACE, in_signature="si", out_signature="s", sender_keyword="sender")
    def GetUsageHistory(self, username: str, days: int, sender: str = "") -> str:
        self._require_auth(sender, POLKIT_ACTION_READ)
        safe, _ = self._ensure_known_user(str(username))
        hist = self._history.get_usage_history(safe, days=int(days), now_local=self._now_fn())
        return json.dumps(hist)

    @dbus.service.method(DBUS_ADMIN_INTERFACE, in_signature="", out_signature="s", sender_keyword="sender")
    def GetDaemonConfig(self, sender: str = "") -> str:
        self._require_auth(sender, POLKIT_ACTION_READ)
        return json.dumps(self._config.to_dict())

    @dbus.service.method(DBUS_ADMIN_INTERFACE, in_signature="s", out_signature="s", sender_keyword="sender")
    def SetDaemonConfig(self, config_json: str, sender: str = "") -> str:
        self._require_auth(sender, POLKIT_ACTION_MANAGE)
        try:
            raw = json.loads(str(config_json))
            if not isinstance(raw, dict):
                raise ValueError("Daemon config must be a JSON object")
            cfg = DaemonConfig.from_dict(raw)
        except (ValueError, KeyError, TypeError) as exc:
            raise _DBusTypedError(DBUS_ERR_VALIDATION, f"Invalid daemon config: {exc}") from exc
        self._config = cfg
        self._store.save_daemon_config(cfg)
        return json.dumps(cfg.to_dict())

    @dbus.service.method(DBUS_ADMIN_INTERFACE, in_signature="ss", out_signature="s", sender_keyword="sender")
    def SetTodayOverride(self, username: str, override_json: str, sender: str = "") -> str:
        _, actor = self._require_auth(sender, POLKIT_ACTION_MANAGE)
        safe, _ = self._ensure_known_user(str(username))
        policy = self._get_policy(safe)
        state = self._get_state(safe)
        now_local = self._now_fn()
        try:
            raw = json.loads(str(override_json))
            if not isinstance(raw, dict):
                raise ValueError("Override payload must be a JSON object")
            res = apply_today_override(
                policy=policy,
                state=state,
                now_local=now_local,
                limit_sec=raw.get("limit_sec"),
                windows=raw.get("windows"),
                reset=bool(raw.get("reset", False)),
            )
        except (ValueError, KeyError, TypeError) as exc:
            raise _DBusTypedError(DBUS_ERR_VALIDATION, f"Invalid today override: {exc}") from exc

        self._store.save_user_state(state)
        self._countdowns.pop(safe, None)
        self._history.record_event(safe, "today_override", actor=actor, detail=res)
        status = self._compute_status(safe)
        self.UserStatusChanged(safe, json.dumps(status))
        self.ClientNotification(safe, json.dumps({"kind": "policy_changed"}))
        return json.dumps(status)

    @dbus.service.method(DBUS_ADMIN_INTERFACE, in_signature="ssi", out_signature="s", sender_keyword="sender")
    def GrantAppTime(self, username: str, pattern: str, delta_seconds: int, sender: str = "") -> str:
        _, actor = self._require_auth(sender, POLKIT_ACTION_MANAGE)
        safe, _ = self._ensure_known_user(str(username))
        policy = self._get_policy(safe)
        state = self._get_state(safe)
        now_local = self._now_fn()
        try:
            applied = apply_app_grant(policy, state, str(pattern), int(delta_seconds), now_local)
        except ValueError as exc:
            raise _DBusTypedError(DBUS_ERR_VALIDATION, str(exc)) from exc

        app = next((a for a in policy.playtime.apps if a.pattern == str(pattern).strip()), None)
        label = app.label if app else str(pattern)
        self._store.save_user_state(state)
        self._history.record_event(
            safe,
            "app_grant",
            actor=actor,
            detail={"pattern": str(pattern), "label": label, "applied_sec": applied},
        )
        status = self._compute_status(safe)
        self.UserStatusChanged(safe, json.dumps(status))
        self.ClientNotification(
            safe,
            json.dumps({"kind": "app_time_granted", "pattern": str(pattern), "label": label, "applied_sec": applied}),
        )
        return json.dumps({"applied_sec": applied, "status": status})

    @dbus.service.method(DBUS_ADMIN_INTERFACE, in_signature="", out_signature="s", sender_keyword="sender")
    def RunDoctor(self, sender: str = "") -> str:
        self._require_auth(sender, POLKIT_ACTION_READ)
        sessions_by_user = self._sessions.list_user_sessions(self._config.tracked_session_types)
        report = run_doctor(
            config_dir=self._store.config_dir,
            state_dir=self._store.state_dir,
            sessions_by_user=sessions_by_user,
            dbus_connected=True,
        )
        return json.dumps(report)

    @dbus.service.method(DBUS_ADMIN_INTERFACE, in_signature="", out_signature="s", sender_keyword="sender")
    def ApplyDoctorFix(self, sender: str = "") -> str:
        self._require_auth(sender, POLKIT_ACTION_MANAGE)
        try:
            fix_result = self._doctor_fixer()
        except PermissionError as exc:
            raise _DBusTypedError(DBUS_ERR_PERMISSION, str(exc)) from exc
        except OSError as exc:
            raise _DBusTypedError(DBUS_ERR_INTERNAL, f"Failed to apply lock-screen protection: {exc}") from exc

        sessions_by_user = self._sessions.list_user_sessions(self._config.tracked_session_types)
        report = run_doctor(
            config_dir=self._store.config_dir,
            state_dir=self._store.state_dir,
            sessions_by_user=sessions_by_user,
            dbus_connected=True,
        )
        return json.dumps({"fix_applied": fix_result, "doctor": report})

    # ---------------- org.curfewd.Daemon1.Client (sender-UID bound) ---------------- #

    @dbus.service.method(DBUS_CLIENT_INTERFACE, in_signature="", out_signature="s", sender_keyword="sender")
    def GetMyStatus(self, sender: str = "") -> str:
        try:
            _, caller_user = self._authorizer.resolve_sender_user(sender)
        except AuthorizationError as exc:
            raise _DBusTypedError(DBUS_ERR_PERMISSION, str(exc)) from exc
        return json.dumps(self._compute_status(caller_user))

    @dbus.service.method(DBUS_CLIENT_INTERFACE, in_signature="i", out_signature="s", sender_keyword="sender")
    def GetMyHistory(self, days: int, sender: str = "") -> str:
        try:
            _, caller_user = self._authorizer.resolve_sender_user(sender)
        except AuthorizationError as exc:
            raise _DBusTypedError(DBUS_ERR_PERMISSION, str(exc)) from exc
        safe_days = max(1, min(30, int(days)))
        hist = self._history.get_usage_history(caller_user, days=safe_days, now_local=self._now_fn())
        # Omit admin audit events from unprivileged self-view
        hist["events"] = []
        return json.dumps(hist)

    @dbus.service.method(DBUS_CLIENT_INTERFACE, in_signature="sb", out_signature="b", sender_keyword="sender")
    def ReportScreensaverState(self, nonce: str, active: bool, sender: str = "") -> bool:
        try:
            _, caller_user = self._authorizer.resolve_sender_user(sender)
        except AuthorizationError as exc:
            raise _DBusTypedError(DBUS_ERR_PERMISSION, str(exc)) from exc

        expected = self._pending_nonces.pop(caller_user, None)
        if not expected or not nonce:
            return False
        if not hmac.compare_digest(str(nonce), str(expected)):
            return False
        self._tray_screensaver_active[caller_user] = bool(active)
        return True

    # ---------------- org.curfewd.Daemon1.Events (signals) ---------------- #

    @dbus.service.signal(DBUS_EVENTS_INTERFACE, signature="ss")
    def UserStatusChanged(self, username: str, status_json: str) -> None:
        pass

    @dbus.service.signal(DBUS_EVENTS_INTERFACE, signature="s")
    def PolicyChanged(self, username: str) -> None:
        pass

    @dbus.service.signal(DBUS_EVENTS_INTERFACE, signature="ss")
    def ClientNotification(self, username: str, payload_json: str) -> None:
        pass

    @dbus.service.signal(DBUS_EVENTS_INTERFACE, signature="ss")
    def ScreensaverChallenge(self, username: str, nonce: str) -> None:
        pass
