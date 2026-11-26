"""Unit tests for curfewd parsers and typed schema-v1 models."""

from datetime import datetime, timezone
import unittest

from curfewd.models import (
    DaemonConfig,
    PauseState,
    PlayTimeApp,
    PlayTimePolicy,
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
        wins = parse_windows("08:00-12:00,15:30-21:00,!21:00-22:00")
        self.assertEqual(
            wins,
            [
                {"start": "08:00", "end": "12:00", "unaccounted": False},
                {"start": "15:30", "end": "21:00", "unaccounted": False},
                {"start": "21:00", "end": "22:00", "unaccounted": True},
            ],
        )
        self.assertEqual(parse_windows("none"), [])
        self.assertEqual(parse_windows("all"), [{"start": "00:00", "end": "24:00", "unaccounted": False}])
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
            daily_limits={d: 3600 for d in ("mon", "tue", "wed", "thu", "fri", "sat", "sun")},
            weekly_limit=25200,
            curfew_windows={"mon": [TimeWindow("08:00", "21:00")]},
            pause=PauseState(active=True, mode="until", until_utc="2026-11-26T00:00:00+00:00", reason="homework"),
            playtime=PlayTimePolicy(enabled=True, apps=[PlayTimeApp("steam", "Steam")]),
        )
        rebuilt = UserPolicy.from_dict(pol.to_dict())
        self.assertEqual(rebuilt.to_dict(), pol.to_dict())
        self.assertEqual(rebuilt.lockout_action, "lock")
        self.assertTrue(rebuilt.pause.is_effective(datetime(2026, 11, 25, 23, 0, tzinfo=timezone.utc)))
        self.assertFalse(rebuilt.pause.is_effective(datetime(2026, 11, 26, 0, 1, tzinfo=timezone.utc)))

    def test_daemon_config_validation(self):
        cfg = DaemonConfig(poll_interval_sec=5, log_level="debug")
        self.assertEqual(cfg.log_level, "DEBUG")
        self.assertEqual(DaemonConfig.from_dict(cfg.to_dict()).to_dict(), cfg.to_dict())
        with self.assertRaises(ValueError):
            DaemonConfig(log_level="BOGUS")

    def test_accounting_state_roundtrip(self):
        st = UserAccountingState(username="alice", date="2026-11-25", spent_today=1200, spent_balance=600, bonus_today=600)
        self.assertEqual(UserAccountingState.from_dict(st.to_dict()).to_dict(), st.to_dict())


if __name__ == "__main__":
    unittest.main()
