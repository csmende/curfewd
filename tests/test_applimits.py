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

    def test_scan_user_processes_flatpak_steam_foreground_ancestry_and_passive(self):
        import os
        import tempfile

        from curfewd.models import CategoryLimitRule

        with tempfile.TemporaryDirectory() as fake_proc:
            # PID 301: Flatpak Sober (Roblox)
            os.makedirs(os.path.join(fake_proc, "301"))
            with open(os.path.join(fake_proc, "301", "cgroup"), "w", encoding="utf-8") as fh:
                fh.write("0::/user.slice/app-flatpak-org.vinegarhq.Sober-1234.scope\n")
            # PID 302: Steam Proton game with SteamAppId=413150 (Stardew Valley)
            os.makedirs(os.path.join(fake_proc, "302"))
            with open(os.path.join(fake_proc, "302", "environ"), "wb") as fh:
                fh.write(b"HOME=/home/alice\x00SteamAppId=413150\x00")
            # PID 303: PrismLauncher -> child PID 304 (java)
            os.makedirs(os.path.join(fake_proc, "303"))
            os.makedirs(os.path.join(fake_proc, "304"))

            def _p(pid: int, ppid: int, name: str, cmd: list[str]):
                p = _fake_proc(pid, 1000, name, f"/usr/bin/{name}", cmd)
                p.info["ppid"] = ppid
                return p

            procs = [
                _p(301, 1, "sober", ["sober"]),
                _p(302, 1, "wine64-preloader", ["wine64-preloader", "game.exe"]),
                _p(303, 1, "prismlauncher", ["prismlauncher"]),
                _p(304, 303, "java", ["java", "-jar", "minecraft.jar"]),
                _p(305, 1, "discord", ["discord"]),
            ]
            rules = [
                AppLimitRule(
                    "roblox_fp",
                    "Roblox",
                    daily_limit_sec=1800,
                    category="Gaming",
                    flatpak_id="org.vinegarhq.Sober",
                ),
                AppLimitRule(
                    "stardew",
                    "Stardew Valley",
                    daily_limit_sec=1800,
                    category="Gaming",
                    steam_app_id="413150",
                ),
                AppLimitRule("prismlauncher", "Minecraft", daily_limit_sec=1800, category="Gaming"),
                AppLimitRule(
                    "discord",
                    "Discord",
                    daily_limit_sec=1800,
                    category="Social",
                    foreground_only=True,
                ),
            ]
            cats = [CategoryLimitRule("Gaming", daily_limit_sec=3600)]
            monitor = AppLimitsMonitor(proc_root=fake_proc)
            with mock.patch("curfewd.engine.applimits.psutil") as fake_psutil:
                fake_psutil.Error = Exception
                fake_psutil.process_iter.return_value = procs
                # Active window is PID 304 (Minecraft java) -> Discord (minimized PID 305) skipped!
                res = monitor.scan_user_processes(
                    1000, rules, category_limits=cats, active_window_pid=304
                )

        matched_pats = set(res.running_patterns)
        self.assertIn("roblox_fp", matched_pats)
        self.assertIn("stardew", matched_pats)
        self.assertIn("prismlauncher", matched_pats)
        self.assertNotIn("discord", matched_pats)  # foreground_only prevented minimized drain!
        # Child PID 304 inherited prismlauncher rule!
        self.assertIn(304, [m["pid"] for m in res.rule_matches if m["pattern"] == "prismlauncher"])
        self.assertIn("Gaming", res.running_categories)
        passive_keys = {k for k, _, _ in res.passive_apps}
        self.assertIn("steam:413150", passive_keys)


if __name__ == "__main__":
    unittest.main()
