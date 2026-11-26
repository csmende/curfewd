"""Unit tests for AtomicJsonStore, symlink safety, SQLite HistoryStore, and Timekpr-nExT migrator."""

from datetime import datetime, timezone
import os
import tempfile
import unittest

from curfewd.history import HistoryStore
from curfewd.migrate import migrate_from_timekpr, parse_legacy_allowed_hours
from curfewd.models import DaemonConfig, TimeWindow, UserAccountingState, UserPolicy
from curfewd.storage import AtomicJsonStore


class TestAtomicJsonStore(unittest.TestCase):
    def test_roundtrip_and_user_listing(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = AtomicJsonStore(config_dir=os.path.join(tmp, "etc"), state_dir=os.path.join(tmp, "var"))
            store.ensure_dirs()

            cfg = DaemonConfig(poll_interval_sec=4, log_level="DEBUG")
            store.save_daemon_config(cfg)
            self.assertEqual(store.load_daemon_config().to_dict(), cfg.to_dict())

            pol = UserPolicy(username="alice", lockout_action="lock")
            store.save_user_policy(pol)
            self.assertTrue(store.has_user_policy("alice"))
            self.assertEqual(store.list_configured_users(), ["alice"])
            self.assertEqual(store.load_user_policy("alice").to_dict(), pol.to_dict())

            st = UserAccountingState(username="alice", date="2026-11-25", spent_today=900, spent_balance=900)
            store.save_user_state(st)
            self.assertEqual(store.load_user_state("alice").spent_today, 900)

    def test_refuses_symlinks_on_read_and_write(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = AtomicJsonStore(config_dir=os.path.join(tmp, "etc"), state_dir=os.path.join(tmp, "var"))
            store.ensure_dirs()
            victim = os.path.join(tmp, "victim.json")
            with open(victim, "w", encoding="utf-8") as fh:
                fh.write('{"original": true}\n')

            os.symlink(victim, store.user_policy_path("alice"))
            with self.assertRaises(OSError):
                store.load_user_policy("alice")
            with self.assertRaises(OSError):
                store.save_user_policy(UserPolicy(username="alice"))
            with open(victim, "r", encoding="utf-8") as fh:
                self.assertEqual(fh.read().strip(), '{"original": true}')


class TestHistoryStore(unittest.TestCase):
    def test_daily_deltas_events_and_pruning(self):
        with tempfile.TemporaryDirectory() as tmp:
            hist = HistoryStore(os.path.join(tmp, "history.db"))
            hist.record_daily_delta("alice", "2026-11-24", active_sec=1800, playtime_sec=600)
            hist.record_daily_delta("alice", "2026-11-25", active_sec=900, bonus_sec=600, lockouts=1)
            hist.record_daily_delta("alice", "2026-11-25", active_sec=300)
            hist.record_event("alice", "grant", actor="root", detail={"applied_sec": 600}, ts_utc="2026-11-25T18:00:00+00:00")
            hist.record_daily_delta("alice", "2024-01-01", active_sec=500)

            now = datetime(2026, 11, 25, 20, 0, tzinfo=timezone.utc)
            rep = hist.get_usage_history("alice", days=7, now_local=now)
            self.assertEqual(len(rep["daily"]), 2)
            self.assertEqual(rep["daily"][1]["active_sec"], 1200)
            self.assertEqual(rep["totals"]["active_sec"], 3000)
            self.assertEqual(rep["totals"]["lockouts"], 1)
            self.assertEqual(rep["events"][0]["kind"], "grant")

            d_del, _ = hist.prune(daily_retention_days=30, event_retention_days=30, now_utc=now)
            self.assertEqual(d_del, 1)


class TestTimekprMigration(unittest.TestCase):
    def test_legacy_allowed_hours_conversion(self):
        wins = parse_legacy_allowed_hours("8;9;10;20[0-30];!21;!22[0-15]")
        self.assertEqual(
            [w.to_dict() for w in wins],
            [
                TimeWindow("08:00", "11:00").to_dict(),
                TimeWindow("20:00", "20:30").to_dict(),
                TimeWindow("21:00", "22:15").to_dict(),
            ],
        )

    def test_full_migrate_from_timekpr(self):
        with tempfile.TemporaryDirectory() as tmp:
            leg_etc = os.path.join(tmp, "etc_timekpr")
            leg_var = os.path.join(tmp, "var_timekpr")
            os.makedirs(leg_etc)
            os.makedirs(os.path.join(leg_var, "config"))
            os.makedirs(os.path.join(leg_var, "work"))

            with open(os.path.join(leg_etc, "timekpr.conf"), "w", encoding="utf-8") as fh:
                fh.write(
                    "[GENERAL]\nTIMEKPR_POLLTIME = 5\nTIMEKPR_TERMINATION_TIME = 20\n"
                    "[MISC]\nTIMEKPR_SESSION_LOCK_FALLBACK = True\nTIMEKPR_RELEASE_X11_GRAB_ON_LOCK = True\n"
                )
            with open(os.path.join(leg_var, "config", "timekpr.alice.conf"), "w", encoding="utf-8") as fh:
                fh.write(
                    "[alice]\n"
                    "ALLOWED_WEEKDAYS = 1;2;3;4;5;6;7\n"
                    "LIMITS_PER_WEEKDAYS = 3600;3600;3600;3600;3600;7200;7200\n"
                    "ALLOWED_HOURS_1 = 8;9;10;11;12;13;14;15;16;17;18;19;20\n"
                    "ALLOWED_HOURS_5 = 8;9;10;11;12;13;14;15;16;17;18;19;20;21;22[0-30]\n"
                    "LOCKOUT_TYPE = lock\n"
                    "TRACK_INACTIVE = False\n"
                    "HIDE_TRAY_ICON = False\n"
                    "[alice.PLAYTIME]\n"
                    "PLAYTIME_ENABLED = True\n"
                    "PLAYTIME_ALLOWED_WEEKDAYS = 1;2;3;4;5;6;7\n"
                    "PLAYTIME_LIMITS_PER_WEEKDAYS = 1800;1800;1800;1800;1800;3600;3600\n"
                    "PLAYTIME_ACTIVITY_001 = steam[Steam]\n"
                )
            with open(os.path.join(leg_var, "work", "alice.time"), "w", encoding="utf-8") as fh:
                fh.write(
                    "[alice]\nTIME_SPENT_DAY = 1234\nTIME_SPENT_BALANCE = 1234\n"
                    "TIME_SPENT_WEEK = 5000\nTIME_SPENT_MONTH = 9000\n"
                    "LAST_CHECKED = 2026-11-25 18:00:00\n"
                )

            store = AtomicJsonStore(config_dir=os.path.join(tmp, "etc_new"), state_dir=os.path.join(tmp, "var_new"))
            dry = migrate_from_timekpr(store, leg_etc, leg_var, dry_run=True)
            self.assertTrue(dry["legacy_found"])
            self.assertEqual(dry["users_migrated"], ["alice"])
            self.assertFalse(store.has_user_policy("alice"))

            real = migrate_from_timekpr(store, leg_etc, leg_var, dry_run=False)
            self.assertEqual(real["users_migrated"], ["alice"])
            cfg = store.load_daemon_config()
            self.assertEqual(cfg.poll_interval_sec, 5)
            self.assertEqual(cfg.final_countdown_sec, 20)

            pol = store.load_user_policy("alice")
            self.assertEqual(pol.daily_limits["mon"], 3600)
            self.assertEqual(pol.daily_limits["sat"], 7200)
            self.assertEqual(pol.curfew_windows["mon"][0].to_dict(), {"start": "08:00", "end": "21:00"})
            self.assertEqual(pol.curfew_windows["fri"][0].to_dict(), {"start": "08:00", "end": "22:30"})
            self.assertTrue(pol.playtime.enabled)
            self.assertEqual(pol.playtime.apps[0].pattern, "steam")
            self.assertEqual(pol.playtime.apps[0].daily_limit_sec, 1800)
            self.assertEqual(store.load_user_state("alice").spent_today, 1234)


if __name__ == "__main__":
    unittest.main()
