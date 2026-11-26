"""Lean, Glade-free, auto-reconnecting desktop tray, HUD, and notification client (`curfewd-tray`)."""

import argparse
import fcntl
import getpass
import json
import logging
import os
import re
import shutil
import signal

# Subprocess is strictly limited to trusted system binaries (shell=False).
import subprocess  # nosec B404
import sys
import threading
from collections.abc import Callable
from contextlib import suppress
from typing import Any

from curfewd import __version__
from curfewd.dbus.client import CurfewClient, CurfewError
from curfewd.engine.screenlock import TRUSTED_BIN_PATH, select_backends
from curfewd.parsing import format_duration
from curfewd.tray.hud import AudioChimePlayer, CountdownHUD

logger = logging.getLogger("curfewd.tray")

ICONS_DIR = "/usr/share/curfewd/icons"
_ICON_MAP = {
    "ok": "curfewd-tray-ok",
    "homework": "curfewd-tray-ok",
    "warning": "curfewd-tray-warning",
    "countdown": "curfewd-tray-alert",
    "locked_out": "curfewd-tray-alert",
    "paused": "curfewd-tray-unlimited",
    "unrestricted": "curfewd-tray-unlimited",
}
_HEX_WIN_RE = re.compile(r"0x[0-9a-fA-F]+")
_PID_RE = re.compile(r"=\s*(\d+)")
_TRAY_LOCK_FH: Any = None


def acquire_tray_lock() -> bool:
    """Prevent duplicate tray instances when both XDG autostart and systemd user unit are active."""
    global _TRAY_LOCK_FH
    runtime_dir = os.environ.get("XDG_RUNTIME_DIR") or f"/run/user/{os.getuid()}"
    if not os.path.isdir(runtime_dir):
        return True
    lock_path = os.path.join(runtime_dir, "curfewd-tray.lock")
    flags = os.O_WRONLY | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0)
    fh = None
    try:
        fd = os.open(lock_path, flags, 0o600)
        fh = os.fdopen(fd, "w")
        fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
        fh.truncate(0)
        fh.write(f"{os.getpid()}\n")
        fh.flush()
        _TRAY_LOCK_FH = fh
        return True
    except BlockingIOError:
        if fh is not None:
            fh.close()
        return False
    except OSError:
        if fh is not None:
            fh.close()
        return True


class SessionScreensaverProbe:
    """Resilient session D-Bus screensaver checker (NameHasOwner + follow_name_owner_changes)."""

    def __init__(self, session_bus=None, desktop: str | None = None) -> None:
        self._bus = session_bus
        self._desktop = (
            desktop
            if desktop is not None
            else (os.getenv("XDG_CURRENT_DESKTOP") or os.getenv("DESKTOP_SESSION") or "")
        )
        self._proxies: dict[str, Any] = {}

    def _get_bus(self):
        if self._bus is None:
            import dbus

            self._bus = dbus.SessionBus()
        return self._bus

    def is_active(self) -> bool:
        """Query the user's session screensaver without waking sleeping D-Bus daemons."""
        import dbus

        bus = self._get_bus()
        for backend in select_backends(self._desktop):
            try:
                if not bus.name_has_owner(backend.dbus_dest):
                    continue
            except dbus.DBusException:
                continue

            for obj_path in backend.dbus_paths:
                key = f"{backend.dbus_dest}:{obj_path}"
                for attempt in range(2):
                    try:
                        iface = self._proxies.get(key)
                        if iface is None:
                            proxy = bus.get_object(
                                backend.dbus_dest,
                                obj_path,
                                follow_name_owner_changes=True,
                            )
                            iface = dbus.Interface(proxy, backend.dbus_interface)
                            self._proxies[key] = iface
                        return bool(iface.GetActive())
                    except dbus.DBusException:
                        self._proxies.pop(key, None)
                        if attempt == 0:
                            continue
        return False


class SessionTelemetryProbe:
    """Safely probes X11 active window PID and idle seconds (graceful no-op on Wayland)."""

    def active_window_pid(self) -> int:
        if (os.getenv("XDG_SESSION_TYPE") or "").lower() == "wayland" or not os.getenv("DISPLAY"):
            return 0
        xprop = shutil.which("xprop", path=TRUSTED_BIN_PATH)
        if not xprop:
            return 0
        try:
            r1 = subprocess.run(  # nosec B603
                [xprop, "-root", "_NET_ACTIVE_WINDOW"],
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
                timeout=0.5,
                check=False,
            )
            if r1.returncode != 0:
                return 0
            m_win = _HEX_WIN_RE.search(r1.stdout or "")
            if not m_win or m_win.group(0) == "0x0":
                return 0
            r2 = subprocess.run(  # nosec B603
                [xprop, "-id", m_win.group(0), "_NET_WM_PID"],
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
                timeout=0.5,
                check=False,
            )
            if r2.returncode != 0:
                return 0
            m_pid = _PID_RE.search(r2.stdout or "")
            return int(m_pid.group(1)) if m_pid else 0
        except (OSError, subprocess.TimeoutExpired, ValueError):
            return 0

    def idle_seconds(self) -> int:
        # Never run xprintidle on Wayland (XWayland reports false idle while native apps are active)
        if (os.getenv("XDG_SESSION_TYPE") or "").lower() == "wayland" or not os.getenv("DISPLAY"):
            return 0
        xprintidle = shutil.which("xprintidle", path=TRUSTED_BIN_PATH)
        if not xprintidle:
            return 0
        try:
            res = subprocess.run(  # nosec B603
                [xprintidle],
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
                timeout=0.5,
                check=False,
            )
            if res.returncode == 0 and (res.stdout or "").strip().isdigit():
                return max(0, int(res.stdout.strip()) // 1000)
        except (OSError, subprocess.TimeoutExpired, ValueError):
            pass
        return 0


class DesktopNotifier:
    """Sends org.freedesktop.Notifications popups without requiring GTK Glade."""

    def __init__(self, session_bus=None) -> None:
        self._bus = session_bus
        self._iface: Any = None
        self._last_id = 0

    def send(self, summary: str, body: str, urgency: int = 1, icon: str = "curfewd") -> None:
        try:
            import dbus

            if self._bus is None:
                self._bus = dbus.SessionBus()
            if self._iface is None:
                proxy = self._bus.get_object(
                    "org.freedesktop.Notifications",
                    "/org/freedesktop/Notifications",
                    follow_name_owner_changes=True,
                )
                self._iface = dbus.Interface(proxy, "org.freedesktop.Notifications")
            hints = dbus.Dictionary({"urgency": dbus.Byte(max(0, min(2, urgency)))}, signature="sv")
            timeout_ms = 10000 if urgency >= 2 else 5000
            self._last_id = int(
                self._iface.Notify(
                    "curfewd",
                    dbus.UInt32(self._last_id),
                    icon,
                    summary,
                    body,
                    dbus.Array([], signature="s"),
                    hints,
                    dbus.Int32(timeout_ms),
                )
            )
        except Exception as exc:
            logger.debug("desktop notification skipped: %s", exc)


class TrayController:
    """Testable controller handling daemon auto-reconnect, status updates, HUD, chimes, and challenges."""

    def __init__(
        self,
        username: str,
        client: CurfewClient,
        screensaver: SessionScreensaverProbe,
        notifier: DesktopNotifier,
        ui_callback=None,
        telemetry_probe: SessionTelemetryProbe | None = None,
        chime: AudioChimePlayer | None = None,
        hud: CountdownHUD | None = None,
        idle_dispatch: Callable[..., Any] | None = None,
    ) -> None:
        self.username = username
        self.client = client
        self.screensaver = screensaver
        self.notifier = notifier
        self.ui_callback = ui_callback
        self.telemetry_probe = telemetry_probe or SessionTelemetryProbe()
        self.chime = chime or AudioChimePlayer()
        self.hud = hud
        self._idle_dispatch = idle_dispatch
        self._probe_lock = threading.Lock()
        self._probe_in_flight = False
        self.connected = False
        self.last_status: dict[str, Any] | None = None
        self.reconnect_count = 0

    def attach(self) -> None:
        """Subscribe to daemon signals and NameOwnerChanged auto-reconnect."""
        self.client.subscribe_signal("UserStatusChanged", self.on_status_changed)
        self.client.subscribe_signal("ClientNotification", self.on_client_notification)
        self.client.subscribe_signal("ScreensaverChallenge", self.on_screensaver_challenge)
        self.client.watch_daemon_owner(self.on_daemon_owner_changed)

    def on_daemon_owner_changed(self, new_owner: str) -> None:
        """Automatically re-sync when curfewd starts or restarts (e.g. during upgrades)."""
        if not new_owner:
            self.connected = False
            logger.info("curfewd daemon disconnected; waiting for reconnect")
            if self.hud:
                self.hud.hide()
            if self.ui_callback:
                self.ui_callback(None, connected=False)
            return

        logger.info("curfewd daemon available (%s); syncing status", new_owner)
        self.reconnect_count += 1
        self.sync_now()

    def sync_now(self) -> bool:
        try:
            status = self.client.get_my_status()
            self.connected = True
            self.last_status = status
            self._update_hud(status)
            if self.ui_callback:
                self.ui_callback(status, connected=True)
            return True
        except CurfewError as exc:
            self.connected = False
            logger.debug("sync_now failed: %s", exc)
            if self.ui_callback:
                self.ui_callback(None, connected=False)
            return False

    def _update_hud(self, status: dict[str, Any]) -> None:
        if self.hud is None:
            return
        prefs = status.get("client_prefs") or {}
        state = status.get("enforcement_state", "ok")
        if (
            not prefs.get("countdown_hud_enabled", True)
            or status.get("screen_locked")
            or state in ("paused", "unrestricted")
        ):
            self.hud.hide()
            return
        grace_avail = bool((status.get("save_grace") or {}).get("available", False))
        cont = int(status.get("time_left_continuous", 86400))
        if state in ("warning", "countdown") and 0 < cont <= 60:
            self.hud.show_countdown("Screen time", cont, save_grace_available=grace_avail)
            return
        al = status.get("app_limits_status") or {}
        if al.get("enabled", True):
            expiring: list[tuple[int, str]] = []
            for a in al.get("apps") or []:
                rem = int(a.get("time_left_today", 0))
                if a.get("running") and 0 < rem <= 60:
                    expiring.append((rem, str(a.get("label") or "App")))
            for c in al.get("categories") or []:
                rem = int(c.get("time_left_today", 0))
                if c.get("running") and 0 < rem <= 60:
                    expiring.append((rem, str(c.get("label") or c.get("category") or "Category")))
            if expiring:
                soonest_rem, soonest_lbl = min(expiring, key=lambda item: item[0])
                self.hud.show_countdown(soonest_lbl, soonest_rem, save_grace_available=False)
                return
        self.hud.hide()

    def on_status_changed(self, username: str, status_json: str) -> None:
        if str(username) != self.username:
            return
        try:
            status = json.loads(str(status_json))
        except ValueError:
            return
        self.connected = True
        self.last_status = status
        self._update_hud(status)
        if self.ui_callback:
            self.ui_callback(status, connected=True)

    def _send_challenge_reply(
        self, nonce: str, active: bool, idle_sec: int, active_window_pid: int
    ) -> bool:
        telemetry = {
            "screensaver_active": bool(active),
            "idle_sec": max(0, int(idle_sec)),
            "active_window_pid": max(0, int(active_window_pid)),
        }
        try:
            if hasattr(self.client, "report_client_telemetry"):
                self.client.report_client_telemetry(nonce, telemetry)
            else:
                self.client.report_screensaver_state(nonce, bool(active))
        except CurfewError:
            try:
                self.client.report_screensaver_state(nonce, bool(active))
            except CurfewError as exc:
                logger.debug("Screensaver challenge report failed: %s", exc)
        return False

    def on_screensaver_challenge(self, username: str, nonce: str) -> None:
        if str(username) != self.username:
            return
        # Query session D-Bus screensaver on the GLib main thread (never from worker threads)
        active = bool(self.screensaver.is_active())
        nonce_str = str(nonce)
        if self._idle_dispatch is None:
            idle_sec = self.telemetry_probe.idle_seconds()
            win_pid = self.telemetry_probe.active_window_pid()
            self._send_challenge_reply(nonce_str, active, idle_sec, win_pid)
            return

        with self._probe_lock:
            if self._probe_in_flight:
                # Don't pile up probe threads; still answer challenge promptly on main thread
                self._send_challenge_reply(nonce_str, active, 0, 0)
                return
            self._probe_in_flight = True

        dispatch = self._idle_dispatch

        def _probe_worker() -> None:
            idle_sec = 0
            win_pid = 0
            try:
                idle_sec = self.telemetry_probe.idle_seconds()
                win_pid = self.telemetry_probe.active_window_pid()
            except Exception as exc:
                logger.debug("Telemetry probe worker failed: %s", exc)
            finally:
                with self._probe_lock:
                    self._probe_in_flight = False
            dispatch(self._send_challenge_reply, nonce_str, active, idle_sec, win_pid)

        threading.Thread(target=_probe_worker, name="curfewd-tray-probe", daemon=True).start()

    def claim_save_grace(self) -> None:
        try:
            # Daemon emits ClientNotification(kind="save_grace_granted") on success; avoid duplicate popup
            self.client.claim_save_grace()
        except CurfewError as exc:
            self.notifier.send("Save grace unavailable", str(exc), urgency=1)

    def _notify_grant(self, payload: dict[str, Any], is_app: bool = False) -> None:
        applied = int(payload.get("applied_sec", 0))
        note = str(payload.get("note") or "").strip()
        note_sfx = f" — {note}" if note else ""
        if is_app:
            label = str(payload.get("label") or payload.get("pattern") or "App")
            if applied >= 0:
                self.notifier.send(
                    "Bonus app time added",
                    f"+{format_duration(applied)} added for {label} today{note_sfx}.",
                    urgency=1,
                )
            else:
                self.notifier.send(
                    "App time adjusted",
                    f"{format_duration(applied)} adjusted for {label} today.",
                    urgency=1,
                )
            return
        if applied >= 0:
            self.notifier.send(
                "Bonus screen time added",
                f"+{format_duration(applied)} added today{note_sfx}.",
                urgency=1,
            )
        else:
            self.notifier.send(
                "Screen time adjusted", f"{format_duration(applied)} adjusted today.", urgency=1
            )

    def on_client_notification(self, username: str, payload_json: str) -> None:
        if str(username) != self.username:
            return
        try:
            payload = json.loads(str(payload_json))
        except ValueError:
            return

        kind = payload.get("kind")
        if payload.get("chime"):
            self.chime.play(urgent=int(payload.get("seconds_left", 999)) <= 60)

        if kind == "warning":
            left = format_duration(int(payload.get("seconds_left", 0)))
            self.notifier.send(
                "Screen time remaining", f"{left} remaining before curfew/lock.", urgency=1
            )
        elif kind in ("app_warning", "category_warning"):
            label = str(payload.get("label") or payload.get("pattern") or "App")
            left = format_duration(int(payload.get("seconds_left", 0)))
            self.notifier.send(
                f"{label} time remaining",
                f"{left} left on {label} today — remember to save your progress!",
                urgency=1,
            )
        elif kind == "countdown":
            secs = int(payload.get("seconds_left", 0))
            action = payload.get("action", "lock")
            self.notifier.send(
                "Screen time is up",
                f"Session will {action} in {secs} seconds. Save your work!",
                urgency=2,
            )
        elif kind == "time_granted":
            self._notify_grant(payload, is_app=False)
        elif kind == "app_time_granted":
            self._notify_grant(payload, is_app=True)
        elif kind == "app_limit_reached":
            labels = ", ".join(str(x) for x in (payload.get("apps") or []) if x) or "Tracked app"
            reason = payload.get("reason")
            msg = (
                f"{labels} was closed during Homework Mode."
                if reason == "homework_mode"
                else f"{labels} reached its daily time limit and was closed."
            )
            self.notifier.send("App closed", msg, urgency=1)
        elif kind == "save_grace_granted":
            self.notifier.send(
                "2-minute save grace active",
                "Finish saving your work — screen will lock in 2 minutes.",
                urgency=1,
            )
        elif kind == "homework_mode_changed":
            if payload.get("active"):
                self.notifier.send(
                    "Homework Mode active",
                    "Daily screen-time countdown is paused while games & social apps are blocked.",
                    urgency=0,
                )
            else:
                self.notifier.send(
                    "Homework Mode ended", "Your regular schedule is active again.", urgency=0
                )
        elif kind == "time_request_resolved":
            lbl = str(payload.get("target_label") or "Screen time")
            if payload.get("approved"):
                dur = format_duration(int(payload.get("granted_sec", 0)))
                self.notifier.send("Time request approved", f"+{dur} granted for {lbl}!", urgency=1)
            else:
                self.notifier.send(
                    "Time request declined", f"Your request for more {lbl} was declined.", urgency=1
                )
        elif kind == "paused":
            self.notifier.send(
                "Screen time limits paused", "Limits have been temporarily paused.", urgency=0
            )
        elif kind == "resumed":
            self.notifier.send(
                "Screen time limits resumed", "Your regular schedule is active again.", urgency=1
            )
        elif kind == "policy_changed":
            self.notifier.send(
                "Schedule updated", "Your screen-time schedule was updated.", urgency=0
            )

    @staticmethod
    def select_icon_name(status: dict[str, Any] | None) -> str:
        if not status:
            return "curfewd"
        return _ICON_MAP.get(status.get("enforcement_state", "ok"), "curfewd")


def _reap_ui_proc(proc: subprocess.Popen[Any]) -> None:
    with suppress(OSError):
        proc.wait()


def _launch_my_screen_time(session_bus: bool = False, ask_time: bool = False) -> None:
    """Launch `curfewd-ui --my-status` without blocking the tray event loop."""
    ui_bin = shutil.which("curfewd-ui", path=TRUSTED_BIN_PATH)
    argv = (
        [ui_bin, "--my-status"] if ui_bin else [sys.executable, "-m", "curfewd.ui", "--my-status"]
    )
    if ask_time:
        argv.append("--ask-time")
    if session_bus:
        argv.append("--session-bus")
    try:
        proc = subprocess.Popen(  # nosec B603
            argv,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        threading.Thread(
            target=_reap_ui_proc, args=(proc,), name="curfewd-ui-reap", daemon=True
        ).start()
    except OSError as exc:
        logger.warning("Could not launch curfewd-ui: %s", exc)


def _try_build_indicator(controller: TrayController, session_bus: bool = False):
    """Build an AyatanaAppIndicator3 / AppIndicator3 tray icon if available."""
    try:
        import gi

        gi.require_version("Gtk", "3.0")
        from gi.repository import Gtk

        indicator_mod = None
        for mod_name in ("AyatanaAppIndicator3", "AppIndicator3"):
            try:
                gi.require_version(mod_name, "0.1")
                indicator_mod = __import__(f"gi.repository.{mod_name}", fromlist=[mod_name])
                break
            except (ImportError, ValueError):
                continue
        if indicator_mod is None:
            return None

        indicator = indicator_mod.Indicator.new(
            "curfewd-tray",
            "curfewd",
            indicator_mod.IndicatorCategory.APPLICATION_STATUS,
        )
        if os.path.isdir(ICONS_DIR):
            indicator.set_icon_theme_path(ICONS_DIR)
        indicator.set_status(indicator_mod.IndicatorStatus.ACTIVE)

        menu = Gtk.Menu()
        item_time = Gtk.MenuItem(label="Connecting to curfewd...")
        item_time.set_sensitive(False)
        item_curfew = Gtk.MenuItem(label="")
        item_curfew.set_sensitive(False)
        item_apps = Gtk.MenuItem(label="")
        item_apps.set_sensitive(False)
        item_open_ui = Gtk.MenuItem(label="Open My Screen Time")
        item_ask_time = Gtk.MenuItem(label="Ask for More Time...")
        item_grace = Gtk.MenuItem(label="Use 2-Min Save Grace")
        item_summary = Gtk.MenuItem(label="Show Status Notification")

        item_open_ui.connect("activate", lambda _w: _launch_my_screen_time(session_bus=session_bus))
        item_ask_time.connect(
            "activate", lambda _w: _launch_my_screen_time(session_bus=session_bus, ask_time=True)
        )
        item_grace.connect("activate", lambda _w: controller.claim_save_grace())

        def _show_summary(_widget):
            st = controller.last_status
            if not st:
                controller.notifier.send(
                    "curfewd", "Not currently connected to curfewd.", urgency=0
                )
                return
            state = st.get("enforcement_state", "ok")
            if state == "homework":
                left = "Homework Mode"
            elif state == "paused":
                left = "Paused"
            elif state == "unrestricted":
                left = "Unlimited"
            else:
                left = format_duration(st["time_left_today"])
            spent = format_duration(st["time_spent_today"])
            controller.notifier.send(
                "curfewd Status", f"Remaining: {left} | Spent today: {spent}", urgency=0
            )

        item_summary.connect("activate", _show_summary)
        for item in (
            item_time,
            item_curfew,
            item_apps,
            Gtk.SeparatorMenuItem(),
            item_open_ui,
            item_ask_time,
            item_grace,
            item_summary,
        ):
            menu.append(item)
        menu.show_all()
        item_apps.hide()
        item_grace.hide()
        indicator.set_menu(menu)

        def _update(status: dict[str, Any] | None, connected: bool = True) -> None:
            if not connected or status is None:
                indicator.set_status(indicator_mod.IndicatorStatus.ACTIVE)
                indicator.set_label("", "")
                item_time.set_label("Reconnecting to curfewd...")
                item_curfew.hide()
                item_apps.hide()
                item_grace.hide()
                return

            if status.get("hide_tray_effective"):
                indicator.set_status(indicator_mod.IndicatorStatus.PASSIVE)
                return

            indicator.set_status(indicator_mod.IndicatorStatus.ACTIVE)
            indicator.set_icon_full(controller.select_icon_name(status), "curfewd")
            state = status.get("enforcement_state", "ok")
            if state == "homework":
                indicator.set_label(" Study", " Study")
                item_time.set_label("Homework Mode active (timer paused)")
            elif state in ("unrestricted", "paused"):
                label_txt = "Paused" if state == "paused" else "Unlimited"
                indicator.set_label("", "")
                item_time.set_label(f"Screen time: {label_txt}")
            else:
                left = format_duration(status.get("time_left_continuous", 0))
                idle_tag = " (idle)" if status.get("idle_paused") else ""
                indicator.set_label(f" {left}", " 99h 59m")
                item_time.set_label(
                    f"Remaining today: {format_duration(status.get('time_left_today', 0))}{idle_tag}"
                )

            curfew_at = status.get("next_curfew_at")
            if curfew_at:
                item_curfew.set_label(f"Curfew at: {curfew_at[11:16]}")
                item_curfew.show()
            else:
                item_curfew.hide()

            running_apps = [
                a
                for a in ((status.get("app_limits_status") or {}).get("apps") or [])
                if a.get("running")
            ]
            if running_apps:
                summary = ", ".join(
                    f"{a['label']}: {format_duration(a.get('time_left_today', 0))}"
                    for a in running_apps[:2]
                )
                item_apps.set_label(summary)
                item_apps.show()
            else:
                item_apps.hide()

            grace = status.get("save_grace") or {}
            if grace.get("available") and state in ("warning", "countdown", "locked_out"):
                item_grace.show()
            else:
                item_grace.hide()

        return _update
    except Exception as exc:
        logger.info("AppIndicator unavailable (%s); running in notification-only mode", exc)
        return None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="curfewd-tray", description="curfewd desktop tray companion"
    )
    parser.add_argument(
        "--session-bus", action="store_true", help="Connect to session D-Bus for testing"
    )
    parser.add_argument("--version", action="version", version=f"curfewd-tray {__version__}")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    if not args.session_bus and not acquire_tray_lock():
        logger.info("curfewd-tray is already running for this session; exiting")
        return 0

    from dbus.mainloop.glib import DBusGMainLoop, threads_init

    threads_init()
    DBusGMainLoop(set_as_default=True)

    import dbus
    from gi.repository import GLib

    bus = dbus.SessionBus() if args.session_bus else dbus.SystemBus()
    client = CurfewClient(bus=bus)
    username = getpass.getuser()

    loop = GLib.MainLoop()
    controller_ref: list[TrayController] = []

    def _on_save_grace() -> None:
        if controller_ref:
            controller_ref[0].claim_save_grace()

    hud = CountdownHUD(
        on_save_grace=_on_save_grace,
        on_ask_more=lambda: _launch_my_screen_time(
            session_bus=bool(args.session_bus), ask_time=True
        ),
    )
    controller = TrayController(
        username=username,
        client=client,
        screensaver=SessionScreensaverProbe(),
        notifier=DesktopNotifier(),
        hud=hud,
        idle_dispatch=GLib.idle_add,
    )
    controller_ref.append(controller)
    ui_cb = _try_build_indicator(controller, session_bus=bool(args.session_bus))
    controller.ui_callback = ui_cb
    controller.attach()

    def _quit(*_args) -> bool:
        hud.hide()
        loop.quit()
        return False

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            GLib.unix_signal_add(GLib.PRIORITY_DEFAULT, sig, _quit)
        except AttributeError:
            signal.signal(sig, lambda _s, _f: _quit())

    loop.run()
    return 0
