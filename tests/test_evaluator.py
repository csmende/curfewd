"""Deterministic clock-injected unit tests for curfewd.engine.evaluator."""

from datetime import datetime, timezone
import unittest

from curfewd.engine.evaluator import (
    apply_app_grant,
    apply_grant,
    apply_today_override,
    evaluate_tick,
    rollover_state,
)
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

    def test_today_override_preserves_weekly_template_and_clears_at_midnight(self):
        cfg = DaemonConfig()
        pol = _make_restricted_policy(3600)
        st = UserAccountingState(username="alice", date="2026-11-25", week_key="2026-W48", month_key="2026-11", spent_today=1200, spent_balance=1200)
        now = datetime(2026, 11, 25, 21, 30, tzinfo=timezone.utc)

        # Without override, 21:30 is outside 08:00-21:00 curfew
        res_before = evaluate_tick(pol, st, cfg, now, elapsed_sec=0, is_active=True, screen_locked=False)
        self.assertEqual(res_before.enforcement_state, "locked_out")

        # Extend today's allowance to 2h and curfew to 22:30 without mutating weekly template
        apply_today_override(pol, st, now, limit_sec=7200, windows=[{"start": "08:00", "end": "22:30"}])
        self.assertEqual(pol.daily_limits["wed"], 3600)
        self.assertEqual(pol.curfew_windows["wed"][0].end, "21:00")

        res_after = evaluate_tick(pol, st, cfg, now, elapsed_sec=0, is_active=True, screen_locked=False)
        self.assertEqual(res_after.enforcement_state, "ok")
        self.assertEqual(res_after.daily_limit_today, 7200)
        self.assertEqual(res_after.time_left_today, 6000)

        # Next day midnight rollover clears the today override automatically
        next_day = datetime(2026, 11, 26, 9, 0, tzinfo=timezone.utc)
        res_next = evaluate_tick(pol, st, cfg, next_day, elapsed_sec=0, is_active=True, screen_locked=False)
        self.assertIsNone(st.today_limit_override)
        self.assertIsNone(st.today_windows_override)
        self.assertEqual(res_next.daily_limit_today, 3600)
        self.assertEqual(res_next.today_windows, [{"start": "08:00", "end": "21:00"}])

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
            app_spent_today={"steam": 1800},
            app_bonus_today={"steam": 900},
            today_limit_override=7200,
            today_windows_override=[{"start": "08:00", "end": "23:00"}],
            last_checked_utc="2026-11-29T20:00:00+00:00",
        )
        # Tuesday Dec 1 -> new day, new ISO week W49, new month 2026-12
        rolled = rollover_state(st, datetime(2026, 12, 1, 9, 0, tzinfo=timezone.utc))
        self.assertTrue(rolled)
        self.assertEqual(st.spent_today, 0)
        self.assertEqual(st.spent_week, 0)
        self.assertEqual(st.spent_month, 0)
        self.assertEqual(st.app_spent_today, {})
        self.assertEqual(st.app_bonus_today, {})
        self.assertIsNone(st.today_limit_override)
        self.assertIsNone(st.today_windows_override)

    def test_per_app_limits_isolate_expired_app_and_support_bonus_grant(self):
        cfg = DaemonConfig(playtime_enabled=True)
        pol = _make_restricted_policy(7200)
        pol.playtime = PlayTimePolicy(
            enabled=True,
            apps=[
                PlayTimeApp("steam", "Steam", daily_limit_sec=600, category="Gaming"),
                PlayTimeApp("discord", "Discord", daily_limit_sec=1800, category="Social"),
            ],
        )
        st = UserAccountingState(
            username="alice",
            date="2026-11-25",
            week_key="2026-W48",
            month_key="2026-11",
            app_spent_today={"steam": 598, "discord": 300},
        )
        now = datetime(2026, 11, 25, 16, 0, tzinfo=timezone.utc)

        res = evaluate_tick(
            pol,
            st,
            cfg,
            now,
            elapsed_sec=3,
            is_active=True,
            screen_locked=False,
            running_app_patterns=["steam", "discord"],
        )
        self.assertTrue(res.playtime_should_kill)
        # ONLY steam expired; discord remains running untouched!
        self.assertEqual(res.expired_app_patterns, ["steam"])
        self.assertEqual(st.app_spent_today["steam"], 601)
        self.assertEqual(st.app_spent_today["discord"], 303)

        # Grant +15m bonus strictly to steam -> steam is allowed again
        applied = apply_app_grant(pol, st, "steam", 900, now)
        self.assertEqual(applied, 900)
        res2 = evaluate_tick(
            pol,
            st,
            cfg,
            now,
            elapsed_sec=0,
            is_active=True,
            screen_locked=False,
            running_app_patterns=["steam", "discord"],
        )
        self.assertFalse(res2.playtime_should_kill)
        self.assertEqual(res2.expired_app_patterns, [])


if __name__ == "__main__":
    unittest.main()
