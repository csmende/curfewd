"""Unit tests for curfewd.engine.applimits.AppLimitsMonitor."""

import unittest
from types import SimpleNamespace
from unittest import mock

from curfewd.engine.applimits import AppLimitsMonitor
from curfewd.models import AppLimitRule


def _fake_proc(pid: int, uid: int, name: str, exe: str = "", cmdline: list[str] | None = None):
    proc = mock.MagicMock()
    proc.info = {
        "pid": pid,
        "uids": SimpleNamespace(real=uid),
        "name": name,
        "exe": exe,
        "cmdline": cmdline or [name],
    }
    proc.uids.return_value = SimpleNamespace(real=uid)
    return proc


class TestAppLimitsMonitor(unittest.TestCase):
    def test_matches_only_target_uid_and_handles_regex_plus_invalid_regex_fallback(self):
        rules = [
            AppLimitRule("steam|proton", "Steam", daily_limit_sec=1800, category="Gaming"),
            AppLimitRule("[invalid-regex", "BrokenRegexApp", daily_limit_sec=900, category="App"),
        ]
        procs = [
            _fake_proc(101, 1000, "steam", "/usr/bin/steam", ["/usr/bin/steam"]),
            _fake_proc(102, 1001, "steam", "/usr/bin/steam", ["/usr/bin/steam"]),
            _fake_proc(
                103, 1000, "python3", "/usr/bin/python3", ["python3", "run", "[invalid-regex"]
            ),
            _fake_proc(104, 1000, "bash", "/bin/bash", ["bash"]),
        ]

        monitor = AppLimitsMonitor(term_grace_ticks=2)
        with mock.patch("curfewd.engine.applimits.psutil") as fake_psutil:
            fake_psutil.Error = Exception
            fake_psutil.process_iter.return_value = procs
            matches = monitor.find_matching_processes(1000, rules)

        self.assertEqual([m["pid"] for m in matches], [101, 103])
        self.assertEqual([m["label"] for m in matches], ["Steam", "BrokenRegexApp"])

    def test_enforce_kill_escalates_from_sigterm_to_sigkill_and_cleans_stale_pids(self):
        rules = [AppLimitRule("steam", "Steam", daily_limit_sec=600, category="Gaming")]
        proc_live = _fake_proc(201, 1000, "steam", "/usr/bin/steam")
        proc_other_uid = _fake_proc(202, 1000, "steam", "/usr/bin/steam")
        # Simulate PID reuse race where UID changed between scan and kill
        proc_other_uid.uids.return_value = SimpleNamespace(real=2000)

        monitor = AppLimitsMonitor(term_grace_ticks=2)
        with mock.patch("curfewd.engine.applimits.psutil") as fake_psutil:
            fake_psutil.Error = Exception
            fake_psutil.process_iter.return_value = [proc_live, proc_other_uid]
            fake_psutil.Process.side_effect = lambda pid: (
                proc_live if pid == 201 else proc_other_uid
            )

            # Tick 1 & Tick 2 -> SIGTERM; PID 202 skipped due to UID mismatch
            self.assertEqual(monitor.enforce_kill(1000, rules), [201])
            self.assertEqual(monitor.enforce_kill(1000, rules), [201])
            self.assertEqual(proc_live.terminate.call_count, 2)
            self.assertEqual(proc_live.kill.call_count, 0)
            self.assertEqual(proc_other_uid.terminate.call_count, 0)

            # Tick 3 (> term_grace_ticks) -> SIGKILL escalation
            self.assertEqual(monitor.enforce_kill(1000, rules), [201])
            self.assertEqual(proc_live.kill.call_count, 1)

            # Once process exits, stale attempt counter is evicted
            fake_psutil.process_iter.return_value = []
            self.assertEqual(monitor.enforce_kill(1000, rules), [])
            self.assertEqual(monitor._term_attempts, {})

    def test_returns_empty_when_no_rules_or_psutil_missing(self):
        monitor = AppLimitsMonitor()
        self.assertEqual(monitor.find_matching_processes(1000, []), [])
        with mock.patch("curfewd.engine.applimits.psutil", None):
            self.assertEqual(
                monitor.find_matching_processes(1000, [AppLimitRule("steam", "Steam")]),
                [],
            )


if __name__ == "__main__":
    unittest.main()
