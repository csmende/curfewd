"""Multi-desktop screen lock enforcement for curfewd (Spec 001 + Spec 002 LockResult telemetry).

Implements three-layer non-blocking, privilege-dropped, verified session locking:
  1. systemd-logind Manager.LockSession (caller invokes before/alongside enforcer)
  2. Verified DE screensaver activation over user's session D-Bus / CLI (Cinnamon,
     XFCE, MATE, GNOME, KDE Plasma / FreeDesktop)
  3. Staged X11 fullscreen-game grab release (XF86Ungrab, then non-destructive
     wmctrl/xdotool unfullscreen/minimize) and retry.
"""

from dataclasses import dataclass
import logging
import os
import pwd
import shutil
# Subprocess is strictly limited to privilege-dropped, trusted-PATH, list-argv helpers.
import subprocess  # nosec B404
import threading
import time

from curfewd.models import LockResult

logger = logging.getLogger("curfewd.screenlock")

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
    dbus_paths: tuple[str, ...]
    dbus_interface: str
    lock_dbus_args: tuple[str, ...] = ()
    lock_clis: tuple[tuple[str, ...], ...] = ()
    query_clis: tuple[tuple[str, ...], ...] = ()


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


def normalize_desktop_tokens(desktop_str: str) -> list[str]:
    """Parse XDG_CURRENT_DESKTOP / DESKTOP_SESSION into normalized lowercase tokens."""
    if not desktop_str:
        return []
    tokens: list[str] = []
    for raw in str(desktop_str).split(":"):
        cleaned = raw.strip().lower()
        if cleaned.startswith("x-"):
            cleaned = cleaned[2:]
        if cleaned and cleaned not in tokens:
            tokens.append(cleaned)
    return tokens


def select_backends(desktop_str: str) -> list[ScreensaverBackend]:
    """Return ordered ScreensaverBackend candidates for the detected desktop."""
    tokens = normalize_desktop_tokens(desktop_str)
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


def read_proc_environ(pid: int, proc_root: str = "/proc") -> dict[str, str]:
    """Read safe session environment keys from /proc/<pid>/environ."""
    env: dict[str, str] = {}
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


def _read_proc_uid(pid_dir: str) -> int | None:
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


def _read_proc_comm(pid_dir: str) -> str:
    try:
        with open(os.path.join(pid_dir, "comm"), "r", encoding="utf-8", errors="ignore") as fh:
            return fh.read().strip()
    except OSError:
        return ""


def _discover_user_session_env(uid: int, session_id: str, leader_pid: int, proc_root: str = "/proc") -> dict[str, str]:
    """Collect whitelisted graphical session env vars for a user's session."""
    merged = read_proc_environ(leader_pid, proc_root=proc_root)
    if merged.get("DBUS_SESSION_BUS_ADDRESS") and (merged.get("DISPLAY") or merged.get("WAYLAND_DISPLAY")):
        return merged

    try:
        entries = os.listdir(proc_root)
    except OSError:
        return merged

    candidates: list[tuple[int, dict[str, str]]] = []
    for name in entries:
        if not name.isdigit():
            continue
        pid_int = int(name)
        if pid_int == leader_pid:
            continue
        pid_dir = os.path.join(proc_root, name)
        if _read_proc_uid(pid_dir) != uid:
            continue
        comm = _read_proc_comm(pid_dir)
        is_session_proc = comm in _SESSION_PROCESS_NAMES
        if not is_session_proc and merged.get("DBUS_SESSION_BUS_ADDRESS"):
            continue

        proc_env = read_proc_environ(pid_int, proc_root=proc_root)
        if not proc_env:
            continue

        score = 0
        if is_session_proc:
            score += 4
        if session_id and proc_env.get("XDG_SESSION_ID") == session_id:
            score += 4
        if proc_env.get("DBUS_SESSION_BUS_ADDRESS"):
            score += 2
        if proc_env.get("DISPLAY") or proc_env.get("WAYLAND_DISPLAY"):
            score += 2
        if proc_env.get("XDG_CURRENT_DESKTOP"):
            score += 1
        if score > 0:
            candidates.append((score, proc_env))

    candidates.sort(key=lambda item: item[0], reverse=True)
    for _, proc_env in candidates:
        for key in _SAFE_SESSION_ENV_KEYS:
            if key not in merged and key in proc_env:
                merged[key] = proc_env[key]
        if merged.get("DBUS_SESSION_BUS_ADDRESS") and (merged.get("DISPLAY") or merged.get("WAYLAND_DISPLAY")):
            break

    return merged


def build_session_context(
    session_id: str,
    uid: int,
    username: str,
    session_type: str = "x11",
    display: str = "",
    desktop: str = "",
    leader_pid: int = 0,
    proc_root: str = "/proc",
    run_user_root: str = "/run/user",
    pw_entry=None,
) -> SessionContext:
    """Build a sanitized SessionContext from logind metadata and /proc."""
    gid = uid
    home_dir = ""
    if pw_entry is None:
        try:
            pw_entry = pwd.getpwuid(int(uid))
        except (KeyError, TypeError, ValueError):
            pw_entry = None
    if pw_entry is not None:
        gid = pw_entry.pw_gid
        home_dir = pw_entry.pw_dir
        if not username:
            username = pw_entry.pw_name

    proc_env = _discover_user_session_env(int(uid), str(session_id), int(leader_pid or 0), proc_root=proc_root)

    eff_type = (session_type or proc_env.get("XDG_SESSION_TYPE") or "x11").strip().lower()
    eff_display = display or proc_env.get("DISPLAY", "")
    eff_wayland = proc_env.get("WAYLAND_DISPLAY", "")
    eff_xauth = proc_env.get("XAUTHORITY", "")
    eff_desktop = desktop or proc_env.get("XDG_CURRENT_DESKTOP") or proc_env.get("DESKTOP_SESSION", "")

    default_runtime_dir = os.path.join(run_user_root, str(uid))
    eff_runtime = proc_env.get("XDG_RUNTIME_DIR", "")
    if not eff_runtime and os.path.isdir(default_runtime_dir):
        eff_runtime = default_runtime_dir

    eff_dbus = proc_env.get("DBUS_SESSION_BUS_ADDRESS", "")
    if not eff_dbus and eff_runtime:
        bus_sock = os.path.join(eff_runtime, "bus")
        if os.path.exists(bus_sock):
            eff_dbus = f"unix:path={bus_sock}"

    if not eff_xauth and home_dir:
        candidate_xauth = os.path.join(home_dir, ".Xauthority")
        if os.path.exists(candidate_xauth):
            eff_xauth = candidate_xauth

    return SessionContext(
        session_id=str(session_id),
        uid=int(uid),
        username=str(username),
        gid=int(gid),
        home_dir=home_dir,
        session_type=eff_type,
        display=eff_display,
        xauthority=eff_xauth,
        wayland_display=eff_wayland,
        xdg_runtime_dir=eff_runtime,
        dbus_address=eff_dbus,
        desktop=eff_desktop,
        leader_pid=int(leader_pid or 0),
    )


# Alias retained for ergonomic compatibility with external callers and tests.
discover_session_context = build_session_context


def build_safe_env(ctx: SessionContext) -> dict[str, str]:
    """Construct a minimal, safe environment for privilege-dropped user commands."""
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


def _resolve_trusted_bin(name: str) -> str | None:
    return shutil.which(name, path=TRUSTED_BIN_PATH)


def _run_as_user(ctx: SessionContext, argv: list[str], timeout: float = 3.0, runner=subprocess.run):
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
    if os.geteuid() == 0:
        kwargs["user"] = ctx.uid
        kwargs["group"] = ctx.gid
        kwargs["extra_groups"] = []

    try:
        return runner(cmd, **kwargs)
    except subprocess.TimeoutExpired:
        logger.warning("screenlock helper timed out after %.1fs: %s", timeout, cmd[0])
        return None
    except OSError as exc:
        logger.debug("screenlock helper failed to launch (%s): %s", cmd[0], exc)
        return None


def _parse_gdbus_bool(stdout: str | None) -> bool | None:
    if not stdout:
        return None
    text = stdout.strip().lower()
    if "true" in text:
        return True
    if "false" in text:
        return False
    return None


class ScreenLockEnforcer:
    """Coordinates verified multi-DE screen locking without blocking the GLib loop."""

    def __init__(
        self,
        runner=subprocess.run,
        which_fn=_resolve_trusted_bin,
        sleep_fn=time.sleep,
        logind_settle_sec: float = 0.25,
        verify_settle_sec: float = 0.60,
        grab_settle_sec: float = 0.25,
        cmd_timeout_sec: float = 3.0,
    ) -> None:
        self._runner = runner
        self._which = which_fn
        self._sleep = sleep_fn
        self._logind_settle_sec = logind_settle_sec
        self._verify_settle_sec = verify_settle_sec
        self._grab_settle_sec = grab_settle_sec
        self._cmd_timeout_sec = cmd_timeout_sec
        self._lock = threading.Lock()
        self._in_flight: set[str] = set()

    def _exec(self, ctx: SessionContext, argv: list[str], timeout: float | None = None):
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

    def _gdbus_has_owner(self, ctx: SessionContext, bus_name: str) -> bool | None:
        if not ctx.dbus_address:
            return None
        res = self._exec(
            ctx,
            [
                "gdbus", "call", "--session", "--timeout", "2",
                "--dest", "org.freedesktop.DBus",
                "--object-path", "/org/freedesktop/DBus",
                "--method", "org.freedesktop.DBus.NameHasOwner",
                bus_name,
            ],
            timeout=2.5,
        )
        if res is None or res.returncode != 0:
            return None
        return _parse_gdbus_bool(res.stdout)

    def is_screensaver_active(
        self,
        ctx: SessionContext,
        backends: list[ScreensaverBackend],
    ) -> tuple[bool | None, str | None]:
        """Check whether any candidate screensaver reports an active lock screen."""
        saw_inactive = False
        inactive_backend: str | None = None

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
                            "gdbus", "call", "--session", "--timeout", "2",
                            "--dest", backend.dbus_dest,
                            "--object-path", obj_path,
                            "--method", f"{backend.dbus_interface}.GetActive",
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

    def invoke_backend_lock(
        self,
        ctx: SessionContext,
        backend: ScreensaverBackend,
    ) -> tuple[bool, str | None]:
        if ctx.dbus_address and self._which("gdbus"):
            for obj_path in backend.dbus_paths:
                argv = [
                    "gdbus", "call", "--session",
                    "--timeout", str(int(self._cmd_timeout_sec)),
                    "--dest", backend.dbus_dest,
                    "--object-path", obj_path,
                    "--method", f"{backend.dbus_interface}.Lock",
                ] + list(backend.lock_dbus_args)
                res = self._exec(ctx, argv)
                if res is not None and res.returncode == 0:
                    return True, f"dbus:{backend.dbus_dest}"

        for cli in backend.lock_clis:
            res = self._exec(ctx, list(cli))
            if res is not None and res.returncode == 0:
                return True, f"cli:{cli[0]}"

        return False, None

    def _try_lock_backends(self, ctx: SessionContext, backends: list[ScreensaverBackend]):
        for backend in backends:
            ok, method = self.invoke_backend_lock(ctx, backend)
            if ok:
                return backend, method
        return None, None

    def _try_x11_ungrab_key(self, ctx: SessionContext) -> bool:
        if ctx.session_type != "x11" or not ctx.display:
            return False
        res = self._exec(ctx, ["xdotool", "key", "XF86Ungrab"], timeout=2.0)
        return res is not None and res.returncode == 0

    def _try_x11_release_fullscreen(self, ctx: SessionContext) -> bool:
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

    def enforce_session_lock_detailed(
        self,
        ctx: SessionContext,
        release_x11_grab: bool = True,
    ) -> LockResult:
        """Verify logind lock engaged; fall back to DE screensaver + X11 grab release."""
        t0 = time.monotonic()

        def _done(locked: bool, verified: bool, backend: str, stage: str, error: str = "") -> LockResult:
            elapsed = int((time.monotonic() - t0) * 1000)
            return LockResult(
                session_id=ctx.session_id,
                username=ctx.username,
                locked=locked,
                verified=verified,
                backend=backend,
                stage=stage,
                elapsed_ms=elapsed,
                error=error,
            )

        backends = select_backends(ctx.desktop)
        logger.debug(
            "enforcing lock for user %r session %r (type=%s, desktop=%s)",
            ctx.username, ctx.session_id, ctx.session_type, ctx.desktop or "unknown",
        )

        if self._logind_settle_sec > 0:
            self._sleep(self._logind_settle_sec)

        active, active_be = self.is_screensaver_active(ctx, backends)
        if active is True:
            logger.info("session %r for user %r is locked (verified via %s)", ctx.session_id, ctx.username, active_be)
            return _done(True, True, str(active_be or "logind"), "logind")

        matched_backend, method = self._try_lock_backends(ctx, backends)
        if matched_backend is not None:
            if self._verify_settle_sec > 0:
                self._sleep(self._verify_settle_sec)
            ordered = [matched_backend] + [b for b in backends if b != matched_backend]
            active, active_be = self.is_screensaver_active(ctx, ordered)
            if active is True:
                logger.info("session %r for user %r locked via %s (verified)", ctx.session_id, ctx.username, method)
                return _done(True, True, str(method), "dbus_or_cli")
            if active is None:
                logger.info("session %r for user %r invoked %s (unverifiable)", ctx.session_id, ctx.username, method)
                return _done(True, False, str(method), "dbus_or_cli")

            if active is False and release_x11_grab and ctx.session_type == "x11":
                if self._try_x11_ungrab_key(ctx):
                    if self._grab_settle_sec > 0:
                        self._sleep(self._grab_settle_sec)
                    self.invoke_backend_lock(ctx, matched_backend)
                    if self._verify_settle_sec > 0:
                        self._sleep(self._verify_settle_sec)
                    active, _ = self.is_screensaver_active(ctx, ordered)
                    if active is True:
                        logger.info("session %r for user %r locked via %s after XF86Ungrab", ctx.session_id, ctx.username, method)
                        return _done(True, True, str(method), "xf86ungrab")

                if self._try_x11_release_fullscreen(ctx):
                    logger.info("released fullscreen X11 focus for user %r session %r, retrying lock", ctx.username, ctx.session_id)
                    if self._grab_settle_sec > 0:
                        self._sleep(self._grab_settle_sec)
                    self.invoke_backend_lock(ctx, matched_backend)
                    if self._verify_settle_sec > 0:
                        self._sleep(self._verify_settle_sec)
                    active, _ = self.is_screensaver_active(ctx, ordered)
                    if active is True:
                        logger.info("session %r for user %r locked via %s after fullscreen grab release", ctx.session_id, ctx.username, method)
                        return _done(True, True, str(method), "fullscreen_release")

            msg = (
                f"invoked {method} but screensaver did not verify active "
                "(check dconf lock-enabled / disable-lock-screen or X11 grabs)"
            )
            logger.warning("session %r for user %r: %s", ctx.session_id, ctx.username, msg)
            return _done(False, False, str(method), "failed", msg)

        msg = f"no desktop screensaver backend succeeded (desktop={ctx.desktop or 'unknown'}, dbus={bool(ctx.dbus_address)})"
        logger.warning("session %r for user %r: %s", ctx.session_id, ctx.username, msg)
        return _done(False, False, "", "failed", msg)

    def enforce_session_lock(self, ctx: SessionContext, release_x11_grab: bool = True) -> bool:
        """Boolean convenience wrapper around enforce_session_lock_detailed."""
        return self.enforce_session_lock_detailed(ctx, release_x11_grab=release_x11_grab).locked

    def enforce_session_lock_async(self, ctx: SessionContext, release_x11_grab: bool = True) -> bool:
        """Spawn a non-blocking daemon thread with per-session in-flight deduplication."""
        key = f"{ctx.uid}:{ctx.session_id}"
        with self._lock:
            if key in self._in_flight:
                logger.debug("lock attempt already in progress for session %r", ctx.session_id)
                return False
            self._in_flight.add(key)

        def _worker() -> None:
            try:
                self.enforce_session_lock(ctx, release_x11_grab=release_x11_grab)
            except Exception as exc:
                logger.error("unexpected error in screenlock helper for session %r: %s", ctx.session_id, exc)
            finally:
                with self._lock:
                    self._in_flight.discard(key)

        thread = threading.Thread(target=_worker, name=f"curfewd-lock-{ctx.session_id}", daemon=True)
        thread.start()
        return True
