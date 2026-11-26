"""Live ephemeral D-Bus integration tests for org.curfewd.Daemon1, Polkit, sender-UID, and tray auto-reconnect."""

from datetime import datetime, timezone
import getpass
import json
import os
import shutil
import subprocess
import tempfile
import threading
import time
import unittest

import dbus
from dbus.mainloop.glib import DBusGMainLoop
from gi.repository import GLib

from curfewd.dbus.client import (
    CurfewClient,
    PermissionDeniedError,
    UserNotFoundError,
    ValidationError,
)
from curfewd.dbus.polkit import PolkitAuthorizer
from curfewd.dbus.service import CurfewDaemonService
from curfewd.history import HistoryStore
from curfewd.models import LockResult, TimeWindow, UserPolicy
from curfewd.parsing import WEEKDAYS
from curfewd.storage import AtomicJsonStore
from curfewd.tray.app import DesktopNotifier, SessionScreensaverProbe, TrayController


class _FakeSessionMonitor:
    def __init__(self, username: str) -> None:
        self.username = username
        self.lock_calls = 0

    def list_user_sessions(self, tracked_types=None):
        return {
            self.username: [
                {
                    "session_id": "c1",
                    "uid": os.getuid(),
                    "username": self.username,
                    "session_type": "x11",
                    "active": True,
                    "locked": False,
                    "idle": False,
                    "display": ":0",
                    "desktop": "X-Cinnamon",
                    "leader_pid": os.getpid(),
                }
            ]
        }

    def summarize_sessions(self, sessions):
        return [
            {
                "session_id": s["session_id"],
                "session_type": s["session_type"],
                "desktop": s["desktop"],
                "active": s["active"],
                "locked": s["locked"],
                "display": s["display"],
                "leader_pid": s["leader_pid"],
            }
            for s in sessions
        ]

    def lock_user_sessions(self, sessions, config, async_mode=True):
        self.lock_calls += 1
        return [
            LockResult(
                session_id=s["session_id"],
                username=s["username"],
                locked=True,
                verified=True,
                backend="dbus:org.cinnamon.ScreenSaver",
                stage="dbus_or_cli",
                elapsed_ms=42,
            )
            for s in sessions
        ]

    def enforce_lockout_action(self, action, username, uid, sessions, config):
        self.lock_calls += 1


class _FakeScreensaverProbe(SessionScreensaverProbe):
    def __init__(self, active: bool = True) -> None:
        super().__init__(session_bus=None, desktop="X-Cinnamon")
        self._active = active

    def is_active(self) -> bool:
        return self._active


class _RecordingNotifier(DesktopNotifier):
    def __init__(self) -> None:
        super().__init__(session_bus=None)
        self.messages: list[tuple[str, str, int]] = []

    def send(self, summary: str, body: str, urgency: int = 1, icon: str = "timekpr-client") -> None:
        self.messages.append((summary, body, urgency))


@unittest.skipUnless(shutil.which("dbus-daemon"), "dbus-daemon required for live D-Bus tests")
class TestLiveDaemonDBusIntegration(unittest.TestCase):
    def setUp(self) -> None:
        DBusGMainLoop(set_as_default=True)
        self._tmpdir = tempfile.TemporaryDirectory()
        self._bus_proc = subprocess.Popen(
            ["dbus-daemon", "--session", "--nofork", "--print-address=1"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        self._bus_addr = self._bus_proc.stdout.readline().strip()
        self.assertTrue(self._bus_addr, "Failed to start ephemeral dbus-daemon")

        self._server_bus = dbus.bus.BusConnection(self._bus_addr)
        self._client_bus = dbus.bus.BusConnection(self._bus_addr)
        self._current_user = getpass.getuser()

        self._store = AtomicJsonStore(
            config_dir=os.path.join(self._tmpdir.name, "etc"),
            state_dir=os.path.join(self._tmpdir.name, "var"),
        )
        self._history = HistoryStore(os.path.join(self._tmpdir.name, "var", "history.db"))
        self._allow_polkit = True
        self._authorizer = PolkitAuthorizer(
            self._server_bus,
            polkit_checker=lambda _sender, _action, _uid: self._allow_polkit,
        )
        self._sessions = _FakeSessionMonitor(self._current_user)
        self._fixed_now = datetime(2026, 11, 25, 16, 0, tzinfo=timezone.utc)

        # Seed a restricted policy for the test user
        pol = UserPolicy(
            username=self._current_user,
            daily_limits={d: 3600 for d in WEEKDAYS},
            curfew_windows={d: [TimeWindow("08:00", "21:00")] for d in WEEKDAYS},
        )
        self._store.save_user_policy(pol)

        self._service = CurfewDaemonService(
            bus=self._server_bus,
            store=self._store,
            history=self._history,
            session_monitor=self._sessions,
            authorizer=self._authorizer,
            now_fn=lambda: self._fixed_now,
        )

        self._loop = GLib.MainLoop()
        self._thread = threading.Thread(target=self._loop.run, daemon=True)
        self._thread.start()
        self._client = CurfewClient(bus=self._client_bus)

    def tearDown(self) -> None:
        GLib.idle_add(self._loop.quit)
        self._thread.join(timeout=3.0)
        self._server_bus.close()
        self._client_bus.close()
        self._bus_proc.terminate()
        try:
            self._bus_proc.wait(timeout=3.0)
        except subprocess.TimeoutExpired:
            self._bus_proc.kill()
        self._tmpdir.cleanup()

    def test_admin_crud_grant_pause_resume_lock_history_doctor(self):
        users = self._client.list_users()
        names = [u["username"] for u in users]
        self.assertIn(self._current_user, names)

        st = self._client.get_user_status(self._current_user)
        self.assertEqual(st["daily_limit_today"], 3600)
        self.assertEqual(st["time_left_today"], 3600)
        self.assertEqual(st["enforcement_state"], "ok")

        # Grant +30m
        grant_res = self._client.grant_time(self._current_user, 1800, mode="add")
        self.assertEqual(grant_res["applied_sec"], 1800)
        self.assertEqual(grant_res["status"]["time_left_today"], 5400)

        # Pause & verify schedule untouched
        paused = self._client.pause_user(self._current_user, until_utc="", reason="test", hide_tray=True)
        self.assertEqual(paused["enforcement_state"], "paused")
        self.assertTrue(paused["hide_tray_effective"])
        self.assertEqual(paused["policy"]["daily_limits"]["wed"], 3600)

        resumed = self._client.resume_user(self._current_user)
        self.assertEqual(resumed["enforcement_state"], "ok")

        # Verified LockUser D-Bus endpoint
        lock_res = self._client.lock_user(self._current_user)
        self.assertTrue(lock_res["locked"])
        self.assertTrue(lock_res["verified"])
        self.assertEqual(lock_res["sessions"][0]["backend"], "dbus:org.cinnamon.ScreenSaver")

        # Usage history records grant, pause, resume, lock_manual events
        hist = self._client.get_usage_history(self._current_user, days=7)
        kinds = [ev["kind"] for ev in hist["events"]]
        for expected in ("grant", "pause", "resume", "lock_manual"):
            self.assertIn(expected, kinds)

        # Daemon config & doctor
        cfg = self._client.get_daemon_config()
        cfg["log_level"] = "DEBUG"
        updated_cfg = self._client.set_daemon_config(cfg)
        self.assertEqual(updated_cfg["log_level"], "DEBUG")

        doc = self._client.run_doctor()
        self.assertEqual(doc["checks"][1]["status"], "ok")

    def test_polkit_deny_and_validation_errors(self):
        self._allow_polkit = False
        with self.assertRaises(PermissionDeniedError):
            self._client.list_users()
        with self.assertRaises(PermissionDeniedError):
            self._client.lock_user(self._current_user)

        # Unprivileged client self-status STILL works via sender-UID binding even when Polkit denies admin!
        my_st = self._client.get_my_status()
        self.assertEqual(my_st["username"], self._current_user)

        self._allow_polkit = True
        with self.assertRaises(UserNotFoundError):
            self._client.get_user_status("nonexistent_user_xyz")
        with self.assertRaises(ValidationError):
            self._client.get_user_status("../badpath")

    def test_csprng_nonce_verification_and_replay_rejection(self):
        # Trigger tick on GLib thread to emit a real CSPRNG ScreensaverChallenge
        done = threading.Event()
        GLib.idle_add(lambda: (self._service.tick(), done.set(), False)[2])
        self.assertTrue(done.wait(timeout=3.0))

        nonce = self._service._pending_nonces.get(self._current_user)
        self.assertIsNotNone(nonce)
        self.assertGreaterEqual(len(nonce), 16)

        # Wrong nonce fails and consumes the challenge
        self.assertFalse(self._client.report_screensaver_state("wrong_nonce_value", True))
        self.assertIsNone(self._service._pending_nonces.get(self._current_user))

        # Fresh nonce succeeds once and rejects replay
        self._service._pending_nonces[self._current_user] = "fresh-csprng-nonce-123"
        self.assertTrue(self._client.report_screensaver_state("fresh-csprng-nonce-123", True))
        self.assertTrue(self._service._tray_screensaver_active[self._current_user])
        self.assertFalse(self._client.report_screensaver_state("fresh-csprng-nonce-123", False))

    def test_tray_auto_reconnects_when_daemon_restarts(self):
        notifier = _RecordingNotifier()
        watch_bus = dbus.bus.BusConnection(self._bus_addr)
        rpc_bus = dbus.bus.BusConnection(self._bus_addr)

        class _ThreadedTestClient(CurfewClient):
            def __init__(self) -> None:
                super().__init__(bus=watch_bus)
                self._rpc_client = CurfewClient(bus=rpc_bus)

            def get_my_status(self):
                return self._rpc_client.get_my_status()

        tray_client = _ThreadedTestClient()
        sync_done = threading.Event()

        class _AsyncSyncController(TrayController):
            def sync_now(self) -> bool:
                def _bg():
                    super(_AsyncSyncController, self).sync_now()
                    sync_done.set()

                threading.Thread(target=_bg, daemon=True).start()
                return True

        controller = _AsyncSyncController(
            username=self._current_user,
            client=tray_client,
            screensaver=_FakeScreensaverProbe(active=True),
            notifier=notifier,
        )
        attached = threading.Event()
        GLib.idle_add(lambda: (controller.attach(), attached.set(), False)[2])
        self.assertTrue(attached.wait(timeout=3.0))
        self.assertTrue(sync_done.wait(timeout=3.0))
        self.assertTrue(controller.connected)
        initial_reconnects = controller.reconnect_count

        # Simulate a true daemon restart: close old connection and spin up a fresh service on a new BusConnection
        sync_done.clear()
        restarted_refs = []

        def _restart_daemon():
            self._service.remove_from_connection()
            self._service._bus_name = None
            self._server_bus.close()

            def _start_new():
                new_bus = dbus.bus.BusConnection(self._bus_addr)
                new_auth = PolkitAuthorizer(new_bus, polkit_checker=lambda _s, _a, _u: True)
                new_svc = CurfewDaemonService(
                    bus=new_bus,
                    store=self._store,
                    history=self._history,
                    session_monitor=self._sessions,
                    authorizer=new_auth,
                    now_fn=lambda: self._fixed_now,
                )
                self._server_bus = new_bus
                self._service = new_svc
                restarted_refs.append((new_bus, new_svc))
                return False

            GLib.timeout_add(100, _start_new)
            return False

        GLib.idle_add(_restart_daemon)
        deadline = time.monotonic() + 4.0
        while (controller.reconnect_count <= initial_reconnects or not controller.connected) and time.monotonic() < deadline:
            time.sleep(0.05)

        self.assertGreater(controller.reconnect_count, initial_reconnects)
        self.assertTrue(controller.connected)
        self.assertIsNotNone(controller.last_status)

        # Verify tray notification rendering
        controller.on_client_notification(self._current_user, json.dumps({"kind": "warning", "seconds_left": 600}))
        self.assertEqual(len(notifier.messages), 1)
        self.assertIn("10m", notifier.messages[0][1])
        watch_bus.close()
        rpc_bus.close()


if __name__ == "__main__":
    unittest.main()
