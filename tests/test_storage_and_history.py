"""Unit tests for AtomicJsonStore, symlink safety, and SQLite HistoryStore (including v1->v2 migration)."""

import os
import sqlite3
import tempfile
import unittest
from datetime import datetime, timezone

from curfewd.history import HistoryStore
from curfewd.models import DaemonConfig, UserAccountingState, UserPolicy
from curfewd.storage import AtomicJsonStore


class TestAtomicJsonStore(unittest.TestCase):
    def test_roundtrip_and_user_listing(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = AtomicJsonStore(
                config_dir=os.path.join(tmp, "etc"), state_dir=os.path.join(tmp, "var")
            )
            store.ensure_dirs()

            cfg = DaemonConfig(poll_interval_sec=4, log_level="DEBUG")
            store.save_daemon_config(cfg)
            self.assertEqual(store.load_daemon_config().to_dict(), cfg.to_dict())

            pol = UserPolicy(username="alice", lockout_action="lock")
            store.save_user_policy(pol)
            self.assertTrue(store.has_user_policy("alice"))
            self.assertEqual(store.list_configured_users(), ["alice"])
            self.assertEqual(store.load_user_policy("alice").to_dict(), pol.to_dict())

            st = UserAccountingState(
                username="alice", date="2026-11-25", spent_today=900, spent_balance=900
            )
            store.save_user_state(st)
            self.assertEqual(store.load_user_state("alice").spent_today, 900)

    def test_refuses_symlinks_on_read_and_write(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = AtomicJsonStore(
                config_dir=os.path.join(tmp, "etc"), state_dir=os.path.join(tmp, "var")
            )
            store.ensure_dirs()
            victim = os.path.join(tmp, "victim.json")
            with open(victim, "w", encoding="utf-8") as fh:
                fh.write('{"original": true}\n')

            os.symlink(victim, store.user_policy_path("alice"))
            with self.assertRaises(OSError):
                store.load_user_policy("alice")
            with self.assertRaises(OSError):
                store.save_user_policy(UserPolicy(username="alice"))
            with open(victim, encoding="utf-8") as fh:
                self.assertEqual(fh.read().strip(), '{"original": true}')


class TestHistoryStore(unittest.TestCase):
    def test_daily_deltas_events_and_pruning(self):
        with (
            tempfile.TemporaryDirectory() as tmp,
            HistoryStore(os.path.join(tmp, "history.db")) as hist,
        ):
            hist.record_daily_delta("alice", "2026-11-24", active_sec=1800, app_limits_sec=600)
            hist.record_daily_delta(
                "alice", "2026-11-25", active_sec=900, bonus_sec=600, lockouts=1
            )
            hist.record_daily_delta("alice", "2026-11-25", active_sec=300)
            hist.record_event(
                "alice",
                "grant",
                actor="root",
                detail={"applied_sec": 600},
                ts_utc="2026-11-25T18:00:00+00:00",
            )
            hist.record_daily_delta("alice", "2024-01-01", active_sec=500)

            now = datetime(2026, 11, 25, 20, 0, tzinfo=timezone.utc)
            rep = hist.get_usage_history("alice", days=7, now_local=now)
            self.assertEqual(len(rep["daily"]), 2)
            self.assertEqual(rep["daily"][0]["app_limits_sec"], 600)
            self.assertEqual(rep["daily"][1]["active_sec"], 1200)
            self.assertEqual(rep["totals"]["active_sec"], 3000)
            self.assertEqual(rep["totals"]["app_limits_sec"], 600)
            self.assertEqual(rep["totals"]["lockouts"], 1)
            self.assertEqual(rep["events"][0]["kind"], "grant")

            d_del, _ = hist.prune(daily_retention_days=30, event_retention_days=30, now_utc=now)
            self.assertEqual(d_del, 1)

    def test_sqlite_v1_playtime_sec_forward_migrates_to_app_limits_sec(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = os.path.join(tmp, "history.db")
            # Seed a v1 history.db using legacy `playtime_sec` column
            with sqlite3.connect(db_path) as conn:
                conn.executescript(
                    """
                    CREATE TABLE daily_usage (
                        date TEXT NOT NULL,
                        username TEXT NOT NULL,
                        active_sec INTEGER NOT NULL DEFAULT 0,
                        inactive_sec INTEGER NOT NULL DEFAULT 0,
                        playtime_sec INTEGER NOT NULL DEFAULT 0,
                        bonus_sec INTEGER NOT NULL DEFAULT 0,
                        lockouts INTEGER NOT NULL DEFAULT 0,
                        PRIMARY KEY (date, username)
                    );
                    INSERT INTO daily_usage (date, username, active_sec, playtime_sec)
                    VALUES ('2026-11-25', 'alice', 2400, 900);
                    """
                )

            with HistoryStore(db_path) as hist:
                first_conn = hist._conn
                self.assertIsNotNone(first_conn)
                hist.record_daily_delta("alice", "2026-11-25", active_sec=600, app_limits_sec=300)
                self.assertIs(hist._conn, first_conn)
                rep = hist.get_usage_history(
                    "alice",
                    days=7,
                    now_local=datetime(2026, 11, 25, 20, 0, tzinfo=timezone.utc),
                )
                self.assertEqual(rep["daily"][0]["active_sec"], 3000)
                self.assertEqual(rep["daily"][0]["app_limits_sec"], 1200)
                self.assertEqual(rep["totals"]["app_limits_sec"], 1200)
            self.assertIsNone(hist._conn)

    def test_app_usage_insights_and_time_requests_lifecycle(self):
        with (
            tempfile.TemporaryDirectory() as tmp,
            HistoryStore(os.path.join(tmp, "history.db")) as hist,
        ):
            now = datetime(2026, 11, 25, 20, 0, tzinfo=timezone.utc)
            hist.record_daily_delta(
                "alice",
                "2026-11-24",
                active_sec=3600,
                app_deltas=[
                    ("steam", "Steam", "Gaming", 1800),
                    ("code", "VS Code", "Productivity", 900),
                ],
            )
            hist.record_daily_delta(
                "alice",
                "2026-11-25",
                active_sec=2400,
                app_deltas=[
                    ("steam", "Steam", "Gaming", 1200),
                    ("sober", "Roblox (Sober)", "Gaming", 600),
                ],
            )
            rep = hist.get_usage_history("alice", days=7, now_local=now)
            self.assertEqual(rep["top_apps"][0]["app_key"], "steam")
            self.assertEqual(rep["top_apps"][0]["active_sec"], 3000)
            self.assertEqual(rep["top_apps"][0]["days_used"], 2)

            req = hist.create_time_request(
                "alice", "app", 900, target="steam", target_label="Steam", reason="last round"
            )
            self.assertEqual(req["status"], "pending")
            self.assertEqual(len(hist.list_time_requests(status="pending")), 1)
            resolved = hist.resolve_time_request(
                req["id"], approve=True, resolved_by="parent", granted_sec=900, note="ok"
            )
            self.assertIsNotNone(resolved)
            self.assertEqual(resolved["status"], "approved")
            self.assertEqual(len(hist.list_time_requests(status="pending")), 0)

    def test_wal_autocheckpoint_and_truncate_checkpoint_on_close(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = os.path.join(tmp, "history.db")
            wal_path = db_path + "-wal"
            with HistoryStore(db_path) as hist:
                auto = hist._get_conn().execute("PRAGMA wal_autocheckpoint").fetchone()[0]
                self.assertEqual(auto, 200)
                hist.record_daily_delta("alice", "2026-11-25", active_sec=300)
                busy, _log, _chk = hist.checkpoint("PASSIVE")
                self.assertEqual(busy, 0)
                with self.assertRaises(ValueError):
                    hist.checkpoint("INVALID")
            # close() ran TRUNCATE checkpoint so WAL is removed or 0 bytes
            self.assertTrue(not os.path.exists(wal_path) or os.path.getsize(wal_path) == 0)


if __name__ == "__main__":
    unittest.main()
