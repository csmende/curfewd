"""
Multi-desktop screen lock enforcement for curfewd (Timekpr-nExT fork).

Upstream Timekpr-nExT relies solely on ``org.freedesktop.login1.Manager.LockSession``
which broadcasts a system D-Bus ``Lock`` signal that some desktop environments
(notably Linux Mint Cinnamon where ``cinnamon-screensaver`` exits after 30s of
inactivity, and several XFCE/MATE configurations) do not reliably handle.

Additionally, fullscreen X11 applications and games (Steam/Proton, SDL2, Minecraft,
Roblox Sober, etc.) frequently hold X11 pointer/keyboard grabs that cause
``cinnamon-screensaver`` / ``xfce4-screensaver`` / ``mate-screensaver`` to abort
locking silently while returning exit code 0.

This module implements non-blocking, privilege-dropped, verified session locking
with optional X11 grab release and retry.
"""

from dataclasses import dataclass
import os
import pwd
import shutil
import subprocess  # nosec B404 - privilege-dropped, trusted-PATH, list-argv helpers only
import threading
import time

try:
    from timekpr.common.constants import constants as cons
    from timekpr.common.log import log
except ImportError:  # pragma: no cover - standalone fallback
    import logging

    class _FallbackCons:
        TK_LOG_LEVEL_EXTRA_DEBUG = 5
        TK_LOG_LEVEL_DEBUG = 10
        TK_LOG_LEVEL_INFO = 20
        TK_LOG_LEVEL_WARN = 30
        TK_LOG_LEVEL_ERR = 40

    class _FallbackLog:
        @staticmethod
        def log(level, message):
            logging.getLogger("curfewd.screenlock").log(level, message)

    cons = _FallbackCons()
    log = _FallbackLog()


# Trusted system binary search path to prevent user PATH hijacking.
TRUSTED_BIN_PATH = "/usr/bin:/bin:/usr/sbin:/sbin"

# Environment variables safe to propagate from a user's graphical session.
_SAFE_SESSION_ENV_KEYS = (
    "DBUS_SESSION_BUS_ADDRESS",
    "DISPLAY",
    "XAUTHORITY",
    "WAYLAND_DISPLAY",
    "XDG_RUNTIME_DIR",
    "XDG_CURRENT_DESKTOP",
    "DESKTOP_SESSION",
    "XDG_SESSION_TYPE",
    "XDG_SESSION_ID",
)

# Graphical session processes to inspect if the logind leader lacks session vars.
_SESSION_PROCESS_NAMES = frozenset({
    "cinnamon-session",
    "cinnamon",
    "xfce4-session",
    "mate-session",
    "gnome-session-binary",
    "gnome-shell",
    "plasmashell",
    "ksmserver",
    "lxqt-session",
    "budgie-wm",
    "budgie-daemon",
    "x-session-manager",
    "sway",
    "Hyprland",
    "wayfire",
    "dbus-daemon",
})


@dataclass
class SessionContext:
    """Sanitized context needed to interact with a user's graphical session."""

    session_id: str
    uid: int
    username: str
    gid: int
    home_dir: str
    session_type: str = "x11"
    display: str = ""
    xauthority: str = ""
    wayland_display: str = ""
    xdg_runtime_dir: str = ""
    dbus_address: str = ""
    desktop: str = ""
    leader_pid: int = 0


@dataclass(frozen=True)
class ScreensaverBackend:
    """D-Bus and CLI specification for a desktop screensaver implementation."""

    name: str
    dbus_dest: str
    dbus_paths: tuple
    dbus_interface: str
    lock_dbus_args: tuple = ()
    lock_clis: tuple = ()
    query_clis: tuple = ()


BACKENDS = {
    "cinnamon": ScreensaverBackend(
        name="cinnamon",
        dbus_dest="org.cinnamon.ScreenSaver",
        dbus_paths=("/org/cinnamon/ScreenSaver",),
        dbus_interface="org.cinnamon.ScreenSaver",
        # org.cinnamon.ScreenSaver.Lock(in s message) requires a message string.
        lock_dbus_args=("",),
        lock_clis=(("cinnamon-screensaver-command", "--lock"),),
        query_clis=(("cinnamon-screensaver-command", "-q"),),
    ),
    "xfce": ScreensaverBackend(
        name="xfce",
        dbus_dest="org.xfce.ScreenSaver",
        dbus_paths=("/org/xfce/ScreenSaver",),
        dbus_interface="org.xfce.ScreenSaver",
        lock_dbus_args=(),
        lock_clis=(("xfce4-screensaver-command", "--lock"), ("xflock4",)),
        query_clis=(("xfce4-screensaver-command", "-q"),),
    ),
    "mate": ScreensaverBackend(
        name="mate",
        dbus_dest="org.mate.ScreenSaver",
        dbus_paths=("/org/mate/ScreenSaver",),
        dbus_interface="org.mate.ScreenSaver",
        lock_dbus_args=(),
        lock_clis=(("mate-screensaver-command", "--lock"),),
        query_clis=(("mate-screensaver-command", "-q"),),
    ),
    "gnome": ScreensaverBackend(
        name="gnome",
        dbus_dest="org.gnome.ScreenSaver",
        dbus_paths=("/org/gnome/ScreenSaver",),
        dbus_interface="org.gnome.ScreenSaver",
        lock_dbus_args=(),
        lock_clis=(("gnome-screensaver-command", "--lock"),),
        query_clis=(("gnome-screensaver-command", "-q"),),
    ),
    "freedesktop": ScreensaverBackend(
        name="freedesktop",
        dbus_dest="org.freedesktop.ScreenSaver",
        dbus_paths=("/org/freedesktop/ScreenSaver", "/ScreenSaver"),
        dbus_interface="org.freedesktop.ScreenSaver",
        lock_dbus_args=(),
        lock_clis=(("xdg-screensaver", "lock"),),
        query_clis=(),
    ),
}


def normalize_desktop_tokens(desktop_str):
    """Parse XDG_CURRENT_DESKTOP / DESKTOP_SESSION into normalized lowercase tokens."""
    if not desktop_str:
        return []
    tokens = []
    for raw in str(desktop_str).split(":"):
        cleaned = raw.strip().lower()
        if cleaned.startswith("x-"):
            cleaned = cleaned[2:]
        if cleaned and cleaned not in tokens:
            tokens.append(cleaned)
    return tokens


def select_backends(desktop_str):
    """Return ordered ScreensaverBackend candidates for the detected desktop."""
    tokens = normalize_desktop_tokens(desktop_str)
    ordered_names = []

    if "cinnamon" in tokens:
        ordered_names = ["cinnamon", "freedesktop"]
    elif "xfce" in tokens or "xfce4" in tokens:
        ordered_names = ["xfce", "freedesktop", "mate", "gnome"]
    elif "mate" in tokens:
        ordered_names = ["mate", "freedesktop"]
    elif any(t in tokens for t in ("gnome", "unity", "budgie", "pantheon", "ubuntu")):
        ordered_names = ["gnome", "freedesktop"]
    elif any(t in tokens for t in ("kde", "plasma", "lxqt")):
        ordered_names = ["freedesktop"]
    else:
        ordered_names = ["cinnamon", "xfce", "mate", "gnome", "freedesktop"]

    return [BACKENDS[name] for name in ordered_names]


def read_proc_environ(pid, proc_root="/proc"):
    """Read safe session environment keys from /proc/<pid>/environ."""
    env = {}
    if not pid or pid <= 0:
        return env
    environ_path = os.path.join(proc_root, str(pid), "environ")
    try:
        with open(environ_path, "rb") as fh:
            raw = fh.read()
    except OSError:
        return env

    for entry in raw.split(b"\x00"):
        if b"=" not in entry:
            continue
        k_bytes, v_bytes = entry.split(b"=", 1)
        key = k_bytes.decode("utf-8", errors="ignore")
        val = v_bytes.decode("utf-8", errors="ignore")
        if key in _SAFE_SESSION_ENV_KEYS and val:
            env[key] = val
    return env


def _read_proc_uid(pid_dir):
    """Return the real UID of a process from /proc/<pid>/status, or None."""
    try:
        with open(os.path.join(pid_dir, "status"), "r", encoding="utf-8", errors="ignore") as fh:
            for line in fh:
                if line.startswith("Uid:"):
                    parts = line.split()
                    if len(parts) >= 2:
                        return int(parts[1])
    except (OSError, ValueError):
        return None
    return None


def _read_proc_comm(pid_dir):
    """Return the short process name from /proc/<pid>/comm."""
    try:
        with open(os.path.join(pid_dir, "comm"), "r", encoding="utf-8", errors="ignore") as fh:
            return fh.read().strip()
    except OSError:
        return ""


def _scan_user_session_env(uid, session_id="", proc_root="/proc"):
    """Scan graphical session processes owned by uid to fill missing session vars."""
    merged = {}
    fallback = {}
    try:
        entries = os.listdir(proc_root)
    except OSError:
        return merged

    for name in entries:
        if not name.isdigit():
            continue
        pid_dir = os.path.join(proc_root, name)
        comm = _read_proc_comm(pid_dir)
        if comm not in _SESSION_PROCESS_NAMES:
            continue
        if _read_proc_uid(pid_dir) != uid:
            continue

        proc_env = read_proc_environ(int(name), proc_root=proc_root)
        if not proc_env:
            continue

        target = merged if (session_id and proc_env.get("XDG_SESSION_ID") == session_id) else fallback
        for key in _SAFE_SESSION_ENV_KEYS:
            if key in proc_env and key not in target:
                target[key] = proc_env[key]

        if (
            "DBUS_SESSION_BUS_ADDRESS" in merged
            and "XDG_CURRENT_DESKTOP" in merged
            and ("DISPLAY" in merged or "WAYLAND_DISPLAY" in merged)
            and "XAUTHORITY" in merged
        ):
            break

    for key, val in fallback.items():
        merged.setdefault(key, val)
    return merged


def discover_session_context(
    session_id,
    uid,
    username="",
    session_type="x11",
    display="",
    desktop="",
    leader_pid=0,
    proc_root="/proc",
    run_user_root="/run/user",
):
    """Build a complete, sanitized SessionContext from logind metadata and /proc."""
    gid = uid
    home_dir = ""
    try:
        pw = pwd.getpwuid(int(uid))
        username = username or pw.pw_name
        gid = pw.pw_gid
        home_dir = pw.pw_dir
    except KeyError:
        pass

    env = read_proc_environ(leader_pid, proc_root=proc_root)
    needs_scan = (
        "DBUS_SESSION_BUS_ADDRESS" not in env
        or not (desktop or env.get("XDG_CURRENT_DESKTOP") or env.get("DESKTOP_SESSION"))
        or (session_type == "x11" and ("XAUTHORITY" not in env or not (display or env.get("DISPLAY"))))
        or (session_type == "wayland" and "WAYLAND_DISPLAY" not in env)
    )
    if needs_scan:
        scanned = _scan_user_session_env(int(uid), session_id=session_id, proc_root=proc_root)
        for key, val in scanned.items():
            env.setdefault(key, val)

    xdg_runtime_dir = env.get("XDG_RUNTIME_DIR", "")
    if not xdg_runtime_dir:
        candidate_runtime = os.path.join(run_user_root, str(uid))
        if os.path.isdir(candidate_runtime):
            xdg_runtime_dir = candidate_runtime

    dbus_address = env.get("DBUS_SESSION_BUS_ADDRESS", "")
    if not dbus_address and xdg_runtime_dir:
        bus_sock = os.path.join(xdg_runtime_dir, "bus")
        if os.path.exists(bus_sock):
            dbus_address = "unix:path=%s" % (bus_sock,)

    resolved_display = display or env.get("DISPLAY", "")
    if not resolved_display and session_type == "x11":
        resolved_display = ":0"

    xauthority = env.get("XAUTHORITY", "")
    if not xauthority and session_type == "x11" and home_dir:
        candidate_xauth = os.path.join(home_dir, ".Xauthority")
        if os.path.isfile(candidate_xauth):
            xauthority = candidate_xauth

    resolved_desktop = desktop or env.get("XDG_CURRENT_DESKTOP") or env.get("DESKTOP_SESSION") or ""
    wayland_display = env.get("WAYLAND_DISPLAY", "")

    return SessionContext(
        session_id=str(session_id),
        uid=int(uid),
        username=username or str(uid),
        gid=int(gid),
        home_dir=home_dir,
        session_type=str(session_type or "x11"),
        display=resolved_display,
        xauthority=xauthority,
        wayland_display=wayland_display,
        xdg_runtime_dir=xdg_runtime_dir,
        dbus_address=dbus_address,
        desktop=resolved_desktop,
        leader_pid=int(leader_pid or 0),
    )


def build_safe_env(ctx):
    """Build a strictly whitelisted environment for user-privilege helper commands."""
    safe_env = {
        "PATH": TRUSTED_BIN_PATH,
        "LC_ALL": "C",
        "USER": ctx.username,
        "LOGNAME": ctx.username,
    }
    if ctx.home_dir:
        safe_env["HOME"] = ctx.home_dir
    if ctx.xdg_runtime_dir:
        safe_env["XDG_RUNTIME_DIR"] = ctx.xdg_runtime_dir
    if ctx.dbus_address:
        safe_env["DBUS_SESSION_BUS_ADDRESS"] = ctx.dbus_address
    if ctx.display:
        safe_env["DISPLAY"] = ctx.display
    if ctx.xauthority:
        safe_env["XAUTHORITY"] = ctx.xauthority
    if ctx.wayland_display:
        safe_env["WAYLAND_DISPLAY"] = ctx.wayland_display
    if ctx.desktop:
        safe_env["XDG_CURRENT_DESKTOP"] = ctx.desktop
    if ctx.session_type:
        safe_env["XDG_SESSION_TYPE"] = ctx.session_type
    return safe_env


def _resolve_trusted_bin(name):
    """Resolve binary strictly inside TRUSTED_BIN_PATH."""
    return shutil.which(name, path=TRUSTED_BIN_PATH)


def _run_as_user(ctx, argv, timeout=3.0, runner=subprocess.run):
    """Execute a command with dropped user privileges and bounded timeout."""
    if not argv:
        return None
    bin_path = _resolve_trusted_bin(argv[0]) if not os.path.isabs(argv[0]) else argv[0]
    if not bin_path:
        return None

    cmd = [bin_path] + list(argv[1:])
    env = build_safe_env(ctx)

    kwargs = {
        "env": env,
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.PIPE,
        "stderr": subprocess.PIPE,
        "text": True,
        "timeout": timeout,
        "check": False,
    }

    # Drop root privileges when running as root; skip credential switch in unprivileged tests.
    if os.geteuid() == 0:
        kwargs["user"] = ctx.uid
        kwargs["group"] = ctx.gid
        kwargs["extra_groups"] = []

    try:
        return runner(cmd, **kwargs)
    except subprocess.TimeoutExpired:
        log.log(cons.TK_LOG_LEVEL_WARN, "WARNING: screenlock helper timed out after %.1fs: %s" % (timeout, cmd[0]))
        return None
    except OSError as exc:
        log.log(cons.TK_LOG_LEVEL_DEBUG, "screenlock helper failed to launch (%s): %s" % (cmd[0], exc))
        return None


def _parse_gdbus_bool(stdout):
    """Parse gdbus '(true,)' / '(false,)' boolean tuple output."""
    if not stdout:
        return None
    text = stdout.strip().lower()
    if "true" in text:
        return True
    if "false" in text:
        return False
    return None


class ScreenLockEnforcer(object):
    """Coordinates verified multi-DE screen locking without blocking the GLib loop."""

    def __init__(
        self,
        runner=subprocess.run,
        which_fn=_resolve_trusted_bin,
        sleep_fn=time.sleep,
        logind_settle_sec=0.25,
        verify_settle_sec=0.60,
        grab_settle_sec=0.25,
        cmd_timeout_sec=3.0,
    ):
        self._runner = runner
        self._which = which_fn
        self._sleep = sleep_fn
        self._logind_settle_sec = logind_settle_sec
        self._verify_settle_sec = verify_settle_sec
        self._grab_settle_sec = grab_settle_sec
        self._cmd_timeout_sec = cmd_timeout_sec
        self._lock = threading.Lock()
        self._in_flight = set()

    def _exec(self, ctx, argv, timeout=None):
        """Resolve and execute argv as the session user via configured runner."""
        if not argv:
            return None
        bin_path = self._which(argv[0]) if not os.path.isabs(argv[0]) else argv[0]
        if not bin_path:
            return None
        return _run_as_user(
            ctx,
            [bin_path] + list(argv[1:]),
            timeout=self._cmd_timeout_sec if timeout is None else timeout,
            runner=self._runner,
        )

    def _gdbus_has_owner(self, ctx, bus_name):
        """Check if a D-Bus well-known name currently has an active owner (no activation)."""
        if not ctx.dbus_address:
            return None
        res = self._exec(
            ctx,
            [
                "gdbus",
                "call",
                "--session",
                "--timeout",
                "2",
                "--dest",
                "org.freedesktop.DBus",
                "--object-path",
                "/org/freedesktop/DBus",
                "--method",
                "org.freedesktop.DBus.NameHasOwner",
                bus_name,
            ],
            timeout=2.5,
        )
        if res is None or res.returncode != 0:
            return None
        return _parse_gdbus_bool(res.stdout)

    def is_screensaver_active(self, ctx, backends):
        """Check whether any candidate screensaver reports an active lock screen.

        Returns (True|False|None, backend_name|None). Uses NameHasOwner first so
        sleeping D-Bus-activatable daemons (like cinnamon-screensaver after 30s)
        are not woken unnecessarily.
        """
        saw_inactive = False
        inactive_backend = None

        if ctx.dbus_address and self._which("gdbus"):
            for backend in backends:
                has_owner = self._gdbus_has_owner(ctx, backend.dbus_dest)
                if has_owner is False:
                    saw_inactive = True
                    inactive_backend = inactive_backend or backend.name
                    continue
                if has_owner is not True:
                    continue

                for obj_path in backend.dbus_paths:
                    res = self._exec(
                        ctx,
                        [
                            "gdbus",
                            "call",
                            "--session",
                            "--timeout",
                            "2",
                            "--dest",
                            backend.dbus_dest,
                            "--object-path",
                            obj_path,
                            "--method",
                            "%s.GetActive" % (backend.dbus_interface,),
                        ],
                        timeout=2.5,
                    )
                    if res is not None and res.returncode == 0:
                        state = _parse_gdbus_bool(res.stdout)
                        if state is True:
                            return True, backend.name
                        if state is False:
                            saw_inactive = True
                            inactive_backend = backend.name
                            break

        if saw_inactive:
            return False, inactive_backend

        # Fallback CLI query only when gdbus/session bus wasn't usable
        for backend in backends:
            for cli in backend.query_clis:
                res = self._exec(ctx, list(cli), timeout=2.5)
                if res is not None and res.returncode == 0:
                    out = (res.stdout or "").lower()
                    if "is active" in out and "inactive" not in out:
                        return True, backend.name
                    if "inactive" in out:
                        return False, backend.name
        return None, None

    def invoke_backend_lock(self, ctx, backend):
        """Invoke a single screensaver backend via D-Bus first, then CLI fallback."""
        if ctx.dbus_address and self._which("gdbus"):
            for obj_path in backend.dbus_paths:
                argv = [
                    "gdbus",
                    "call",
                    "--session",
                    "--timeout",
                    str(int(self._cmd_timeout_sec)),
                    "--dest",
                    backend.dbus_dest,
                    "--object-path",
                    obj_path,
                    "--method",
                    "%s.Lock" % (backend.dbus_interface,),
                ] + list(backend.lock_dbus_args)
                res = self._exec(ctx, argv)
                if res is not None and res.returncode == 0:
                    return True, "dbus:%s" % (backend.dbus_dest,)

        for cli in backend.lock_clis:
            res = self._exec(ctx, list(cli))
            if res is not None and res.returncode == 0:
                return True, "cli:%s" % (cli[0],)

        return False, None

    def _try_lock_backends(self, ctx, backends):
        """Attempt locking with ordered backends; stop on first successful invocation."""
        for backend in backends:
            ok, method = self.invoke_backend_lock(ctx, backend)
            if ok:
                return backend, method
        return None, None

    def _try_x11_ungrab_key(self, ctx):
        """Send XF86Ungrab (effective when Xorg AllowDeactivateGrabs is enabled)."""
        if ctx.session_type != "x11" or not ctx.display:
            return False
        res = self._exec(ctx, ["xdotool", "key", "XF86Ungrab"], timeout=2.0)
        return res is not None and res.returncode == 0

    def _try_x11_release_fullscreen(self, ctx):
        """Non-destructively un-fullscreen or minimize active X11 window so games drop grabs."""
        if ctx.session_type != "x11" or not ctx.display:
            return False
        attempted = False
        res_wm = self._exec(ctx, ["wmctrl", "-r", ":ACTIVE:", "-b", "remove,fullscreen"], timeout=2.0)
        if res_wm is not None and res_wm.returncode == 0:
            attempted = True
        res_xd = self._exec(ctx, ["xdotool", "getactivewindow", "windowminimize"], timeout=2.0)
        if res_xd is not None and res_xd.returncode == 0:
            attempted = True
        return attempted

    def enforce_session_lock(self, ctx, release_x11_grab=True):
        """Verify logind lock engaged; fall back to direct DE lock + X11 grab release."""
        backends = select_backends(ctx.desktop)
        log.log(
            cons.TK_LOG_LEVEL_DEBUG,
            "curfewd screenlock: enforcing lock for user \"%s\" session \"%s\" (type=%s, desktop=%s)"
            % (ctx.username, ctx.session_id, ctx.session_type, ctx.desktop or "unknown"),
        )

        # Give systemd-logind LockSession a short moment to work natively (GNOME/KDE/Wayland).
        if self._logind_settle_sec > 0:
            self._sleep(self._logind_settle_sec)

        active, active_be = self.is_screensaver_active(ctx, backends)
        if active is True:
            log.log(
                cons.TK_LOG_LEVEL_INFO,
                "INFO: session \"%s\" for user \"%s\" is locked (verified via %s)"
                % (ctx.session_id, ctx.username, active_be),
            )
            return True

        # Layer 2: directly activate the DE screensaver over user session D-Bus / CLI.
        matched_backend, method = self._try_lock_backends(ctx, backends)
        if matched_backend is not None:
            if self._verify_settle_sec > 0:
                self._sleep(self._verify_settle_sec)
            active, active_be = self.is_screensaver_active(ctx, [matched_backend] + [b for b in backends if b != matched_backend])
            if active is True:
                log.log(
                    cons.TK_LOG_LEVEL_INFO,
                    "INFO: session \"%s\" for user \"%s\" locked via %s (verified)"
                    % (ctx.session_id, ctx.username, method),
                )
                return True
            if active is None:
                log.log(
                    cons.TK_LOG_LEVEL_INFO,
                    "INFO: session \"%s\" for user \"%s\" invoked %s (screensaver state could not be verified)"
                    % (ctx.session_id, ctx.username, method),
                )
                return True

            # Layer 3: X11 fullscreen-game grab release and retry (only when confirmed inactive).
            if active is False and release_x11_grab and ctx.session_type == "x11":
                retry_targets = [matched_backend] + [b for b in backends if b != matched_backend]

                # Stage 3a: XF86Ungrab (instant, does not disturb windows if Xorg allows it).
                if self._try_x11_ungrab_key(ctx):
                    if self._grab_settle_sec > 0:
                        self._sleep(self._grab_settle_sec)
                    self.invoke_backend_lock(ctx, matched_backend)
                    if self._verify_settle_sec > 0:
                        self._sleep(self._verify_settle_sec)
                    active, _ = self.is_screensaver_active(ctx, retry_targets)
                    if active is True:
                        log.log(
                            cons.TK_LOG_LEVEL_INFO,
                            "INFO: session \"%s\" for user \"%s\" locked via %s after XF86Ungrab"
                            % (ctx.session_id, ctx.username, method),
                        )
                        return True

                # Stage 3b: Non-destructively un-fullscreen / minimize active window so SDL2/Proton/etc. drop grabs.
                if self._try_x11_release_fullscreen(ctx):
                    log.log(
                        cons.TK_LOG_LEVEL_INFO,
                        "INFO: released fullscreen X11 focus for user \"%s\" session \"%s\", retrying lock"
                        % (ctx.username, ctx.session_id),
                    )
                    if self._grab_settle_sec > 0:
                        self._sleep(self._grab_settle_sec)
                    self.invoke_backend_lock(ctx, matched_backend)
                    if self._verify_settle_sec > 0:
                        self._sleep(self._verify_settle_sec)
                    active, _ = self.is_screensaver_active(ctx, retry_targets)
                    if active is True:
                        log.log(
                            cons.TK_LOG_LEVEL_INFO,
                            "INFO: session \"%s\" for user \"%s\" locked via %s after fullscreen grab release"
                            % (ctx.session_id, ctx.username, method),
                        )
                        return True

            log.log(
                cons.TK_LOG_LEVEL_WARN,
                "WARNING: session \"%s\" for user \"%s\" invoked %s but screensaver did not verify active "
                "(check desktop lock-enabled / disable-lock-screen settings or fullscreen X11 grabs)"
                % (ctx.session_id, ctx.username, method),
            )
            return False

        log.log(
            cons.TK_LOG_LEVEL_WARN,
            "WARNING: could not invoke any desktop screensaver locker for user \"%s\" session \"%s\" (desktop=%s, dbus=%s)"
            % (ctx.username, ctx.session_id, ctx.desktop or "unknown", bool(ctx.dbus_address)),
        )
        return False

    def enforce_session_lock_async(self, ctx, release_x11_grab=True):
        """Spawn a non-blocking daemon thread to enforce session lock with per-session deduplication."""
        key = "%s:%s" % (ctx.uid, ctx.session_id)
        with self._lock:
            if key in self._in_flight:
                log.log(
                    cons.TK_LOG_LEVEL_DEBUG,
                    "curfewd screenlock: lock attempt already in progress for session \"%s\"" % (ctx.session_id,),
                )
                return False
            self._in_flight.add(key)

        def _worker():
            try:
                self.enforce_session_lock(ctx, release_x11_grab=release_x11_grab)
            except Exception as exc:
                log.log(
                    cons.TK_LOG_LEVEL_ERR,
                    "ERROR: unexpected error in screenlock helper for session \"%s\": %s" % (ctx.session_id, exc),
                )
            finally:
                with self._lock:
                    self._in_flight.discard(key)

        thread = threading.Thread(target=_worker, name="curfewd-lock-%s" % (ctx.session_id,), daemon=True)
        thread.start()
        return True
