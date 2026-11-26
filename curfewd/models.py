"""Typed schema-versioned data models for curfewd policies, state, and telemetry."""

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from itertools import pairwise
from typing import Any

from curfewd import SCHEMA_VERSION
from curfewd.parsing import (
    SECONDS_PER_DAY,
    WEEKDAYS,
    minutes_to_hhmm,
    parse_hhmm_to_minutes,
    validate_username,
)

LOCKOUT_ACTIONS = ("lock", "suspend", "terminate", "kill", "shutdown")
ENFORCEMENT_STATES = ("ok", "warning", "countdown", "locked_out", "paused", "unrestricted")
DEFAULT_WARNING_THRESHOLDS = [3600, 1800, 600, 300, 60]
DEFAULT_TRACKED_SESSION_TYPES = ["x11", "wayland", "mir"]
DEFAULT_EXCLUDED_USERS = [
    "gdm",
    "gdm3",
    "lightdm",
    "sddm",
    "lxdm",
    "mdm",
    "xdm",
    "kdm",
    "root",
    "nobody",
]


def utc_now_iso() -> str:
    """Return current UTC timestamp in ISO-8601 format."""
    return datetime.now(timezone.utc).isoformat()


@dataclass
class TimeWindow:
    """An allowed time interval within a single day (minute precision)."""

    start: str = "00:00"
    end: str = "24:00"

    def __post_init__(self) -> None:
        s_min = parse_hhmm_to_minutes(self.start)
        e_min = parse_hhmm_to_minutes(self.end)
        if e_min <= s_min:
            raise ValueError(f"Window end must exceed start: {self.start}-{self.end}")
        self.start = minutes_to_hhmm(s_min)
        self.end = minutes_to_hhmm(e_min)

    @property
    def start_minutes(self) -> int:
        return parse_hhmm_to_minutes(self.start)

    @property
    def end_minutes(self) -> int:
        return parse_hhmm_to_minutes(self.end)

    def to_dict(self) -> dict[str, Any]:
        return {"start": self.start, "end": self.end}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "TimeWindow":
        return cls(
            start=str(data["start"]),
            end=str(data["end"]),
        )


@dataclass
class PauseState:
    """Non-destructive enforcement pause state (preserves saved schedule)."""

    active: bool = False
    mode: str = "none"  # "none" | "indefinite" | "until"
    until_utc: str | None = None
    reason: str = ""
    hide_tray_while_paused: bool = True

    def __post_init__(self) -> None:
        self.active = bool(self.active)
        if self.mode not in ("none", "indefinite", "until"):
            raise ValueError(f"Invalid pause mode: {self.mode!r}")
        if not self.active:
            self.mode = "none"
            self.until_utc = None
            self.reason = ""
        elif self.mode == "until":
            if not self.until_utc:
                raise ValueError("Pause mode 'until' requires until_utc")
            # Validate ISO timestamp
            datetime.fromisoformat(self.until_utc.replace("Z", "+00:00"))
        elif self.mode == "none":
            self.mode = "indefinite"
        self.hide_tray_while_paused = bool(self.hide_tray_while_paused)

    def is_effective(self, now_utc: datetime) -> bool:
        """Return True if pause is currently active and not expired."""
        if not self.active:
            return False
        if self.mode == "until" and self.until_utc:
            until_dt = datetime.fromisoformat(self.until_utc.replace("Z", "+00:00"))
            if until_dt.tzinfo is None:
                until_dt = until_dt.replace(tzinfo=timezone.utc)
            return now_utc < until_dt
        return True

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> "PauseState":
        if not data:
            return cls()
        return cls(
            active=bool(data.get("active", False)),
            mode=str(data.get("mode", "none")),
            until_utc=data.get("until_utc"),
            reason=str(data.get("reason", "")),
            hide_tray_while_paused=bool(data.get("hide_tray_while_paused", True)),
        )


@dataclass
class AppLimitRule:
    """A tracked application or game rule with its own daily time limit."""

    pattern: str
    label: str = ""
    daily_limit_sec: int = 3600
    category: str = ""

    def __post_init__(self) -> None:
        self.pattern = str(self.pattern).strip()
        if not self.pattern:
            raise ValueError("App limit pattern cannot be empty")
        self.label = str(self.label or self.pattern).strip()
        self.daily_limit_sec = int(self.daily_limit_sec)
        if self.daily_limit_sec < 0 or self.daily_limit_sec > SECONDS_PER_DAY:
            raise ValueError(f"App daily_limit_sec out of range (0..86400): {self.daily_limit_sec}")
        self.category = str(self.category or "").strip()

    def to_dict(self) -> dict[str, Any]:
        return {
            "pattern": self.pattern,
            "label": self.label,
            "daily_limit_sec": self.daily_limit_sec,
            "category": self.category,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any], default_limit_sec: int = 3600) -> "AppLimitRule":
        raw_lim = data.get("daily_limit_sec")
        limit_sec = default_limit_sec if raw_lim is None else int(raw_lim)
        return cls(
            pattern=str(data["pattern"]),
            label=str(data.get("label", "")),
            daily_limit_sec=limit_sec,
            category=str(data.get("category", "")),
        )


@dataclass
class AppLimitsPolicy:
    """Per-user App Limits configuration (individual daily limits per tracked app)."""

    enabled: bool = False
    override_main: bool = False
    apps: list[AppLimitRule] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.enabled = bool(self.enabled)
        self.override_main = bool(self.override_main)
        self.apps = [
            a if isinstance(a, AppLimitRule) else AppLimitRule.from_dict(a) for a in self.apps
        ]

    def to_dict(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "override_main": self.override_main,
            "apps": [a.to_dict() for a in self.apps],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> "AppLimitsPolicy":
        if not data:
            return cls()
        # Preserve legacy v0.7.0 shared daily_limits as fallback for apps lacking daily_limit_sec
        default_limit = 3600
        legacy_limits = data.get("daily_limits")
        if isinstance(legacy_limits, dict):
            for day in WEEKDAYS:
                val = int(legacy_limits.get(day, 0) or 0)
                if 0 < val <= SECONDS_PER_DAY:
                    default_limit = val
                    break
        return cls(
            enabled=bool(data.get("enabled", False)),
            override_main=bool(data.get("override_main", False)),
            apps=[
                a
                if isinstance(a, AppLimitRule)
                else AppLimitRule.from_dict(a, default_limit_sec=default_limit)
                for a in data.get("apps", [])
            ],
        )


def default_daily_limits(seconds: int = SECONDS_PER_DAY) -> dict[str, int]:
    return dict.fromkeys(WEEKDAYS, seconds)


def default_curfew_windows() -> dict[str, list[TimeWindow]]:
    return {day: [TimeWindow("00:00", "24:00")] for day in WEEKDAYS}


@dataclass
class UserPolicy:
    """Persisted per-user curfewd policy (/var/lib/curfewd/users/<user>.json)."""

    username: str
    schema_version: int = SCHEMA_VERSION
    enabled: bool = True
    lockout_action: str = "lock"
    daily_limits: dict[str, int] = field(default_factory=default_daily_limits)
    weekly_limit: int | None = None
    monthly_limit: int | None = None
    curfew_windows: dict[str, list[TimeWindow]] = field(default_factory=default_curfew_windows)
    track_inactive: bool = False
    hide_tray_icon: bool = False
    pause: PauseState = field(default_factory=PauseState)
    app_limits: AppLimitsPolicy = field(default_factory=AppLimitsPolicy)

    def __post_init__(self) -> None:
        self.username = validate_username(self.username)
        self.schema_version = max(SCHEMA_VERSION, int(self.schema_version))
        self.enabled = bool(self.enabled)
        if self.lockout_action not in LOCKOUT_ACTIONS:
            raise ValueError(
                f"Invalid lockout_action {self.lockout_action!r}; expected one of {LOCKOUT_ACTIONS}"
            )
        norm_limits: dict[str, int] = {}
        for day in WEEKDAYS:
            val = int(self.daily_limits.get(day, SECONDS_PER_DAY))
            if val < 0 or val > SECONDS_PER_DAY:
                raise ValueError(f"Daily limit for {day} out of range (0..86400): {val}")
            norm_limits[day] = val
        self.daily_limits = norm_limits
        if self.weekly_limit is not None:
            self.weekly_limit = int(self.weekly_limit)
            if self.weekly_limit <= 0 or self.weekly_limit >= SECONDS_PER_DAY * 7:
                self.weekly_limit = None
        if self.monthly_limit is not None:
            self.monthly_limit = int(self.monthly_limit)
            if self.monthly_limit <= 0 or self.monthly_limit >= SECONDS_PER_DAY * 31:
                self.monthly_limit = None

        norm_windows: dict[str, list[TimeWindow]] = {}
        for day in WEEKDAYS:
            raw_list = self.curfew_windows.get(day)
            if raw_list is None:
                norm_windows[day] = [TimeWindow("00:00", "24:00")]
            else:
                parsed = [
                    w if isinstance(w, TimeWindow) else TimeWindow.from_dict(w) for w in raw_list
                ]
                parsed.sort(key=lambda w: (w.start_minutes, w.end_minutes))
                for prev, curr in pairwise(parsed):
                    if curr.start_minutes < prev.end_minutes:
                        raise ValueError(
                            f"Overlapping curfew windows on {day}: {prev.start}-{prev.end} vs {curr.start}-{curr.end}"
                        )
                norm_windows[day] = parsed
        self.curfew_windows = norm_windows
        self.track_inactive = bool(self.track_inactive)
        self.hide_tray_icon = bool(self.hide_tray_icon)
        if not isinstance(self.pause, PauseState):
            self.pause = PauseState.from_dict(self.pause)
        if not isinstance(self.app_limits, AppLimitsPolicy):
            self.app_limits = AppLimitsPolicy.from_dict(self.app_limits)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION,
            "username": self.username,
            "enabled": self.enabled,
            "lockout_action": self.lockout_action,
            "daily_limits": dict(self.daily_limits),
            "weekly_limit": self.weekly_limit,
            "monthly_limit": self.monthly_limit,
            "curfew_windows": {
                day: [w.to_dict() for w in self.curfew_windows[day]] for day in WEEKDAYS
            },
            "track_inactive": self.track_inactive,
            "hide_tray_icon": self.hide_tray_icon,
            "pause": self.pause.to_dict(),
            "app_limits": self.app_limits.to_dict(),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "UserPolicy":
        # Forward-migrate schema v1 `playtime` key cleanly into `app_limits`
        raw_app_limits = data.get("app_limits")
        if raw_app_limits is None:
            raw_app_limits = data.get("playtime")
        return cls(
            username=str(data["username"]),
            schema_version=SCHEMA_VERSION,
            enabled=bool(data.get("enabled", True)),
            lockout_action=str(data.get("lockout_action", "lock")),
            daily_limits=dict(data.get("daily_limits", default_daily_limits())),
            weekly_limit=data.get("weekly_limit"),
            monthly_limit=data.get("monthly_limit"),
            curfew_windows=dict(data.get("curfew_windows", {})),
            track_inactive=bool(data.get("track_inactive", False)),
            hide_tray_icon=bool(data.get("hide_tray_icon", False)),
            pause=PauseState.from_dict(data.get("pause")),
            app_limits=AppLimitsPolicy.from_dict(raw_app_limits),
        )


@dataclass
class UserAccountingState:
    """Persisted per-user live time accounting counters (/var/lib/curfewd/state/<user>.json)."""

    username: str
    schema_version: int = SCHEMA_VERSION
    date: str = ""  # YYYY-MM-DD (local)
    week_key: str = ""  # YYYY-Www (ISO week)
    month_key: str = ""  # YYYY-MM
    spent_today: int = 0
    spent_balance: int = (
        0  # effective spent after bonus grants (can be negative when bonus > spent)
    )
    spent_week: int = 0
    spent_month: int = 0
    bonus_today: int = 0
    app_limits_spent_today: int = 0
    app_limits_balance: int = 0
    app_spent_today: dict[str, int] = field(default_factory=dict)
    app_bonus_today: dict[str, int] = field(default_factory=dict)
    today_limit_override: int | None = None
    today_windows_override: list[dict[str, str]] | None = None
    last_checked_utc: str = ""

    def __post_init__(self) -> None:
        self.username = validate_username(self.username)
        self.schema_version = max(SCHEMA_VERSION, int(self.schema_version))
        self.spent_today = max(0, int(self.spent_today))
        self.spent_balance = int(self.spent_balance)
        self.spent_week = max(0, int(self.spent_week))
        self.spent_month = max(0, int(self.spent_month))
        self.bonus_today = int(self.bonus_today)
        self.app_limits_spent_today = max(0, int(self.app_limits_spent_today))
        self.app_limits_balance = int(self.app_limits_balance)
        self.app_spent_today = {
            str(k).strip(): max(0, int(v))
            for k, v in (self.app_spent_today or {}).items()
            if str(k).strip()
        }
        self.app_bonus_today = {
            str(k).strip(): int(v)
            for k, v in (self.app_bonus_today or {}).items()
            if str(k).strip()
        }
        if self.today_limit_override is not None:
            lim = int(self.today_limit_override)
            if lim < 0 or lim > SECONDS_PER_DAY:
                raise ValueError(f"today_limit_override out of range (0..86400): {lim}")
            self.today_limit_override = lim
        if self.today_windows_override is not None:
            parsed = [
                w if isinstance(w, TimeWindow) else TimeWindow.from_dict(w)
                for w in self.today_windows_override
            ]
            parsed.sort(key=lambda w: (w.start_minutes, w.end_minutes))
            for prev, curr in pairwise(parsed):
                if curr.start_minutes < prev.end_minutes:
                    raise ValueError(
                        f"Overlapping today_windows_override: {prev.start}-{prev.end} vs {curr.start}-{curr.end}"
                    )
            self.today_windows_override = [w.to_dict() for w in parsed]

    def to_dict(self) -> dict[str, Any]:
        out = asdict(self)
        out["schema_version"] = SCHEMA_VERSION
        return out

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "UserAccountingState":
        # Forward-migrate schema v1 `playtime_*` counters cleanly
        al_spent = data.get("app_limits_spent_today")
        if al_spent is None:
            al_spent = data.get("playtime_spent_today", 0)
        al_bal = data.get("app_limits_balance")
        if al_bal is None:
            al_bal = data.get("playtime_balance", 0)
        return cls(
            username=str(data["username"]),
            schema_version=SCHEMA_VERSION,
            date=str(data.get("date", "")),
            week_key=str(data.get("week_key", "")),
            month_key=str(data.get("month_key", "")),
            spent_today=int(data.get("spent_today", 0)),
            spent_balance=int(data.get("spent_balance", 0)),
            spent_week=int(data.get("spent_week", 0)),
            spent_month=int(data.get("spent_month", 0)),
            bonus_today=int(data.get("bonus_today", 0)),
            app_limits_spent_today=int(al_spent),
            app_limits_balance=int(al_bal),
            app_spent_today=dict(data.get("app_spent_today", {}) or {}),
            app_bonus_today=dict(data.get("app_bonus_today", {}) or {}),
            today_limit_override=data.get("today_limit_override"),
            today_windows_override=data.get("today_windows_override"),
            last_checked_utc=str(data.get("last_checked_utc", "")),
        )


@dataclass
class DaemonConfig:
    """Global daemon configuration (/etc/curfewd/daemon.json)."""

    schema_version: int = SCHEMA_VERSION
    poll_interval_sec: int = 3
    save_interval_sec: int = 30
    warning_thresholds_sec: list[int] = field(
        default_factory=lambda: list(DEFAULT_WARNING_THRESHOLDS)
    )
    final_countdown_sec: int = 15
    session_lock_fallback: bool = True
    release_x11_grab_on_lock: bool = True
    use_locked_hint: bool = True
    app_limits_enabled: bool = True
    tracked_session_types: list[str] = field(
        default_factory=lambda: list(DEFAULT_TRACKED_SESSION_TYPES)
    )
    excluded_users: list[str] = field(default_factory=lambda: list(DEFAULT_EXCLUDED_USERS))
    history_retention_days: int = 365
    event_retention_days: int = 90
    log_level: str = "INFO"

    def __post_init__(self) -> None:
        self.schema_version = max(SCHEMA_VERSION, int(self.schema_version))
        self.poll_interval_sec = max(1, min(60, int(self.poll_interval_sec)))
        self.save_interval_sec = max(5, min(300, int(self.save_interval_sec)))
        self.warning_thresholds_sec = sorted(
            {int(x) for x in self.warning_thresholds_sec if int(x) > 0},
            reverse=True,
        )
        self.final_countdown_sec = max(0, min(120, int(self.final_countdown_sec)))
        self.session_lock_fallback = bool(self.session_lock_fallback)
        self.release_x11_grab_on_lock = bool(self.release_x11_grab_on_lock)
        self.use_locked_hint = bool(self.use_locked_hint)
        self.app_limits_enabled = bool(self.app_limits_enabled)
        self.tracked_session_types = [
            str(s).strip().lower() for s in self.tracked_session_types if str(s).strip()
        ]
        self.excluded_users = [str(u).strip() for u in self.excluded_users if str(u).strip()]
        self.history_retention_days = max(7, min(3650, int(self.history_retention_days)))
        self.event_retention_days = max(7, min(3650, int(self.event_retention_days)))
        lvl = str(self.log_level).upper()
        if lvl not in ("DEBUG", "INFO", "WARNING", "ERROR"):
            raise ValueError(f"Invalid log_level: {self.log_level!r}")
        self.log_level = lvl

    def to_dict(self) -> dict[str, Any]:
        out = asdict(self)
        out["schema_version"] = SCHEMA_VERSION
        return out

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> "DaemonConfig":
        if not data:
            return cls()
        # Forward-migrate schema v1 `playtime_enabled` key cleanly
        raw_al_enabled = data.get("app_limits_enabled")
        if raw_al_enabled is None:
            raw_al_enabled = data.get("playtime_enabled", True)
        return cls(
            schema_version=SCHEMA_VERSION,
            poll_interval_sec=int(data.get("poll_interval_sec", 3)),
            save_interval_sec=int(data.get("save_interval_sec", 30)),
            warning_thresholds_sec=list(
                data.get("warning_thresholds_sec", DEFAULT_WARNING_THRESHOLDS)
            ),
            final_countdown_sec=int(data.get("final_countdown_sec", 15)),
            session_lock_fallback=bool(data.get("session_lock_fallback", True)),
            release_x11_grab_on_lock=bool(data.get("release_x11_grab_on_lock", True)),
            use_locked_hint=bool(data.get("use_locked_hint", True)),
            app_limits_enabled=bool(raw_al_enabled),
            tracked_session_types=list(
                data.get("tracked_session_types", DEFAULT_TRACKED_SESSION_TYPES)
            ),
            excluded_users=list(data.get("excluded_users", DEFAULT_EXCLUDED_USERS)),
            history_retention_days=int(data.get("history_retention_days", 365)),
            event_retention_days=int(data.get("event_retention_days", 90)),
            log_level=str(data.get("log_level", "INFO")),
        )


@dataclass
class SessionSummary:
    """Lightweight representation of an active user session."""

    session_id: str
    session_type: str = "x11"
    desktop: str = ""
    active: bool = False
    locked: bool = False
    display: str = ""
    leader_pid: int = 0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class LockResult:
    """Structured outcome of enforcing a lock on one graphical session."""

    session_id: str
    username: str
    locked: bool
    verified: bool
    backend: str = ""
    stage: str = ""  # "logind" | "dbus_or_cli" | "xf86ungrab" | "fullscreen_release" | "failed"
    elapsed_ms: int = 0
    error: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
