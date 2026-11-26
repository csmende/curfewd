"""SQLite WAL-mode usage history, per-app insights, time requests, and audit store (/var/lib/curfewd/history.db)."""

import json
import os
import sqlite3
import threading
from collections.abc import Iterable, Iterator
from contextlib import contextmanager, suppress
from datetime import datetime, timedelta, timezone
from typing import Any

from curfewd import DEFAULT_STATE_DIR
from curfewd.models import utc_now_iso
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

CREATE TABLE IF NOT EXISTS daily_app_usage (
    date TEXT NOT NULL,
    username TEXT NOT NULL,
    app_key TEXT NOT NULL,
    label TEXT NOT NULL,
    category TEXT NOT NULL DEFAULT 'App',
    active_sec INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (date, username, app_key)
);

CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts_utc TEXT NOT NULL,
    username TEXT NOT NULL,
    kind TEXT NOT NULL,
    actor TEXT NOT NULL DEFAULT 'daemon',
    detail_json TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS time_requests (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT NOT NULL,
    kind TEXT NOT NULL,
    target TEXT NOT NULL DEFAULT '',
    target_label TEXT NOT NULL DEFAULT '',
    requested_sec INTEGER NOT NULL,
    reason TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'pending',
    created_utc TEXT NOT NULL,
    resolved_utc TEXT NOT NULL DEFAULT '',
    resolved_by TEXT NOT NULL DEFAULT '',
    granted_sec INTEGER NOT NULL DEFAULT 0,
    note TEXT NOT NULL DEFAULT ''
);

CREATE INDEX IF NOT EXISTS idx_events_user_ts ON events(username, ts_utc DESC);
CREATE INDEX IF NOT EXISTS idx_daily_usage_user_date ON daily_usage(username, date DESC);
CREATE INDEX IF NOT EXISTS idx_daily_app_usage_user_date ON daily_app_usage(username, date DESC);
CREATE INDEX IF NOT EXISTS idx_time_requests_status_ts ON time_requests(status, created_utc DESC);
CREATE INDEX IF NOT EXISTS idx_time_requests_user_ts ON time_requests(username, created_utc DESC);
"""


def _row_to_request(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": int(row["id"]),
        "username": str(row["username"]),
        "kind": str(row["kind"]),
        "target": str(row["target"]),
        "target_label": str(row["target_label"]),
        "requested_sec": int(row["requested_sec"]),
        "reason": str(row["reason"]),
        "status": str(row["status"]),
        "created_utc": str(row["created_utc"]),
        "resolved_utc": str(row["resolved_utc"]),
        "resolved_by": str(row["resolved_by"]),
        "granted_sec": int(row["granted_sec"]),
        "note": str(row["note"]),
    }


class HistoryStore:
    """Persists daily screen-time rollups, per-app usage insights, time requests, and audit events."""

    def __init__(self, db_path: str | None = None) -> None:
        self.db_path = db_path or os.path.join(DEFAULT_STATE_DIR, "history.db")
        parent = os.path.dirname(os.path.abspath(self.db_path))
        os.makedirs(parent, mode=0o750, exist_ok=True)
        if os.path.islink(self.db_path):
            raise OSError(f"Refusing to open symlinked history database: {self.db_path}")
        self._lock = threading.RLock()
        self._conn: sqlite3.Connection | None = None
        self._init_db()

    def _get_conn(self) -> sqlite3.Connection:
        if self._conn is None:
            conn = sqlite3.connect(self.db_path, timeout=5.0, check_same_thread=False)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=NORMAL")
            self._conn = conn
        return self._conn

    @contextmanager
    def _transaction(self) -> Iterator[sqlite3.Connection]:
        with self._lock:
            conn = self._get_conn()
            with conn:
                yield conn

    def close(self) -> None:
        """Close the underlying SQLite connection cleanly."""
        with self._lock:
            if self._conn is not None:
                self._conn.close()
                self._conn = None

    def __enter__(self) -> "HistoryStore":
        return self

    def __exit__(self, _exc_type, _exc, _tb) -> None:
        self.close()

    def _init_db(self) -> None:
        with self._transaction() as conn:
            conn.executescript(_SCHEMA_SQL)
            # Forward-migrate schema v1 `playtime_sec` column cleanly without data loss
            cols = {
                row["name"] for row in conn.execute("PRAGMA table_info(daily_usage)").fetchall()
            }
            if "playtime_sec" in cols and "app_limits_sec" not in cols:
                conn.execute("ALTER TABLE daily_usage RENAME COLUMN playtime_sec TO app_limits_sec")
        with suppress(OSError):
            os.chmod(self.db_path, 0o640)

    def record_daily_delta(
        self,
        username: str,
        date_str: str,
        active_sec: int = 0,
        inactive_sec: int = 0,
        app_limits_sec: int = 0,
        bonus_sec: int = 0,
        lockouts: int = 0,
        app_deltas: Iterable[tuple[str, str, str, int]] | None = None,
    ) -> None:
        """Atomically record daily screen-time deltas and optional per-app active deltas."""
        safe = validate_username(username)
        with self._transaction() as conn:
            if any((active_sec, inactive_sec, app_limits_sec, bonus_sec, lockouts)):
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
            if app_deltas:
                for app_key, label, category, delta_sec in app_deltas:
                    clean_key = str(app_key or "").strip()[:120]
                    sec = int(delta_sec)
                    if not clean_key or sec <= 0:
                        continue
                    clean_label = str(label or clean_key).strip()[:120]
                    clean_cat = str(category or "App").strip()[:48] or "App"
                    conn.execute(
                        """
                        INSERT INTO daily_app_usage (date, username, app_key, label, category, active_sec)
                        VALUES (?, ?, ?, ?, ?, ?)
                        ON CONFLICT(date, username, app_key) DO UPDATE SET
                            label = excluded.label,
                            category = excluded.category,
                            active_sec = MAX(0, active_sec + excluded.active_sec)
                        """,
                        (date_str, safe, clean_key, clean_label, clean_cat, sec),
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
        ts = ts_utc or utc_now_iso()
        detail_json = json.dumps(detail or {}, sort_keys=True)
        with self._transaction() as conn:
            conn.execute(
                """
                INSERT INTO events (ts_utc, username, kind, actor, detail_json)
                VALUES (?, ?, ?, ?, ?)
                """,
                (ts, safe, str(kind), str(actor), detail_json),
            )

    def get_app_usage_summary(
        self,
        username: str,
        days: int = 7,
        now_local: datetime | None = None,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        """Return top apps used over the last N days sorted by active_sec descending."""
        safe = validate_username(username)
        span = max(1, min(365, int(days)))
        ref = now_local or datetime.now().astimezone()
        start_date = (ref.date() - timedelta(days=span - 1)).isoformat()

        with self._transaction() as conn:
            rows = conn.execute(
                """
                WITH totals AS (
                    SELECT app_key,
                           SUM(active_sec) AS active_sec,
                           COUNT(DISTINCT date) AS days_used
                    FROM daily_app_usage
                    WHERE username = ? AND date >= ?
                    GROUP BY app_key
                ),
                latest AS (
                    SELECT app_key, label, category,
                           ROW_NUMBER() OVER (PARTITION BY app_key ORDER BY date DESC) AS rn
                    FROM daily_app_usage
                    WHERE username = ? AND date >= ?
                )
                SELECT t.app_key, l.label, l.category, t.active_sec, t.days_used
                FROM totals t
                JOIN latest l ON l.app_key = t.app_key AND l.rn = 1
                ORDER BY t.active_sec DESC, l.label ASC
                LIMIT ?
                """,
                (safe, start_date, safe, start_date, max(1, min(100, int(limit)))),
            ).fetchall()

        return [
            {
                "app_key": str(r["app_key"]),
                "label": str(r["label"]),
                "category": str(r["category"]),
                "active_sec": int(r["active_sec"]),
                "days_used": int(r["days_used"]),
                "daily_avg_sec": int(r["active_sec"]) // span,
            }
            for r in rows
        ]

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

        with self._transaction() as conn:
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

        top_apps = self.get_app_usage_summary(safe, days=span, now_local=ref)
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
            "top_apps": top_apps,
            "events": events,
        }

    # ---------------- Time Requests ("Ask for More Time") ---------------- #

    def create_time_request(
        self,
        username: str,
        kind: str,
        requested_sec: int,
        target: str = "",
        target_label: str = "",
        reason: str = "",
        ts_utc: str | None = None,
    ) -> dict[str, Any]:
        safe = validate_username(username)
        clean_kind = str(kind or "daily").strip().lower()
        if clean_kind not in ("daily", "app", "category"):
            raise ValueError(f"Invalid time request kind: {kind!r}")
        req_sec = max(300, min(7200, int(requested_sec)))
        clean_target = str(target or "").strip()[:120]
        clean_label = str(target_label or clean_target or "Screen Time").strip()[:120]
        clean_reason = str(reason or "").strip()[:160]
        created = ts_utc or utc_now_iso()

        with self._transaction() as conn:
            cur = conn.execute(
                """
                INSERT INTO time_requests
                    (username, kind, target, target_label, requested_sec, reason, status, created_utc)
                VALUES (?, ?, ?, ?, ?, ?, 'pending', ?)
                """,
                (safe, clean_kind, clean_target, clean_label, req_sec, clean_reason, created),
            )
            req_id = int(cur.lastrowid or 0)
            row = conn.execute("SELECT * FROM time_requests WHERE id = ?", (req_id,)).fetchone()
        if row is None:
            raise RuntimeError(f"Inserted time request #{req_id} not found")
        return _row_to_request(row)

    def count_recent_requests(self, username: str, since_utc: str) -> int:
        safe = validate_username(username)
        with self._transaction() as conn:
            row = conn.execute(
                "SELECT COUNT(*) AS cnt FROM time_requests WHERE username = ? AND created_utc >= ?",
                (safe, since_utc),
            ).fetchone()
        return int(row["cnt"]) if row else 0

    def get_time_request(self, request_id: int) -> dict[str, Any] | None:
        with self._transaction() as conn:
            row = conn.execute(
                "SELECT * FROM time_requests WHERE id = ?", (int(request_id),)
            ).fetchone()
        return _row_to_request(row) if row is not None else None

    def list_time_requests(
        self,
        status: str = "",
        username: str = "",
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        clauses: list[str] = []
        params: list[Any] = []
        if status:
            clauses.append("status = ?")
            params.append(str(status).strip().lower())
        if username:
            clauses.append("username = ?")
            params.append(validate_username(username))
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        params.append(max(1, min(200, int(limit))))
        with self._transaction() as conn:
            rows = conn.execute(
                f"SELECT * FROM time_requests {where} ORDER BY id DESC LIMIT ?",  # nosec B608 - parameterized clauses only
                tuple(params),
            ).fetchall()
        return [_row_to_request(r) for r in rows]

    def resolve_time_request(
        self,
        request_id: int,
        approve: bool,
        resolved_by: str,
        granted_sec: int = 0,
        note: str = "",
        ts_utc: str | None = None,
    ) -> dict[str, Any] | None:
        status = "approved" if approve else "denied"
        resolved = ts_utc or utc_now_iso()
        clean_note = str(note or "").strip()[:160]
        with self._transaction() as conn:
            cur = conn.execute(
                """
                UPDATE time_requests
                SET status = ?, resolved_utc = ?, resolved_by = ?, granted_sec = ?, note = ?
                WHERE id = ? AND status = 'pending'
                """,
                (status, resolved, str(resolved_by), int(granted_sec), clean_note, int(request_id)),
            )
            if cur.rowcount != 1:
                return None
            updated = conn.execute(
                "SELECT * FROM time_requests WHERE id = ?", (int(request_id),)
            ).fetchone()
        return _row_to_request(updated) if updated is not None else None

    def expire_stale_requests(
        self, max_age_hours: int = 24, now_utc: datetime | None = None
    ) -> int:
        ref = now_utc or datetime.now(timezone.utc)
        cutoff = (ref - timedelta(hours=max(1, int(max_age_hours)))).isoformat()
        with self._transaction() as conn:
            cur = conn.execute(
                """
                UPDATE time_requests
                SET status = 'expired', resolved_utc = ?, resolved_by = 'daemon'
                WHERE status = 'pending' AND created_utc < ?
                """,
                (ref.isoformat(), cutoff),
            )
            return int(cur.rowcount)

    def prune(
        self,
        daily_retention_days: int = 365,
        event_retention_days: int = 90,
        now_utc: datetime | None = None,
    ) -> tuple[int, int]:
        ref = now_utc or datetime.now(timezone.utc)
        daily_cutoff = (ref.date() - timedelta(days=max(7, int(daily_retention_days)))).isoformat()
        event_cutoff = (ref - timedelta(days=max(7, int(event_retention_days)))).isoformat()
        with self._transaction() as conn:
            c1 = conn.execute("DELETE FROM daily_usage WHERE date < ?", (daily_cutoff,))
            conn.execute("DELETE FROM daily_app_usage WHERE date < ?", (daily_cutoff,))
            c2 = conn.execute("DELETE FROM events WHERE ts_utc < ?", (event_cutoff,))
            conn.execute(
                "DELETE FROM time_requests WHERE status != 'pending' AND created_utc < ?",
                (event_cutoff,),
            )
            return c1.rowcount, c2.rowcount
