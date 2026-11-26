"""Pure, clock-injectable policy evaluator and time-accounting engine (Schema v3 / Spec 006)."""

from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime, time, timedelta, timezone
from itertools import pairwise
from typing import Any

from curfewd.models import (
    DaemonConfig,
    HolidayOverride,
    TimeWindow,
    UserAccountingState,
    UserPolicy,
)
from curfewd.parsing import SECONDS_PER_DAY, WEEKDAYS

# Guard against CMOS clock jumps > 3 years forward resetting counters unexpectedly
_CMOS_JUMP_GUARD_SEC = 86400 * 365 * 3
SAVE_GRACE_DURATION_SEC = 120


@dataclass
class EvaluationResult:
    """Deterministic output of evaluating one user's policy at a point in time."""

    enforcement_state: str
    time_left_today: int
    time_left_continuous: int
    daily_limit_today: int
    should_enforce: bool
    pause_active: bool
    pause_expired: bool
    hide_tray_effective: bool
    next_curfew_at: str | None = None
    next_allowed_at: str | None = None
    active_delta: int = 0
    inactive_delta: int = 0
    app_limits_delta: int = 0
    app_limits_left_today: int = 0
    app_limits_should_kill: bool = False
    expired_app_patterns: list[str] = field(default_factory=list)
    expired_categories: list[str] = field(default_factory=list)
    homework_blocked_categories: list[str] = field(default_factory=list)
    app_statuses: list[dict[str, Any]] = field(default_factory=list)
    category_statuses: list[dict[str, Any]] = field(default_factory=list)
    today_windows: list[dict[str, str]] = field(default_factory=list)
    crossed_warnings: list[int] = field(default_factory=list)
    crossed_app_warnings: list[dict[str, Any]] = field(default_factory=list)
    homework_active: bool = False
    homework_expired: bool = False
    homework_source: str = ""  # "" | "manual" | "scheduled"
    active_holiday: dict[str, Any] | None = None
    save_grace_active: bool = False
    save_grace_remaining_sec: int = 0
    save_grace_available: bool = False
    idle_paused: bool = False


def _local_keys(now_local: datetime) -> tuple[str, str, str, str]:
    """Return (weekday_name, date_str, iso_week_key, month_key) for local datetime."""
    day_name = WEEKDAYS[now_local.weekday()]
    date_str = now_local.date().isoformat()
    iso_yr, iso_wk, _ = now_local.isocalendar()
    week_key = f"{iso_yr}-W{iso_wk:02d}"
    month_key = now_local.strftime("%Y-%m")
    return day_name, date_str, week_key, month_key


def active_holiday_for(policy: UserPolicy, now_local: datetime) -> HolidayOverride | None:
    """Return the first HolidayOverride covering now_local's local calendar date, if any."""
    date_str = now_local.date().isoformat()
    for hol in policy.holidays:
        if hol.covers(date_str):
            return hol
    return None


def effective_daily_limit(
    policy: UserPolicy,
    state: UserAccountingState,
    day_name: str,
    holiday: HolidayOverride | None = None,
) -> int:
    """Return today's active daily allowance (honoring Today-only override, then active holiday)."""
    if state.today_limit_override is not None:
        return state.today_limit_override
    if holiday is not None:
        if holiday.mode == "pause":
            return SECONDS_PER_DAY
        if holiday.mode == "weekend":
            return policy.daily_limits.get("sat", SECONDS_PER_DAY)
        if holiday.mode == "custom" and holiday.daily_limit_sec is not None:
            return holiday.daily_limit_sec
    return policy.daily_limits.get(day_name, SECONDS_PER_DAY)


def effective_today_windows(
    policy: UserPolicy,
    state: UserAccountingState,
    day_name: str,
    holiday: HolidayOverride | None = None,
) -> list[TimeWindow]:
    """Return today's active allowed windows (honoring Today-only override, then active holiday)."""
    if state.today_windows_override is not None:
        return [TimeWindow.from_dict(w) for w in state.today_windows_override]
    if holiday is not None:
        if holiday.mode == "pause":
            return [TimeWindow("00:00", "24:00")]
        if holiday.mode == "weekend":
            return list(policy.curfew_windows.get("sat", []))
        if holiday.mode == "custom" and holiday.windows is not None:
            return list(holiday.windows)
    return list(policy.curfew_windows.get(day_name, []))


def _is_cmos_forward_jump(
    state: UserAccountingState, now_local: datetime, now_utc: datetime
) -> bool:
    """Return True if system clock appears jumped >3 years forward (e.g. dead CMOS battery)."""
    if state.last_checked_utc:
        try:
            prev_utc = datetime.fromisoformat(state.last_checked_utc.replace("Z", "+00:00"))
            if prev_utc.tzinfo is None:
                prev_utc = prev_utc.replace(tzinfo=timezone.utc)
            if (now_utc - prev_utc).total_seconds() > _CMOS_JUMP_GUARD_SEC:
                return True
        except ValueError:
            pass
    if state.date:
        try:
            prev_date = datetime.strptime(state.date, "%Y-%m-%d").date()
            if (now_local.date() - prev_date).days > 365 * 3:
                return True
        except ValueError:
            pass
    return False


def rollover_state(state: UserAccountingState, now_local: datetime) -> bool:
    """Roll over daily/weekly/monthly counters on forward calendar change only.

    Guards against both CMOS forward jumps (> 3 years) and backward clock jumps
    (e.g. dual-boot UTC drift or BIOS clock rollback) wiping spent_today to 0.
    """
    _, date_str, week_key, month_key = _local_keys(now_local)
    now_utc = now_local.astimezone(timezone.utc)

    if _is_cmos_forward_jump(state, now_local, now_utc):
        # Never overwrite state.last_checked_utc during a CMOS jump or tick #2 would roll state.date forward.
        return False

    changed = False
    if state.date and date_str > state.date:
        active_grace, _ = _save_grace_status(state, now_utc)
        state.spent_today = 0
        state.spent_balance = 0
        state.bonus_today = 0
        state.last_bonus_note = ""
        state.app_limits_spent_today = 0
        state.app_spent_today = {}
        state.app_bonus_today = {}
        state.category_spent_today = {}
        state.category_bonus_today = {}
        state.today_limit_override = None
        state.today_windows_override = None
        state.save_grace_used_today = False
        if not active_grace:
            state.save_grace_until_utc = None
        state.date = date_str
        changed = True
    elif not state.date:
        state.date = date_str

    if state.week_key and week_key > state.week_key:
        state.spent_week = 0
        state.week_key = week_key
        changed = True
    elif not state.week_key:
        state.week_key = week_key

    if state.month_key and month_key > state.month_key:
        state.spent_month = 0
        state.month_key = month_key
        changed = True
    elif not state.month_key:
        state.month_key = month_key

    return changed


def apply_grant(
    policy: UserPolicy,
    state: UserAccountingState,
    delta_seconds: int,
    mode: str,
    now_local: datetime,
    note: str = "",
) -> int:
    """Adjust today's remaining balance via 'add' (+/-) or 'set' (=exact remaining)."""
    rollover_state(state, now_local)
    day_name = WEEKDAYS[now_local.weekday()]
    holiday = active_holiday_for(policy, now_local)
    daily_limit = effective_daily_limit(policy, state, day_name, holiday=holiday)

    if mode == "set":
        target_left = max(0, min(SECONDS_PER_DAY, int(delta_seconds)))
        old_left = max(0, daily_limit - state.spent_balance)
        applied = target_left - old_left
        state.spent_balance = daily_limit - target_left
        state.bonus_today += applied
        if note.strip():
            state.last_bonus_note = note.strip()[:160]
        return applied

    if mode == "add":
        delta = int(delta_seconds)
        new_balance = state.spent_balance - delta
        min_balance = daily_limit - SECONDS_PER_DAY
        max_balance = daily_limit
        clamped = max(min_balance, min(max_balance, new_balance))
        applied = state.spent_balance - clamped
        state.spent_balance = clamped
        state.bonus_today += applied
        if applied > 0 and note.strip():
            state.last_bonus_note = note.strip()[:160]
        return applied

    raise ValueError(f"Unsupported grant mode: {mode!r} (expected 'add' or 'set')")


def apply_today_override(
    state: UserAccountingState,
    now_local: datetime,
    limit_sec: int | None = None,
    windows: list[dict[str, Any]] | None = None,
    reset: bool = False,
) -> dict[str, Any]:
    """Override today's allowance and/or allowed windows without altering the weekly template."""
    rollover_state(state, now_local)
    if reset:
        state.today_limit_override = None
        state.today_windows_override = None
        state.spent_balance = state.spent_today
        state.bonus_today = 0
        state.last_bonus_note = ""
        return {
            "reset": True,
            "limit_sec": None,
            "windows": None,
        }

    if limit_sec is not None:
        lim = int(limit_sec)
        if lim < 0 or lim > SECONDS_PER_DAY:
            raise ValueError(f"today_limit_override out of range (0..86400): {lim}")
        state.today_limit_override = lim
        state.spent_balance = state.spent_today
        state.bonus_today = 0
        state.last_bonus_note = ""

    if windows is not None:
        parsed = [w if isinstance(w, TimeWindow) else TimeWindow.from_dict(w) for w in windows]
        parsed.sort(key=lambda w: (w.start_minutes, w.end_minutes))
        for prev, curr in pairwise(parsed):
            if curr.start_minutes < prev.end_minutes:
                raise ValueError(
                    f"Overlapping today windows: {prev.start}-{prev.end} vs {curr.start}-{curr.end}"
                )
        state.today_windows_override = [w.to_dict() for w in parsed]

    return {
        "reset": False,
        "limit_sec": state.today_limit_override,
        "windows": state.today_windows_override,
    }


def apply_app_grant(
    policy: UserPolicy,
    state: UserAccountingState,
    pattern: str,
    delta_seconds: int,
    now_local: datetime,
) -> int:
    """Grant (or subtract) bonus time today for a single tracked application pattern."""
    rollover_state(state, now_local)
    target = str(pattern).strip()
    app = next((a for a in policy.app_limits.apps if a.pattern == target), None)
    if app is None:
        raise ValueError(f"Tracked app pattern not found: {target!r}")

    day_name = WEEKDAYS[now_local.weekday()]
    hol = active_holiday_for(policy, now_local)
    base_limit = app.limit_for_day(
        day_name, is_weekend_override=(hol is not None and hol.mode == "weekend")
    )
    delta = int(delta_seconds)
    cur_bonus = int(state.app_bonus_today.get(app.pattern, 0))
    new_bonus = cur_bonus + delta
    min_bonus = -base_limit
    max_bonus = SECONDS_PER_DAY - base_limit
    clamped = max(min_bonus, min(max_bonus, new_bonus))
    applied = clamped - cur_bonus
    state.app_bonus_today[app.pattern] = clamped
    return applied


def apply_category_grant(
    policy: UserPolicy,
    state: UserAccountingState,
    category: str,
    delta_seconds: int,
    now_local: datetime,
) -> int:
    """Grant (or subtract) bonus time today for a shared category budget."""
    rollover_state(state, now_local)
    target = str(category).strip()
    cat_rule = next(
        (c for c in policy.app_limits.category_limits if c.category.lower() == target.lower()),
        None,
    )
    if cat_rule is None:
        raise ValueError(f"Category budget not found: {target!r}")

    day_name = WEEKDAYS[now_local.weekday()]
    hol = active_holiday_for(policy, now_local)
    base_limit = cat_rule.limit_for_day(
        day_name, is_weekend_override=(hol is not None and hol.mode == "weekend")
    )
    delta = int(delta_seconds)
    cur_bonus = int(state.category_bonus_today.get(cat_rule.category, 0))
    new_bonus = cur_bonus + delta
    clamped = max(-base_limit, min(SECONDS_PER_DAY - base_limit, new_bonus))
    applied = clamped - cur_bonus
    state.category_bonus_today[cat_rule.category] = clamped
    return applied


def _recently_ended_curfew_window(
    policy: UserPolicy,
    state: UserAccountingState,
    now_local: datetime,
    today_windows: list[TimeWindow],
    sec_of_day: int,
    max_past_sec: int = 120,
) -> bool:
    """Return True if a curfew window ended within the last max_past_sec seconds."""
    for win in today_windows:
        end_sec = win.end_minutes * 60
        if 0 <= sec_of_day - end_sec <= max_past_sec:
            return True
    if sec_of_day <= max_past_sec:
        prev_dt = now_local - timedelta(days=1)
        _, prev_wins = _resolve_future_day_schedule(policy, prev_dt)
        if any(w.end_minutes == 1440 for w in prev_wins):
            return True
    return False


def apply_save_grace(
    policy: UserPolicy,
    state: UserAccountingState,
    now_local: datetime,
    duration_sec: int = SAVE_GRACE_DURATION_SEC,
) -> dict[str, Any]:
    """Activate one-time-per-day 2-minute Save Your Work grace window near expiration."""
    if now_local.tzinfo is None:
        now_local = now_local.astimezone()
    now_utc = now_local.astimezone(timezone.utc)
    rollover_state(state, now_local)
    if not policy.save_grace_enabled:
        raise ValueError("Save grace is disabled for this account")
    if not policy.enabled or policy.role == "parent":
        raise ValueError("Save grace is not applicable to unrestricted/parent accounts")
    if state.save_grace_used_today:
        raise ValueError("Save grace has already been used today")

    day_name = WEEKDAYS[now_local.weekday()]
    holiday = active_holiday_for(policy, now_local)
    pause_active, _ = _evaluate_pause(policy, state, now_utc, holiday)
    if pause_active or _is_policy_unrestricted(policy, state, day_name, holiday=holiday):
        raise ValueError("Save grace is not needed while enforcement is paused or unrestricted")

    sec_of_day = now_local.hour * 3600 + now_local.minute * 60 + now_local.second
    homework_active, _, _ = _evaluate_homework(policy, now_local, now_utc, day_name, sec_of_day)
    today_windows = effective_today_windows(policy, state, day_name, holiday=holiday)
    daily_limit = effective_daily_limit(policy, state, day_name, holiday=holiday)
    time_left_today = _compute_time_left_today(policy, state, daily_limit)
    active_win, window_left, _, _ = _compute_window_bounds(
        policy, state, now_local, today_windows, sec_of_day, holiday
    )
    raw_continuous = window_left if homework_active else min(time_left_today, window_left)
    if raw_continuous > 60:
        raise ValueError("Save grace is only available in the final minute before lockout")
    if active_win is None and not _recently_ended_curfew_window(
        policy, state, now_local, today_windows, sec_of_day, max_past_sec=120
    ):
        raise ValueError("Save grace cannot be claimed after curfew has already started")

    sec = max(30, min(SAVE_GRACE_DURATION_SEC, int(duration_sec)))
    until = now_utc + timedelta(seconds=sec)
    state.save_grace_used_today = True
    state.save_grace_until_utc = until.isoformat()
    return {"granted_sec": sec, "until_utc": state.save_grace_until_utc}


def _save_grace_status(state: UserAccountingState, now_utc: datetime) -> tuple[bool, int]:
    if not state.save_grace_until_utc:
        return False, 0
    try:
        until = datetime.fromisoformat(state.save_grace_until_utc.replace("Z", "+00:00"))
        if until.tzinfo is None:
            until = until.replace(tzinfo=timezone.utc)
    except ValueError:
        state.save_grace_until_utc = None
        return False, 0
    rem = int((until - now_utc).total_seconds())
    if rem <= 0:
        state.save_grace_until_utc = None
        return False, 0
    return True, rem


def _window_at(windows: list[TimeWindow], sec_of_day: int) -> tuple[TimeWindow | None, int]:
    """Find the active window containing sec_of_day and its index."""
    for idx, win in enumerate(windows):
        if win.start_minutes * 60 <= sec_of_day < win.end_minutes * 60:
            return win, idx
    return None, -1


def _resolve_future_day_schedule(
    policy: UserPolicy, target_dt: datetime
) -> tuple[int, list[TimeWindow]]:
    """Resolve (daily_limit, windows) for a non-today date honoring all holiday modes."""
    day_name = WEEKDAYS[target_dt.weekday()]
    hol = active_holiday_for(policy, target_dt)
    if hol is not None:
        if hol.mode == "pause":
            return SECONDS_PER_DAY, [TimeWindow("00:00", "24:00")]
        if hol.mode == "weekend":
            return policy.daily_limits.get("sat", 0), list(policy.curfew_windows.get("sat", []))
        if hol.mode == "custom":
            lim = (
                hol.daily_limit_sec
                if hol.daily_limit_sec is not None
                else policy.daily_limits.get(day_name, 0)
            )
            wins = (
                list(hol.windows)
                if hol.windows is not None
                else list(policy.curfew_windows.get(day_name, []))
            )
            return lim, wins
    return policy.daily_limits.get(day_name, 0), list(policy.curfew_windows.get(day_name, []))


def _contiguous_window_end(
    policy: UserPolicy,
    state: UserAccountingState,
    now_local: datetime,
    active_idx: int,
    holiday: HolidayOverride | None = None,
) -> tuple[int, datetime | None]:
    """Compute continuous seconds remaining before curfew and the curfew timestamp."""
    day_offset = 0
    base_midnight = datetime.combine(now_local.date(), time(0, 0), tzinfo=now_local.tzinfo)

    day_name = WEEKDAYS[now_local.weekday()]
    windows = effective_today_windows(policy, state, day_name, holiday=holiday)
    end_sec = windows[active_idx].end_minutes * 60

    for nxt in windows[active_idx + 1 :]:
        if nxt.start_minutes * 60 == end_sec:
            end_sec = nxt.end_minutes * 60
        else:
            break

    while end_sec == SECONDS_PER_DAY and day_offset < 7:
        day_offset += 1
        future_dt = now_local + timedelta(days=day_offset)
        _, next_windows = _resolve_future_day_schedule(policy, future_dt)
        if not next_windows or next_windows[0].start_minutes != 0:
            curfew_dt = base_midnight + timedelta(days=day_offset)
            return int((curfew_dt - now_local).total_seconds()), curfew_dt
        end_sec = next_windows[0].end_minutes * 60
        for nxt in next_windows[1:]:
            if nxt.start_minutes * 60 == end_sec:
                end_sec = nxt.end_minutes * 60
            else:
                break

    if day_offset >= 7 and end_sec == SECONDS_PER_DAY:
        return SECONDS_PER_DAY * 7, None

    curfew_dt = base_midnight + timedelta(days=day_offset, seconds=end_sec)
    return max(0, int((curfew_dt - now_local).total_seconds())), curfew_dt


def _find_next_allowed_at(
    policy: UserPolicy,
    state: UserAccountingState,
    now_local: datetime,
    holiday: HolidayOverride | None = None,
) -> datetime | None:
    """Find the next future datetime when an allowed window starts (up to 7 days)."""
    base_midnight = datetime.combine(now_local.date(), time(0, 0), tzinfo=now_local.tzinfo)
    sec_of_day = now_local.hour * 3600 + now_local.minute * 60 + now_local.second

    for offset in range(8):
        day_name = WEEKDAYS[(now_local.weekday() + offset) % 7]
        if offset == 0:
            lim = effective_daily_limit(policy, state, day_name, holiday=holiday)
            wins = effective_today_windows(policy, state, day_name, holiday=holiday)
        else:
            future_dt = now_local + timedelta(days=offset)
            lim, wins = _resolve_future_day_schedule(policy, future_dt)
        if lim <= 0:
            continue
        for win in wins:
            start_sec = win.start_minutes * 60
            if offset == 0 and start_sec <= sec_of_day:
                continue
            return base_midnight + timedelta(days=offset, seconds=start_sec)
    return None


def _is_policy_unrestricted(
    policy: UserPolicy,
    state: UserAccountingState,
    today_day: str,
    holiday: HolidayOverride | None = None,
) -> bool:
    if not policy.enabled or policy.role == "parent":
        return True
    if policy.weekly_limit is not None or policy.monthly_limit is not None:
        return False
    if effective_daily_limit(policy, state, today_day, holiday=holiday) < SECONDS_PER_DAY:
        return False
    today_wins = effective_today_windows(policy, state, today_day, holiday=holiday)
    if (
        len(today_wins) != 1
        or today_wins[0].start_minutes != 0
        or today_wins[0].end_minutes != 1440
    ):
        return False
    for day in WEEKDAYS:
        if policy.daily_limits.get(day, 0) < SECONDS_PER_DAY:
            return False
        wins = policy.curfew_windows.get(day, [])
        if len(wins) != 1 or wins[0].start_minutes != 0 or wins[0].end_minutes != 1440:
            return False
    return True


def _evaluate_pause(
    policy: UserPolicy,
    state: UserAccountingState,
    now_utc: datetime,
    holiday: HolidayOverride | None,
) -> tuple[bool, bool]:
    """Return (pause_active, pause_expired), auto-clearing expired timed pauses."""
    expired = False
    if policy.pause.active:
        if policy.pause.is_effective(now_utc):
            return True, False
        policy.pause.active = False
        policy.pause.mode = "none"
        policy.pause.until_utc = None
        policy.pause.reason = ""
        expired = True
    has_today_override = (
        state.today_limit_override is not None or state.today_windows_override is not None
    )
    if holiday is not None and holiday.mode == "pause" and not has_today_override:
        return True, expired
    return False, expired


def _evaluate_homework(
    policy: UserPolicy,
    now_local: datetime,
    now_utc: datetime,
    day_name: str,
    sec_of_day: int,
) -> tuple[bool, bool, str]:
    """Return (homework_active, homework_expired, source)."""
    hw = policy.homework_mode
    expired = False
    if hw.active:
        if hw.is_manual_effective(now_utc):
            return True, False, "manual"
        hw.active = False
        hw.mode = "none"
        hw.until_utc = None
        hw.reason = ""
        expired = True
    if hw.is_scheduled_now(day_name, sec_of_day):
        return True, expired, "scheduled"
    return False, expired, ""


def _compute_time_left_today(
    policy: UserPolicy,
    state: UserAccountingState,
    daily_limit: int,
) -> int:
    """Return effective seconds remaining across daily, weekly, and monthly caps."""
    caps = [max(0, daily_limit - state.spent_balance)]
    if policy.weekly_limit is not None:
        caps.append(max(0, policy.weekly_limit - state.spent_week))
    if policy.monthly_limit is not None:
        caps.append(max(0, policy.monthly_limit - state.spent_month))
    return min(caps)


def _normalize_running_apps(
    policy: UserPolicy,
    app_limits_active: bool,
    running_app_patterns: Iterable[str] | None,
) -> set[str]:
    if running_app_patterns is not None:
        return {str(p).strip() for p in running_app_patterns if str(p).strip()}
    if app_limits_active:
        return {a.pattern for a in policy.app_limits.apps}
    return set()


def _normalize_running_categories(
    policy: UserPolicy,
    running_set: set[str],
    running_categories: Iterable[str] | None,
    homework_active: bool = False,
) -> set[str]:
    cats = {str(c).strip() for c in (running_categories or []) if str(c).strip()}
    hw_allowed = {p.lower() for p in policy.homework_mode.allowed_apps}
    for app in policy.app_limits.apps:
        if app.always_allowed or (homework_active and app.pattern.lower() in hw_allowed):
            continue
        if app.pattern in running_set and app.category:
            cats.add(app.category)
    return cats


def _snapshot_app_and_category_rem(
    policy: UserPolicy,
    state: UserAccountingState,
    day_name: str,
    is_weekend_override: bool,
) -> tuple[dict[str, int], dict[str, int]]:
    app_rem: dict[str, int] = {}
    for app in policy.app_limits.apps:
        lim = app.limit_for_day(day_name, is_weekend_override=is_weekend_override)
        allotted = max(0, lim + int(state.app_bonus_today.get(app.pattern, 0)))
        app_rem[app.pattern] = max(0, allotted - int(state.app_spent_today.get(app.pattern, 0)))

    cat_rem: dict[str, int] = {}
    for cat in policy.app_limits.category_limits:
        lim = cat.limit_for_day(day_name, is_weekend_override=is_weekend_override)
        allotted = max(0, lim + int(state.category_bonus_today.get(cat.category, 0)))
        cat_rem[cat.category] = max(
            0, allotted - int(state.category_spent_today.get(cat.category, 0))
        )
    return app_rem, cat_rem


def _advance_accounting(
    policy: UserPolicy,
    state: UserAccountingState,
    config: DaemonConfig,
    safe_elapsed: int,
    effective_active: bool,
    pause_active: bool,
    homework_active: bool,
    running_set: set[str],
    running_cats: set[str],
    daily_limit: int = SECONDS_PER_DAY,
    grace_active: bool = False,
) -> tuple[int, int, int]:
    """Advance daily/weekly/monthly, per-app, and category counters."""
    if safe_elapsed <= 0:
        return 0, 0, 0
    if not effective_active:
        return 0, safe_elapsed, 0

    active_delta = safe_elapsed
    app_limits_delta = 0
    if policy.enabled and policy.role != "parent" and not pause_active and not homework_active:
        state.spent_today += safe_elapsed
        if grace_active and state.spent_balance >= daily_limit:
            # Cap spent_balance at daily_limit during Save Grace so future parent grants aren't shorted.
            state.spent_balance = daily_limit
        else:
            next_bal = state.spent_balance + safe_elapsed
            state.spent_balance = min(daily_limit, next_bal) if grace_active else next_bal
        state.spent_week += safe_elapsed
        state.spent_month += safe_elapsed

    if (
        config.app_limits_enabled
        and policy.enabled
        and policy.app_limits.enabled
        and policy.role != "parent"
        and not pause_active
    ):
        any_charged = False
        hw_allowed = {p.lower() for p in policy.homework_mode.allowed_apps}
        for app in policy.app_limits.apps:
            is_exempt = app.always_allowed or (
                homework_active and app.pattern.lower() in hw_allowed
            )
            if app.pattern in running_set and not is_exempt:
                state.app_spent_today[app.pattern] = (
                    state.app_spent_today.get(app.pattern, 0) + safe_elapsed
                )
                any_charged = True
        running_cats_lower = {c.lower() for c in running_cats}
        for cat in policy.app_limits.category_limits:
            if cat.category.lower() in running_cats_lower:
                state.category_spent_today[cat.category] = (
                    state.category_spent_today.get(cat.category, 0) + safe_elapsed
                )
                any_charged = True
        if any_charged:
            app_limits_delta = safe_elapsed
            state.app_limits_spent_today += safe_elapsed
    return active_delta, 0, app_limits_delta


def _evaluate_app_and_category_limits(
    policy: UserPolicy,
    state: UserAccountingState,
    config: DaemonConfig,
    day_name: str,
    is_weekend_override: bool,
    pause_active: bool,
    homework_active: bool,
    running_set: set[str],
    running_cats: set[str],
    prev_app_rem: dict[str, int],
    prev_cat_rem: dict[str, int],
    active_delta: int,
) -> tuple[
    list[dict[str, Any]],
    list[dict[str, Any]],
    list[str],
    list[str],
    list[str],
    list[dict[str, Any]],
    int,
]:
    """Evaluate per-app limits, category budgets, homework blocks, and advance warnings."""
    app_statuses: list[dict[str, Any]] = []
    cat_statuses: list[dict[str, Any]] = []
    expired_patterns: list[str] = []
    expired_categories: list[str] = []
    crossed_warnings: list[dict[str, Any]] = []

    can_enforce_apps = (
        config.app_limits_enabled
        and policy.enabled
        and policy.role != "parent"
        and not pause_active
        and policy.app_limits.enabled
    )
    hw_blocked_cats = (
        list(policy.homework_mode.blocked_categories)
        if (homework_active and policy.enabled and policy.role != "parent" and not pause_active)
        else []
    )
    hw_blocked_lower = {c.lower() for c in hw_blocked_cats}
    hw_allowed_lower = {p.lower() for p in policy.homework_mode.allowed_apps}
    running_cats_lower = {c.lower() for c in running_cats}

    for app in policy.app_limits.apps:
        day_lim = app.limit_for_day(day_name, is_weekend_override=is_weekend_override)
        bonus = int(state.app_bonus_today.get(app.pattern, 0))
        allotted = max(0, day_lim + bonus)
        spent = int(state.app_spent_today.get(app.pattern, 0))
        rem = max(0, allotted - spent)
        is_running = app.pattern in running_set
        is_exempt = app.always_allowed or (
            homework_active and app.pattern.lower() in hw_allowed_lower
        )
        blocked_by_hw = bool(
            hw_blocked_lower and app.category.lower() in hw_blocked_lower and not is_exempt
        )
        app_statuses.append(
            {
                "pattern": app.pattern,
                "label": app.label,
                "category": app.category,
                "daily_limit_sec": app.daily_limit_sec,
                "weekend_limit_sec": app.weekend_limit_sec,
                "effective_limit_sec": day_lim,
                "bonus_today": bonus,
                "allotted_today": allotted,
                "spent_today": spent,
                "time_left_today": rem,
                "running": is_running,
                "always_allowed": app.always_allowed,
                "foreground_only": app.foreground_only,
                "flatpak_id": app.flatpak_id,
                "steam_app_id": app.steam_app_id,
                "blocked_by_homework": blocked_by_hw,
            }
        )
        if (
            can_enforce_apps
            and is_running
            and active_delta > 0
            and not blocked_by_hw
            and not is_exempt
        ):
            prev_r = prev_app_rem.get(app.pattern, rem)
            for thresh in config.app_warning_thresholds_sec:
                if prev_r > thresh >= rem:
                    crossed_warnings.append(
                        {
                            "kind": "app_warning",
                            "pattern": app.pattern,
                            "label": app.label,
                            "seconds_left": rem,
                            "threshold_sec": thresh,
                        }
                    )
        if is_running and not is_exempt and ((can_enforce_apps and rem <= 0) or blocked_by_hw):
            expired_patterns.append(app.pattern)

    for cat in policy.app_limits.category_limits:
        day_lim = cat.limit_for_day(day_name, is_weekend_override=is_weekend_override)
        bonus = int(state.category_bonus_today.get(cat.category, 0))
        allotted = max(0, day_lim + bonus)
        spent = int(state.category_spent_today.get(cat.category, 0))
        rem = max(0, allotted - spent)
        is_running = cat.category.lower() in running_cats_lower
        cat_statuses.append(
            {
                "category": cat.category,
                "daily_limit_sec": cat.daily_limit_sec,
                "weekend_limit_sec": cat.weekend_limit_sec,
                "effective_limit_sec": day_lim,
                "bonus_today": bonus,
                "allotted_today": allotted,
                "spent_today": spent,
                "time_left_today": rem,
                "running": is_running,
            }
        )
        if can_enforce_apps and is_running and active_delta > 0:
            prev_r = prev_cat_rem.get(cat.category, rem)
            for thresh in config.app_warning_thresholds_sec:
                if prev_r > thresh >= rem:
                    crossed_warnings.append(
                        {
                            "kind": "category_warning",
                            "category": cat.category,
                            "label": cat.category,
                            "seconds_left": rem,
                            "threshold_sec": thresh,
                        }
                    )
        if can_enforce_apps and is_running and rem <= 0:
            expired_categories.append(cat.category)

    rem_pool = [a["time_left_today"] for a in app_statuses if not a["always_allowed"]] + [
        c["time_left_today"] for c in cat_statuses
    ]
    al_left = min(rem_pool, default=0) if policy.app_limits.enabled else 0
    return (
        app_statuses,
        cat_statuses,
        expired_patterns,
        expired_categories,
        hw_blocked_cats,
        crossed_warnings,
        al_left,
    )


def _classify_enforcement_state(
    time_left_continuous: int,
    should_enforce: bool,
    config: DaemonConfig,
    save_grace_active: bool = False,
) -> str:
    """Map continuous remaining time into an enforcement state string."""
    if save_grace_active:
        return "countdown"
    if time_left_continuous <= 0:
        return "locked_out"
    if should_enforce:
        return "countdown"
    if config.warning_thresholds_sec and time_left_continuous <= max(
        min(config.warning_thresholds_sec), 600
    ):
        return "warning"
    return "ok"


def _compute_window_bounds(
    policy: UserPolicy,
    state: UserAccountingState,
    now_local: datetime,
    today_windows: list[TimeWindow],
    sec_of_day: int,
    holiday: HolidayOverride | None,
) -> tuple[TimeWindow | None, int, str | None, str | None]:
    active_win, active_idx = _window_at(today_windows, sec_of_day)
    if active_win is not None:
        window_left, next_curfew_dt = _contiguous_window_end(
            policy, state, now_local, active_idx, holiday=holiday
        )
        return (
            active_win,
            window_left,
            (next_curfew_dt.isoformat() if next_curfew_dt else None),
            None,
        )
    next_allowed_dt = _find_next_allowed_at(policy, state, now_local, holiday=holiday)
    return None, 0, None, (next_allowed_dt.isoformat() if next_allowed_dt else None)


def evaluate_tick(
    policy: UserPolicy,
    state: UserAccountingState,
    config: DaemonConfig,
    now_local: datetime,
    elapsed_sec: int,
    is_active: bool,
    screen_locked: bool,
    app_limits_active: bool = False,
    running_app_patterns: Iterable[str] | None = None,
    running_categories: Iterable[str] | None = None,
    is_idle: bool = False,
) -> EvaluationResult:
    """Advance accounting counters by elapsed_sec and evaluate current enforcement state."""
    if now_local.tzinfo is None:
        now_local = now_local.astimezone()
    now_utc = now_local.astimezone(timezone.utc)

    cmos_jumped = _is_cmos_forward_jump(state, now_local, now_utc)
    rollover_state(state, now_local)
    day_name = WEEKDAYS[now_local.weekday()]
    holiday = active_holiday_for(policy, now_local)
    is_weekend_override = holiday is not None and holiday.mode == "weekend"

    safe_elapsed = max(0, int(elapsed_sec))
    if safe_elapsed >= max(15, config.poll_interval_sec * 15):
        safe_elapsed = 0

    pause_active, pause_expired = _evaluate_pause(policy, state, now_utc, holiday)
    sec_of_day = now_local.hour * 3600 + now_local.minute * 60 + now_local.second
    homework_active, homework_expired, homework_source = _evaluate_homework(
        policy, now_local, now_utc, day_name, sec_of_day
    )

    today_windows = effective_today_windows(policy, state, day_name, holiday=holiday)
    daily_limit = effective_daily_limit(policy, state, day_name, holiday=holiday)
    running_set = _normalize_running_apps(policy, app_limits_active, running_app_patterns)
    running_cats = _normalize_running_categories(
        policy, running_set, running_categories, homework_active=homework_active
    )

    idle_paused = bool(is_idle and config.idle_pause_sec > 0 and not policy.track_inactive)
    effective_active = bool(is_active) and (
        policy.track_inactive or (not screen_locked and not idle_paused)
    )
    grace_active, grace_rem = _save_grace_status(state, now_utc)
    prev_time_left_today = _compute_time_left_today(policy, state, daily_limit)
    prev_app_rem, prev_cat_rem = _snapshot_app_and_category_rem(
        policy, state, day_name, is_weekend_override
    )

    active_delta, inactive_delta, app_limits_delta = _advance_accounting(
        policy,
        state,
        config,
        safe_elapsed,
        effective_active,
        pause_active,
        homework_active,
        running_set,
        running_cats,
        daily_limit=daily_limit,
        grace_active=grace_active,
    )
    if not cmos_jumped:
        state.last_checked_utc = now_utc.isoformat()

    (
        app_statuses,
        cat_statuses,
        expired_app_patterns,
        expired_categories,
        hw_blocked_cats,
        crossed_app_warnings,
        al_left,
    ) = _evaluate_app_and_category_limits(
        policy,
        state,
        config,
        day_name,
        is_weekend_override,
        pause_active,
        homework_active,
        running_set,
        running_cats,
        prev_app_rem,
        prev_cat_rem,
        active_delta,
    )
    running_cats_lower = {c.lower() for c in running_cats}
    hw_cat_running = any(c.lower() in running_cats_lower for c in hw_blocked_cats)
    al_should_kill = bool(expired_app_patterns or expired_categories or hw_cat_running)
    hide_tray_effective = bool(
        policy.hide_tray_icon or (pause_active and policy.pause.hide_tray_while_paused)
    )
    time_left_today = _compute_time_left_today(policy, state, daily_limit)
    active_win, window_left, next_curfew_iso, next_allowed_iso = _compute_window_bounds(
        policy, state, now_local, today_windows, sec_of_day, holiday
    )
    today_wins_dicts = [w.to_dict() for w in today_windows]
    holiday_dict = holiday.to_dict() if holiday is not None else None
    grace_avail = bool(
        policy.save_grace_enabled
        and policy.enabled
        and policy.role != "parent"
        and not state.save_grace_used_today
        and not grace_active
    )

    common: dict[str, Any] = {
        "time_left_today": time_left_today,
        "daily_limit_today": daily_limit,
        "pause_expired": pause_expired,
        "hide_tray_effective": hide_tray_effective,
        "next_curfew_at": next_curfew_iso,
        "next_allowed_at": next_allowed_iso,
        "active_delta": active_delta,
        "inactive_delta": inactive_delta,
        "app_limits_delta": app_limits_delta,
        "app_limits_left_today": al_left,
        "app_statuses": app_statuses,
        "category_statuses": cat_statuses,
        "today_windows": today_wins_dicts,
        "homework_active": homework_active,
        "homework_expired": homework_expired,
        "homework_source": homework_source,
        "active_holiday": holiday_dict,
        "save_grace_active": grace_active,
        "save_grace_remaining_sec": grace_rem,
        "save_grace_available": grace_avail,
        "idle_paused": idle_paused,
    }

    if pause_active:
        return EvaluationResult(
            enforcement_state="paused",
            time_left_continuous=SECONDS_PER_DAY,
            should_enforce=False,
            pause_active=True,
            app_limits_should_kill=False,
            **common,
        )

    if _is_policy_unrestricted(policy, state, day_name, holiday=holiday):
        return EvaluationResult(
            enforcement_state="unrestricted",
            time_left_continuous=SECONDS_PER_DAY,
            should_enforce=False,
            pause_active=False,
            app_limits_should_kill=al_should_kill,
            expired_app_patterns=expired_app_patterns,
            expired_categories=expired_categories,
            homework_blocked_categories=hw_blocked_cats,
            crossed_app_warnings=crossed_app_warnings,
            **{
                **common,
                "time_left_today": SECONDS_PER_DAY,
                "next_curfew_at": None,
                "next_allowed_at": None,
            },
        )

    raw_continuous = window_left if homework_active else min(time_left_today, window_left)
    time_left_continuous = max(raw_continuous, grace_rem) if grace_active else raw_continuous
    prev_window_left = (window_left + safe_elapsed) if active_win is not None else 0
    prev_time_left_continuous = (
        prev_window_left if homework_active else min(prev_time_left_today, prev_window_left)
    )
    should_enforce = (not grace_active) and (time_left_continuous <= config.final_countdown_sec)

    crossed_warnings = (
        [
            thresh
            for thresh in config.warning_thresholds_sec
            if prev_time_left_continuous > thresh >= raw_continuous
        ]
        if active_delta > 0 and not grace_active
        else []
    )
    if homework_active and not should_enforce and not grace_active:
        enf_state = "homework"
    else:
        enf_state = _classify_enforcement_state(
            time_left_continuous, should_enforce, config, save_grace_active=grace_active
        )

    return EvaluationResult(
        enforcement_state=enf_state,
        time_left_continuous=time_left_continuous,
        should_enforce=should_enforce,
        pause_active=False,
        app_limits_should_kill=al_should_kill,
        expired_app_patterns=expired_app_patterns,
        expired_categories=expired_categories,
        homework_blocked_categories=hw_blocked_cats,
        crossed_warnings=crossed_warnings,
        crossed_app_warnings=crossed_app_warnings,
        **common,
    )


def build_user_status_dict(
    policy: UserPolicy,
    state: UserAccountingState,
    eval_res: EvaluationResult,
    uid: int | None = None,
    full_name: str = "",
    logged_in: bool = False,
    active: bool = False,
    screen_locked: bool = False,
    sessions: list[dict[str, Any]] | None = None,
    active_limited_apps: list[str] | None = None,
    pending_request_count: int = 0,
    config: DaemonConfig | None = None,
) -> dict[str, Any]:
    """Assemble the canonical UserStatus JSON object returned by D-Bus and CLI."""
    return {
        "schema_version": policy.schema_version,
        "username": policy.username,
        "uid": uid,
        "full_name": full_name,
        "role": policy.role,
        "logged_in": bool(logged_in),
        "active": bool(active),
        "screen_locked": bool(screen_locked),
        "idle_paused": bool(eval_res.idle_paused),
        "enforcement_state": eval_res.enforcement_state,
        "hide_tray_effective": eval_res.hide_tray_effective,
        "time_left_today": eval_res.time_left_today,
        "time_left_continuous": eval_res.time_left_continuous,
        "daily_limit_today": eval_res.daily_limit_today,
        "time_spent_today": state.spent_today,
        "time_spent_balance": state.spent_balance,
        "time_spent_week": state.spent_week,
        "time_spent_month": state.spent_month,
        "bonus_today": state.bonus_today,
        "last_bonus_note": state.last_bonus_note,
        "today_windows": list(eval_res.today_windows),
        "today_override": {
            "active": state.today_limit_override is not None
            or state.today_windows_override is not None,
            "limit_sec": state.today_limit_override,
            "windows": state.today_windows_override,
        },
        "active_holiday": eval_res.active_holiday,
        "homework_mode": {
            **policy.homework_mode.to_dict(),
            "effective": eval_res.homework_active,
            "source": eval_res.homework_source,
        },
        "save_grace": {
            "enabled": policy.save_grace_enabled,
            "used_today": state.save_grace_used_today,
            "active": eval_res.save_grace_active,
            "remaining_sec": eval_res.save_grace_remaining_sec,
            "available": eval_res.save_grace_available,
        },
        "pending_request_count": int(pending_request_count),
        "next_curfew_at": eval_res.next_curfew_at,
        "next_allowed_at": eval_res.next_allowed_at,
        "sessions": list(sessions or []),
        "pause": policy.pause.to_dict(),
        "client_prefs": {
            "warning_chime_enabled": config.warning_chime_enabled if config else True,
            "countdown_hud_enabled": config.countdown_hud_enabled if config else True,
        },
        "app_limits_status": {
            "enabled": policy.app_limits.enabled,
            "time_left_today": eval_res.app_limits_left_today,
            "time_spent_today": state.app_limits_spent_today,
            "active_apps": list(active_limited_apps or []),
            "apps": list(eval_res.app_statuses),
            "categories": list(eval_res.category_statuses),
        },
        "policy": policy.to_dict(),
    }
