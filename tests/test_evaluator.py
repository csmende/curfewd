"""Deterministic clock-injected unit tests for curfewd.engine.evaluator."""

from datetime import datetime, timezone
import unittest

from curfewd.engine.evaluator import apply_grant, evaluate_tick, rollover_state
from curfewd.models import (
    DaemonConfig,
    PauseState,
    PlayTimeApp,
    PlayTimePolicy,
    TimeWindow,
    UserAccountingState,
    UserPolicy,
)
from curfewd.parsing import WEEKDAYS


def _make_restricted_policy(daily_sec: int = 3600) -> UserPolicy:
    return UserPolicy(
        username="alice",
        lockout_action="lock",
        daily_limits={d: daily_sec for d in WEEKDAYS},
        curfew_windows={d: [TimeWindow("08:00", "21:00")] for d in WEEKDAYS},
    )


class TestPolicyEvaluator(unittest.TestCase):
    def test_active_accounting_and_warning_threshold(self):
        cfg = DaemonConfig(warning_thresholds_sec=[3600, 1800, 600], final_countdown_sec=15)
        pol = _make_restricted_policy(3600)
        st = UserAccountingState(username="alice", date="2026-11-25", week_key="2026-W48", month_key="2026-11", spent_today=2998, spent_balance=2998)
        now = datetime(2026, 11, 25, 15, 0, tzinfo=timezone.utc)  # Wednesday

        res = evaluate_tick(pol, st, cfg, now, elapsed_sec=3, is_active=True, screen_locked=False)
        self.assertEqual(st.spent_today, 3001)
        self.assertEqual(res.time_left_today, 599)
        self.assertEqual(res.crossed_warnings, [600])
        self.assertEqual(res.enforcement_state, "warning")
        self.assertFalse(res.should_enforce)

    def test_locked_screen_does_not_burn_time_when_track_inactive_false(self):
        cfg = DaemonConfig()
        pol = _make_restricted_policy(3600)
        st = UserAccountingState(username="alice", date="2026-11-25", week_key="2026-W48", month_key="2026-11", spent_today=100, spent_balance=100)
        now = datetime(2026, 11, 25, 15, 0, tzinfo=timezone.utc)

        res = evaluate_tick(pol, st, cfg, now, elapsed_sec=10, is_active=True, screen_locked=True)
        self.assertEqual(st.spent_today, 100)
        self.assertEqual(res.active_delta, 0)
        self.assertEqual(res.inactive_delta, 10)

    def test_outside_curfew_window_triggers_locked_out_and_next_allowed(self):
        cfg = DaemonConfig(final_countdown_sec=15)
        pol = _make_restricted_policy(3600)
        st = UserAccountingState(username="alice")
        now = datetime(2026, 11, 25, 22, 0, tzinfo=timezone.utc)

        res = evaluate_tick(pol, st, cfg, now, elapsed_sec=3, is_active=True, screen_locked=False)
        self.assertEqual(res.time_left_continuous, 0)
        self.assertEqual(res.enforcement_state, "locked_out")
        self.assertTrue(res.should_enforce)
        self.assertEqual(res.next_allowed_at, "2026-11-26T08:00:00+00:00")

    def test_pause_indefinite_and_timed_auto_expiry_preserve_schedule(self):
        cfg = DaemonConfig()
        pol = _make_restricted_policy(3600)
        pol.pause = PauseState(active=True, mode="until", until_utc="2026-11-25T20:00:00+00:00", reason="homework", hide_tray_while_paused=True)
        st = UserAccountingState(username="alice", date="2026-11-25", week_key="2026-W48", month_key="2026-11", spent_today=3600, spent_balance=3600)

        # Before expiry: paused, no enforcement, tray hidden
        t1 = datetime(2026, 11, 25, 19, 30, tzinfo=timezone.utc)
        res1 = evaluate_tick(pol, st, cfg, t1, elapsed_sec=5, is_active=True, screen_locked=False)
        self.assertTrue(res1.pause_active)
        self.assertFalse(res1.should_enforce)
        self.assertEqual(res1.enforcement_state, "paused")
        self.assertTrue(res1.hide_tray_effective)
        self.assertEqual(st.spent_balance, 3600)  # paused did not charge balance

        # After expiry: pause auto-clears and saved 3600s limit enforces immediately!
        t2 = datetime(2026, 11, 25, 20, 0, 5, tzinfo=timezone.utc)
        res2 = evaluate_tick(pol, st, cfg, t2, elapsed_sec=5, is_active=True, screen_locked=False)
        self.assertTrue(res2.pause_expired)
        self.assertFalse(pol.pause.active)
        self.assertTrue(res2.should_enforce)
        self.assertEqual(res2.enforcement_state, "locked_out")
        self.assertEqual(pol.daily_limits["wed"], 3600)

    def test_unaccounted_window_does_not_charge_or_enforce(self):
        cfg = DaemonConfig()
        pol = _make_restricted_policy(3600)
        pol.curfew_windows["wed"] = [TimeWindow("08:00", "15:00", unaccounted=True), TimeWindow("15:00", "21:00", unaccounted=False)]
        st = UserAccountingState(username="alice", date="2026-11-25", week_key="2026-W48", month_key="2026-11", spent_today=3600, spent_balance=3600)
        now = datetime(2026, 11, 25, 10, 0, tzinfo=timezone.utc)

        res = evaluate_tick(pol, st, cfg, now, elapsed_sec=10, is_active=True, screen_locked=False)
        self.assertTrue(res.unaccounted_now)
        self.assertFalse(res.should_enforce)
        self.assertEqual(st.spent_balance, 3600)

    def test_grant_add_subtract_and_set(self):
        pol = _make_restricted_policy(3600)
        st = UserAccountingState(username="alice", date="2026-11-25", week_key="2026-W48", month_key="2026-11", spent_today=3600, spent_balance=3600)
        now = datetime(2026, 11, 25, 16, 0, tzinfo=timezone.utc)

        applied = apply_grant(pol, st, 1800, "add", now)
        self.assertEqual(applied, 1800)
        self.assertEqual(st.spent_balance, 1800)
        self.assertEqual(st.bonus_today, 1800)

        applied2 = apply_grant(pol, st, 900, "set", now)
        self.assertEqual(applied2, -900)
        self.assertEqual(st.spent_balance, 2700)

    def test_day_week_month_rollover(self):
        st = UserAccountingState(
            username="alice",
            date="2026-11-29",  # Sunday, ISO W48
            week_key="2026-W48",
            month_key="2026-11",
            spent_today=3600,
            spent_balance=3600,
            spent_week=12000,
            spent_month=40000,
            last_checked_utc="2026-11-29T20:00:00+00:00",
        )
        # Tuesday Dec 1 -> new day, new ISO week W49, new month 2026-12
        rolled = rollover_state(st, datetime(2026, 12, 1, 9, 0, tzinfo=timezone.utc))
        self.assertTrue(rolled)
        self.assertEqual(st.spent_today, 0)
        self.assertEqual(st.spent_week, 0)
        self.assertEqual(st.spent_month, 0)

    def test_playtime_triggers_kill_when_exhausted(self):
        cfg = DaemonConfig(playtime_enabled=True)
        pol = _make_restricted_policy(7200)
        pol.playtime = PlayTimePolicy(
            enabled=True,
            daily_limits={d: 600 for d in WEEKDAYS},
            apps=[PlayTimeApp("steam", "Steam")],
        )
        st = UserAccountingState(username="alice", date="2026-11-25", week_key="2026-W48", month_key="2026-11", playtime_spent_today=598, playtime_balance=598)
        now = datetime(2026, 11, 25, 16, 0, tzinfo=timezone.utc)

        res = evaluate_tick(pol, st, cfg, now, elapsed_sec=3, is_active=True, screen_locked=False, playtime_active=True)
        self.assertEqual(res.playtime_left_today, 0)
        self.assertTrue(res.playtime_should_kill)


if __name__ == "__main__":
    unittest.main()
