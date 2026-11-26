"""Typed Python client SDK for org.curfewd.Daemon1 (shared by curfewctl, tray, and Spec 003 UI)."""

import json
from typing import Any, Callable

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


def _translate_dbus_error(exc: dbus.DBusException) -> CurfewError:
    name = exc.get_dbus_name() or ""
    msg = exc.get_dbus_message() or str(exc)
    if "PermissionDenied" in name or "AccessDenied" in name:
        return PermissionDeniedError(msg)
    if "UserNotFound" in name:
        return UserNotFoundError(msg)
    if "ValidationError" in name:
        return ValidationError(msg)
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
    ) -> None:
        self._bus = bus
        self._bus_name = bus_name
        self._follow_name_owner = follow_name_owner
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
        try:
            iface = dbus.Interface(self._get_proxy(), interface_name)
            return getattr(iface, method_name)(*args)
        except dbus.DBusException as exc:
            raise _translate_dbus_error(exc) from exc

    def _call_json(self, interface_name: str, method_name: str, *args) -> dict[str, Any]:
        raw = self._call(interface_name, method_name, *args)
        return json.loads(str(raw))

    # --- Admin API ---

    def list_users(self) -> list[dict[str, Any]]:
        return self._call_json(DBUS_ADMIN_INTERFACE, "ListUsers").get("users", [])

    def get_user_status(self, username: str) -> dict[str, Any]:
        return self._call_json(DBUS_ADMIN_INTERFACE, "GetUserStatus", str(username))

    def set_user_policy(self, username: str, policy: dict[str, Any]) -> dict[str, Any]:
        return self._call_json(DBUS_ADMIN_INTERFACE, "SetUserPolicy", str(username), json.dumps(policy))

    def grant_time(self, username: str, delta_seconds: int, mode: str = "add") -> dict[str, Any]:
        return self._call_json(
            DBUS_ADMIN_INTERFACE, "GrantTime", str(username), dbus.Int32(int(delta_seconds)), str(mode)
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

    def lock_user(self, username: str) -> dict[str, Any]:
        return self._call_json(DBUS_ADMIN_INTERFACE, "LockUser", str(username))

    def get_usage_history(self, username: str, days: int = 7) -> dict[str, Any]:
        return self._call_json(DBUS_ADMIN_INTERFACE, "GetUsageHistory", str(username), dbus.Int32(int(days)))

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
        res = self._call(DBUS_CLIENT_INTERFACE, "ReportScreensaverState", str(nonce), dbus.Boolean(bool(active)))
        return bool(res)

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
