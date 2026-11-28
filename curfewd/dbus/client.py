"""Typed Python client SDK for org.curfewd.Daemon1 (shared by curfewctl, tray, and Spec 003 UI)."""

import json
from collections.abc import Callable
from typing import Any

import dbus

from curfewd import (
    DBUS_ADMIN_INTERFACE,
    DBUS_BUS_NAME,
    DBUS_CLIENT_INTERFACE,
    DBUS_EVENTS_INTERFACE,
    DBUS_OBJECT_PATH,
)


class CurfewError(Exception):
    """Base error raised by CurfewClient."""


class DaemonUnavailableError(CurfewError):
    """curfewd.service is not running or unreachable on D-Bus."""


class PermissionDeniedError(CurfewError):
    """Caller failed Polkit / root / curfewd-group authorization."""


class UserNotFoundError(CurfewError):
    """Requested Linux user does not exist or is untracked."""


class ValidationError(CurfewError):
    """Invalid policy, schedule, duration, or configuration payload."""


def _translate_dbus_error(
    exc: dbus.DBusException, *, interactive_admin: bool = False
) -> CurfewError:
    name = exc.get_dbus_name() or ""
    msg = exc.get_dbus_message() or str(exc)
    if "PermissionDenied" in name or "AccessDenied" in name:
        return PermissionDeniedError(msg)
    if "UserNotFound" in name:
        return UserNotFoundError(msg)
    if "ValidationError" in name:
        return ValidationError(msg)
    if "NoReply" in name and interactive_admin:
        return PermissionDeniedError(f"Authorization timed out or no response ({msg})")
    if any(
        k in name
        for k in ("ServiceUnknown", "NameHasNoOwner", "NoServer", "FileNotFound", "NoReply")
    ):
        return DaemonUnavailableError(
            f"{DBUS_BUS_NAME} is not reachable ({msg}). Is `curfewd.service` running?"
        )
    return CurfewError(f"{name}: {msg}" if name else msg)


class CurfewClient:
    """High-level client for org.curfewd.Daemon1 (supports both one-shot CLI and GLib loop clients)."""

    def __init__(
        self,
        bus=None,
        bus_name: str = DBUS_BUS_NAME,
        follow_name_owner: bool | None = None,
        admin_timeout_sec: int = 125,
    ) -> None:
        self._bus = bus
        self._bus_name = bus_name
        self._follow_name_owner = follow_name_owner
        self._admin_timeout_sec = max(25, int(admin_timeout_sec))
        self._proxy = None

    def _get_bus(self):
        if self._bus is None:
            self._bus = dbus.SystemBus()
        return self._bus

    def _get_proxy(self):
        if self._proxy is None:
            bus = self._get_bus()
            use_follow = (
                self._follow_name_owner
                if self._follow_name_owner is not None
                else (dbus.get_default_main_loop() is not None)
            )
            try:
                self._proxy = bus.get_object(
                    self._bus_name,
                    DBUS_OBJECT_PATH,
                    introspect=False,
                    follow_name_owner_changes=bool(use_follow),
                )
            except RuntimeError:
                # Synchronous bus without an attached main loop (e.g. one-shot CLI / worker thread)
                self._proxy = bus.get_object(
                    self._bus_name,
                    DBUS_OBJECT_PATH,
                    introspect=False,
                    follow_name_owner_changes=False,
                )
            except dbus.DBusException as exc:
                raise _translate_dbus_error(exc) from exc
        return self._proxy

    def _call(self, interface_name: str, method_name: str, *args) -> Any:
        is_admin = interface_name == DBUS_ADMIN_INTERFACE
        try:
            iface = dbus.Interface(self._get_proxy(), interface_name)
            fn = getattr(iface, method_name)
            if is_admin:
                return fn(*args, timeout=self._admin_timeout_sec)
            return fn(*args)
        except dbus.DBusException as exc:
            raise _translate_dbus_error(exc, interactive_admin=is_admin) from exc

    def _call_json(self, interface_name: str, method_name: str, *args) -> dict[str, Any]:
        raw = self._call(interface_name, method_name, *args)
        return json.loads(str(raw))

    # --- Admin API ---

    def list_users(self) -> list[dict[str, Any]]:
        return self._call_json(DBUS_ADMIN_INTERFACE, "ListUsers").get("users", [])

    def get_user_status(self, username: str) -> dict[str, Any]:
        return self._call_json(DBUS_ADMIN_INTERFACE, "GetUserStatus", str(username))

    def set_user_policy(self, username: str, policy: dict[str, Any]) -> dict[str, Any]:
        return self._call_json(
            DBUS_ADMIN_INTERFACE, "SetUserPolicy", str(username), json.dumps(policy)
        )

    def grant_time(
        self,
        username: str,
        delta_seconds: int,
        mode: str = "add",
        note: str = "",
    ) -> dict[str, Any]:
        if note:
            return self._call_json(
                DBUS_ADMIN_INTERFACE,
                "GrantTimeDetailed",
                str(username),
                json.dumps(
                    {"delta_seconds": int(delta_seconds), "mode": str(mode), "note": str(note)}
                ),
            )
        return self._call_json(
            DBUS_ADMIN_INTERFACE,
            "GrantTime",
            str(username),
            dbus.Int32(int(delta_seconds)),
            str(mode),
        )

    def pause_user(
        self,
        username: str,
        until_utc: str = "",
        reason: str = "",
        hide_tray: bool = True,
    ) -> dict[str, Any]:
        return self._call_json(
            DBUS_ADMIN_INTERFACE,
            "PauseUser",
            str(username),
            str(until_utc or ""),
            str(reason or ""),
            dbus.Boolean(bool(hide_tray)),
        )

    def resume_user(self, username: str) -> dict[str, Any]:
        return self._call_json(DBUS_ADMIN_INTERFACE, "ResumeUser", str(username))

    def set_homework_mode(self, username: str, payload: dict[str, Any]) -> dict[str, Any]:
        return self._call_json(
            DBUS_ADMIN_INTERFACE, "SetHomeworkMode", str(username), json.dumps(payload)
        )

    def lock_user(self, username: str) -> dict[str, Any]:
        return self._call_json(DBUS_ADMIN_INTERFACE, "LockUser", str(username))

    def test_lock(self, username: str) -> dict[str, Any]:
        return self._call_json(DBUS_ADMIN_INTERFACE, "TestLock", str(username))

    def get_usage_history(self, username: str, days: int = 7) -> dict[str, Any]:
        return self._call_json(
            DBUS_ADMIN_INTERFACE, "GetUsageHistory", str(username), dbus.Int32(int(days))
        )

    def get_daemon_config(self) -> dict[str, Any]:
        return self._call_json(DBUS_ADMIN_INTERFACE, "GetDaemonConfig")

    def set_daemon_config(self, config: dict[str, Any]) -> dict[str, Any]:
        return self._call_json(DBUS_ADMIN_INTERFACE, "SetDaemonConfig", json.dumps(config))

    def set_today_override(
        self,
        username: str,
        limit_sec: int | None = None,
        windows: list[dict[str, str]] | None = None,
        reset: bool = False,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {"reset": bool(reset)}
        if limit_sec is not None:
            payload["limit_sec"] = int(limit_sec)
        if windows is not None:
            payload["windows"] = list(windows)
        return self._call_json(
            DBUS_ADMIN_INTERFACE, "SetTodayOverride", str(username), json.dumps(payload)
        )

    def grant_app_time(self, username: str, pattern: str, delta_seconds: int) -> dict[str, Any]:
        return self._call_json(
            DBUS_ADMIN_INTERFACE,
            "GrantAppTime",
            str(username),
            str(pattern),
            dbus.Int32(int(delta_seconds)),
        )

    def grant_category_time(
        self, username: str, category: str, delta_seconds: int
    ) -> dict[str, Any]:
        return self._call_json(
            DBUS_ADMIN_INTERFACE,
            "GrantCategoryTime",
            str(username),
            str(category),
            dbus.Int32(int(delta_seconds)),
        )

    def list_time_requests(self, status_filter: str = "") -> list[dict[str, Any]]:
        return self._call_json(
            DBUS_ADMIN_INTERFACE, "ListTimeRequests", str(status_filter or "")
        ).get("requests", [])

    def resolve_time_request(
        self,
        request_id: int,
        approve: bool,
        grant_seconds: int = 0,
        note: str = "",
    ) -> dict[str, Any]:
        return self._call_json(
            DBUS_ADMIN_INTERFACE,
            "ResolveTimeRequest",
            dbus.Int32(int(request_id)),
            dbus.Boolean(bool(approve)),
            dbus.Int32(int(grant_seconds)),
            str(note or ""),
        )

    def quick_approve_request(
        self,
        request_id: int,
        grant_seconds: int = 0,
        note: str = "",
    ) -> dict[str, Any]:
        return self._call_json(
            DBUS_ADMIN_INTERFACE,
            "QuickApproveRequest",
            dbus.Int32(int(request_id)),
            dbus.Int32(int(grant_seconds)),
            str(note or ""),
        )

    def get_parent_code_status(self) -> dict[str, Any]:
        return self._call_json(DBUS_ADMIN_INTERFACE, "GetParentCodeStatus")

    def setup_parent_code(self, enable: bool = True, rotate: bool = False) -> dict[str, Any]:
        return self._call_json(
            DBUS_ADMIN_INTERFACE,
            "SetupParentCode",
            dbus.Boolean(bool(enable)),
            dbus.Boolean(bool(rotate)),
        )

    def list_installed_apps(self, username: str = "") -> list[dict[str, Any]]:
        return self._call_json(DBUS_ADMIN_INTERFACE, "ListInstalledApps", str(username or "")).get(
            "apps", []
        )

    def run_doctor(self) -> dict[str, Any]:
        return self._call_json(DBUS_ADMIN_INTERFACE, "RunDoctor")

    def apply_doctor_fix(self) -> dict[str, Any]:
        return self._call_json(DBUS_ADMIN_INTERFACE, "ApplyDoctorFix")

    # --- Unprivileged Self-Client API ---

    def get_my_status(self) -> dict[str, Any]:
        return self._call_json(DBUS_CLIENT_INTERFACE, "GetMyStatus")

    def get_my_history(self, days: int = 7) -> dict[str, Any]:
        return self._call_json(DBUS_CLIENT_INTERFACE, "GetMyHistory", dbus.Int32(int(days)))

    def report_screensaver_state(self, nonce: str, active: bool) -> bool:
        res = self._call(
            DBUS_CLIENT_INTERFACE, "ReportScreensaverState", str(nonce), dbus.Boolean(bool(active))
        )
        return bool(res)

    def report_client_telemetry(self, nonce: str, telemetry: dict[str, Any]) -> bool:
        res = self._call(
            DBUS_CLIENT_INTERFACE, "ReportClientTelemetry", str(nonce), json.dumps(telemetry)
        )
        return bool(res)

    def notify_session_unlocked(self) -> bool:
        res = self._call(DBUS_CLIENT_INTERFACE, "NotifySessionUnlocked")
        return bool(res)

    def request_more_time(
        self,
        requested_sec: int = 900,
        kind: str = "daily",
        target: str = "",
        target_label: str = "",
        reason: str = "",
    ) -> dict[str, Any]:
        return self._call_json(
            DBUS_CLIENT_INTERFACE,
            "RequestMoreTime",
            json.dumps(
                {
                    "kind": str(kind),
                    "target": str(target),
                    "target_label": str(target_label),
                    "requested_sec": int(requested_sec),
                    "reason": str(reason),
                }
            ),
        )

    def list_my_time_requests(self) -> list[dict[str, Any]]:
        return self._call_json(DBUS_CLIENT_INTERFACE, "ListMyTimeRequests").get("requests", [])

    def claim_save_grace(self) -> dict[str, Any]:
        return self._call_json(DBUS_CLIENT_INTERFACE, "ClaimSaveGrace")

    def approve_with_parent_code(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self._call_json(DBUS_CLIENT_INTERFACE, "ApproveWithParentCode", json.dumps(payload))

    # --- Signals & Auto-Reconnect ---

    def subscribe_signal(self, signal_name: str, handler: Callable[..., None]):
        """Subscribe to an org.curfewd.Daemon1.Events signal."""
        return self._get_bus().add_signal_receiver(
            handler_function=handler,
            signal_name=signal_name,
            dbus_interface=DBUS_EVENTS_INTERFACE,
            path=DBUS_OBJECT_PATH,
        )

    def watch_daemon_owner(self, callback: Callable[[str], None]):
        """Invoke callback(new_owner) whenever org.curfewd.Daemon1 starts, stops, or restarts."""
        return self._get_bus().watch_name_owner(self._bus_name, callback)
