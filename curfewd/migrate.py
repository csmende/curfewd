"""One-way configuration and state migrator from legacy Timekpr-nExT / curfewd 0.6.x."""

import configparser
from datetime import datetime, timezone
import os
import re
from typing import Any

from curfewd.models import (
    DaemonConfig,
    LOCKOUT_ACTIONS,
    PlayTimeApp,
    PlayTimePolicy,
    TimeWindow,
    UserAccountingState,
    UserPolicy,
)
from curfewd.parsing import (
    ISO_NUM_TO_WEEKDAY,
    SECONDS_PER_DAY,
    WEEKDAYS,
    minutes_to_hhmm,
    validate_username,
)
from curfewd.storage import AtomicJsonStore

_HOUR_TOKEN_RE = re.compile(r"^(!)?(\d{1,2})(?:\[(\d{1,2})-(\d{1,2})\])?$")


def _parse_bool(raw: str | None, default: bool = False) -> bool:
    if raw is None:
        return default
    return str(raw).strip().lower() in ("true", "1", "yes", "on")


def _parse_int(raw: str | None, default: int = 0) -> int:
    if raw is None or not str(raw).strip():
        return default
    try:
        return int(float(str(raw).strip()))
    except ValueError:
        return default


def _split_semicolon(raw: str | None) -> list[str]:
    if not raw:
        return []
    return [tok.strip() for tok in str(raw).split(";") if tok.strip()]


def parse_legacy_allowed_hours(raw: str | None) -> list[TimeWindow]:
    """Convert Timekpr-nExT ALLOWED_HOURS string (e.g. '8;9;10;20[0-30];!21') into TimeWindows."""
    tokens = _split_semicolon(raw)
    if not tokens:
        return []

    raw_intervals: list[tuple[int, int, bool]] = []
    for tok in tokens:
        match = _HOUR_TOKEN_RE.match(tok)
        if not match:
            continue
        unaccounted = bool(match.group(1))
        hour = int(match.group(2))
        if not (0 <= hour <= 23):
            continue
        s_min = int(match.group(3)) if match.group(3) is not None else 0
        e_min = int(match.group(4)) if match.group(4) is not None else 60
        s_min = max(0, min(59, s_min))
        e_min = max(1, min(60, e_min))
        if e_min <= s_min:
            continue
        raw_intervals.append((hour * 60 + s_min, hour * 60 + e_min, unaccounted))

    if not raw_intervals:
        return []

    raw_intervals.sort(key=lambda item: (item[0], item[1]))
    merged: list[list[Any]] = []
    for start_m, end_m, uacc in raw_intervals:
        if merged and merged[-1][2] == uacc and start_m <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], end_m)
        else:
            merged.append([start_m, end_m, uacc])

    return [
        TimeWindow(start=minutes_to_hhmm(s), end=minutes_to_hhmm(e), unaccounted=u)
        for s, e, u in merged
    ]


def _read_ini(path: str) -> configparser.ConfigParser | None:
    if not os.path.isfile(path) or os.path.islink(path):
        return None
    cp = configparser.ConfigParser(interpolation=None)
    cp.read(path, encoding="utf-8")
    return cp


def convert_legacy_daemon_config(legacy_conf_path: str) -> DaemonConfig:
    """Convert /etc/timekpr/timekpr.conf to DaemonConfig."""
    cp = _read_ini(legacy_conf_path)
    if cp is None:
        return DaemonConfig()

    def get(section: str, key: str, default: str | None = None) -> str | None:
        return cp.get(section, key, fallback=default)

    log_lvl_num = _parse_int(get("GENERAL", "TIMEKPR_LOGLEVEL", "1"), 1)
    log_level = "DEBUG" if log_lvl_num >= 2 else "INFO"

    warnings = [
        _parse_int(tok, 0)
        for tok in _split_semicolon(get("GENERAL", "TIMEKPR_NOTIFICATION_LEVELS", "3600;1800;600;300;60"))
        if _parse_int(tok, 0) > 0
    ]

    return DaemonConfig(
        poll_interval_sec=_parse_int(get("GENERAL", "TIMEKPR_POLLTIME", "3"), 3),
        save_interval_sec=_parse_int(get("GENERAL", "TIMEKPR_SAVE_TIME", "30"), 30),
        warning_thresholds_sec=warnings or [3600, 1800, 600, 300, 60],
        final_countdown_sec=_parse_int(get("GENERAL", "TIMEKPR_TERMINATION_TIME", "15"), 15),
        session_lock_fallback=_parse_bool(get("MISC", "TIMEKPR_SESSION_LOCK_FALLBACK", "True"), True),
        release_x11_grab_on_lock=_parse_bool(get("MISC", "TIMEKPR_RELEASE_X11_GRAB_ON_LOCK", "True"), True),
        use_locked_hint=_parse_bool(get("MISC", "TIMEKPR_USE_LOCKED_HINT", "True"), True),
        playtime_enabled=_parse_bool(get("PLAYTIME", "TIMEKPR_PLAYTIME_ENABLED", "True"), True),
        tracked_session_types=_split_semicolon(get("SESSIONS", "TIMEKPR_SESSION_TYPES_CTRL", "x11;wayland;mir")) or ["x11", "wayland", "mir"],
        excluded_users=_split_semicolon(get("SESSIONS", "TIMEKPR_USERS_EXCLUDED")) or DaemonConfig().excluded_users,
        log_level=log_level,
    )


def convert_legacy_user_policy(username: str, legacy_user_conf_path: str) -> UserPolicy:
    """Convert /var/lib/timekpr/config/timekpr.<user>.conf to UserPolicy."""
    safe = validate_username(username)
    cp = _read_ini(legacy_user_conf_path)
    if cp is None or not cp.has_section(safe):
        return UserPolicy(username=safe)

    def get(section: str, key: str, default: str | None = None) -> str | None:
        return cp.get(section, key, fallback=default)

    allowed_days = _split_semicolon(get(safe, "ALLOWED_WEEKDAYS", "1;2;3;4;5;6;7"))
    limits_raw = _split_semicolon(get(safe, "LIMITS_PER_WEEKDAYS", ""))
    daily_limits = {d: 0 for d in WEEKDAYS}
    curfew_windows: dict[str, list[TimeWindow]] = {d: [] for d in WEEKDAYS}

    for idx, iso_day in enumerate(allowed_days):
        day_name = ISO_NUM_TO_WEEKDAY.get(iso_day)
        if not day_name:
            continue
        lim = _parse_int(limits_raw[idx], SECONDS_PER_DAY) if idx < len(limits_raw) else SECONDS_PER_DAY
        daily_limits[day_name] = max(0, min(SECONDS_PER_DAY, lim))
        curfew_windows[day_name] = parse_legacy_allowed_hours(get(safe, f"ALLOWED_HOURS_{iso_day}", ""))

    raw_lockout = (get(safe, "LOCKOUT_TYPE", "lock") or "lock").strip().lower()
    if raw_lockout == "suspendwake":
        raw_lockout = "suspend"
    if raw_lockout not in LOCKOUT_ACTIONS:
        raw_lockout = "lock"

    week_lim = _parse_int(get(safe, "LIMIT_PER_WEEK", "604800"), 604800)
    month_lim = _parse_int(get(safe, "LIMIT_PER_MONTH", "2678400"), 2678400)

    # PlayTime section
    pt_section = f"{safe}.PLAYTIME"
    pt_policy = PlayTimePolicy()
    if cp.has_section(pt_section):
        pt_days = _split_semicolon(get(pt_section, "PLAYTIME_ALLOWED_WEEKDAYS", ""))
        pt_lims = _split_semicolon(get(pt_section, "PLAYTIME_LIMITS_PER_WEEKDAYS", ""))
        pt_daily = {d: 0 for d in WEEKDAYS}
        for idx, iso_day in enumerate(pt_days):
            day_name = ISO_NUM_TO_WEEKDAY.get(iso_day)
            if day_name and idx < len(pt_lims):
                pt_daily[day_name] = max(0, min(SECONDS_PER_DAY, _parse_int(pt_lims[idx], 0)))
        apps: list[PlayTimeApp] = []
        for k, v in cp.items(pt_section):
            if k.lower().startswith("playtime_activity_") and v.strip():
                match = re.match(r"^([^\[]+?)(?:\[(.*)\])?$", v.strip())
                if match and match.group(1).strip():
                    pat = match.group(1).strip()
                    lbl = (match.group(2) or pat).strip()
                    apps.append(PlayTimeApp(pattern=pat, label=lbl))
        pt_policy = PlayTimePolicy(
            enabled=_parse_bool(get(pt_section, "PLAYTIME_ENABLED", "False"), False),
            override_main=_parse_bool(get(pt_section, "PLAYTIME_LIMIT_OVERRIDE_ENABLED", "False"), False),
            allow_unaccounted=_parse_bool(get(pt_section, "PLAYTIME_UNACCOUNTED_INTERVALS_ENABLED", "False"), False),
            daily_limits=pt_daily,
            apps=apps,
        )

    return UserPolicy(
        username=safe,
        enabled=True,
        lockout_action=raw_lockout,
        daily_limits=daily_limits,
        weekly_limit=week_lim if 0 < week_lim < SECONDS_PER_DAY * 7 else None,
        monthly_limit=month_lim if 0 < month_lim < SECONDS_PER_DAY * 31 else None,
        curfew_windows=curfew_windows,
        track_inactive=_parse_bool(get(safe, "TRACK_INACTIVE", "False"), False),
        hide_tray_icon=_parse_bool(get(safe, "HIDE_TRAY_ICON", "False"), False),
        playtime=pt_policy,
    )


def convert_legacy_user_state(username: str, legacy_work_path: str) -> UserAccountingState:
    """Convert /var/lib/timekpr/work/<user>.time to UserAccountingState."""
    safe = validate_username(username)
    cp = _read_ini(legacy_work_path)
    if cp is None or not cp.has_section(safe):
        return UserAccountingState(username=safe)

    def get(key: str, default: str | None = None) -> str | None:
        return cp.get(safe, key, fallback=default)

    last_checked_raw = (get("LAST_CHECKED", "") or "").strip()
    date_str = ""
    week_key = ""
    month_key = ""
    last_checked_utc = ""
    if last_checked_raw:
        try:
            dt = datetime.strptime(last_checked_raw, "%Y-%m-%d %H:%M:%S")
            date_str = dt.date().isoformat()
            iso_yr, iso_wk, _ = dt.isocalendar()
            week_key = f"{iso_yr}-W{iso_wk:02d}"
            month_key = dt.strftime("%Y-%m")
            last_checked_utc = dt.astimezone(timezone.utc).isoformat()
        except ValueError:
            pass

    return UserAccountingState(
        username=safe,
        date=date_str,
        week_key=week_key,
        month_key=month_key,
        spent_today=_parse_int(get("TIME_SPENT_DAY", "0"), 0),
        spent_balance=_parse_int(get("TIME_SPENT_BALANCE", "0"), 0),
        spent_week=_parse_int(get("TIME_SPENT_WEEK", "0"), 0),
        spent_month=_parse_int(get("TIME_SPENT_MONTH", "0"), 0),
        playtime_spent_today=_parse_int(get("PLAYTIME_SPENT_DAY", "0"), 0),
        playtime_balance=_parse_int(get("PLAYTIME_SPENT_BALANCE", "0"), 0),
        last_checked_utc=last_checked_utc,
    )


def migrate_from_timekpr(
    store: AtomicJsonStore,
    legacy_etc_dir: str = "/etc/timekpr",
    legacy_var_dir: str = "/var/lib/timekpr",
    dry_run: bool = False,
) -> dict[str, Any]:
    """One-way import of legacy Timekpr-nExT / curfewd 0.6.x config & work files."""
    report: dict[str, Any] = {
        "dry_run": dry_run,
        "legacy_found": False,
        "daemon_config_migrated": False,
        "users_migrated": [],
        "skipped": [],
    }

    legacy_main = os.path.join(legacy_etc_dir, "timekpr.conf")
    legacy_cfg_dir = os.path.join(legacy_var_dir, "config")
    legacy_work_dir = os.path.join(legacy_var_dir, "work")

    if os.path.isfile(legacy_main):
        report["legacy_found"] = True
        cfg = convert_legacy_daemon_config(legacy_main)
        if not dry_run:
            store.save_daemon_config(cfg)
        report["daemon_config_migrated"] = True

    if os.path.isdir(legacy_cfg_dir):
        for fname in sorted(os.listdir(legacy_cfg_dir)):
            if not (fname.startswith("timekpr.") and fname.endswith(".conf")):
                continue
            uname = fname[len("timekpr."):-len(".conf")]
            if uname == "USER":
                report["skipped"].append(fname)
                continue
            try:
                validate_username(uname)
            except ValueError:
                report["skipped"].append(fname)
                continue

            report["legacy_found"] = True
            policy = convert_legacy_user_policy(uname, os.path.join(legacy_cfg_dir, fname))
            work_path = os.path.join(legacy_work_dir, f"{uname}.time")
            state = convert_legacy_user_state(uname, work_path)
            if not dry_run:
                store.save_user_policy(policy)
                if os.path.isfile(work_path):
                    store.save_user_state(state)
            report["users_migrated"].append(uname)

    return report
