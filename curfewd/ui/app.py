"""Native WebKitGTK desktop window host for curfewd-ui (GTK4/WebKit 6.0 with GTK3/WebKit2 4.1 fallback)."""

import argparse
import json
import logging
import os
import queue
import signal
import sys
import threading
from typing import Any

from curfewd import __version__
from curfewd.dbus.client import CurfewClient
from curfewd.ui.bridge import UIBridge

logger = logging.getLogger("curfewd.ui")

_ALLOWED_ASSETS: dict[str, str] = {
    "/": "index.html",
    "/index.html": "index.html",
    "/styles.css": "styles.css",
    "/app.js": "app.js",
}

_MIME_TYPES: dict[str, str] = {
    "index.html": "text/html; charset=utf-8",
    "styles.css": "text/css; charset=utf-8",
    "app.js": "application/javascript; charset=utf-8",
}


def resolve_web_dir() -> str:
    """Locate bundled offline UI assets (package dir or /usr/share/curfewd/ui)."""
    pkg_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "web")
    if os.path.isfile(os.path.join(pkg_dir, "index.html")):
        return pkg_dir
    sys_dir = "/usr/share/curfewd/ui"
    if os.path.isfile(os.path.join(sys_dir, "index.html")):
        return sys_dir
    raise FileNotFoundError("Could not locate curfewd-ui web assets (index.html)")


def _load_webkit_stack() -> tuple[int, Any, Any, Any, Any]:
    """Import GTK4 + WebKit 6.0 if available, otherwise fall back to GTK3 + WebKit2 4.1/4.0."""
    import gi

    # Prefer GTK4 + WebKit 6.0
    try:
        gi.require_version("Gtk", "4.0")
        gi.require_version("WebKit", "6.0")
        from gi.repository import Gio, GLib, Gtk, WebKit

        return 4, Gtk, WebKit, GLib, Gio
    except (ImportError, ValueError):
        pass

    # Fallback: GTK3 + WebKit2 4.1 (Ubuntu 24.04 / Linux Mint 22) or 4.0 (older distros)
    for wk_ver in ("4.1", "4.0"):
        try:
            gi.require_version("Gtk", "3.0")
            gi.require_version("WebKit2", wk_ver)
            from gi.repository import Gio, GLib, Gtk, WebKit2

            return 3, Gtk, WebKit2, GLib, Gio
        except (ImportError, ValueError):
            continue

    raise RuntimeError(
        "WebKitGTK Python bindings not found. Install `gir1.2-webkit-6.0` (GTK4) "
        "or `gir1.2-webkit2-4.1` (GTK3): sudo apt install gir1.2-webkit2-4.1"
    )


class _RPCWorker:
    """Executes D-Bus bridge calls on a private worker thread so Polkit dialogs never freeze GTK."""

    def __init__(self, session_bus: bool, on_response) -> None:
        self._session_bus = session_bus
        self._on_response = on_response
        self._queue: queue.Queue[tuple[str, str, dict[str, Any]] | None] = queue.Queue()
        self._thread = threading.Thread(target=self._run, name="curfewd-ui-rpc", daemon=True)
        self._thread.start()

    def submit(self, req_id: str, action: str, params: dict[str, Any]) -> None:
        self._queue.put((req_id, action, params))

    def stop(self) -> None:
        self._queue.put(None)

    def _run(self) -> None:
        import dbus

        bus = dbus.SessionBus(private=True) if self._session_bus else dbus.SystemBus(private=True)
        bridge = UIBridge(client=CurfewClient(bus=bus, follow_name_owner=False))
        try:
            while True:
                item = self._queue.get()
                if item is None:
                    break
                req_id, action, params = item
                result = bridge.handle(action, params)
                self._on_response(req_id, result)
        finally:
            try:
                bus.close()
            except Exception:  # nosec B110 - best-effort private bus cleanup on shutdown
                pass


def _build_uri_scheme_handler(web_dir: str, Gio, GLib):
    """Serve strictly allowlisted static files from `curfewd://app/...`."""

    def _on_request(request) -> None:
        uri = str(request.get_uri() or "")
        # Strip scheme + authority (`curfewd://app/index.html?mode=admin` -> `/index.html`)
        path = uri.split("?", 1)[0].split("#", 1)[0]
        if path.startswith("curfewd://"):
            rest = path[len("curfewd://"):]
            slash = rest.find("/")
            path = rest[slash:] if slash != -1 else "/"

        filename = _ALLOWED_ASSETS.get(path)
        if not filename:
            request.finish_error(GLib.Error.new_literal(Gio.io_error_quark(), "Not found", 1))
            return

        full_path = os.path.join(web_dir, filename)
        try:
            with open(full_path, "rb") as fh:
                data = fh.read()
        except OSError as exc:
            request.finish_error(GLib.Error.new_literal(Gio.io_error_quark(), str(exc), 1))
            return

        stream = Gio.MemoryInputStream.new_from_bytes(GLib.Bytes.new(data))
        request.finish(stream, len(data), _MIME_TYPES.get(filename, "application/octet-stream"))

    return _on_request


def _eval_js(webview, gtk_major: int, code: str) -> None:
    if gtk_major == 4 or hasattr(webview, "evaluate_javascript"):
        try:
            webview.evaluate_javascript(code, -1, None, None, None, None, None)
            return
        except TypeError:
            pass
    if hasattr(webview, "run_javascript"):
        webview.run_javascript(code, None, None, None)


def _extract_js_message(gtk_major: int, payload) -> str:
    if gtk_major == 4:
        return str(payload.to_string())
    if hasattr(payload, "get_js_value"):
        return str(payload.get_js_value().to_string())
    return str(payload)


def _configure_webview(gtk_major: int, WebKit, web_dir: str, Gio, GLib, on_rpc_message):
    context = WebKit.WebContext.get_default()
    context.register_uri_scheme("curfewd", _build_uri_scheme_handler(web_dir, Gio, GLib))

    ucm = WebKit.UserContentManager()
    if gtk_major == 4:
        ucm.register_script_message_handler("curfewd", None)
    else:
        ucm.register_script_message_handler("curfewd")

    def _on_script_msg(_manager, payload) -> None:
        raw = _extract_js_message(gtk_major, payload)
        on_rpc_message(raw)

    ucm.connect("script-message-received::curfewd", _on_script_msg)

    if gtk_major == 4:
        webview = WebKit.WebView(user_content_manager=ucm)
    else:
        webview = WebKit.WebView.new_with_user_content_manager(ucm)

    settings = webview.get_settings()
    settings.set_enable_developer_extras(False)
    settings.set_javascript_can_open_windows_automatically(False)
    if hasattr(settings, "set_allow_file_access_from_file_urls"):
        settings.set_allow_file_access_from_file_urls(False)
    if hasattr(settings, "set_allow_universal_access_from_file_urls"):
        settings.set_allow_universal_access_from_file_urls(False)

    # Suppress browser context menu for clean native app feel
    webview.connect("context-menu", lambda *_args: True)

    # Block navigation outside our offline curfewd:// origin
    def _decide_policy(_wv, decision, decision_type) -> bool:
        nav_types = (
            WebKit.PolicyDecisionType.NAVIGATION_ACTION,
            WebKit.PolicyDecisionType.NEW_WINDOW_ACTION,
        )
        if decision_type in nav_types:
            req = decision.get_navigation_action().get_request()
            uri = str(req.get_uri() or "")
            if not uri.startswith("curfewd://"):
                decision.ignore()
                return True
        return False

    webview.connect("decide-policy", _decide_policy)
    return webview


def _attach_live_signals(session_bus: bool, GLib, push_event) -> None:
    """Subscribe to org.curfewd.Daemon1.Events on the GLib main loop for live updates."""
    try:
        from dbus.mainloop.glib import DBusGMainLoop
        import dbus

        DBusGMainLoop(set_as_default=True)
        bus = dbus.SessionBus() if session_bus else dbus.SystemBus()
        client = CurfewClient(bus=bus)

        def _on_status(username: str, status_json: str) -> None:
            try:
                status = json.loads(str(status_json))
            except ValueError:
                return
            GLib.idle_add(push_event, "user_status", {"username": str(username), "status": status})

        client.subscribe_signal("UserStatusChanged", _on_status)
    except Exception as exc:
        logger.debug("Live D-Bus signal subscription unavailable: %s", exc)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="curfewd-ui", description="curfewd native Material Design 3 desktop UI")
    parser.add_argument("--my-status", action="store_true", help="Open directly in passwordless My Screen Time view")
    parser.add_argument("--session-bus", action="store_true", help="Connect to session D-Bus (development/testing)")
    parser.add_argument("--version", action="version", version=f"curfewd-ui {__version__}")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    try:
        web_dir = resolve_web_dir()
        gtk_major, Gtk, WebKit, GLib, Gio = _load_webkit_stack()
    except (FileNotFoundError, RuntimeError) as exc:
        sys.stderr.write(f"curfewd-ui: {exc}\n")
        return 1

    mode = "my_status" if args.my_status else "admin"
    title = "curfewd — My Screen Time" if args.my_status else "curfewd — Screen Time Control"
    width, height = (600, 760) if args.my_status else (1120, 760)
    start_uri = f"curfewd://app/index.html?mode={mode}"

    webview_ref: dict[str, Any] = {"view": None}

    def _deliver_response(req_id: str, payload: dict[str, Any]) -> None:
        js = f"window.__curfewdResolve && window.__curfewdResolve({json.dumps(req_id)}, {json.dumps(payload)});"

        def _run_on_main() -> bool:
            wv = webview_ref.get("view")
            if wv is not None:
                _eval_js(wv, gtk_major, js)
            return False

        GLib.idle_add(_run_on_main)

    def _push_event(event_name: str, data: dict[str, Any]) -> bool:
        wv = webview_ref.get("view")
        if wv is not None:
            js = f"window.__curfewdEvent && window.__curfewdEvent({json.dumps(event_name)}, {json.dumps(data)});"
            _eval_js(wv, gtk_major, js)
        return False

    worker = _RPCWorker(session_bus=bool(args.session_bus), on_response=_deliver_response)

    def _on_rpc_message(raw: str) -> None:
        try:
            msg = json.loads(raw)
            req_id = str(msg["id"])
            action = str(msg["action"])
            params = msg.get("params") or {}
        except (ValueError, KeyError, TypeError):
            return
        worker.submit(req_id, action, params)

    _attach_live_signals(bool(args.session_bus), GLib, _push_event)

    if gtk_major == 4:
        app_id = "org.curfewd.curfewd.mystatus" if args.my_status else "org.curfewd.curfewd"
        app = Gtk.Application(application_id=app_id)

        def _on_activate(gtk_app) -> None:
            win = Gtk.ApplicationWindow(application=gtk_app)
            win.set_title(title)
            win.set_default_size(width, height)
            win.set_icon_name("curfewd")
            wv = _configure_webview(4, WebKit, web_dir, Gio, GLib, _on_rpc_message)
            webview_ref["view"] = wv
            win.set_child(wv)
            wv.load_uri(start_uri)
            win.present()

        app.connect("activate", _on_activate)
        signal.signal(signal.SIGINT, lambda *_a: app.quit())
        try:
            return int(app.run([]))
        finally:
            worker.stop()

    # GTK3 + WebKit2 4.1 / 4.0 host
    win = Gtk.Window(title=title)
    win.set_default_size(width, height)
    win.set_icon_name("curfewd")
    wv = _configure_webview(3, WebKit, web_dir, Gio, GLib, _on_rpc_message)
    webview_ref["view"] = wv
    win.add(wv)
    win.connect("destroy", Gtk.main_quit)
    signal.signal(signal.SIGINT, lambda *_a: Gtk.main_quit())
    wv.load_uri(start_uri)
    win.show_all()
    try:
        Gtk.main()
    finally:
        worker.stop()
    return 0
