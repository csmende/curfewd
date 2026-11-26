"""Polkit authorization and D-Bus sender Unix UID resolution for org.curfewd.Daemon1."""

import grp
import logging
import pwd
from collections.abc import Callable

from curfewd import DEFAULT_ADMIN_GROUP

logger = logging.getLogger("curfewd.polkit")

_POLKIT_BUS = "org.freedesktop.PolicyKit1"
_POLKIT_PATH = "/org/freedesktop/PolicyKit1/Authority"
_POLKIT_IF = "org.freedesktop.PolicyKit1.Authority"
_DBUS_BUS = "org.freedesktop.DBus"
_DBUS_PATH = "/org/freedesktop/DBus"
_DBUS_IF = "org.freedesktop.DBus"


class AuthorizationError(PermissionError):
    """Raised when a D-Bus caller is not authorized for an action."""


class PolkitAuthorizer:
    """Authorizes D-Bus callers via root/group fast-path or Polkit CheckAuthorization."""

    def __init__(
        self,
        bus,
        admin_group: str = DEFAULT_ADMIN_GROUP,
        polkit_checker: Callable[[str, str, int], bool] | None = None,
        allow_same_uid_testing: bool = False,
        polkit_timeout_sec: int = 120,
    ) -> None:
        self._bus = bus
        self._admin_group = admin_group
        self._polkit_checker = polkit_checker
        self._allow_same_uid_testing = allow_same_uid_testing
        self._polkit_timeout_sec = max(15, int(polkit_timeout_sec))
        self._inflight_cancel_ids: dict[int, str] = {}
        self._cancel_seq = 0

    def resolve_sender_uid(self, sender: str) -> int:
        """Resolve a D-Bus unique sender name (e.g. ':1.42') to its real Unix UID."""
        if not sender:
            raise AuthorizationError("Missing D-Bus sender identity")
        import dbus

        proxy = self._bus.get_object(_DBUS_BUS, _DBUS_PATH)
        dbus_iface = dbus.Interface(proxy, _DBUS_IF)
        return int(dbus_iface.GetConnectionUnixUser(sender))

    def resolve_sender_user(self, sender: str) -> tuple[int, str]:
        """Resolve a D-Bus sender to (uid, username)."""
        uid = self.resolve_sender_uid(sender)
        try:
            username = pwd.getpwuid(uid).pw_name
        except KeyError as exc:
            raise AuthorizationError(f"Unknown Unix UID {uid}") from exc
        return uid, username

    def _is_in_admin_group(self, uid: int, username: str) -> bool:
        if not self._admin_group:
            return False
        try:
            group = grp.getgrnam(self._admin_group)
        except KeyError:
            return False
        if username in group.gr_mem:
            return True
        try:
            return pwd.getpwuid(uid).pw_gid == group.gr_gid
        except KeyError:
            return False

    def _fast_path_decision(
        self, sender: str, action_id: str, uid: int, username: str
    ) -> bool | None:
        """Return True/False if decided synchronously without Polkit IPC, else None."""
        if self._polkit_checker is not None:
            return bool(self._polkit_checker(sender, action_id, uid))
        if uid == 0 or self._is_in_admin_group(uid, username):
            return True
        if self._allow_same_uid_testing:
            import os

            if uid == os.geteuid():
                return True
        return None

    def _denied_error(self, action_id: str, uid: int) -> AuthorizationError:
        if self._polkit_checker is not None:
            return AuthorizationError(f"Not authorized for {action_id} (uid={uid})")
        return AuthorizationError(
            f"Permission denied for {action_id}. Authenticate via Polkit, run with sudo, "
            f"or join the '{self._admin_group}' group."
        )

    def _check_polkit(self, sender: str, action_id: str) -> bool:
        import dbus

        proxy = self._bus.get_object(_POLKIT_BUS, _POLKIT_PATH)
        authority = dbus.Interface(proxy, _POLKIT_IF)
        subject = ("system-bus-name", {"name": dbus.String(sender)})
        details = dbus.Dictionary({}, signature="ss")
        is_authorized, _is_challenge, _details = authority.CheckAuthorization(
            subject,
            action_id,
            details,
            dbus.UInt32(1),
            "",
            timeout=self._polkit_timeout_sec,
        )
        return bool(is_authorized)

    def authorize(self, sender: str, action_id: str) -> tuple[int, str]:
        """Verify caller is authorized for action_id; return (uid, username) or raise."""
        uid, username = self.resolve_sender_user(sender)
        fast = self._fast_path_decision(sender, action_id, uid, username)
        if fast is True:
            return uid, username
        if fast is False:
            raise self._denied_error(action_id, uid)

        try:
            if self._check_polkit(sender, action_id):
                return uid, username
        except Exception as exc:
            logger.warning(
                "Polkit CheckAuthorization(%s) failed for uid=%d: %s", action_id, uid, exc
            )

        raise self._denied_error(action_id, uid)

    def authorize_async(
        self,
        sender: str,
        action_id: str,
        on_ok: Callable[[int, str], None],
        on_err: Callable[[AuthorizationError], None],
    ) -> None:
        """Non-blocking Polkit authorization on the GLib main loop with per-UID prompt cap."""
        try:
            uid, username = self.resolve_sender_user(sender)
        except AuthorizationError as exc:
            on_err(exc)
            return

        fast = self._fast_path_decision(sender, action_id, uid, username)
        if fast is True:
            on_ok(uid, username)
            return
        if fast is False:
            on_err(self._denied_error(action_id, uid))
            return

        import dbus

        try:
            proxy = self._bus.get_object(_POLKIT_BUS, _POLKIT_PATH)
            authority = dbus.Interface(proxy, _POLKIT_IF)
            prev_cid = self._inflight_cancel_ids.pop(uid, None)
            if prev_cid:
                try:
                    authority.CancelCheckAuthorization(
                        prev_cid,
                        reply_handler=lambda *_a: None,
                        error_handler=lambda *_a: None,
                    )
                except Exception:
                    logger.debug("Ignoring CancelCheckAuthorization error for %s", prev_cid)

            self._cancel_seq += 1
            cancel_id = f"curfewd-{uid}-{self._cancel_seq}"
            self._inflight_cancel_ids[uid] = cancel_id
            subject = ("system-bus-name", {"name": dbus.String(sender)})
            details = dbus.Dictionary({}, signature="ss")

            def _on_reply(result) -> None:
                if self._inflight_cancel_ids.get(uid) == cancel_id:
                    self._inflight_cancel_ids.pop(uid, None)
                is_auth = bool(result[0]) if result else False
                if is_auth:
                    on_ok(uid, username)
                else:
                    on_err(self._denied_error(action_id, uid))

            def _on_dbus_err(exc) -> None:
                if self._inflight_cancel_ids.get(uid) == cancel_id:
                    self._inflight_cancel_ids.pop(uid, None)
                logger.warning(
                    "Async Polkit CheckAuthorization(%s) failed for uid=%d: %s",
                    action_id,
                    uid,
                    exc,
                )
                on_err(self._denied_error(action_id, uid))

            authority.CheckAuthorization(
                subject,
                action_id,
                details,
                dbus.UInt32(1),
                cancel_id,
                timeout=self._polkit_timeout_sec,
                reply_handler=_on_reply,
                error_handler=_on_dbus_err,
            )
        except Exception as exc:
            logger.warning(
                "Async Polkit CheckAuthorization(%s) dispatch failed for uid=%d: %s",
                action_id,
                uid,
                exc,
            )
            on_err(self._denied_error(action_id, uid))
