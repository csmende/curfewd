"""Unit tests for curfewd.tray.osd and fullscreen/lock-screen notifications (Spec 007)."""

import subprocess
import unittest

from curfewd.tray.app import DesktopNotifier, TrayController
from curfewd.tray.osd import is_fullscreen_window_active


class _StubOsdBanner:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, int, str]] = []
        self.dismiss_calls = 0

    def show(
        self,
        summary: str,
        body: str,
        duration_ms: int = 5000,
        accent: str = "warning",
    ) -> bool:
        self.calls.append((summary, body, duration_ms, accent))
        return True

    def dismiss(self) -> bool:
        self.dismiss_calls += 1
        return False


class _StubChime:
    def __init__(self) -> None:
        self.calls: list[bool] = []

    def play(self, urgent: bool = False) -> bool:
        self.calls.append(urgent)
        return True


class _StubNotificationIface:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def Notify(self, app_name, replaces_id, icon, summary, body, actions, hints, timeout):
        self.calls.append(
            {
                "app_name": str(app_name),
                "replaces_id": int(replaces_id),
                "icon": str(icon),
                "summary": str(summary),
                "body": str(body),
                "hints": dict(hints),
                "timeout": int(timeout),
            }
        )
        return len(self.calls)


class _StubClient:
    def __init__(self) -> None:
        self.unlock_hints = 0

    def subscribe_signal(self, *_args):
        pass

    def watch_daemon_owner(self, *_args):
        pass

    def get_my_status(self):
        return {}

    def report_screensaver_state(self, *_args):
        pass

    def notify_session_unlocked(self) -> bool:
        self.unlock_hints += 1
        return True


class _StubScreensaver:
    def is_active(self) -> bool:
        return False


class _StubTelemetryProbe:
    def collect(self):
        return (False, None, None)


class TestFullscreenDetection(unittest.TestCase):
    def test_detects_x11_fullscreen_via_xprop(self):
        def fake_run(argv, _timeout):
            if "_NET_ACTIVE_WINDOW" in argv:
                return subprocess.CompletedProcess(
                    argv, 0, stdout="_NET_ACTIVE_WINDOW(WINDOW): window id # 0x3a00007\n", stderr=""
                )
            if "_NET_WM_STATE" in argv:
                return subprocess.CompletedProcess(
                    argv,
                    0,
                    stdout="_NET_WM_STATE(ATOM) = _NET_WM_STATE_FULLSCREEN, _NET_WM_STATE_FOCUSED\n",
                    stderr="",
                )
            return subprocess.CompletedProcess(argv, 1, stdout="", stderr="")

        self.assertTrue(
            is_fullscreen_window_active(
                env={"DISPLAY": ":0", "XDG_SESSION_TYPE": "x11"},
                which_fn=lambda cmd: "/usr/bin/xprop" if cmd == "xprop" else None,
                run_fn=fake_run,
            )
        )

    def test_returns_false_when_wayland_or_not_fullscreen(self):
        self.assertFalse(
            is_fullscreen_window_active(
                env={"DISPLAY": ":0", "XDG_SESSION_TYPE": "wayland"},
                which_fn=lambda _cmd: "/usr/bin/xprop",
            )
        )

        def fake_run_normal(argv, _timeout):
            if "_NET_ACTIVE_WINDOW" in argv:
                return subprocess.CompletedProcess(
                    argv, 0, stdout="_NET_ACTIVE_WINDOW(WINDOW): window id # 0x1234\n", stderr=""
                )
            return subprocess.CompletedProcess(
                argv, 0, stdout="_NET_WM_STATE(ATOM) = _NET_WM_STATE_MAXIMIZED_VERT\n", stderr=""
            )

        self.assertFalse(
            is_fullscreen_window_active(
                env={"DISPLAY": ":0", "XDG_SESSION_TYPE": "x11"},
                which_fn=lambda cmd: "/usr/bin/xprop" if cmd == "xprop" else None,
                run_fn=fake_run_normal,
            )
        )


class TestDesktopNotifierAndController(unittest.TestCase):
    def test_fullscreen_triggers_critical_hints_osd_and_chime(self):
        osd = _StubOsdBanner()
        chime = _StubChime()
        notifier = DesktopNotifier(
            session_bus=object(),
            fullscreen_probe=lambda: True,
            osd_banner=osd,  # type: ignore[arg-type]
        )
        iface = _StubNotificationIface()
        notifier._iface = iface

        ctrl = TrayController(
            username="alice",
            client=_StubClient(),  # type: ignore[arg-type]
            screensaver=_StubScreensaver(),  # type: ignore[arg-type]
            notifier=notifier,
            telemetry_probe=_StubTelemetryProbe(),  # type: ignore[arg-type]
            chime=chime,  # type: ignore[arg-type]
        )
        ctrl.on_client_notification("alice", '{"kind": "warning", "seconds_left": 300}')

        self.assertEqual(len(iface.calls), 1)
        hints = iface.calls[0]["hints"]
        self.assertEqual(int(hints["urgency"]), 2)
        self.assertTrue(bool(hints["x-cinnamon-override-dnd"]))
        self.assertFalse(bool(hints["transient"]))
        self.assertEqual(len(osd.calls), 1)
        self.assertEqual(osd.calls[0][3], "warning")
        self.assertEqual(chime.calls, [False])

        ctrl.on_client_notification("alice", '{"kind": "countdown", "seconds_left": 12}')
        self.assertEqual(len(osd.calls), 2)
        self.assertEqual(osd.calls[1][3], "critical")
        self.assertEqual(chime.calls, [False, True])

    def test_tray_controller_time_granted_when_session_locked(self):
        osd = _StubOsdBanner()
        notifier = DesktopNotifier(
            session_bus=object(),
            fullscreen_probe=lambda: False,
            osd_banner=osd,  # type: ignore[arg-type]
        )
        iface = _StubNotificationIface()
        notifier._iface = iface

        ctrl = TrayController(
            username="alice",
            client=_StubClient(),  # type: ignore[arg-type]
            screensaver=_StubScreensaver(),  # type: ignore[arg-type]
            notifier=notifier,
            telemetry_probe=_StubTelemetryProbe(),  # type: ignore[arg-type]
            chime=_StubChime(),  # type: ignore[arg-type]
        )
        ctrl.on_client_notification(
            "alice",
            '{"kind": "time_granted", "applied_sec": 900, "note": "chores", "session_locked": true}',
        )
        self.assertEqual(len(iface.calls), 1)
        self.assertIn("You may unlock your session now.", str(iface.calls[0]["body"]))
        hints = iface.calls[0]["hints"]
        self.assertEqual(int(hints["urgency"]), 2)
        self.assertTrue(bool(hints["resident"]))

        notifier.dismiss()
        self.assertEqual(osd.dismiss_calls, 1)

    def test_screensaver_active_changed_triggers_early_unlock_hint_only_when_locked_out(self):
        from unittest import mock

        from curfewd.tray.app import SessionScreensaverProbe

        receivers: list[tuple[str, str, object]] = []

        class _FakeSessionBus:
            def add_signal_receiver(self, handler, signal_name="", dbus_interface="", **_kw):
                receivers.append((signal_name, dbus_interface, handler))
                return (signal_name, dbus_interface)

        client = _StubClient()
        probe = SessionScreensaverProbe(session_bus=_FakeSessionBus(), desktop="X-Cinnamon")
        ctrl = TrayController(
            username="alice",
            client=client,  # type: ignore[arg-type]
            screensaver=probe,
            notifier=DesktopNotifier(
                session_bus=object(),
                fullscreen_probe=lambda: False,
                osd_banner=_StubOsdBanner(),  # type: ignore[arg-type]
            ),
            telemetry_probe=_StubTelemetryProbe(),  # type: ignore[arg-type]
            chime=_StubChime(),  # type: ignore[arg-type]
        )
        ctrl.attach()
        ifaces = {iface for sig, iface, _ in receivers if sig == "ActiveChanged"}
        self.assertEqual(ifaces, {"org.cinnamon.ScreenSaver", "org.freedesktop.ScreenSaver"})

        cinnamon_cb = next(h for _, iface, h in receivers if iface == "org.cinnamon.ScreenSaver")
        fdo_cb = next(h for _, iface, h in receivers if iface == "org.freedesktop.ScreenSaver")

        fake_mono = [500.0]
        with mock.patch("curfewd.tray.app.time.monotonic", side_effect=lambda: fake_mono[0]):
            # When not locked_out, ActiveChanged(False) does not send unlock hints
            ctrl.last_status = {"enforcement_state": "ok"}
            cinnamon_cb(False)  # type: ignore[operator]
            self.assertEqual(client.unlock_hints, 0)

            # When locked_out, ActiveChanged(True) ignored; ActiveChanged(False) fires once (debounced)
            ctrl.last_status = {"enforcement_state": "locked_out"}
            cinnamon_cb(True)  # type: ignore[operator]
            self.assertEqual(client.unlock_hints, 0)
            cinnamon_cb(False)  # type: ignore[operator]
            fdo_cb(False)  # type: ignore[operator]
            self.assertEqual(client.unlock_hints, 1)

            # After 1.5s debounce window, another unlock fires cleanly
            fake_mono[0] += 1.5
            cinnamon_cb(False)  # type: ignore[operator]
            self.assertEqual(client.unlock_hints, 2)


class TestTrayGracefulShutdownAndDisplayCheck(unittest.TestCase):
    def test_osd_banner_dismiss_destroys_window_and_clears_timeout(self):
        from curfewd.tray.osd import FullscreenOsdBanner

        destroyed = {"count": 0}

        class _FakeWindow:
            def destroy(self) -> None:
                destroyed["count"] += 1

        banner = FullscreenOsdBanner()
        banner._window = _FakeWindow()
        banner._timeout_id = None
        self.assertFalse(banner.dismiss())
        self.assertEqual(destroyed["count"], 1)
        self.assertIsNone(banner._window)

    def test_has_active_graphical_display_and_clean_exit_without_display(self):
        from unittest import mock

        from curfewd.tray.app import _has_active_graphical_display, main

        self.assertFalse(_has_active_graphical_display(env={}))
        self.assertFalse(_has_active_graphical_display(env={"DISPLAY": ""}, gtk_check=lambda: True))
        self.assertFalse(
            _has_active_graphical_display(env={"DISPLAY": ":0"}, gtk_check=lambda: False)
        )
        self.assertTrue(
            _has_active_graphical_display(env={"DISPLAY": ":0"}, gtk_check=lambda: True)
        )
        self.assertTrue(
            _has_active_graphical_display(
                env={"WAYLAND_DISPLAY": "wayland-0"}, gtk_check=lambda: True
            )
        )

        with mock.patch("curfewd.tray.app._has_active_graphical_display", return_value=False):
            self.assertEqual(main([]), 0)

    def test_attach_session_exit_handlers_covers_login1_bus_and_gdk(self):
        from curfewd.tray.app import _attach_session_exit_handlers

        sys_receivers: dict[str, object] = {}
        sess_receivers: dict[str, object] = {}
        gdk_signals: dict[str, object] = {}
        exit_on_disconnect_calls: list[bool] = []
        quits = {"count": 0}

        class _FakeBus:
            def __init__(self, target: dict[str, object]) -> None:
                self._target = target

            def set_exit_on_disconnect(self, flag: bool) -> None:
                exit_on_disconnect_calls.append(flag)

            def add_signal_receiver(self, handler, signal_name="", **_kw):
                self._target[signal_name] = handler
                return signal_name

        class _FakeGdkDisplay:
            def connect(self, sig: str, cb):
                gdk_signals[sig] = cb
                return sig

        handles = _attach_session_exit_handlers(
            lambda: quits.__setitem__("count", quits["count"] + 1),
            system_bus=_FakeBus(sys_receivers),
            session_bus=_FakeBus(sess_receivers),
            session_id="c2",
            gdk_display=_FakeGdkDisplay(),
        )
        self.assertEqual(len(handles), 4)
        self.assertEqual(exit_on_disconnect_calls, [False])

        # PrepareForShutdown(False) must not quit; True must quit
        sys_receivers["PrepareForShutdown"](False)  # type: ignore[operator]
        self.assertEqual(quits["count"], 0)
        sys_receivers["PrepareForShutdown"](True)  # type: ignore[operator]
        self.assertEqual(quits["count"], 1)

        # SessionRemoved only quits for matching session ID
        sys_receivers["SessionRemoved"]("c99", "/org/freedesktop/login1/session/c99")  # type: ignore[operator]
        self.assertEqual(quits["count"], 1)
        sys_receivers["SessionRemoved"]("c2", "/org/freedesktop/login1/session/c2")  # type: ignore[operator]
        self.assertEqual(quits["count"], 2)

        # SessionBus Disconnected and GDK Display closed both quit cleanly
        sess_receivers["Disconnected"]()  # type: ignore[operator]
        self.assertEqual(quits["count"], 3)
        gdk_signals["closed"](None, False)  # type: ignore[operator]
        self.assertEqual(quits["count"], 4)


if __name__ == "__main__":
    unittest.main()
