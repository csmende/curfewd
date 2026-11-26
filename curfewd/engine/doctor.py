"""Host diagnostics (`curfewctl doctor`) and built-in dconf screensaver lock remediation."""

import os
import shutil

# Subprocess is strictly limited to fixed trusted-PATH diagnostic binaries (shell=False).
import subprocess  # nosec B404
from typing import Any

from curfewd import (
    DBUS_BUS_NAME,
    DEFAULT_CONFIG_DIR,
    DEFAULT_STATE_DIR,
    __version__,
)
from curfewd.engine.screenlock import TRUSTED_BIN_PATH, select_backends

POLKIT_POLICY_PATH = "/usr/share/polkit-1/actions/org.curfewd.policy"
DBUS_POLICY_PATH = "/usr/share/dbus-1/system.d/org.curfewd.Daemon1.conf"
DCONF_PROFILE_PATH = "/etc/dconf/profile/user"
DCONF_DB_LOCKSCREEN = "/etc/dconf/db/local.d/00-curfewd-lockscreen"
DCONF_LOCKS_FILE = "/etc/dconf/db/local.d/locks/curfewd-lockscreen"

_DCONF_PROFILE_CONTENT = "user-db:user\nsystem-db:local\n"
_DCONF_DB_CONTENT = """# Managed by curfewd doctor --fix
[org/cinnamon/desktop/screensaver]
lock-enabled=true

[org/cinnamon/desktop/lockdown]
disable-lock-screen=false

[org/gnome/desktop/screensaver]
lock-enabled=true

[org/gnome/desktop/lockdown]
disable-lock-screen=false

[org/mate/screensaver]
lock-enabled=true

[org/mate/desktop/lockdown]
disable-lock-screen=false
"""
_DCONF_LOCKS_CONTENT = """/org/cinnamon/desktop/screensaver/lock-enabled
/org/cinnamon/desktop/lockdown/disable-lock-screen
/org/gnome/desktop/screensaver/lock-enabled
/org/gnome/desktop/lockdown/disable-lock-screen
/org/mate/screensaver/lock-enabled
/org/mate/desktop/lockdown/disable-lock-screen
"""


def _run_cmd(argv: list[str], timeout: float = 3.0) -> tuple[int, str, str]:
    bin_path = shutil.which(argv[0], path=TRUSTED_BIN_PATH)
    if not bin_path:
        return 127, "", f"{argv[0]} not found"
    try:
        res = subprocess.run(  # nosec B603
            [bin_path, *argv[1:]],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
        return res.returncode, res.stdout.strip(), res.stderr.strip()
    except (OSError, subprocess.TimeoutExpired) as exc:
        return 1, "", str(exc)


def _check(name: str, status: str, detail: str, fixable: bool = False) -> dict[str, Any]:
    return {"name": name, "status": status, "detail": detail, "fixable": fixable}


def check_dconf_lock_policy(root_prefix: str = "") -> dict[str, Any]:
    """Verify root-owned dconf screensaver lock files exist."""
    profile = root_prefix + DCONF_PROFILE_PATH
    db_file = root_prefix + DCONF_DB_LOCKSCREEN
    lock_file = root_prefix + DCONF_LOCKS_FILE
    missing = [p for p in (profile, db_file, lock_file) if not os.path.isfile(p)]
    if not missing:
        return _check(
            "dconf_lock_policy",
            "ok",
            "Root dconf lock policy installed (lock-enabled=true, disable-lock-screen=false)",
        )
    return _check(
        "dconf_lock_policy",
        "warn",
        "Missing root dconf lock files: "
        + ", ".join(missing)
        + " (run `sudo curfewctl doctor --fix`)",
        fixable=True,
    )


def apply_dconf_lock_fix(root_prefix: str = "") -> dict[str, Any]:
    """Install root dconf lock-screen policy and run `dconf update`."""
    if not root_prefix and os.geteuid() != 0:
        raise PermissionError("`curfewctl doctor --fix` requires root privileges")

    profile = root_prefix + DCONF_PROFILE_PATH
    db_file = root_prefix + DCONF_DB_LOCKSCREEN
    lock_file = root_prefix + DCONF_LOCKS_FILE

    for path in (profile, db_file, lock_file):
        os.makedirs(os.path.dirname(path), mode=0o755, exist_ok=True)

    if not os.path.isfile(profile):
        with open(profile, "w", encoding="utf-8") as fh:
            fh.write(_DCONF_PROFILE_CONTENT)
        os.chmod(profile, 0o644)
    else:
        with open(profile, encoding="utf-8") as fh:
            content = fh.read()
        if "system-db:local" not in content:
            with open(profile, "a", encoding="utf-8") as fh:
                fh.write("\nsystem-db:local\n")

    with open(db_file, "w", encoding="utf-8") as fh:
        fh.write(_DCONF_DB_CONTENT)
    os.chmod(db_file, 0o644)

    with open(lock_file, "w", encoding="utf-8") as fh:
        fh.write(_DCONF_LOCKS_CONTENT)
    os.chmod(lock_file, 0o644)

    dconf_updated = False
    dconf_note = "skipped (root_prefix set)"
    if not root_prefix:
        rc, _, err = _run_cmd(["dconf", "update"], timeout=10.0)
        dconf_updated = rc == 0
        dconf_note = "dconf update succeeded" if dconf_updated else f"dconf update failed: {err}"

    return {
        "applied": True,
        "files": [profile, db_file, lock_file],
        "dconf_updated": dconf_updated,
        "detail": dconf_note,
    }


def run_doctor(
    config_dir: str = DEFAULT_CONFIG_DIR,
    state_dir: str = DEFAULT_STATE_DIR,
    sessions_by_user: dict[str, list[dict[str, Any]]] | None = None,
    dbus_connected: bool | None = None,
) -> dict[str, Any]:
    """Run comprehensive host health diagnostics and return a structured report."""
    checks: list[dict[str, Any]] = []

    # 1. Systemd unit state
    rc, out, _ = _run_cmd(["systemctl", "is-active", "curfewd.service"])
    if rc == 0 and out == "active":
        checks.append(_check("systemd_service", "ok", "curfewd.service is active"))
    else:
        checks.append(
            _check(
                "systemd_service", "warn", f"curfewd.service state: {out or 'inactive/unavailable'}"
            )
        )

    # 2. D-Bus availability
    if dbus_connected is True:
        checks.append(_check("dbus_service", "ok", f"{DBUS_BUS_NAME} is reachable on system bus"))
    elif dbus_connected is False:
        checks.append(
            _check("dbus_service", "error", f"{DBUS_BUS_NAME} is not reachable on system bus")
        )
    else:
        checks.append(_check("dbus_service", "ok", f"Running inside {DBUS_BUS_NAME} daemon"))

    # 3. D-Bus & Polkit policy files
    if os.path.isfile(DBUS_POLICY_PATH):
        checks.append(_check("dbus_policy", "ok", f"{DBUS_POLICY_PATH} present"))
    else:
        checks.append(_check("dbus_policy", "warn", f"Missing {DBUS_POLICY_PATH}"))

    if os.path.isfile(POLKIT_POLICY_PATH):
        checks.append(_check("polkit_policy", "ok", f"{POLKIT_POLICY_PATH} present"))
    else:
        checks.append(_check("polkit_policy", "warn", f"Missing {POLKIT_POLICY_PATH}"))

    # 4. Storage directories
    for label, path in (("config_dir", config_dir), ("state_dir", state_dir)):
        if os.path.isdir(path):
            checks.append(_check(label, "ok", f"{path} exists"))
        else:
            checks.append(_check(label, "warn", f"{path} not created yet"))

    # 5. Root dconf screensaver lock policy
    checks.append(check_dconf_lock_policy())

    # 6. Helper binaries
    for tool, required in (
        ("gdbus", True),
        ("xdotool", False),
        ("wmctrl", False),
        ("dconf", False),
    ):
        found = shutil.which(tool, path=TRUSTED_BIN_PATH)
        if found:
            checks.append(_check(f"bin_{tool}", "ok", found))
        else:
            lvl = "error" if required else "warn"
            checks.append(_check(f"bin_{tool}", lvl, f"{tool} not installed in trusted PATH"))

    # 7. Active graphical sessions summary
    active_sessions = []
    for uname, sess_list in (sessions_by_user or {}).items():
        for s in sess_list:
            backends = [b.name for b in select_backends(s.get("desktop", ""))]
            active_sessions.append(
                {
                    "username": uname,
                    "session_id": s.get("session_id"),
                    "session_type": s.get("session_type"),
                    "desktop": s.get("desktop") or "unknown",
                    "locked": bool(s.get("locked", False)),
                    "lock_backends": backends,
                }
            )

    has_error = any(c["status"] == "error" for c in checks)
    has_warn = any(c["status"] == "warn" for c in checks)
    overall = "error" if has_error else ("warn" if has_warn else "ok")

    return {
        "version": __version__,
        "overall": overall,
        "checks": checks,
        "active_sessions": active_sessions,
    }
