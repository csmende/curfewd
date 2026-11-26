"""Pure, clock-injectable policy evaluator and time-accounting engine."""

from dataclasses import dataclass, field
from datetime import datetime, time, timedelta, timezone
from typing import Any

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
    unaccounted_now: bool
    should_enforce: bool
    pause_active: bool
    pause_expired: bool
    hide_tray_effective: bool
    next_curfew_at: str | None = None
    next_allowed_at: str | None = None
    active_delta: int = 0
    inactive_delta: int = 0
    playtime_delta: int = 0
    playtime_left_today: int = 0
    playtime_should_kill: bool = False
    crossed_warnings: list[int] = field(default_factory=list)


def _local_keys(now_local: datetime) -> tuple[str, str, str, str]:
    """Return (weekday_name, date_str, iso_week_key, month_key) for local datetime."""
    day_name = WEEKDAYS[now_local.weekday()]
    date_str = now_local.date().isoformat()
    iso_yr, iso_wk, _ = now_local.isocalendar()
    week_key = f"{iso_yr}-W{iso_wk:02d}"
    month_key = now_local.strftime("%Y-%m")
    return day_name, date_str, week_key, month_key


def rollover_state(state: UserAccountingState, now_local: datetime) -> bool:
    """Roll over daily/weekly/monthly counters when calendar boundaries change.

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
        state.playtime_spent_today = 0
        state.playtime_balance = 0
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
    daily_limit = policy.daily_limits.get(day_name, SECONDS_PER_DAY)

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


def _window_at(windows: list[TimeWindow], sec_of_day: int) -> tuple[TimeWindow | None, int]:
    """Find the active window containing sec_of_day and its index."""
    for idx, win in enumerate(windows):
        if win.start_minutes * 60 <= sec_of_day < win.end_minutes * 60:
            return win, idx
    return None, -1


def _contiguous_window_end(
    policy: UserPolicy,
    now_local: datetime,
    active_idx: int,
) -> tuple[int, datetime | None]:
    """Compute continuous seconds remaining before curfew and the curfew timestamp.

    Bridges contiguous windows within the same day and across midnight up to 7 days.
    Returns (seconds_remaining, next_curfew_dt_or_None_if_24x7).
    """
    day_offset = 0
    sec_of_day = now_local.hour * 3600 + now_local.minute * 60 + now_local.second
    base_midnight = datetime.combine(now_local.date(), time(0, 0), tzinfo=now_local.tzinfo)

    day_name = WEEKDAYS[now_local.weekday()]
    windows = policy.curfew_windows.get(day_name, [])
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


def _find_next_allowed_at(policy: UserPolicy, now_local: datetime) -> datetime | None:
    """Find the next future datetime when an allowed window starts (up to 7 days)."""
    base_midnight = datetime.combine(now_local.date(), time(0, 0), tzinfo=now_local.tzinfo)
    sec_of_day = now_local.hour * 3600 + now_local.minute * 60 + now_local.second

    for offset in range(8):
        day_name = WEEKDAYS[(now_local.weekday() + offset) % 7]
        if policy.daily_limits.get(day_name, 0) <= 0:
            continue
        for win in policy.curfew_windows.get(day_name, []):
            start_sec = win.start_minutes * 60
            if offset == 0 and start_sec <= sec_of_day:
                continue
            return base_midnight + timedelta(days=offset, seconds=start_sec)
    return None


def _is_policy_unrestricted(policy: UserPolicy) -> bool:
    if not policy.enabled:
        return True
    if policy.weekly_limit is not None or policy.monthly_limit is not None:
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
    playtime_active: bool = False,
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
    today_windows = policy.curfew_windows.get(day_name, [])
    active_win, active_idx = _window_at(today_windows, sec_of_day)
    unaccounted_now = bool(active_win and active_win.unaccounted)

    daily_limit = policy.daily_limits.get(day_name, SECONDS_PER_DAY)

    # Determine whether this tick counts as active usage
    effective_active = bool(is_active) and (policy.track_inactive or not screen_locked)
    if config.playtime_enabled and policy.playtime.enabled and policy.playtime.override_main:
        effective_active = effective_active and bool(playtime_active)

    # Compute previous remaining time (to detect threshold crossings)
    prev_daily_left = max(0, daily_limit - state.spent_balance)

    active_delta = 0
    inactive_delta = 0
    playtime_delta = 0

    if safe_elapsed > 0:
        if effective_active:
            active_delta = safe_elapsed
            # Only charge limits when enforcement is active, not paused, and not unaccounted
            if policy.enabled and not pause_active and not unaccounted_now:
                state.spent_today += safe_elapsed
                state.spent_balance += safe_elapsed
                state.spent_week += safe_elapsed
                state.spent_month += safe_elapsed
            if (
                config.playtime_enabled
                and policy.playtime.enabled
                and playtime_active
                and not pause_active
            ):
                playtime_delta = safe_elapsed
                state.playtime_spent_today += safe_elapsed
                if not policy.playtime.override_main:
                    state.playtime_balance += safe_elapsed
        else:
            inactive_delta = safe_elapsed

    state.last_checked_utc = now_utc.isoformat()

    # PlayTime remaining calculation
    pt_limit_today = policy.playtime.daily_limits.get(day_name, 0)
    pt_left = max(0, pt_limit_today - state.playtime_balance) if policy.playtime.enabled else 0
    pt_should_kill = False
    if (
        config.playtime_enabled
        and policy.enabled
        and not pause_active
        and policy.playtime.enabled
        and playtime_active
    ):
        if (not policy.playtime.override_main and pt_left <= 0) or (
            unaccounted_now and not policy.playtime.allow_unaccounted
        ):
            pt_should_kill = True

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
        window_left, next_curfew_dt = _contiguous_window_end(policy, now_local, active_idx)
        next_curfew_iso = next_curfew_dt.isoformat() if next_curfew_dt else None
        next_allowed_iso = None
    else:
        window_left = 0
        next_curfew_iso = None
        next_allowed_dt = _find_next_allowed_at(policy, now_local)
        next_allowed_iso = next_allowed_dt.isoformat() if next_allowed_dt else None

    # Paused or disabled policy -> never enforce
    if pause_active:
        return EvaluationResult(
            enforcement_state="paused",
            time_left_today=time_left_today,
            time_left_continuous=SECONDS_PER_DAY,
            daily_limit_today=daily_limit,
            unaccounted_now=unaccounted_now,
            should_enforce=False,
            pause_active=True,
            pause_expired=False,
            hide_tray_effective=hide_tray_effective,
            next_curfew_at=next_curfew_iso,
            next_allowed_at=next_allowed_iso,
            active_delta=active_delta,
            inactive_delta=inactive_delta,
            playtime_delta=playtime_delta,
            playtime_left_today=pt_left,
            playtime_should_kill=False,
        )

    if _is_policy_unrestricted(policy):
        return EvaluationResult(
            enforcement_state="unrestricted",
            time_left_today=SECONDS_PER_DAY,
            time_left_continuous=SECONDS_PER_DAY,
            daily_limit_today=daily_limit,
            unaccounted_now=unaccounted_now,
            should_enforce=False,
            pause_active=False,
            pause_expired=pause_expired,
            hide_tray_effective=hide_tray_effective,
            next_curfew_at=None,
            next_allowed_at=None,
            active_delta=active_delta,
            inactive_delta=inactive_delta,
            playtime_delta=playtime_delta,
            playtime_left_today=pt_left,
            playtime_should_kill=pt_should_kill,
        )

    time_left_continuous = window_left if unaccounted_now else min(time_left_today, window_left)
    should_enforce = (not unaccounted_now) and (time_left_continuous <= config.final_countdown_sec)

    # Detect newly crossed warning thresholds
    crossed_warnings: list[int] = []
    if active_delta > 0 and not unaccounted_now:
        for thresh in config.warning_thresholds_sec:
            if prev_daily_left > thresh >= time_left_continuous:
                crossed_warnings.append(thresh)

    if not unaccounted_now and time_left_continuous <= 0:
        enf_state = "locked_out"
    elif should_enforce:
        enf_state = "countdown"
    elif not unaccounted_now and config.warning_thresholds_sec and time_left_continuous <= max(
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
        unaccounted_now=unaccounted_now,
        should_enforce=should_enforce,
        pause_active=False,
        pause_expired=pause_expired,
        hide_tray_effective=hide_tray_effective,
        next_curfew_at=next_curfew_iso,
        next_allowed_at=next_allowed_iso,
        active_delta=active_delta,
        inactive_delta=inactive_delta,
        playtime_delta=playtime_delta,
        playtime_left_today=pt_left,
        playtime_should_kill=pt_should_kill,
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
    active_playtime_apps: list[str] | None = None,
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
        "unaccounted_now": eval_res.unaccounted_now,
        "hide_tray_effective": eval_res.hide_tray_effective,
        "time_left_today": eval_res.time_left_today,
        "time_left_continuous": eval_res.time_left_continuous,
        "daily_limit_today": eval_res.daily_limit_today,
        "time_spent_today": state.spent_today,
        "time_spent_balance": state.spent_balance,
        "time_spent_week": state.spent_week,
        "time_spent_month": state.spent_month,
        "bonus_today": state.bonus_today,
        "next_curfew_at": eval_res.next_curfew_at,
        "next_allowed_at": eval_res.next_allowed_at,
        "sessions": list(sessions or []),
        "pause": policy.pause.to_dict(),
        "playtime_status": {
            "enabled": policy.playtime.enabled,
            "time_left_today": eval_res.playtime_left_today,
            "time_spent_today": state.playtime_spent_today,
            "active_apps": list(active_playtime_apps or []),
        },
        "policy": policy.to_dict(),
    }
