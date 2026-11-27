"""Human-readable and JSON terminal formatters for `curfewctl` (Spec 005 + Spec 006)."""

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
        f"{'USER':<14} {'ROLE':<7} {'STATE':<13} {'LEFT TODAY':<11} "
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
        role = str(u.get("role") or "child")
        stream.write(
            f"{u['username']:<14} {role:<7} {u['enforcement_state']:<13} {left:<11} "
            f"{spent:<9} {limit:<9} {u['lockout_action']:<10} {logged}\n"
        )


def _print_user_schedule(pol: dict[str, Any], stream: Any) -> None:
    stream.write("Schedule:\n")
    for day in WEEKDAYS:
        lim = pol["daily_limits"].get(day, 86400)
        lim_s = "unlimited" if lim >= 86400 else format_duration(lim)
        win_s = format_windows_human(pol["curfew_windows"].get(day, []))
        stream.write(f"  {day}: limit={lim_s:<10} windows={win_s}\n")
    holidays = pol.get("holidays") or []
    if holidays:
        stream.write("Holidays:\n")
        for h in holidays:
            lbl = f" ({h['label']})" if h.get("label") else ""
            stream.write(f"  - {h['id']}: {h['start_date']}..{h['end_date']} [{h['mode']}]{lbl}\n")


def _print_user_app_limits(st: dict[str, Any], stream: Any) -> None:
    al = st.get("app_limits_status", {})
    if not al.get("enabled"):
        return
    apps_list = al.get("apps") or []
    cats_list = al.get("categories") or []
    stream.write(f"App Limits: enabled ({len(apps_list)} tracked, {len(cats_list)} categories)\n")
    for c in cats_list:
        run_tag = " [running]" if c.get("running") else ""
        stream.write(
            f"  [Category] {c['category']}: "
            f"{format_duration(c.get('time_left_today', 0))} left / "
            f"{format_duration(c.get('allotted_today', c.get('daily_limit_sec', 0)))}{run_tag}\n"
        )
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
    hw = st.get("homework_mode") or {}
    stream.write(
        f"User: {st['username']} ({st.get('full_name') or st['username']}) [role={st.get('role', 'child')}]\n"
    )
    stream.write(
        f"State: {st['enforcement_state']} | Logged in: {'yes' if st['logged_in'] else 'no'} "
        f"| Locked: {'yes' if st['screen_locked'] else 'no'}"
        f"{' | Idle-paused' if st.get('idle_paused') else ''}\n"
    )
    if pause.get("active"):
        until = pause.get("until_utc") or "indefinite"
        reason = f" ({pause['reason']})" if pause.get("reason") else ""
        stream.write(f"Pause: ACTIVE until {until}{reason}\n")
    if hw.get("effective"):
        until = hw.get("until_utc") or hw.get("source") or "indefinite"
        stream.write(
            f"Homework Mode: ACTIVE ({until}) — blocking {', '.join(hw.get('blocked_categories') or [])}\n"
        )
    if st.get("active_holiday"):
        hol = st["active_holiday"]
        stream.write(
            f"Holiday Override: ACTIVE ({hol.get('label') or hol['id']} — {hol['mode']})\n"
        )

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
    note_sfx = f" ({st['last_bonus_note']})" if st.get("last_bonus_note") else ""
    stream.write(
        f"Today: {left_str} left (continuous: {cont_str}) | "
        f"Spent: {format_duration(st['time_spent_today'])} / {daily_lim}"
        f" | Bonus: {format_duration(st['bonus_today'])}{note_sfx}\n"
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
    top_apps = hist.get("top_apps") or []
    if top_apps:
        stream.write("Top apps:\n")
        for app in top_apps[:10]:
            stream.write(
                f"  - {app['label']} [{app['category']}]: {format_duration(app['active_sec'])} "
                f"(avg {format_duration(app['daily_avg_sec'])}/day)\n"
            )
    if hist["events"]:
        stream.write("Recent events:\n")
        for ev in hist["events"][:10]:
            stream.write(
                f"  {ev['ts_utc']} [{ev['kind']}] by {ev['actor']} {json.dumps(ev['detail'])}\n"
            )


def print_insights(username: str, days: int, top_apps: list[dict[str, Any]], stream: Any) -> None:
    stream.write(f"Top Apps for {username} (last {days} days):\n")
    if not top_apps:
        stream.write("  No passive app usage recorded yet.\n")
        return
    stream.write(f"  {'APP':<28} {'CATEGORY':<14} {'TOTAL':<10} {'DAILY AVG':<10} {'DAYS'}\n")
    for app in top_apps:
        stream.write(
            f"  {app['label'][:27]:<28} {app['category'][:13]:<14} "
            f"{format_duration(app['active_sec']):<10} {format_duration(app['daily_avg_sec']):<10} "
            f"{app.get('days_used', 0)}\n"
        )


def print_requests(requests: list[dict[str, Any]], stream: Any) -> None:
    if not requests:
        stream.write("No time requests found.\n")
        return
    stream.write(f"{'ID':<5} {'USER':<12} {'STATUS':<10} {'TARGET':<18} {'REQ':<8} {'REASON'}\n")
    for r in requests:
        target_lbl = str(r.get("target_label") or r.get("kind") or "daily")
        stream.write(
            f"{r['id']:<5} {r['username']:<12} {r['status']:<10} "
            f"{target_lbl[:17]:<18} {format_duration(r['requested_sec']):<8} {r.get('reason', '')}\n"
        )


def print_doctor(report: dict[str, Any], stream: Any) -> None:
    mode_tag = f" | session={report['session_mode']}" if report.get("session_mode") else ""
    stream.write(
        f"curfewd doctor (v{report.get('version', __version__)}) — overall: {report['overall'].upper()}{mode_tag}\n"
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
            if "screen_locked" in s and "logind_locked" in s:
                lock_txt = (
                    f"screen_locked={s['screen_locked']} (logind_locked={s['logind_locked']})"
                )
            else:
                lock_txt = f"logind_locked={s.get('logind_locked', s.get('locked', False))}"
            stream.write(
                f"  - {s['username']} session={s['session_id']} type={s['session_type']} "
                f"desktop={s['desktop']} {lock_txt} backends={','.join(s['lock_backends'])}\n"
            )
