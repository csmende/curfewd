"""Unit tests for curfewd.tray.osd and fullscreen/lock-screen notifications (Spec 007)."""

import subprocess
import unittest

from curfewd.tray.app import DesktopNotifier, TrayController
from curfewd.tray.osd import is_fullscreen_window_active


class _StubOsdBanner:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, int, str]] = []

    def show(
        self,
        summary: str,
        body: str,
        duration_ms: int = 5000,
        accent: str = "warning",
    ) -> bool:
        self.calls.append((summary, body, duration_ms, accent))
        return True


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
    def subscribe_signal(self, *_args):
        pass

    def watch_daemon_owner(self, *_args):
        pass

    def get_my_status(self):
        return {}

    def report_screensaver_state(self, *_args):
        pass


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


if __name__ == "__main__":
    unittest.main()
