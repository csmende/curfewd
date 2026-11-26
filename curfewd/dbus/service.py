"""Thin org.curfewd.Daemon1 D-Bus adapter, Polkit gate, and async RPC executor (Spec 005)."""

import json
import logging
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from typing import Any

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
from curfewd.engine.applimits import AppLimitsMonitor
from curfewd.engine.doctor import apply_dconf_lock_fix, run_doctor
from curfewd.engine.runtime import EnforcementRuntime, UserNotFoundLookupError
from curfewd.engine.sessions import LogindSessionMonitor
from curfewd.history import HistoryStore
from curfewd.models import DaemonConfig
from curfewd.storage import AtomicJsonStore

logger = logging.getLogger("curfewd.service")

DBUS_ERR_PERMISSION = "org.curfewd.Daemon1.Error.PermissionDenied"
DBUS_ERR_NOT_FOUND = "org.curfewd.Daemon1.Error.UserNotFound"
DBUS_ERR_VALIDATION = "org.curfewd.Daemon1.Error.ValidationError"
DBUS_ERR_INTERNAL = "org.curfewd.Daemon1.Error.InternalError"


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
        app_limits_monitor: AppLimitsMonitor | None = None,
        authorizer: PolkitAuthorizer | None = None,
        now_fn: Callable[[], datetime] | None = None,
        doctor_fixer: Callable[[], dict[str, Any]] | None = None,
        on_config_changed: Callable[[DaemonConfig], None] | None = None,
        bus_name_str: str = DBUS_BUS_NAME,
        own_name: bool = True,
    ) -> None:
        self._bus = bus
        self._authorizer = authorizer or PolkitAuthorizer(bus)
        self._doctor_fixer = doctor_fixer or apply_dconf_lock_fix
        self._on_config_changed = on_config_changed
        self._rpc_pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="curfewd-rpc")

        sessions = session_monitor or LogindSessionMonitor(system_bus=bus)
        self._runtime = EnforcementRuntime(
            store=store,
            history=history,
            session_monitor=sessions,
            app_limits_monitor=app_limits_monitor,
            now_fn=now_fn,
            on_status_changed=lambda u, st: self.UserStatusChanged(u, json.dumps(st)),
            on_policy_changed=self.PolicyChanged,
            on_client_notification=lambda u, p: self.ClientNotification(u, json.dumps(p)),
            on_screensaver_challenge=self.ScreensaverChallenge,
        )

        if own_name:
            self._bus_name = dbus.service.BusName(bus_name_str, bus=bus, replace_existing=True)
            super().__init__(self._bus_name, DBUS_OBJECT_PATH)
        else:
            self._bus_name = None
            super().__init__(bus, DBUS_OBJECT_PATH)

        logger.info("org.curfewd.Daemon1 initialized (object=%s)", DBUS_OBJECT_PATH)

    # Expose runtime challenge dicts for integration test introspection
    @property
    def _pending_nonces(self) -> dict[str, str]:
        return self._runtime._pending_nonces

    @property
    def _tray_screensaver_active(self) -> dict[str, bool]:
        return self._runtime._tray_screensaver_active

    def tick(self) -> bool:
        """Run one accounting & enforcement cycle (called on GLib main loop)."""
        return self._runtime.tick()

    def flush_states(self) -> None:
        """Persist all cached accounting states atomically to disk."""
        self._runtime.flush_states()

    def shutdown(self) -> None:
        """Flush cached state and stop the bounded async RPC worker pool."""
        self._runtime.flush_states()
        self._rpc_pool.shutdown(wait=False, cancel_futures=True)

    # ---------------- Auth, Validation & Async Helpers ---------------- #

    def _require_auth(self, sender: str, action_id: str) -> tuple[int, str]:
        try:
            return self._authorizer.authorize(sender, action_id)
        except AuthorizationError as exc:
            raise _DBusTypedError(DBUS_ERR_PERMISSION, str(exc)) from exc

    def _resolve_caller(self, sender: str) -> str:
        try:
            _, caller_user = self._authorizer.resolve_sender_user(sender)
            return caller_user
        except AuthorizationError as exc:
            raise _DBusTypedError(DBUS_ERR_PERMISSION, str(exc)) from exc

    def _ensure_known_user(self, username: str) -> tuple[str, dict[str, Any] | None]:
        try:
            return self._runtime.ensure_known_user(username)
        except UserNotFoundLookupError as exc:
            raise _DBusTypedError(DBUS_ERR_NOT_FOUND, str(exc)) from exc
        except ValueError as exc:
            raise _DBusTypedError(DBUS_ERR_VALIDATION, str(exc)) from exc

    @staticmethod
    def _parse_json_object(raw_json: str, label: str) -> dict[str, Any]:
        try:
            parsed = json.loads(str(raw_json))
            if not isinstance(parsed, dict):
                raise ValueError(f"{label} must be a JSON object")
            return parsed
        except (ValueError, TypeError) as exc:
            raise _DBusTypedError(DBUS_ERR_VALIDATION, f"Invalid {label.lower()}: {exc}") from exc

    @staticmethod
    def _map_worker_error(exc: Exception) -> _DBusTypedError:
        if isinstance(exc, _DBusTypedError):
            return exc
        if isinstance(exc, PermissionError):
            return _DBusTypedError(DBUS_ERR_PERMISSION, str(exc))
        if isinstance(exc, (ValueError, KeyError, TypeError)):
            return _DBusTypedError(DBUS_ERR_VALIDATION, str(exc))
        return _DBusTypedError(DBUS_ERR_INTERNAL, str(exc))

    def _run_async(
        self,
        work_fn: Callable[[], Any],
        on_success: Callable[[Any], str],
        reply_cb: Callable[[str], None],
        error_cb: Callable[[Exception], None],
    ) -> None:
        """Run blocking work on the bounded RPC pool and finalize replies on the GLib thread."""
        from gi.repository import GLib

        def _finish_ok(result: Any) -> bool:
            try:
                reply_cb(on_success(result))
            except Exception as exc:
                error_cb(self._map_worker_error(exc))
            return False

        def _finish_err(exc: Exception) -> bool:
            error_cb(self._map_worker_error(exc))
            return False

        def _worker() -> None:
            try:
                result = work_fn()
            except Exception as exc:
                GLib.idle_add(_finish_err, exc)
                return
            GLib.idle_add(_finish_ok, result)

        self._rpc_pool.submit(_worker)

    # ---------------- org.curfewd.Daemon1.Admin ---------------- #

    @dbus.service.method(
        DBUS_ADMIN_INTERFACE, in_signature="", out_signature="s", sender_keyword="sender"
    )
    def ListUsers(self, sender: str = "") -> str:
        self._require_auth(sender, POLKIT_ACTION_READ)
        return json.dumps({"users": self._runtime.list_users()})

    @dbus.service.method(
        DBUS_ADMIN_INTERFACE, in_signature="s", out_signature="s", sender_keyword="sender"
    )
    def GetUserStatus(self, username: str, sender: str = "") -> str:
        self._require_auth(sender, POLKIT_ACTION_READ)
        safe, _ = self._ensure_known_user(str(username))
        return json.dumps(self._runtime.compute_status(safe))

    @dbus.service.method(
        DBUS_ADMIN_INTERFACE, in_signature="ss", out_signature="s", sender_keyword="sender"
    )
    def SetUserPolicy(self, username: str, policy_json: str, sender: str = "") -> str:
        _, actor = self._require_auth(sender, POLKIT_ACTION_MANAGE)
        safe, _ = self._ensure_known_user(str(username))
        raw = self._parse_json_object(policy_json, "Policy payload")
        try:
            status = self._runtime.set_user_policy(safe, raw, actor)
        except (ValueError, KeyError, TypeError) as exc:
            raise _DBusTypedError(DBUS_ERR_VALIDATION, f"Invalid policy: {exc}") from exc
        return json.dumps(status)

    @dbus.service.method(
        DBUS_ADMIN_INTERFACE, in_signature="sis", out_signature="s", sender_keyword="sender"
    )
    def GrantTime(self, username: str, delta_seconds: int, mode: str, sender: str = "") -> str:
        _, actor = self._require_auth(sender, POLKIT_ACTION_MANAGE)
        safe, _ = self._ensure_known_user(str(username))
        try:
            result = self._runtime.grant_time(safe, int(delta_seconds), str(mode), actor)
        except ValueError as exc:
            raise _DBusTypedError(DBUS_ERR_VALIDATION, str(exc)) from exc
        return json.dumps(result)

    @dbus.service.method(
        DBUS_ADMIN_INTERFACE, in_signature="sssb", out_signature="s", sender_keyword="sender"
    )
    def PauseUser(
        self, username: str, until_utc: str, reason: str, hide_tray: bool, sender: str = ""
    ) -> str:
        _, actor = self._require_auth(sender, POLKIT_ACTION_MANAGE)
        safe, _ = self._ensure_known_user(str(username))
        try:
            status = self._runtime.pause_user(safe, until_utc, reason, bool(hide_tray), actor)
        except ValueError as exc:
            raise _DBusTypedError(DBUS_ERR_VALIDATION, str(exc)) from exc
        return json.dumps(status)

    @dbus.service.method(
        DBUS_ADMIN_INTERFACE, in_signature="s", out_signature="s", sender_keyword="sender"
    )
    def ResumeUser(self, username: str, sender: str = "") -> str:
        _, actor = self._require_auth(sender, POLKIT_ACTION_MANAGE)
        safe, _ = self._ensure_known_user(str(username))
        return json.dumps(self._runtime.resume_user(safe, actor))

    @dbus.service.method(
        DBUS_ADMIN_INTERFACE,
        in_signature="s",
        out_signature="s",
        sender_keyword="sender",
        async_callbacks=("reply_cb", "error_cb"),
    )
    def LockUser(
        self,
        username: str,
        reply_cb: Callable[[str], None],
        error_cb: Callable[[Exception], None],
        sender: str = "",
    ) -> None:
        _, actor = self._require_auth(sender, POLKIT_ACTION_LOCK)
        safe, _ = self._ensure_known_user(str(username))
        cfg = self._runtime.config
        sessions_by_user = self._runtime.sessions.list_user_sessions(cfg.tracked_session_types)
        sessions = sessions_by_user.get(safe, self._runtime.last_sessions_for(safe))
        if not sessions:
            reply_cb(
                json.dumps(
                    {
                        "username": safe,
                        "locked": False,
                        "sessions": [],
                        "detail": "No active graphical sessions",
                    }
                )
            )
            return

        monitor = self._runtime.sessions
        if hasattr(monitor, "prepare_session_locks") and hasattr(monitor, "execute_fallback_locks"):
            # GLib-thread Phase 1: Layer 1 logind LockSession + pure-Python SessionContext snapshots
            contexts = monitor.prepare_session_locks(sessions)
            work_fn = lambda: monitor.execute_fallback_locks(contexts, cfg)  # noqa: E731
        else:
            work_fn = lambda: monitor.lock_user_sessions(sessions, cfg, async_mode=False)  # noqa: E731

        def _on_locked(results: list[Any]) -> str:
            payload = {
                "username": safe,
                "locked": any(r.locked for r in results),
                "verified": all(r.verified for r in results) if results else False,
                "sessions": [r.to_dict() for r in results],
            }
            self._runtime.history.record_event(safe, "lock_manual", actor=actor, detail=payload)
            return json.dumps(payload)

        self._run_async(work_fn, _on_locked, reply_cb, error_cb)

    @dbus.service.method(
        DBUS_ADMIN_INTERFACE, in_signature="si", out_signature="s", sender_keyword="sender"
    )
    def GetUsageHistory(self, username: str, days: int, sender: str = "") -> str:
        self._require_auth(sender, POLKIT_ACTION_READ)
        safe, _ = self._ensure_known_user(str(username))
        hist = self._runtime.history.get_usage_history(
            safe, days=int(days), now_local=self._runtime.now_local()
        )
        return json.dumps(hist)

    @dbus.service.method(
        DBUS_ADMIN_INTERFACE, in_signature="", out_signature="s", sender_keyword="sender"
    )
    def GetDaemonConfig(self, sender: str = "") -> str:
        self._require_auth(sender, POLKIT_ACTION_READ)
        return json.dumps(self._runtime.config.to_dict())

    @dbus.service.method(
        DBUS_ADMIN_INTERFACE, in_signature="s", out_signature="s", sender_keyword="sender"
    )
    def SetDaemonConfig(self, config_json: str, sender: str = "") -> str:
        self._require_auth(sender, POLKIT_ACTION_MANAGE)
        raw = self._parse_json_object(config_json, "Daemon config")
        try:
            cfg = self._runtime.set_daemon_config(raw)
        except (ValueError, KeyError, TypeError) as exc:
            raise _DBusTypedError(DBUS_ERR_VALIDATION, f"Invalid daemon config: {exc}") from exc
        if self._on_config_changed is not None:
            self._on_config_changed(cfg)
        return json.dumps(cfg.to_dict())

    @dbus.service.method(
        DBUS_ADMIN_INTERFACE, in_signature="ss", out_signature="s", sender_keyword="sender"
    )
    def SetTodayOverride(self, username: str, override_json: str, sender: str = "") -> str:
        _, actor = self._require_auth(sender, POLKIT_ACTION_MANAGE)
        safe, _ = self._ensure_known_user(str(username))
        raw = self._parse_json_object(override_json, "Override payload")
        try:
            status = self._runtime.set_today_override(safe, raw, actor)
        except (ValueError, KeyError, TypeError) as exc:
            raise _DBusTypedError(DBUS_ERR_VALIDATION, f"Invalid today override: {exc}") from exc
        return json.dumps(status)

    @dbus.service.method(
        DBUS_ADMIN_INTERFACE, in_signature="ssi", out_signature="s", sender_keyword="sender"
    )
    def GrantAppTime(
        self, username: str, pattern: str, delta_seconds: int, sender: str = ""
    ) -> str:
        _, actor = self._require_auth(sender, POLKIT_ACTION_MANAGE)
        safe, _ = self._ensure_known_user(str(username))
        try:
            result = self._runtime.grant_app_time(safe, str(pattern), int(delta_seconds), actor)
        except ValueError as exc:
            raise _DBusTypedError(DBUS_ERR_VALIDATION, str(exc)) from exc
        return json.dumps(result)

    @dbus.service.method(
        DBUS_ADMIN_INTERFACE,
        in_signature="",
        out_signature="s",
        sender_keyword="sender",
        async_callbacks=("reply_cb", "error_cb"),
    )
    def RunDoctor(
        self,
        reply_cb: Callable[[str], None],
        error_cb: Callable[[Exception], None],
        sender: str = "",
    ) -> None:
        self._require_auth(sender, POLKIT_ACTION_READ)
        cfg = self._runtime.config
        sessions_by_user = self._runtime.sessions.list_user_sessions(cfg.tracked_session_types)
        config_dir = self._runtime.store.config_dir
        state_dir = self._runtime.store.state_dir

        def _work() -> dict[str, Any]:
            return run_doctor(
                config_dir=config_dir,
                state_dir=state_dir,
                sessions_by_user=sessions_by_user,
                dbus_connected=True,
            )

        self._run_async(_work, json.dumps, reply_cb, error_cb)

    @dbus.service.method(
        DBUS_ADMIN_INTERFACE,
        in_signature="",
        out_signature="s",
        sender_keyword="sender",
        async_callbacks=("reply_cb", "error_cb"),
    )
    def ApplyDoctorFix(
        self,
        reply_cb: Callable[[str], None],
        error_cb: Callable[[Exception], None],
        sender: str = "",
    ) -> None:
        self._require_auth(sender, POLKIT_ACTION_MANAGE)
        cfg = self._runtime.config
        sessions_by_user = self._runtime.sessions.list_user_sessions(cfg.tracked_session_types)
        config_dir = self._runtime.store.config_dir
        state_dir = self._runtime.store.state_dir
        fixer = self._doctor_fixer

        def _work() -> dict[str, Any]:
            try:
                fix_result = fixer()
            except OSError as exc:
                if isinstance(exc, PermissionError):
                    raise
                raise _DBusTypedError(
                    DBUS_ERR_INTERNAL, f"Failed to apply lock-screen protection: {exc}"
                ) from exc
            report = run_doctor(
                config_dir=config_dir,
                state_dir=state_dir,
                sessions_by_user=sessions_by_user,
                dbus_connected=True,
            )
            return {"fix_applied": fix_result, "doctor": report}

        self._run_async(_work, json.dumps, reply_cb, error_cb)

    # ---------------- org.curfewd.Daemon1.Client (sender-UID bound) ---------------- #

    @dbus.service.method(
        DBUS_CLIENT_INTERFACE, in_signature="", out_signature="s", sender_keyword="sender"
    )
    def GetMyStatus(self, sender: str = "") -> str:
        caller_user = self._resolve_caller(sender)
        return json.dumps(self._runtime.compute_status(caller_user))

    @dbus.service.method(
        DBUS_CLIENT_INTERFACE, in_signature="i", out_signature="s", sender_keyword="sender"
    )
    def GetMyHistory(self, days: int, sender: str = "") -> str:
        caller_user = self._resolve_caller(sender)
        safe_days = max(1, min(30, int(days)))
        hist = self._runtime.history.get_usage_history(
            caller_user, days=safe_days, now_local=self._runtime.now_local()
        )
        # Omit admin audit events from unprivileged self-view
        hist["events"] = []
        return json.dumps(hist)

    @dbus.service.method(
        DBUS_CLIENT_INTERFACE, in_signature="sb", out_signature="b", sender_keyword="sender"
    )
    def ReportScreensaverState(self, nonce: str, active: bool, sender: str = "") -> bool:
        caller_user = self._resolve_caller(sender)
        return self._runtime.verify_screensaver_state(caller_user, nonce, bool(active))

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
