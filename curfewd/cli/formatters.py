"""Human-readable and JSON terminal formatters for `curfewctl` (Spec 005)."""

import json
import sys
from typing import Any

from curfewd import __version__
from curfewd.parsing import WEEKDAYS, format_duration


def print_json(data: Any, stream: Any = None) -> None:
    out = stream or sys.stdout
    out.write(json.dumps(data, indent=2, sort_keys=True) + "\n")


def format_windows_human(windows: list[dict[str, Any]]) -> str:
    if not windows:
        return "blocked"
    return ", ".join(f"{w['start']}-{w['end']}" for w in windows)


def print_users_table(users: list[dict[str, Any]], stream: Any) -> None:
    if not users:
        stream.write("No tracked or discoverable desktop users found.\n")
        return
    header = (
        f"{'USER':<14} {'STATE':<13} {'LEFT TODAY':<11} "
        f"{'SPENT':<9} {'LIMIT':<9} {'LOCKOUT':<10} {'LOGGED IN'}\n"
    )
    stream.write(header)
    stream.write("-" * (len(header) - 1) + "\n")
    for u in users:
        left = (
            "unlimited"
            if u["enforcement_state"] in ("unrestricted", "paused")
            else format_duration(u["time_left_today"])
        )
        spent = format_duration(u["time_spent_today"])
        limit = (
            "unlimited"
            if u["daily_limit_today"] >= 86400
            else format_duration(u["daily_limit_today"])
        )
        logged = (
            "yes (locked)"
            if (u["logged_in"] and u["screen_locked"])
            else ("yes" if u["logged_in"] else "no")
        )
        stream.write(
            f"{u['username']:<14} {u['enforcement_state']:<13} {left:<11} "
            f"{spent:<9} {limit:<9} {u['lockout_action']:<10} {logged}\n"
        )


def _print_user_schedule(pol: dict[str, Any], stream: Any) -> None:
    stream.write("Schedule:\n")
    for day in WEEKDAYS:
        lim = pol["daily_limits"].get(day, 86400)
        lim_s = "unlimited" if lim >= 86400 else format_duration(lim)
        win_s = format_windows_human(pol["curfew_windows"].get(day, []))
        stream.write(f"  {day}: limit={lim_s:<10} windows={win_s}\n")


def _print_user_app_limits(st: dict[str, Any], stream: Any) -> None:
    al = st.get("app_limits_status", {})
    if not al.get("enabled"):
        return
    apps_list = al.get("apps") or []
    stream.write(f"App Limits: enabled ({len(apps_list)} tracked)\n")
    for a in apps_list:
        run_tag = " [running]" if a.get("running") else ""
        stream.write(
            f"  - {a['label']} ({a['pattern']}): "
            f"{format_duration(a.get('time_left_today', 0))} left / "
            f"{format_duration(a.get('allotted_today', a.get('daily_limit_sec', 0)))}{run_tag}\n"
        )


def print_user_status(st: dict[str, Any], stream: Any) -> None:
    pol = st["policy"]
    pause = st["pause"]
    stream.write(f"User: {st['username']} ({st.get('full_name') or st['username']})\n")
    stream.write(
        f"State: {st['enforcement_state']} | Logged in: {'yes' if st['logged_in'] else 'no'} "
        f"| Locked: {'yes' if st['screen_locked'] else 'no'}\n"
    )
    if pause.get("active"):
        until = pause.get("until_utc") or "indefinite"
        reason = f" ({pause['reason']})" if pause.get("reason") else ""
        stream.write(f"Pause: ACTIVE until {until}{reason}\n")

    is_open = st["enforcement_state"] in ("unrestricted", "paused")
    left_str = (
        "unlimited" if is_open else format_duration(st["time_left_today"], include_seconds=True)
    )
    cont_str = (
        "unlimited"
        if is_open
        else format_duration(st["time_left_continuous"], include_seconds=True)
    )
    daily_lim = (
        "unlimited"
        if st["daily_limit_today"] >= 86400
        else format_duration(st["daily_limit_today"])
    )
    stream.write(
        f"Today: {left_str} left (continuous: {cont_str}) | "
        f"Spent: {format_duration(st['time_spent_today'])} / {daily_lim}"
        f" | Bonus: {format_duration(st['bonus_today'])}\n"
    )
    ovr = st.get("today_override") or {}
    if ovr.get("active"):
        win_txt = format_windows_human(st.get("today_windows") or [])
        stream.write(f"Today override: ACTIVE (windows={win_txt})\n")
    stream.write(
        f"Totals: week={format_duration(st['time_spent_week'])} "
        f"month={format_duration(st['time_spent_month'])} | "
        f"Lockout: {pol['lockout_action']} | Track inactive: {pol['track_inactive']} "
        f"| Hide tray: {pol['hide_tray_icon']}\n"
    )
    if st.get("next_curfew_at"):
        stream.write(f"Next curfew: {st['next_curfew_at']}\n")
    if st.get("next_allowed_at"):
        stream.write(f"Next allowed window: {st['next_allowed_at']}\n")
    _print_user_schedule(pol, stream)
    _print_user_app_limits(st, stream)


def print_history(hist: dict[str, Any], stream: Any) -> None:
    stream.write(
        f"Usage History for {hist['username']} (last {hist['days']} days since {hist['start_date']}):\n"
    )
    if not hist["daily"]:
        stream.write("  No daily usage recorded yet.\n")
    else:
        stream.write(
            f"  {'DATE':<12} {'ACTIVE':<10} {'INACTIVE':<10} {'APPS':<10} {'BONUS':<9} {'LOCKOUTS'}\n"
        )
        for row in hist["daily"]:
            stream.write(
                f"  {row['date']:<12} {format_duration(row['active_sec']):<10} "
                f"{format_duration(row['inactive_sec']):<10} {format_duration(row['app_limits_sec']):<10} "
                f"{format_duration(row['bonus_sec']):<9} {row['lockouts']}\n"
            )
    if hist["events"]:
        stream.write("Recent events:\n")
        for ev in hist["events"][:10]:
            stream.write(
                f"  {ev['ts_utc']} [{ev['kind']}] by {ev['actor']} {json.dumps(ev['detail'])}\n"
            )


def print_doctor(report: dict[str, Any], stream: Any) -> None:
    stream.write(
        f"curfewd doctor (v{report.get('version', __version__)}) — overall: {report['overall'].upper()}\n"
    )
    for chk in report.get("checks", []):
        tag = {"ok": "OK  ", "warn": "WARN", "error": "FAIL"}.get(
            chk["status"], chk["status"].upper()
        )
        stream.write(f"  [{tag}] {chk['name']}: {chk['detail']}\n")
    sessions = report.get("active_sessions", [])
    if sessions:
        stream.write("Active graphical sessions:\n")
        for s in sessions:
            stream.write(
                f"  - {s['username']} session={s['session_id']} type={s['session_type']} "
                f"desktop={s['desktop']} locked={s['locked']} backends={','.join(s['lock_backends'])}\n"
            )
