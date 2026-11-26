"""Polkit authorization and D-Bus sender Unix UID resolution for org.curfewd.Daemon1."""

import grp
import logging
import pwd
from typing import Callable

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
    ) -> None:
        self._bus = bus
        self._admin_group = admin_group
        self._polkit_checker = polkit_checker
        self._allow_same_uid_testing = allow_same_uid_testing

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

    def _check_polkit(self, sender: str, action_id: str) -> bool:
        import dbus

        proxy = self._bus.get_object(_POLKIT_BUS, _POLKIT_PATH)
        authority = dbus.Interface(proxy, _POLKIT_IF)
        subject = ("system-bus-name", {"name": dbus.String(sender)})
        details = dbus.Dictionary({}, signature="ss")
        # flags=1 (AllowUserInteraction) enables desktop auth prompts for unprivileged UIs
        is_authorized, _is_challenge, _details = authority.CheckAuthorization(
            subject,
            action_id,
            details,
            dbus.UInt32(1),
            "",
        )
        return bool(is_authorized)

    def authorize(self, sender: str, action_id: str) -> tuple[int, str]:
        """Verify caller is authorized for action_id; return (uid, username) or raise."""
        uid, username = self.resolve_sender_user(sender)

        # Injected Polkit checker (used in tests) takes priority so deny paths can be tested cleanly.
        if self._polkit_checker is not None:
            if self._polkit_checker(sender, action_id, uid):
                return uid, username
            raise AuthorizationError(f"Not authorized for {action_id} (uid={uid})")

        # Fast path: root or member of curfewd admin group
        if uid == 0 or self._is_in_admin_group(uid, username):
            return uid, username

        if self._allow_same_uid_testing:
            import os

            if uid == os.geteuid():
                return uid, username

        # Unprivileged desktop caller: delegate to Polkit Authority
        try:
            if self._check_polkit(sender, action_id):
                return uid, username
        except Exception as exc:
            logger.warning("Polkit CheckAuthorization(%s) failed for uid=%d: %s", action_id, uid, exc)

        raise AuthorizationError(
            f"Permission denied for {action_id}. Authenticate via Polkit, run with sudo, "
            f"or join the '{self._admin_group}' group."
        )
