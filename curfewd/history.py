"""SQLite WAL-mode usage history and audit event store (/var/lib/curfewd/history.db)."""

from datetime import datetime, timedelta, timezone
import json
import os
import sqlite3
from typing import Any

from curfewd import DEFAULT_STATE_DIR
from curfewd.parsing import validate_username

_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS daily_usage (
    date TEXT NOT NULL,
    username TEXT NOT NULL,
    active_sec INTEGER NOT NULL DEFAULT 0,
    inactive_sec INTEGER NOT NULL DEFAULT 0,
    app_limits_sec INTEGER NOT NULL DEFAULT 0,
    bonus_sec INTEGER NOT NULL DEFAULT 0,
    lockouts INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (date, username)
);

CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts_utc TEXT NOT NULL,
    username TEXT NOT NULL,
    kind TEXT NOT NULL,
    actor TEXT NOT NULL DEFAULT 'daemon',
    detail_json TEXT NOT NULL DEFAULT '{}'
);

CREATE INDEX IF NOT EXISTS idx_events_user_ts ON events(username, ts_utc DESC);
CREATE INDEX IF NOT EXISTS idx_daily_usage_user_date ON daily_usage(username, date DESC);
"""


class HistoryStore:
    """Persists daily screen-time rollups and enforcement/admin audit events."""

    def __init__(self, db_path: str | None = None) -> None:
        self.db_path = db_path or os.path.join(DEFAULT_STATE_DIR, "history.db")
        parent = os.path.dirname(os.path.abspath(self.db_path))
        os.makedirs(parent, mode=0o750, exist_ok=True)
        if os.path.islink(self.db_path):
            raise OSError(f"Refusing to open symlinked history database: {self.db_path}")
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=5.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        return conn

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.executescript(_SCHEMA_SQL)
            # Forward-migrate schema v1 `playtime_sec` column cleanly without data loss
            cols = {row["name"] for row in conn.execute("PRAGMA table_info(daily_usage)").fetchall()}
            if "playtime_sec" in cols and "app_limits_sec" not in cols:
                conn.execute("ALTER TABLE daily_usage RENAME COLUMN playtime_sec TO app_limits_sec")
        try:
            os.chmod(self.db_path, 0o640)
        except OSError:
            pass

    def record_daily_delta(
        self,
        username: str,
        date_str: str,
        active_sec: int = 0,
        inactive_sec: int = 0,
        app_limits_sec: int = 0,
        bonus_sec: int = 0,
        lockouts: int = 0,
    ) -> None:
        safe = validate_username(username)
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO daily_usage (date, username, active_sec, inactive_sec, app_limits_sec, bonus_sec, lockouts)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(date, username) DO UPDATE SET
                    active_sec = MAX(0, active_sec + excluded.active_sec),
                    inactive_sec = MAX(0, inactive_sec + excluded.inactive_sec),
                    app_limits_sec = MAX(0, app_limits_sec + excluded.app_limits_sec),
                    bonus_sec = bonus_sec + excluded.bonus_sec,
                    lockouts = MAX(0, lockouts + excluded.lockouts)
                """,
                (
                    date_str,
                    safe,
                    int(active_sec),
                    int(inactive_sec),
                    int(app_limits_sec),
                    int(bonus_sec),
                    int(lockouts),
                ),
            )

    def record_event(
        self,
        username: str,
        kind: str,
        actor: str = "daemon",
        detail: dict[str, Any] | None = None,
        ts_utc: str | None = None,
    ) -> None:
        safe = validate_username(username)
        ts = ts_utc or datetime.now(timezone.utc).isoformat()
        detail_json = json.dumps(detail or {}, sort_keys=True)
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO events (ts_utc, username, kind, actor, detail_json)
                VALUES (?, ?, ?, ?, ?)
                """,
                (ts, safe, str(kind), str(actor), detail_json),
            )

    def get_usage_history(
        self,
        username: str,
        days: int = 7,
        max_events: int = 50,
        now_local: datetime | None = None,
    ) -> dict[str, Any]:
        safe = validate_username(username)
        span = max(1, min(365, int(days)))
        ref = now_local or datetime.now().astimezone()
        start_date = (ref.date() - timedelta(days=span - 1)).isoformat()

        with self._connect() as conn:
            daily_rows = conn.execute(
                """
                SELECT date, active_sec, inactive_sec, app_limits_sec, bonus_sec, lockouts
                FROM daily_usage
                WHERE username = ? AND date >= ?
                ORDER BY date ASC
                """,
                (safe, start_date),
            ).fetchall()

            event_rows = conn.execute(
                """
                SELECT id, ts_utc, kind, actor, detail_json
                FROM events
                WHERE username = ?
                ORDER BY id DESC
                LIMIT ?
                """,
                (safe, max(1, min(200, int(max_events)))),
            ).fetchall()

        daily = [
            {
                "date": r["date"],
                "active_sec": int(r["active_sec"]),
                "inactive_sec": int(r["inactive_sec"]),
                "app_limits_sec": int(r["app_limits_sec"]),
                "bonus_sec": int(r["bonus_sec"]),
                "lockouts": int(r["lockouts"]),
            }
            for r in daily_rows
        ]
        events = [
            {
                "id": int(r["id"]),
                "ts_utc": r["ts_utc"],
                "kind": r["kind"],
                "actor": r["actor"],
                "detail": json.loads(r["detail_json"] or "{}"),
            }
            for r in event_rows
        ]
        totals = {
            "active_sec": sum(d["active_sec"] for d in daily),
            "inactive_sec": sum(d["inactive_sec"] for d in daily),
            "app_limits_sec": sum(d["app_limits_sec"] for d in daily),
            "bonus_sec": sum(d["bonus_sec"] for d in daily),
            "lockouts": sum(d["lockouts"] for d in daily),
        }
        return {
            "username": safe,
            "days": span,
            "start_date": start_date,
            "daily": daily,
            "totals": totals,
            "events": events,
        }

    def prune(
        self,
        daily_retention_days: int = 365,
        event_retention_days: int = 90,
        now_utc: datetime | None = None,
    ) -> tuple[int, int]:
        ref = now_utc or datetime.now(timezone.utc)
        daily_cutoff = (ref.date() - timedelta(days=max(7, int(daily_retention_days)))).isoformat()
        event_cutoff = (ref - timedelta(days=max(7, int(event_retention_days)))).isoformat()
        with self._connect() as conn:
            c1 = conn.execute("DELETE FROM daily_usage WHERE date < ?", (daily_cutoff,))
            c2 = conn.execute("DELETE FROM events WHERE ts_utc < ?", (event_cutoff,))
            return c1.rowcount, c2.rowcount
