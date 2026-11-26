"""Unit tests for curfewctl CLI commands, --json output, exit codes, and doctor --fix."""

import io
import json
import os
import tempfile
import unittest

from curfewd import (
    EXIT_DAEMON_UNAVAILABLE,
    EXIT_OK,
    EXIT_PERMISSION_DENIED,
    EXIT_USAGE,
)
from curfewd.cli.main import run_cli
from curfewd.dbus.client import DaemonUnavailableError, PermissionDeniedError
from curfewd.engine.doctor import apply_dconf_lock_fix, check_dconf_lock_policy
from curfewd.models import TimeWindow, UserPolicy
from curfewd.parsing import WEEKDAYS


class _StubClient:
    def __init__(self) -> None:
        self.policy = UserPolicy(
            username="alice",
            daily_limits={d: 3600 for d in WEEKDAYS},
            curfew_windows={d: [TimeWindow("08:00", "21:00")] for d in WEEKDAYS},
        ).to_dict()
        self.time_left = 3600
        self.bonus = 0
        self.config = {"log_level": "INFO", "poll_interval_sec": 3, "session_lock_fallback": True}
        self.last_grant = None

    def _status(self):
        return {
            "username": "alice",
            "full_name": "Alice",
            "logged_in": True,
            "active": True,
            "screen_locked": False,
            "enforcement_state": "paused" if self.policy["pause"]["active"] else "ok",
            "unaccounted_now": False,
            "hide_tray_effective": self.policy["pause"]["active"],
            "time_left_today": self.time_left,
            "time_left_continuous": self.time_left,
            "daily_limit_today": self.policy["daily_limits"]["wed"],
            "time_spent_today": 0,
            "time_spent_balance": 0,
            "time_spent_week": 1200,
            "time_spent_month": 3600,
            "bonus_today": self.bonus,
            "next_curfew_at": "2026-11-25T21:00:00+00:00",
            "next_allowed_at": None,
            "sessions": [],
            "pause": self.policy["pause"],
            "playtime_status": {"enabled": self.policy["playtime"]["enabled"], "time_left_today": 0, "time_spent_today": 0, "active_apps": []},
            "policy": self.policy,
        }

    def list_users(self):
        st = self._status()
        return [{
            "username": "alice",
            "enforcement_state": st["enforcement_state"],
            "time_left_today": st["time_left_today"],
            "time_spent_today": st["time_spent_today"],
            "daily_limit_today": st["daily_limit_today"],
            "lockout_action": self.policy["lockout_action"],
            "logged_in": True,
            "screen_locked": False,
        }]

    def get_user_status(self, username):
        return self._status()

    def set_user_policy(self, username, policy):
        self.policy = policy
        return self._status()

    def grant_time(self, username, delta, mode="add"):
        self.last_grant = (username, delta, mode)
        if mode == "set":
            self.time_left = delta
        else:
            self.time_left = max(0, self.time_left + delta)
            self.bonus += delta
        return {"applied_sec": delta, "status": self._status()}

    def pause_user(self, username, until_utc="", reason="", hide_tray=True):
        self.policy["pause"] = {"active": True, "mode": "until" if until_utc else "indefinite", "until_utc": until_utc or None, "reason": reason, "hide_tray_while_paused": hide_tray}
        return self._status()

    def resume_user(self, username):
        self.policy["pause"] = {"active": False, "mode": "none", "until_utc": None, "reason": "", "hide_tray_while_paused": True}
        return self._status()

    def lock_user(self, username):
        return {"username": username, "locked": True, "verified": True, "sessions": [{"session_id": "c1", "backend": "dbus:org.cinnamon.ScreenSaver", "stage": "dbus_or_cli", "elapsed_ms": 50}]}

    def get_usage_history(self, username, days=7):
        return {"username": username, "days": days, "start_date": "2026-11-19", "daily": [{"date": "2026-11-25", "active_sec": 1800, "inactive_sec": 300, "playtime_sec": 600, "bonus_sec": 0, "lockouts": 1}], "totals": {"active_sec": 1800}, "events": []}

    def get_daemon_config(self):
        return dict(self.config)

    def set_daemon_config(self, cfg):
        self.config = dict(cfg)
        return dict(self.config)

    def run_doctor(self):
        return {"version": "0.7.0", "overall": "ok", "checks": [{"name": "dbus_service", "status": "ok", "detail": "ok", "fixable": False}], "active_sessions": []}


class TestCurfewctlCLI(unittest.TestCase):
    def _run(self, argv, stub=None):
        client = stub or _StubClient()
        out, err = io.StringIO(), io.StringIO()
        rc = run_cli(argv, client_factory=lambda _sess: client, stdout=out, stderr=err)
        return rc, out.getvalue(), err.getvalue(), client

    def test_status_users_and_json_flag_positions(self):
        rc, out, _, _ = self._run(["status", "alice", "--json"])
        self.assertEqual(rc, EXIT_OK)
        data = json.loads(out)
        self.assertEqual(data["username"], "alice")

        rc2, out2, _, _ = self._run(["--json", "users"])
        self.assertEqual(rc2, EXIT_OK)
        self.assertEqual(json.loads(out2)["users"][0]["username"], "alice")

    def test_grant_supports_positive_negative_and_set_without_double_dash(self):
        stub = _StubClient()
        rc, out, _, _ = self._run(["grant", "alice", "+30m", "--json"], stub)
        self.assertEqual(rc, EXIT_OK)
        self.assertEqual(stub.last_grant, ("alice", 1800, "add"))

        rc, out, _, _ = self._run(["grant", "alice", "-15m", "--json"], stub)
        self.assertEqual(rc, EXIT_OK)
        self.assertEqual(stub.last_grant, ("alice", -900, "add"))

        rc, out, _, _ = self._run(["grant", "alice", "=2h"], stub)
        self.assertEqual(rc, EXIT_OK)
        self.assertEqual(stub.last_grant, ("alice", 7200, "set"))
        self.assertIn("2h", out)

    def test_limit_window_lockout_pause_resume_lock_playtime_history_config(self):
        stub = _StubClient()
        self.assertEqual(self._run(["limit", "alice", "1h30m", "--days", "mon-fri"], stub)[0], EXIT_OK)
        self.assertEqual(stub.policy["daily_limits"]["mon"], 5400)
        self.assertEqual(stub.policy["daily_limits"]["sat"], 3600)

        self.assertEqual(self._run(["window", "alice", "07:30-20:30", "--days", "sun-thu"], stub)[0], EXIT_OK)
        self.assertEqual(stub.policy["curfew_windows"]["sun"][0]["start"], "07:30")

        self.assertEqual(self._run(["lockout", "alice", "suspend"], stub)[0], EXIT_OK)
        self.assertEqual(stub.policy["lockout_action"], "suspend")

        self.assertEqual(self._run(["pause", "alice", "--for", "2h", "--reason", "project"], stub)[0], EXIT_OK)
        self.assertTrue(stub.policy["pause"]["active"])
        self.assertEqual(stub.policy["pause"]["reason"], "project")

        self.assertEqual(self._run(["resume", "alice"], stub)[0], EXIT_OK)
        self.assertFalse(stub.policy["pause"]["active"])

        rc, out, _, _ = self._run(["lock", "alice", "--json"], stub)
        self.assertEqual(rc, EXIT_OK)
        self.assertTrue(json.loads(out)["locked"])

        self.assertEqual(self._run(["playtime", "alice", "enable"], stub)[0], EXIT_OK)
        self.assertEqual(self._run(["playtime", "alice", "add", "steam", "Steam"], stub)[0], EXIT_OK)
        self.assertEqual(self._run(["playtime", "alice", "limit", "45m", "--days", "all"], stub)[0], EXIT_OK)
        self.assertEqual(stub.policy["playtime"]["apps"][0]["pattern"], "steam")
        self.assertEqual(stub.policy["playtime"]["daily_limits"]["mon"], 2700)

        rc, out, _, _ = self._run(["history", "alice", "--days", "7"], stub)
        self.assertEqual(rc, EXIT_OK)
        self.assertIn("2026-11-25", out)

        rc, out, _, _ = self._run(["config", "set", "session_lock_fallback", "false", "--json"], stub)
        self.assertEqual(rc, EXIT_OK)
        self.assertFalse(json.loads(out)["session_lock_fallback"])

    def test_standardized_exit_codes(self):
        class _DeniedClient(_StubClient):
            def list_users(self):
                raise PermissionDeniedError("polkit denied")

        class _DownClient(_StubClient):
            def list_users(self):
                raise DaemonUnavailableError("not running")

        self.assertEqual(self._run(["users"], _DeniedClient())[0], EXIT_PERMISSION_DENIED)
        self.assertEqual(self._run(["users"], _DownClient())[0], EXIT_DAEMON_UNAVAILABLE)
        self.assertEqual(self._run(["limit", "alice", "not-a-duration"])[0], EXIT_USAGE)

    def test_doctor_dconf_check_and_fix_in_temp_root(self):
        with tempfile.TemporaryDirectory() as tmp:
            before = check_dconf_lock_policy(root_prefix=tmp)
            self.assertEqual(before["status"], "warn")
            self.assertTrue(before["fixable"])

            res = apply_dconf_lock_fix(root_prefix=tmp)
            self.assertTrue(res["applied"])
            after = check_dconf_lock_policy(root_prefix=tmp)
            self.assertEqual(after["status"], "ok")
            with open(os.path.join(tmp, "etc/dconf/db/local.d/locks/curfewd-lockscreen"), "r", encoding="utf-8") as fh:
                self.assertIn("/org/cinnamon/desktop/screensaver/lock-enabled", fh.read())


if __name__ == "__main__":
    unittest.main()
