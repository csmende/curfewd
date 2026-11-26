"""Typed schema-versioned data models for curfewd policies, state, and telemetry (Schema v3)."""

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
    validate_iso_date,
    validate_username,
)

LOCKOUT_ACTIONS = ("lock", "suspend", "terminate", "kill", "shutdown")
ENFORCEMENT_STATES = (
    "ok",
    "warning",
    "countdown",
    "locked_out",
    "paused",
    "homework",
    "unrestricted",
)
USER_ROLES = ("child", "parent")
HOLIDAY_MODES = ("weekend", "pause", "custom")
DEFAULT_WARNING_THRESHOLDS = [3600, 1800, 600, 300, 60]
DEFAULT_APP_WARNING_THRESHOLDS = [600, 300, 60]
DEFAULT_TRACKED_SESSION_TYPES = ["x11", "wayland", "mir"]
DEFAULT_HOMEWORK_BLOCKED_CATEGORIES = ["Gaming", "Social"]
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


def _parse_sorted_windows(raw_list: list[Any], label: str) -> list["TimeWindow"]:
    parsed = [w if isinstance(w, TimeWindow) else TimeWindow.from_dict(w) for w in raw_list]
    parsed.sort(key=lambda w: (w.start_minutes, w.end_minutes))
    for prev, curr in pairwise(parsed):
        if curr.start_minutes < prev.end_minutes:
            raise ValueError(
                f"Overlapping {label}: {prev.start}-{prev.end} vs {curr.start}-{curr.end}"
            )
    return parsed


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
            if not isinstance(self.until_utc, str) or not self.until_utc.strip():
                raise ValueError("Pause mode 'until' requires ISO-8601 string until_utc")
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
class HolidayOverride:
    """Scheduled date-range holiday or vacation override (preserves weekly template)."""

    start_date: str
    end_date: str
    mode: str = "weekend"  # "weekend" | "pause" | "custom"
    label: str = ""
    id: str = ""
    daily_limit_sec: int | None = None
    windows: list[TimeWindow] | None = None

    def __post_init__(self) -> None:
        self.start_date = validate_iso_date(self.start_date)
        self.end_date = validate_iso_date(self.end_date)
        if self.end_date < self.start_date:
            raise ValueError(
                f"Holiday end_date ({self.end_date}) must be >= start_date ({self.start_date})"
            )
        self.mode = str(self.mode or "weekend").strip().lower()
        if self.mode not in HOLIDAY_MODES:
            raise ValueError(
                f"Invalid holiday mode: {self.mode!r}; expected one of {HOLIDAY_MODES}"
            )
        self.label = str(self.label or "").strip()[:80]
        self.id = str(self.id or f"{self.start_date}_{self.end_date}_{self.mode}").strip()[:64]
        if self.daily_limit_sec is not None:
            lim = int(self.daily_limit_sec)
            if lim < 0 or lim > SECONDS_PER_DAY:
                raise ValueError(f"Holiday daily_limit_sec out of range (0..86400): {lim}")
            self.daily_limit_sec = lim
        if self.windows is not None:
            self.windows = _parse_sorted_windows(self.windows, f"holiday {self.id} windows")

    def covers(self, date_str: str) -> bool:
        return self.start_date <= date_str <= self.end_date

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "label": self.label,
            "start_date": self.start_date,
            "end_date": self.end_date,
            "mode": self.mode,
            "daily_limit_sec": self.daily_limit_sec,
            "windows": [w.to_dict() for w in self.windows] if self.windows is not None else None,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "HolidayOverride":
        raw_wins = data.get("windows")
        return cls(
            id=str(data.get("id", "")),
            label=str(data.get("label", "")),
            start_date=str(data["start_date"]),
            end_date=str(data["end_date"]),
            mode=str(data.get("mode", "weekend")),
            daily_limit_sec=data.get("daily_limit_sec"),
            windows=[TimeWindow.from_dict(w) for w in raw_wins]
            if isinstance(raw_wins, list)
            else None,
        )


@dataclass
class HomeworkModeState:
    """Homework / Study Mode: pauses main daily countdown while blocking distracting categories."""

    active: bool = False
    mode: str = "none"  # "none" | "indefinite" | "until"
    until_utc: str | None = None
    reason: str = ""
    scheduled_windows: dict[str, list[TimeWindow]] = field(default_factory=dict)
    allowed_apps: list[str] = field(default_factory=list)
    blocked_categories: list[str] = field(
        default_factory=lambda: list(DEFAULT_HOMEWORK_BLOCKED_CATEGORIES)
    )

    def __post_init__(self) -> None:
        self.active = bool(self.active)
        if self.mode not in ("none", "indefinite", "until"):
            raise ValueError(f"Invalid homework mode: {self.mode!r}")
        if not self.active:
            self.mode = "none"
            self.until_utc = None
            self.reason = ""
        elif self.mode == "until":
            if not isinstance(self.until_utc, str) or not self.until_utc.strip():
                raise ValueError("Homework mode 'until' requires ISO-8601 string until_utc")
            datetime.fromisoformat(self.until_utc.replace("Z", "+00:00"))
        elif self.mode == "none":
            self.mode = "indefinite"
        self.reason = str(self.reason or "").strip()[:120]

        norm_sched: dict[str, list[TimeWindow]] = {}
        for day, raw_list in (self.scheduled_windows or {}).items():
            if day in WEEKDAYS and raw_list:
                norm_sched[day] = _parse_sorted_windows(raw_list, f"homework windows on {day}")
        self.scheduled_windows = norm_sched
        self.allowed_apps = sorted(
            {str(p).strip().lower() for p in (self.allowed_apps or []) if str(p).strip()}
        )
        cats = [str(c).strip() for c in (self.blocked_categories or []) if str(c).strip()]
        self.blocked_categories = cats or list(DEFAULT_HOMEWORK_BLOCKED_CATEGORIES)

    def is_manual_effective(self, now_utc: datetime) -> bool:
        if not self.active:
            return False
        if self.mode == "until" and self.until_utc:
            until_dt = datetime.fromisoformat(self.until_utc.replace("Z", "+00:00"))
            if until_dt.tzinfo is None:
                until_dt = until_dt.replace(tzinfo=timezone.utc)
            return now_utc < until_dt
        return True

    def is_scheduled_now(self, day_name: str, sec_of_day: int) -> bool:
        for win in self.scheduled_windows.get(day_name, []):
            if win.start_minutes * 60 <= sec_of_day < win.end_minutes * 60:
                return True
        return False

    def to_dict(self) -> dict[str, Any]:
        return {
            "active": self.active,
            "mode": self.mode,
            "until_utc": self.until_utc,
            "reason": self.reason,
            "scheduled_windows": {
                day: [w.to_dict() for w in wins] for day, wins in self.scheduled_windows.items()
            },
            "allowed_apps": list(self.allowed_apps),
            "blocked_categories": list(self.blocked_categories),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> "HomeworkModeState":
        if not data:
            return cls()
        raw_sched = data.get("scheduled_windows")
        return cls(
            active=bool(data.get("active", False)),
            mode=str(data.get("mode", "none")),
            until_utc=data.get("until_utc"),
            reason=str(data.get("reason", "")),
            scheduled_windows=dict(raw_sched) if isinstance(raw_sched, dict) else {},
            allowed_apps=list(data.get("allowed_apps") or []),
            blocked_categories=list(
                data.get("blocked_categories") or DEFAULT_HOMEWORK_BLOCKED_CATEGORIES
            ),
        )


@dataclass
class AppLimitRule:
    """A tracked application or game rule with its own daily/weekend limit and matching hints."""

    pattern: str
    label: str = ""
    daily_limit_sec: int = 3600
    category: str = ""
    weekend_limit_sec: int | None = None
    flatpak_id: str = ""
    steam_app_id: str = ""
    always_allowed: bool = False
    foreground_only: bool = False

    def __post_init__(self) -> None:
        self.pattern = str(self.pattern).strip()
        if not self.pattern:
            raise ValueError("App limit pattern cannot be empty")
        self.label = str(self.label or self.pattern).strip()
        self.daily_limit_sec = int(self.daily_limit_sec)
        if self.daily_limit_sec < 0 or self.daily_limit_sec > SECONDS_PER_DAY:
            raise ValueError(f"App daily_limit_sec out of range (0..86400): {self.daily_limit_sec}")
        self.category = str(self.category or "").strip()
        if self.weekend_limit_sec is not None:
            wlim = int(self.weekend_limit_sec)
            if wlim < 0 or wlim > SECONDS_PER_DAY:
                raise ValueError(f"App weekend_limit_sec out of range (0..86400): {wlim}")
            self.weekend_limit_sec = wlim
        self.flatpak_id = str(self.flatpak_id or "").strip()
        self.steam_app_id = str(self.steam_app_id or "").strip()
        self.always_allowed = bool(self.always_allowed)
        self.foreground_only = bool(self.foreground_only)

    def limit_for_day(self, day_name: str, is_weekend_override: bool = False) -> int:
        if (
            day_name in ("sat", "sun") or is_weekend_override
        ) and self.weekend_limit_sec is not None:
            return self.weekend_limit_sec
        return self.daily_limit_sec

    def to_dict(self) -> dict[str, Any]:
        return {
            "pattern": self.pattern,
            "label": self.label,
            "daily_limit_sec": self.daily_limit_sec,
            "category": self.category,
            "weekend_limit_sec": self.weekend_limit_sec,
            "flatpak_id": self.flatpak_id,
            "steam_app_id": self.steam_app_id,
            "always_allowed": self.always_allowed,
            "foreground_only": self.foreground_only,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any], default_limit_sec: int = 3600) -> "AppLimitRule":
        raw_lim = data.get("daily_limit_sec")
        limit_sec = default_limit_sec if raw_lim is None else int(raw_lim)
        raw_wlim = data.get("weekend_limit_sec")
        return cls(
            pattern=str(data["pattern"]),
            label=str(data.get("label", "")),
            daily_limit_sec=limit_sec,
            category=str(data.get("category", "")),
            weekend_limit_sec=None if raw_wlim is None else int(raw_wlim),
            flatpak_id=str(data.get("flatpak_id", "") or ""),
            steam_app_id=str(data.get("steam_app_id", "") or ""),
            always_allowed=bool(data.get("always_allowed", False)),
            foreground_only=bool(data.get("foreground_only", False)),
        )


@dataclass
class CategoryLimitRule:
    """Shared daily/weekend budget spanning all apps in a category (e.g. Gaming)."""

    category: str
    daily_limit_sec: int = 3600
    weekend_limit_sec: int | None = None

    def __post_init__(self) -> None:
        self.category = str(self.category).strip()
        if not self.category:
            raise ValueError("Category limit name cannot be empty")
        self.daily_limit_sec = int(self.daily_limit_sec)
        if self.daily_limit_sec < 0 or self.daily_limit_sec > SECONDS_PER_DAY:
            raise ValueError(
                f"Category daily_limit_sec out of range (0..86400): {self.daily_limit_sec}"
            )
        if self.weekend_limit_sec is not None:
            wlim = int(self.weekend_limit_sec)
            if wlim < 0 or wlim > SECONDS_PER_DAY:
                raise ValueError(f"Category weekend_limit_sec out of range (0..86400): {wlim}")
            self.weekend_limit_sec = wlim

    def limit_for_day(self, day_name: str, is_weekend_override: bool = False) -> int:
        if (
            day_name in ("sat", "sun") or is_weekend_override
        ) and self.weekend_limit_sec is not None:
            return self.weekend_limit_sec
        return self.daily_limit_sec

    def to_dict(self) -> dict[str, Any]:
        return {
            "category": self.category,
            "daily_limit_sec": self.daily_limit_sec,
            "weekend_limit_sec": self.weekend_limit_sec,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "CategoryLimitRule":
        raw_wlim = data.get("weekend_limit_sec")
        return cls(
            category=str(data["category"]),
            daily_limit_sec=int(data.get("daily_limit_sec", 3600)),
            weekend_limit_sec=None if raw_wlim is None else int(raw_wlim),
        )


@dataclass
class AppLimitsPolicy:
    """Per-user App Limits configuration (individual app rules + shared category budgets)."""

    enabled: bool = False
    apps: list[AppLimitRule] = field(default_factory=list)
    category_limits: list[CategoryLimitRule] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.enabled = bool(self.enabled)
        dedup_apps: dict[str, AppLimitRule] = {}
        for a in self.apps or []:
            rule = a if isinstance(a, AppLimitRule) else AppLimitRule.from_dict(a)
            dedup_apps[rule.pattern.lower()] = rule
        self.apps = list(dedup_apps.values())
        dedup_cats: dict[str, CategoryLimitRule] = {}
        for c in self.category_limits or []:
            crule = c if isinstance(c, CategoryLimitRule) else CategoryLimitRule.from_dict(c)
            dedup_cats[crule.category.lower()] = crule
        self.category_limits = list(dedup_cats.values())

    def to_dict(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "apps": [a.to_dict() for a in self.apps],
            "category_limits": [c.to_dict() for c in self.category_limits],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> "AppLimitsPolicy":
        if not data:
            return cls()
        # Preserve legacy v0.7.0 shared daily_limits as fallback for apps lacking daily_limit_sec
        default_limit = 3600
        legacy_limits = data.get("daily_limits")
        if isinstance(legacy_limits, dict):
            vals = [
                int(legacy_limits[day])
                for day in WEEKDAYS
                if day in legacy_limits and legacy_limits[day] is not None
            ]
            positive = [v for v in vals if 0 < v <= SECONDS_PER_DAY]
            if positive:
                default_limit = positive[0]
            elif vals and all(v == 0 for v in vals):
                default_limit = 0
        raw_apps = data.get("apps")
        raw_cats = data.get("category_limits")
        return cls(
            enabled=bool(data.get("enabled", False)),
            apps=[
                a
                if isinstance(a, AppLimitRule)
                else AppLimitRule.from_dict(a, default_limit_sec=default_limit)
                for a in (raw_apps if isinstance(raw_apps, list) else [])
            ],
            category_limits=[
                c if isinstance(c, CategoryLimitRule) else CategoryLimitRule.from_dict(c)
                for c in (raw_cats if isinstance(raw_cats, list) else [])
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
    role: str = "child"  # "child" | "parent"
    enabled: bool = True
    lockout_action: str = "lock"
    daily_limits: dict[str, int] = field(default_factory=default_daily_limits)
    weekly_limit: int | None = None
    monthly_limit: int | None = None
    curfew_windows: dict[str, list[TimeWindow]] = field(default_factory=default_curfew_windows)
    track_inactive: bool = False
    hide_tray_icon: bool = False
    save_grace_enabled: bool = True
    pause: PauseState = field(default_factory=PauseState)
    homework_mode: HomeworkModeState = field(default_factory=HomeworkModeState)
    holidays: list[HolidayOverride] = field(default_factory=list)
    app_limits: AppLimitsPolicy = field(default_factory=AppLimitsPolicy)

    def __post_init__(self) -> None:
        self.username = validate_username(self.username)
        self.schema_version = max(SCHEMA_VERSION, int(self.schema_version))
        self.role = str(self.role or "child").strip().lower()
        if self.role not in USER_ROLES:
            raise ValueError(f"Invalid role {self.role!r}; expected one of {USER_ROLES}")
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
            if self.weekly_limit < 0:
                raise ValueError(f"weekly_limit must be >= 0, got {self.weekly_limit}")
            if self.weekly_limit >= SECONDS_PER_DAY * 7:
                self.weekly_limit = None
        if self.monthly_limit is not None:
            self.monthly_limit = int(self.monthly_limit)
            if self.monthly_limit < 0:
                raise ValueError(f"monthly_limit must be >= 0, got {self.monthly_limit}")
            if self.monthly_limit >= SECONDS_PER_DAY * 31:
                self.monthly_limit = None

        norm_windows: dict[str, list[TimeWindow]] = {}
        for day in WEEKDAYS:
            raw_list = self.curfew_windows.get(day)
            if raw_list is None:
                norm_windows[day] = [TimeWindow("00:00", "24:00")]
            else:
                norm_windows[day] = _parse_sorted_windows(raw_list, f"curfew windows on {day}")
        self.curfew_windows = norm_windows
        self.track_inactive = bool(self.track_inactive)
        self.hide_tray_icon = bool(self.hide_tray_icon)
        self.save_grace_enabled = bool(self.save_grace_enabled)
        if not isinstance(self.pause, PauseState):
            self.pause = PauseState.from_dict(self.pause)
        if not isinstance(self.homework_mode, HomeworkModeState):
            self.homework_mode = HomeworkModeState.from_dict(self.homework_mode)
        self.holidays = [
            h if isinstance(h, HolidayOverride) else HolidayOverride.from_dict(h)
            for h in (self.holidays or [])
        ]
        if not isinstance(self.app_limits, AppLimitsPolicy):
            self.app_limits = AppLimitsPolicy.from_dict(self.app_limits)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION,
            "username": self.username,
            "role": self.role,
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
            "save_grace_enabled": self.save_grace_enabled,
            "pause": self.pause.to_dict(),
            "homework_mode": self.homework_mode.to_dict(),
            "holidays": [h.to_dict() for h in self.holidays],
            "app_limits": self.app_limits.to_dict(),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "UserPolicy":
        # Forward-migrate schema v1 `playtime` key cleanly into `app_limits`
        raw_app_limits = data.get("app_limits")
        if raw_app_limits is None:
            raw_app_limits = data.get("playtime")
        raw_daily = data.get("daily_limits")
        raw_wins = data.get("curfew_windows")
        return cls(
            username=str(data["username"]),
            schema_version=SCHEMA_VERSION,
            role=str(data.get("role") or "child"),
            enabled=bool(True if data.get("enabled") is None else data.get("enabled")),
            lockout_action=str(data.get("lockout_action") or "lock"),
            daily_limits=dict(raw_daily) if isinstance(raw_daily, dict) else default_daily_limits(),
            weekly_limit=data.get("weekly_limit"),
            monthly_limit=data.get("monthly_limit"),
            curfew_windows=dict(raw_wins) if isinstance(raw_wins, dict) else {},
            track_inactive=bool(data.get("track_inactive", False)),
            hide_tray_icon=bool(data.get("hide_tray_icon", False)),
            save_grace_enabled=bool(
                True if data.get("save_grace_enabled") is None else data.get("save_grace_enabled")
            ),
            pause=PauseState.from_dict(data.get("pause")),
            homework_mode=HomeworkModeState.from_dict(data.get("homework_mode")),
            holidays=[
                h if isinstance(h, HolidayOverride) else HolidayOverride.from_dict(h)
                for h in (data.get("holidays") or [])
            ],
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
    spent_balance: int = 0
    spent_week: int = 0
    spent_month: int = 0
    bonus_today: int = 0
    last_bonus_note: str = ""
    app_limits_spent_today: int = 0
    app_spent_today: dict[str, int] = field(default_factory=dict)
    app_bonus_today: dict[str, int] = field(default_factory=dict)
    category_spent_today: dict[str, int] = field(default_factory=dict)
    category_bonus_today: dict[str, int] = field(default_factory=dict)
    today_limit_override: int | None = None
    today_windows_override: list[dict[str, str]] | None = None
    save_grace_used_today: bool = False
    save_grace_until_utc: str | None = None
    last_checked_utc: str = ""

    def __post_init__(self) -> None:
        self.username = validate_username(self.username)
        self.schema_version = max(SCHEMA_VERSION, int(self.schema_version))
        self.spent_today = max(0, int(self.spent_today))
        self.spent_balance = int(self.spent_balance)
        self.spent_week = max(0, int(self.spent_week))
        self.spent_month = max(0, int(self.spent_month))
        self.bonus_today = int(self.bonus_today)
        self.last_bonus_note = str(self.last_bonus_note or "").strip()[:160]
        self.app_limits_spent_today = max(0, int(self.app_limits_spent_today))
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
        self.category_spent_today = {
            str(k).strip(): max(0, int(v))
            for k, v in (self.category_spent_today or {}).items()
            if str(k).strip()
        }
        self.category_bonus_today = {
            str(k).strip(): int(v)
            for k, v in (self.category_bonus_today or {}).items()
            if str(k).strip()
        }
        if self.today_limit_override is not None:
            lim = int(self.today_limit_override)
            if lim < 0 or lim > SECONDS_PER_DAY:
                raise ValueError(f"today_limit_override out of range (0..86400): {lim}")
            self.today_limit_override = lim
        if self.today_windows_override is not None:
            parsed = _parse_sorted_windows(self.today_windows_override, "today_windows_override")
            self.today_windows_override = [w.to_dict() for w in parsed]
        self.save_grace_used_today = bool(self.save_grace_used_today)
        if self.save_grace_until_utc:
            datetime.fromisoformat(str(self.save_grace_until_utc).replace("Z", "+00:00"))
            self.save_grace_until_utc = str(self.save_grace_until_utc)
        else:
            self.save_grace_until_utc = None

    def to_dict(self) -> dict[str, Any]:
        out = asdict(self)
        out["schema_version"] = SCHEMA_VERSION
        return out

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "UserAccountingState":
        al_spent = data.get("app_limits_spent_today")
        if al_spent is None:
            al_spent = data.get("playtime_spent_today", 0)
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
            last_bonus_note=str(data.get("last_bonus_note", "") or ""),
            app_limits_spent_today=int(al_spent),
            app_spent_today=dict(data.get("app_spent_today", {}) or {}),
            app_bonus_today=dict(data.get("app_bonus_today", {}) or {}),
            category_spent_today=dict(data.get("category_spent_today", {}) or {}),
            category_bonus_today=dict(data.get("category_bonus_today", {}) or {}),
            today_limit_override=data.get("today_limit_override"),
            today_windows_override=data.get("today_windows_override"),
            save_grace_used_today=bool(data.get("save_grace_used_today", False)),
            save_grace_until_utc=data.get("save_grace_until_utc"),
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
    app_warning_thresholds_sec: list[int] = field(
        default_factory=lambda: list(DEFAULT_APP_WARNING_THRESHOLDS)
    )
    final_countdown_sec: int = 15
    idle_pause_sec: int = 300
    warning_chime_enabled: bool = True
    countdown_hud_enabled: bool = True
    passive_app_insights_enabled: bool = True
    setup_completed: bool = False
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
        self.app_warning_thresholds_sec = sorted(
            {int(x) for x in self.app_warning_thresholds_sec if int(x) > 0},
            reverse=True,
        )
        self.final_countdown_sec = max(0, min(120, int(self.final_countdown_sec)))
        self.idle_pause_sec = max(0, min(3600, int(self.idle_pause_sec)))
        self.warning_chime_enabled = bool(self.warning_chime_enabled)
        self.countdown_hud_enabled = bool(self.countdown_hud_enabled)
        self.passive_app_insights_enabled = bool(self.passive_app_insights_enabled)
        self.setup_completed = bool(self.setup_completed)
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
            app_warning_thresholds_sec=list(
                data.get("app_warning_thresholds_sec", DEFAULT_APP_WARNING_THRESHOLDS)
            ),
            final_countdown_sec=int(data.get("final_countdown_sec", 15)),
            idle_pause_sec=int(data.get("idle_pause_sec", 300)),
            warning_chime_enabled=bool(data.get("warning_chime_enabled", True)),
            countdown_hud_enabled=bool(data.get("countdown_hud_enabled", True)),
            passive_app_insights_enabled=bool(data.get("passive_app_insights_enabled", True)),
            setup_completed=bool(data.get("setup_completed", False)),
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
    idle: bool = False
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
