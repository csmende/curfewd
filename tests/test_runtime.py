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
    def __init__(self, username: str) -> None:
        self.username = username
        self.lockout_actions: list[tuple[str, str]] = []

    def list_user_sessions(self, _tracked_types=None):
        return {
            self.username: [
                {
                    "session_id": "c1",
                    "uid": 1000,
                    "username": self.username,
                    "session_type": "x11",
                    "active": True,
                    "locked": False,
                    "idle": False,
                    "display": ":0",
                    "desktop": "X-Cinnamon",
                    "leader_pid": 1234,
                }
            ]
        }

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


if __name__ == "__main__":
    unittest.main()
