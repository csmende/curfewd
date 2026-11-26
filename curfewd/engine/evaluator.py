"""Pure, clock-injectable policy evaluator and time-accounting engine."""

from dataclasses import dataclass, field
from datetime import datetime, time, timedelta, timezone
from typing import Any, Iterable

from curfewd.models import (
    DaemonConfig,
    TimeWindow,
    UserAccountingState,
    UserPolicy,
)
from curfewd.parsing import SECONDS_PER_DAY, WEEKDAYS

# Guard against CMOS clock jumps > 3 years forward resetting counters unexpectedly
_CMOS_JUMP_GUARD_SEC = 86400 * 365 * 3


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
    app_statuses: list[dict[str, Any]] = field(default_factory=list)
    today_windows: list[dict[str, str]] = field(default_factory=list)
    crossed_warnings: list[int] = field(default_factory=list)


def _local_keys(now_local: datetime) -> tuple[str, str, str, str]:
    """Return (weekday_name, date_str, iso_week_key, month_key) for local datetime."""
    day_name = WEEKDAYS[now_local.weekday()]
    date_str = now_local.date().isoformat()
    iso_yr, iso_wk, _ = now_local.isocalendar()
    week_key = f"{iso_yr}-W{iso_wk:02d}"
    month_key = now_local.strftime("%Y-%m")
    return day_name, date_str, week_key, month_key


def effective_daily_limit(policy: UserPolicy, state: UserAccountingState, day_name: str) -> int:
    """Return today's active daily allowance (honoring any Today-only override)."""
    if state.today_limit_override is not None:
        return state.today_limit_override
    return policy.daily_limits.get(day_name, SECONDS_PER_DAY)


def effective_today_windows(policy: UserPolicy, state: UserAccountingState, day_name: str) -> list[TimeWindow]:
    """Return today's active allowed windows (honoring any Today-only override)."""
    if state.today_windows_override is not None:
        return [TimeWindow.from_dict(w) for w in state.today_windows_override]
    return list(policy.curfew_windows.get(day_name, []))


def rollover_state(state: UserAccountingState, now_local: datetime) -> bool:
    """Roll over daily/weekly/monthly counters and clear Today-only overrides on calendar change.

    Returns True if any counter rolled over.
    """
    _, date_str, week_key, month_key = _local_keys(now_local)
    now_utc = now_local.astimezone(timezone.utc)

    # Check CMOS jump guard if we have a previous timestamp
    if state.last_checked_utc:
        try:
            prev_utc = datetime.fromisoformat(state.last_checked_utc.replace("Z", "+00:00"))
            if prev_utc.tzinfo is None:
                prev_utc = prev_utc.replace(tzinfo=timezone.utc)
            if (now_utc - prev_utc).total_seconds() > _CMOS_JUMP_GUARD_SEC:
                state.last_checked_utc = now_utc.isoformat()
                return False
        except ValueError:
            pass

    changed = False
    if state.date and state.date != date_str:
        state.spent_today = 0
        state.spent_balance = 0
        state.bonus_today = 0
        state.app_limits_spent_today = 0
        state.app_limits_balance = 0
        state.app_spent_today = {}
        state.app_bonus_today = {}
        state.today_limit_override = None
        state.today_windows_override = None
        changed = True
    if state.week_key and state.week_key != week_key:
        state.spent_week = 0
        changed = True
    if state.month_key and state.month_key != month_key:
        state.spent_month = 0
        changed = True

    state.date = date_str
    state.week_key = week_key
    state.month_key = month_key
    return changed


def apply_grant(
    policy: UserPolicy,
    state: UserAccountingState,
    delta_seconds: int,
    mode: str,
    now_local: datetime,
) -> int:
    """Adjust today's remaining balance via 'add' (+/-) or 'set' (=exact remaining).

    Returns the effective signed bonus delta applied to today's balance.
    """
    rollover_state(state, now_local)
    day_name = WEEKDAYS[now_local.weekday()]
    daily_limit = effective_daily_limit(policy, state, day_name)

    if mode == "set":
        target_left = max(0, min(SECONDS_PER_DAY, int(delta_seconds)))
        old_left = max(0, daily_limit - state.spent_balance)
        applied = target_left - old_left
        state.spent_balance = daily_limit - target_left
        state.bonus_today += applied
        return applied

    if mode == "add":
        delta = int(delta_seconds)
        # Positive delta grants extra time (reduces spent_balance); negative removes time.
        new_balance = state.spent_balance - delta
        # Clamp so remaining time stays within [0, SECONDS_PER_DAY]
        min_balance = daily_limit - SECONDS_PER_DAY
        max_balance = daily_limit
        clamped = max(min_balance, min(max_balance, new_balance))
        applied = state.spent_balance - clamped
        state.spent_balance = clamped
        state.bonus_today += applied
        return applied

    raise ValueError(f"Unsupported grant mode: {mode!r} (expected 'add' or 'set')")


def apply_today_override(
    policy: UserPolicy,
    state: UserAccountingState,
    now_local: datetime,
    limit_sec: int | None = None,
    windows: list[dict[str, Any]] | None = None,
    reset: bool = False,
) -> dict[str, Any]:
    """Override today's allowance and/or allowed windows without altering the weekly template.

    Automatically resets at local midnight via rollover_state().
    """
    rollover_state(state, now_local)
    if reset:
        state.today_limit_override = None
        state.today_windows_override = None
        state.spent_balance = state.spent_today
        state.bonus_today = 0
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
        # Re-anchor balance to actual time spent today so Remaining == max(0, today_limit - spent_today)
        state.spent_balance = state.spent_today
        state.bonus_today = 0

    if windows is not None:
        parsed = [
            w if isinstance(w, TimeWindow) else TimeWindow.from_dict(w)
            for w in windows
        ]
        parsed.sort(key=lambda w: (w.start_minutes, w.end_minutes))
        for prev, curr in zip(parsed, parsed[1:]):
            if curr.start_minutes < prev.end_minutes:
                raise ValueError(f"Overlapping today windows: {prev.start}-{prev.end} vs {curr.start}-{curr.end}")
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

    delta = int(delta_seconds)
    cur_bonus = int(state.app_bonus_today.get(app.pattern, 0))
    new_bonus = cur_bonus + delta
    # Clamp allotted time to [0, SECONDS_PER_DAY]
    min_bonus = -app.daily_limit_sec
    max_bonus = SECONDS_PER_DAY - app.daily_limit_sec
    clamped = max(min_bonus, min(max_bonus, new_bonus))
    applied = clamped - cur_bonus
    state.app_bonus_today[app.pattern] = clamped
    return applied


def _window_at(windows: list[TimeWindow], sec_of_day: int) -> tuple[TimeWindow | None, int]:
    """Find the active window containing sec_of_day and its index."""
    for idx, win in enumerate(windows):
        if win.start_minutes * 60 <= sec_of_day < win.end_minutes * 60:
            return win, idx
    return None, -1


def _contiguous_window_end(
    policy: UserPolicy,
    state: UserAccountingState,
    now_local: datetime,
    active_idx: int,
) -> tuple[int, datetime | None]:
    """Compute continuous seconds remaining before curfew and the curfew timestamp.

    Bridges contiguous windows within the same day and across midnight up to 7 days.
    Returns (seconds_remaining, next_curfew_dt_or_None_if_24x7).
    """
    day_offset = 0
    base_midnight = datetime.combine(now_local.date(), time(0, 0), tzinfo=now_local.tzinfo)

    day_name = WEEKDAYS[now_local.weekday()]
    windows = effective_today_windows(policy, state, day_name)
    end_sec = windows[active_idx].end_minutes * 60

    # Merge contiguous windows later in the same day
    for nxt in windows[active_idx + 1:]:
        if nxt.start_minutes * 60 == end_sec:
            end_sec = nxt.end_minutes * 60
        else:
            break

    # If contiguous through midnight (24:00), continue checking subsequent days
    while end_sec == SECONDS_PER_DAY and day_offset < 7:
        day_offset += 1
        next_day_name = WEEKDAYS[(now_local.weekday() + day_offset) % 7]
        next_windows = policy.curfew_windows.get(next_day_name, [])
        if not next_windows or next_windows[0].start_minutes != 0:
            # Curfew starts right at midnight of day_offset
            curfew_dt = base_midnight + timedelta(days=day_offset)
            return int((curfew_dt - now_local).total_seconds()), curfew_dt
        end_sec = next_windows[0].end_minutes * 60
        for nxt in next_windows[1:]:
            if nxt.start_minutes * 60 == end_sec:
                end_sec = nxt.end_minutes * 60
            else:
                break

    if day_offset >= 7 and end_sec == SECONDS_PER_DAY:
        # Allowed 24/7 with no curfew boundary
        return SECONDS_PER_DAY * 7, None

    curfew_dt = base_midnight + timedelta(days=day_offset, seconds=end_sec)
    return max(0, int((curfew_dt - now_local).total_seconds())), curfew_dt


def _find_next_allowed_at(
    policy: UserPolicy,
    state: UserAccountingState,
    now_local: datetime,
) -> datetime | None:
    """Find the next future datetime when an allowed window starts (up to 7 days)."""
    base_midnight = datetime.combine(now_local.date(), time(0, 0), tzinfo=now_local.tzinfo)
    sec_of_day = now_local.hour * 3600 + now_local.minute * 60 + now_local.second

    for offset in range(8):
        day_name = WEEKDAYS[(now_local.weekday() + offset) % 7]
        if offset == 0:
            lim = effective_daily_limit(policy, state, day_name)
            wins = effective_today_windows(policy, state, day_name)
        else:
            lim = policy.daily_limits.get(day_name, 0)
            wins = policy.curfew_windows.get(day_name, [])
        if lim <= 0:
            continue
        for win in wins:
            start_sec = win.start_minutes * 60
            if offset == 0 and start_sec <= sec_of_day:
                continue
            return base_midnight + timedelta(days=offset, seconds=start_sec)
    return None


def _is_policy_unrestricted(policy: UserPolicy, state: UserAccountingState, today_day: str) -> bool:
    if not policy.enabled:
        return True
    if policy.weekly_limit is not None or policy.monthly_limit is not None:
        return False
    # Check today's effective override first
    if effective_daily_limit(policy, state, today_day) < SECONDS_PER_DAY:
        return False
    today_wins = effective_today_windows(policy, state, today_day)
    if len(today_wins) != 1 or today_wins[0].start_minutes != 0 or today_wins[0].end_minutes != 1440:
        return False
    for day in WEEKDAYS:
        if policy.daily_limits.get(day, 0) < SECONDS_PER_DAY:
            return False
        wins = policy.curfew_windows.get(day, [])
        if len(wins) != 1 or wins[0].start_minutes != 0 or wins[0].end_minutes != 1440:
            return False
    return True


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
) -> EvaluationResult:
    """Advance accounting counters by elapsed_sec and evaluate current enforcement state."""
    if now_local.tzinfo is None:
        now_local = now_local.astimezone()
    now_utc = now_local.astimezone(timezone.utc)

    rollover_state(state, now_local)
    day_name = WEEKDAYS[now_local.weekday()]

    # Sleep / suspend jump guard: ignore huge single-tick deltas (> 15 * poll)
    safe_elapsed = max(0, int(elapsed_sec))
    if safe_elapsed >= max(15, config.poll_interval_sec * 15):
        safe_elapsed = 0

    # Check pause state (auto-expire timed pauses cleanly)
    pause_expired = False
    pause_active = False
    if policy.pause.active:
        if policy.pause.is_effective(now_utc):
            pause_active = True
        else:
            policy.pause.active = False
            policy.pause.mode = "none"
            policy.pause.until_utc = None
            policy.pause.reason = ""
            pause_expired = True

    sec_of_day = now_local.hour * 3600 + now_local.minute * 60 + now_local.second
    today_windows = effective_today_windows(policy, state, day_name)
    active_win, active_idx = _window_at(today_windows, sec_of_day)
    daily_limit = effective_daily_limit(policy, state, day_name)

    # Normalize running per-app patterns
    if running_app_patterns is not None:
        running_set = {str(p).strip() for p in running_app_patterns if str(p).strip()}
    elif app_limits_active:
        running_set = {a.pattern for a in policy.app_limits.apps}
    else:
        running_set = set()
    any_app_running = bool(running_set)

    # Determine whether this tick counts as active usage
    effective_active = bool(is_active) and (policy.track_inactive or not screen_locked)
    if config.app_limits_enabled and policy.app_limits.enabled and policy.app_limits.override_main:
        effective_active = effective_active and any_app_running

    # Compute previous remaining time (to detect threshold crossings)
    prev_daily_left = max(0, daily_limit - state.spent_balance)

    active_delta = 0
    inactive_delta = 0
    app_limits_delta = 0

    if safe_elapsed > 0:
        if effective_active:
            active_delta = safe_elapsed
            if policy.enabled and not pause_active:
                state.spent_today += safe_elapsed
                state.spent_balance += safe_elapsed
                state.spent_week += safe_elapsed
                state.spent_month += safe_elapsed
            if (
                config.app_limits_enabled
                and policy.app_limits.enabled
                and any_app_running
                and not pause_active
            ):
                app_limits_delta = safe_elapsed
                state.app_limits_spent_today += safe_elapsed
                if not policy.app_limits.override_main:
                    state.app_limits_balance += safe_elapsed
                for app in policy.app_limits.apps:
                    if app.pattern in running_set:
                        state.app_spent_today[app.pattern] = (
                            state.app_spent_today.get(app.pattern, 0) + safe_elapsed
                        )
        else:
            inactive_delta = safe_elapsed

    state.last_checked_utc = now_utc.isoformat()

    # Individual per-app remaining calculation & isolated kill detection
    app_statuses: list[dict[str, Any]] = []
    expired_app_patterns: list[str] = []
    for app in policy.app_limits.apps:
        bonus = int(state.app_bonus_today.get(app.pattern, 0))
        allotted = max(0, app.daily_limit_sec + bonus)
        spent = int(state.app_spent_today.get(app.pattern, 0))
        rem = max(0, allotted - spent)
        is_running = app.pattern in running_set
        app_statuses.append(
            {
                "pattern": app.pattern,
                "label": app.label,
                "category": app.category,
                "daily_limit_sec": app.daily_limit_sec,
                "bonus_today": bonus,
                "allotted_today": allotted,
                "spent_today": spent,
                "time_left_today": rem,
                "running": is_running,
            }
        )
        if (
            config.app_limits_enabled
            and policy.enabled
            and not pause_active
            and policy.app_limits.enabled
            and not policy.app_limits.override_main
            and is_running
            and rem <= 0
        ):
            expired_app_patterns.append(app.pattern)

    al_left = min((a["time_left_today"] for a in app_statuses), default=0) if policy.app_limits.enabled else 0
    al_should_kill = bool(expired_app_patterns)

    hide_tray_effective = bool(
        policy.hide_tray_icon or (pause_active and policy.pause.hide_tray_while_paused)
    )

    # Compute time left today (daily, weekly, monthly caps)
    daily_left = max(0, daily_limit - state.spent_balance)
    caps = [daily_left]
    if policy.weekly_limit is not None:
        caps.append(max(0, policy.weekly_limit - state.spent_week))
    if policy.monthly_limit is not None:
        caps.append(max(0, policy.monthly_limit - state.spent_month))
    time_left_today = min(caps)

    # Window bounds
    if active_win is not None:
        window_left, next_curfew_dt = _contiguous_window_end(policy, state, now_local, active_idx)
        next_curfew_iso = next_curfew_dt.isoformat() if next_curfew_dt else None
        next_allowed_iso = None
    else:
        window_left = 0
        next_curfew_iso = None
        next_allowed_dt = _find_next_allowed_at(policy, state, now_local)
        next_allowed_iso = next_allowed_dt.isoformat() if next_allowed_dt else None

    today_wins_dicts = [w.to_dict() for w in today_windows]

    # Paused or disabled policy -> never enforce
    if pause_active:
        return EvaluationResult(
            enforcement_state="paused",
            time_left_today=time_left_today,
            time_left_continuous=SECONDS_PER_DAY,
            daily_limit_today=daily_limit,
            should_enforce=False,
            pause_active=True,
            pause_expired=False,
            hide_tray_effective=hide_tray_effective,
            next_curfew_at=next_curfew_iso,
            next_allowed_at=next_allowed_iso,
            active_delta=active_delta,
            inactive_delta=inactive_delta,
            app_limits_delta=app_limits_delta,
            app_limits_left_today=al_left,
            app_limits_should_kill=False,
            expired_app_patterns=[],
            app_statuses=app_statuses,
            today_windows=today_wins_dicts,
        )

    if _is_policy_unrestricted(policy, state, day_name):
        return EvaluationResult(
            enforcement_state="unrestricted",
            time_left_today=SECONDS_PER_DAY,
            time_left_continuous=SECONDS_PER_DAY,
            daily_limit_today=daily_limit,
            should_enforce=False,
            pause_active=False,
            pause_expired=pause_expired,
            hide_tray_effective=hide_tray_effective,
            next_curfew_at=None,
            next_allowed_at=None,
            active_delta=active_delta,
            inactive_delta=inactive_delta,
            app_limits_delta=app_limits_delta,
            app_limits_left_today=al_left,
            app_limits_should_kill=al_should_kill,
            expired_app_patterns=expired_app_patterns,
            app_statuses=app_statuses,
            today_windows=today_wins_dicts,
        )

    time_left_continuous = min(time_left_today, window_left)
    should_enforce = time_left_continuous <= config.final_countdown_sec

    # Detect newly crossed warning thresholds
    crossed_warnings: list[int] = []
    if active_delta > 0:
        for thresh in config.warning_thresholds_sec:
            if prev_daily_left > thresh >= time_left_continuous:
                crossed_warnings.append(thresh)

    if time_left_continuous <= 0:
        enf_state = "locked_out"
    elif should_enforce:
        enf_state = "countdown"
    elif config.warning_thresholds_sec and time_left_continuous <= max(
        min(config.warning_thresholds_sec), 600
    ):
        enf_state = "warning"
    else:
        enf_state = "ok"

    return EvaluationResult(
        enforcement_state=enf_state,
        time_left_today=time_left_today,
        time_left_continuous=time_left_continuous,
        daily_limit_today=daily_limit,
        should_enforce=should_enforce,
        pause_active=False,
        pause_expired=pause_expired,
        hide_tray_effective=hide_tray_effective,
        next_curfew_at=next_curfew_iso,
        next_allowed_at=next_allowed_iso,
        active_delta=active_delta,
        inactive_delta=inactive_delta,
        app_limits_delta=app_limits_delta,
        app_limits_left_today=al_left,
        app_limits_should_kill=al_should_kill,
        expired_app_patterns=expired_app_patterns,
        app_statuses=app_statuses,
        today_windows=today_wins_dicts,
        crossed_warnings=crossed_warnings,
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
) -> dict[str, Any]:
    """Assemble the canonical UserStatus JSON object returned by D-Bus and CLI."""
    return {
        "schema_version": policy.schema_version,
        "username": policy.username,
        "uid": uid,
        "full_name": full_name,
        "logged_in": bool(logged_in),
        "active": bool(active),
        "screen_locked": bool(screen_locked),
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
        "today_windows": list(eval_res.today_windows),
        "today_override": {
            "active": state.today_limit_override is not None or state.today_windows_override is not None,
            "limit_sec": state.today_limit_override,
            "windows": state.today_windows_override,
        },
        "next_curfew_at": eval_res.next_curfew_at,
        "next_allowed_at": eval_res.next_allowed_at,
        "sessions": list(sessions or []),
        "pause": policy.pause.to_dict(),
        "app_limits_status": {
            "enabled": policy.app_limits.enabled,
            "time_left_today": eval_res.app_limits_left_today,
            "time_spent_today": state.app_limits_spent_today,
            "active_apps": list(active_limited_apps or []),
            "apps": list(eval_res.app_statuses),
        },
        "policy": policy.to_dict(),
    }
