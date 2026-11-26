"""Host diagnostics (`curfewctl doctor`), Wayland awareness, and multi-DE lock-screen hardening (Spec 006)."""

import configparser
import os
import secrets
import shutil

# Subprocess is strictly limited to fixed trusted-PATH diagnostic binaries (shell=False).
import subprocess  # nosec B404
import xml.etree.ElementTree as ET  # nosec B405 - parses only local root-owned /etc/xdg XML
from contextlib import suppress
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
KDE_LOCKSCREEN_PATH = "/etc/xdg/kscreenlockerrc"
XFCE_SCREENSAVER_PATH = "/etc/xdg/xfce4/xfconf/xfce-perchannel-xml/xfce4-screensaver.xml"
TRAY_USER_UNIT_PATH = "/usr/lib/systemd/user/curfewd-tray.service"
TRAY_AUTOSTART_PATH = "/etc/xdg/autostart/curfewd-tray.desktop"

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
_KDE_LOCKSCREEN_CONTENT = """# Managed by curfewd doctor --fix (KDE Kiosk immutability)
[Daemon][$i]
Autolock=true
LockOnResume=true
"""
_XFCE_SCREENSAVER_CONTENT = """<?xml version="1.0" encoding="UTF-8"?>
<!-- Managed by curfewd doctor --fix -->
<channel name="xfce4-screensaver" version="1.0" locked="*" unlocked="root">
  <property name="lock" type="empty">
    <property name="enabled" type="bool" value="true" locked="*"/>
  </property>
</channel>
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


def _detect_installed_des(
    sessions_by_user: dict[str, list[dict[str, Any]]] | None = None,
) -> set[str]:
    """Detect whether KDE Plasma or XFCE are installed or running."""
    des: set[str] = set()
    for sess_list in (sessions_by_user or {}).values():
        for s in sess_list:
            d = str(s.get("desktop") or "").lower()
            if "kde" in d or "plasma" in d:
                des.add("kde")
            if "xfce" in d:
                des.add("xfce")
            if any(k in d for k in ("cinnamon", "gnome", "mate", "ubuntu")):
                des.add("dconf")
    if shutil.which("plasmashell", path=TRUSTED_BIN_PATH) or shutil.which(
        "kscreenlocker_greet", path=TRUSTED_BIN_PATH
    ):
        des.add("kde")
    if shutil.which("xfce4-session", path=TRUSTED_BIN_PATH) or shutil.which(
        "xfce4-screensaver", path=TRUSTED_BIN_PATH
    ):
        des.add("xfce")
    return des


def _detect_session_mode(sessions_by_user: dict[str, list[dict[str, Any]]] | None) -> str:
    types: set[str] = set()
    for sess_list in (sessions_by_user or {}).values():
        for s in sess_list:
            st = str(s.get("session_type") or "").lower()
            if st in ("x11", "wayland"):
                types.add(st)
    if not types:
        return "none"
    if types == {"wayland"}:
        return "wayland"
    if types == {"x11"}:
        return "x11"
    return "mixed"


def _read_text_nofollow(path: str) -> str | None:
    if os.path.islink(path):
        return None
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(path, flags)
    except OSError:
        return None
    try:
        with os.fdopen(fd, "r", encoding="utf-8", errors="ignore") as fh:
            return fh.read(131072)
    except OSError:
        return None


def _write_text_atomic_nofollow(path: str, content: str) -> None:
    """Atomically write a system-wide kiosk policy file without following symlinks.

    Creates a unique sibling temp file exclusively (``O_CREAT | O_EXCL`` via
    mode ``"x"``) so it inherits the root/service ``UMask=0022`` (``0o644``)
    without widening a private ``mkstemp`` file via ``chmod``.
    """
    parent = os.path.dirname(path)
    os.makedirs(parent, mode=0o755, exist_ok=True)
    if os.path.islink(path):
        raise OSError(f"Refusing to write through symlink: {path}")
    tmp_path = os.path.join(parent, f".curfewd-{secrets.token_hex(8)}.tmp")
    try:
        with open(tmp_path, "x", encoding="utf-8") as fh:
            fh.write(content)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp_path, path)
    except Exception:
        with suppress(OSError):
            os.unlink(tmp_path)
        raise


def _merge_kde_lockscreen_ini(existing: str) -> str:
    """Merge immutable [Daemon][$i] Autolock/LockOnResume into existing kscreenlockerrc."""
    if not existing.strip():
        return _KDE_LOCKSCREEN_CONTENT
    cp = configparser.ConfigParser(interpolation=None, strict=False)
    cp.optionxform = str  # type: ignore[assignment]
    try:
        cp.read_string(existing)
    except configparser.Error:
        return existing.rstrip() + "\n\n" + _KDE_LOCKSCREEN_CONTENT
    target_sec = "Daemon][$i"
    if cp.has_section("Daemon"):
        # Preserve any existing [Daemon] settings inside the immutable section
        if not cp.has_section(target_sec):
            cp.add_section(target_sec)
        for k, v in cp.items("Daemon"):
            cp.set(target_sec, k, v)
        cp.remove_section("Daemon")
    elif not cp.has_section(target_sec):
        cp.add_section(target_sec)
    cp.set(target_sec, "Autolock", "true")
    cp.set(target_sec, "LockOnResume", "true")
    import io

    buf = io.StringIO()
    cp.write(buf, space_around_delimiters=False)
    return "# Managed by curfewd doctor --fix (KDE Kiosk immutability)\n" + buf.getvalue()


def _merge_xfce_screensaver_xml(existing: str) -> str:
    """Merge locked lock/enabled=true into existing XFCE screensaver xfconf XML."""
    if not existing.strip():
        return _XFCE_SCREENSAVER_CONTENT
    try:
        root = ET.fromstring(existing)  # nosec B314 - local root-owned /etc/xdg XML
    except ET.ParseError:
        return _XFCE_SCREENSAVER_CONTENT
    if root.tag != "channel":
        return _XFCE_SCREENSAVER_CONTENT
    root.set("name", "xfce4-screensaver")
    root.set("version", root.get("version") or "1.0")
    root.set("locked", "*")
    root.set("unlocked", "root")
    lock_prop = None
    for child in root.findall("property"):
        if child.get("name") == "lock":
            lock_prop = child
            break
    if lock_prop is None:
        lock_prop = ET.SubElement(root, "property", {"name": "lock", "type": "empty"})
    enabled_prop = None
    for child in lock_prop.findall("property"):
        if child.get("name") == "enabled":
            enabled_prop = child
            break
    if enabled_prop is None:
        enabled_prop = ET.SubElement(lock_prop, "property", {"name": "enabled"})
    enabled_prop.set("type", "bool")
    enabled_prop.set("value", "true")
    enabled_prop.set("locked", "*")
    return '<?xml version="1.0" encoding="UTF-8"?>\n' + ET.tostring(root, encoding="unicode") + "\n"


def check_dconf_lock_policy(root_prefix: str = "") -> dict[str, Any]:
    """Verify root-owned dconf screensaver lock files exist (rejecting symlinks)."""
    profile = root_prefix + DCONF_PROFILE_PATH
    db_file = root_prefix + DCONF_DB_LOCKSCREEN
    lock_file = root_prefix + DCONF_LOCKS_FILE
    missing = [
        p for p in (profile, db_file, lock_file) if not os.path.isfile(p) or os.path.islink(p)
    ]
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


def check_kde_lock_policy(root_prefix: str = "") -> dict[str, Any]:
    """Verify immutable KDE Kiosk kscreenlockerrc lock policy exists."""
    path = root_prefix + KDE_LOCKSCREEN_PATH
    content = _read_text_nofollow(path)
    if content is not None and "[Daemon][$i]" in content and "Autolock=true" in content:
        return _check(
            "kde_lock_policy",
            "ok",
            f"KDE Plasma Kiosk lock policy installed ({KDE_LOCKSCREEN_PATH})",
        )
    return _check(
        "kde_lock_policy",
        "warn",
        f"Missing immutable KDE Kiosk lock policy at {KDE_LOCKSCREEN_PATH} (run `sudo curfewctl doctor --fix`)",
        fixable=True,
    )


def check_xfce_lock_policy(root_prefix: str = "") -> dict[str, Any]:
    """Verify locked XFCE screensaver kiosk XML exists."""
    path = root_prefix + XFCE_SCREENSAVER_PATH
    content = _read_text_nofollow(path)
    if content is not None and 'locked="*"' in content and 'value="true"' in content:
        return _check(
            "xfce_lock_policy",
            "ok",
            f"XFCE screensaver kiosk policy installed ({XFCE_SCREENSAVER_PATH})",
        )
    return _check(
        "xfce_lock_policy",
        "warn",
        f"Missing XFCE screensaver kiosk XML at {XFCE_SCREENSAVER_PATH} (run `sudo curfewctl doctor --fix`)",
        fixable=True,
    )


def apply_dconf_lock_fix(root_prefix: str = "") -> dict[str, Any]:
    """Install root dconf lock-screen policy atomically and run `dconf update`."""
    if not root_prefix and os.geteuid() != 0:
        raise PermissionError("`curfewctl doctor --fix` requires root privileges")

    profile = root_prefix + DCONF_PROFILE_PATH
    db_file = root_prefix + DCONF_DB_LOCKSCREEN
    lock_file = root_prefix + DCONF_LOCKS_FILE

    existing_profile = _read_text_nofollow(profile)
    if existing_profile is None:
        if os.path.islink(profile):
            raise OSError(f"Refusing to write through symlink: {profile}")
        _write_text_atomic_nofollow(profile, _DCONF_PROFILE_CONTENT)
    elif "system-db:local" not in existing_profile:
        merged_profile = existing_profile.rstrip() + "\nsystem-db:local\n"
        _write_text_atomic_nofollow(profile, merged_profile)

    _write_text_atomic_nofollow(db_file, _DCONF_DB_CONTENT)
    _write_text_atomic_nofollow(lock_file, _DCONF_LOCKS_CONTENT)

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


def apply_kde_lock_fix(root_prefix: str = "") -> dict[str, Any]:
    """Install or merge immutable KDE Plasma Kiosk screen-lock configuration atomically."""
    if not root_prefix and os.geteuid() != 0:
        raise PermissionError("`curfewctl doctor --fix` requires root privileges")
    path = root_prefix + KDE_LOCKSCREEN_PATH
    if os.path.islink(path):
        raise OSError(f"Refusing to write through symlink: {path}")
    existing = _read_text_nofollow(path) or ""
    _write_text_atomic_nofollow(path, _merge_kde_lockscreen_ini(existing))
    return {"applied": True, "files": [path], "detail": "KDE Kiosk kscreenlockerrc installed"}


def apply_xfce_lock_fix(root_prefix: str = "") -> dict[str, Any]:
    """Install or merge locked XFCE screensaver kiosk XML configuration atomically."""
    if not root_prefix and os.geteuid() != 0:
        raise PermissionError("`curfewctl doctor --fix` requires root privileges")
    path = root_prefix + XFCE_SCREENSAVER_PATH
    if os.path.islink(path):
        raise OSError(f"Refusing to write through symlink: {path}")
    existing = _read_text_nofollow(path) or ""
    _write_text_atomic_nofollow(path, _merge_xfce_screensaver_xml(existing))
    return {"applied": True, "files": [path], "detail": "XFCE kiosk screensaver policy installed"}


def apply_all_lock_fixes(
    root_prefix: str = "",
    sessions_by_user: dict[str, list[dict[str, Any]]] | None = None,
    include_kde: bool | None = None,
    include_xfce: bool | None = None,
) -> dict[str, Any]:
    """Apply dconf hardening plus KDE Plasma / XFCE kiosk hardening when relevant."""
    res = apply_dconf_lock_fix(root_prefix=root_prefix)
    files = list(res["files"])
    details = [res["detail"]]
    detected = _detect_installed_des(sessions_by_user)

    if include_kde or (include_kde is None and "kde" in detected):
        kde_res = apply_kde_lock_fix(root_prefix=root_prefix)
        files.extend(kde_res["files"])
        details.append(kde_res["detail"])

    if include_xfce or (include_xfce is None and "xfce" in detected):
        xfce_res = apply_xfce_lock_fix(root_prefix=root_prefix)
        files.extend(xfce_res["files"])
        details.append(xfce_res["detail"])

    return {
        "applied": True,
        "files": files,
        "dconf_updated": res["dconf_updated"],
        "detail": "; ".join(details),
    }


def run_doctor(
    config_dir: str = DEFAULT_CONFIG_DIR,
    state_dir: str = DEFAULT_STATE_DIR,
    sessions_by_user: dict[str, list[dict[str, Any]]] | None = None,
    dbus_connected: bool | None = None,
    root_prefix: str = "",
) -> dict[str, Any]:
    """Run comprehensive host health diagnostics and return a structured report."""
    checks: list[dict[str, Any]] = []
    session_mode = _detect_session_mode(sessions_by_user)
    detected_des = _detect_installed_des(sessions_by_user)

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
    if os.path.isfile(root_prefix + DBUS_POLICY_PATH):
        checks.append(_check("dbus_policy", "ok", f"{DBUS_POLICY_PATH} present"))
    else:
        checks.append(_check("dbus_policy", "warn", f"Missing {DBUS_POLICY_PATH}"))

    if os.path.isfile(root_prefix + POLKIT_POLICY_PATH):
        checks.append(_check("polkit_policy", "ok", f"{POLKIT_POLICY_PATH} present"))
    else:
        checks.append(_check("polkit_policy", "warn", f"Missing {POLKIT_POLICY_PATH}"))

    # 4. Storage directories
    for label, path in (("config_dir", config_dir), ("state_dir", state_dir)):
        if os.path.isdir(path):
            checks.append(_check(label, "ok", f"{path} exists"))
        else:
            checks.append(_check(label, "warn", f"{path} not created yet"))

    # 5. Lock-screen protection policies (dconf + KDE Kiosk + XFCE Kiosk when applicable)
    checks.append(check_dconf_lock_policy(root_prefix=root_prefix))
    if "kde" in detected_des:
        checks.append(check_kde_lock_policy(root_prefix=root_prefix))
    if "xfce" in detected_des:
        checks.append(check_xfce_lock_policy(root_prefix=root_prefix))

    # 6. Helper binaries (Wayland-aware for X11-only grab-release tools)
    pure_wayland = session_mode == "wayland"
    for tool, required, x11_only in (
        ("gdbus", True, False),
        ("xdotool", False, True),
        ("wmctrl", False, True),
        ("dconf", False, False),
    ):
        found = shutil.which(tool, path=TRUSTED_BIN_PATH)
        if found:
            checks.append(_check(f"bin_{tool}", "ok", found))
        elif x11_only and pure_wayland:
            checks.append(
                _check(
                    f"bin_{tool}",
                    "ok",
                    "Not needed on Wayland (compositor manages fullscreen grabs natively)",
                )
            )
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
                    "idle": bool(s.get("idle", False)),
                    "lock_backends": backends,
                }
            )

    has_error = any(c["status"] == "error" for c in checks)
    has_warn = any(c["status"] == "warn" for c in checks)
    overall = "error" if has_error else ("warn" if has_warn else "ok")

    return {
        "version": __version__,
        "overall": overall,
        "session_mode": session_mode,
        "detected_desktops": sorted(detected_des),
        "checks": checks,
        "active_sessions": active_sessions,
    }
