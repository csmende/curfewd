"""Fullscreen-aware OSD banner and X11 fullscreen detection for `curfewd-tray` (Spec 007)."""

import logging
import os
import re
import shutil

# Subprocess is strictly limited to trusted-PATH, list-argv X11 inspection (shell=False).
import subprocess  # nosec B404
from collections.abc import Callable, Mapping
from typing import Any

from curfewd.engine.screenlock import TRUSTED_BIN_PATH

logger = logging.getLogger("curfewd.tray.osd")

_ACTIVE_WIN_RE = re.compile(r"0x[0-9a-fA-F]+")

_ACCENT_COLORS = {
    "info": "#64b5f6",
    "success": "#81c784",
    "warning": "#ffb74d",
    "critical": "#e57373",
}


def _default_run(argv: list[str], timeout: float) -> subprocess.CompletedProcess[str] | None:
    try:
        return subprocess.run(  # nosec B603
            argv,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None


def is_fullscreen_window_active(
    env: Mapping[str, str] | None = None,
    which_fn: Callable[[str], str | None] | None = None,
    run_fn: Callable[[list[str], float], Any] | None = None,
) -> bool:
    """Detect whether the currently focused desktop window is fullscreen."""
    environ = os.environ if env is None else env
    session_type = str(environ.get("XDG_SESSION_TYPE", "")).strip().lower()
    display = str(environ.get("DISPLAY", "")).strip()

    if session_type == "wayland" or not display:
        return False

    which = which_fn or (lambda cmd: shutil.which(cmd, path=TRUSTED_BIN_PATH))
    runner = run_fn or _default_run

    xprop_bin = which("xprop")
    if xprop_bin:
        res_active = runner([xprop_bin, "-root", "_NET_ACTIVE_WINDOW"], 0.5)
        if res_active is not None and getattr(res_active, "returncode", 1) == 0:
            match = _ACTIVE_WIN_RE.search(getattr(res_active, "stdout", "") or "")
            if match:
                win_id = match.group(0)
                if win_id.lower() != "0x0":
                    res_state = runner([xprop_bin, "-id", win_id, "_NET_WM_STATE"], 0.5)
                    if res_state is not None and getattr(res_state, "returncode", 1) == 0:
                        stdout = getattr(res_state, "stdout", "") or ""
                        return "_NET_WM_STATE_FULLSCREEN" in stdout

    try:
        import gi

        gi.require_version("Gdk", "3.0")
        from gi.repository import Gdk

        screen = Gdk.Screen.get_default()
        if screen is not None:
            active_win = screen.get_active_window()
            if active_win is not None:
                return bool(active_win.get_state() & Gdk.WindowState.FULLSCREEN)
    except Exception:
        return False

    return False


class FullscreenOsdBanner:
    """Top-most non-focus-stealing GTK3 X11 override-redirect OSD banner."""

    def __init__(self) -> None:
        self._window: Any = None
        self._timeout_id: int | None = None
        self._css_loaded = False

    def dismiss(self) -> bool:
        """Close any currently displayed OSD banner."""
        self._timeout_id = None
        if self._window is not None:
            try:
                self._window.destroy()
            except Exception as exc:
                logger.debug("OSD dismiss failed: %s", exc)
            self._window = None
        return False

    def show(
        self,
        summary: str,
        body: str,
        duration_ms: int = 5000,
        accent: str = "warning",
    ) -> bool:
        """Display an override-redirect top-center OSD banner above fullscreen windows."""
        try:
            import gi

            gi.require_version("Gtk", "3.0")
            gi.require_version("Gdk", "3.0")
            from gi.repository import Gdk, GLib, Gtk

            if Gdk.Display.get_default() is None:
                return False

            if self._timeout_id is not None:
                GLib.source_remove(self._timeout_id)
                self._timeout_id = None
            if self._window is not None:
                self._window.destroy()
                self._window = None

            accent_hex = _ACCENT_COLORS.get(accent, _ACCENT_COLORS["warning"])
            if not self._css_loaded:
                provider = Gtk.CssProvider()
                css = (
                    b".curfewd-osd-box {"
                    b"  background-color: rgba(20, 22, 28, 0.94);"
                    b"  border-radius: 12px;"
                    b"  padding: 12px 18px;"
                    b"  border: 2px solid rgba(255, 255, 255, 0.16);"
                    b"}"
                    b".curfewd-osd-title {"
                    b"  color: #ffffff;"
                    b"  font-weight: 700;"
                    b"  font-size: 13pt;"
                    b"}"
                    b".curfewd-osd-body {"
                    b"  color: #e0e4ec;"
                    b"  font-size: 10.5pt;"
                    b"}"
                )
                provider.load_from_data(css)
                screen = Gdk.Screen.get_default()
                if screen is not None:
                    Gtk.StyleContext.add_provider_for_screen(
                        screen,
                        provider,
                        Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION,
                    )
                self._css_loaded = True

            win = Gtk.Window(type=Gtk.WindowType.POPUP)
            win.set_decorated(False)
            win.set_skip_taskbar_hint(True)
            win.set_skip_pager_hint(True)
            win.set_accept_focus(False)
            win.set_focus_on_map(False)
            win.set_keep_above(True)
            win.stick()

            outer = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
            outer.get_style_context().add_class("curfewd-osd-box")

            bar = Gtk.DrawingArea()
            bar.set_size_request(5, 36)

            def _draw_bar(_widget, cr):
                rgba = Gdk.RGBA()
                rgba.parse(accent_hex)
                cr.set_source_rgba(rgba.red, rgba.green, rgba.blue, rgba.alpha)
                cr.paint()
                return False

            bar.connect("draw", _draw_bar)
            outer.pack_start(bar, False, False, 0)

            text_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=3)
            lbl_title = Gtk.Label(label=summary)
            lbl_title.set_xalign(0.0)
            lbl_title.get_style_context().add_class("curfewd-osd-title")
            lbl_body = Gtk.Label(label=body)
            lbl_body.set_xalign(0.0)
            lbl_body.set_line_wrap(True)
            lbl_body.set_max_width_chars(48)
            lbl_body.get_style_context().add_class("curfewd-osd-body")
            text_box.pack_start(lbl_title, False, False, 0)
            text_box.pack_start(lbl_body, False, False, 0)
            outer.pack_start(text_box, True, True, 0)

            win.add(outer)
            win.show_all()

            display = Gdk.Display.get_default()
            monitor = display.get_primary_monitor() if display is not None else None
            if monitor is not None:
                geom = monitor.get_geometry()
                nat_w = win.get_preferred_width()[1]
                x_pos = geom.x + max(16, (geom.width - nat_w) // 2)
                y_pos = geom.y + 28
                win.move(x_pos, y_pos)

            gdk_win = win.get_window()
            if gdk_win is not None:
                gdk_win.set_override_redirect(True)
                gdk_win.raise_()

            self._window = win
            self._timeout_id = int(GLib.timeout_add(max(1000, int(duration_ms)), self.dismiss))
            return True
        except Exception as exc:
            logger.debug("fullscreen OSD skipped: %s", exc)
            return False
