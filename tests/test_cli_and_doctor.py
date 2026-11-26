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
from curfewd.engine.doctor import (
    apply_all_lock_fixes,
    apply_dconf_lock_fix,
    apply_kde_lock_fix,
    apply_xfce_lock_fix,
    check_dconf_lock_policy,
    check_kde_lock_policy,
    check_xfce_lock_policy,
    run_doctor,
)
from curfewd.models import TimeWindow, UserPolicy
from curfewd.parsing import WEEKDAYS


class _StubClient:
    def __init__(self) -> None:
        self.policy = UserPolicy(
            username="alice",
            daily_limits=dict.fromkeys(WEEKDAYS, 3600),
            curfew_windows={d: [TimeWindow("08:00", "21:00")] for d in WEEKDAYS},
        ).to_dict()
        self.time_left = 3600
        self.bonus = 0
        self.config = {"log_level": "INFO", "poll_interval_sec": 3, "session_lock_fallback": True}
        self.last_grant = None
        self.today_override = {"active": False, "limit_sec": None, "windows": None}
        self.app_grants = []

    def _status(self):
        return {
            "username": "alice",
            "full_name": "Alice",
            "logged_in": True,
            "active": True,
            "screen_locked": False,
            "enforcement_state": "paused" if self.policy["pause"]["active"] else "ok",
            "hide_tray_effective": self.policy["pause"]["active"],
            "time_left_today": self.time_left,
            "time_left_continuous": self.time_left,
            "daily_limit_today": self.today_override["limit_sec"]
            if self.today_override["limit_sec"] is not None
            else self.policy["daily_limits"]["wed"],
            "time_spent_today": 0,
            "time_spent_balance": 0,
            "time_spent_week": 1200,
            "time_spent_month": 3600,
            "bonus_today": self.bonus,
            "today_windows": self.today_override["windows"] or self.policy["curfew_windows"]["wed"],
            "today_override": dict(self.today_override),
            "next_curfew_at": "2026-11-25T21:00:00+00:00",
            "next_allowed_at": None,
            "sessions": [],
            "pause": self.policy["pause"],
            "app_limits_status": {
                "enabled": self.policy["app_limits"]["enabled"],
                "time_left_today": 0,
                "time_spent_today": 0,
                "active_apps": [],
                "apps": [
                    {
                        "pattern": a["pattern"],
                        "label": a["label"],
                        "category": a.get("category", ""),
                        "daily_limit_sec": a["daily_limit_sec"],
                        "bonus_today": 0,
                        "allotted_today": a["daily_limit_sec"],
                        "spent_today": 0,
                        "time_left_today": a["daily_limit_sec"],
                        "running": False,
                    }
                    for a in self.policy["app_limits"]["apps"]
                ],
            },
            "policy": self.policy,
        }

    def list_users(self):
        st = self._status()
        return [
            {
                "username": "alice",
                "enforcement_state": st["enforcement_state"],
                "time_left_today": st["time_left_today"],
                "time_spent_today": st["time_spent_today"],
                "daily_limit_today": st["daily_limit_today"],
                "lockout_action": self.policy["lockout_action"],
                "logged_in": True,
                "screen_locked": False,
            }
        ]

    def get_user_status(self, username):
        return self._status()

    def set_user_policy(self, username, policy):
        self.policy = policy
        return self._status()

    def set_today_override(self, username, limit_sec=None, windows=None, reset=False):
        if reset:
            self.today_override = {"active": False, "limit_sec": None, "windows": None}
        else:
            if limit_sec is not None:
                self.today_override["limit_sec"] = limit_sec
            if windows is not None:
                self.today_override["windows"] = windows
            self.today_override["active"] = True
        return self._status()

    def grant_app_time(self, username, pattern, delta_seconds):
        self.app_grants.append((username, pattern, delta_seconds))
        return {"applied_sec": delta_seconds, "status": self._status()}

    def grant_time(self, username, delta, mode="add", note=""):
        self.last_grant = (username, delta, mode, note)
        if mode == "set":
            self.time_left = delta
        else:
            self.time_left = max(0, self.time_left + delta)
            self.bonus += delta
        return {"applied_sec": delta, "status": self._status()}

    def pause_user(self, username, until_utc="", reason="", hide_tray=True):
        self.policy["pause"] = {
            "active": True,
            "mode": "until" if until_utc else "indefinite",
            "until_utc": until_utc or None,
            "reason": reason,
            "hide_tray_while_paused": hide_tray,
        }
        return self._status()

    def resume_user(self, username):
        self.policy["pause"] = {
            "active": False,
            "mode": "none",
            "until_utc": None,
            "reason": "",
            "hide_tray_while_paused": True,
        }
        return self._status()

    def set_homework_mode(self, username, payload):
        self.policy["homework_mode"] = {
            "active": bool(payload.get("active", False)),
            "mode": payload.get("mode", "none"),
            "until_utc": payload.get("until_utc"),
            "reason": payload.get("reason", ""),
        }
        return self._status()

    def list_time_requests(self, status_filter="pending"):
        return [
            {
                "id": 12,
                "username": "alice",
                "kind": "daily",
                "target": "",
                "target_label": "Daily screen time",
                "requested_sec": 900,
                "status": "pending",
                "reason": "Homework slide",
                "created_utc": "2026-11-25T16:00:00+00:00",
            }
        ]

    def resolve_time_request(self, request_id, approve=True, grant_seconds=0, note=""):
        return {
            "request": {
                "id": request_id,
                "username": "alice",
                "status": "approved" if approve else "denied",
                "granted_sec": grant_seconds or 900,
                "parent_note": note,
            },
            "status": self._status(),
        }

    def get_parent_code_status(self):
        return {"enabled": True, "created_utc": "2026-11-26T00:00:00+00:00"}

    def setup_parent_code(self, enable=True, rotate=False):
        return {
            "enabled": enable,
            "secret_b32": "JBSWY3DPEHPK3PXP" if enable else "",
            "otpauth_uri": "otpauth://totp/curfewd:host?secret=JBSWY3DPEHPK3PXP" if enable else "",
            "qr_svg": "<svg></svg>" if enable else "",
        }

    def lock_user(self, username):
        return {
            "username": username,
            "locked": True,
            "verified": True,
            "sessions": [
                {
                    "session_id": "c1",
                    "backend": "dbus:org.cinnamon.ScreenSaver",
                    "stage": "dbus_or_cli",
                    "elapsed_ms": 50,
                }
            ],
        }

    def test_lock(self, username):
        return self.lock_user(username)

    def get_usage_history(self, username, days=7):
        return {
            "username": username,
            "days": days,
            "start_date": "2026-11-19",
            "daily": [
                {
                    "date": "2026-11-25",
                    "active_sec": 1800,
                    "inactive_sec": 300,
                    "app_limits_sec": 600,
                    "bonus_sec": 0,
                    "lockouts": 1,
                }
            ],
            "totals": {"active_sec": 1800},
            "top_apps": [
                {
                    "app_key": "steam",
                    "label": "Steam",
                    "category": "Gaming",
                    "active_sec": 2400,
                    "daily_avg_sec": 1200,
                    "days_used": 2,
                }
            ],
            "events": [],
        }

    def get_daemon_config(self):
        return dict(self.config)

    def set_daemon_config(self, cfg):
        self.config = dict(cfg)
        return dict(self.config)

    def run_doctor(self):
        return {
            "version": "2.0.0",
            "overall": "ok",
            "checks": [{"name": "dbus_service", "status": "ok", "detail": "ok", "fixable": False}],
            "active_sessions": [],
        }

    def apply_doctor_fix(self):
        return {
            "fix_applied": {"applied": True, "detail": "dconf update succeeded"},
            "doctor": self.run_doctor(),
        }


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
        rc, out, _, _ = self._run(
            ["grant", "alice", "+30m", "--note", "Cleaned room", "--json"], stub
        )
        self.assertEqual(rc, EXIT_OK)
        self.assertEqual(stub.last_grant, ("alice", 1800, "add", "Cleaned room"))

        rc, out, _, _ = self._run(["grant", "alice", "-15m", "--json"], stub)
        self.assertEqual(rc, EXIT_OK)
        self.assertEqual(stub.last_grant, ("alice", -900, "add", ""))

        rc, out, _, _ = self._run(["grant", "alice", "=2h"], stub)
        self.assertEqual(rc, EXIT_OK)
        self.assertEqual(stub.last_grant, ("alice", 7200, "set", ""))
        self.assertIn("2h", out)

    def test_limit_window_today_lockout_pause_resume_lock_app_limits_history_config(self):
        stub = _StubClient()
        self.assertEqual(
            self._run(["limit", "alice", "1h30m", "--days", "mon-fri"], stub)[0], EXIT_OK
        )
        self.assertEqual(stub.policy["daily_limits"]["mon"], 5400)
        self.assertEqual(stub.policy["daily_limits"]["sat"], 3600)

        self.assertEqual(
            self._run(["window", "alice", "07:30-20:30", "--days", "sun-thu"], stub)[0], EXIT_OK
        )
        self.assertEqual(stub.policy["curfew_windows"]["sun"][0]["start"], "07:30")

        # Today-only override and reset
        self.assertEqual(
            self._run(["today", "alice", "--limit", "2h", "--window", "08:00-22:30"], stub)[0],
            EXIT_OK,
        )
        self.assertTrue(stub.today_override["active"])
        self.assertEqual(stub.today_override["limit_sec"], 7200)
        self.assertEqual(self._run(["today", "alice", "--reset"], stub)[0], EXIT_OK)
        self.assertFalse(stub.today_override["active"])

        self.assertEqual(self._run(["lockout", "alice", "suspend"], stub)[0], EXIT_OK)
        self.assertEqual(stub.policy["lockout_action"], "suspend")

        self.assertEqual(
            self._run(["pause", "alice", "--for", "2h", "--reason", "project"], stub)[0], EXIT_OK
        )
        self.assertTrue(stub.policy["pause"]["active"])
        self.assertEqual(stub.policy["pause"]["reason"], "project")

        self.assertEqual(self._run(["resume", "alice"], stub)[0], EXIT_OK)
        self.assertFalse(stub.policy["pause"]["active"])

        rc, out, _, _ = self._run(["lock", "alice", "--json"], stub)
        self.assertEqual(rc, EXIT_OK)
        self.assertTrue(json.loads(out)["locked"])

        # Per-app limits (`app-limits` canonical subcommand)
        self.assertEqual(self._run(["app-limits", "alice", "enable"], stub)[0], EXIT_OK)
        self.assertEqual(
            self._run(
                [
                    "app-limits",
                    "alice",
                    "add",
                    "steam",
                    "Steam",
                    "--limit",
                    "45m",
                    "--weekend",
                    "2h",
                    "--category",
                    "Gaming",
                    "--foreground-only",
                ],
                stub,
            )[0],
            EXIT_OK,
        )
        self.assertEqual(stub.policy["app_limits"]["apps"][0]["pattern"], "steam")
        self.assertEqual(stub.policy["app_limits"]["apps"][0]["daily_limit_sec"], 2700)
        self.assertEqual(stub.policy["app_limits"]["apps"][0]["weekend_limit_sec"], 7200)
        self.assertTrue(stub.policy["app_limits"]["apps"][0]["foreground_only"])
        self.assertEqual(
            self._run(["app-limits", "alice", "limit", "steam", "1h"], stub)[0], EXIT_OK
        )
        self.assertEqual(stub.policy["app_limits"]["apps"][0]["daily_limit_sec"], 3600)
        self.assertEqual(
            self._run(["app-limits", "alice", "grant", "steam", "+15m"], stub)[0], EXIT_OK
        )
        self.assertEqual(stub.app_grants, [("alice", "steam", 900)])

        # Shared category budgets
        self.assertEqual(
            self._run(
                ["app-limits", "alice", "category", "set", "Gaming", "1h", "--weekend", "2h"], stub
            )[0],
            EXIT_OK,
        )
        self.assertEqual(stub.policy["app_limits"]["category_limits"][0]["category"], "Gaming")
        self.assertEqual(
            self._run(["app-limits", "alice", "category", "remove", "Gaming"], stub)[0], EXIT_OK
        )
        self.assertEqual(stub.policy["app_limits"]["category_limits"], [])

        rc, out, _, _ = self._run(["history", "alice", "--days", "7"], stub)
        self.assertEqual(rc, EXIT_OK)
        self.assertIn("2026-11-25", out)

        rc, out, _, _ = self._run(
            ["config", "set", "session_lock_fallback", "false", "--json"], stub
        )
        self.assertEqual(rc, EXIT_OK)
        self.assertFalse(json.loads(out)["session_lock_fallback"])

        rc, out, _, _ = self._run(["doctor", "--fix", "--json"], stub)
        self.assertEqual(rc, EXIT_OK)
        self.assertTrue(json.loads(out)["fix_applied"]["applied"])

    def test_v2_cli_commands_role_homework_holiday_requests_parent_code_insights_test_lock(self):
        stub = _StubClient()

        # role
        self.assertEqual(self._run(["role", "alice", "parent"], stub)[0], EXIT_OK)
        self.assertEqual(stub.policy["role"], "parent")

        # homework on/off
        self.assertEqual(
            self._run(["homework", "alice", "on", "--for", "1h", "--reason", "Math"], stub)[0],
            EXIT_OK,
        )
        self.assertTrue(stub.policy["homework_mode"]["active"])
        self.assertEqual(stub.policy["homework_mode"]["reason"], "Math")
        self.assertEqual(self._run(["homework", "alice", "off"], stub)[0], EXIT_OK)
        self.assertFalse(stub.policy["homework_mode"]["active"])

        # holiday add/list/remove
        self.assertEqual(
            self._run(
                [
                    "holiday",
                    "alice",
                    "add",
                    "2026-12-24",
                    "2026-12-31",
                    "--mode",
                    "weekend",
                    "--label",
                    "Winter Break",
                ],
                stub,
            )[0],
            EXIT_OK,
        )
        self.assertEqual(len(stub.policy["holidays"]), 1)
        hol_id = stub.policy["holidays"][0]["id"]
        rc, out, _, _ = self._run(["holiday", "alice", "list"], stub)
        self.assertEqual(rc, EXIT_OK)
        self.assertIn("Winter Break", out)
        self.assertEqual(self._run(["holiday", "alice", "remove", hol_id], stub)[0], EXIT_OK)
        self.assertEqual(stub.policy["holidays"], [])

        # requests list/approve/deny
        rc, out, _, _ = self._run(["requests", "list"], stub)
        self.assertEqual(rc, EXIT_OK)
        self.assertIn("alice", out)
        rc, out, _, _ = self._run(
            ["requests", "approve", "12", "--grant", "30m", "--note", "ok"], stub
        )
        self.assertEqual(rc, EXIT_OK)
        self.assertIn("approved", out)
        rc, out, _, _ = self._run(["requests", "deny", "12", "--note", "bedtime"], stub)
        self.assertEqual(rc, EXIT_OK)
        self.assertIn("denied", out)

        # parent-code status/enable/disable
        rc, out, _, _ = self._run(["parent-code", "status"], stub)
        self.assertEqual(rc, EXIT_OK)
        self.assertIn("ENABLED", out)
        rc, out, _, _ = self._run(["parent-code", "enable"], stub)
        self.assertEqual(rc, EXIT_OK)
        self.assertIn("JBSWY3DPEHPK3PXP", out)
        rc, out, _, _ = self._run(["parent-code", "disable"], stub)
        self.assertEqual(rc, EXIT_OK)
        self.assertIn("disabled", out)

        # insights & test-lock
        rc, out, _, _ = self._run(["insights", "alice", "--days", "7"], stub)
        self.assertEqual(rc, EXIT_OK)
        self.assertIn("Steam", out)
        rc, out, _, _ = self._run(["test-lock", "alice", "--json"], stub)
        self.assertEqual(rc, EXIT_OK)
        self.assertTrue(json.loads(out)["verified"])

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

    def test_doctor_dconf_kde_xfce_hardening_and_wayland_checks(self):
        with tempfile.TemporaryDirectory() as tmp:
            before = check_dconf_lock_policy(root_prefix=tmp)
            self.assertEqual(before["status"], "warn")
            self.assertTrue(before["fixable"])

            self.assertEqual(check_kde_lock_policy(root_prefix=tmp)["status"], "warn")
            self.assertEqual(check_xfce_lock_policy(root_prefix=tmp)["status"], "warn")

            res = apply_all_lock_fixes(root_prefix=tmp, include_kde=True, include_xfce=True)
            self.assertTrue(res["applied"])
            self.assertEqual(check_dconf_lock_policy(root_prefix=tmp)["status"], "ok")
            self.assertEqual(check_kde_lock_policy(root_prefix=tmp)["status"], "ok")
            self.assertEqual(check_xfce_lock_policy(root_prefix=tmp)["status"], "ok")

            with open(
                os.path.join(tmp, "etc/dconf/db/local.d/locks/curfewd-lockscreen"), encoding="utf-8"
            ) as fh:
                self.assertIn("/org/cinnamon/desktop/screensaver/lock-enabled", fh.read())
            with open(os.path.join(tmp, "etc/xdg/kscreenlockerrc"), encoding="utf-8") as fh:
                self.assertIn("[Daemon][$i]", fh.read())
            with open(
                os.path.join(tmp, "etc/xdg/xfce4/xfconf/xfce-perchannel-xml/xfce4-screensaver.xml"),
                encoding="utf-8",
            ) as fh:
                self.assertIn('locked="*"', fh.read())

            # Wayland-aware doctor suppresses xdotool/wmctrl missing warnings on pure Wayland
            from unittest import mock

            def _only_gdbus(tool, path=None):
                return "/usr/bin/gdbus" if tool == "gdbus" else None

            with mock.patch("shutil.which", side_effect=_only_gdbus):
                rep = run_doctor(
                    config_dir=tmp,
                    state_dir=tmp,
                    sessions_by_user={
                        "alice": [
                            {
                                "session_id": "2",
                                "session_type": "wayland",
                                "desktop": "KDE",
                                "locked": False,
                                "idle": False,
                            }
                        ]
                    },
                    dbus_connected=True,
                    root_prefix=tmp,
                )
            self.assertEqual(rep["session_mode"], "wayland")
            by_name = {c["name"]: c for c in rep["checks"]}
            self.assertEqual(by_name["bin_xdotool"]["status"], "ok")
            self.assertIn("Wayland", by_name["bin_xdotool"]["detail"])
            self.assertEqual(by_name["kde_lock_policy"]["status"], "ok")

            # Standalone KDE/XFCE helpers remain directly callable
            self.assertTrue(apply_kde_lock_fix(root_prefix=tmp)["applied"])
            self.assertTrue(apply_xfce_lock_fix(root_prefix=tmp)["applied"])
            self.assertTrue(apply_dconf_lock_fix(root_prefix=tmp)["applied"])


if __name__ == "__main__":
    unittest.main()
