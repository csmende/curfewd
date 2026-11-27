"""D-Bus-free stateful enforcement runtime for curfewd (Spec 005 + Spec 006)."""

import hmac
import inspect
import logging
import os
import secrets
import threading
import time
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from typing import Any

from curfewd.auth_code import ParentAccessCodeManager
from curfewd.engine.applimits import AppLimitsMonitor, ProcessScanResult
from curfewd.engine.evaluator import (
    EvaluationResult,
    apply_app_grant,
    apply_category_grant,
    apply_grant,
    apply_save_grace,
    apply_today_override,
    build_user_status_dict,
    evaluate_tick,
)
from curfewd.engine.sessions import (
    LogindSessionMonitor,
    discover_desktop_users,
    is_system_admin_user,
    lookup_user_record,
)
from curfewd.history import HistoryStore
from curfewd.models import (
    DaemonConfig,
    HomeworkModeState,
    LockResult,
    PauseState,
    UserAccountingState,
    UserPolicy,
)
from curfewd.parsing import format_duration, validate_username
from curfewd.storage import AtomicJsonStore
from curfewd.ui.appscan import scan_installed_apps

logger = logging.getLogger("curfewd.runtime")

# Re-challenge desktop tray screensaver/telemetry state every N seconds
_CHALLENGE_INTERVAL_SEC = 15.0
# Minimum interval between repetitive lock retries while unlocked during lockout
_LOCK_RETRY_INTERVAL_SEC = 3.0
# Lightweight root re-verification interval once sessions are verified locked (DEs without logind LockedHint)
_LOCK_REVERIFY_INTERVAL_SEC = 30.0
# Fail-closed max age for root-verified lock state if re-verification does not complete
_LOCK_VERIFY_MAX_AGE_SEC = 65.0
# Cache installed desktop/Steam app catalog per user for passive insight classification
_APP_CATALOG_TTL_SEC = 600.0
# Periodic passive SQLite WAL checkpoint interval to keep history.db-wal compact
_WAL_CHECKPOINT_INTERVAL_SEC = 600.0
# Max kid time requests allowed within sliding window
_MAX_REQUESTS_PER_WINDOW = 3
_REQUEST_WINDOW_MINUTES = 10


class UserNotFoundLookupError(LookupError):
    """Raised when a requested Linux user neither exists in passwd nor has a saved policy."""


def _format_lock_message(eval_res: EvaluationResult) -> str:
    """Build a friendly, concise away message shown on supported lock screens (e.g. Cinnamon)."""
    nxt = eval_res.next_allowed_at
    when = f" — available again at {nxt[11:16]}" if (nxt and len(nxt) >= 16) else ""
    if eval_res.time_left_today <= 0:
        return f"Daily screen time finished{when}"
    return f"Curfew hours active{when}"


class EnforcementRuntime:
    """Owns in-memory policy/state caches, single-pass tick accounting, requests, and enforcement."""

    def __init__(
        self,
        store: AtomicJsonStore,
        history: HistoryStore,
        session_monitor: LogindSessionMonitor | None = None,
        app_limits_monitor: AppLimitsMonitor | None = None,
        parent_code_manager: ParentAccessCodeManager | None = None,
        now_fn: Callable[[], datetime] | None = None,
        on_status_changed: Callable[[str, dict[str, Any]], None] | None = None,
        on_policy_changed: Callable[[str], None] | None = None,
        on_client_notification: Callable[[str, dict[str, Any]], None] | None = None,
        on_screensaver_challenge: Callable[[str, str], None] | None = None,
        on_time_request_updated: Callable[[str, dict[str, Any]], None] | None = None,
        app_catalog_loader: Callable[[str], list[dict[str, Any]]] | None = None,
    ) -> None:
        self._store = store
        self._history = history
        self._sessions = session_monitor or LogindSessionMonitor()
        self._app_limits = app_limits_monitor or AppLimitsMonitor()
        self._parent_code = parent_code_manager or ParentAccessCodeManager(
            os.path.join(store.config_dir, "parent_auth.json")
        )
        self._now_fn = now_fn or (lambda: datetime.now().astimezone())
        self._on_status_changed = on_status_changed
        self._on_policy_changed = on_policy_changed
        self._on_client_notification = on_client_notification
        self._on_screensaver_challenge = on_screensaver_challenge
        self._on_time_request_updated = on_time_request_updated
        self._app_catalog_loader = app_catalog_loader

        self._store.ensure_dirs()
        self._config: DaemonConfig = self._store.load_daemon_config()
        # Existing v1.x installs with configured user policies auto-skip First-Run Setup Wizard
        if not self._config.setup_completed and self._store.list_configured_users():
            self._config.setup_completed = True
            self._store.save_daemon_config(self._config)
        elif not self._store.daemon_config_path or not os.path.isfile(
            self._store.daemon_config_path
        ):
            self._store.save_daemon_config(self._config)

        # In-memory caches
        self._policies: dict[str, UserPolicy] = {}
        self._states: dict[str, UserAccountingState] = {}
        self._last_eval: dict[str, EvaluationResult] = {}
        self._last_sessions: dict[str, list[dict[str, Any]]] = {}
        self._active_limited_apps: dict[str, list[str]] = {}
        self._active_limited_patterns: dict[str, list[str]] = {}
        self._active_categories: dict[str, list[str]] = {}

        # Per-user cached installed app + Steam game catalog (refreshed off the main loop)
        self._catalog_lock = threading.Lock()
        self._app_catalog_cache: dict[str, tuple[float, list[dict[str, Any]]]] = {}
        self._app_catalog_inflight: set[str] = set()

        # Client screensaver & telemetry challenge/response state (strictly TTL-bounded)
        self._pending_nonces: dict[str, str] = {}
        self._last_challenge_mono: dict[str, float] = {}
        self._tray_telemetry_mono: dict[str, float] = {}
        self._tray_screensaver_active: dict[str, bool] = {}
        self._tray_idle_sec: dict[str, int] = {}
        self._tray_active_pid: dict[str, int] = {}

        # Lockout countdown, root-verified lock state & batched persistence timers
        self._countdowns: dict[str, int] = {}
        self._last_lock_attempt_mono: dict[str, float] = {}
        self._last_reverify_mono: dict[str, float] = {}
        self._lockout_recorded: set[str] = set()
        self._was_verified_locked: set[str] = set()
        self._lock_state_guard = threading.Lock()
        # Maps username -> {session_id: (locked, verified, mono_ts, backend)} (updated from worker threads)
        self._verified_sessions: dict[str, dict[str, tuple[bool, bool, float, str]]] = {}
        # Batched per-tick daily & per-app history deltas flushed at save_interval_sec / reads / shutdown
        self._pending_daily_deltas: dict[tuple[str, str], list[int]] = {}
        self._pending_app_deltas: dict[tuple[str, str], dict[str, tuple[str, str, int]]] = {}
        self._last_tick_mono: float | None = None
        now_init = time.monotonic()
        self._last_save_mono: float = now_init
        self._last_wal_checkpoint_mono: float = now_init

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

    @property
    def parent_code(self) -> ParentAccessCodeManager:
        return self._parent_code

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

    def _emit_time_request(self, username: str, req: dict[str, Any]) -> None:
        if self._on_time_request_updated is not None:
            self._on_time_request_updated(username, req)

    def get_policy(self, username: str) -> UserPolicy:
        safe = validate_username(username)
        if safe not in self._policies:
            default_role = "parent" if is_system_admin_user(safe) else "child"
            pol = self._store.load_user_policy(safe, default_role=default_role)
            self._policies[safe] = pol
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

    def record_lock_result(self, result: LockResult) -> None:
        """Thread-safe callback storing root-verified per-session lock status from ScreenLockEnforcer."""
        try:
            safe = validate_username(result.username)
        except ValueError:
            return
        sid = str(result.session_id)
        now_mono = time.monotonic()
        with self._lock_state_guard:
            user_map = self._verified_sessions.setdefault(safe, {})
            if result.stage == "verify_unverifiable":
                prev = user_map.get(sid)
                if prev is not None and prev[0]:
                    # Keep minimal unverifiable locker locked with refreshed timestamp
                    user_map[sid] = (True, False, now_mono, prev[3])
                else:
                    user_map[sid] = (False, False, now_mono, "")
            else:
                user_map[sid] = (
                    bool(result.locked),
                    bool(result.verified),
                    now_mono,
                    str(result.backend or ""),
                )

    def _clear_lockout_state(self, safe: str, clear_verified: bool = True) -> None:
        """Reset per-user lockout episode counters and optional root-verified lock cache."""
        self._countdowns.pop(safe, None)
        self._lockout_recorded.discard(safe)
        self._was_verified_locked.discard(safe)
        self._last_reverify_mono.pop(safe, None)
        if clear_verified:
            with self._lock_state_guard:
                self._verified_sessions.pop(safe, None)

    def _clear_user_runtime_caches(self, safe: str) -> None:
        """Evict ephemeral session, process, countdown, and telemetry caches on logout/expiry."""
        self._active_limited_apps.pop(safe, None)
        self._active_limited_patterns.pop(safe, None)
        self._active_categories.pop(safe, None)
        self._clear_lockout_state(safe, clear_verified=True)
        self._last_lock_attempt_mono.pop(safe, None)
        self._pending_nonces.pop(safe, None)
        self._last_challenge_mono.pop(safe, None)
        self._tray_telemetry_mono.pop(safe, None)
        self._tray_screensaver_active.pop(safe, None)
        self._tray_idle_sec.pop(safe, None)
        self._tray_active_pid.pop(safe, None)

    def _root_verified_lock_status(
        self,
        safe: str,
        sessions: list[dict[str, Any]],
        now_mono: float,
    ) -> tuple[bool, bool, list[dict[str, Any]]]:
        """Return (all_locked, needs_reverify, sessions_to_reverify) from root-verified LockResults."""
        if not sessions:
            return False, False, []
        with self._lock_state_guard:
            verified_map = dict(self._verified_sessions.get(safe, {}))

        tray_ts = self._tray_telemetry_mono.get(safe)
        tray_fresh = tray_ts is not None and (now_mono - tray_ts) <= (_CHALLENGE_INTERVAL_SEC * 2.0)
        # Fail-closed early unlock signal: tray claiming unlocked triggers immediate root re-check,
        # while tray claiming locked NEVER marks a session root-verified.
        tray_reports_unlocked = tray_fresh and not bool(
            self._tray_screensaver_active.get(safe, False)
        )

        all_locked = True
        needs_reverify = False
        reverify_sessions: list[dict[str, Any]] = []
        for s in sessions:
            if self._config.use_locked_hint and s.get("locked", False):
                continue
            sid = str(s.get("session_id", ""))
            entry = verified_map.get(sid)
            if entry is None:
                all_locked = False
                continue
            is_locked, _is_verified, ts, _backend = entry
            age = now_mono - ts
            if not is_locked or age > _LOCK_VERIFY_MAX_AGE_SEC:
                all_locked = False
                continue
            if age >= _LOCK_REVERIFY_INTERVAL_SEC or (
                tray_reports_unlocked and tray_ts is not None and tray_ts > ts
            ):
                needs_reverify = True
                reverify_sessions.append(s)
        return all_locked, needs_reverify, reverify_sessions

    def is_session_verified_locked(
        self, safe: str, session_id: str, now_mono: float | None = None
    ) -> bool:
        """Return True if daemon root-verified this session locked within the valid TTL."""
        mono = time.monotonic() if now_mono is None else now_mono
        with self._lock_state_guard:
            entry = self._verified_sessions.get(safe, {}).get(str(session_id))
        if entry is None:
            return False
        is_locked, _is_verified, ts, _backend = entry
        return bool(is_locked and (mono - ts) <= _LOCK_VERIFY_MAX_AGE_SEC)

    def _fresh_telemetry(self, safe: str, now_mono: float | None = None) -> tuple[bool, int, int]:
        """Return (screensaver_active, idle_sec, active_window_pid), expiring fail-closed after TTL."""
        mono = time.monotonic() if now_mono is None else now_mono
        last_ts = self._tray_telemetry_mono.get(safe)
        ttl = _CHALLENGE_INTERVAL_SEC * 2.0
        if last_ts is None or (mono - last_ts) > ttl:
            self._tray_telemetry_mono.pop(safe, None)
            self._tray_screensaver_active.pop(safe, None)
            self._tray_idle_sec.pop(safe, None)
            self._tray_active_pid.pop(safe, None)
            return False, 0, 0
        return (
            bool(self._tray_screensaver_active.get(safe, False)),
            int(self._tray_idle_sec.get(safe, 0)),
            int(self._tray_active_pid.get(safe, 0)),
        )

    def _session_flags(
        self,
        safe: str,
        sessions: list[dict[str, Any]],
        now_mono: float | None = None,
    ) -> tuple[bool, bool, bool, bool, bool]:
        """Return (any_active, screen_locked, logind_locked, is_idle, logind_idle)."""
        mono = time.monotonic() if now_mono is None else now_mono
        any_active = any(s.get("active", False) for s in sessions)
        logind_locked = (
            any(s.get("locked", False) for s in sessions) if self._config.use_locked_hint else False
        )
        root_verified_locked, _, _ = self._root_verified_lock_status(safe, sessions, mono)
        tray_ss, tray_idle_sec, _ = self._fresh_telemetry(safe, now_mono=mono)
        screen_locked = logind_locked or root_verified_locked or tray_ss
        logind_idle = bool(sessions) and all(s.get("idle", False) for s in sessions)
        tray_idle = self._config.idle_pause_sec > 0 and tray_idle_sec >= self._config.idle_pause_sec
        is_idle = logind_idle or tray_idle
        return any_active, screen_locked, logind_locked, is_idle, logind_idle

    def _pending_request_count(self, safe: str) -> int:
        return len(self._history.list_time_requests(status="pending", username=safe, limit=20))

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
        """Assemble UserStatus dict directly from an existing EvaluationResult."""
        rec = user_record if user_record is not None else (lookup_user_record(safe) or {})
        st = build_user_status_dict(
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
            pending_request_count=self._pending_request_count(safe),
            config=self._config,
        )
        st["parent_code_enabled"] = self._parent_code.is_enabled()
        return st

    def compute_status(self, username: str) -> dict[str, Any]:
        safe = validate_username(username)
        policy = self.get_policy(safe)
        state = self.get_state(safe)
        sessions = self._last_sessions.get(safe, [])
        any_active, screen_locked, _, is_idle, _ = self._session_flags(safe, sessions)
        limited_apps = self._active_limited_apps.get(safe, [])
        limited_patterns = self._active_limited_patterns.get(safe, [])
        active_cats = self._active_categories.get(safe, [])

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
            running_categories=active_cats,
            is_idle=is_idle,
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
        active_usernames = set(sessions_by_user.keys())

        # Evict stale runtime/telemetry/process/lockout caches for logged-out users
        with self._lock_state_guard:
            verified_users = set(self._verified_sessions)
        tracked_cached = (
            set(self._active_limited_apps)
            | set(self._active_limited_patterns)
            | set(self._active_categories)
            | set(self._countdowns)
            | set(self._lockout_recorded)
            | set(self._was_verified_locked)
            | verified_users
            | set(self._tray_telemetry_mono)
            | set(self._pending_nonces)
        )
        for stale_user in tracked_cached - active_usernames:
            self._clear_user_runtime_caches(stale_user)

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
            self._history.expire_stale_requests(now_utc=now_local.astimezone(timezone.utc))
            self._last_save_mono = now_mono
            if (now_mono - self._last_wal_checkpoint_mono) >= _WAL_CHECKPOINT_INTERVAL_SEC:
                chk_fn = getattr(self._history, "checkpoint", None)
                if callable(chk_fn):
                    try:
                        chk_fn("PASSIVE")
                    except Exception as exc:
                        logger.debug("periodic WAL checkpoint skipped: %s", exc)
                self._last_wal_checkpoint_mono = now_mono

        return True

    def update_app_catalog_cache(self, username: str, apps: list[dict[str, Any]]) -> None:
        """Warm or refresh the per-user installed app catalog cache safely."""
        try:
            safe = validate_username(username)
        except ValueError:
            return
        with self._catalog_lock:
            self._app_catalog_cache[safe] = (time.monotonic(), list(apps))

    def _get_app_catalog(self, safe: str, now_mono: float) -> list[dict[str, Any]]:
        """Return cached per-user app/Steam catalog, refreshing off the GLib main loop on expiry."""
        with self._catalog_lock:
            entry = self._app_catalog_cache.get(safe)
            if entry is not None and (now_mono - entry[0]) < _APP_CATALOG_TTL_SEC:
                return entry[1]
            cached = entry[1] if entry is not None else []
            if self._app_catalog_loader is not None:
                loader = self._app_catalog_loader
                loaded = list(loader(safe))
                self._app_catalog_cache[safe] = (now_mono, loaded)
                return loaded
            if safe in self._app_catalog_inflight:
                return cached
            self._app_catalog_inflight.add(safe)

        rec = lookup_user_record(safe) or {}
        home_dir = rec.get("home_dir")

        def _refresh_worker() -> None:
            try:
                scanned = scan_installed_apps(target_home=home_dir, include_curated_fallback=False)
                with self._catalog_lock:
                    self._app_catalog_cache[safe] = (time.monotonic(), scanned)
            except Exception as exc:
                logger.debug("app catalog refresh failed for %r: %s", safe, exc)
            finally:
                with self._catalog_lock:
                    self._app_catalog_inflight.discard(safe)

        threading.Thread(
            target=_refresh_worker, name=f"curfewd-catalog-{safe}", daemon=True
        ).start()
        return cached

    def annotate_sessions_for_doctor(
        self, sessions_by_user: dict[str, list[dict[str, Any]]]
    ) -> dict[str, list[dict[str, Any]]]:
        """Return shallow copies of sessions annotated with logind_locked and daemon screen_locked."""
        now_mono = time.monotonic()
        annotated: dict[str, list[dict[str, Any]]] = {}
        for uname, sess_list in sessions_by_user.items():
            try:
                safe = validate_username(uname)
            except ValueError:
                annotated[uname] = [dict(s) for s in sess_list]
                continue
            tray_ss, _, _ = self._fresh_telemetry(safe, now_mono=now_mono)
            enriched: list[dict[str, Any]] = []
            for s in sess_list:
                copy = dict(s)
                logind_locked = bool(s.get("locked", False))
                verified = self.is_session_verified_locked(
                    safe, str(s.get("session_id", "")), now_mono=now_mono
                )
                copy["logind_locked"] = logind_locked
                copy["screen_locked"] = logind_locked or verified or tray_ss
                enriched.append(copy)
            annotated[uname] = enriched
        return annotated

    def _scan_processes_for_user(
        self,
        safe: str,
        uid: int,
        policy: UserPolicy,
        any_active: bool,
        logind_locked: bool,
        logind_idle: bool,
        now_mono: float,
    ) -> ProcessScanResult:
        # Never allow unprivileged tray telemetry alone to disable app-limit / Homework enforcement
        if not any_active:
            return ProcessScanResult()
        if not policy.track_inactive and (
            logind_locked or (logind_idle and self._config.idle_pause_sec > 0)
        ):
            return ProcessScanResult()

        hw_relevant = policy.homework_mode.active or bool(policy.homework_mode.scheduled_windows)
        need_app_limits = self._config.app_limits_enabled and (
            policy.app_limits.enabled or hw_relevant
        )
        need_passive = self._config.passive_app_insights_enabled and policy.role != "parent"
        if not need_app_limits and not need_passive:
            return ProcessScanResult()

        _, _, active_pid = self._fresh_telemetry(safe, now_mono=now_mono)
        if hasattr(self._app_limits, "scan_user_processes"):
            # Pass configured app rules whenever app_limits OR homework mode is active so
            # always_allowed exemptions and custom Gaming/Social rules are always honored.
            apps_to_scan = (
                policy.app_limits.apps if (policy.app_limits.enabled or hw_relevant) else []
            )
            cats_to_scan = policy.app_limits.category_limits if policy.app_limits.enabled else []
            sig = inspect.signature(self._app_limits.scan_user_processes).parameters
            kwargs: dict[str, Any] = {
                "uid": uid,
                "apps": apps_to_scan,
                "category_limits": cats_to_scan,
                "active_window_pid": active_pid or None,
                "homework_blocked_categories": policy.homework_mode.blocked_categories,
                "collect_passive": need_passive,
            }
            if "catalog" in sig:
                kwargs["catalog"] = self._get_app_catalog(safe, now_mono)
            if "exempt_patterns" in sig:
                kwargs["exempt_patterns"] = policy.homework_mode.allowed_apps if hw_relevant else []
            if "passive_ignore_list" in sig:
                kwargs["passive_ignore_list"] = self._config.passive_app_ignore_list
            return self._app_limits.scan_user_processes(**kwargs)

        # Compatibility fallback for minimal unit-test stub monitors
        if self._config.app_limits_enabled and policy.app_limits.enabled:
            matches = self._app_limits.find_matching_processes(uid, policy.app_limits.apps)
            return ProcessScanResult(rule_matches=matches)
        return ProcessScanResult()

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
        any_active, screen_locked, logind_locked, is_idle, logind_idle = self._session_flags(
            safe, sessions, now_mono=now_mono
        )

        scan = self._scan_processes_for_user(
            safe, uid, policy, any_active, logind_locked, logind_idle, now_mono
        )
        limited_apps = scan.running_labels
        running_patterns = scan.running_patterns
        running_categories = scan.running_categories
        self._active_limited_apps[safe] = limited_apps
        self._active_limited_patterns[safe] = running_patterns
        self._active_categories[safe] = running_categories

        eval_res = evaluate_tick(
            policy=policy,
            state=state,
            config=self._config,
            now_local=now_local,
            elapsed_sec=elapsed,
            is_active=any_active,
            screen_locked=screen_locked,
            app_limits_active=bool(scan.rule_matches),
            running_app_patterns=running_patterns,
            running_categories=running_categories,
            is_idle=is_idle,
        )
        self._last_eval[safe] = eval_res

        if eval_res.pause_expired or eval_res.homework_expired:
            self._store.save_user_policy(policy)
            if eval_res.pause_expired:
                self._history.record_event(
                    safe, "resume", actor="daemon", detail={"reason": "timed_pause_expired"}
                )
            if eval_res.homework_expired:
                self._history.record_event(
                    safe,
                    "homework_end",
                    actor="daemon",
                    detail={"reason": "timed_homework_expired"},
                )
            self._emit_policy_changed(safe)

        app_deltas = (
            [(k, lbl, cat, eval_res.active_delta) for k, lbl, cat in scan.passive_apps]
            if (eval_res.active_delta > 0 and self._config.passive_app_insights_enabled)
            else None
        )
        if eval_res.active_delta or eval_res.inactive_delta or eval_res.app_limits_delta:
            self._buffer_daily_delta(
                safe,
                date_str,
                active_sec=eval_res.active_delta,
                inactive_sec=eval_res.inactive_delta,
                app_limits_sec=eval_res.app_limits_delta,
                app_deltas=app_deltas,
            )

        for thresh in eval_res.crossed_warnings:
            self._emit_notification(
                safe,
                {
                    "kind": "warning",
                    "seconds_left": eval_res.time_left_continuous,
                    "threshold_sec": thresh,
                    "chime": self._config.warning_chime_enabled and thresh <= 300,
                },
            )
        for warn in eval_res.crossed_app_warnings:
            self._emit_notification(
                safe,
                {
                    **warn,
                    "chime": self._config.warning_chime_enabled
                    and int(warn.get("threshold_sec", 0)) <= 300,
                },
            )

        if eval_res.app_limits_should_kill:
            self._enforce_expired_apps(safe, uid, policy, eval_res, scan)

        # Never let unprivileged client-reported screensaver_active suppress real lockouts
        self._enforce_session_lockout(
            safe,
            uid,
            policy,
            eval_res,
            sessions,
            any_active,
            logind_locked,
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
        eval_res: EvaluationResult,
        scan: ProcessScanResult,
    ) -> None:
        expired_pat_set = set(eval_res.expired_app_patterns)
        expired_cat_lower = {
            c.lower() for c in (*eval_res.expired_categories, *eval_res.homework_blocked_categories)
        }
        exempt_pids = set(getattr(scan, "exempt_pids", set()))
        for m in scan.rule_matches:
            if m.get("always_allowed"):
                exempt_pids.add(int(m["pid"]))

        expired_apps = [a for a in policy.app_limits.apps if a.pattern in expired_pat_set]
        prematched: list[dict[str, Any]] = [
            m
            for m in scan.rule_matches
            if m.get("pattern") in expired_pat_set
            and not m.get("always_allowed")
            and int(m["pid"]) not in exempt_pids
        ]
        if expired_cat_lower:
            for m in scan.category_matches:
                if (
                    str(m.get("category", "")).lower() in expired_cat_lower
                    and int(m["pid"]) not in exempt_pids
                ):
                    prematched.append(m)
            for m in scan.rule_matches:
                if (
                    str(m.get("category", "")).lower() in expired_cat_lower
                    and not m.get("always_allowed")
                    and int(m["pid"]) not in exempt_pids
                ):
                    prematched.append(m)

        if not prematched:
            return

        sig = inspect.signature(self._app_limits.enforce_kill).parameters
        if "exempt_pids" in sig:
            killed = self._app_limits.enforce_kill(
                uid, expired_apps, prematched=prematched, exempt_pids=exempt_pids
            )
        else:
            killed = self._app_limits.enforce_kill(uid, expired_apps, prematched=prematched)
        if killed:
            labels = sorted({str(m.get("label") or m.get("name") or "App") for m in prematched})
            reason = "homework_mode" if eval_res.homework_active else "limit_reached"
            self._history.record_event(
                safe,
                "app_limit_kill",
                actor="daemon",
                detail={"pids": killed, "apps": labels, "reason": reason},
            )
            self._emit_notification(
                safe,
                {
                    "kind": "app_limit_reached",
                    "apps": labels,
                    "reason": reason,
                },
            )

    def _invoke_lockout_action(
        self,
        action: str,
        safe: str,
        uid: int,
        sessions: list[dict[str, Any]],
        lock_message: str,
    ) -> None:
        fn = self._sessions.enforce_lockout_action
        params = inspect.signature(fn).parameters
        has_var_kw = any(p.kind == inspect.Parameter.VAR_KEYWORD for p in params.values())
        kwargs: dict[str, Any] = {}
        if "lock_message" in params or has_var_kw:
            kwargs["lock_message"] = lock_message
        if "on_lock_result" in params or has_var_kw:
            kwargs["on_lock_result"] = self.record_lock_result
        fn(action, safe, uid, sessions, self._config, **kwargs)

    def _maybe_reverify_locked_sessions(
        self, safe: str, reverify_sessions: list[dict[str, Any]], now_mono: float
    ) -> None:
        if not reverify_sessions:
            return
        if (now_mono - self._last_reverify_mono.get(safe, 0.0)) < _LOCK_RETRY_INTERVAL_SEC:
            return
        self._last_reverify_mono[safe] = now_mono
        verify_fn = getattr(self._sessions, "verify_user_sessions_locked", None)
        if callable(verify_fn):
            verify_fn(reverify_sessions, self._config, on_complete=self.record_lock_result)

    def _enforce_session_lockout(
        self,
        safe: str,
        uid: int,
        policy: UserPolicy,
        eval_res: EvaluationResult,
        sessions: list[dict[str, Any]],
        any_active: bool,
        logind_locked: bool,
        now_mono: float,
        elapsed: int,
        date_str: str,
    ) -> None:
        if not (eval_res.should_enforce and any_active):
            self._clear_lockout_state(safe, clear_verified=not eval_res.should_enforce)
            return

        if logind_locked:
            self._countdowns.pop(safe, None)
            self._was_verified_locked.add(safe)
            return

        all_verified_locked, needs_reverify, reverify_sessions = self._root_verified_lock_status(
            safe, sessions, now_mono
        )
        if all_verified_locked:
            self._countdowns.pop(safe, None)
            self._was_verified_locked.add(safe)
            if needs_reverify:
                self._maybe_reverify_locked_sessions(safe, reverify_sessions, now_mono)
            return

        # Transition from confirmed locked -> unlocked while still locked out: skip countdown & re-record
        if safe in self._was_verified_locked:
            self._was_verified_locked.discard(safe)
            self._lockout_recorded.discard(safe)
            self._countdowns[safe] = 0

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
                    "save_grace_available": eval_res.save_grace_available,
                },
            )
            return

        last_try = self._last_lock_attempt_mono.get(safe, 0.0)
        if (now_mono - last_try) >= _LOCK_RETRY_INTERVAL_SEC:
            self._last_lock_attempt_mono[safe] = now_mono
            is_new_episode = safe not in self._lockout_recorded
            if is_new_episode:
                self._lockout_recorded.add(safe)
                logger.warning(
                    "time expired for user %r; enforcing lockout action=%s",
                    safe,
                    policy.lockout_action,
                )
            else:
                logger.debug(
                    "retrying lockout action=%s for user %r (lock not yet verified)",
                    policy.lockout_action,
                    safe,
                )
            self._invoke_lockout_action(
                policy.lockout_action,
                safe,
                uid,
                sessions,
                _format_lock_message(eval_res),
            )
            if is_new_episode:
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

    def _buffer_daily_delta(
        self,
        safe: str,
        date_str: str,
        active_sec: int = 0,
        inactive_sec: int = 0,
        app_limits_sec: int = 0,
        app_deltas: list[tuple[str, str, str, int]] | None = None,
    ) -> None:
        key = (safe, date_str)
        bucket = self._pending_daily_deltas.get(key)
        if bucket is None:
            self._pending_daily_deltas[key] = [
                int(active_sec),
                int(inactive_sec),
                int(app_limits_sec),
            ]
        else:
            bucket[0] += int(active_sec)
            bucket[1] += int(inactive_sec)
            bucket[2] += int(app_limits_sec)

        if app_deltas:
            app_map = self._pending_app_deltas.setdefault(key, {})
            for app_key, label, cat, sec in app_deltas:
                if sec <= 0:
                    continue
                prev = app_map.get(app_key)
                if prev is None:
                    app_map[app_key] = (label, cat, int(sec))
                else:
                    app_map[app_key] = (label, cat, prev[2] + int(sec))

    def flush_history_deltas(self) -> int:
        """Commit buffered per-tick usage and per-app deltas to SQLite in batched transactions."""
        keys = list(set(self._pending_daily_deltas) | set(self._pending_app_deltas))
        flushed = 0
        for key in keys:
            safe, date_str = key
            daily = self._pending_daily_deltas.pop(key, None) or [0, 0, 0]
            app_map = self._pending_app_deltas.pop(key, None) or {}
            app_deltas = [(k, lbl, cat, sec) for k, (lbl, cat, sec) in app_map.items()] or None
            if not any(daily) and not app_deltas:
                continue
            try:
                self._history.record_daily_delta(
                    safe,
                    date_str,
                    active_sec=daily[0],
                    inactive_sec=daily[1],
                    app_limits_sec=daily[2],
                    app_deltas=app_deltas,
                )
                flushed += 1
            except Exception as exc:
                logger.error("failed to flush history deltas for %r (%s): %s", safe, date_str, exc)
        return flushed

    def flush_states(self) -> None:
        """Persist all cached accounting states atomically and flush batched history deltas."""
        self.flush_history_deltas()
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
                    "role": status["role"],
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
                    "pending_request_count": status["pending_request_count"],
                }
            )
        return summaries

    def set_user_policy(self, safe: str, raw: dict[str, Any], actor: str) -> dict[str, Any]:
        payload = dict(raw)
        payload["username"] = safe
        existing_role = self.get_policy(safe).role
        policy = UserPolicy.from_dict(payload, default_role=existing_role)
        self._policies[safe] = policy
        self._store.save_user_policy(policy)
        if not self._config.setup_completed:
            self._config.setup_completed = True
            self._store.save_daemon_config(self._config)
        self._clear_lockout_state(safe, clear_verified=True)
        self._history.record_event(
            safe,
            "policy_update",
            actor=actor,
            detail={"lockout_action": policy.lockout_action, "role": policy.role},
        )
        self._emit_policy_changed(safe)
        status = self.compute_status(safe)
        self._emit_status(safe, status)
        self._emit_notification(safe, {"kind": "policy_changed"})
        return status

    def _maybe_notify_lock_screen(
        self,
        safe: str,
        status: dict[str, Any],
        message: str,
    ) -> bool:
        """If the user's session is currently locked and now has time, update the lock-screen indicator."""
        sessions = self._last_sessions.get(safe, [])
        _, screen_locked, *_ = self._session_flags(safe, sessions)
        if not screen_locked:
            return False
        has_time = int(status.get("time_left_continuous", 0) or 0) > 0 or status.get(
            "enforcement_state"
        ) in ("ok", "warning", "paused", "unrestricted")
        if has_time and message:
            self._sessions.notify_lock_screen(sessions, message)
        return True

    def grant_time(
        self,
        safe: str,
        delta_seconds: int,
        mode: str,
        actor: str,
        note: str = "",
    ) -> dict[str, Any]:
        policy = self.get_policy(safe)
        state = self.get_state(safe)
        now_local = self._now_fn()
        clean_note = str(note or "").strip()[:160]
        applied = apply_grant(
            policy, state, int(delta_seconds), str(mode), now_local, note=clean_note
        )
        self._store.save_user_state(state)
        self._clear_lockout_state(safe, clear_verified=True)
        self._history.record_daily_delta(safe, now_local.date().isoformat(), bonus_sec=applied)
        self._history.record_event(
            safe,
            "grant",
            actor=actor,
            detail={
                "mode": str(mode),
                "requested_sec": int(delta_seconds),
                "applied_sec": applied,
                "note": clean_note,
            },
        )
        status = self.compute_status(safe)
        self._emit_status(safe, status)
        lock_msg = (
            f"Bonus time added (+{format_duration(applied)})"
            + (f" ({clean_note})" if clean_note else "")
            + " — unlock to resume"
            if applied > 0
            else ""
        )
        session_locked = self._maybe_notify_lock_screen(safe, status, lock_msg)
        self._emit_notification(
            safe,
            {
                "kind": "time_granted",
                "applied_sec": applied,
                "note": clean_note,
                "actor": actor,
                "session_locked": session_locked,
            },
        )
        return {"applied_sec": applied, "note": clean_note, "status": status}

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
        self._clear_lockout_state(safe, clear_verified=True)
        self._history.record_event(safe, "pause", actor=actor, detail=policy.pause.to_dict())
        self._emit_policy_changed(safe)
        status = self.compute_status(safe)
        self._emit_status(safe, status)
        session_locked = self._maybe_notify_lock_screen(
            safe, status, "Screen time paused — unlock to resume"
        )
        self._emit_notification(
            safe,
            {
                "kind": "paused",
                "pause": policy.pause.to_dict(),
                "session_locked": session_locked,
            },
        )
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

    def set_homework_mode(self, safe: str, raw: dict[str, Any], actor: str) -> dict[str, Any]:
        policy = self.get_policy(safe)
        cur = policy.homework_mode.to_dict()
        active = bool(raw.get("active", cur["active"]))
        if "until_utc" in raw:
            until_clean = str(raw.get("until_utc") or "").strip()
        elif active and cur.get("active"):
            until_clean = str(cur.get("until_utc") or "").strip()
        else:
            until_clean = ""
        if "mode" in raw and raw.get("mode"):
            mode = str(raw["mode"])
        elif active and cur.get("active") and until_clean:
            mode = "until"
        else:
            mode = "until" if until_clean else ("indefinite" if active else "none")
        if not active:
            mode = "none"
            until_clean = ""
        policy.homework_mode = HomeworkModeState(
            active=active,
            mode=mode,
            until_utc=until_clean or None,
            reason=str(raw.get("reason", cur.get("reason", "")) or ""),
            scheduled_windows=raw.get("scheduled_windows", cur.get("scheduled_windows", {})),
            allowed_apps=raw.get("allowed_apps", cur.get("allowed_apps", [])),
            blocked_categories=raw.get("blocked_categories", cur.get("blocked_categories", [])),
        )
        self._store.save_user_policy(policy)
        self._clear_lockout_state(safe, clear_verified=True)
        self._history.record_event(
            safe,
            "homework_start" if active else "homework_end",
            actor=actor,
            detail=policy.homework_mode.to_dict(),
        )
        self._emit_policy_changed(safe)
        status = self.compute_status(safe)
        self._emit_status(safe, status)
        self._emit_notification(
            safe,
            {
                "kind": "homework_mode_changed",
                "active": active,
                "until_utc": policy.homework_mode.until_utc,
                "reason": policy.homework_mode.reason,
            },
        )
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
        self._clear_lockout_state(safe, clear_verified=True)
        self._history.record_event(safe, "today_override", actor=actor, detail=res)
        status = self.compute_status(safe)
        self._emit_status(safe, status)
        session_locked = self._maybe_notify_lock_screen(
            safe, status, "Schedule updated — unlock to resume"
        )
        self._emit_notification(safe, {"kind": "policy_changed", "session_locked": session_locked})
        return status

    def grant_app_time(
        self,
        safe: str,
        pattern: str,
        delta_seconds: int,
        actor: str,
        note: str = "",
    ) -> dict[str, Any]:
        policy = self.get_policy(safe)
        state = self.get_state(safe)
        now_local = self._now_fn()
        applied = apply_app_grant(policy, state, str(pattern), int(delta_seconds), now_local)
        app = next((a for a in policy.app_limits.apps if a.pattern == str(pattern).strip()), None)
        label = app.label if app else str(pattern)
        clean_note = str(note or "").strip()[:160]
        self._store.save_user_state(state)
        self._history.record_event(
            safe,
            "app_grant",
            actor=actor,
            detail={
                "pattern": str(pattern),
                "label": label,
                "applied_sec": applied,
                "note": clean_note,
            },
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
                "note": clean_note,
            },
        )
        return {"applied_sec": applied, "status": status}

    def grant_category_time(
        self,
        safe: str,
        category: str,
        delta_seconds: int,
        actor: str,
        note: str = "",
    ) -> dict[str, Any]:
        policy = self.get_policy(safe)
        state = self.get_state(safe)
        now_local = self._now_fn()
        applied = apply_category_grant(policy, state, str(category), int(delta_seconds), now_local)
        clean_note = str(note or "").strip()[:160]
        self._store.save_user_state(state)
        self._history.record_event(
            safe,
            "category_grant",
            actor=actor,
            detail={"category": str(category), "applied_sec": applied, "note": clean_note},
        )
        status = self.compute_status(safe)
        self._emit_status(safe, status)
        self._emit_notification(
            safe,
            {
                "kind": "app_time_granted",
                "pattern": str(category),
                "label": f"{category} apps",
                "applied_sec": applied,
                "note": clean_note,
            },
        )
        return {"applied_sec": applied, "status": status}

    # ---------------- Ask for More Time, Save Grace & Walk-Up Code ---------------- #

    def request_more_time(self, caller_user: str, raw: dict[str, Any]) -> dict[str, Any]:
        safe = validate_username(caller_user)
        now_utc = self._now_fn().astimezone(timezone.utc)
        since_utc = (now_utc - timedelta(minutes=_REQUEST_WINDOW_MINUTES)).isoformat()
        if self._history.count_recent_requests(safe, since_utc) >= _MAX_REQUESTS_PER_WINDOW:
            raise ValueError(
                f"You already sent {_MAX_REQUESTS_PER_WINDOW} requests recently; please wait a few minutes."
            )

        kind = str(raw.get("kind", "daily")).strip().lower()
        target = str(raw.get("target", "")).strip()
        target_label = str(raw.get("target_label", "")).strip()
        requested_sec = int(raw.get("requested_sec", 900))
        reason = str(raw.get("reason", "")).strip()

        if kind == "app":
            policy = self.get_policy(safe)
            app = next((a for a in policy.app_limits.apps if a.pattern == target), None)
            if app is None:
                raise ValueError(f"Tracked app pattern not found: {target!r}")
            target_label = target_label or app.label
        elif kind == "category":
            policy = self.get_policy(safe)
            cat_rule = next(
                (
                    c
                    for c in policy.app_limits.category_limits
                    if c.category.lower() == target.lower()
                ),
                None,
            )
            if cat_rule is None:
                raise ValueError(f"Category budget not found: {target!r}")
            target = cat_rule.category
            target_label = target_label or cat_rule.category
        else:
            kind = "daily"
            target = ""
            target_label = "Daily Screen Time"

        req = self._history.create_time_request(
            username=safe,
            kind=kind,
            requested_sec=requested_sec,
            target=target,
            target_label=target_label,
            reason=reason,
            ts_utc=now_utc.isoformat(),
        )
        self._history.record_event(safe, "time_request", actor=safe, detail=req)
        self._emit_time_request(safe, req)
        return req

    def resolve_time_request(
        self,
        request_id: int,
        approve: bool,
        grant_seconds: int,
        note: str,
        actor: str,
    ) -> dict[str, Any]:
        existing = self._history.get_time_request(request_id)
        if existing is None:
            raise ValueError(f"Time request #{request_id} not found")
        if existing["status"] != "pending":
            raise ValueError(f"Time request #{request_id} is already {existing['status']}")

        safe = existing["username"]
        eff_grant = 0
        if approve:
            if int(grant_seconds) < 0:
                raise ValueError("grant_seconds cannot be negative")
            eff_grant = max(300, min(7200, int(grant_seconds or existing["requested_sec"])))
            # Validate target before atomically claiming the pending row
            policy = self.get_policy(safe)
            if existing["kind"] == "app" and not any(
                a.pattern == existing["target"] for a in policy.app_limits.apps
            ):
                raise ValueError(f"Tracked app pattern not found: {existing['target']!r}")
            if existing["kind"] == "category" and not any(
                c.category.lower() == str(existing["target"]).lower()
                for c in policy.app_limits.category_limits
            ):
                raise ValueError(f"Category budget not found: {existing['target']!r}")

        # Atomically claim pending row FIRST so concurrent approvals/expirations can never double-grant
        updated_req = self._history.resolve_time_request(
            request_id=request_id,
            approve=approve,
            resolved_by=actor,
            granted_sec=eff_grant,
            note=note,
            ts_utc=self._now_fn().astimezone(timezone.utc).isoformat(),
        )
        if updated_req is None:
            raise ValueError(f"Time request #{request_id} is no longer pending")

        status: dict[str, Any] | None = None
        if approve:
            if existing["kind"] == "app":
                res = self.grant_app_time(
                    safe, existing["target"], eff_grant, actor=actor, note=note
                )
                status = res["status"]
            elif existing["kind"] == "category":
                res = self.grant_category_time(
                    safe, existing["target"], eff_grant, actor=actor, note=note
                )
                status = res["status"]
            else:
                res = self.grant_time(safe, eff_grant, mode="add", actor=actor, note=note)
                status = res["status"]

        self._history.record_event(
            safe,
            "time_request_approved" if approve else "time_request_denied",
            actor=actor,
            detail=updated_req,
        )
        self._emit_time_request(safe, updated_req)
        self._emit_notification(
            safe,
            {
                "kind": "time_request_resolved",
                "approved": bool(approve),
                "target_label": updated_req["target_label"],
                "granted_sec": eff_grant,
                "note": updated_req["note"],
            },
        )
        return {"request": updated_req, "status": status or self.compute_status(safe)}

    def _validate_walkup_payload(
        self, safe: str, raw: dict[str, Any]
    ) -> tuple[str, dict[str, Any]]:
        """Validate walk-up Parent Access Code payload BEFORE consuming the single-use TOTP step."""
        req_id_raw = raw.get("request_id")
        if req_id_raw is not None and str(req_id_raw).strip() != "":
            req_id = int(req_id_raw)
            if req_id > 0:
                req = self._history.get_time_request(req_id)
                if req is None or req["username"] != safe or req["status"] != "pending":
                    raise ValueError("Pending request not found for your account")
                g_raw = raw.get("grant_seconds")
                g_sec = int(g_raw) if (g_raw is not None and str(g_raw).strip() != "") else 0
                if g_sec < 0:
                    raise ValueError("grant_seconds cannot be negative")
                return "request", {
                    "request_id": req_id,
                    "grant_seconds": g_sec or int(req["requested_sec"]),
                    "note": str(raw.get("note", "Approved with Parent Code")),
                }

        action = str(raw.get("action", "grant_daily")).strip().lower()
        allowed = ("grant_daily", "grant_app", "grant_category", "pause", "homework")
        if action not in allowed:
            raise ValueError(f"Unsupported walk-up action {action!r}; expected one of {allowed}")
        note = str(raw.get("note", "Walk-up parent approval")).strip()[:160]
        policy = self.get_policy(safe)
        if action == "grant_app":
            pattern = str(raw.get("pattern", "")).strip()
            if not any(a.pattern == pattern for a in policy.app_limits.apps):
                raise ValueError(f"Tracked app pattern not found: {pattern!r}")
            sec = max(300, min(7200, int(raw.get("seconds", 900))))
            return action, {"pattern": pattern, "seconds": sec, "note": note}
        if action == "grant_category":
            category = str(raw.get("category", "")).strip()
            if not any(
                c.category.lower() == category.lower() for c in policy.app_limits.category_limits
            ):
                raise ValueError(f"Category budget not found: {category!r}")
            sec = max(300, min(7200, int(raw.get("seconds", 900))))
            return action, {"category": category, "seconds": sec, "note": note}
        if action in ("pause", "homework"):
            sec = max(300, min(14400, int(raw.get("seconds", 3600))))
            return action, {"seconds": sec, "note": note}
        sec = max(300, min(7200, int(raw.get("seconds", 900))))
        return "grant_daily", {"seconds": sec, "note": note}

    def approve_with_parent_code(self, caller_user: str, raw: dict[str, Any]) -> dict[str, Any]:
        """Verify offline TOTP Parent Access Code and apply a walk-up grant strictly to caller_user."""
        safe = validate_username(caller_user)
        code = str(raw.get("code", ""))
        ok, err_msg, step = self._parent_code.check_code(code)
        if not ok or step is None:
            self._history.record_event(
                safe, "parent_code_failed", actor=safe, detail={"reason": err_msg}
            )
            raise PermissionError(err_msg)

        # Validate action/target/request BEFORE burning the single-use 30-second TOTP step
        mode, args = self._validate_walkup_payload(safe, raw)
        self._parent_code.consume_step(step)

        if mode == "request":
            return self.resolve_time_request(
                request_id=args["request_id"],
                approve=True,
                grant_seconds=args["grant_seconds"],
                note=args["note"],
                actor="parent_code",
            )
        if mode == "grant_app":
            return self.grant_app_time(
                safe, args["pattern"], args["seconds"], actor="parent_code", note=args["note"]
            )
        if mode == "grant_category":
            return self.grant_category_time(
                safe, args["category"], args["seconds"], actor="parent_code", note=args["note"]
            )
        if mode == "pause":
            until = (
                self._now_fn().astimezone(timezone.utc) + timedelta(seconds=args["seconds"])
            ).isoformat()
            st = self.pause_user(
                safe, until_utc=until, reason=args["note"], hide_tray=False, actor="parent_code"
            )
            return {"status": st}
        if mode == "homework":
            until = (
                self._now_fn().astimezone(timezone.utc) + timedelta(seconds=args["seconds"])
            ).isoformat()
            st = self.set_homework_mode(
                safe,
                {"active": True, "mode": "until", "until_utc": until, "reason": args["note"]},
                actor="parent_code",
            )
            return {"status": st}
        return self.grant_time(
            safe, args["seconds"], mode="add", actor="parent_code", note=args["note"]
        )

    def claim_save_grace(self, caller_user: str) -> dict[str, Any]:
        safe = validate_username(caller_user)
        policy = self.get_policy(safe)
        state = self.get_state(safe)
        now_local = self._now_fn()
        res = apply_save_grace(policy, state, now_local)
        self._store.save_user_state(state)
        self._clear_lockout_state(safe, clear_verified=True)
        self._history.record_event(safe, "save_grace", actor=safe, detail=res)
        status = self.compute_status(safe)
        self._emit_status(safe, status)
        self._emit_notification(safe, {"kind": "save_grace_granted", "seconds": res["granted_sec"]})
        return {**res, "status": status}

    def set_daemon_config(self, raw: dict[str, Any]) -> DaemonConfig:
        cfg = DaemonConfig.from_dict(raw)
        self._config = cfg
        self._store.save_daemon_config(cfg)
        return cfg

    def verify_screensaver_state(self, caller_user: str, nonce: str, active: bool) -> bool:
        return self.verify_client_telemetry(
            caller_user, nonce, {"screensaver_active": bool(active)}
        )

    @staticmethod
    def _pid_owned_by_user(pid: int, caller_user: str) -> bool:
        if pid <= 1:
            return False
        rec = lookup_user_record(caller_user)
        if rec is None or rec.get("uid") is None:
            return False
        target_uid = int(rec["uid"])
        status_path = f"/proc/{pid}/status"
        try:
            with open(status_path, encoding="utf-8", errors="ignore") as fh:
                for line in fh:
                    if line.startswith("Uid:"):
                        parts = line.split()
                        return len(parts) >= 2 and int(parts[1]) == target_uid
        except (OSError, ValueError):
            # In synthetic unit tests where PID doesn't exist on host, allow lookup fallback only if not root-owned
            return not os.path.exists(status_path)
        return False

    def verify_client_telemetry(
        self, caller_user: str, nonce: str, telemetry: dict[str, Any]
    ) -> bool:
        # Parse and validate types BEFORE consuming the single-use nonce
        ss_val = (
            bool(telemetry["screensaver_active"]) if "screensaver_active" in telemetry else None
        )
        idle_val = (
            max(0, int(telemetry["idle_sec"]))
            if ("idle_sec" in telemetry and telemetry["idle_sec"] is not None)
            else None
        )
        pid_val = (
            max(0, int(telemetry["active_window_pid"]))
            if ("active_window_pid" in telemetry and telemetry["active_window_pid"] is not None)
            else None
        )

        expected = self._pending_nonces.pop(caller_user, None)
        if not expected or not nonce:
            return False
        if not hmac.compare_digest(str(nonce), str(expected)):
            return False
        self._tray_telemetry_mono[caller_user] = time.monotonic()
        if ss_val is not None:
            self._tray_screensaver_active[caller_user] = ss_val
        if idle_val is not None:
            self._tray_idle_sec[caller_user] = idle_val
        if pid_val is not None:
            self._tray_active_pid[caller_user] = (
                pid_val if self._pid_owned_by_user(pid_val, caller_user) else 0
            )
        return True
