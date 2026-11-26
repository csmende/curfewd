"""Optional soft audio chime and non-focus-stealing corner Countdown HUD for curfewd-tray (Spec 006)."""

import logging
import os
import shutil

# Subprocess is strictly limited to trusted system audio utilities (shell=False).
import subprocess  # nosec B404
import threading
import time
from collections.abc import Callable
from contextlib import suppress
from typing import Any

from curfewd.engine.screenlock import TRUSTED_BIN_PATH

logger = logging.getLogger("curfewd.tray.hud")

_SOUND_CANDIDATES = (
    "/usr/share/sounds/freedesktop/stereo/message.oga",
    "/usr/share/sounds/freedesktop/stereo/bell.oga",
    "/usr/share/sounds/freedesktop/stereo/dialog-information.oga",
)


def _reap_subprocess(proc: subprocess.Popen[Any], timeout_sec: float = 10.0) -> None:
    """Wait on a detached helper process so it never lingers as a <defunct> zombie."""
    try:
        proc.wait(timeout=timeout_sec)
    except subprocess.TimeoutExpired:
        with suppress(OSError):
            proc.kill()
        with suppress(OSError):
            proc.wait(timeout=2.0)
    except OSError:
        pass


class AudioChimePlayer:
    """Plays a gentle, non-blocking audio chime so fullscreen games don't hide 5m/1m warnings."""

    def __init__(self, min_interval_sec: float = 10.0) -> None:
        self._min_interval_sec = max(1.0, float(min_interval_sec))
        self._last_played_mono: float = 0.0

    def play(self, urgent: bool = False) -> bool:
        now = time.monotonic()
        if (now - self._last_played_mono) < self._min_interval_sec:
            return False

        argv = self._select_command(urgent=urgent)
        if not argv:
            return False

        try:
            proc = subprocess.Popen(  # nosec B603
                argv,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
            threading.Thread(
                target=_reap_subprocess,
                args=(proc, 10.0),
                name="curfewd-chime-reap",
                daemon=True,
            ).start()
            self._last_played_mono = now
            return True
        except OSError as exc:
            logger.debug("audio chime skipped: %s", exc)
            return False

    @staticmethod
    def _select_command(urgent: bool = False) -> list[str] | None:
        canberra = shutil.which("canberra-gtk-play", path=TRUSTED_BIN_PATH)
        if canberra:
            event_id = "bell" if urgent else "message"
            return [canberra, "-i", event_id]
        paplay = shutil.which("paplay", path=TRUSTED_BIN_PATH)
        if paplay:
            for sound_path in _SOUND_CANDIDATES:
                if os.path.isfile(sound_path):
                    return [paplay, sound_path]
        return None


class CountdownHUD:
    """Small, non-focus-stealing top-right Material 3 countdown pill shown in the final 60 seconds."""

    def __init__(
        self,
        on_save_grace: Callable[[], None] | None = None,
        on_ask_more: Callable[[], None] | None = None,
    ) -> None:
        self._on_save_grace = on_save_grace
        self._on_ask_more = on_ask_more
        self._win: Any = None
        self._lbl: Any = None
        self._btn_grace: Any = None
        self._gtk: Any = None
        self._available = False
        self._initialized = False

    def _ensure_ui(self) -> bool:
        if self._initialized:
            return self._available
        self._initialized = True
        try:
            import gi

            gi.require_version("Gtk", "3.0")
            from gi.repository import Gdk, Gtk

            if Gdk.Display.get_default() is None:
                return False

            win = Gtk.Window(type=Gtk.WindowType.TOPLEVEL)
            win.set_type_hint(Gdk.WindowTypeHint.NOTIFICATION)
            win.set_decorated(False)
            win.set_keep_above(True)
            win.set_accept_focus(False)
            win.set_skip_taskbar_hint(True)
            win.set_skip_pager_hint(True)
            win.set_resizable(False)
            win.set_border_width(8)

            css = Gtk.CssProvider()
            css.load_from_data(
                b"""
                window {
                    background-color: rgba(21, 24, 34, 0.94);
                    color: #e5e8f3;
                    border: 1px solid #3e455d;
                    border-radius: 16px;
                }
                label { color: #ffdad5; font-weight: 700; font-size: 13px; }
                button {
                    background: #253a80;
                    color: #dce4ff;
                    border-radius: 999px;
                    padding: 3px 10px;
                    font-size: 11px;
                    font-weight: 600;
                    border: none;
                }
                """
            )
            ctx = win.get_style_context()
            ctx.add_provider(css, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)

            box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
            lbl = Gtk.Label(label="")
            lbl.get_style_context().add_provider(css, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
            box.pack_start(lbl, False, False, 4)

            btn_grace = Gtk.Button(label="+2m Save")
            btn_grace.get_style_context().add_provider(css, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
            on_grace = self._on_save_grace
            if on_grace is not None:
                btn_grace.connect("clicked", lambda _w, cb=on_grace: cb())
            box.pack_start(btn_grace, False, False, 0)

            btn_ask = Gtk.Button(label="Ask Parent")
            btn_ask.get_style_context().add_provider(css, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
            on_ask = self._on_ask_more
            if on_ask is not None:
                btn_ask.connect("clicked", lambda _w, cb=on_ask: cb())
            box.pack_start(btn_ask, False, False, 0)

            win.add(box)
            box.show_all()
            self._win = win
            self._lbl = lbl
            self._btn_grace = btn_grace
            self._gtk = Gtk
            self._available = True
            return True
        except Exception as exc:
            logger.debug("CountdownHUD unavailable: %s", exc)
            return False

    def show_countdown(
        self,
        title: str,
        seconds_left: int,
        save_grace_available: bool = False,
    ) -> None:
        if not self._ensure_ui() or self._win is None:
            return
        mins, secs = divmod(max(0, int(seconds_left)), 60)
        self._lbl.set_text(f"{title}: {mins}:{secs:02d} left")
        if save_grace_available:
            self._btn_grace.show()
        else:
            self._btn_grace.hide()
        with suppress(Exception):
            from gi.repository import Gdk

            display = Gdk.Display.get_default()
            if display is not None:
                mon = display.get_primary_monitor() or display.get_monitor(0)
                if mon is not None:
                    geo = mon.get_geometry()
                    w, _ = self._win.get_size()
                    self._win.move(geo.x + max(16, geo.width - max(w, 290) - 20), geo.y + 44)
        self._win.show()

    def hide(self) -> None:
        if self._win is not None:
            with suppress(Exception):
                self._win.hide()
