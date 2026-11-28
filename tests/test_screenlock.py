"""Unit tests for curfewd multi-DE screen lock enforcement."""

import os
import subprocess
import tempfile
import threading
import time
import unittest
from unittest import mock

from curfewd.engine import screenlock


def _proc_result(returncode=0, stdout="", stderr=""):
    return subprocess.CompletedProcess(args=[], returncode=returncode, stdout=stdout, stderr=stderr)


class TestDesktopSelection(unittest.TestCase):
    def test_cinnamon_selection(self):
        backends = screenlock.select_backends("X-Cinnamon")
        self.assertEqual([b.name for b in backends], ["cinnamon", "freedesktop"])
        self.assertEqual(backends[0].lock_dbus_args, ("",))

    def test_ubuntu_gnome_selection(self):
        backends = screenlock.select_backends("ubuntu:GNOME")
        self.assertEqual([b.name for b in backends], ["gnome", "freedesktop"])

    def test_xfce_selection(self):
        backends = screenlock.select_backends("XFCE")
        self.assertEqual(backends[0].name, "xfce")
        self.assertIn("freedesktop", [b.name for b in backends])

    def test_mate_selection(self):
        backends = screenlock.select_backends("MATE")
        self.assertEqual([b.name for b in backends], ["mate", "freedesktop"])

    def test_kde_plasma_selection(self):
        backends = screenlock.select_backends("KDE")
        self.assertEqual([b.name for b in backends], ["freedesktop"])
        self.assertIn("/ScreenSaver", backends[0].dbus_paths)

    def test_unknown_desktop_falls_back_to_all(self):
        backends = screenlock.select_backends("")
        self.assertEqual(
            [b.name for b in backends], ["cinnamon", "xfce", "mate", "gnome", "freedesktop"]
        )


class TestSessionContextDiscovery(unittest.TestCase):
    def test_reads_proc_environ_and_sanitizes_unsafe_vars(self):
        with tempfile.TemporaryDirectory() as tmp:
            proc_root = os.path.join(tmp, "proc")
            pid_dir = os.path.join(proc_root, "1234")
            os.makedirs(pid_dir)
            raw_env = (
                b"DISPLAY=:0\x00"
                b"XAUTHORITY=/home/alice/.Xauthority\x00"
                b"DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/1000/bus\x00"
                b"XDG_CURRENT_DESKTOP=X-Cinnamon\x00"
                b"LD_PRELOAD=/tmp/evil.so\x00"
                b"PYTHONPATH=/tmp/hijack\x00"
            )
            with open(os.path.join(pid_dir, "environ"), "wb") as fh:
                fh.write(raw_env)

            ctx = screenlock.discover_session_context(
                session_id="c2",
                uid=os.getuid(),
                username="alice",
                session_type="x11",
                leader_pid=1234,
                proc_root=proc_root,
                run_user_root=os.path.join(tmp, "run_user"),
            )
            self.assertEqual(ctx.display, ":0")
            self.assertEqual(ctx.xauthority, "/home/alice/.Xauthority")
            self.assertEqual(ctx.dbus_address, "unix:path=/run/user/1000/bus")
            self.assertEqual(ctx.desktop, "X-Cinnamon")

            safe = screenlock.build_safe_env(ctx)
            self.assertEqual(safe["PATH"], screenlock.TRUSTED_BIN_PATH)
            self.assertEqual(safe["DISPLAY"], ":0")
            self.assertEqual(safe["XAUTHORITY"], "/home/alice/.Xauthority")
            self.assertEqual(safe["DBUS_SESSION_BUS_ADDRESS"], "unix:path=/run/user/1000/bus")
            self.assertNotIn("LD_PRELOAD", safe)
            self.assertNotIn("PYTHONPATH", safe)

    def test_scans_session_process_and_run_user_bus_fallback(self):
        with tempfile.TemporaryDirectory() as tmp:
            proc_root = os.path.join(tmp, "proc")
            run_user = os.path.join(tmp, "run_user")
            uid = os.getuid()
            os.makedirs(os.path.join(run_user, str(uid)))
            bus_path = os.path.join(run_user, str(uid), "bus")
            with open(bus_path, "w") as fh:
                fh.write("")

            # Leader PID has minimal environment (common with LightDM)
            leader_dir = os.path.join(proc_root, "200")
            os.makedirs(leader_dir)
            with open(os.path.join(leader_dir, "environ"), "wb") as fh:
                fh.write(b"DISPLAY=:0\x00XDG_SESSION_ID=c1\x00")

            # Child cinnamon-session process holds desktop & XAUTHORITY
            sess_dir = os.path.join(proc_root, "250")
            os.makedirs(sess_dir)
            with open(os.path.join(sess_dir, "comm"), "w") as fh:
                fh.write("cinnamon-session\n")
            with open(os.path.join(sess_dir, "status"), "w") as fh:
                fh.write(f"Name:\tcinnamon-session\nUid:\t{uid}\t{uid}\t{uid}\t{uid}\n")
            with open(os.path.join(sess_dir, "environ"), "wb") as fh:
                fh.write(
                    b"XDG_SESSION_ID=c1\x00XDG_CURRENT_DESKTOP=X-Cinnamon\x00XAUTHORITY=/tmp/lightdm-xauth\x00"
                )

            ctx = screenlock.discover_session_context(
                session_id="c1",
                uid=uid,
                username="alice",
                session_type="x11",
                leader_pid=200,
                proc_root=proc_root,
                run_user_root=run_user,
            )
            self.assertEqual(ctx.desktop, "X-Cinnamon")
            self.assertEqual(ctx.xauthority, "/tmp/lightdm-xauth")
            self.assertEqual(ctx.dbus_address, f"unix:path={bus_path}")


class TestScreenLockEnforcer(unittest.TestCase):
    def _make_ctx(self, desktop="X-Cinnamon", session_type="x11"):
        return screenlock.SessionContext(
            session_id="c1",
            uid=1000,
            username="alice",
            gid=1000,
            home_dir="/home/alice",
            session_type=session_type,
            display=":0" if session_type == "x11" else "",
            xauthority="/home/alice/.Xauthority" if session_type == "x11" else "",
            xdg_runtime_dir="/run/user/1000",
            dbus_address="unix:path=/run/user/1000/bus",
            desktop=desktop,
            leader_pid=4242,
        )

    def test_cinnamon_activates_and_verifies_when_daemon_exited_after_30s(self):
        """Simulates cinnamon-screensaver having exited after 30s (NameHasOwner=false) then locking via D-Bus."""
        calls = []
        state = {"owner": False, "active": False}

        def fake_runner(cmd, **kwargs):
            calls.append((cmd, kwargs))
            if "org.freedesktop.DBus.NameHasOwner" in cmd:
                dest = cmd[-1]
                if dest == "org.cinnamon.ScreenSaver":
                    return _proc_result(0, "(true,)\n" if state["owner"] else "(false,)\n")
                return _proc_result(0, "(false,)\n")
            if "org.cinnamon.ScreenSaver.Lock" in cmd:
                # Verify empty message argument required by Cinnamon Lock(in s message)
                self.assertEqual(cmd[-1], "")
                state["owner"] = True
                state["active"] = True
                return _proc_result(0, "()\n")
            if "org.cinnamon.ScreenSaver.GetActive" in cmd:
                return _proc_result(0, "(true,)\n" if state["active"] else "(false,)\n")
            return _proc_result(1, "", "unexpected")

        enforcer = screenlock.ScreenLockEnforcer(
            runner=fake_runner,
            which_fn=lambda n: f"/usr/bin/{n}",
            sleep_fn=lambda _s: None,
        )
        ok = enforcer.enforce_session_lock(self._make_ctx("X-Cinnamon"), release_x11_grab=True)
        self.assertTrue(ok)
        # Must NOT have invoked xdotool or wmctrl since normal lock succeeded
        bins = [os.path.basename(c[0][0]) for c in calls]
        self.assertNotIn("xdotool", bins)
        self.assertNotIn("wmctrl", bins)
        self.assertTrue(any("org.cinnamon.ScreenSaver.Lock" in c[0] for c in calls))
        self.assertTrue(any("org.cinnamon.ScreenSaver.GetActive" in c[0] for c in calls))

    def test_already_locked_by_logind_skips_redundant_lock(self):
        calls = []

        def fake_runner(cmd, **kwargs):
            calls.append(cmd)
            if "org.freedesktop.DBus.NameHasOwner" in cmd:
                return _proc_result(0, "(true,)\n")
            if any(arg.endswith(".GetActive") for arg in cmd):
                return _proc_result(0, "(true,)\n")
            return _proc_result(1)

        enforcer = screenlock.ScreenLockEnforcer(
            runner=fake_runner,
            which_fn=lambda n: f"/usr/bin/{n}",
            sleep_fn=lambda _s: None,
        )
        ok = enforcer.enforce_session_lock(self._make_ctx("ubuntu:GNOME", "wayland"))
        self.assertTrue(ok)
        self.assertFalse(any("Lock" in " ".join(c) for c in calls))

    def test_fullscreen_game_grab_released_via_stage_3b_unfullscreen_and_retry(self):
        """Simulates Steam/Proton/Minecraft holding X11 grab so Lock() returns rc=0 with Active=false until unfullscreen."""
        calls = []
        state = {"grab_held": True, "active": False, "owner": True}

        def fake_runner(cmd, **kwargs):
            calls.append(cmd)
            if "org.freedesktop.DBus.NameHasOwner" in cmd:
                return _proc_result(
                    0, "(true,)\n" if cmd[-1] == "org.cinnamon.ScreenSaver" else "(false,)\n"
                )
            if "org.cinnamon.ScreenSaver.GetActive" in cmd:
                return _proc_result(0, "(true,)\n" if state["active"] else "(false,)\n")
            if "org.cinnamon.ScreenSaver.Lock" in cmd:
                # Silent failure when grab is held (exact cinnamon-screensaver behavior!)
                if not state["grab_held"]:
                    state["active"] = True
                return _proc_result(0, "()\n")
            if cmd[0].endswith("xdotool") and "XF86Ungrab" in cmd:
                # Simulate AllowDeactivateGrabs=False (grab remains held)
                return _proc_result(0)
            if cmd[0].endswith("wmctrl") and "remove,fullscreen" in cmd:
                # Game drops X11 grab once un-fullscreened
                state["grab_held"] = False
                return _proc_result(0)
            if cmd[0].endswith("xdotool") and "windowminimize" in cmd:
                state["grab_held"] = False
                return _proc_result(0)
            return _proc_result(1)

        enforcer = screenlock.ScreenLockEnforcer(
            runner=fake_runner,
            which_fn=lambda n: f"/usr/bin/{n}",
            sleep_fn=lambda _s: None,
        )
        ok = enforcer.enforce_session_lock(
            self._make_ctx("X-Cinnamon", "x11"), release_x11_grab=True
        )
        self.assertTrue(ok)
        self.assertTrue(any(c[0].endswith("wmctrl") for c in calls))
        lock_calls = [c for c in calls if "org.cinnamon.ScreenSaver.Lock" in c]
        self.assertEqual(len(lock_calls), 3)  # initial + after XF86Ungrab + after unfullscreen

    def test_xf86ungrab_success_does_not_minimize_window(self):
        """When Xorg AllowDeactivateGrabs=True lets XF86Ungrab break the grab, wmctrl/minimize is skipped."""
        calls = []
        state = {"grab_held": True, "active": False}

        def fake_runner(cmd, **kwargs):
            calls.append(cmd)
            if "org.freedesktop.DBus.NameHasOwner" in cmd:
                return _proc_result(
                    0, "(true,)\n" if cmd[-1] == "org.cinnamon.ScreenSaver" else "(false,)\n"
                )
            if any(arg.endswith(".GetActive") for arg in cmd):
                return _proc_result(0, "(true,)\n" if state["active"] else "(false,)\n")
            if "org.cinnamon.ScreenSaver.Lock" in cmd:
                if not state["grab_held"]:
                    state["active"] = True
                return _proc_result(0)
            if cmd[0].endswith("xdotool") and "XF86Ungrab" in cmd:
                state["grab_held"] = False
                return _proc_result(0)
            return _proc_result(1)

        enforcer = screenlock.ScreenLockEnforcer(
            runner=fake_runner,
            which_fn=lambda n: f"/usr/bin/{n}",
            sleep_fn=lambda _s: None,
        )
        ok = enforcer.enforce_session_lock(
            self._make_ctx("X-Cinnamon", "x11"), release_x11_grab=True
        )
        self.assertTrue(ok)
        self.assertTrue(any("XF86Ungrab" in c for c in calls))
        self.assertFalse(any(c[0].endswith("wmctrl") for c in calls))
        self.assertFalse(any("windowminimize" in c for c in calls))

    def test_cli_fallback_when_gdbus_unavailable(self):
        calls = []

        def fake_runner(cmd, **kwargs):
            calls.append(cmd)
            if cmd[0].endswith("xfce4-screensaver-command") and "-q" in cmd:
                locked = any("--lock" in c for c in calls)
                return _proc_result(
                    0, "The screensaver is active\n" if locked else "The screensaver is inactive\n"
                )
            if cmd[0].endswith("xfce4-screensaver-command") and "--lock" in cmd:
                return _proc_result(0)
            return _proc_result(1)

        def which_no_gdbus(name):
            if name == "gdbus":
                return None
            return f"/usr/bin/{name}"

        enforcer = screenlock.ScreenLockEnforcer(
            runner=fake_runner,
            which_fn=which_no_gdbus,
            sleep_fn=lambda _s: None,
        )
        ok = enforcer.enforce_session_lock(self._make_ctx("XFCE", "x11"))
        self.assertTrue(ok)
        self.assertTrue(any("--lock" in c for c in calls))

    def test_unverifiable_minimal_locker_does_not_minimize(self):
        calls = []

        def fake_runner(cmd, **kwargs):
            calls.append(cmd)
            if cmd[0].endswith("xdg-screensaver") and "lock" in cmd:
                return _proc_result(0)
            return _proc_result(1)

        def which_minimal(name):
            if name in ("xdg-screensaver", "xdotool", "wmctrl"):
                return f"/usr/bin/{name}"
            return None

        ctx = self._make_ctx("i3", "x11")
        ctx.dbus_address = ""
        enforcer = screenlock.ScreenLockEnforcer(
            runner=fake_runner,
            which_fn=which_minimal,
            sleep_fn=lambda _s: None,
        )
        ok = enforcer.enforce_session_lock(ctx, release_x11_grab=True)
        self.assertTrue(ok)
        self.assertFalse(any(c[0].endswith("wmctrl") for c in calls))
        self.assertFalse(any(c[0].endswith("xdotool") for c in calls))

    def test_drops_privileges_when_running_as_root(self):
        captured = {}

        def fake_runner(cmd, **kwargs):
            captured.update(kwargs)
            return _proc_result(0, "(true,)\n")

        ctx = self._make_ctx("X-Cinnamon")
        with mock.patch("os.geteuid", return_value=0):
            screenlock._run_as_user(
                ctx, ["/usr/bin/gdbus", "call"], timeout=2.0, runner=fake_runner
            )

        self.assertEqual(captured.get("user"), 1000)
        self.assertEqual(captured.get("group"), 1000)
        self.assertEqual(captured.get("extra_groups"), [])
        self.assertEqual(captured.get("timeout"), 2.0)

    def test_async_deduplicates_in_flight_session_locks(self):
        gate = threading.Event()
        started = threading.Event()

        class _SlowEnforcer(screenlock.ScreenLockEnforcer):
            def enforce_session_lock(self, ctx, release_x11_grab=True):
                started.set()
                gate.wait(timeout=2.0)
                return True

        enforcer = _SlowEnforcer(sleep_fn=lambda _s: None)
        ctx = self._make_ctx("X-Cinnamon")

        first = enforcer.enforce_session_lock_async(ctx)
        self.assertTrue(first)
        self.assertTrue(started.wait(timeout=1.0))

        # Second concurrent trigger for same session must be deduplicated
        second = enforcer.enforce_session_lock_async(ctx)
        self.assertFalse(second)

        gate.set()
        # Wait briefly for worker cleanup
        for _ in range(50):
            if not enforcer._in_flight:
                break
            time.sleep(0.01)
        self.assertEqual(len(enforcer._in_flight), 0)

    def test_timeout_expired_is_handled_gracefully(self):
        def timeout_runner(cmd, **kwargs):
            raise subprocess.TimeoutExpired(cmd, kwargs.get("timeout", 1.0))

        enforcer = screenlock.ScreenLockEnforcer(
            runner=timeout_runner,
            which_fn=lambda n: f"/usr/bin/{n}",
            sleep_fn=lambda _s: None,
        )
        ok = enforcer.enforce_session_lock(self._make_ctx("X-Cinnamon"))
        self.assertFalse(ok)

    def test_cinnamon_passes_sanitized_lock_message_over_dbus_and_cli(self):
        dbus_calls = []

        def fake_dbus_runner(cmd, **kwargs):
            dbus_calls.append(cmd)
            if "org.cinnamon.ScreenSaver.Lock" in cmd:
                return _proc_result(0, "()\n")
            return _proc_result(1)

        enforcer = screenlock.ScreenLockEnforcer(
            runner=fake_dbus_runner,
            which_fn=lambda n: f"/usr/bin/{n}",
            sleep_fn=lambda _s: None,
        )
        ok, method = enforcer.invoke_backend_lock(
            self._make_ctx("X-Cinnamon"),
            screenlock.BACKENDS["cinnamon"],
            lock_message="Curfew time!\n\x07  Back at 08:00 tomorrow",
        )
        self.assertTrue(ok)
        self.assertEqual(method, "dbus:org.cinnamon.ScreenSaver")
        self.assertIn("--", dbus_calls[0])
        self.assertEqual(dbus_calls[0][-1], "Curfew time! Back at 08:00 tomorrow")

        cli_calls = []

        def fake_cli_runner(cmd, **kwargs):
            cli_calls.append(cmd)
            return _proc_result(0)

        cli_enforcer = screenlock.ScreenLockEnforcer(
            runner=fake_cli_runner,
            which_fn=lambda n: None if n == "gdbus" else f"/usr/bin/{n}",
            sleep_fn=lambda _s: None,
        )
        ok2, method2 = cli_enforcer.invoke_backend_lock(
            self._make_ctx("X-Cinnamon"),
            screenlock.BACKENDS["cinnamon"],
            lock_message="Screen time finished for today",
        )
        self.assertTrue(ok2)
        self.assertEqual(method2, "cli:cinnamon-screensaver-command")
        self.assertIn("--away-message=Screen time finished for today", cli_calls[0])

    def test_verify_session_lock_and_async_completion_callback(self):
        state = {"active": True}

        def fake_runner(cmd, **kwargs):
            if "org.freedesktop.DBus.NameHasOwner" in cmd:
                return _proc_result(
                    0, "(true,)\n" if cmd[-1] == "org.cinnamon.ScreenSaver" else "(false,)\n"
                )
            if "org.cinnamon.ScreenSaver.GetActive" in cmd:
                return _proc_result(0, "(true,)\n" if state["active"] else "(false,)\n")
            if "org.cinnamon.ScreenSaver.Lock" in cmd:
                state["active"] = True
                return _proc_result(0, "()\n")
            return _proc_result(1)

        enforcer = screenlock.ScreenLockEnforcer(
            runner=fake_runner,
            which_fn=lambda n: f"/usr/bin/{n}",
            sleep_fn=lambda _s: None,
        )
        ctx = self._make_ctx("X-Cinnamon")

        res_locked = enforcer.verify_session_lock_detailed(ctx)
        self.assertTrue(res_locked.locked)
        self.assertTrue(res_locked.verified)
        self.assertEqual(res_locked.stage, "verify")

        state["active"] = False
        done = threading.Event()
        captured = []

        def _on_done(res):
            captured.append(res)
            done.set()

        self.assertTrue(enforcer.verify_session_lock_async(ctx, on_complete=_on_done))
        self.assertTrue(done.wait(timeout=2.0))
        self.assertFalse(captured[0].locked)
        self.assertTrue(captured[0].verified)
        self.assertEqual(captured[0].stage, "verify_unlocked")

        done.clear()
        captured.clear()
        self.assertTrue(
            enforcer.enforce_session_lock_async(ctx, release_x11_grab=False, on_complete=_on_done)
        )
        self.assertTrue(done.wait(timeout=2.0))
        self.assertTrue(captured[0].locked)
        self.assertTrue(captured[0].verified)

    def test_gdbus_timeout_headroom_and_default_lock_timeout(self):
        captured: list[tuple[list[str], float]] = []

        def fake_runner(cmd, **kwargs):
            captured.append((list(cmd), float(kwargs.get("timeout", 0.0))))
            if "org.freedesktop.DBus.NameHasOwner" in cmd:
                return _proc_result(0, "(true,)\n")
            if "org.cinnamon.ScreenSaver.GetActive" in cmd:
                return _proc_result(0, "(false,)\n")
            if "org.cinnamon.ScreenSaver.Lock" in cmd:
                return _proc_result(0, "()\n")
            return _proc_result(1)

        enforcer = screenlock.ScreenLockEnforcer(
            runner=fake_runner,
            which_fn=lambda n: f"/usr/bin/{n}",
            sleep_fn=lambda _s: None,
        )
        ctx = self._make_ctx("X-Cinnamon")
        enforcer.is_screensaver_active(ctx, screenlock.select_backends("X-Cinnamon"))
        ok, _ = enforcer.invoke_backend_lock(ctx, screenlock.BACKENDS["cinnamon"])
        self.assertTrue(ok)

        for cmd, subproc_timeout in captured:
            if "--timeout" in cmd:
                gdbus_timeout = float(cmd[cmd.index("--timeout") + 1])
                self.assertGreaterEqual(
                    subproc_timeout,
                    gdbus_timeout + 1.0,
                    f"subprocess timeout {subproc_timeout}s must exceed gdbus --timeout {gdbus_timeout}s",
                )
        lock_calls = [(c, t) for c, t in captured if "org.cinnamon.ScreenSaver.Lock" in c]
        self.assertEqual(len(lock_calls), 1)
        self.assertEqual(lock_calls[0][0][lock_calls[0][0].index("--timeout") + 1], "4")
        self.assertEqual(lock_calls[0][1], 5.0)

    def test_is_screensaver_active_skips_alias_and_cli_when_owned_backend_times_out(self):
        calls: list[list[str]] = []

        def fake_runner(cmd, **kwargs):
            calls.append(list(cmd))
            if "org.freedesktop.DBus.NameHasOwner" in cmd:
                return _proc_result(0, "(true,)\n")
            if "org.cinnamon.ScreenSaver.GetActive" in cmd:
                return _proc_result(1, "", "Error: Timeout was reached\n")
            return _proc_result(1)

        enforcer = screenlock.ScreenLockEnforcer(
            runner=fake_runner,
            which_fn=lambda n: f"/usr/bin/{n}",
            sleep_fn=lambda _s: None,
        )
        active, backend = enforcer.is_screensaver_active(
            self._make_ctx("X-Cinnamon"), screenlock.select_backends("X-Cinnamon")
        )
        self.assertIsNone(active)
        self.assertIsNone(backend)
        # Must NOT probe org.freedesktop.ScreenSaver alias or hang on cinnamon-screensaver-command -q
        self.assertFalse(any("org.freedesktop.ScreenSaver" in c for c in calls))
        self.assertFalse(any(c[0].endswith("cinnamon-screensaver-command") for c in calls))

    def test_helper_timeout_logs_info_not_warning(self):
        def timeout_runner(cmd, **kwargs):
            raise subprocess.TimeoutExpired(cmd, kwargs.get("timeout", 1.0))

        with self.assertLogs("curfewd.screenlock", level="INFO") as cm:
            res = screenlock._run_as_user(
                self._make_ctx("X-Cinnamon"),
                ["/usr/bin/gdbus", "call"],
                timeout=3.0,
                runner=timeout_runner,
            )
        self.assertIsNone(res)
        self.assertTrue(any(rec.levelname == "INFO" for rec in cm.records))
        self.assertFalse(any(rec.levelname == "WARNING" for rec in cm.records))


class TestLiveDBusIntegration(unittest.TestCase):
    """End-to-end wire test against a real ephemeral dbus-daemon using real /usr/bin/gdbus."""

    @unittest.skipUnless(
        screenlock._resolve_trusted_bin("gdbus") and screenlock._resolve_trusted_bin("dbus-daemon"),
        "requires gdbus and dbus-daemon",
    )
    def test_real_gdbus_locks_and_verifies_cinnamon_service(self):
        with tempfile.TemporaryDirectory() as tmp:
            sock_path = os.path.join(tmp, "bus")
            bus_addr = f"unix:path={sock_path}"
            daemon = subprocess.Popen(
                ["dbus-daemon", "--session", f"--address={bus_addr}", "--nofork", "--nopidfile"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            server = None
            try:
                # Wait for socket
                for _ in range(50):
                    if os.path.exists(sock_path):
                        break
                    time.sleep(0.02)
                self.assertTrue(os.path.exists(sock_path))

                # Spawn minimal mock org.cinnamon.ScreenSaver D-Bus service
                mock_service_code = """
import dbus, dbus.service
from dbus.mainloop.glib import DBusGMainLoop
from gi.repository import GLib

DBusGMainLoop(set_as_default=True)
bus = dbus.SessionBus()
name = dbus.service.BusName("org.cinnamon.ScreenSaver", bus)

class FakeCinnamonSS(dbus.service.Object):
    def __init__(self):
        super().__init__(bus, "/org/cinnamon/ScreenSaver")
        self.active = False

    @dbus.service.method("org.cinnamon.ScreenSaver", in_signature="s", out_signature="")
    def Lock(self, msg):
        self.active = True

    @dbus.service.method("org.cinnamon.ScreenSaver", in_signature="", out_signature="b")
    def GetActive(self):
        return dbus.Boolean(self.active)

obj = FakeCinnamonSS()
print("READY", flush=True)
GLib.MainLoop().run()
"""
                srv_env = os.environ.copy()
                srv_env["DBUS_SESSION_BUS_ADDRESS"] = bus_addr
                server = subprocess.Popen(
                    ["/usr/bin/python3", "-c", mock_service_code],
                    env=srv_env,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                )
                ready_line = server.stdout.readline().strip()
                self.assertEqual(ready_line, "READY")

                ctx = screenlock.SessionContext(
                    session_id="c1",
                    uid=os.getuid(),
                    username="testuser",
                    gid=os.getgid(),
                    home_dir=tmp,
                    session_type="x11",
                    display=":0",
                    xdg_runtime_dir=tmp,
                    dbus_address=bus_addr,
                    desktop="X-Cinnamon",
                )
                enforcer = screenlock.ScreenLockEnforcer(
                    logind_settle_sec=0.0,
                    verify_settle_sec=0.05,
                    grab_settle_sec=0.0,
                )
                # Initially inactive -> Lock("") -> verified active via GetActive()
                self.assertTrue(enforcer.enforce_session_lock(ctx, release_x11_grab=False))
                # Confirm active directly
                active, be = enforcer.is_screensaver_active(
                    ctx, screenlock.select_backends("X-Cinnamon")
                )
                self.assertTrue(active)
                self.assertEqual(be, "cinnamon")
            finally:
                if server is not None:
                    server.terminate()
                    server.wait(timeout=3)
                    if server.stdout:
                        server.stdout.close()
                    if server.stderr:
                        server.stderr.close()
                daemon.terminate()
                daemon.wait(timeout=3)


class TestLockScreenMessage(unittest.TestCase):
    def _make_ctx(self, desktop: str = "X-Cinnamon") -> screenlock.SessionContext:
        return screenlock.SessionContext(
            session_id="c1",
            uid=1000,
            username="alice",
            gid=1000,
            home_dir="/home/alice",
            session_type="x11",
            display=":0",
            dbus_address="unix:path=/run/user/1000/bus",
            desktop=desktop,
        )

    def test_refuses_to_lock_when_screensaver_inactive(self):
        called_argvs: list[list[str]] = []

        def fake_run(argv, **_kwargs):
            called_argvs.append(list(argv))
            if any("GetActive" in a for a in argv):
                return subprocess.CompletedProcess(argv, 0, stdout="(false,)\n", stderr="")
            return subprocess.CompletedProcess(argv, 0, stdout="()", stderr="")

        enforcer = screenlock.ScreenLockEnforcer(
            which_fn=lambda cmd: f"/usr/bin/{cmd}",
            runner=fake_run,
            sleep_fn=lambda _s: None,
        )
        ok = enforcer.update_lock_message(self._make_ctx(), "Bonus time added (+15m)")
        self.assertFalse(ok)
        self.assertFalse(any("Lock" in a for argv in called_argvs for a in argv))

    def test_updates_cinnamon_away_message_when_locked_via_dbus_and_cli_fallback(self):
        called_argvs: list[list[str]] = []

        def fake_run(argv, **_kwargs):
            called_argvs.append(list(argv))
            if any("NameHasOwner" in a or "GetActive" in a for a in argv):
                return subprocess.CompletedProcess(argv, 0, stdout="(true,)\n", stderr="")
            if "gdbus" in argv[0] and "org.cinnamon.ScreenSaver.Lock" in argv:
                return subprocess.CompletedProcess(argv, 0, stdout="()", stderr="")
            return subprocess.CompletedProcess(argv, 1, stdout="", stderr="err")

        enforcer = screenlock.ScreenLockEnforcer(
            which_fn=lambda cmd: f"/usr/bin/{cmd}",
            runner=fake_run,
            sleep_fn=lambda _s: None,
        )
        ok = enforcer.update_lock_message(self._make_ctx(), "Bonus time added\n(+15m)\x07")
        self.assertTrue(ok)
        lock_calls = [a for a in called_argvs if "org.cinnamon.ScreenSaver.Lock" in a]
        self.assertEqual(len(lock_calls), 1)
        self.assertEqual(lock_calls[0][-1], "Bonus time added (+15m)")

        # CLI fallback when gdbus fails
        called_argvs.clear()

        def fake_run_cli_fallback(argv, **_kwargs):
            called_argvs.append(list(argv))
            if argv[0].endswith("cinnamon-screensaver-command") and "-q" in argv:
                return subprocess.CompletedProcess(
                    argv, 0, stdout="The screensaver is active\n", stderr=""
                )
            if argv[0].endswith("cinnamon-screensaver-command") and any(
                a.startswith("--away-message=") for a in argv
            ):
                return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")
            return subprocess.CompletedProcess(argv, 1, stdout="", stderr="dbus failed")

        enforcer2 = screenlock.ScreenLockEnforcer(
            which_fn=lambda cmd: f"/usr/bin/{cmd}",
            runner=fake_run_cli_fallback,
            sleep_fn=lambda _s: None,
        )
        self.assertTrue(enforcer2.update_lock_message(self._make_ctx(), "Bonus time added (+15m)"))
        self.assertTrue(
            any(
                a[0].endswith("cinnamon-screensaver-command")
                and "--away-message=Bonus time added (+15m)" in a
                for a in called_argvs
            )
        )


if __name__ == "__main__":
    unittest.main()
