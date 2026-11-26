"""Modern curfewctl command-line interface with structured --json output (Spec 005 + Spec 006)."""

import argparse
import sys
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

from curfewd import (
    EXIT_DAEMON_UNAVAILABLE,
    EXIT_ERROR,
    EXIT_OK,
    EXIT_PERMISSION_DENIED,
    EXIT_USAGE,
    __version__,
)
from curfewd.cli.formatters import (
    format_windows_human,
    print_doctor,
    print_history,
    print_insights,
    print_json,
    print_requests,
    print_user_status,
    print_users_table,
)
from curfewd.dbus.client import (
    CurfewClient,
    CurfewError,
    DaemonUnavailableError,
    PermissionDeniedError,
    UserNotFoundError,
    ValidationError,
)
from curfewd.engine.doctor import apply_all_lock_fixes, run_doctor
from curfewd.models import (
    HOLIDAY_MODES,
    LOCKOUT_ACTIONS,
    USER_ROLES,
    AppLimitRule,
    CategoryLimitRule,
    HolidayOverride,
)
from curfewd.parsing import (
    format_duration,
    parse_days,
    parse_delta,
    parse_duration,
    parse_until_timestamp,
    parse_windows,
)

_NEGATIVE_KEYWORDS = frozenset({"-unlimited", "-blocked", "-block", "-off", "-none", "-inf"})


def _is_negative_delta_token(tok: str) -> bool:
    low = tok.lower()
    if low in _NEGATIVE_KEYWORDS:
        return True
    return len(tok) >= 2 and tok[0] == "-" and tok[1].isdigit()


def _find_grant_subcommand_index(argv: list[str]) -> int | None:
    """Locate the structural `grant` subcommand (`grant` or `app-limits <user> grant`)."""
    cmd_idx = 0
    while cmd_idx < len(argv) and argv[cmd_idx].startswith("-"):
        cmd_idx += 1
    if cmd_idx >= len(argv):
        return None
    if argv[cmd_idx] == "grant":
        return cmd_idx
    if argv[cmd_idx] == "app-limits":
        # Skip `app-limits`, optional flags, then `<user>`, then look for `grant`
        i = cmd_idx + 1
        seen_user = False
        while i < len(argv):
            tok = argv[i]
            if tok.startswith("-"):
                i += 1
                continue
            if not seen_user:
                seen_user = True
                i += 1
                continue
            return i if tok == "grant" else None
    return None


def _normalize_argv(argv: list[str]) -> list[str]:
    """Allow negative duration tokens like `curfewctl grant alice -15m --json` without `--`."""
    idx = _find_grant_subcommand_index(argv)
    if idx is None:
        return argv
    prefix = argv[: idx + 1]
    rest = argv[idx + 1 :]
    flags: list[str] = []
    positionals: list[str] = []
    expect_note = False
    for tok in rest:
        if expect_note:
            flags.append(f"--note={tok}" if tok.startswith("-") else tok)
            if tok.startswith("-"):
                flags.pop(-2)  # Replace standalone "--note" with "--note=<val>"
            expect_note = False
            continue
        if tok == "--":
            continue
        if tok in ("-h", "--help"):
            flags.append(tok)
            continue
        if tok == "--note":
            flags.append(tok)
            expect_note = True
            continue
        if tok.startswith("--"):
            flags.append(tok)
        else:
            positionals.append(tok)
    if expect_note:
        return argv
    if len(positionals) >= 2 and _is_negative_delta_token(positionals[1]):
        return [*prefix, *flags, positionals[0], "--", *positionals[1:]]
    return argv


def _add_schedule_parsers(sub: Any, common: argparse.ArgumentParser) -> None:
    p_grant = sub.add_parser(
        "grant", parents=[common], help="Add, subtract, or set remaining screen time today"
    )
    p_grant.add_argument("user", help="Linux username")
    p_grant.add_argument("delta", help="Adjustment such as +30m, -15m, =2h")
    p_grant.add_argument("--note", default="", help="Optional positive note (e.g. 'Cleaned room')")

    p_limit = sub.add_parser(
        "limit", parents=[common], help="Configure daily, weekly, or monthly time limits"
    )
    p_limit.add_argument("user", help="Linux username")
    p_limit.add_argument("duration", help="Daily limit (e.g. 1h, 1h30m, 45m, unlimited)")
    p_limit.add_argument(
        "--days",
        default="all",
        help="Days to update (e.g. all, weekdays, weekends, mon-fri, sat,sun)",
    )
    p_limit.add_argument("--week", default=None, help="Optional weekly cap (e.g. 10h or unlimited)")
    p_limit.add_argument(
        "--month", default=None, help="Optional monthly cap (e.g. 40h or unlimited)"
    )

    p_win = sub.add_parser("window", parents=[common], help="Configure allowed curfew windows")
    p_win.add_argument("user", help="Linux username")
    p_win.add_argument(
        "windows",
        help="Comma-separated windows e.g. '08:00-22:00', '07:30-08:30,15:00-21:00', 'all', 'none'",
    )
    p_win.add_argument(
        "--days", default="all", help="Days to update (e.g. all, weekdays, fri, mon-thu,sun)"
    )

    p_lockout = sub.add_parser(
        "lockout", parents=[common], help="Set lockout action when time expires"
    )
    p_lockout.add_argument("user", help="Linux username")
    p_lockout.add_argument(
        "action", choices=LOCKOUT_ACTIONS, help="Action to take at 00:00 or curfew"
    )

    p_role = sub.add_parser("role", parents=[common], help="Set user role (child or parent)")
    p_role.add_argument("user", help="Linux username")
    p_role.add_argument("role_name", choices=USER_ROLES, help="Account role: child or parent")

    p_today = sub.add_parser(
        "today",
        parents=[common],
        help="Override today's allowance or allowed window without altering the weekly schedule",
    )
    p_today.add_argument("user", help="Linux username")
    p_today.add_argument("--limit", dest="today_limit", default=None)
    p_today.add_argument("--window", dest="today_window", default=None)
    p_today.add_argument("--reset", action="store_true")


def _add_family_parsers(sub: Any, common: argparse.ArgumentParser) -> None:
    p_pause = sub.add_parser(
        "pause", parents=[common], help="Pause enforcement without altering saved schedule"
    )
    p_pause.add_argument("user", help="Linux username")
    grp = p_pause.add_mutually_exclusive_group()
    grp.add_argument("--for", dest="pause_for", help="Pause duration (e.g. 2h, 30m)")
    grp.add_argument("--until", dest="pause_until", help="Pause until HH:MM, tomorrow, or ISO-8601")
    grp.add_argument("--today", action="store_true", help="Pause until next local midnight")
    grp.add_argument("--indefinite", action="store_true", help="Pause indefinitely until resumed")
    p_pause.add_argument("--reason", default="", help="Optional audit reason")
    p_pause.add_argument("--keep-tray", action="store_true", help="Keep tray visible while paused")

    p_resume = sub.add_parser("resume", parents=[common], help="Resume saved schedule")
    p_resume.add_argument("user", help="Linux username")

    p_hw = sub.add_parser(
        "homework", parents=[common], help="Enable or disable Homework / Study Mode"
    )
    p_hw.add_argument("user", help="Linux username")
    p_hw.add_argument("hw_action", choices=("on", "off"), help="Turn Homework Mode on or off")
    hw_grp = p_hw.add_mutually_exclusive_group()
    hw_grp.add_argument("--for", dest="hw_for", help="Duration (e.g. 1h, 90m)")
    hw_grp.add_argument("--until", dest="hw_until", help="End time (HH:MM or ISO-8601)")
    hw_grp.add_argument("--today", action="store_true", help="Active until local midnight")
    p_hw.add_argument("--reason", default="", help="Optional note (e.g. 'Math assignment')")

    p_hol = sub.add_parser(
        "holiday", parents=[common], help="Manage scheduled holiday/vacation date overrides"
    )
    p_hol.add_argument("user", help="Linux username")
    hol_sub = p_hol.add_subparsers(dest="hol_action", required=True)
    hol_sub.add_parser("list", parents=[common])
    p_hol_add = hol_sub.add_parser("add", parents=[common])
    p_hol_add.add_argument("start_date", help="Start date YYYY-MM-DD")
    p_hol_add.add_argument("end_date", help="End date YYYY-MM-DD")
    p_hol_add.add_argument("--mode", choices=HOLIDAY_MODES, default="weekend")
    p_hol_add.add_argument("--label", default="", help="Friendly label (e.g. 'Winter Break')")
    p_hol_add.add_argument("--limit", dest="hol_limit", default=None)
    p_hol_rm = hol_sub.add_parser("remove", parents=[common])
    p_hol_rm.add_argument("holiday_id", help="Holiday ID to remove")

    p_req = sub.add_parser(
        "requests", parents=[common], help="List or resolve Ask for More Time requests"
    )
    req_sub = p_req.add_subparsers(dest="req_action", required=True)
    p_req_ls = req_sub.add_parser("list", parents=[common])
    p_req_ls.add_argument("--status", default="pending", help="pending | approved | denied | all")
    p_req_ap = req_sub.add_parser("approve", parents=[common])
    p_req_ap.add_argument("request_id", type=int)
    p_req_ap.add_argument("--grant", default=None, help="Override granted duration (e.g. 15m)")
    p_req_ap.add_argument("--note", default="")
    p_req_dn = req_sub.add_parser("deny", parents=[common])
    p_req_dn.add_argument("request_id", type=int)
    p_req_dn.add_argument("--note", default="")

    p_pc = sub.add_parser(
        "parent-code", parents=[common], help="Manage offline 6-digit TOTP Parent Access Code"
    )
    pc_sub = p_pc.add_subparsers(dest="pc_action", required=True)
    pc_sub.add_parser("status", parents=[common])
    pc_sub.add_parser("enable", parents=[common])
    pc_sub.add_parser("rotate", parents=[common])
    pc_sub.add_parser("disable", parents=[common])


def _add_app_limits_parser(sub: Any, common: argparse.ArgumentParser) -> None:
    p_al = sub.add_parser(
        "app-limits", parents=[common], help="Manage per-app limits and shared category budgets"
    )
    p_al.add_argument("user", help="Linux username")
    al_sub = p_al.add_subparsers(dest="al_action", required=True)
    al_sub.add_parser("status", parents=[common])
    al_sub.add_parser("enable", parents=[common])
    al_sub.add_parser("disable", parents=[common])
    p_al_lim = al_sub.add_parser("limit", parents=[common])
    p_al_lim.add_argument("target", help="App pattern (or duration if setting all tracked apps)")
    p_al_lim.add_argument("duration", nargs="?", default=None)
    p_al_add = al_sub.add_parser("add", parents=[common])
    p_al_add.add_argument("pattern", help="Process name or regex pattern")
    p_al_add.add_argument("label", nargs="?", default="", help="Friendly app label")
    p_al_add.add_argument("--limit", dest="app_limit", default="45m")
    p_al_add.add_argument("--weekend", dest="app_weekend", default=None)
    p_al_add.add_argument("--category", default="")
    p_al_add.add_argument("--foreground-only", action="store_true")
    p_al_add.add_argument("--always-allowed", action="store_true")
    p_al_grant = al_sub.add_parser("grant", parents=[common])
    p_al_grant.add_argument("pattern", help="Tracked app pattern")
    p_al_grant.add_argument("delta", help="Adjustment such as +15m or -15m")
    p_al_rm = al_sub.add_parser("remove", parents=[common])
    p_al_rm.add_argument("pattern", help="Pattern to remove")
    p_al_cat = al_sub.add_parser(
        "category", parents=[common], help="Manage shared category budgets"
    )
    cat_sub = p_al_cat.add_subparsers(dest="cat_action", required=True)
    p_cat_set = cat_sub.add_parser("set", parents=[common])
    p_cat_set.add_argument("category", help="Category name (e.g. Gaming, Social)")
    p_cat_set.add_argument("duration", help="Daily cap (e.g. 1h, blocked)")
    p_cat_set.add_argument("--weekend", default=None, help="Optional weekend cap (e.g. 2h)")
    p_cat_rm = cat_sub.add_parser("remove", parents=[common])
    p_cat_rm.add_argument("category", help="Category name to remove")


def build_parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        default=argparse.SUPPRESS,
        help="Emit structured JSON output",
    )

    parser = argparse.ArgumentParser(
        prog="curfewctl",
        description="curfewd screen-time control plane CLI",
        parents=[common],
    )
    parser.add_argument("--version", action="version", version=f"curfewctl {__version__}")
    parser.add_argument(
        "--session-bus", action="store_true", help="Connect to session D-Bus (development/testing)"
    )

    sub = parser.add_subparsers(dest="command", required=True)
    p_status = sub.add_parser("status", parents=[common], help="Show live status")
    p_status.add_argument("user", nargs="?", help="Optional Linux username")
    sub.add_parser("users", parents=[common], help="List tracked and discoverable desktop users")

    _add_schedule_parsers(sub, common)
    _add_family_parsers(sub, common)
    _add_app_limits_parser(sub, common)

    p_lock = sub.add_parser("lock", parents=[common], help="Immediately lock active session(s)")
    p_lock.add_argument("user", help="Linux username")

    p_tlock = sub.add_parser(
        "test-lock", parents=[common], help="Run verified test lock with diagnostics"
    )
    p_tlock.add_argument("user", help="Linux username")

    p_hist = sub.add_parser("history", parents=[common], help="Show daily history and audit log")
    p_hist.add_argument("user", help="Linux username")
    p_hist.add_argument("--days", type=int, default=7)

    p_ins = sub.add_parser("insights", parents=[common], help="Show Top Apps usage insights")
    p_ins.add_argument("user", help="Linux username")
    p_ins.add_argument("--days", type=int, default=7)

    p_cfg = sub.add_parser(
        "config", parents=[common], help="Inspect or update global daemon config"
    )
    cfg_sub = p_cfg.add_subparsers(dest="cfg_action", required=True)
    cfg_sub.add_parser("show", parents=[common])
    p_cfg_set = cfg_sub.add_parser("set", parents=[common])
    p_cfg_set.add_argument("key")
    p_cfg_set.add_argument("value")

    p_doc = sub.add_parser(
        "doctor", parents=[common], help="Verify service, D-Bus, and lock safety"
    )
    p_doc.add_argument("--fix", action="store_true", help="Apply multi-DE lock-screen hardening")

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


@dataclass
class _CLIContext:
    session_bus: bool
    as_json: bool
    out: Any
    err: Any
    client_factory: Callable[[bool], CurfewClient] | None = None
    _client: CurfewClient | None = field(default=None, init=False, repr=False)

    @property
    def client(self) -> CurfewClient:
        if self._client is None:
            if self.client_factory is not None:
                self._client = self.client_factory(self.session_bus)
            elif self.session_bus:
                import dbus

                self._client = CurfewClient(bus=dbus.SessionBus())
            else:
                self._client = CurfewClient()
        return self._client

    def emit(self, payload: Any, human_fn: Callable[[], None]) -> None:
        if self.as_json:
            print_json(payload, self.out)
        else:
            human_fn()


# ---------------- Per-Subcommand Handlers ---------------- #


def _cmd_doctor(args: argparse.Namespace, ctx: _CLIContext) -> int:
    fix_result = None
    if args.fix:
        try:
            fixed = ctx.client.apply_doctor_fix()
            fix_result = fixed.get("fix_applied")
            report = fixed.get("doctor") or run_doctor(dbus_connected=True)
        except DaemonUnavailableError:
            fix_result = apply_all_lock_fixes()
            report = run_doctor(dbus_connected=False)
    else:
        try:
            report = ctx.client.run_doctor()
        except DaemonUnavailableError:
            report = run_doctor(dbus_connected=False)

    if fix_result is not None:
        report["fix_applied"] = fix_result

    def _human() -> None:
        if fix_result is not None:
            ctx.out.write(f"Applied lock-screen hardening: {fix_result['detail']}\n")
        print_doctor(report, ctx.out)

    ctx.emit(report, _human)
    return EXIT_OK if report["overall"] != "error" else EXIT_ERROR


def _cmd_users(_args: argparse.Namespace, ctx: _CLIContext) -> int:
    users = ctx.client.list_users()
    ctx.emit({"users": users}, lambda: print_users_table(users, ctx.out))
    return EXIT_OK


def _cmd_status(args: argparse.Namespace, ctx: _CLIContext) -> int:
    if args.user:
        st = ctx.client.get_user_status(args.user)
        ctx.emit(st, lambda: print_user_status(st, ctx.out))
    else:
        users = ctx.client.list_users()
        ctx.emit({"users": users}, lambda: print_users_table(users, ctx.out))
    return EXIT_OK


def _cmd_grant(args: argparse.Namespace, ctx: _CLIContext) -> int:
    mode, delta_sec = parse_delta(args.delta)
    note = str(getattr(args, "note", "") or "")
    res = (
        ctx.client.grant_time(args.user, delta_sec, mode=mode, note=note)
        if note
        else ctx.client.grant_time(args.user, delta_sec, mode=mode)
    )
    st = res["status"]
    ctx.emit(
        res,
        lambda: ctx.out.write(
            f"Updated {args.user}: applied {format_duration(res['applied_sec'], include_seconds=True)}; "
            f"remaining today: {format_duration(st['time_left_today'], include_seconds=True)}\n"
        ),
    )
    return EXIT_OK


def _cmd_limit(args: argparse.Namespace, ctx: _CLIContext) -> int:
    days = parse_days(args.days)
    sec = parse_duration(args.duration)
    st = ctx.client.get_user_status(args.user)
    policy = dict(st["policy"])
    policy["daily_limits"] = dict(policy["daily_limits"])
    for d in days:
        policy["daily_limits"][d] = sec
    if args.week is not None:
        policy["weekly_limit"] = (
            None if args.week.lower() in ("unlimited", "none", "inf") else parse_duration(args.week)
        )
    if args.month is not None:
        policy["monthly_limit"] = (
            None
            if args.month.lower() in ("unlimited", "none", "inf")
            else parse_duration(args.month)
        )
    updated = ctx.client.set_user_policy(args.user, policy)
    ctx.emit(
        updated,
        lambda: ctx.out.write(
            f"Updated daily limit for {args.user} ({','.join(days)}): {format_duration(sec)}\n"
        ),
    )
    return EXIT_OK


def _cmd_window(args: argparse.Namespace, ctx: _CLIContext) -> int:
    days = parse_days(args.days)
    windows = parse_windows(args.windows)
    st = ctx.client.get_user_status(args.user)
    policy = dict(st["policy"])
    policy["curfew_windows"] = dict(policy["curfew_windows"])
    for d in days:
        policy["curfew_windows"][d] = windows
    updated = ctx.client.set_user_policy(args.user, policy)
    ctx.emit(
        updated,
        lambda: ctx.out.write(
            f"Updated curfew windows for {args.user} ({','.join(days)}): {format_windows_human(windows)}\n"
        ),
    )
    return EXIT_OK


def _cmd_lockout(args: argparse.Namespace, ctx: _CLIContext) -> int:
    st = ctx.client.get_user_status(args.user)
    policy = dict(st["policy"])
    policy["lockout_action"] = args.action
    updated = ctx.client.set_user_policy(args.user, policy)
    ctx.emit(
        updated,
        lambda: ctx.out.write(f"Updated lockout action for {args.user}: {args.action}\n"),
    )
    return EXIT_OK


def _cmd_role(args: argparse.Namespace, ctx: _CLIContext) -> int:
    st = ctx.client.get_user_status(args.user)
    policy = dict(st["policy"])
    policy["role"] = args.role_name
    updated = ctx.client.set_user_policy(args.user, policy)
    ctx.emit(updated, lambda: ctx.out.write(f"Updated role for {args.user}: {args.role_name}\n"))
    return EXIT_OK


def _cmd_pause(args: argparse.Namespace, ctx: _CLIContext) -> int:
    until_utc = ""
    if args.pause_for:
        dur = parse_duration(args.pause_for)
        until_utc = (datetime.now(timezone.utc) + timedelta(seconds=dur)).isoformat()
    elif args.pause_until:
        until_utc = parse_until_timestamp(args.pause_until)
    elif args.today:
        until_utc = parse_until_timestamp("today")
    updated = ctx.client.pause_user(
        args.user,
        until_utc=until_utc,
        reason=args.reason,
        hide_tray=not bool(args.keep_tray),
    )
    mode_str = f"until {until_utc}" if until_utc else "indefinitely"
    ctx.emit(
        updated,
        lambda: ctx.out.write(
            f"Paused enforcement for {args.user} ({mode_str}). Saved schedule preserved.\n"
        ),
    )
    return EXIT_OK


def _cmd_resume(args: argparse.Namespace, ctx: _CLIContext) -> int:
    updated = ctx.client.resume_user(args.user)
    ctx.emit(
        updated,
        lambda: ctx.out.write(
            f"Resumed enforcement for {args.user} (state: {updated['enforcement_state']}).\n"
        ),
    )
    return EXIT_OK


def _cmd_homework(args: argparse.Namespace, ctx: _CLIContext) -> int:
    if args.hw_action == "off":
        updated = ctx.client.set_homework_mode(args.user, {"active": False})
        ctx.emit(updated, lambda: ctx.out.write(f"Disabled Homework Mode for {args.user}.\n"))
        return EXIT_OK
    until_utc = ""
    if args.hw_for:
        dur = parse_duration(args.hw_for)
        until_utc = (datetime.now(timezone.utc) + timedelta(seconds=dur)).isoformat()
    elif args.hw_until:
        until_utc = parse_until_timestamp(args.hw_until)
    elif args.today:
        until_utc = parse_until_timestamp("today")
    payload = {
        "active": True,
        "mode": "until" if until_utc else "indefinite",
        "until_utc": until_utc or None,
        "reason": args.reason,
    }
    updated = ctx.client.set_homework_mode(args.user, payload)
    ctx.emit(
        updated,
        lambda: ctx.out.write(
            f"Enabled Homework Mode for {args.user} ({until_utc or 'until turned off'}).\n"
        ),
    )
    return EXIT_OK


def _cmd_holiday(args: argparse.Namespace, ctx: _CLIContext) -> int:
    st = ctx.client.get_user_status(args.user)
    policy = dict(st["policy"])
    holidays = list(policy.get("holidays") or [])
    if args.hol_action == "list":
        ctx.emit(
            {"username": args.user, "holidays": holidays},
            lambda: ctx.out.write(
                "\n".join(
                    f"  {h['id']}: {h['start_date']}..{h['end_date']} [{h['mode']}] {h.get('label', '')}"
                    for h in holidays
                )
                + ("\n" if holidays else "No holiday overrides configured.\n")
            ),
        )
        return EXIT_OK
    if args.hol_action == "add":
        lim_sec = parse_duration(args.hol_limit) if args.hol_limit else None
        hol = HolidayOverride(
            start_date=args.start_date,
            end_date=args.end_date,
            mode=args.mode,
            label=args.label,
            daily_limit_sec=lim_sec,
        ).to_dict()
        holidays = [h for h in holidays if h.get("id") != hol["id"]] + [hol]
        policy["holidays"] = holidays
        updated = ctx.client.set_user_policy(args.user, policy)
        ctx.emit(
            updated,
            lambda: ctx.out.write(
                f"Added holiday {hol['id']} ({hol['start_date']}..{hol['end_date']}, mode={hol['mode']}).\n"
            ),
        )
        return EXIT_OK
    policy["holidays"] = [h for h in holidays if h.get("id") != args.holiday_id]
    updated = ctx.client.set_user_policy(args.user, policy)
    ctx.emit(updated, lambda: ctx.out.write(f"Removed holiday {args.holiday_id}.\n"))
    return EXIT_OK


def _cmd_requests(args: argparse.Namespace, ctx: _CLIContext) -> int:
    if args.req_action == "list":
        flt = "" if args.status == "all" else args.status
        reqs = ctx.client.list_time_requests(status_filter=flt)
        ctx.emit({"requests": reqs}, lambda: print_requests(reqs, ctx.out))
        return EXIT_OK
    approve = args.req_action == "approve"
    raw_grant = getattr(args, "grant", None)
    grant_sec = 0
    if approve and raw_grant is not None:
        grant_sec = parse_duration(raw_grant)
        if grant_sec <= 0:
            raise ValueError("--grant duration must be positive (e.g. 15m)")
    res = ctx.client.resolve_time_request(
        args.request_id, approve=approve, grant_seconds=grant_sec, note=args.note
    )
    req = res["request"]
    granted_txt = f" (+{format_duration(req['granted_sec'])})" if approve else ""
    ctx.emit(
        res,
        lambda: ctx.out.write(
            f"Request #{req['id']} ({req['username']}) {req['status']}{granted_txt}.\n"
        ),
    )
    return EXIT_OK


def _cmd_parent_code(args: argparse.Namespace, ctx: _CLIContext) -> int:
    if args.pc_action == "status":
        st = ctx.client.get_parent_code_status()
        created_txt = f" (created {st['created_utc']})" if st.get("created_utc") else ""
        ctx.emit(
            st,
            lambda: ctx.out.write(
                f"Parent Access Code: {'ENABLED' if st['enabled'] else 'disabled'}{created_txt}\n"
            ),
        )
        return EXIT_OK
    enable = args.pc_action != "disable"
    rotate = args.pc_action == "rotate"
    res = ctx.client.setup_parent_code(enable=enable, rotate=rotate)

    def _human() -> None:
        if not res["enabled"]:
            ctx.out.write("Parent Access Code disabled.\n")
        elif res.get("already_configured"):
            ctx.out.write(
                "Parent Access Code is already enabled (use 'curfewctl parent-code rotate' to issue a new key).\n"
            )
        else:
            ctx.out.write(
                f"Parent Access Code enabled.\nSetup key: {res['secret_b32']}\nURI: {res['otpauth_uri']}\n"
            )

    ctx.emit(res, _human)
    return EXIT_OK


def _cmd_lock(args: argparse.Namespace, ctx: _CLIContext) -> int:
    res = ctx.client.lock_user(args.user)

    def _human() -> None:
        if res.get("locked"):
            detail = ", ".join(
                f"{s['session_id']} via {s['backend']} ({s['stage']}, {s['elapsed_ms']}ms)"
                for s in res.get("sessions", [])
            )
            ctx.out.write(f"Locked {args.user}: {detail}\n")
        else:
            ctx.out.write(
                f"Could not lock {args.user}: {res.get('detail') or 'lock verification failed'}\n"
            )

    ctx.emit(res, _human)
    return EXIT_OK if res.get("locked") else EXIT_ERROR


def _cmd_test_lock(args: argparse.Namespace, ctx: _CLIContext) -> int:
    res = ctx.client.test_lock(args.user)
    ctx.emit(
        res,
        lambda: ctx.out.write(
            f"Test lock for {args.user}: locked={res.get('locked')} verified={res.get('verified')}\n"
        ),
    )
    return EXIT_OK if res.get("locked") else EXIT_ERROR


def _cmd_history(args: argparse.Namespace, ctx: _CLIContext) -> int:
    hist = ctx.client.get_usage_history(args.user, days=args.days)
    ctx.emit(hist, lambda: print_history(hist, ctx.out))
    return EXIT_OK


def _cmd_insights(args: argparse.Namespace, ctx: _CLIContext) -> int:
    hist = ctx.client.get_usage_history(args.user, days=args.days)
    top_apps = hist.get("top_apps") or []
    ctx.emit(
        {"username": args.user, "days": args.days, "top_apps": top_apps},
        lambda: print_insights(args.user, args.days, top_apps, ctx.out),
    )
    return EXIT_OK


def _cmd_today(args: argparse.Namespace, ctx: _CLIContext) -> int:
    if not args.reset and args.today_limit is None and args.today_window is None:
        raise ValueError("Specify at least one of --limit, --window, or --reset")
    lim_sec = parse_duration(args.today_limit) if args.today_limit is not None else None
    wins = parse_windows(args.today_window) if args.today_window is not None else None
    updated = ctx.client.set_today_override(
        args.user,
        limit_sec=lim_sec,
        windows=wins,
        reset=bool(args.reset),
    )

    def _human() -> None:
        if args.reset:
            ctx.out.write(
                f"Cleared today's override for {args.user}; reverted to weekly schedule.\n"
            )
        else:
            allowance = (
                "unlimited"
                if updated["daily_limit_today"] >= 86400
                else format_duration(updated["daily_limit_today"])
            )
            ctx.out.write(
                f"Updated today's schedule for {args.user}: "
                f"allowance={allowance}, "
                f"windows={format_windows_human(updated.get('today_windows') or [])}\n"
            )

    ctx.emit(updated, _human)
    return EXIT_OK


def _mutate_app_limits(args: argparse.Namespace, al: dict[str, Any]) -> None:
    action = args.al_action
    if action == "enable":
        al["enabled"] = True
    elif action == "disable":
        al["enabled"] = False
    elif action == "limit":
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
    elif action == "add":
        lim_sec = parse_duration(args.app_limit)
        wlim_sec = parse_duration(args.app_weekend) if args.app_weekend is not None else None
        app = AppLimitRule(
            pattern=args.pattern,
            label=args.label or args.pattern,
            daily_limit_sec=lim_sec,
            weekend_limit_sec=wlim_sec,
            category=args.category,
            foreground_only=bool(args.foreground_only),
            always_allowed=bool(args.always_allowed),
        ).to_dict()
        al["apps"] = [a for a in al["apps"] if a["pattern"] != app["pattern"]] + [app]
    elif action == "remove":
        al["apps"] = [a for a in al["apps"] if a["pattern"] != args.pattern]
    elif action == "category":
        cats = list(al.get("category_limits") or [])
        if args.cat_action == "set":
            rule = CategoryLimitRule(
                category=args.category,
                daily_limit_sec=parse_duration(args.duration),
                weekend_limit_sec=parse_duration(args.weekend) if args.weekend else None,
            ).to_dict()
            cats = [c for c in cats if c["category"].lower() != rule["category"].lower()] + [rule]
        elif args.cat_action == "remove":
            cats = [c for c in cats if c["category"].lower() != args.category.lower()]
        al["category_limits"] = cats


def _cmd_app_limits(args: argparse.Namespace, ctx: _CLIContext) -> int:
    if args.al_action == "grant":
        mode, delta_sec = parse_delta(args.delta)
        if mode == "set":
            raise ValueError("app-limits grant supports relative deltas like +15m or -15m")
        res = ctx.client.grant_app_time(args.user, args.pattern, delta_sec)
        ctx.emit(
            res,
            lambda: ctx.out.write(
                f"Updated {args.pattern} for {args.user}: applied "
                f"{format_duration(res['applied_sec'], include_seconds=True)}\n"
            ),
        )
        return EXIT_OK

    st = ctx.client.get_user_status(args.user)
    policy = dict(st["policy"])
    al = dict(policy["app_limits"])
    if args.al_action == "status":
        payload = {
            "username": args.user,
            "app_limits": al,
            "app_limits_status": st["app_limits_status"],
        }

        def _human() -> None:
            cats = st["app_limits_status"].get("categories", al.get("category_limits", []))
            ctx.out.write(
                f"App Limits for {args.user}: enabled={al['enabled']} "
                f"apps={len(al['apps'])} categories={len(cats)}\n"
            )
            for c in cats:
                lim_s = format_duration(c.get("allotted_today", c.get("daily_limit_sec", 0)))
                rem_s = format_duration(c.get("time_left_today", c.get("daily_limit_sec", 0)))
                ctx.out.write(f"  [Category] {c['category']}: {rem_s} left / {lim_s}\n")
            for a in st["app_limits_status"].get("apps", al["apps"]):
                lim_s = format_duration(a.get("allotted_today", a.get("daily_limit_sec", 0)))
                rem_s = format_duration(a.get("time_left_today", a.get("daily_limit_sec", 0)))
                ctx.out.write(f"  - {a['label']} ({a['pattern']}): {rem_s} left / {lim_s}\n")

        ctx.emit(payload, _human)
        return EXIT_OK

    _mutate_app_limits(args, al)
    policy["app_limits"] = al
    updated = ctx.client.set_user_policy(args.user, policy)
    ctx.emit(
        updated,
        lambda: ctx.out.write(
            f"Updated App Limits for {args.user} "
            f"(enabled={updated['policy']['app_limits']['enabled']}).\n"
        ),
    )
    return EXIT_OK


def _cmd_config(args: argparse.Namespace, ctx: _CLIContext) -> int:
    cfg = ctx.client.get_daemon_config()
    if args.cfg_action == "show":

        def _print_cfg() -> None:
            for k, v in sorted(cfg.items()):
                ctx.out.write(f"{k} = {v}\n")

        ctx.emit(cfg, _print_cfg)
        return EXIT_OK
    if args.cfg_action == "set":
        if args.key not in cfg:
            raise ValidationError(f"Unknown daemon config key: {args.key!r}")
        cfg[args.key] = _coerce_config_value(cfg[args.key], args.value)
        updated = ctx.client.set_daemon_config(cfg)
        ctx.emit(
            updated,
            lambda: ctx.out.write(f"Updated daemon config: {args.key} = {updated[args.key]}\n"),
        )
        return EXIT_OK
    return EXIT_USAGE


_COMMAND_HANDLERS: dict[str, Callable[[argparse.Namespace, _CLIContext], int]] = {
    "doctor": _cmd_doctor,
    "users": _cmd_users,
    "status": _cmd_status,
    "grant": _cmd_grant,
    "limit": _cmd_limit,
    "window": _cmd_window,
    "lockout": _cmd_lockout,
    "role": _cmd_role,
    "pause": _cmd_pause,
    "resume": _cmd_resume,
    "homework": _cmd_homework,
    "holiday": _cmd_holiday,
    "requests": _cmd_requests,
    "parent-code": _cmd_parent_code,
    "lock": _cmd_lock,
    "test-lock": _cmd_test_lock,
    "history": _cmd_history,
    "insights": _cmd_insights,
    "today": _cmd_today,
    "app-limits": _cmd_app_limits,
    "config": _cmd_config,
}


def _emit_error(ctx: _CLIContext, code: str, prefix: str, exc: Exception) -> None:
    if ctx.as_json:
        print_json({"error": code, "message": str(exc)}, ctx.err)
    else:
        ctx.err.write(f"{prefix}: {exc}\n")


def run_cli(
    argv: list[str] | None = None,
    client_factory: Callable[[bool], CurfewClient] | None = None,
    stdout: Any = None,
    stderr: Any = None,
) -> int:
    out = stdout or sys.stdout
    err = stderr or sys.stderr
    raw_argv = list(sys.argv[1:] if argv is None else argv)
    parser = build_parser()

    try:
        args = parser.parse_args(_normalize_argv(raw_argv))
    except SystemExit as exc:
        return int(exc.code) if isinstance(exc.code, int) else EXIT_USAGE

    ctx = _CLIContext(
        session_bus=bool(args.session_bus),
        as_json=bool(getattr(args, "json_output", False)),
        out=out,
        err=err,
        client_factory=client_factory,
    )
    handler = _COMMAND_HANDLERS.get(args.command)
    if handler is None:
        return EXIT_USAGE

    try:
        return handler(args, ctx)
    except (ValueError, OverflowError, ValidationError) as exc:
        _emit_error(ctx, "validation_error", "error", exc)
        return EXIT_USAGE
    except (PermissionError, PermissionDeniedError) as exc:
        _emit_error(ctx, "permission_denied", "permission denied", exc)
        return EXIT_PERMISSION_DENIED
    except DaemonUnavailableError as exc:
        _emit_error(ctx, "daemon_unavailable", "daemon unavailable", exc)
        return EXIT_DAEMON_UNAVAILABLE
    except (UserNotFoundError, CurfewError, OSError) as exc:
        _emit_error(ctx, "error", "error", exc)
        return EXIT_ERROR


def main(argv: list[str] | None = None) -> int:
    return run_cli(argv)
