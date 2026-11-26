"""Modern curfewctl command-line interface with structured --json output."""

import argparse
from datetime import datetime, timedelta, timezone
import json
import sys
from typing import Any, Callable

from curfewd import (
    EXIT_DAEMON_UNAVAILABLE,
    EXIT_ERROR,
    EXIT_OK,
    EXIT_PERMISSION_DENIED,
    EXIT_USAGE,
    __version__,
)
from curfewd.dbus.client import (
    CurfewClient,
    CurfewError,
    DaemonUnavailableError,
    PermissionDeniedError,
    UserNotFoundError,
    ValidationError,
)
from curfewd.engine.doctor import apply_dconf_lock_fix, run_doctor
from curfewd.models import LOCKOUT_ACTIONS, AppLimitRule
from curfewd.parsing import (
    WEEKDAYS,
    format_duration,
    parse_days,
    parse_delta,
    parse_duration,
    parse_until_timestamp,
    parse_windows,
)


def _normalize_argv(argv: list[str]) -> list[str]:
    """Allow negative duration tokens like `curfewctl grant alice -15m --json` without `--`."""
    if not argv or "grant" not in argv:
        return argv
    idx = argv.index("grant")
    prefix = argv[: idx + 1]
    rest = argv[idx + 1 :]
    flags: list[str] = []
    positionals: list[str] = []
    for tok in rest:
        if tok == "--":
            continue
        if tok.startswith("--"):
            flags.append(tok)
        else:
            positionals.append(tok)
    if len(positionals) >= 2 and len(positionals[1]) >= 2 and positionals[1][0] == "-" and positionals[1][1].isdigit():
        return prefix + flags + [positionals[0], "--"] + positionals[1:]
    return argv


def _print_json(data: Any, stream=None) -> None:
    out = stream or sys.stdout
    out.write(json.dumps(data, indent=2, sort_keys=True) + "\n")


def _format_windows_human(windows: list[dict[str, Any]]) -> str:
    if not windows:
        return "blocked"
    return ", ".join(f"{w['start']}-{w['end']}" for w in windows)


def _print_users_table(users: list[dict[str, Any]], stream) -> None:
    if not users:
        stream.write("No tracked or discoverable desktop users found.\n")
        return
    header = f"{'USER':<14} {'STATE':<13} {'LEFT TODAY':<11} {'SPENT':<9} {'LIMIT':<9} {'LOCKOUT':<10} {'LOGGED IN'}\n"
    stream.write(header)
    stream.write("-" * (len(header) - 1) + "\n")
    for u in users:
        left = "unlimited" if u["enforcement_state"] in ("unrestricted", "paused") else format_duration(u["time_left_today"])
        spent = format_duration(u["time_spent_today"])
        limit = "unlimited" if u["daily_limit_today"] >= 86400 else format_duration(u["daily_limit_today"])
        logged = "yes (locked)" if (u["logged_in"] and u["screen_locked"]) else ("yes" if u["logged_in"] else "no")
        stream.write(
            f"{u['username']:<14} {u['enforcement_state']:<13} {left:<11} {spent:<9} {limit:<9} {u['lockout_action']:<10} {logged}\n"
        )


def _print_user_status(st: dict[str, Any], stream) -> None:
    pol = st["policy"]
    pause = st["pause"]
    stream.write(f"User: {st['username']} ({st.get('full_name') or st['username']})\n")
    stream.write(f"State: {st['enforcement_state']} | Logged in: {'yes' if st['logged_in'] else 'no'} | Locked: {'yes' if st['screen_locked'] else 'no'}\n")
    if pause.get("active"):
        until = pause.get("until_utc") or "indefinite"
        reason = f" ({pause['reason']})" if pause.get("reason") else ""
        stream.write(f"Pause: ACTIVE until {until}{reason}\n")
    left_str = "unlimited" if st["enforcement_state"] in ("unrestricted", "paused") else format_duration(st["time_left_today"], include_seconds=True)
    cont_str = "unlimited" if st["enforcement_state"] in ("unrestricted", "paused") else format_duration(st["time_left_continuous"], include_seconds=True)
    stream.write(
        f"Today: {left_str} left (continuous: {cont_str}) | "
        f"Spent: {format_duration(st['time_spent_today'])} / "
        f"{'unlimited' if st['daily_limit_today'] >= 86400 else format_duration(st['daily_limit_today'])}"
        f" | Bonus: {format_duration(st['bonus_today'])}\n"
    )
    ovr = st.get("today_override") or {}
    if ovr.get("active"):
        win_txt = _format_windows_human(st.get("today_windows") or [])
        stream.write(f"Today override: ACTIVE (windows={win_txt})\n")
    stream.write(
        f"Totals: week={format_duration(st['time_spent_week'])} "
        f"month={format_duration(st['time_spent_month'])} | "
        f"Lockout: {pol['lockout_action']} | Track inactive: {pol['track_inactive']} | Hide tray: {pol['hide_tray_icon']}\n"
    )
    if st.get("next_curfew_at"):
        stream.write(f"Next curfew: {st['next_curfew_at']}\n")
    if st.get("next_allowed_at"):
        stream.write(f"Next allowed window: {st['next_allowed_at']}\n")
    stream.write("Schedule:\n")
    for day in WEEKDAYS:
        lim = pol["daily_limits"].get(day, 86400)
        lim_s = "unlimited" if lim >= 86400 else format_duration(lim)
        win_s = _format_windows_human(pol["curfew_windows"].get(day, []))
        stream.write(f"  {day}: limit={lim_s:<10} windows={win_s}\n")
    al = st.get("app_limits_status", {})
    if al.get("enabled"):
        apps_list = al.get("apps") or []
        stream.write(f"App Limits: enabled ({len(apps_list)} tracked)\n")
        for a in apps_list:
            run_tag = " [running]" if a.get("running") else ""
            stream.write(
                f"  - {a['label']} ({a['pattern']}): "
                f"{format_duration(a.get('time_left_today', 0))} left / "
                f"{format_duration(a.get('allotted_today', a.get('daily_limit_sec', 0)))}{run_tag}\n"
            )


def _print_history(hist: dict[str, Any], stream) -> None:
    stream.write(f"Usage History for {hist['username']} (last {hist['days']} days since {hist['start_date']}):\n")
    if not hist["daily"]:
        stream.write("  No daily usage recorded yet.\n")
    else:
        stream.write(f"  {'DATE':<12} {'ACTIVE':<10} {'INACTIVE':<10} {'APPS':<10} {'BONUS':<9} {'LOCKOUTS'}\n")
        for row in hist["daily"]:
            stream.write(
                f"  {row['date']:<12} {format_duration(row['active_sec']):<10} "
                f"{format_duration(row['inactive_sec']):<10} {format_duration(row['app_limits_sec']):<10} "
                f"{format_duration(row['bonus_sec']):<9} {row['lockouts']}\n"
            )
    if hist["events"]:
        stream.write("Recent events:\n")
        for ev in hist["events"][:10]:
            stream.write(f"  {ev['ts_utc']} [{ev['kind']}] by {ev['actor']} {json.dumps(ev['detail'])}\n")


def _print_doctor(report: dict[str, Any], stream) -> None:
    stream.write(f"curfewd doctor (v{report.get('version', __version__)}) — overall: {report['overall'].upper()}\n")
    for chk in report.get("checks", []):
        tag = {"ok": "OK  ", "warn": "WARN", "error": "FAIL"}.get(chk["status"], chk["status"].upper())
        stream.write(f"  [{tag}] {chk['name']}: {chk['detail']}\n")
    sessions = report.get("active_sessions", [])
    if sessions:
        stream.write("Active graphical sessions:\n")
        for s in sessions:
            stream.write(
                f"  - {s['username']} session={s['session_id']} type={s['session_type']} "
                f"desktop={s['desktop']} locked={s['locked']} backends={','.join(s['lock_backends'])}\n"
            )


def build_parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--json", action="store_true", dest="json_output", default=argparse.SUPPRESS, help="Emit structured JSON output")

    parser = argparse.ArgumentParser(
        prog="curfewctl",
        description="curfewd screen-time control plane CLI",
        parents=[common],
    )
    parser.add_argument("--version", action="version", version=f"curfewctl {__version__}")
    parser.add_argument("--session-bus", action="store_true", help="Connect to session D-Bus (development/testing)")

    sub = parser.add_subparsers(dest="command", required=True)

    p_status = sub.add_parser("status", parents=[common], help="Show live status for all users or one user")
    p_status.add_argument("user", nargs="?", help="Optional Linux username")

    sub.add_parser("users", parents=[common], help="List tracked and discoverable desktop users")

    p_grant = sub.add_parser("grant", parents=[common], help="Add, subtract, or set remaining screen time today")
    p_grant.add_argument("user", help="Linux username")
    p_grant.add_argument("delta", help="Adjustment such as +30m, -15m, =2h")

    p_limit = sub.add_parser("limit", parents=[common], help="Configure daily, weekly, or monthly time limits")
    p_limit.add_argument("user", help="Linux username")
    p_limit.add_argument("duration", help="Daily limit (e.g. 1h, 1h30m, 45m, unlimited)")
    p_limit.add_argument("--days", default="all", help="Days to update (e.g. all, weekdays, weekends, mon-fri, sat,sun)")
    p_limit.add_argument("--week", default=None, help="Optional weekly cap (e.g. 10h or unlimited)")
    p_limit.add_argument("--month", default=None, help="Optional monthly cap (e.g. 40h or unlimited)")

    p_win = sub.add_parser("window", parents=[common], help="Configure allowed curfew windows")
    p_win.add_argument("user", help="Linux username")
    p_win.add_argument("windows", help="Comma-separated windows e.g. '08:00-22:00', '07:30-08:30,15:00-21:00', 'all', 'none'")
    p_win.add_argument("--days", default="all", help="Days to update (e.g. all, weekdays, fri, mon-thu,sun)")

    p_lockout = sub.add_parser("lockout", parents=[common], help="Set lockout action when time expires")
    p_lockout.add_argument("user", help="Linux username")
    p_lockout.add_argument("action", choices=LOCKOUT_ACTIONS, help="Action to take at 00:00 or curfew")

    p_pause = sub.add_parser("pause", parents=[common], help="Pause enforcement without altering saved schedule")
    p_pause.add_argument("user", help="Linux username")
    grp = p_pause.add_mutually_exclusive_group()
    grp.add_argument("--for", dest="pause_for", help="Pause duration (e.g. 2h, 30m)")
    grp.add_argument("--until", dest="pause_until", help="Pause until time (HH:MM, tomorrow, or ISO-8601)")
    grp.add_argument("--today", action="store_true", help="Pause until next local midnight")
    grp.add_argument("--indefinite", action="store_true", help="Pause indefinitely until resumed")
    p_pause.add_argument("--reason", default="", help="Optional audit reason")
    p_pause.add_argument("--keep-tray", action="store_true", help="Keep tray icon visible while paused")

    p_resume = sub.add_parser("resume", parents=[common], help="Resume saved schedule for a paused user")
    p_resume.add_argument("user", help="Linux username")

    p_lock = sub.add_parser("lock", parents=[common], help="Immediately lock a user's active graphical session(s)")
    p_lock.add_argument("user", help="Linux username")

    p_hist = sub.add_parser("history", parents=[common], help="Show daily screen-time history and audit events")
    p_hist.add_argument("user", help="Linux username")
    p_hist.add_argument("--days", type=int, default=7, help="Number of days (1..365, default 7)")

    p_today = sub.add_parser("today", parents=[common], help="Override today's allowance or allowed window without altering the weekly schedule")
    p_today.add_argument("user", help="Linux username")
    p_today.add_argument("--limit", dest="today_limit", default=None, help="Today-only daily allowance (e.g. 2h, 45m, unlimited)")
    p_today.add_argument("--window", dest="today_window", default=None, help="Today-only allowed window (e.g. '08:00-22:30', 'all', 'none')")
    p_today.add_argument("--reset", action="store_true", help="Clear today's override and revert to the weekly schedule")

    p_al = sub.add_parser("app-limits", parents=[common], help="Manage individual per-app daily limits")
    p_al.add_argument("user", help="Linux username")
    al_sub = p_al.add_subparsers(dest="al_action", required=True)
    al_sub.add_parser("status", parents=[common])
    al_sub.add_parser("enable", parents=[common])
    al_sub.add_parser("disable", parents=[common])
    p_al_lim = al_sub.add_parser("limit", parents=[common], help="Set daily limit for an app pattern (or all tracked apps)")
    p_al_lim.add_argument("target", help="App pattern (or duration if setting all tracked apps)")
    p_al_lim.add_argument("duration", nargs="?", default=None, help="Daily limit duration (e.g. 45m, 1h)")
    p_al_add = al_sub.add_parser("add", parents=[common], help="Track an app or game with its own daily limit")
    p_al_add.add_argument("pattern", help="Process name or regex pattern")
    p_al_add.add_argument("label", nargs="?", default="", help="Friendly app label")
    p_al_add.add_argument("--limit", dest="app_limit", default="45m", help="Daily time limit for this app (default: 45m)")
    p_al_add.add_argument("--category", default="", help="Optional category (e.g. Gaming, Social, Media)")
    p_al_grant = al_sub.add_parser("grant", parents=[common], help="Add or subtract bonus time today for one app")
    p_al_grant.add_argument("pattern", help="Tracked app pattern")
    p_al_grant.add_argument("delta", help="Adjustment such as +15m or -15m")
    p_al_rm = al_sub.add_parser("remove", parents=[common], help="Remove an app limit rule")
    p_al_rm.add_argument("pattern", help="Pattern to remove")

    p_cfg = sub.add_parser("config", parents=[common], help="Inspect or update global daemon configuration")
    cfg_sub = p_cfg.add_subparsers(dest="cfg_action", required=True)
    cfg_sub.add_parser("show", parents=[common])
    p_cfg_set = cfg_sub.add_parser("set", parents=[common])
    p_cfg_set.add_argument("key", help="DaemonConfig key (e.g. log_level, final_countdown_sec, session_lock_fallback)")
    p_cfg_set.add_argument("value", help="New value")

    p_doc = sub.add_parser("doctor", parents=[common], help="Verify service, D-Bus, Polkit, dconf lock policy, and helpers")
    p_doc.add_argument("--fix", action="store_true", help="Apply built-in root dconf lock-screen hardening")

    return parser


def _coerce_config_value(existing: Any, raw_value: str) -> Any:
    if isinstance(existing, bool):
        low = raw_value.strip().lower()
        if low in ("true", "1", "yes", "on"):
            return True
        if low in ("false", "0", "no", "off"):
            return False
        raise ValueError(f"Expected boolean value, got {raw_value!r}")
    if isinstance(existing, int):
        return int(raw_value.strip())
    if isinstance(existing, list):
        if existing and isinstance(existing[0], int):
            return [int(x.strip()) for x in raw_value.split(",") if x.strip()]
        return [x.strip() for x in raw_value.split(",") if x.strip()]
    return raw_value.strip()


def run_cli(
    argv: list[str] | None = None,
    client_factory: Callable[[bool], CurfewClient] | None = None,
    stdout=None,
    stderr=None,
) -> int:
    out = stdout or sys.stdout
    err = stderr or sys.stderr
    raw_argv = list(sys.argv[1:] if argv is None else argv)
    parser = build_parser()

    try:
        args = parser.parse_args(_normalize_argv(raw_argv))
    except SystemExit as exc:
        return int(exc.code) if isinstance(exc.code, int) else EXIT_USAGE

    as_json = bool(getattr(args, "json_output", False))

    def _make_client() -> CurfewClient:
        if client_factory is not None:
            return client_factory(bool(args.session_bus))
        if args.session_bus:
            import dbus

            return CurfewClient(bus=dbus.SessionBus())
        return CurfewClient()

    try:
        cmd = args.command

        if cmd == "doctor":
            fix_result = None
            if args.fix:
                try:
                    fixed = _make_client().apply_doctor_fix()
                    fix_result = fixed.get("fix_applied")
                    report = fixed.get("doctor") or run_doctor(dbus_connected=True)
                except DaemonUnavailableError:
                    fix_result = apply_dconf_lock_fix()
                    report = run_doctor(dbus_connected=False)
            else:
                try:
                    report = _make_client().run_doctor()
                except DaemonUnavailableError:
                    report = run_doctor(dbus_connected=False)
            if fix_result is not None:
                report["fix_applied"] = fix_result
            if as_json:
                _print_json(report, out)
            else:
                if fix_result is not None:
                    out.write(f"Applied dconf lock hardening: {fix_result['detail']}\n")
                _print_doctor(report, out)
            return EXIT_OK if report["overall"] != "error" else EXIT_ERROR

        client = _make_client()

        if cmd == "users":
            users = client.list_users()
            if as_json:
                _print_json({"users": users}, out)
            else:
                _print_users_table(users, out)
            return EXIT_OK

        if cmd == "status":
            if args.user:
                st = client.get_user_status(args.user)
                if as_json:
                    _print_json(st, out)
                else:
                    _print_user_status(st, out)
            else:
                users = client.list_users()
                if as_json:
                    _print_json({"users": users}, out)
                else:
                    _print_users_table(users, out)
            return EXIT_OK

        if cmd == "grant":
            mode, delta_sec = parse_delta(args.delta)
            res = client.grant_time(args.user, delta_sec, mode=mode)
            if as_json:
                _print_json(res, out)
            else:
                st = res["status"]
                out.write(
                    f"Updated {args.user}: applied {format_duration(res['applied_sec'], include_seconds=True)}; "
                    f"remaining today: {format_duration(st['time_left_today'], include_seconds=True)}\n"
                )
            return EXIT_OK

        if cmd == "limit":
            days = parse_days(args.days)
            sec = parse_duration(args.duration)
            st = client.get_user_status(args.user)
            policy = dict(st["policy"])
            policy["daily_limits"] = dict(policy["daily_limits"])
            for d in days:
                policy["daily_limits"][d] = sec
            if args.week is not None:
                policy["weekly_limit"] = None if args.week.lower() in ("unlimited", "none") else parse_duration(args.week)
            if args.month is not None:
                policy["monthly_limit"] = None if args.month.lower() in ("unlimited", "none") else parse_duration(args.month)
            updated = client.set_user_policy(args.user, policy)
            if as_json:
                _print_json(updated, out)
            else:
                out.write(f"Updated daily limit for {args.user} ({','.join(days)}): {format_duration(sec)}\n")
            return EXIT_OK

        if cmd == "window":
            days = parse_days(args.days)
            windows = parse_windows(args.windows)
            st = client.get_user_status(args.user)
            policy = dict(st["policy"])
            policy["curfew_windows"] = dict(policy["curfew_windows"])
            for d in days:
                policy["curfew_windows"][d] = windows
            updated = client.set_user_policy(args.user, policy)
            if as_json:
                _print_json(updated, out)
            else:
                out.write(f"Updated curfew windows for {args.user} ({','.join(days)}): {_format_windows_human(windows)}\n")
            return EXIT_OK

        if cmd == "lockout":
            st = client.get_user_status(args.user)
            policy = dict(st["policy"])
            policy["lockout_action"] = args.action
            updated = client.set_user_policy(args.user, policy)
            if as_json:
                _print_json(updated, out)
            else:
                out.write(f"Updated lockout action for {args.user}: {args.action}\n")
            return EXIT_OK

        if cmd == "pause":
            until_utc = ""
            if args.pause_for:
                dur = parse_duration(args.pause_for)
                until_utc = (datetime.now(timezone.utc) + timedelta(seconds=dur)).isoformat()
            elif args.pause_until:
                until_utc = parse_until_timestamp(args.pause_until)
            elif args.today:
                until_utc = parse_until_timestamp("today")
            updated = client.pause_user(
                args.user,
                until_utc=until_utc,
                reason=args.reason,
                hide_tray=not bool(args.keep_tray),
            )
            if as_json:
                _print_json(updated, out)
            else:
                mode_str = f"until {until_utc}" if until_utc else "indefinitely"
                out.write(f"Paused enforcement for {args.user} ({mode_str}). Saved schedule preserved.\n")
            return EXIT_OK

        if cmd == "resume":
            updated = client.resume_user(args.user)
            if as_json:
                _print_json(updated, out)
            else:
                out.write(f"Resumed enforcement for {args.user} (state: {updated['enforcement_state']}).\n")
            return EXIT_OK

        if cmd == "lock":
            res = client.lock_user(args.user)
            if as_json:
                _print_json(res, out)
            else:
                if res.get("locked"):
                    sessions = res.get("sessions", [])
                    detail = ", ".join(
                        f"{s['session_id']} via {s['backend']} ({s['stage']}, {s['elapsed_ms']}ms)"
                        for s in sessions
                    )
                    out.write(f"Locked {args.user}: {detail}\n")
                else:
                    out.write(f"Could not lock {args.user}: {res.get('detail') or 'lock verification failed'}\n")
            return EXIT_OK if res.get("locked") else EXIT_ERROR

        if cmd == "history":
            hist = client.get_usage_history(args.user, days=args.days)
            if as_json:
                _print_json(hist, out)
            else:
                _print_history(hist, out)
            return EXIT_OK

        if cmd == "today":
            if not args.reset and args.today_limit is None and args.today_window is None:
                raise ValueError("Specify at least one of --limit, --window, or --reset")
            lim_sec = parse_duration(args.today_limit) if args.today_limit is not None else None
            wins = parse_windows(args.today_window) if args.today_window is not None else None
            updated = client.set_today_override(
                args.user,
                limit_sec=lim_sec,
                windows=wins,
                reset=bool(args.reset),
            )
            if as_json:
                _print_json(updated, out)
            else:
                if args.reset:
                    out.write(f"Cleared today's override for {args.user}; reverted to weekly schedule.\n")
                else:
                    out.write(
                        f"Updated today's schedule for {args.user}: "
                        f"allowance={'unlimited' if updated['daily_limit_today'] >= 86400 else format_duration(updated['daily_limit_today'])}, "
                        f"windows={_format_windows_human(updated.get('today_windows') or [])}\n"
                    )
            return EXIT_OK

        if cmd == "app-limits":
            st = client.get_user_status(args.user)
            policy = dict(st["policy"])
            al = dict(policy["app_limits"])
            if args.al_action == "status":
                payload = {"username": args.user, "app_limits": al, "app_limits_status": st["app_limits_status"]}
                if as_json:
                    _print_json(payload, out)
                else:
                    out.write(f"App Limits for {args.user}: enabled={al['enabled']} apps={len(al['apps'])}\n")
                    for a in st["app_limits_status"].get("apps", al["apps"]):
                        lim_s = format_duration(a.get("allotted_today", a.get("daily_limit_sec", 0)))
                        rem_s = format_duration(a.get("time_left_today", a.get("daily_limit_sec", 0)))
                        out.write(f"  - {a['label']} ({a['pattern']}): {rem_s} left / {lim_s}\n")
                return EXIT_OK
            if args.al_action == "grant":
                mode, delta_sec = parse_delta(args.delta)
                if mode == "set":
                    raise ValueError("app-limits grant supports relative deltas like +15m or -15m")
                res = client.grant_app_time(args.user, args.pattern, delta_sec)
                if as_json:
                    _print_json(res, out)
                else:
                    out.write(
                        f"Updated {args.pattern} for {args.user}: applied {format_duration(res['applied_sec'], include_seconds=True)}\n"
                    )
                return EXIT_OK
            if args.al_action == "enable":
                al["enabled"] = True
            elif args.al_action == "disable":
                al["enabled"] = False
            elif args.al_action == "limit":
                apps = [dict(a) for a in al["apps"]]
                if args.duration is None:
                    sec = parse_duration(args.target)
                    for a in apps:
                        a["daily_limit_sec"] = sec
                else:
                    sec = parse_duration(args.duration)
                    matched = False
                    for a in apps:
                        if a["pattern"] == args.target:
                            a["daily_limit_sec"] = sec
                            matched = True
                    if not matched:
                        raise ValueError(f"Tracked app pattern not found: {args.target!r}")
                al["apps"] = apps
            elif args.al_action == "add":
                lim_sec = parse_duration(args.app_limit)
                app = AppLimitRule(
                    pattern=args.pattern,
                    label=args.label or args.pattern,
                    daily_limit_sec=lim_sec,
                    category=args.category,
                ).to_dict()
                apps = [a for a in al["apps"] if a["pattern"] != app["pattern"]]
                apps.append(app)
                al["apps"] = apps
            elif args.al_action == "remove":
                al["apps"] = [a for a in al["apps"] if a["pattern"] != args.pattern]
            policy["app_limits"] = al
            updated = client.set_user_policy(args.user, policy)
            if as_json:
                _print_json(updated, out)
            else:
                out.write(f"Updated App Limits for {args.user} (enabled={updated['policy']['app_limits']['enabled']}).\n")
            return EXIT_OK

        if cmd == "config":
            cfg = client.get_daemon_config()
            if args.cfg_action == "show":
                if as_json:
                    _print_json(cfg, out)
                else:
                    for k, v in sorted(cfg.items()):
                        out.write(f"{k} = {v}\n")
                return EXIT_OK
            if args.cfg_action == "set":
                if args.key not in cfg:
                    raise ValidationError(f"Unknown daemon config key: {args.key!r}")
                cfg[args.key] = _coerce_config_value(cfg[args.key], args.value)
                updated = client.set_daemon_config(cfg)
                if as_json:
                    _print_json(updated, out)
                else:
                    out.write(f"Updated daemon config: {args.key} = {updated[args.key]}\n")
                return EXIT_OK

        return EXIT_USAGE

    except (ValueError, ValidationError) as exc:
        if as_json:
            _print_json({"error": "validation_error", "message": str(exc)}, err)
        else:
            err.write(f"error: {exc}\n")
        return EXIT_USAGE
    except (PermissionError, PermissionDeniedError) as exc:
        if as_json:
            _print_json({"error": "permission_denied", "message": str(exc)}, err)
        else:
            err.write(f"permission denied: {exc}\n")
        return EXIT_PERMISSION_DENIED
    except DaemonUnavailableError as exc:
        if as_json:
            _print_json({"error": "daemon_unavailable", "message": str(exc)}, err)
        else:
            err.write(f"daemon unavailable: {exc}\n")
        return EXIT_DAEMON_UNAVAILABLE
    except (UserNotFoundError, CurfewError, OSError) as exc:
        if as_json:
            _print_json({"error": "error", "message": str(exc)}, err)
        else:
            err.write(f"error: {exc}\n")
        return EXIT_ERROR


def main(argv: list[str] | None = None) -> int:
    return run_cli(argv)
