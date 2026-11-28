"""D-Bus-free unit tests for curfewd.engine.runtime.EnforcementRuntime (Spec 005)."""

import os
import tempfile
import unittest
from datetime import datetime, timezone
from unittest import mock

from curfewd.engine import evaluator as evaluator_mod
from curfewd.engine.runtime import EnforcementRuntime
from curfewd.history import HistoryStore
from curfewd.models import (
    AppLimitRule,
    AppLimitsPolicy,
    DaemonConfig,
    PauseState,
    TimeWindow,
    UserAccountingState,
    UserPolicy,
)
from curfewd.parsing import WEEKDAYS
from curfewd.storage import AtomicJsonStore


class _StubSessionMonitor:
    def __init__(self, username: str, locked: bool = False) -> None:
        self.username = username
        self.locked = locked
        self.lockout_actions: list[tuple[str, str]] = []
        self.lock_screen_messages: list[str] = []

    def list_user_sessions(self, _tracked_types=None):
        return {
            self.username: [
                {
                    "session_id": "c1",
                    "uid": 1000,
                    "username": self.username,
                    "session_type": "x11",
                    "active": True,
                    "locked": self.locked,
                    "idle": False,
                    "display": ":0",
                    "desktop": "X-Cinnamon",
                    "leader_pid": 1234,
                }
            ]
        }

    def notify_lock_screen(self, sessions, message: str) -> int:
        if not sessions or not message:
            return 0
        self.lock_screen_messages.append(message)
        return len(sessions)

    def summarize_sessions(self, sessions):
        return [
            {
                "session_id": s["session_id"],
                "session_type": s["session_type"],
                "desktop": s["desktop"],
                "active": s["active"],
                "locked": s["locked"],
                "display": s["display"],
                "leader_pid": s["leader_pid"],
            }
            for s in sessions
        ]

    def enforce_lockout_action(self, action, username, _uid, _sessions, _config):
        self.lockout_actions.append((action, username))


class _CountingAppLimitsMonitor:
    def __init__(self, matches: list[dict]) -> None:
        self.matches = matches
        self.scan_calls = 0
        self.kill_calls: list[tuple[int, list[str], list[dict] | None]] = []

    def find_matching_processes(self, _uid: int, _apps: list[AppLimitRule]):
        self.scan_calls += 1
        return list(self.matches)

    def enforce_kill(self, uid: int, apps: list[AppLimitRule], prematched=None):
        self.kill_calls.append((uid, [a.pattern for a in apps], prematched))
        return [m["pid"] for m in (prematched or [])]


class TestEnforcementRuntime(unittest.TestCase):
    def test_tick_evaluates_once_and_scans_processes_once_on_app_expiry(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = AtomicJsonStore(
                config_dir=os.path.join(tmp, "etc"),
                state_dir=os.path.join(tmp, "var"),
            )
            store.ensure_dirs()
            store.save_daemon_config(DaemonConfig(poll_interval_sec=3, app_limits_enabled=True))
            pol = UserPolicy(
                username="alice",
                daily_limits=dict.fromkeys(WEEKDAYS, 7200),
                curfew_windows={d: [TimeWindow("08:00", "21:00")] for d in WEEKDAYS},
                app_limits=AppLimitsPolicy(
                    enabled=True,
                    apps=[
                        AppLimitRule("steam", "Steam", daily_limit_sec=600, category="Gaming"),
                        AppLimitRule("discord", "Discord", daily_limit_sec=1800, category="Social"),
                    ],
                ),
            )
            store.save_user_policy(pol)
            store.save_user_state(
                UserAccountingState(
                    username="alice",
                    date="2026-11-25",
                    week_key="2026-W48",
                    month_key="2026-11",
                    app_spent_today={"steam": 600, "discord": 100},
                )
            )

            app_mon = _CountingAppLimitsMonitor(
                [
                    {"pid": 501, "name": "steam", "label": "Steam", "pattern": "steam"},
                    {"pid": 502, "name": "discord", "label": "Discord", "pattern": "discord"},
                ]
            )
            status_events: list[tuple[str, dict]] = []
            notif_events: list[tuple[str, dict]] = []
            challenges: list[tuple[str, str]] = []

            with HistoryStore(os.path.join(tmp, "var", "history.db")) as hist:
                rt = EnforcementRuntime(
                    store=store,
                    history=hist,
                    session_monitor=_StubSessionMonitor("alice"),
                    app_limits_monitor=app_mon,
                    now_fn=lambda: datetime(2026, 11, 25, 16, 0, tzinfo=timezone.utc),
                    on_status_changed=lambda u, st: status_events.append((u, st)),
                    on_client_notification=lambda u, p: notif_events.append((u, p)),
                    on_screensaver_challenge=lambda u, n: challenges.append((u, n)),
                )
                with mock.patch(
                    "curfewd.engine.runtime.evaluate_tick",
                    wraps=evaluator_mod.evaluate_tick,
                ) as spy_eval:
                    rt.tick()

            # Single evaluation per active user (no double evaluate_tick in _compute_status!)
            self.assertEqual(spy_eval.call_count, 1)
            # Single /proc scan even when Steam expires and triggers enforce_kill
            self.assertEqual(app_mon.scan_calls, 1)
            self.assertEqual(len(app_mon.kill_calls), 1)
            uid, expired_pats, prematched = app_mon.kill_calls[0]
            self.assertEqual(uid, 1000)
            self.assertEqual(expired_pats, ["steam"])
            self.assertEqual([m["pid"] for m in prematched or []], [501])
            self.assertEqual(len(status_events), 1)
            self.assertTrue(any(p.get("kind") == "app_limit_reached" for _, p in notif_events))
            self.assertEqual(len(challenges), 1)
            # Verify screensaver CSPRNG nonce roundtrip
            user, nonce = challenges[0]
            self.assertTrue(rt.verify_screensaver_state(user, nonce, True))
            self.assertFalse(rt.verify_screensaver_state(user, nonce, False))

    def test_tick_auto_expires_timed_pause_and_enforces_lockout_after_countdown(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = AtomicJsonStore(
                config_dir=os.path.join(tmp, "etc"),
                state_dir=os.path.join(tmp, "var"),
            )
            store.ensure_dirs()
            store.save_daemon_config(DaemonConfig(final_countdown_sec=0))
            pol = UserPolicy(
                username="alice",
                daily_limits=dict.fromkeys(WEEKDAYS, 3600),
                curfew_windows={d: [TimeWindow("08:00", "21:00")] for d in WEEKDAYS},
                pause=PauseState(
                    active=True,
                    mode="until",
                    until_utc="2026-11-25T15:00:00+00:00",
                ),
            )
            store.save_user_policy(pol)
            store.save_user_state(
                UserAccountingState(
                    username="alice",
                    date="2026-11-25",
                    week_key="2026-W48",
                    month_key="2026-11",
                    spent_today=3600,
                    spent_balance=3600,
                )
            )
            sess_mon = _StubSessionMonitor("alice")
            policy_events: list[str] = []

            with HistoryStore(os.path.join(tmp, "var", "history.db")) as hist:
                rt = EnforcementRuntime(
                    store=store,
                    history=hist,
                    session_monitor=sess_mon,
                    now_fn=lambda: datetime(2026, 11, 25, 16, 0, tzinfo=timezone.utc),
                    on_policy_changed=policy_events.append,
                )
                rt.tick()

            self.assertEqual(policy_events, ["alice"])
            self.assertFalse(store.load_user_policy("alice").pause.active)
            self.assertEqual(sess_mon.lockout_actions, [("lock", "alice")])

    def test_time_requests_parent_code_walkup_and_save_grace(self):
        import time as _time

        from curfewd.auth_code import compute_totp

        with tempfile.TemporaryDirectory() as tmp:
            store = AtomicJsonStore(
                config_dir=os.path.join(tmp, "etc"),
                state_dir=os.path.join(tmp, "var"),
            )
            store.ensure_dirs()
            store.save_user_policy(
                UserPolicy(
                    username="alice",
                    daily_limits=dict.fromkeys(WEEKDAYS, 3600),
                    curfew_windows={d: [TimeWindow("08:00", "21:00")] for d in WEEKDAYS},
                )
            )
            req_events: list[tuple[str, dict]] = []
            with HistoryStore(os.path.join(tmp, "var", "history.db")) as hist:
                rt = EnforcementRuntime(
                    store=store,
                    history=hist,
                    session_monitor=_StubSessionMonitor("alice"),
                    now_fn=lambda: datetime(2026, 11, 25, 16, 0, tzinfo=timezone.utc),
                    on_time_request_updated=lambda u, r: req_events.append((u, r)),
                )
                # Upgraded install with existing policy auto-skips First-Run Setup Wizard
                self.assertTrue(rt.config.setup_completed)

                # Rate-limited kid requests (max 3 per 10m window)
                r1 = rt.request_more_time("alice", {"requested_sec": 900, "reason": "homework"})
                rt.request_more_time("alice", {"requested_sec": 900})
                rt.request_more_time("alice", {"requested_sec": 900})
                with self.assertRaises(ValueError):
                    rt.request_more_time("alice", {"requested_sec": 900})
                self.assertEqual(len(req_events), 3)

                # Enroll offline TOTP Parent Access Code & approve r1 walk-up
                enrolled = rt.parent_code.setup(enable=True, account="test-host")
                step = int(_time.time() // 30)
                code = compute_totp(enrolled["secret_b32"], step=step)
                res = rt.approve_with_parent_code("alice", {"code": code, "request_id": r1["id"]})
                self.assertEqual(res["request"]["status"], "approved")
                self.assertEqual(res["status"]["bonus_today"], 900)

                # Claim one-time-per-day 2-minute Save Grace (requires <= 60s remaining)
                with self.assertRaises(ValueError):
                    rt.claim_save_grace("alice")
                rt.grant_time("alice", 30, mode="set", actor="parent")
                grace = rt.claim_save_grace("alice")
                self.assertEqual(grace["granted_sec"], 120)
                self.assertTrue(grace["status"]["save_grace"]["active"])
                with self.assertRaises(ValueError):
                    rt.claim_save_grace("alice")

    def test_cinnamon_lockout_deduplicates_events_and_backs_off_reverification(self):
        """Regression test for #7: DEs without logind LockedHint must not spam lockouts/events."""
        from curfewd.models import LockResult

        class _VerifyingSessionMonitor(_StubSessionMonitor):
            def __init__(self, username: str) -> None:
                super().__init__(username)
                self.screensaver_locked = False
                self.lock_calls = 0
                self.verify_calls = 0
                self.fail_next_lock = False

            def enforce_lockout_action(
                self,
                action,
                username,
                _uid,
                sessions,
                _config,
                lock_message="",
                on_lock_result=None,
            ):
                self.lockout_actions.append((action, username))
                self.lock_calls += 1
                if self.fail_next_lock:
                    self.screensaver_locked = False
                    if on_lock_result:
                        on_lock_result(
                            LockResult(
                                session_id=sessions[0]["session_id"],
                                username=username,
                                locked=False,
                                verified=False,
                                backend="dbus:org.cinnamon.ScreenSaver",
                                stage="failed",
                            )
                        )
                    return
                self.screensaver_locked = True
                if on_lock_result:
                    on_lock_result(
                        LockResult(
                            session_id=sessions[0]["session_id"],
                            username=username,
                            locked=True,
                            verified=True,
                            backend="dbus:org.cinnamon.ScreenSaver",
                            stage="dbus_or_cli",
                        )
                    )

            def verify_user_sessions_locked(self, sessions, _config, on_complete=None):
                self.verify_calls += 1
                if on_complete:
                    on_complete(
                        LockResult(
                            session_id=sessions[0]["session_id"],
                            username=self.username,
                            locked=self.screensaver_locked,
                            verified=True,
                            backend="cinnamon",
                            stage="verify" if self.screensaver_locked else "verify_unlocked",
                        )
                    )

        with tempfile.TemporaryDirectory() as tmp:
            store = AtomicJsonStore(
                config_dir=os.path.join(tmp, "etc"),
                state_dir=os.path.join(tmp, "var"),
            )
            store.ensure_dirs()
            store.save_daemon_config(DaemonConfig(final_countdown_sec=0))
            pol = UserPolicy(
                username="alice",
                daily_limits=dict.fromkeys(WEEKDAYS, 3600),
                curfew_windows={d: [TimeWindow("08:00", "21:00")] for d in WEEKDAYS},
            )
            store.save_user_policy(pol)
            store.save_user_state(
                UserAccountingState(
                    username="alice",
                    date="2026-11-25",
                    week_key="2026-W48",
                    month_key="2026-11",
                    spent_today=3600,
                    spent_balance=3600,
                )
            )

            sess_mon = _VerifyingSessionMonitor("alice")
            fake_mono = [1000.0]

            def _mono() -> float:
                return fake_mono[0]

            with (
                HistoryStore(os.path.join(tmp, "var", "history.db")) as hist,
                mock.patch("curfewd.engine.runtime.time.monotonic", side_effect=_mono),
            ):
                rt = EnforcementRuntime(
                    store=store,
                    history=hist,
                    session_monitor=sess_mon,
                    now_fn=lambda: datetime(2026, 11, 25, 16, 0, tzinfo=timezone.utc),
                )

                # First tick fails to lock -> retries on tick 2 without double-counting lockouts
                sess_mon.fail_next_lock = True
                rt.tick()
                self.assertEqual(sess_mon.lock_calls, 1)
                fake_mono[0] += 4.0
                sess_mon.fail_next_lock = False
                rt.tick()
                self.assertEqual(sess_mon.lock_calls, 2)
                self.assertTrue(sess_mon.screensaver_locked)

                # Next tick (+3s < 5s locked-out re-verify interval) -> 0 lock calls, 0 verify calls
                fake_mono[0] += 3.0
                rt.tick()
                self.assertEqual(sess_mon.lock_calls, 2)
                self.assertEqual(sess_mon.verify_calls, 0)

                # Subsequent ticks across 24s while still locked -> lightweight verify runs every >=5s,
                # confirming locked with ZERO duplicate lock calls or lockout inflation
                for _ in range(7):
                    fake_mono[0] += 3.0
                    rt.tick()
                self.assertEqual(sess_mon.lock_calls, 2)
                self.assertGreaterEqual(sess_mon.verify_calls, 3)

                rep1 = hist.get_usage_history(
                    "alice",
                    days=7,
                    now_local=datetime(2026, 11, 25, 16, 1, tzinfo=timezone.utc),
                )
                self.assertEqual(rep1["totals"]["lockouts"], 1)
                self.assertEqual(
                    len([e for e in rep1["events"] if e["kind"] == "lockout"]),
                    1,
                )

                # Simulate child bypassing/unlocking screensaver while still locked out (without tray!)
                # Caught within 5s locked-out re-verify interval instead of 30s
                prev_verifies = sess_mon.verify_calls
                sess_mon.screensaver_locked = False
                fake_mono[0] += 6.0
                rt.tick()  # triggers lightweight verify -> discovers unlocked
                self.assertEqual(sess_mon.verify_calls, prev_verifies + 1)
                fake_mono[0] += 3.0
                rt.tick()  # re-locks immediately & records genuine 2nd lockout
                self.assertEqual(sess_mon.lock_calls, 3)
                self.assertTrue(sess_mon.screensaver_locked)

                rep2 = hist.get_usage_history(
                    "alice",
                    days=7,
                    now_local=datetime(2026, 11, 25, 16, 2, tzinfo=timezone.utc),
                )
                self.assertEqual(rep2["totals"]["lockouts"], 2)
                self.assertEqual(
                    len([e for e in rep2["events"] if e["kind"] == "lockout"]),
                    2,
                )

    def test_early_unlock_interrupt_and_immediate_relock_recheck(self):
        """F2: Tray early-unlock hint triggers root re-verify and immediate re-lock on unlock."""
        from curfewd.models import LockResult

        class _FastVerifyMonitor(_StubSessionMonitor):
            def __init__(self, username: str) -> None:
                super().__init__(username)
                self.screensaver_locked = False
                self.lock_calls = 0
                self.verify_calls = 0

            def enforce_lockout_action(
                self,
                action,
                username,
                _uid,
                sessions,
                _config,
                lock_message="",
                on_lock_result=None,
            ):
                self.lockout_actions.append((action, username))
                self.lock_calls += 1
                self.screensaver_locked = True
                if on_lock_result:
                    on_lock_result(
                        LockResult(
                            session_id=sessions[0]["session_id"],
                            username=username,
                            locked=True,
                            verified=True,
                            backend="dbus:org.cinnamon.ScreenSaver",
                            stage="dbus_or_cli",
                        )
                    )

            def verify_user_sessions_locked(self, sessions, _config, on_complete=None):
                self.verify_calls += 1
                if on_complete:
                    on_complete(
                        LockResult(
                            session_id=sessions[0]["session_id"],
                            username=self.username,
                            locked=self.screensaver_locked,
                            verified=True,
                            backend="cinnamon",
                            stage="verify" if self.screensaver_locked else "verify_unlocked",
                        )
                    )

        with tempfile.TemporaryDirectory() as tmp:
            store = AtomicJsonStore(
                config_dir=os.path.join(tmp, "etc"),
                state_dir=os.path.join(tmp, "var"),
            )
            store.ensure_dirs()
            store.save_daemon_config(DaemonConfig(final_countdown_sec=0))
            store.save_user_policy(
                UserPolicy(
                    username="alice",
                    daily_limits=dict.fromkeys(WEEKDAYS, 3600),
                    curfew_windows={d: [TimeWindow("08:00", "21:00")] for d in WEEKDAYS},
                )
            )
            store.save_user_state(
                UserAccountingState(
                    username="alice",
                    date="2026-11-25",
                    week_key="2026-W48",
                    month_key="2026-11",
                    spent_today=3600,
                    spent_balance=3600,
                )
            )

            sess_mon = _FastVerifyMonitor("alice")
            fake_mono = [2000.0]
            challenges: list[tuple[str, str]] = []
            unlock_callbacks: list[str] = []

            with (
                HistoryStore(os.path.join(tmp, "var", "history.db")) as hist,
                mock.patch(
                    "curfewd.engine.runtime.time.monotonic", side_effect=lambda: fake_mono[0]
                ),
            ):
                rt = EnforcementRuntime(
                    store=store,
                    history=hist,
                    session_monitor=sess_mon,
                    now_fn=lambda: datetime(2026, 11, 25, 16, 0, tzinfo=timezone.utc),
                    on_screensaver_challenge=lambda u, n: challenges.append((u, n)),
                    on_unlock_detected=unlock_callbacks.append,
                )
                # Not yet evaluated -> trigger_unlock_reverify returns False cleanly
                self.assertFalse(rt.trigger_unlock_reverify("alice"))

                # Initial lockout locks and verifies
                rt.tick()
                self.assertEqual(sess_mon.lock_calls, 1)
                self.assertEqual(len(challenges), 1)

                # Locked-out challenge cadence is tightened to 3s (instead of 15s)
                fake_mono[0] += 3.0
                rt.tick()
                self.assertEqual(len(challenges), 2)

                # User manually unlocks 0.5s later; tray fires NotifySessionUnlocked -> immediate verify
                sess_mon.screensaver_locked = False
                fake_mono[0] += 0.5
                self.assertTrue(rt.trigger_unlock_reverify("alice"))
                self.assertEqual(sess_mon.verify_calls, 1)
                # Root verification discovered unlock -> on_unlock_detected fired
                self.assertEqual(unlock_callbacks, ["alice"])

                # Rate-limited to >=1s against rapid duplicates
                self.assertFalse(rt.trigger_unlock_reverify("alice"))

                # Main-loop callback re-locks immediately without waiting for next tick!
                rt.enforce_unlock_recheck("alice")
                self.assertEqual(sess_mon.lock_calls, 2)
                self.assertTrue(sess_mon.screensaver_locked)

    def test_schema_v2_migration_defaults_admin_users_to_parent_role(self):
        """Regression test for #11: v2 policies lacking 'role' default sudo/admin users to 'parent'."""
        import json

        with tempfile.TemporaryDirectory() as tmp:
            store = AtomicJsonStore(
                config_dir=os.path.join(tmp, "etc"),
                state_dir=os.path.join(tmp, "var"),
            )
            store.ensure_dirs()
            # Seed legacy schema v2 policies without a "role" key
            for uname in ("parentadmin", "kiduser"):
                with open(store.user_policy_path(uname), "w", encoding="utf-8") as fh:
                    json.dump(
                        {
                            "schema_version": 2,
                            "username": uname,
                            "enabled": True,
                            "lockout_action": "lock",
                        },
                        fh,
                    )
            # Explicit v3 role="child" on an admin must still be respected
            with open(store.user_policy_path("explicitadmin"), "w", encoding="utf-8") as fh:
                json.dump(
                    {
                        "schema_version": 3,
                        "username": "explicitadmin",
                        "role": "child",
                        "enabled": True,
                        "lockout_action": "lock",
                    },
                    fh,
                )

            with (
                HistoryStore(os.path.join(tmp, "var", "history.db")) as hist,
                mock.patch(
                    "curfewd.engine.runtime.is_system_admin_user",
                    side_effect=lambda u: u in ("parentadmin", "explicitadmin"),
                ),
            ):
                rt = EnforcementRuntime(
                    store=store,
                    history=hist,
                    session_monitor=_StubSessionMonitor("kiduser"),
                    now_fn=lambda: datetime(2026, 11, 25, 16, 0, tzinfo=timezone.utc),
                )
                self.assertEqual(rt.get_policy("parentadmin").role, "parent")
                self.assertEqual(rt.get_policy("kiduser").role, "child")
                self.assertEqual(rt.get_policy("explicitadmin").role, "child")

    def test_runtime_passes_cached_catalog_and_batches_history_deltas(self):
        """Regression tests for #12 (cached app catalog) and #13 (batched SQLite writes)."""
        from curfewd.engine.applimits import ProcessScanResult

        class _RecordingMonitor:
            def __init__(self) -> None:
                self.received_catalogs: list[list[dict]] = []
                self.received_ignores: list[list[str]] = []

            def scan_user_processes(
                self,
                uid,
                apps,
                category_limits=None,
                catalog=None,
                active_window_pid=None,
                homework_blocked_categories=None,
                collect_passive=True,
                exempt_patterns=None,
                passive_ignore_list=None,
            ):
                self.received_catalogs.append(list(catalog or []))
                self.received_ignores.append(list(passive_ignore_list or []))
                return ProcessScanResult(
                    passive_apps=[("steam:3072000", "Cozy Adventure", "Gaming")]
                )

        with tempfile.TemporaryDirectory() as tmp:
            store = AtomicJsonStore(
                config_dir=os.path.join(tmp, "etc"),
                state_dir=os.path.join(tmp, "var"),
            )
            store.ensure_dirs()
            store.save_daemon_config(
                DaemonConfig(
                    save_interval_sec=30,
                    passive_app_insights_enabled=True,
                    passive_app_ignore_list=["org.example.wallpaper"],
                )
            )
            store.save_user_policy(
                UserPolicy(
                    username="alice",
                    role="child",
                    daily_limits=dict.fromkeys(WEEKDAYS, 7200),
                    curfew_windows={d: [TimeWindow("08:00", "21:00")] for d in WEEKDAYS},
                )
            )
            loader_calls = []

            def _loader(uname: str):
                loader_calls.append(uname)
                return [
                    {"steam_app_id": "3072000", "label": "Cozy Adventure", "category": "Gaming"}
                ]

            rec_mon = _RecordingMonitor()
            fake_mono = [500.0]

            with (
                HistoryStore(os.path.join(tmp, "var", "history.db")) as hist,
                mock.patch(
                    "curfewd.engine.runtime.time.monotonic", side_effect=lambda: fake_mono[0]
                ),
            ):
                rt = EnforcementRuntime(
                    store=store,
                    history=hist,
                    session_monitor=_StubSessionMonitor("alice"),
                    app_limits_monitor=rec_mon,
                    now_fn=lambda: datetime(2026, 11, 25, 16, 0, tzinfo=timezone.utc),
                    app_catalog_loader=_loader,
                )
                with mock.patch.object(
                    hist, "record_daily_delta", wraps=hist.record_daily_delta
                ) as spy_write:
                    # 6 ticks spaced 3s apart (15s elapsed < 30s save_interval) -> 0 SQLite commits yet
                    rt.tick()
                    for _ in range(5):
                        fake_mono[0] += 3.0
                        rt.tick()
                    self.assertEqual(spy_write.call_count, 0)
                    # App catalog loaded once and reused from cache across all 6 ticks
                    self.assertEqual(loader_calls, ["alice"])
                    self.assertEqual(len(rec_mon.received_catalogs), 6)
                    self.assertEqual(rec_mon.received_catalogs[0][0]["steam_app_id"], "3072000")
                    self.assertEqual(rec_mon.received_ignores[0], ["org.example.wallpaper"])

                    # Crossing save_interval_sec flushes all buffered deltas in ONE batched write
                    fake_mono[0] += 20.0
                    rt.tick()
                    self.assertEqual(spy_write.call_count, 1)

                rep = hist.get_usage_history(
                    "alice",
                    days=7,
                    now_local=datetime(2026, 11, 25, 16, 1, tzinfo=timezone.utc),
                )
                self.assertEqual(rep["totals"]["active_sec"], 35)
                self.assertEqual(rep["top_apps"][0]["app_key"], "steam:3072000")
                self.assertEqual(rep["top_apps"][0]["label"], "Cozy Adventure")
                self.assertEqual(rep["top_apps"][0]["active_sec"], 35)

    def test_grant_time_and_pause_notify_lock_screen_only_when_locked(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = AtomicJsonStore(
                config_dir=os.path.join(tmp, "etc"),
                state_dir=os.path.join(tmp, "var"),
            )
            store.ensure_dirs()
            pol = UserPolicy(
                username="alice",
                daily_limits=dict.fromkeys(WEEKDAYS, 3600),
                curfew_windows={d: [TimeWindow("08:00", "23:00")] for d in WEEKDAYS},
            )
            store.save_user_policy(pol)
            store.save_user_state(
                UserAccountingState(
                    username="alice",
                    date="2026-11-25",
                    week_key="2026-W48",
                    month_key="2026-11",
                    spent_today=3600,
                    spent_balance=3600,
                )
            )
            sess_mon = _StubSessionMonitor("alice", locked=True)
            notif_events: list[tuple[str, dict]] = []

            with HistoryStore(os.path.join(tmp, "var", "history.db")) as hist:
                rt = EnforcementRuntime(
                    store=store,
                    history=hist,
                    session_monitor=sess_mon,
                    now_fn=lambda: datetime(2026, 11, 25, 16, 0, tzinfo=timezone.utc),
                    on_client_notification=lambda u, p: notif_events.append((u, p)),
                )
                rt.tick()
                rt.grant_time("alice", 900, "add", actor="parent", note="finished chores")
                self.assertEqual(len(sess_mon.lock_screen_messages), 1)
                self.assertIn("Bonus time added (+15m)", sess_mon.lock_screen_messages[0])
                self.assertIn("finished chores", sess_mon.lock_screen_messages[0])
                self.assertTrue(notif_events[-1][1]["session_locked"])

                rt.pause_user("alice", "", "chore", False, actor="parent")
                self.assertEqual(len(sess_mon.lock_screen_messages), 2)
                self.assertIn("Screen time paused", sess_mon.lock_screen_messages[1])
                self.assertTrue(notif_events[-1][1]["session_locked"])

                sess_mon.locked = False
                rt.tick()
                rt.grant_time("alice", 300, "add", actor="parent")
                self.assertEqual(len(sess_mon.lock_screen_messages), 2)
                self.assertFalse(notif_events[-1][1]["session_locked"])


if __name__ == "__main__":
    unittest.main()
