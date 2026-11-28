"""Thin org.curfewd.Daemon1 D-Bus adapter, Polkit gate, and async RPC executor (Spec 005 + Spec 006)."""

import inspect
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
    POLKIT_ACTION_QUICK_GRANT,
    POLKIT_ACTION_READ,
)
from curfewd.dbus.polkit import AuthorizationError, PolkitAuthorizer
from curfewd.engine.applimits import AppLimitsMonitor
from curfewd.engine.doctor import apply_all_lock_fixes, run_doctor
from curfewd.engine.runtime import EnforcementRuntime, UserNotFoundLookupError
from curfewd.engine.sessions import LogindSessionMonitor
from curfewd.history import HistoryStore
from curfewd.models import DaemonConfig
from curfewd.storage import AtomicJsonStore
from curfewd.ui.appscan import scan_installed_apps

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
        doctor_fixer: Callable[..., dict[str, Any]] | None = None,
        on_config_changed: Callable[[DaemonConfig], None] | None = None,
        bus_name_str: str = DBUS_BUS_NAME,
        own_name: bool = True,
    ) -> None:
        self._bus = bus
        self._authorizer = authorizer or PolkitAuthorizer(bus)
        self._doctor_fixer = doctor_fixer or apply_all_lock_fixes
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
            on_time_request_updated=lambda u, r: self.TimeRequestUpdated(u, json.dumps(r)),
            on_unlock_detected=self._schedule_unlock_recheck,
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

    def _schedule_unlock_recheck(self, username: str) -> None:
        """Marshal worker-thread unlock detections back to the GLib main loop safely."""
        from gi.repository import GLib

        def _run_on_main() -> bool:
            self._runtime.enforce_unlock_recheck(username)
            return False

        GLib.idle_add(_run_on_main)

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
        if isinstance(exc, UserNotFoundLookupError):
            return _DBusTypedError(DBUS_ERR_NOT_FOUND, str(exc))
        if isinstance(exc, PermissionError):
            return _DBusTypedError(DBUS_ERR_PERMISSION, str(exc))
        if isinstance(exc, (ValueError, KeyError, TypeError)):
            return _DBusTypedError(DBUS_ERR_VALIDATION, str(exc))
        return _DBusTypedError(DBUS_ERR_INTERNAL, str(exc))

    def _auth_then(
        self,
        sender: str,
        action_id: str,
        reply_cb: Callable[[str], None],
        error_cb: Callable[[Exception], None],
        handler: Callable[[str], str | None],
    ) -> None:
        """Authorize asynchronously via Polkit on GLib loop so prompts never block tick()."""

        def _on_ok(_uid: int, actor: str) -> None:
            try:
                res = handler(actor)
            except Exception as exc:
                error_cb(self._map_worker_error(exc))
                return
            if res is not None:
                reply_cb(res)

        def _on_err(exc: AuthorizationError) -> None:
            error_cb(_DBusTypedError(DBUS_ERR_PERMISSION, str(exc)))

        self._authorizer.authorize_async(sender, action_id, _on_ok, _on_err)

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

    def _dispatch_lock_async(
        self,
        safe: str,
        actor: str,
        event_kind: str,
        lock_message: str,
        reply_cb: Callable[[str], None],
        error_cb: Callable[[Exception], None],
    ) -> None:
        cfg = self._runtime.config
        sessions_by_user = self._runtime.sessions.list_user_sessions(cfg.tracked_session_types)
        sessions = sessions_by_user.get(safe, self._runtime.last_sessions_for(safe))
        if not sessions:
            reply_cb(
                json.dumps(
                    {
                        "username": safe,
                        "locked": False,
                        "verified": False,
                        "sessions": [],
                        "detail": "No active graphical sessions",
                    }
                )
            )
            return

        monitor = self._runtime.sessions
        if hasattr(monitor, "prepare_session_locks") and hasattr(monitor, "execute_fallback_locks"):
            contexts = monitor.prepare_session_locks(sessions)
            params = inspect.signature(monitor.execute_fallback_locks).parameters

            def _work_fn() -> list[Any]:
                if "lock_message" in params:
                    return monitor.execute_fallback_locks(contexts, cfg, lock_message=lock_message)
                return monitor.execute_fallback_locks(contexts, cfg)
        else:

            def _work_fn() -> list[Any]:
                return monitor.lock_user_sessions(sessions, cfg, async_mode=False)

        def _on_locked(results: list[Any]) -> str:
            payload = {
                "username": safe,
                "locked": any(r.locked for r in results),
                "verified": all(r.verified for r in results) if results else False,
                "sessions": [r.to_dict() for r in results],
            }
            self._runtime.history.record_event(safe, event_kind, actor=actor, detail=payload)
            return json.dumps(payload)

        self._run_async(_work_fn, _on_locked, reply_cb, error_cb)

    # ---------------- org.curfewd.Daemon1.Admin ---------------- #

    @dbus.service.method(
        DBUS_ADMIN_INTERFACE,
        in_signature="",
        out_signature="s",
        sender_keyword="sender",
        async_callbacks=("reply_cb", "error_cb"),
    )
    def ListUsers(
        self,
        reply_cb: Callable[[str], None],
        error_cb: Callable[[Exception], None],
        sender: str = "",
    ) -> None:
        self._auth_then(
            sender,
            POLKIT_ACTION_READ,
            reply_cb,
            error_cb,
            lambda _actor: json.dumps({"users": self._runtime.list_users()}),
        )

    @dbus.service.method(
        DBUS_ADMIN_INTERFACE,
        in_signature="s",
        out_signature="s",
        sender_keyword="sender",
        async_callbacks=("reply_cb", "error_cb"),
    )
    def GetUserStatus(
        self,
        username: str,
        reply_cb: Callable[[str], None],
        error_cb: Callable[[Exception], None],
        sender: str = "",
    ) -> None:
        def _do(_actor: str) -> str:
            safe, _ = self._ensure_known_user(str(username))
            return json.dumps(self._runtime.compute_status(safe))

        self._auth_then(sender, POLKIT_ACTION_READ, reply_cb, error_cb, _do)

    @dbus.service.method(
        DBUS_ADMIN_INTERFACE,
        in_signature="ss",
        out_signature="s",
        sender_keyword="sender",
        async_callbacks=("reply_cb", "error_cb"),
    )
    def SetUserPolicy(
        self,
        username: str,
        policy_json: str,
        reply_cb: Callable[[str], None],
        error_cb: Callable[[Exception], None],
        sender: str = "",
    ) -> None:
        def _do(actor: str) -> str:
            safe, _ = self._ensure_known_user(str(username))
            raw = self._parse_json_object(policy_json, "Policy payload")
            return json.dumps(self._runtime.set_user_policy(safe, raw, actor))

        self._auth_then(sender, POLKIT_ACTION_MANAGE, reply_cb, error_cb, _do)

    @dbus.service.method(
        DBUS_ADMIN_INTERFACE,
        in_signature="sis",
        out_signature="s",
        sender_keyword="sender",
        async_callbacks=("reply_cb", "error_cb"),
    )
    def GrantTime(
        self,
        username: str,
        delta_seconds: int,
        mode: str,
        reply_cb: Callable[[str], None],
        error_cb: Callable[[Exception], None],
        sender: str = "",
    ) -> None:
        def _do(actor: str) -> str:
            safe, _ = self._ensure_known_user(str(username))
            return json.dumps(self._runtime.grant_time(safe, int(delta_seconds), str(mode), actor))

        self._auth_then(sender, POLKIT_ACTION_MANAGE, reply_cb, error_cb, _do)

    @dbus.service.method(
        DBUS_ADMIN_INTERFACE,
        in_signature="ss",
        out_signature="s",
        sender_keyword="sender",
        async_callbacks=("reply_cb", "error_cb"),
    )
    def GrantTimeDetailed(
        self,
        username: str,
        payload_json: str,
        reply_cb: Callable[[str], None],
        error_cb: Callable[[Exception], None],
        sender: str = "",
    ) -> None:
        def _do(actor: str) -> str:
            safe, _ = self._ensure_known_user(str(username))
            raw = self._parse_json_object(payload_json, "Grant payload")
            res = self._runtime.grant_time(
                safe,
                int(raw.get("delta_seconds", 900)),
                str(raw.get("mode", "add")),
                actor,
                note=str(raw.get("note", "")),
            )
            return json.dumps(res)

        self._auth_then(sender, POLKIT_ACTION_MANAGE, reply_cb, error_cb, _do)

    @dbus.service.method(
        DBUS_ADMIN_INTERFACE,
        in_signature="sssb",
        out_signature="s",
        sender_keyword="sender",
        async_callbacks=("reply_cb", "error_cb"),
    )
    def PauseUser(
        self,
        username: str,
        until_utc: str,
        reason: str,
        hide_tray: bool,
        reply_cb: Callable[[str], None],
        error_cb: Callable[[Exception], None],
        sender: str = "",
    ) -> None:
        def _do(actor: str) -> str:
            safe, _ = self._ensure_known_user(str(username))
            return json.dumps(
                self._runtime.pause_user(safe, until_utc, reason, bool(hide_tray), actor)
            )

        self._auth_then(sender, POLKIT_ACTION_MANAGE, reply_cb, error_cb, _do)

    @dbus.service.method(
        DBUS_ADMIN_INTERFACE,
        in_signature="s",
        out_signature="s",
        sender_keyword="sender",
        async_callbacks=("reply_cb", "error_cb"),
    )
    def ResumeUser(
        self,
        username: str,
        reply_cb: Callable[[str], None],
        error_cb: Callable[[Exception], None],
        sender: str = "",
    ) -> None:
        def _do(actor: str) -> str:
            safe, _ = self._ensure_known_user(str(username))
            return json.dumps(self._runtime.resume_user(safe, actor))

        self._auth_then(sender, POLKIT_ACTION_MANAGE, reply_cb, error_cb, _do)

    @dbus.service.method(
        DBUS_ADMIN_INTERFACE,
        in_signature="ss",
        out_signature="s",
        sender_keyword="sender",
        async_callbacks=("reply_cb", "error_cb"),
    )
    def SetHomeworkMode(
        self,
        username: str,
        payload_json: str,
        reply_cb: Callable[[str], None],
        error_cb: Callable[[Exception], None],
        sender: str = "",
    ) -> None:
        def _do(actor: str) -> str:
            safe, _ = self._ensure_known_user(str(username))
            raw = self._parse_json_object(payload_json, "Homework mode payload")
            return json.dumps(self._runtime.set_homework_mode(safe, raw, actor))

        self._auth_then(sender, POLKIT_ACTION_MANAGE, reply_cb, error_cb, _do)

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
        def _do(actor: str) -> None:
            safe, _ = self._ensure_known_user(str(username))
            self._dispatch_lock_async(
                safe, actor, "lock_manual", "Locked by administrator", reply_cb, error_cb
            )

        self._auth_then(sender, POLKIT_ACTION_LOCK, reply_cb, error_cb, _do)

    @dbus.service.method(
        DBUS_ADMIN_INTERFACE,
        in_signature="s",
        out_signature="s",
        sender_keyword="sender",
        async_callbacks=("reply_cb", "error_cb"),
    )
    def TestLock(
        self,
        username: str,
        reply_cb: Callable[[str], None],
        error_cb: Callable[[Exception], None],
        sender: str = "",
    ) -> None:
        def _do(actor: str) -> None:
            safe, _ = self._ensure_known_user(str(username))
            self._dispatch_lock_async(
                safe, actor, "lock_test", "curfewd test lock — unlock normally", reply_cb, error_cb
            )

        self._auth_then(sender, POLKIT_ACTION_LOCK, reply_cb, error_cb, _do)

    @dbus.service.method(
        DBUS_ADMIN_INTERFACE,
        in_signature="si",
        out_signature="s",
        sender_keyword="sender",
        async_callbacks=("reply_cb", "error_cb"),
    )
    def GetUsageHistory(
        self,
        username: str,
        days: int,
        reply_cb: Callable[[str], None],
        error_cb: Callable[[Exception], None],
        sender: str = "",
    ) -> None:
        def _do(_actor: str) -> str:
            safe, _ = self._ensure_known_user(str(username))
            self._runtime.flush_history_deltas()
            hist = self._runtime.history.get_usage_history(
                safe, days=int(days), now_local=self._runtime.now_local()
            )
            return json.dumps(hist)

        self._auth_then(sender, POLKIT_ACTION_READ, reply_cb, error_cb, _do)

    @dbus.service.method(
        DBUS_ADMIN_INTERFACE,
        in_signature="",
        out_signature="s",
        sender_keyword="sender",
        async_callbacks=("reply_cb", "error_cb"),
    )
    def GetDaemonConfig(
        self,
        reply_cb: Callable[[str], None],
        error_cb: Callable[[Exception], None],
        sender: str = "",
    ) -> None:
        self._auth_then(
            sender,
            POLKIT_ACTION_READ,
            reply_cb,
            error_cb,
            lambda _actor: json.dumps(self._runtime.config.to_dict()),
        )

    @dbus.service.method(
        DBUS_ADMIN_INTERFACE,
        in_signature="s",
        out_signature="s",
        sender_keyword="sender",
        async_callbacks=("reply_cb", "error_cb"),
    )
    def SetDaemonConfig(
        self,
        config_json: str,
        reply_cb: Callable[[str], None],
        error_cb: Callable[[Exception], None],
        sender: str = "",
    ) -> None:
        def _do(_actor: str) -> str:
            raw = self._parse_json_object(config_json, "Daemon config")
            cfg = self._runtime.set_daemon_config(raw)
            if self._on_config_changed is not None:
                self._on_config_changed(cfg)
            return json.dumps(cfg.to_dict())

        self._auth_then(sender, POLKIT_ACTION_MANAGE, reply_cb, error_cb, _do)

    @dbus.service.method(
        DBUS_ADMIN_INTERFACE,
        in_signature="ss",
        out_signature="s",
        sender_keyword="sender",
        async_callbacks=("reply_cb", "error_cb"),
    )
    def SetTodayOverride(
        self,
        username: str,
        override_json: str,
        reply_cb: Callable[[str], None],
        error_cb: Callable[[Exception], None],
        sender: str = "",
    ) -> None:
        def _do(actor: str) -> str:
            safe, _ = self._ensure_known_user(str(username))
            raw = self._parse_json_object(override_json, "Override payload")
            return json.dumps(self._runtime.set_today_override(safe, raw, actor))

        self._auth_then(sender, POLKIT_ACTION_MANAGE, reply_cb, error_cb, _do)

    @dbus.service.method(
        DBUS_ADMIN_INTERFACE,
        in_signature="ssi",
        out_signature="s",
        sender_keyword="sender",
        async_callbacks=("reply_cb", "error_cb"),
    )
    def GrantAppTime(
        self,
        username: str,
        pattern: str,
        delta_seconds: int,
        reply_cb: Callable[[str], None],
        error_cb: Callable[[Exception], None],
        sender: str = "",
    ) -> None:
        def _do(actor: str) -> str:
            safe, _ = self._ensure_known_user(str(username))
            return json.dumps(
                self._runtime.grant_app_time(safe, str(pattern), int(delta_seconds), actor)
            )

        self._auth_then(sender, POLKIT_ACTION_MANAGE, reply_cb, error_cb, _do)

    @dbus.service.method(
        DBUS_ADMIN_INTERFACE,
        in_signature="ssi",
        out_signature="s",
        sender_keyword="sender",
        async_callbacks=("reply_cb", "error_cb"),
    )
    def GrantCategoryTime(
        self,
        username: str,
        category: str,
        delta_seconds: int,
        reply_cb: Callable[[str], None],
        error_cb: Callable[[Exception], None],
        sender: str = "",
    ) -> None:
        def _do(actor: str) -> str:
            safe, _ = self._ensure_known_user(str(username))
            return json.dumps(
                self._runtime.grant_category_time(safe, str(category), int(delta_seconds), actor)
            )

        self._auth_then(sender, POLKIT_ACTION_MANAGE, reply_cb, error_cb, _do)

    @dbus.service.method(
        DBUS_ADMIN_INTERFACE,
        in_signature="s",
        out_signature="s",
        sender_keyword="sender",
        async_callbacks=("reply_cb", "error_cb"),
    )
    def ListTimeRequests(
        self,
        status_filter: str,
        reply_cb: Callable[[str], None],
        error_cb: Callable[[Exception], None],
        sender: str = "",
    ) -> None:
        self._auth_then(
            sender,
            POLKIT_ACTION_READ,
            reply_cb,
            error_cb,
            lambda _actor: json.dumps(
                {
                    "requests": self._runtime.history.list_time_requests(
                        status=str(status_filter or "").strip()
                    )
                }
            ),
        )

    @dbus.service.method(
        DBUS_ADMIN_INTERFACE,
        in_signature="ibis",
        out_signature="s",
        sender_keyword="sender",
        async_callbacks=("reply_cb", "error_cb"),
    )
    def ResolveTimeRequest(
        self,
        request_id: int,
        approve: bool,
        grant_seconds: int,
        note: str,
        reply_cb: Callable[[str], None],
        error_cb: Callable[[Exception], None],
        sender: str = "",
    ) -> None:
        def _do(actor: str) -> str:
            res = self._runtime.resolve_time_request(
                int(request_id), bool(approve), int(grant_seconds), str(note), actor
            )
            return json.dumps(res)

        self._auth_then(sender, POLKIT_ACTION_MANAGE, reply_cb, error_cb, _do)

    @dbus.service.method(
        DBUS_ADMIN_INTERFACE,
        in_signature="iis",
        out_signature="s",
        sender_keyword="sender",
        async_callbacks=("reply_cb", "error_cb"),
    )
    def QuickApproveRequest(
        self,
        request_id: int,
        grant_seconds: int,
        note: str,
        reply_cb: Callable[[str], None],
        error_cb: Callable[[Exception], None],
        sender: str = "",
    ) -> None:
        """Non-cached Polkit (`auth_admin`) single-use walk-up approval for shared/child sessions."""

        def _do(actor: str) -> str:
            res = self._runtime.resolve_time_request(
                int(request_id), True, int(grant_seconds), str(note), actor
            )
            return json.dumps(res)

        self._auth_then(sender, POLKIT_ACTION_QUICK_GRANT, reply_cb, error_cb, _do)

    @dbus.service.method(
        DBUS_ADMIN_INTERFACE,
        in_signature="",
        out_signature="s",
        sender_keyword="sender",
        async_callbacks=("reply_cb", "error_cb"),
    )
    def GetParentCodeStatus(
        self,
        reply_cb: Callable[[str], None],
        error_cb: Callable[[Exception], None],
        sender: str = "",
    ) -> None:
        self._auth_then(
            sender,
            POLKIT_ACTION_READ,
            reply_cb,
            error_cb,
            lambda _actor: json.dumps(self._runtime.parent_code.status()),
        )

    @dbus.service.method(
        DBUS_ADMIN_INTERFACE,
        in_signature="bb",
        out_signature="s",
        sender_keyword="sender",
        async_callbacks=("reply_cb", "error_cb"),
    )
    def SetupParentCode(
        self,
        enable: bool,
        rotate: bool,
        reply_cb: Callable[[str], None],
        error_cb: Callable[[Exception], None],
        sender: str = "",
    ) -> None:
        """Require fresh non-cached auth_admin before enrolling, rotating, or disabling TOTP."""

        def _do(actor: str) -> str:
            res = self._runtime.parent_code.setup(enable=bool(enable), rotate=bool(rotate))
            self._runtime.history.record_event(
                actor,
                "parent_code_updated",
                actor=actor,
                detail={"enabled": bool(enable), "rotated": bool(rotate)},
            )
            return json.dumps(res)

        self._auth_then(sender, POLKIT_ACTION_QUICK_GRANT, reply_cb, error_cb, _do)

    @dbus.service.method(
        DBUS_ADMIN_INTERFACE,
        in_signature="s",
        out_signature="s",
        sender_keyword="sender",
        async_callbacks=("reply_cb", "error_cb"),
    )
    def ListInstalledApps(
        self,
        username: str,
        reply_cb: Callable[[str], None],
        error_cb: Callable[[Exception], None],
        sender: str = "",
    ) -> None:
        def _do(_actor: str) -> None:
            safe_user: str | None = None
            target_home: str | None = None
            if str(username or "").strip():
                safe_user, rec = self._ensure_known_user(str(username))
                target_home = (rec or {}).get("home_dir")

            def _work() -> dict[str, Any]:
                return {"apps": scan_installed_apps(target_home=target_home)}

            def _finish(result: dict[str, Any]) -> str:
                if safe_user:
                    self._runtime.update_app_catalog_cache(safe_user, result.get("apps") or [])
                return json.dumps(result)

            self._run_async(_work, _finish, reply_cb, error_cb)

        self._auth_then(sender, POLKIT_ACTION_READ, reply_cb, error_cb, _do)

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
        def _do(_actor: str) -> None:
            cfg = self._runtime.config
            raw_sessions = self._runtime.sessions.list_user_sessions(cfg.tracked_session_types)
            sessions_by_user = self._runtime.annotate_sessions_for_doctor(raw_sessions)
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

        self._auth_then(sender, POLKIT_ACTION_READ, reply_cb, error_cb, _do)

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
        def _do(_actor: str) -> None:
            cfg = self._runtime.config
            raw_sessions = self._runtime.sessions.list_user_sessions(cfg.tracked_session_types)
            sessions_by_user = self._runtime.annotate_sessions_for_doctor(raw_sessions)
            config_dir = self._runtime.store.config_dir
            state_dir = self._runtime.store.state_dir
            fixer = self._doctor_fixer

            def _work() -> dict[str, Any]:
                try:
                    params = inspect.signature(fixer).parameters
                    if "sessions_by_user" in params:
                        fix_result = fixer(sessions_by_user=sessions_by_user)
                    else:
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

        self._auth_then(sender, POLKIT_ACTION_MANAGE, reply_cb, error_cb, _do)

    # ---------------- org.curfewd.Daemon1.Client (sender-UID bound) ---------------- #

    @dbus.service.method(
        DBUS_CLIENT_INTERFACE, in_signature="", out_signature="s", sender_keyword="sender"
    )
    def GetMyStatus(self, sender: str = "") -> str:
        caller_user = self._resolve_caller(sender)
        st = self._runtime.compute_status(caller_user)
        st["parent_code_enabled"] = self._runtime.parent_code.is_enabled()
        return json.dumps(st)

    @dbus.service.method(
        DBUS_CLIENT_INTERFACE, in_signature="i", out_signature="s", sender_keyword="sender"
    )
    def GetMyHistory(self, days: int, sender: str = "") -> str:
        caller_user = self._resolve_caller(sender)
        safe_days = max(1, min(30, int(days)))
        self._runtime.flush_history_deltas()
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

    @dbus.service.method(
        DBUS_CLIENT_INTERFACE, in_signature="ss", out_signature="b", sender_keyword="sender"
    )
    def ReportClientTelemetry(self, nonce: str, telemetry_json: str, sender: str = "") -> bool:
        caller_user = self._resolve_caller(sender)
        raw = self._parse_json_object(telemetry_json, "Telemetry payload")
        try:
            return self._runtime.verify_client_telemetry(caller_user, nonce, raw)
        except (ValueError, TypeError) as exc:
            raise _DBusTypedError(DBUS_ERR_VALIDATION, f"Invalid telemetry: {exc}") from exc

    @dbus.service.method(
        DBUS_CLIENT_INTERFACE, in_signature="", out_signature="b", sender_keyword="sender"
    )
    def NotifySessionUnlocked(self, sender: str = "") -> bool:
        """Fail-closed early unlock interrupt from tray; triggers root-verified lock re-check."""
        caller_user = self._resolve_caller(sender)
        return self._runtime.trigger_unlock_reverify(caller_user)

    @dbus.service.method(
        DBUS_CLIENT_INTERFACE, in_signature="s", out_signature="s", sender_keyword="sender"
    )
    def RequestMoreTime(self, request_json: str, sender: str = "") -> str:
        caller_user = self._resolve_caller(sender)
        raw = self._parse_json_object(request_json, "Time request payload")
        try:
            req = self._runtime.request_more_time(caller_user, raw)
        except (ValueError, TypeError) as exc:
            raise _DBusTypedError(DBUS_ERR_VALIDATION, str(exc)) from exc
        return json.dumps(req)

    @dbus.service.method(
        DBUS_CLIENT_INTERFACE, in_signature="", out_signature="s", sender_keyword="sender"
    )
    def ListMyTimeRequests(self, sender: str = "") -> str:
        caller_user = self._resolve_caller(sender)
        reqs = self._runtime.history.list_time_requests(username=caller_user, limit=15)
        return json.dumps({"requests": reqs})

    @dbus.service.method(
        DBUS_CLIENT_INTERFACE, in_signature="", out_signature="s", sender_keyword="sender"
    )
    def ClaimSaveGrace(self, sender: str = "") -> str:
        caller_user = self._resolve_caller(sender)
        try:
            res = self._runtime.claim_save_grace(caller_user)
        except ValueError as exc:
            raise _DBusTypedError(DBUS_ERR_VALIDATION, str(exc)) from exc
        return json.dumps(res)

    @dbus.service.method(
        DBUS_CLIENT_INTERFACE, in_signature="s", out_signature="s", sender_keyword="sender"
    )
    def ApproveWithParentCode(self, payload_json: str, sender: str = "") -> str:
        caller_user = self._resolve_caller(sender)
        raw = self._parse_json_object(payload_json, "Parent code approval payload")
        try:
            res = self._runtime.approve_with_parent_code(caller_user, raw)
        except PermissionError as exc:
            raise _DBusTypedError(DBUS_ERR_PERMISSION, str(exc)) from exc
        except (ValueError, TypeError) as exc:
            raise _DBusTypedError(DBUS_ERR_VALIDATION, str(exc)) from exc
        return json.dumps(res)

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

    @dbus.service.signal(DBUS_EVENTS_INTERFACE, signature="ss")
    def TimeRequestUpdated(self, username: str, request_json: str) -> None:
        pass
