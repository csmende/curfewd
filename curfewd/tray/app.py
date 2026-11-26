"""Lean, Glade-free, auto-reconnecting desktop tray & notification client (`curfewd-tray`)."""

import argparse
import getpass
import json
import logging
import os
import shutil
import signal
# Subprocess is strictly limited to launching the trusted curfewd-ui binary (shell=False).
import subprocess  # nosec B404
import sys
from typing import Any

from curfewd import __version__
from curfewd.dbus.client import CurfewClient, CurfewError
from curfewd.engine.screenlock import TRUSTED_BIN_PATH, select_backends
from curfewd.parsing import format_duration

logger = logging.getLogger("curfewd.tray")

ICONS_DIR = "/usr/share/curfewd/icons"
_ICON_MAP = {
    "ok": "timekpr-padlock-limited-green",
    "warning": "timekpr-padlock-limited-yellow",
    "countdown": "timekpr-padlock-limited-red",
    "locked_out": "timekpr-padlock-limited-red",
    "paused": "timekpr-padlock-unlimited-green",
    "unrestricted": "timekpr-padlock-unlimited-green",
}


class SessionScreensaverProbe:
    """Resilient session D-Bus screensaver checker (NameHasOwner + follow_name_owner_changes)."""

    def __init__(self, session_bus=None, desktop: str | None = None) -> None:
        self._bus = session_bus
        self._desktop = desktop if desktop is not None else (
            os.getenv("XDG_CURRENT_DESKTOP") or os.getenv("DESKTOP_SESSION") or ""
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
                        if bool(iface.GetActive()):
                            return True
                        return False
                    except dbus.DBusException:
                        self._proxies.pop(key, None)
                        if attempt == 0:
                            continue
        return False


class DesktopNotifier:
    """Sends org.freedesktop.Notifications popups without requiring GTK Glade."""

    def __init__(self, session_bus=None) -> None:
        self._bus = session_bus
        self._iface = None
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
    """Testable controller handling daemon auto-reconnect, status updates, and challenges."""

    def __init__(
        self,
        username: str,
        client: CurfewClient,
        screensaver: SessionScreensaverProbe,
        notifier: DesktopNotifier,
        ui_callback=None,
    ) -> None:
        self.username = username
        self.client = client
        self.screensaver = screensaver
        self.notifier = notifier
        self.ui_callback = ui_callback
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
            if self.ui_callback:
                self.ui_callback(status, connected=True)
            return True
        except CurfewError as exc:
            self.connected = False
            logger.debug("sync_now failed: %s", exc)
            if self.ui_callback:
                self.ui_callback(None, connected=False)
            return False

    def on_status_changed(self, username: str, status_json: str) -> None:
        if str(username) != self.username:
            return
        try:
            status = json.loads(str(status_json))
        except ValueError:
            return
        self.connected = True
        self.last_status = status
        if self.ui_callback:
            self.ui_callback(status, connected=True)

    def on_screensaver_challenge(self, username: str, nonce: str) -> None:
        if str(username) != self.username:
            return
        active = self.screensaver.is_active()
        try:
            self.client.report_screensaver_state(str(nonce), active)
        except CurfewError as exc:
            logger.debug("ReportScreensaverState failed: %s", exc)

    def on_client_notification(self, username: str, payload_json: str) -> None:
        if str(username) != self.username:
            return
        try:
            payload = json.loads(str(payload_json))
        except ValueError:
            return

        kind = payload.get("kind")
        if kind == "warning":
            left = format_duration(int(payload.get("seconds_left", 0)))
            self.notifier.send("Screen time remaining", f"{left} remaining before curfew/lock.", urgency=1)
        elif kind == "countdown":
            secs = int(payload.get("seconds_left", 0))
            action = payload.get("action", "lock")
            self.notifier.send(
                "Screen time is up",
                f"Session will {action} in {secs} seconds. Save your work!",
                urgency=2,
            )
        elif kind == "time_granted":
            applied = int(payload.get("applied_sec", 0))
            if applied >= 0:
                self.notifier.send("Bonus screen time added", f"+{format_duration(applied)} added today.", urgency=1)
            else:
                self.notifier.send("Screen time adjusted", f"{format_duration(applied)} adjusted today.", urgency=1)
        elif kind == "app_time_granted":
            applied = int(payload.get("applied_sec", 0))
            label = str(payload.get("label") or payload.get("pattern") or "App")
            if applied >= 0:
                self.notifier.send("Bonus app time added", f"+{format_duration(applied)} added for {label} today.", urgency=1)
            else:
                self.notifier.send("App time adjusted", f"{format_duration(applied)} adjusted for {label} today.", urgency=1)
        elif kind == "app_limit_reached":
            labels = ", ".join(str(x) for x in (payload.get("apps") or []) if x) or "Tracked app"
            self.notifier.send("App time limit reached", f"{labels} reached its daily time limit and was closed.", urgency=1)
        elif kind == "paused":
            self.notifier.send("Screen time limits paused", "Limits have been temporarily paused.", urgency=0)
        elif kind == "resumed":
            self.notifier.send("Screen time limits resumed", "Your regular schedule is active again.", urgency=1)
        elif kind == "policy_changed":
            self.notifier.send("Schedule updated", "Your screen-time schedule was updated.", urgency=0)

    @staticmethod
    def select_icon_name(status: dict[str, Any] | None) -> str:
        if not status:
            return "curfewd"
        return _ICON_MAP.get(status.get("enforcement_state", "ok"), "curfewd")


def _launch_my_screen_time(session_bus: bool = False) -> None:
    """Launch `curfewd-ui --my-status` without blocking the tray event loop."""
    ui_bin = shutil.which("curfewd-ui", path=TRUSTED_BIN_PATH)
    argv = [ui_bin, "--my-status"] if ui_bin else [sys.executable, "-m", "curfewd.ui", "--my-status"]
    if session_bus:
        argv.append("--session-bus")
    try:
        subprocess.Popen(  # nosec B603
            argv,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
    except OSError as exc:
        logger.warning("Could not launch curfewd-ui: %s", exc)


def _try_build_indicator(controller: TrayController, quit_fn, session_bus: bool = False):
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
        item_open_ui = Gtk.MenuItem(label="Open My Screen Time")
        item_summary = Gtk.MenuItem(label="Show Status Notification")

        def _open_ui(_widget):
            _launch_my_screen_time(session_bus=session_bus)

        def _show_summary(_widget):
            st = controller.last_status
            if not st:
                controller.notifier.send("curfewd", "Not currently connected to curfewd.", urgency=0)
                return
            left = (
                "Unlimited"
                if st["enforcement_state"] in ("unrestricted", "paused")
                else format_duration(st["time_left_today"])
            )
            spent = format_duration(st["time_spent_today"])
            controller.notifier.send("curfewd Status", f"Remaining: {left} | Spent today: {spent}", urgency=0)

        item_open_ui.connect("activate", _open_ui)
        item_summary.connect("activate", _show_summary)
        menu.append(item_time)
        menu.append(item_curfew)
        menu.append(Gtk.SeparatorMenuItem())
        menu.append(item_open_ui)
        menu.append(item_summary)
        menu.show_all()
        indicator.set_menu(menu)

        def _update(status: dict[str, Any] | None, connected: bool = True) -> None:
            if not connected or status is None:
                indicator.set_status(indicator_mod.IndicatorStatus.ACTIVE)
                indicator.set_label("", "")
                item_time.set_label("Reconnecting to curfewd...")
                item_curfew.hide()
                return

            if status.get("hide_tray_effective"):
                indicator.set_status(indicator_mod.IndicatorStatus.PASSIVE)
                return

            indicator.set_status(indicator_mod.IndicatorStatus.ACTIVE)
            indicator.set_icon_full(controller.select_icon_name(status), "curfewd")
            state = status.get("enforcement_state", "ok")
            if state in ("unrestricted", "paused"):
                label_txt = "Paused" if state == "paused" else "Unlimited"
                indicator.set_label("", "")
                item_time.set_label(f"Screen time: {label_txt}")
            else:
                left = format_duration(status.get("time_left_continuous", 0))
                indicator.set_label(f" {left}", " 99h 59m")
                item_time.set_label(f"Remaining today: {format_duration(status.get('time_left_today', 0))}")

            curfew_at = status.get("next_curfew_at")
            if curfew_at:
                item_curfew.set_label(f"Curfew at: {curfew_at[11:16]}")
                item_curfew.show()
            else:
                item_curfew.hide()

        return _update
    except Exception as exc:
        logger.info("AppIndicator unavailable (%s); running in notification-only mode", exc)
        return None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="curfewd-tray", description="curfewd desktop tray companion")
    parser.add_argument("--session-bus", action="store_true", help="Connect to session D-Bus for testing")
    parser.add_argument("--version", action="version", version=f"curfewd-tray {__version__}")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    from dbus.mainloop.glib import DBusGMainLoop

    DBusGMainLoop(set_as_default=True)

    import dbus
    from gi.repository import GLib

    bus = dbus.SessionBus() if args.session_bus else dbus.SystemBus()
    client = CurfewClient(bus=bus)
    username = getpass.getuser()

    loop = GLib.MainLoop()
    controller = TrayController(
        username=username,
        client=client,
        screensaver=SessionScreensaverProbe(),
        notifier=DesktopNotifier(),
    )
    ui_cb = _try_build_indicator(controller, loop.quit, session_bus=bool(args.session_bus))
    controller.ui_callback = ui_cb
    controller.attach()

    def _quit(*_args) -> bool:
        loop.quit()
        return False

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            GLib.unix_signal_add(GLib.PRIORITY_DEFAULT, sig, _quit)
        except AttributeError:
            signal.signal(sig, lambda _s, _f: _quit())

    loop.run()
    return 0
