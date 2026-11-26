"""Unit tests for curfewd parsers and typed schema-v2 models."""

import unittest
from datetime import datetime, timezone

from curfewd.models import (
    AppLimitRule,
    AppLimitsPolicy,
    DaemonConfig,
    PauseState,
    TimeWindow,
    UserAccountingState,
    UserPolicy,
)
from curfewd.parsing import (
    format_duration,
    parse_days,
    parse_delta,
    parse_duration,
    parse_hhmm_to_minutes,
    parse_until_timestamp,
    parse_windows,
    validate_username,
)


class TestParsers(unittest.TestCase):
    def test_validate_username_accepts_valid_and_rejects_traversal(self):
        self.assertEqual(validate_username("alice"), "alice")
        self.assertEqual(validate_username("user_01-a.b"), "user_01-a.b")
        for bad in ("", "../root", "alice/bob", "Alice", "1user", "a" * 40, "alice;rm"):
            with self.assertRaises(ValueError):
                validate_username(bad)

    def test_parse_duration_and_format(self):
        self.assertEqual(parse_duration("1h30m"), 5400)
        self.assertEqual(parse_duration("45m"), 2700)
        self.assertEqual(parse_duration("90s"), 90)
        self.assertEqual(parse_duration("3600"), 3600)
        self.assertEqual(parse_duration("1d"), 86400)
        self.assertEqual(parse_duration("unlimited"), 86400)
        self.assertEqual(format_duration(5400), "1h 30m")
        self.assertEqual(format_duration(45, include_seconds=True), "45s")
        self.assertEqual(format_duration(-900), "-15m")
        for bad in ("", "-5m", "1h bogus", "abc"):
            with self.assertRaises(ValueError):
                parse_duration(bad)

    def test_parse_delta(self):
        self.assertEqual(parse_delta("+30m"), ("add", 1800))
        self.assertEqual(parse_delta("-15m"), ("add", -900))
        self.assertEqual(parse_delta("=2h"), ("set", 7200))
        self.assertEqual(parse_delta("20m"), ("add", 1200))

    def test_parse_days(self):
        self.assertEqual(parse_days("all"), ["mon", "tue", "wed", "thu", "fri", "sat", "sun"])
        self.assertEqual(parse_days("weekdays"), ["mon", "tue", "wed", "thu", "fri"])
        self.assertEqual(parse_days("weekends"), ["sat", "sun"])
        self.assertEqual(parse_days("mon-thu,sun"), ["mon", "tue", "wed", "thu", "sun"])
        self.assertEqual(parse_days("sun-thu"), ["sun", "mon", "tue", "wed", "thu"])
        with self.assertRaises(ValueError):
            parse_days("funday")

    def test_parse_windows(self):
        wins = parse_windows("08:00-12:00,15:30-21:00")
        self.assertEqual(
            wins,
            [
                {"start": "08:00", "end": "12:00"},
                {"start": "15:30", "end": "21:00"},
            ],
        )
        self.assertEqual(parse_windows("none"), [])
        self.assertEqual(parse_windows("all"), [{"start": "00:00", "end": "24:00"}])
        with self.assertRaises(ValueError):
            parse_windows("!21:00-22:00")
        with self.assertRaises(ValueError):
            parse_windows("10:00-14:00,13:00-16:00")
        with self.assertRaises(ValueError):
            parse_windows("22:00-08:00")
        self.assertEqual(parse_hhmm_to_minutes("24:00"), 1440)

    def test_parse_until_timestamp(self):
        now = datetime(2026, 11, 25, 18, 0, tzinfo=timezone.utc)
        self.assertEqual(parse_until_timestamp("20:30", now_local=now), "2026-11-25T20:30:00+00:00")
        self.assertEqual(parse_until_timestamp("17:00", now_local=now), "2026-11-26T17:00:00+00:00")
        self.assertEqual(parse_until_timestamp("today", now_local=now), "2026-11-26T00:00:00+00:00")


class TestModels(unittest.TestCase):
    def test_user_policy_roundtrip(self):
        pol = UserPolicy(
            username="alice",
            lockout_action="lock",
            daily_limits=dict.fromkeys(("mon", "tue", "wed", "thu", "fri", "sat", "sun"), 3600),
            weekly_limit=25200,
            curfew_windows={"mon": [TimeWindow("08:00", "21:00")]},
            pause=PauseState(
                active=True, mode="until", until_utc="2026-11-26T00:00:00+00:00", reason="homework"
            ),
            app_limits=AppLimitsPolicy(
                enabled=True,
                apps=[AppLimitRule("steam", "Steam", daily_limit_sec=2700, category="Gaming")],
            ),
        )
        rebuilt = UserPolicy.from_dict(pol.to_dict())
        self.assertEqual(rebuilt.to_dict(), pol.to_dict())
        self.assertEqual(rebuilt.schema_version, 2)
        self.assertEqual(rebuilt.lockout_action, "lock")
        self.assertEqual(rebuilt.app_limits.apps[0].daily_limit_sec, 2700)
        self.assertEqual(rebuilt.app_limits.apps[0].category, "Gaming")
        self.assertTrue(
            rebuilt.pause.is_effective(datetime(2026, 11, 25, 23, 0, tzinfo=timezone.utc))
        )
        self.assertFalse(
            rebuilt.pause.is_effective(datetime(2026, 11, 26, 0, 1, tzinfo=timezone.utc))
        )

        # Schema v1 forward-migration: legacy `playtime` policy, `playtime_enabled` config & `playtime_*` state
        v1_pol = UserPolicy.from_dict(
            {
                "schema_version": 1,
                "username": "alice",
                "playtime": {
                    "enabled": True,
                    "daily_limits": {"mon": 1800},
                    "apps": [{"pattern": "minecraft", "label": "Minecraft"}],
                },
            }
        )
        self.assertEqual(v1_pol.schema_version, 2)
        self.assertTrue(v1_pol.app_limits.enabled)
        self.assertEqual(v1_pol.app_limits.apps[0].daily_limit_sec, 1800)
        self.assertIn("app_limits", v1_pol.to_dict())
        self.assertNotIn("playtime", v1_pol.to_dict())

        v1_cfg = DaemonConfig.from_dict({"schema_version": 1, "playtime_enabled": False})
        self.assertEqual(v1_cfg.schema_version, 2)
        self.assertFalse(v1_cfg.app_limits_enabled)

        v1_st = UserAccountingState.from_dict(
            {
                "schema_version": 1,
                "username": "alice",
                "playtime_spent_today": 420,
                "playtime_balance": 420,
            }
        )
        self.assertEqual(v1_st.schema_version, 2)
        self.assertEqual(v1_st.app_limits_spent_today, 420)
        self.assertEqual(v1_st.app_limits_balance, 420)
        self.assertEqual(
            TimeWindow.from_dict({"start": "08:00", "end": "20:00", "unaccounted": True}).to_dict(),
            {"start": "08:00", "end": "20:00"},
        )

    def test_daemon_config_validation(self):
        cfg = DaemonConfig(poll_interval_sec=5, log_level="debug")
        self.assertEqual(cfg.log_level, "DEBUG")
        self.assertEqual(DaemonConfig.from_dict(cfg.to_dict()).to_dict(), cfg.to_dict())
        with self.assertRaises(ValueError):
            DaemonConfig(log_level="BOGUS")

    def test_accounting_state_roundtrip(self):
        st = UserAccountingState(
            username="alice",
            date="2026-11-25",
            spent_today=1200,
            spent_balance=600,
            bonus_today=600,
            app_spent_today={"steam": 900},
            app_bonus_today={"steam": 900},
            today_limit_override=5400,
            today_windows_override=[{"start": "08:00", "end": "22:30"}],
        )
        self.assertEqual(UserAccountingState.from_dict(st.to_dict()).to_dict(), st.to_dict())


if __name__ == "__main__":
    unittest.main()
