"""Live ephemeral D-Bus integration tests for org.curfewd.Daemon1, Polkit, sender-UID, and tray auto-reconnect."""

import contextlib
import getpass
import json
import os
import shutil
import subprocess
import tempfile
import threading
import time
import unittest
from datetime import datetime, timezone

import dbus
from dbus.mainloop.glib import DBusGMainLoop, threads_init
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
        self.lock_delay_sec = 0.0
        self.lock_started = threading.Event()

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

    def prepare_session_locks(self, sessions):
        return list(sessions)

    def execute_fallback_locks(self, contexts, config):
        self.lock_calls += 1
        self.lock_started.set()
        if self.lock_delay_sec > 0:
            time.sleep(self.lock_delay_sec)
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
            for s in contexts
        ]

    def lock_user_sessions(self, sessions, config, async_mode=True):
        return self.execute_fallback_locks(sessions, config)

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

    def send(self, summary: str, body: str, urgency: int = 1, icon: str = "curfewd") -> None:
        self.messages.append((summary, body, urgency))


@unittest.skipUnless(shutil.which("dbus-daemon"), "dbus-daemon required for live D-Bus tests")
class TestLiveDaemonDBusIntegration(unittest.TestCase):
    def setUp(self) -> None:
        if dbus.get_default_main_loop() is None:
            threads_init()
            DBusGMainLoop(set_as_default=True)
        self._extra_buses: list[dbus.bus.BusConnection] = []
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
            daily_limits=dict.fromkeys(WEEKDAYS, 3600),
            curfew_windows={d: [TimeWindow("08:00", "21:00")] for d in WEEKDAYS},
        )
        self._store.save_user_policy(pol)

        self._doctor_fix_calls = 0
        self._config_reloads = []
        self._service = CurfewDaemonService(
            bus=self._server_bus,
            store=self._store,
            history=self._history,
            session_monitor=self._sessions,
            authorizer=self._authorizer,
            now_fn=lambda: self._fixed_now,
            doctor_fixer=self._fake_doctor_fix,
            on_config_changed=self._config_reloads.append,
        )

        self._loop = GLib.MainLoop()
        self._thread = threading.Thread(target=self._loop.run, daemon=True)
        self._thread.start()
        self._client = CurfewClient(bus=self._client_bus)

    def _open_extra_bus(self) -> dbus.bus.BusConnection:
        """Open a BusConnection on the GLib thread and defer closing until loop exit."""
        created: list[dbus.bus.BusConnection] = []
        done = threading.Event()

        def _create() -> bool:
            created.append(dbus.bus.BusConnection(self._bus_addr))
            done.set()
            return False

        GLib.idle_add(_create)
        self.assertTrue(done.wait(timeout=3.0), "Timed out opening BusConnection on GLib thread")
        bus = created[0]
        self._extra_buses.append(bus)
        return bus

    def _fake_doctor_fix(self):
        self._doctor_fix_calls += 1
        return {"applied": True, "files": [], "dconf_updated": True, "detail": "stub ok"}

    def tearDown(self) -> None:
        def _stop() -> bool:
            with contextlib.suppress(Exception):
                self._service.remove_from_connection()
            self._service._bus_name = None
            self._service._name = None
            self._loop.quit()
            return False

        GLib.idle_add(_stop)
        self._thread.join(timeout=3.0)
        self._service.shutdown()
        self._history.close()
        for bus in (*reversed(self._extra_buses), self._client_bus, self._server_bus):
            with contextlib.suppress(Exception):
                bus.close()
        self._bus_proc.terminate()
        try:
            self._bus_proc.wait(timeout=3.0)
        except subprocess.TimeoutExpired:
            self._bus_proc.kill()
        if self._bus_proc.stdout:
            self._bus_proc.stdout.close()
        if self._bus_proc.stderr:
            self._bus_proc.stderr.close()
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

        # Today-only override does not alter weekly template
        ovr_st = self._client.set_today_override(
            self._current_user,
            limit_sec=7200,
            windows=[{"start": "08:00", "end": "22:30"}],
        )
        self.assertTrue(ovr_st["today_override"]["active"])
        self.assertEqual(ovr_st["daily_limit_today"], 7200)
        self.assertEqual(ovr_st["today_windows"], [{"start": "08:00", "end": "22:30"}])
        self.assertEqual(ovr_st["policy"]["daily_limits"]["wed"], 3600)

        # Reset today override restores weekly template
        rst_st = self._client.set_today_override(self._current_user, reset=True)
        self.assertFalse(rst_st["today_override"]["active"])
        self.assertEqual(rst_st["daily_limit_today"], 3600)

        # Per-app limits + GrantAppTime
        pol = dict(st["policy"])
        pol["app_limits"] = {
            "enabled": True,
            "apps": [
                {
                    "pattern": "steam",
                    "label": "Steam",
                    "daily_limit_sec": 2700,
                    "category": "Gaming",
                }
            ],
        }
        self._client.set_user_policy(self._current_user, pol)
        app_grant = self._client.grant_app_time(self._current_user, "steam", 900)
        self.assertEqual(app_grant["applied_sec"], 900)
        self.assertEqual(
            app_grant["status"]["app_limits_status"]["apps"][0]["allotted_today"], 3600
        )

        # Pause & verify schedule untouched
        paused = self._client.pause_user(
            self._current_user, until_utc="", reason="test", hide_tray=True
        )
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

        # Usage history records grant, today_override, app_grant, pause, resume, lock_manual events
        hist = self._client.get_usage_history(self._current_user, days=7)
        kinds = [ev["kind"] for ev in hist["events"]]
        for expected in ("grant", "today_override", "app_grant", "pause", "resume", "lock_manual"):
            self.assertIn(expected, kinds)

        # Daemon config & doctor + ApplyDoctorFix
        cfg = self._client.get_daemon_config()
        cfg["log_level"] = "DEBUG"
        updated_cfg = self._client.set_daemon_config(cfg)
        self.assertEqual(updated_cfg["log_level"], "DEBUG")

        doc = self._client.run_doctor()
        self.assertEqual(doc["checks"][1]["status"], "ok")

        fix_resp = self._client.apply_doctor_fix()
        self.assertTrue(fix_resp["fix_applied"]["applied"])
        self.assertEqual(self._doctor_fix_calls, 1)

    def test_polkit_deny_and_validation_errors(self):
        self._allow_polkit = False
        with self.assertRaises(PermissionDeniedError):
            self._client.list_users()
        with self.assertRaises(PermissionDeniedError):
            self._client.lock_user(self._current_user)
        with self.assertRaises(PermissionDeniedError):
            self._client.apply_doctor_fix()

        # Unprivileged client self-status & self-history STILL work via sender-UID binding even when Polkit denies admin!
        my_st = self._client.get_my_status()
        self.assertEqual(my_st["username"], self._current_user)
        my_hist = self._client.get_my_history(days=7)
        self.assertEqual(my_hist["username"], self._current_user)
        self.assertEqual(my_hist["events"], [])  # admin audit events omitted

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
        watch_bus = self._open_extra_bus()
        rpc_bus = self._open_extra_bus()

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
            old_svc = self._service
            old_bus = self._server_bus
            with contextlib.suppress(Exception):
                old_svc.remove_from_connection()
            old_svc._bus_name = None
            old_svc._name = None
            old_svc.shutdown()
            old_bus.close()

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
        while (
            controller.reconnect_count <= initial_reconnects or not controller.connected
        ) and time.monotonic() < deadline:
            time.sleep(0.05)

        self.assertGreater(controller.reconnect_count, initial_reconnects)
        self.assertTrue(controller.connected)
        self.assertIsNotNone(controller.last_status)

        # Verify tray notification rendering
        controller.on_client_notification(
            self._current_user, json.dumps({"kind": "warning", "seconds_left": 600})
        )
        self.assertEqual(len(notifier.messages), 1)
        self.assertIn("10m", notifier.messages[0][1])

    def test_client_proxy_works_without_mainloop(self):
        """Regression test: one-shot curfewctl / worker threads work when follow_name_owner_changes raises RuntimeError."""
        calls = []

        class _NoLoopBus:
            def get_object(self, bus_name, path, introspect=False, follow_name_owner_changes=False):
                calls.append(follow_name_owner_changes)
                if follow_name_owner_changes:
                    raise RuntimeError("D-Bus connections must be attached to a main loop")
                return "sync-proxy-ok"

        c = CurfewClient(bus=_NoLoopBus(), follow_name_owner=True)
        self.assertEqual(c._get_proxy(), "sync-proxy-ok")
        self.assertEqual(calls, [True, False])

    def test_async_lock_user_does_not_block_concurrent_status_calls(self):
        """Spec 005: blocking LockUser runs on RPC worker pool while GLib main loop stays responsive."""
        self._sessions.lock_delay_sec = 0.4
        self._sessions.lock_started.clear()
        lock_result: dict[str, object] = {}

        lock_bus = self._open_extra_bus()
        lock_client = CurfewClient(bus=lock_bus, follow_name_owner=False)

        def _invoke_lock() -> None:
            lock_result["resp"] = lock_client.lock_user(self._current_user)

        t = threading.Thread(target=_invoke_lock, daemon=True)
        t.start()
        self.assertTrue(self._sessions.lock_started.wait(timeout=2.0))

        # While LockUser is sleeping 0.4s in the worker pool, GetUserStatus must return immediately
        t0 = time.monotonic()
        st = self._client.get_user_status(self._current_user)
        elapsed = time.monotonic() - t0
        self.assertEqual(st["username"], self._current_user)
        self.assertLess(
            elapsed, 0.25, f"GLib main loop was blocked for {elapsed:.3f}s during LockUser"
        )

        t.join(timeout=3.0)
        self.assertTrue(lock_result.get("resp", {}).get("locked"))

    def test_set_daemon_config_invokes_live_reload_callback(self):
        cfg = self._client.get_daemon_config()
        cfg["poll_interval_sec"] = 5
        cfg["log_level"] = "DEBUG"
        updated = self._client.set_daemon_config(cfg)
        self.assertEqual(updated["poll_interval_sec"], 5)
        self.assertEqual(updated["log_level"], "DEBUG")
        self.assertEqual(len(self._config_reloads), 1)
        self.assertEqual(self._config_reloads[0].poll_interval_sec, 5)
        self.assertEqual(self._config_reloads[0].log_level, "DEBUG")

    def test_spec006_dbus_requests_parent_code_homework_telemetry_and_async_rpcs(self):
        from curfewd.auth_code import compute_totp

        # Grant with positive note
        g = self._client.grant_time(self._current_user, 900, mode="add", note="Cleaned room")
        self.assertEqual(g["note"], "Cleaned room")
        self.assertEqual(g["status"]["last_bonus_note"], "Cleaned room")

        # Homework Mode toggle
        hw_on = self._client.set_homework_mode(
            self._current_user, {"active": True, "mode": "indefinite", "reason": "math"}
        )
        self.assertEqual(hw_on["enforcement_state"], "homework")
        hw_off = self._client.set_homework_mode(self._current_user, {"active": False})
        self.assertNotEqual(hw_off["enforcement_state"], "homework")

        # Unprivileged client: RequestMoreTime + ListMyTimeRequests + ClaimSaveGrace work even when Polkit denies admin
        self._client.grant_time(self._current_user, 30, mode="set")
        self._allow_polkit = False
        req = self._client.request_more_time(900, kind="daily", reason="finishing quiz")
        self.assertEqual(req["status"], "pending")
        self.assertEqual(len(self._client.list_my_time_requests()), 1)
        grace = self._client.claim_save_grace()
        self.assertEqual(grace["granted_sec"], 120)

        # ReportClientTelemetry validates CSPRNG nonce and updates telemetry for owned PID
        my_pid = os.getpid()
        self._service._pending_nonces[self._current_user] = "telemetry-nonce-xyz"
        self.assertTrue(
            self._client.report_client_telemetry(
                "telemetry-nonce-xyz",
                {"screensaver_active": False, "idle_sec": 45, "active_window_pid": my_pid},
            )
        )
        self.assertEqual(self._service._runtime._tray_active_pid[self._current_user], my_pid)

        # Admin sets up offline TOTP Parent Access Code, then unprivileged client approves walk-up with 6-digit code
        self._allow_polkit = True
        pc = self._client.setup_parent_code(enable=True, rotate=False)
        self.assertTrue(pc["enabled"])
        self.assertIn("<svg", pc["qr_svg"])
        self.assertNotIn("secret_b32", self._client.get_parent_code_status())

        self._allow_polkit = False
        code = compute_totp(pc["secret_b32"], step=int(time.time() // 30))
        approved = self._client.approve_with_parent_code({"code": code, "request_id": req["id"]})
        self.assertEqual(approved["request"]["status"], "approved")

        # Admin async ListInstalledApps & TestLock + QuickApproveRequest
        self._allow_polkit = True
        apps = self._client.list_installed_apps(self._current_user)
        self.assertIsInstance(apps, list)
        tlock = self._client.test_lock(self._current_user)
        self.assertTrue(tlock["locked"])
        self.assertTrue(tlock["verified"])

        req2 = self._client.request_more_time(900, kind="daily", reason="extra")
        q = self._client.quick_approve_request(req2["id"], 900, "ok")
        self.assertEqual(q["request"]["status"], "approved")


if __name__ == "__main__":
    unittest.main()
