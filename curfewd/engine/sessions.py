"""systemd-logind session monitor, desktop user discovery, and lockout actions."""

import logging
import os
import pwd
import re
from typing import Any

from curfewd.engine.screenlock import ScreenLockEnforcer, SessionContext, build_session_context
from curfewd.models import DaemonConfig, LockResult, SessionSummary
from curfewd.parsing import validate_username

logger = logging.getLogger("curfewd.sessions")

_LOGIN_DEFS_PATHS = ("/etc/login.defs", "/usr/etc/login.defs")
_LOGIN1_BUS = "org.freedesktop.login1"
_LOGIN1_PATH = "/org/freedesktop/login1"
_LOGIN1_MGR_IF = "org.freedesktop.login1.Manager"
_LOGIN1_SESS_IF = "org.freedesktop.login1.Session"
_DBUS_PROPS_IF = "org.freedesktop.DBus.Properties"


def read_uid_bounds() -> tuple[int, int]:
    """Read UID_MIN and UID_MAX from /etc/login.defs (defaults 1000..60000)."""
    uid_min, uid_max = 1000, 60000
    for path in _LOGIN_DEFS_PATHS:
        if not os.path.isfile(path):
            continue
        try:
            with open(path, encoding="utf-8", errors="ignore") as fh:
                for line in fh:
                    stripped = line.strip()
                    m_min = re.match(r"^UID_MIN\s+(\d+)$", stripped)
                    if m_min:
                        uid_min = int(m_min.group(1))
                    m_max = re.match(r"^UID_MAX\s+(\d+)$", stripped)
                    if m_max:
                        uid_max = int(m_max.group(1))
            break
        except OSError:
            continue
    return uid_min, uid_max


def discover_desktop_users(excluded_users: list[str] | None = None) -> list[dict[str, Any]]:
    """Return normal interactive desktop users from /etc/passwd."""
    uid_min, uid_max = read_uid_bounds()
    excluded = set(excluded_users or [])
    users: list[dict[str, Any]] = []
    for entry in pwd.getpwall():
        if entry.pw_name in excluded:
            continue
        if not (uid_min <= entry.pw_uid <= uid_max):
            continue
        shell = entry.pw_shell or ""
        if "/nologin" in shell or "/false" in shell or not shell:
            continue
        try:
            validate_username(entry.pw_name)
        except ValueError:
            continue
        gecos = (entry.pw_gecos or "").split(",")[0].strip()
        users.append(
            {
                "username": entry.pw_name,
                "uid": int(entry.pw_uid),
                "gid": int(entry.pw_gid),
                "full_name": gecos or entry.pw_name,
                "home_dir": entry.pw_dir,
            }
        )
    users.sort(key=lambda u: u["username"])
    return users


def lookup_user_record(username: str) -> dict[str, Any] | None:
    """Look up a single user's passwd metadata, or None if not found."""
    try:
        entry = pwd.getpwnam(username)
    except KeyError:
        return None
    gecos = (entry.pw_gecos or "").split(",")[0].strip()
    return {
        "username": entry.pw_name,
        "uid": int(entry.pw_uid),
        "gid": int(entry.pw_gid),
        "full_name": gecos or entry.pw_name,
        "home_dir": entry.pw_dir,
    }


class LogindSessionMonitor:
    """Interfaces with systemd-logind over SystemBus for sessions and lockout enforcement."""

    def __init__(
        self,
        system_bus=None,
        screenlock: ScreenLockEnforcer | None = None,
    ) -> None:
        self._bus = system_bus
        self._screenlock = screenlock or ScreenLockEnforcer()
        self._manager = None

    def _get_manager(self):
        if self._manager is not None:
            return self._manager
        if self._bus is None:
            return None
        try:
            import dbus

            proxy = self._bus.get_object(_LOGIN1_BUS, _LOGIN1_PATH, follow_name_owner_changes=True)
            self._manager = dbus.Interface(proxy, _LOGIN1_MGR_IF)
            return self._manager
        except Exception as exc:
            logger.debug("systemd-logind unavailable: %s", exc)
            return None

    def list_user_sessions(
        self,
        tracked_types: list[str] | None = None,
    ) -> dict[str, list[dict[str, Any]]]:
        """Return {username: [session_meta_dict, ...]} from logind."""
        mgr = self._get_manager()
        if mgr is None:
            return {}

        allowed_types = {t.lower() for t in (tracked_types or ["x11", "wayland", "mir"])}
        result: dict[str, list[dict[str, Any]]] = {}

        try:
            import dbus

            raw_sessions = mgr.ListSessions()
        except Exception as exc:
            logger.debug("logind ListSessions failed: %s", exc)
            return {}

        for sess_id, uid, uname, _seat, obj_path in raw_sessions:
            sid = str(sess_id)
            username = str(uname)
            try:
                s_obj = self._bus.get_object(_LOGIN1_BUS, str(obj_path))
                props = dbus.Interface(s_obj, _DBUS_PROPS_IF)
                all_props = props.GetAll(_LOGIN1_SESS_IF)
            except Exception as exc:
                logger.debug("could not inspect logind session %s: %s", sid, exc)
                continue

            s_type = str(all_props.get("Type", "")).lower()
            s_class = str(all_props.get("Class", "")).lower()
            if s_class and s_class != "user":
                continue
            if allowed_types and s_type not in allowed_types:
                continue

            state_str = str(all_props.get("State", "")).lower()
            active = bool(all_props.get("Active", False)) or state_str == "active"
            locked_hint = bool(all_props.get("LockedHint", False))
            idle_hint = bool(all_props.get("IdleHint", False))
            display = str(all_props.get("Display", ""))
            desktop = str(all_props.get("Desktop", ""))
            leader_pid = int(all_props.get("Leader", 0))

            meta = {
                "session_id": sid,
                "uid": int(uid),
                "username": username,
                "session_type": s_type,
                "active": active,
                "locked": locked_hint,
                "idle": idle_hint,
                "display": display,
                "desktop": desktop,
                "leader_pid": leader_pid,
                "object_path": str(obj_path),
            }
            result.setdefault(username, []).append(meta)

        return result

    def summarize_sessions(self, sessions: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return [
            SessionSummary(
                session_id=s["session_id"],
                session_type=s.get("session_type", "x11"),
                desktop=s.get("desktop", ""),
                active=bool(s.get("active", False)),
                locked=bool(s.get("locked", False)),
                display=s.get("display", ""),
                leader_pid=int(s.get("leader_pid", 0)),
            ).to_dict()
            for s in sessions
        ]

    def build_contexts(self, sessions: list[dict[str, Any]]) -> list[SessionContext]:
        return [
            build_session_context(
                session_id=s["session_id"],
                uid=int(s["uid"]),
                username=s["username"],
                session_type=s.get("session_type", "x11"),
                display=s.get("display", ""),
                desktop=s.get("desktop", ""),
                leader_pid=int(s.get("leader_pid", 0)),
            )
            for s in sessions
        ]

    def prepare_session_locks(self, sessions: list[dict[str, Any]]) -> list[SessionContext]:
        """GLib-thread phase: invoke Layer 1 logind LockSession and snapshot SessionContexts."""
        mgr = self._get_manager()
        contexts = self.build_contexts(sessions)
        for ctx in contexts:
            if mgr is not None:
                try:
                    mgr.LockSession(ctx.session_id)
                except Exception as exc:
                    logger.debug("logind LockSession(%s) failed: %s", ctx.session_id, exc)
        return contexts

    def execute_fallback_locks(
        self,
        contexts: list[SessionContext],
        config: DaemonConfig,
    ) -> list[LockResult]:
        """Worker-safe phase: run Layers 2-3 privilege-dropped lock verification (no SystemBus)."""
        results: list[LockResult] = []
        for ctx in contexts:
            if config.session_lock_fallback:
                res = self._screenlock.enforce_session_lock_detailed(
                    ctx,
                    release_x11_grab=config.release_x11_grab_on_lock,
                )
                results.append(res)
            else:
                results.append(
                    LockResult(
                        session_id=ctx.session_id,
                        username=ctx.username,
                        locked=True,
                        verified=False,
                        backend="logind",
                        stage="logind",
                    )
                )
        return results

    def lock_user_sessions(
        self,
        sessions: list[dict[str, Any]],
        config: DaemonConfig,
        async_mode: bool = True,
    ) -> list[LockResult]:
        """Invoke Layer 1 logind LockSession + Layers 2-3 verified multi-DE lock."""
        contexts = self.prepare_session_locks(sessions)
        if async_mode:
            if config.session_lock_fallback:
                for ctx in contexts:
                    self._screenlock.enforce_session_lock_async(
                        ctx,
                        release_x11_grab=config.release_x11_grab_on_lock,
                    )
            return []
        return self.execute_fallback_locks(contexts, config)

    def enforce_lockout_action(
        self,
        action: str,
        username: str,
        uid: int | None,
        sessions: list[dict[str, Any]],
        config: DaemonConfig,
    ) -> None:
        """Execute the configured lockout action ('lock', 'suspend', 'terminate', 'kill', 'shutdown')."""
        mgr = self._get_manager()
        if action == "lock":
            self.lock_user_sessions(sessions, config, async_mode=True)
            return
        if action == "suspend":
            self.lock_user_sessions(sessions, config, async_mode=True)
            if mgr is not None:
                try:
                    mgr.Suspend(False)
                except Exception as exc:
                    logger.warning("logind Suspend failed: %s", exc)
            return
        if action == "terminate":
            if mgr is not None:
                for s in sessions:
                    try:
                        mgr.TerminateSession(s["session_id"])
                    except Exception as exc:
                        logger.warning("TerminateSession(%s) failed: %s", s["session_id"], exc)
            return
        if action == "kill":
            if mgr is not None and uid is not None:
                try:
                    mgr.KillUser(int(uid), 9)
                except Exception as exc:
                    logger.warning("KillUser(%s) failed: %s", username, exc)
            return
        if action == "shutdown" and mgr is not None:
            try:
                mgr.PowerOff(False)
            except Exception as exc:
                logger.warning("PowerOff failed: %s", exc)
