"""Modern curfewctl command-line interface with structured --json output (Spec 005)."""

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
    print_json,
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
from curfewd.engine.doctor import apply_dconf_lock_fix, run_doctor
from curfewd.models import LOCKOUT_ACTIONS, AppLimitRule
from curfewd.parsing import (
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
    if (
        len(positionals) >= 2
        and len(positionals[1]) >= 2
        and positionals[1][0] == "-"
        and positionals[1][1].isdigit()
    ):
        return [*prefix, *flags, positionals[0], "--", *positionals[1:]]
    return argv


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

    p_status = sub.add_parser(
        "status", parents=[common], help="Show live status for all users or one user"
    )
    p_status.add_argument("user", nargs="?", help="Optional Linux username")

    sub.add_parser("users", parents=[common], help="List tracked and discoverable desktop users")

    p_grant = sub.add_parser(
        "grant", parents=[common], help="Add, subtract, or set remaining screen time today"
    )
    p_grant.add_argument("user", help="Linux username")
    p_grant.add_argument("delta", help="Adjustment such as +30m, -15m, =2h")

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

    p_pause = sub.add_parser(
        "pause", parents=[common], help="Pause enforcement without altering saved schedule"
    )
    p_pause.add_argument("user", help="Linux username")
    grp = p_pause.add_mutually_exclusive_group()
    grp.add_argument("--for", dest="pause_for", help="Pause duration (e.g. 2h, 30m)")
    grp.add_argument(
        "--until", dest="pause_until", help="Pause until time (HH:MM, tomorrow, or ISO-8601)"
    )
    grp.add_argument("--today", action="store_true", help="Pause until next local midnight")
    grp.add_argument("--indefinite", action="store_true", help="Pause indefinitely until resumed")
    p_pause.add_argument("--reason", default="", help="Optional audit reason")
    p_pause.add_argument(
        "--keep-tray", action="store_true", help="Keep tray icon visible while paused"
    )

    p_resume = sub.add_parser(
        "resume", parents=[common], help="Resume saved schedule for a paused user"
    )
    p_resume.add_argument("user", help="Linux username")

    p_lock = sub.add_parser(
        "lock", parents=[common], help="Immediately lock a user's active graphical session(s)"
    )
    p_lock.add_argument("user", help="Linux username")

    p_hist = sub.add_parser(
        "history", parents=[common], help="Show daily screen-time history and audit events"
    )
    p_hist.add_argument("user", help="Linux username")
    p_hist.add_argument("--days", type=int, default=7, help="Number of days (1..365, default 7)")

    p_today = sub.add_parser(
        "today",
        parents=[common],
        help="Override today's allowance or allowed window without altering the weekly schedule",
    )
    p_today.add_argument("user", help="Linux username")
    p_today.add_argument(
        "--limit",
        dest="today_limit",
        default=None,
        help="Today-only daily allowance (e.g. 2h, 45m, unlimited)",
    )
    p_today.add_argument(
        "--window",
        dest="today_window",
        default=None,
        help="Today-only allowed window (e.g. '08:00-22:30', 'all', 'none')",
    )
    p_today.add_argument(
        "--reset",
        action="store_true",
        help="Clear today's override and revert to the weekly schedule",
    )

    p_al = sub.add_parser(
        "app-limits", parents=[common], help="Manage individual per-app daily limits"
    )
    p_al.add_argument("user", help="Linux username")
    al_sub = p_al.add_subparsers(dest="al_action", required=True)
    al_sub.add_parser("status", parents=[common])
    al_sub.add_parser("enable", parents=[common])
    al_sub.add_parser("disable", parents=[common])
    p_al_lim = al_sub.add_parser(
        "limit", parents=[common], help="Set daily limit for an app pattern (or all tracked apps)"
    )
    p_al_lim.add_argument("target", help="App pattern (or duration if setting all tracked apps)")
    p_al_lim.add_argument(
        "duration", nargs="?", default=None, help="Daily limit duration (e.g. 45m, 1h)"
    )
    p_al_add = al_sub.add_parser(
        "add", parents=[common], help="Track an app or game with its own daily limit"
    )
    p_al_add.add_argument("pattern", help="Process name or regex pattern")
    p_al_add.add_argument("label", nargs="?", default="", help="Friendly app label")
    p_al_add.add_argument(
        "--limit",
        dest="app_limit",
        default="45m",
        help="Daily time limit for this app (default: 45m)",
    )
    p_al_add.add_argument(
        "--category", default="", help="Optional category (e.g. Gaming, Social, Media)"
    )
    p_al_grant = al_sub.add_parser(
        "grant", parents=[common], help="Add or subtract bonus time today for one app"
    )
    p_al_grant.add_argument("pattern", help="Tracked app pattern")
    p_al_grant.add_argument("delta", help="Adjustment such as +15m or -15m")
    p_al_rm = al_sub.add_parser("remove", parents=[common], help="Remove an app limit rule")
    p_al_rm.add_argument("pattern", help="Pattern to remove")

    p_cfg = sub.add_parser(
        "config", parents=[common], help="Inspect or update global daemon configuration"
    )
    cfg_sub = p_cfg.add_subparsers(dest="cfg_action", required=True)
    cfg_sub.add_parser("show", parents=[common])
    p_cfg_set = cfg_sub.add_parser("set", parents=[common])
    p_cfg_set.add_argument(
        "key", help="DaemonConfig key (e.g. log_level, final_countdown_sec, session_lock_fallback)"
    )
    p_cfg_set.add_argument("value", help="New value")

    p_doc = sub.add_parser(
        "doctor",
        parents=[common],
        help="Verify service, D-Bus, Polkit, dconf lock policy, and helpers",
    )
    p_doc.add_argument(
        "--fix", action="store_true", help="Apply built-in root dconf lock-screen hardening"
    )

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
            fix_result = apply_dconf_lock_fix()
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
            ctx.out.write(f"Applied dconf lock hardening: {fix_result['detail']}\n")
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
    res = ctx.client.grant_time(args.user, delta_sec, mode=mode)
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
            None if args.week.lower() in ("unlimited", "none") else parse_duration(args.week)
        )
    if args.month is not None:
        policy["monthly_limit"] = (
            None if args.month.lower() in ("unlimited", "none") else parse_duration(args.month)
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


def _cmd_history(args: argparse.Namespace, ctx: _CLIContext) -> int:
    hist = ctx.client.get_usage_history(args.user, days=args.days)
    ctx.emit(hist, lambda: print_history(hist, ctx.out))
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
        app = AppLimitRule(
            pattern=args.pattern,
            label=args.label or args.pattern,
            daily_limit_sec=lim_sec,
            category=args.category,
        ).to_dict()
        al["apps"] = [a for a in al["apps"] if a["pattern"] != app["pattern"]] + [app]
    elif action == "remove":
        al["apps"] = [a for a in al["apps"] if a["pattern"] != args.pattern]


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
            ctx.out.write(
                f"App Limits for {args.user}: enabled={al['enabled']} apps={len(al['apps'])}\n"
            )
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
    "pause": _cmd_pause,
    "resume": _cmd_resume,
    "lock": _cmd_lock,
    "history": _cmd_history,
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
    except (ValueError, ValidationError) as exc:
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
