"""Unit tests for curfewd security hardening (CSPRNG challenge keys, symlink-safe PID/log I/O)."""

import fcntl
import os
import string
import sys
import tempfile
import types
import unittest
from unittest import mock

_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if "timekpr" not in sys.modules:
    _pkg = types.ModuleType("timekpr")
    _pkg.__path__ = [_REPO_ROOT]
    _pkg.__package__ = "timekpr"
    sys.modules["timekpr"] = _pkg


class _FakeNotifier:
    def __init__(self):
        self.calls = []

    def procesSessionAttributes(self, what, key):
        self.calls.append((what, key))


class TestSessionAttributeVerification(unittest.TestCase):
    def _make_user(self):
        from timekpr.common.constants import constants as cons
        from timekpr.server.user.userdata import timekprUser

        user = timekprUser.__new__(timekprUser)
        user._timekprUserData = {
            cons.TK_CTRL_SCR_N: False,
            cons.TK_CTRL_SCR_K: None,
        }
        user._timekprUserNotification = _FakeNotifier()
        return user, cons

    def test_generates_csprng_key_and_verifies_once(self):
        user, cons = self._make_user()

        user.processUserSessionAttributes(cons.TK_CTRL_SCR_N, "", "")
        self.assertEqual(len(user._timekprUserNotification.calls), 1)
        what, key = user._timekprUserNotification.calls[0]
        self.assertEqual(what, cons.TK_CTRL_SCR_N)
        self.assertEqual(len(key), 16)
        allowed = set(string.ascii_uppercase + string.digits)
        self.assertTrue(set(key).issubset(allowed))
        self.assertEqual(user._timekprUserData[cons.TK_CTRL_SCR_K], key)

        # Valid verification succeeds and consumes the one-time key.
        user.processUserSessionAttributes(cons.TK_CTRL_SCR_N, key, "true")
        self.assertTrue(user._timekprUserData[cons.TK_CTRL_SCR_N])
        self.assertIsNone(user._timekprUserData[cons.TK_CTRL_SCR_K])

        # Replaying the same key must fail.
        user.processUserSessionAttributes(cons.TK_CTRL_SCR_N, key, "true")
        self.assertFalse(user._timekprUserData[cons.TK_CTRL_SCR_N])

    def test_wrong_key_rejects_and_clears_challenge(self):
        user, cons = self._make_user()

        user.processUserSessionAttributes(cons.TK_CTRL_SCR_N, "", "")
        issued_key = user._timekprUserData[cons.TK_CTRL_SCR_K]
        wrong_key = "0" * 16 if issued_key != "0" * 16 else "1" * 16

        user.processUserSessionAttributes(cons.TK_CTRL_SCR_N, wrong_key, "true")
        self.assertFalse(user._timekprUserData[cons.TK_CTRL_SCR_N])
        self.assertIsNone(user._timekprUserData[cons.TK_CTRL_SCR_K])


class TestPidAndLogSymlinkHardening(unittest.TestCase):
    def tearDown(self):
        from timekpr.common.utils import misc

        if misc._LOCK_FILE is not None:
            try:
                misc._LOCK_FILE.close()
            except OSError:
                pass
            misc._LOCK_FILE = None

    def test_pid_file_refuses_symlink_and_falls_through(self):
        from timekpr.common.constants import constants as cons
        from timekpr.common.utils import misc

        with tempfile.TemporaryDirectory() as tmpdir:
            victim = os.path.join(tmpdir, "victim.txt")
            with open(victim, "w", encoding="utf-8") as fh:
                fh.write("ORIGINAL")

            dir_bad = os.path.join(tmpdir, "dir_bad")
            dir_good = os.path.join(tmpdir, "dir_good")
            os.mkdir(dir_bad)
            os.mkdir(dir_good)
            os.symlink(victim, os.path.join(dir_bad, "curfewd-test.alice.pid"))

            with mock.patch.object(cons, "TK_LOG_PID_DIRS", [dir_bad, dir_good]):
                running = misc.checkAndSetRunning("curfewd-test", "alice")

            self.assertFalse(running)
            with open(victim, "r", encoding="utf-8") as fh:
                self.assertEqual(fh.read(), "ORIGINAL")
            good_pid = os.path.join(dir_good, "curfewd-test.alice.pid")
            self.assertTrue(os.path.isfile(good_pid))
            with open(good_pid, "r", encoding="utf-8") as fh:
                self.assertEqual(fh.read().strip(), str(os.getpid()))

    def test_existing_locked_pid_file_is_not_truncated(self):
        from timekpr.common.constants import constants as cons
        from timekpr.common.utils import misc

        with tempfile.TemporaryDirectory() as tmpdir:
            pid_path = os.path.join(tmpdir, "curfewd-locktest.pid")
            with open(pid_path, "w", encoding="utf-8") as holder:
                fcntl.flock(holder, fcntl.LOCK_EX | fcntl.LOCK_NB)
                holder.write("999999")
                holder.flush()

                with mock.patch.object(cons, "TK_LOG_PID_DIRS", [tmpdir]):
                    running = misc.checkAndSetRunning("curfewd-locktest")

                self.assertTrue(running)
                with open(pid_path, "r", encoding="utf-8") as reader:
                    self.assertEqual(reader.read(), "999999")

    def test_root_skips_shared_temp_dir_for_pid_file(self):
        from timekpr.common.constants import constants as cons
        from timekpr.common.utils import misc

        with tempfile.TemporaryDirectory() as tmpdir:
            fake_temp = os.path.join(tmpdir, "shared_tmp")
            safe_run = os.path.join(tmpdir, "safe_run")
            os.mkdir(fake_temp)
            os.mkdir(safe_run)

            with (
                mock.patch("timekpr.common.utils.misc.os.geteuid", return_value=0),
                mock.patch.object(cons, "TK_LOG_TEMP_DIR", fake_temp),
                mock.patch.object(cons, "TK_LOG_PID_DIRS", [fake_temp, safe_run]),
            ):
                running = misc.checkAndSetRunning("curfewd-roottest")

            self.assertFalse(running)
            self.assertFalse(os.path.exists(os.path.join(fake_temp, "curfewd-roottest.pid")))
            self.assertTrue(os.path.isfile(os.path.join(safe_run, "curfewd-roottest.pid")))

    def test_file_log_flush_refuses_symlink(self):
        from timekpr.common.constants import constants as cons
        from timekpr.common.log import log

        with tempfile.TemporaryDirectory() as tmpdir:
            victim = os.path.join(tmpdir, "victim.log")
            with open(victim, "w", encoding="utf-8") as fh:
                fh.write("SAFE")

            symlink_log = os.path.join(tmpdir, "timekpr.log")
            os.symlink(victim, symlink_log)

            orig_facility = log._LOG_FACILITY
            orig_file = log._LOG_FILE
            orig_buf = list(log._LOG_BUFFER)
            try:
                log._LOG_FACILITY = cons.TK_LOG_FACILITY_TKL
                log._LOG_FILE = symlink_log
                log._LOG_BUFFER[:] = [{"l": cons.TK_LOG_LEVEL_INFO, "m": "SHOULD_NOT_WRITE\n"}]
                with mock.patch.object(log, "consoleOut"):
                    log._flushLogFile()
            finally:
                log._LOG_FACILITY = orig_facility
                log._LOG_FILE = orig_file
                log._LOG_BUFFER[:] = orig_buf

            with open(victim, "r", encoding="utf-8") as fh:
                self.assertEqual(fh.read(), "SAFE")


if __name__ == "__main__":
    unittest.main()
