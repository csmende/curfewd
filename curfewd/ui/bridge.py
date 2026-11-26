"""Testable in-process JSON RPC bridge between curfewd-ui WebKit frontend and org.curfewd.Daemon1."""

import getpass
import json
import os
from collections.abc import Callable
from typing import Any

from curfewd import __version__
from curfewd.dbus.client import (
    CurfewClient,
    CurfewError,
    DaemonUnavailableError,
    PermissionDeniedError,
    UserNotFoundError,
    ValidationError,
)
from curfewd.storage import write_json_atomic
from curfewd.ui.appscan import scan_installed_apps

VALID_THEMES = ("auto", "light", "dark")
VALID_ACCENTS = ("indigo", "emerald", "slate", "amber")
VALID_TIME_FORMATS = ("auto", "12h", "24h")


def default_prefs_path() -> str:
    cfg_home = os.environ.get("XDG_CONFIG_HOME") or os.path.join(os.path.expanduser("~"), ".config")
    return os.path.join(cfg_home, "curfewd", "ui.json")


class UIPreferencesStore:
    """Persists per-user UI appearance settings (theme, accent, time_format) in ~/.config/curfewd/ui.json."""

    def __init__(self, path: str | None = None) -> None:
        self.path = path or default_prefs_path()

    def load(self) -> dict[str, str]:
        defaults = {"theme": "auto", "accent": "indigo", "time_format": "auto"}
        if not os.path.isfile(self.path) or os.path.islink(self.path):
            return defaults
        try:
            with open(self.path, encoding="utf-8") as fh:
                raw = json.load(fh)
            if not isinstance(raw, dict):
                return defaults
            theme = str(raw.get("theme", "auto")).lower()
            accent = str(raw.get("accent", "indigo")).lower()
            t_fmt = str(raw.get("time_format", "auto")).lower()
            return {
                "theme": theme if theme in VALID_THEMES else "auto",
                "accent": accent if accent in VALID_ACCENTS else "indigo",
                "time_format": t_fmt if t_fmt in VALID_TIME_FORMATS else "auto",
            }
        except (OSError, ValueError):
            return defaults

    def save(
        self,
        theme: str | None = None,
        accent: str | None = None,
        time_format: str | None = None,
    ) -> dict[str, str]:
        current = self.load()
        if theme is not None:
            t = str(theme).lower()
            if t not in VALID_THEMES:
                raise ValueError(f"Invalid theme {theme!r}")
            current["theme"] = t
        if accent is not None:
            a = str(accent).lower()
            if a not in VALID_ACCENTS:
                raise ValueError(f"Invalid accent {accent!r}")
            current["accent"] = a
        if time_format is not None:
            tf = str(time_format).lower()
            if tf not in VALID_TIME_FORMATS:
                raise ValueError(f"Invalid time_format {time_format!r}")
            current["time_format"] = tf

        write_json_atomic(self.path, current, mode=0o600, dir_mode=0o700)
        return current


class UIBridge:
    """Dispatches structured JSON RPC requests from the WebKitGTK frontend."""

    def __init__(
        self,
        client: CurfewClient,
        prefs: UIPreferencesStore | None = None,
        app_scanner: Callable[[], list[dict[str, Any]]] | None = None,
        current_user: str | None = None,
    ) -> None:
        self._client = client
        self._prefs = prefs or UIPreferencesStore()
        self._app_scanner = app_scanner or scan_installed_apps
        self._current_user = current_user or getpass.getuser()

    def handle(self, action: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        """Execute one frontend RPC command and return `{ok: bool, data|code|message}`."""
        args = params if isinstance(params, dict) else {}
        try:
            handler = getattr(self, f"_cmd_{action}", None)
            if handler is None or not callable(handler):
                return {
                    "ok": False,
                    "code": "unknown_action",
                    "message": f"Unknown UI action: {action}",
                }
            data = handler(args)
            return {"ok": True, "data": data}
        except PermissionDeniedError as exc:
            return {"ok": False, "code": "permission_denied", "message": str(exc)}
        except DaemonUnavailableError as exc:
            return {"ok": False, "code": "daemon_unavailable", "message": str(exc)}
        except (ValidationError, ValueError, TypeError, UserNotFoundError) as exc:
            return {"ok": False, "code": "validation_error", "message": str(exc)}
        except (CurfewError, OSError) as exc:
            return {"ok": False, "code": "error", "message": str(exc)}

    def _safe_optional_call(self, method_name: str, default: Any, *args: Any) -> Any:
        fn = getattr(self._client, method_name, None)
        if not callable(fn):
            return default
        try:
            return fn(*args)
        except CurfewError:
            return default

    def _cmd_bootstrap(self, params: dict[str, Any]) -> dict[str, Any]:
        mode = str(params.get("mode", "admin")).strip().lower()
        prefs = self._prefs.load()

        if mode == "my_status":
            try:
                me = self._client.get_my_status()
                history7 = self._client.get_my_history(days=7)
            except DaemonUnavailableError as exc:
                return {
                    "surface": "unavailable",
                    "error": str(exc),
                    "prefs": prefs,
                    "version": __version__,
                    "current_user": self._current_user,
                }
            my_requests = self._safe_optional_call("list_my_time_requests", [])
            return {
                "surface": "my_status",
                "me": me,
                "history7": history7,
                "my_requests": my_requests,
                "prefs": prefs,
                "version": __version__,
                "current_user": self._current_user,
            }

        try:
            user_summaries = self._client.list_users()
            config = self._client.get_daemon_config()
            doctor = self._client.run_doctor()
        except PermissionDeniedError:
            return {
                "surface": "auth",
                "prefs": prefs,
                "version": __version__,
                "current_user": self._current_user,
            }
        except DaemonUnavailableError as exc:
            return {
                "surface": "unavailable",
                "error": str(exc),
                "prefs": prefs,
                "version": __version__,
                "current_user": self._current_user,
            }

        users: dict[str, dict[str, Any]] = {}
        histories: dict[str, dict[str, Any]] = {}
        for summary in user_summaries:
            uname = summary["username"]
            try:
                users[uname] = self._client.get_user_status(uname)
                histories[uname] = self._client.get_usage_history(uname, days=30)
            except CurfewError:
                continue

        time_requests = self._safe_optional_call("list_time_requests", [], "pending")
        parent_code = self._safe_optional_call(
            "get_parent_code_status", {"enabled": False, "created_utc": ""}
        )
        return {
            "surface": "admin",
            "users": users,
            "histories": histories,
            "config": config,
            "doctor": doctor,
            "time_requests": time_requests,
            "parent_code": parent_code,
            "installed_apps": self._app_scanner(),
            "prefs": prefs,
            "version": __version__,
            "current_user": self._current_user,
        }

    def _cmd_get_user_bundle(self, params: dict[str, Any]) -> dict[str, Any]:
        username = str(params.get("username", "")).strip()
        days = max(1, min(30, int(params.get("days", 30))))
        status = self._client.get_user_status(username)
        history = self._client.get_usage_history(username, days=days)
        return {"status": status, "history": history}

    def _cmd_save_changes(self, params: dict[str, Any]) -> dict[str, Any]:
        updated_users: dict[str, dict[str, Any]] = {}
        policies = params.get("policies") or {}
        if isinstance(policies, dict):
            for username, pol in policies.items():
                if isinstance(pol, dict):
                    updated_users[username] = self._client.set_user_policy(username, pol)

        today_overrides = params.get("today_overrides") or {}
        if isinstance(today_overrides, dict):
            for username, ovr in today_overrides.items():
                if isinstance(ovr, dict):
                    updated_users[username] = self._client.set_today_override(
                        username,
                        limit_sec=ovr.get("limit_sec"),
                        windows=ovr.get("windows"),
                        reset=bool(ovr.get("reset", False)),
                    )

        updated_config = None
        daemon_config = params.get("daemon_config")
        if isinstance(daemon_config, dict):
            updated_config = self._client.set_daemon_config(daemon_config)

        return {
            "users": updated_users,
            "config": updated_config,
        }

    def _cmd_grant_time(self, params: dict[str, Any]) -> dict[str, Any]:
        username = str(params.get("username", "")).strip()
        delta_seconds = int(params.get("delta_seconds", 900))
        mode = str(params.get("mode", "add")).strip()
        note = str(params.get("note", "")).strip()
        res = self._client.grant_time(username, delta_seconds, mode=mode, note=note)
        hist = self._safe_optional_call("get_usage_history", None, username, 30)
        return {**res, "history": hist}

    def _cmd_pause_user(self, params: dict[str, Any]) -> dict[str, Any]:
        username = str(params.get("username", "")).strip()
        until_utc = str(params.get("until_utc", "") or "")
        reason = str(params.get("reason", "") or "")
        hide_tray = bool(params.get("hide_tray", False))
        status = self._client.pause_user(
            username, until_utc=until_utc, reason=reason, hide_tray=hide_tray
        )
        hist = self._safe_optional_call("get_usage_history", None, username, 30)
        return {"status": status, "history": hist}

    def _cmd_resume_user(self, params: dict[str, Any]) -> dict[str, Any]:
        username = str(params.get("username", "")).strip()
        status = self._client.resume_user(username)
        hist = self._safe_optional_call("get_usage_history", None, username, 30)
        return {"status": status, "history": hist}

    def _cmd_reset_today(self, params: dict[str, Any]) -> dict[str, Any]:
        username = str(params.get("username", "")).strip()
        status = self._client.set_today_override(username, reset=True)
        hist = self._safe_optional_call("get_usage_history", None, username, 30)
        return {"status": status, "history": hist}

    def _cmd_set_homework_mode(self, params: dict[str, Any]) -> dict[str, Any]:
        username = str(params.get("username", "")).strip()
        raw_payload = params.get("payload")
        payload: dict[str, Any] = raw_payload if isinstance(raw_payload, dict) else {}
        status = self._client.set_homework_mode(username, payload)
        hist = self._safe_optional_call("get_usage_history", None, username, 30)
        return {"status": status, "history": hist}

    def _cmd_lock_user(self, params: dict[str, Any]) -> dict[str, Any]:
        username = str(params.get("username", "")).strip()
        result = self._client.lock_user(username)
        status = self._client.get_user_status(username)
        return {"result": result, "status": status}

    def _cmd_test_lock(self, params: dict[str, Any]) -> dict[str, Any]:
        username = str(params.get("username", "")).strip()
        return self._client.test_lock(username)

    def _cmd_grant_app_time(self, params: dict[str, Any]) -> dict[str, Any]:
        username = str(params.get("username", "")).strip()
        pattern = str(params.get("pattern", "")).strip()
        delta_seconds = int(params.get("delta_seconds", 900))
        return self._client.grant_app_time(username, pattern, delta_seconds)

    def _cmd_grant_category_time(self, params: dict[str, Any]) -> dict[str, Any]:
        username = str(params.get("username", "")).strip()
        category = str(params.get("category", "")).strip()
        delta_seconds = int(params.get("delta_seconds", 900))
        return self._client.grant_category_time(username, category, delta_seconds)

    def _cmd_list_time_requests(self, params: dict[str, Any]) -> dict[str, Any]:
        status_flt = str(params.get("status", "pending") or "")
        return {"requests": self._client.list_time_requests(status_filter=status_flt)}

    def _cmd_resolve_time_request(self, params: dict[str, Any]) -> dict[str, Any]:
        req_id = int(params.get("request_id", 0))
        approve = bool(params.get("approve", True))
        grant_sec = int(params.get("grant_seconds", 0))
        note = str(params.get("note", "") or "")
        res = self._client.resolve_time_request(req_id, approve, grant_sec, note)
        pending = self._safe_optional_call("list_time_requests", [], "pending")
        return {**res, "pending_requests": pending}

    def _cmd_quick_approve_request(self, params: dict[str, Any]) -> dict[str, Any]:
        req_id = int(params.get("request_id", 0))
        grant_sec = int(params.get("grant_seconds", 0))
        note = str(params.get("note", "") or "")
        return self._client.quick_approve_request(req_id, grant_sec, note)

    def _cmd_request_more_time(self, params: dict[str, Any]) -> dict[str, Any]:
        req = self._client.request_more_time(
            requested_sec=int(params.get("requested_sec", 900)),
            kind=str(params.get("kind", "daily")),
            target=str(params.get("target", "")),
            target_label=str(params.get("target_label", "")),
            reason=str(params.get("reason", "")),
        )
        mine = self._safe_optional_call("list_my_time_requests", [req])
        return {"request": req, "my_requests": mine}

    def _cmd_claim_save_grace(self, _params: dict[str, Any]) -> dict[str, Any]:
        return self._client.claim_save_grace()

    def _cmd_approve_with_parent_code(self, params: dict[str, Any]) -> dict[str, Any]:
        res = self._client.approve_with_parent_code(params)
        mine = self._safe_optional_call("list_my_time_requests", [])
        return {**res, "my_requests": mine}

    def _cmd_get_parent_code_status(self, _params: dict[str, Any]) -> dict[str, Any]:
        return self._client.get_parent_code_status()

    def _cmd_setup_parent_code(self, params: dict[str, Any]) -> dict[str, Any]:
        return self._client.setup_parent_code(
            enable=bool(params.get("enable", True)),
            rotate=bool(params.get("rotate", False)),
        )

    def _cmd_list_user_apps(self, params: dict[str, Any]) -> dict[str, Any]:
        username = str(params.get("username", "")).strip()
        apps = self._safe_optional_call("list_installed_apps", None, username)
        if apps is None:
            apps = self._app_scanner()
        return {"apps": apps}

    def _cmd_complete_setup(self, params: dict[str, Any]) -> dict[str, Any]:
        updated_users: dict[str, dict[str, Any]] = {}
        policies = params.get("policies") or {}
        if isinstance(policies, dict):
            for uname, pol in policies.items():
                if isinstance(pol, dict):
                    updated_users[uname] = self._client.set_user_policy(uname, pol)
        doctor = None
        if params.get("apply_fix"):
            fixed = self._client.apply_doctor_fix()
            doctor = fixed.get("doctor")
        cfg = self._client.get_daemon_config()
        cfg["setup_completed"] = True
        updated_cfg = self._client.set_daemon_config(cfg)
        return {"users": updated_users, "config": updated_cfg, "doctor": doctor}

    def _cmd_apply_doctor_fix(self, _params: dict[str, Any]) -> dict[str, Any]:
        return self._client.apply_doctor_fix()

    def _cmd_refresh_doctor(self, _params: dict[str, Any]) -> dict[str, Any]:
        return self._client.run_doctor()

    def _cmd_save_prefs(self, params: dict[str, Any]) -> dict[str, Any]:
        return self._prefs.save(
            theme=params.get("theme"),
            accent=params.get("accent"),
            time_format=params.get("time_format"),
        )

    def _cmd_scan_apps(self, _params: dict[str, Any]) -> dict[str, Any]:
        return {"apps": self._app_scanner()}
