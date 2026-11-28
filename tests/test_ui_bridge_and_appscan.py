"""Unit tests for curfewd.ui.appscan, UIPreferencesStore, and UIBridge RPC dispatcher."""

import os
import stat
import tempfile
import unittest

from curfewd.dbus.client import DaemonUnavailableError, PermissionDeniedError
from curfewd.models import TimeWindow, UserPolicy
from curfewd.parsing import WEEKDAYS
from curfewd.ui.appscan import parse_desktop_file, scan_installed_apps
from curfewd.ui.bridge import UIBridge, UIPreferencesStore


class _FakeUIClient:
    def __init__(self, deny_admin: bool = False, unavailable: bool = False) -> None:
        self.deny_admin = deny_admin
        self.unavailable = unavailable
        self.policy = UserPolicy(
            username="maya",
            daily_limits=dict.fromkeys(WEEKDAYS, 3600),
            curfew_windows={d: [TimeWindow("08:00", "21:00")] for d in WEEKDAYS},
        ).to_dict()
        self.config = {
            "session_lock_fallback": True,
            "release_x11_grab_on_lock": True,
            "final_countdown_sec": 15,
        }
        self.today_override = {"active": False, "limit_sec": None, "windows": None}
        self.doctor_fixed = False
        self.calls: list[tuple] = []

    def _check(self, admin: bool = True):
        if self.unavailable:
            raise DaemonUnavailableError("curfewd.service stopped")
        if admin and self.deny_admin:
            raise PermissionDeniedError("Polkit denied")

    def _status(self, username: str = "maya"):
        return {
            "username": username,
            "full_name": "Maya Chen",
            "logged_in": True,
            "active": True,
            "screen_locked": False,
            "enforcement_state": "ok",
            "time_left_today": 2400,
            "daily_limit_today": self.today_override["limit_sec"] or 3600,
            "time_spent_today": 1200,
            "time_spent_week": 9000,
            "bonus_today": 0,
            "today_windows": self.today_override["windows"] or [{"start": "08:00", "end": "21:00"}],
            "today_override": dict(self.today_override),
            "app_limits_status": {"enabled": False, "apps": [], "active_apps": []},
            "policy": self.policy,
        }

    def list_users(self):
        self._check(admin=True)
        return [{"username": "maya"}]

    def get_user_status(self, username: str):
        self._check(admin=True)
        return self._status(username)

    def get_usage_history(self, username: str, days: int = 14):
        self._check(admin=True)
        return {
            "username": username,
            "days": days,
            "daily": [{"date": "2026-11-25", "active_sec": 1200, "app_limits_sec": 600}],
            "events": [],
        }

    def get_daemon_config(self):
        self._check(admin=True)
        return dict(self.config)

    def set_daemon_config(self, cfg):
        self._check(admin=True)
        self.calls.append(("set_daemon_config", cfg))
        self.config = dict(cfg)
        return dict(self.config)

    def set_user_policy(self, username: str, pol):
        self._check(admin=True)
        self.calls.append(("set_user_policy", username))
        self.policy = dict(pol)
        return self._status(username)

    def set_today_override(self, username: str, limit_sec=None, windows=None, reset=False):
        self._check(admin=True)
        self.calls.append(("set_today_override", username, limit_sec, windows, reset))
        if reset:
            self.today_override = {"active": False, "limit_sec": None, "windows": None}
        else:
            self.today_override = {"active": True, "limit_sec": limit_sec, "windows": windows}
        return self._status(username)

    def lock_user(self, username: str):
        self._check(admin=True)
        self.calls.append(("lock_user", username))
        return {"username": username, "locked": True, "verified": True}

    def grant_time(self, username: str, delta_seconds: int, mode: str = "add", note: str = ""):
        self._check(admin=True)
        self.calls.append(("grant_time", username, delta_seconds, mode, note))
        return {"applied_sec": delta_seconds, "status": self._status(username)}

    def pause_user(
        self, username: str, until_utc: str = "", reason: str = "", hide_tray: bool = False
    ):
        self._check(admin=True)
        self.calls.append(("pause_user", username, until_utc, reason, hide_tray))
        return self._status(username)

    def resume_user(self, username: str):
        self._check(admin=True)
        self.calls.append(("resume_user", username))
        return self._status(username)

    def set_homework_mode(self, username: str, payload):
        self._check(admin=True)
        self.calls.append(("set_homework_mode", username, payload))
        return self._status(username)

    def test_lock(self, username: str):
        self._check(admin=True)
        self.calls.append(("test_lock", username))
        return {"username": username, "locked": True, "verified": True, "sessions": []}

    def grant_app_time(self, username: str, pattern: str, delta_seconds: int):
        self._check(admin=True)
        self.calls.append(("grant_app_time", username, pattern, delta_seconds))
        return {"applied_sec": delta_seconds, "status": self._status(username)}

    def grant_category_time(self, username: str, category: str, delta_seconds: int):
        self._check(admin=True)
        self.calls.append(("grant_category_time", username, category, delta_seconds))
        return {"applied_sec": delta_seconds, "status": self._status(username)}

    def list_time_requests(self, status_filter: str = "pending"):
        self._check(admin=True)
        return [
            {
                "id": 7,
                "username": "maya",
                "kind": "daily",
                "requested_sec": 900,
                "status": "pending",
                "reason": "Almost done",
            }
        ]

    def resolve_time_request(
        self, request_id: int, approve: bool, grant_seconds: int = 0, note: str = ""
    ):
        self._check(admin=True)
        self.calls.append(("resolve_time_request", request_id, approve, grant_seconds, note))
        return {
            "request": {
                "id": request_id,
                "username": "maya",
                "status": "approved" if approve else "denied",
                "granted_sec": grant_seconds or 900,
            },
            "status": self._status("maya"),
        }

    def quick_approve_request(self, request_id: int, grant_seconds: int = 0, note: str = ""):
        self._check(admin=True)
        self.calls.append(("quick_approve_request", request_id, grant_seconds, note))
        return {"request": {"id": request_id, "status": "approved"}, "status": self._status("maya")}

    def get_parent_code_status(self):
        self._check(admin=True)
        return {"enabled": True, "created_utc": "2026-11-26T00:00:00+00:00"}

    def setup_parent_code(self, enable: bool = True, rotate: bool = False):
        self._check(admin=True)
        self.calls.append(("setup_parent_code", enable, rotate))
        if not enable:
            return {"enabled": False, "created_utc": ""}
        return {
            "enabled": True,
            "already_configured": False,
            "created_utc": "2026-11-26T00:00:00+00:00",
            "secret_b32": "JBSWY3DPEHPK3PXP",
            "otpauth_uri": "otpauth://totp/curfewd:host?secret=JBSWY3DPEHPK3PXP",
            "qr_svg": "<svg></svg>",
        }

    def list_installed_apps(self, username: str):
        self._check(admin=True)
        return [
            {
                "pattern": "steam_app_413150",
                "label": "Stardew Valley",
                "category": "Gaming",
                "installed": True,
            }
        ]

    def run_doctor(self):
        self._check(admin=True)
        return {
            "version": "2.0.0",
            "overall": "ok" if self.doctor_fixed else "warn",
            "checks": [
                {"name": "systemd_service", "status": "ok", "detail": "active", "fixable": False},
                {
                    "name": "dconf_lock_policy",
                    "status": "ok" if self.doctor_fixed else "warn",
                    "detail": "...",
                    "fixable": not self.doctor_fixed,
                },
            ],
        }

    def apply_doctor_fix(self):
        self._check(admin=True)
        self.doctor_fixed = True
        return {"fix_applied": {"applied": True}, "doctor": self.run_doctor()}

    def get_my_status(self):
        self._check(admin=False)
        return self._status("maya")

    def get_my_history(self, days: int = 7):
        self._check(admin=False)
        return {"username": "maya", "days": days, "daily": [], "events": []}

    def list_my_time_requests(self):
        self._check(admin=False)
        return [{"id": 7, "username": "maya", "status": "pending", "requested_sec": 900}]

    def request_more_time(
        self,
        requested_sec: int,
        kind: str = "daily",
        target: str = "",
        target_label: str = "",
        reason: str = "",
    ):
        self._check(admin=False)
        self.calls.append(("request_more_time", requested_sec, kind, target, reason))
        return {"id": 8, "username": "maya", "requested_sec": requested_sec, "status": "pending"}

    def claim_save_grace(self):
        self._check(admin=False)
        self.calls.append(("claim_save_grace",))
        return {"applied_sec": 120, "status": self._status("maya")}

    def approve_with_parent_code(self, payload):
        self._check(admin=False)
        self.calls.append(("approve_with_parent_code", payload))
        return {"applied_sec": 900, "status": self._status("maya")}


class TestAppScanner(unittest.TestCase):
    def test_scans_native_and_flatpak_desktop_entries_and_filters_noise(self):
        with tempfile.TemporaryDirectory() as tmp:
            # Native game wrapped in `sh -c 'ENV=1 steam %U'` (Ubuntu/Linux Mint style)
            with open(os.path.join(tmp, "steam.desktop"), "w", encoding="utf-8") as fh:
                fh.write(
                    "[Desktop Entry]\nType=Application\nName=Install Steam\nExec=sh -c 'STEAM_FRAME_FORCE_CLOSE=1 steam %U'\nCategories=Network;FileTransfer;Game;\n"
                )
            # Symlinked Flatpak Roblox (Sober), matching /var/lib/flatpak/exports/share/applications layout
            fp_target = os.path.join(tmp, "real_sober.desktop")
            with open(fp_target, "w", encoding="utf-8") as fh:
                fh.write(
                    "[Desktop Entry]\nType=Application\nName=Sober\nExec=/usr/bin/flatpak run --branch=stable org.vinegarhq.Sober\nX-Flatpak=org.vinegarhq.Sober\nCategories=Game;\n"
                )
            os.symlink(fp_target, os.path.join(tmp, "org.vinegarhq.Sober.desktop"))
            # Steam game desktop entry with steam://rungameid/413150
            with open(os.path.join(tmp, "Stardew Valley.desktop"), "w", encoding="utf-8") as fh:
                fh.write(
                    "[Desktop Entry]\nType=Application\nName=Stardew Valley\nExec=steam steam://rungameid/413150\nCategories=Game;\n"
                )
            # Hidden / NoDisplay / Settings utility should be ignored
            with open(os.path.join(tmp, "hidden.desktop"), "w", encoding="utf-8") as fh:
                fh.write(
                    "[Desktop Entry]\nType=Application\nName=Hidden\nExec=/usr/bin/foo\nNoDisplay=true\n"
                )
            with open(os.path.join(tmp, "settings.desktop"), "w", encoding="utf-8") as fh:
                fh.write(
                    "[Desktop Entry]\nType=Application\nName=Display Settings\nExec=cinnamon-settings display\nCategories=Settings;System;\n"
                )
            # Symlink pointing to a non-.desktop file must still be rejected
            secret_file = os.path.join(tmp, "not_a_desktop.txt")
            with open(secret_file, "w", encoding="utf-8") as fh:
                fh.write("[Desktop Entry]\nType=Application\nName=Bad\nExec=/usr/bin/bad\n")
            os.symlink(secret_file, os.path.join(tmp, "badlink.desktop"))
            self.assertIsNone(parse_desktop_file(os.path.join(tmp, "badlink.desktop")))

            apps = scan_installed_apps(search_dirs=[tmp], include_curated_fallback=False)
            patterns = [a["pattern"] for a in apps]
            self.assertIn("steam", patterns)
            self.assertIn("sober", patterns)
            self.assertIn("steam_app_413150", patterns)
            self.assertNotIn("foo", patterns)
            self.assertNotIn("cinnamon-settings", patterns)
            steam = next(a for a in apps if a["pattern"] == "steam")
            self.assertEqual(steam["label"], "Steam")
            sober = next(a for a in apps if a["pattern"] == "sober")
            self.assertEqual(sober["label"], "Roblox (Sober)")
            self.assertEqual(sober["category"], "Gaming")
            self.assertEqual(sober["flatpak_id"], "org.vinegarhq.Sober")
            stardew = next(a for a in apps if a["pattern"] == "steam_app_413150")
            self.assertEqual(stardew["steam_app_id"], "413150")
            self.assertEqual(stardew["label"], "Stardew Valley")

    def test_scan_installed_apps_discovers_target_user_home_entries(self):
        with tempfile.TemporaryDirectory() as fake_home:
            apps_dir = os.path.join(fake_home, ".local", "share", "applications")
            os.makedirs(apps_dir, exist_ok=True)
            with open(os.path.join(apps_dir, "customgame.desktop"), "w", encoding="utf-8") as fh:
                fh.write(
                    "[Desktop Entry]\nType=Application\nName=Child Game\nExec=/usr/bin/childgame\nCategories=Game;\n"
                )
            apps = scan_installed_apps(target_home=fake_home, include_curated_fallback=False)
            self.assertIn("childgame", [a["pattern"] for a in apps])

    def test_audiovideoediting_category_and_full_curated_fallback(self):
        from curfewd.ui.appscan import DEFAULT_CURATED_APPS, _map_category

        self.assertEqual(_map_category("AudioVideoEditing;"), "Creative")
        with tempfile.TemporaryDirectory() as empty_dir:
            apps = scan_installed_apps(search_dirs=[empty_dir], include_curated_fallback=True)
            self.assertEqual(len(apps), len(DEFAULT_CURATED_APPS))
            self.assertTrue(all(not a["installed"] for a in apps))

    def test_scan_steam_games_reads_acf_manifests_filters_proton_and_rejects_symlinks(self):
        from curfewd.ui.appscan import scan_steam_games

        with tempfile.TemporaryDirectory() as fake_home:
            primary_steamapps = os.path.join(fake_home, ".local", "share", "Steam", "steamapps")
            extra_lib = os.path.join(fake_home, "ExtraLibrary")
            extra_steamapps = os.path.join(extra_lib, "steamapps")
            os.makedirs(primary_steamapps, exist_ok=True)
            os.makedirs(extra_steamapps, exist_ok=True)

            with open(
                os.path.join(primary_steamapps, "appmanifest_3072000.acf"),
                "w",
                encoding="utf-8",
            ) as fh:
                fh.write('"AppState"\n{\n\t"appid"\t\t"3072000"\n\t"name"\t\t"Cozy Adventure"\n}\n')
            with open(
                os.path.join(primary_steamapps, "appmanifest_1493710.acf"),
                "w",
                encoding="utf-8",
            ) as fh:
                fh.write(
                    '"AppState"\n{\n\t"appid"\t\t"1493710"\n\t"name"\t\t"Proton Experimental"\n}\n'
                )
            with open(
                os.path.join(primary_steamapps, "libraryfolders.vdf"), "w", encoding="utf-8"
            ) as fh:
                fh.write(
                    f'"libraryfolders"\n{{\n\t"1"\n\t{{\n\t\t"path"\t\t"{extra_lib}"\n\t}}\n}}\n'
                )
            with open(
                os.path.join(extra_steamapps, "appmanifest_413150.acf"), "w", encoding="utf-8"
            ) as fh:
                fh.write('"AppState"\n{\n\t"appid"\t\t"413150"\n\t"name"\t\t"Stardew Valley"\n}\n')

            # Symlinked manifest must be rejected by O_NOFOLLOW
            real_secret = os.path.join(fake_home, "symlink_target.acf")
            with open(real_secret, "w", encoding="utf-8") as fh:
                fh.write('"AppState"\n{\n\t"appid"\t\t"999999"\n\t"name"\t\t"Symlink Game"\n}\n')
            os.symlink(real_secret, os.path.join(primary_steamapps, "appmanifest_999999.acf"))

            games = scan_steam_games(target_home=fake_home)
            by_id = {g["steam_app_id"]: g for g in games}
            self.assertEqual(by_id["3072000"]["label"], "Cozy Adventure")
            self.assertEqual(by_id["413150"]["label"], "Stardew Valley")
            self.assertNotIn("1493710", by_id)
            self.assertNotIn("999999", by_id)


class TestUIBridgeAndPrefs(unittest.TestCase):
    def test_prefs_store_roundtrip_and_restrictive_mode(self):
        with tempfile.TemporaryDirectory() as tmp:
            prefs_path = os.path.join(tmp, "curfewd", "ui.json")
            store = UIPreferencesStore(path=prefs_path)
            self.assertEqual(
                store.load(), {"theme": "auto", "accent": "indigo", "time_format": "auto"}
            )
            saved = store.save(theme="dark", accent="emerald", time_format="24h")
            self.assertEqual(saved, {"theme": "dark", "accent": "emerald", "time_format": "24h"})
            self.assertEqual(
                store.load(), {"theme": "dark", "accent": "emerald", "time_format": "24h"}
            )
            mode = stat.S_IMODE(os.stat(prefs_path).st_mode)
            self.assertEqual(mode, 0o600)
            with self.assertRaises(ValueError):
                store.save(theme="neon")
            with self.assertRaises(ValueError):
                store.save(time_format="13h")

    def test_bridge_admin_bootstrap_save_fix_and_lock(self):
        with tempfile.TemporaryDirectory() as tmp:
            client = _FakeUIClient()
            bridge = UIBridge(
                client=client,
                prefs=UIPreferencesStore(os.path.join(tmp, "ui.json")),
                app_scanner=lambda: [
                    {"pattern": "steam", "label": "Steam", "category": "Gaming", "installed": True}
                ],
                current_user="parent",
            )
            boot = bridge.handle("bootstrap", {"mode": "admin"})
            self.assertTrue(boot["ok"])
            self.assertEqual(boot["data"]["surface"], "admin")
            self.assertIn("maya", boot["data"]["users"])
            self.assertEqual(len(boot["data"]["installed_apps"]), 1)

            saved = bridge.handle(
                "save_changes",
                {
                    "policies": {"maya": client.policy},
                    "today_overrides": {
                        "maya": {"limit_sec": 5400, "windows": [{"start": "08:00", "end": "22:00"}]}
                    },
                    "daemon_config": {
                        "session_lock_fallback": True,
                        "release_x11_grab_on_lock": False,
                        "final_countdown_sec": 20,
                    },
                },
            )
            self.assertTrue(saved["ok"])
            self.assertEqual(saved["data"]["users"]["maya"]["daily_limit_today"], 5400)
            self.assertEqual(saved["data"]["config"]["final_countdown_sec"], 20)

            lock_resp = bridge.handle("lock_user", {"username": "maya"})
            self.assertTrue(lock_resp["ok"])
            self.assertTrue(lock_resp["data"]["result"]["locked"])

            grant_resp = bridge.handle(
                "grant_app_time", {"username": "maya", "pattern": "steam", "delta_seconds": 900}
            )
            self.assertTrue(grant_resp["ok"])
            self.assertEqual(grant_resp["data"]["applied_sec"], 900)

            fix_resp = bridge.handle("apply_doctor_fix", {})
            self.assertTrue(fix_resp["ok"])
            self.assertEqual(fix_resp["data"]["doctor"]["overall"], "ok")

    def test_bridge_v2_family_workflow_and_setup_rpcs(self):
        with tempfile.TemporaryDirectory() as tmp:
            client = _FakeUIClient()
            bridge = UIBridge(
                client=client,
                prefs=UIPreferencesStore(os.path.join(tmp, "ui.json")),
                app_scanner=list,
                current_user="parent",
            )
            boot = bridge.handle("bootstrap", {"mode": "admin"})
            self.assertTrue(boot["ok"])
            self.assertEqual(len(boot["data"]["time_requests"]), 1)
            self.assertTrue(boot["data"]["parent_code"]["enabled"])

            self.assertTrue(
                bridge.handle(
                    "grant_time",
                    {"username": "maya", "delta_seconds": 900, "note": "Good job"},
                )["ok"]
            )
            self.assertTrue(
                bridge.handle(
                    "pause_user", {"username": "maya", "reason": "Movie night", "hide_tray": False}
                )["ok"]
            )
            self.assertTrue(bridge.handle("resume_user", {"username": "maya"})["ok"])
            self.assertTrue(bridge.handle("reset_today", {"username": "maya"})["ok"])
            self.assertTrue(
                bridge.handle(
                    "set_homework_mode",
                    {"username": "maya", "payload": {"active": True, "reason": "Math"}},
                )["ok"]
            )
            self.assertTrue(
                bridge.handle(
                    "grant_category_time",
                    {"username": "maya", "category": "Gaming", "delta_seconds": 600},
                )["ok"]
            )
            self.assertTrue(bridge.handle("test_lock", {"username": "maya"})["ok"])

            req_res = bridge.handle(
                "resolve_time_request",
                {"request_id": 7, "approve": True, "grant_seconds": 900, "note": "Sure"},
            )
            self.assertTrue(req_res["ok"])
            self.assertEqual(req_res["data"]["request"]["status"], "approved")
            self.assertTrue(
                bridge.handle("quick_approve_request", {"request_id": 7, "grant_seconds": 900})[
                    "ok"
                ]
            )

            pc = bridge.handle("setup_parent_code", {"enable": True, "rotate": True})
            self.assertTrue(pc["ok"])
            self.assertIn("<svg>", pc["data"]["qr_svg"])

            apps_resp = bridge.handle("list_user_apps", {"username": "maya"})
            self.assertTrue(apps_resp["ok"])
            self.assertEqual(apps_resp["data"]["apps"][0]["pattern"], "steam_app_413150")

            setup_resp = bridge.handle(
                "complete_setup",
                {"policies": {"maya": client.policy}, "apply_fix": True},
            )
            self.assertTrue(setup_resp["ok"])
            self.assertTrue(setup_resp["data"]["config"]["setup_completed"])

            # Kid self-service RPCs
            self.assertTrue(
                bridge.handle(
                    "request_more_time",
                    {"requested_sec": 900, "kind": "daily", "reason": "Saving game"},
                )["ok"]
            )
            self.assertTrue(bridge.handle("claim_save_grace", {})["ok"])
            self.assertTrue(
                bridge.handle(
                    "approve_with_parent_code",
                    {"code": "123456", "action": "grant_daily", "delta_seconds": 900},
                )["ok"]
            )

    def test_bridge_auth_gate_fallback_and_passwordless_my_status(self):
        with tempfile.TemporaryDirectory() as tmp:
            client = _FakeUIClient(deny_admin=True)
            bridge = UIBridge(
                client=client,
                prefs=UIPreferencesStore(os.path.join(tmp, "ui.json")),
                app_scanner=list,
                current_user="maya",
            )
            # Admin bootstrap falls back cleanly to Authentication Required gate
            boot_admin = bridge.handle("bootstrap", {"mode": "admin"})
            self.assertTrue(boot_admin["ok"])
            self.assertEqual(boot_admin["data"]["surface"], "auth")

            # Passwordless My Screen Time still succeeds without admin privileges
            boot_self = bridge.handle("bootstrap", {"mode": "my_status"})
            self.assertTrue(boot_self["ok"])
            self.assertEqual(boot_self["data"]["surface"], "my_status")
            self.assertEqual(boot_self["data"]["me"]["username"], "maya")

            # Unknown action returns structured error
            bad = bridge.handle("nonexistent_cmd", {})
            self.assertFalse(bad["ok"])
            self.assertEqual(bad["code"], "unknown_action")

            # Daemon unavailable returns unavailable surface cleanly
            down_bridge = UIBridge(
                client=_FakeUIClient(unavailable=True),
                prefs=UIPreferencesStore(os.path.join(tmp, "ui2.json")),
            )
            down = down_bridge.handle("bootstrap", {"mode": "admin"})
            self.assertTrue(down["ok"])
            self.assertEqual(down["data"]["surface"], "unavailable")

    def test_material3_brand_and_tray_svg_icons_valid(self):
        import xml.etree.ElementTree as ET

        from curfewd.models import ENFORCEMENT_STATES
        from curfewd.tray.app import TrayController

        icons_dir = os.path.abspath(
            os.path.join(os.path.dirname(__file__), "..", "resource", "icons")
        )
        all_files = sorted(os.listdir(icons_dir))
        self.assertEqual([f for f in all_files if "timekpr" in f.lower()], [])

        expected = [
            "curfewd.svg",
            "curfewd-tray-ok.svg",
            "curfewd-tray-warning.svg",
            "curfewd-tray-alert.svg",
            "curfewd-tray-unlimited.svg",
        ]
        self.assertEqual(all_files, sorted(expected))

        ns = {"svg": "http://www.w3.org/2000/svg"}
        for name in expected:
            path = os.path.join(icons_dir, name)
            self.assertTrue(os.path.isfile(path), f"Missing icon: {name}")
            root = ET.parse(path).getroot()  # nosec B314 - static local repo assets
            self.assertEqual(root.attrib.get("viewBox"), "0 0 40 40", name)
            rect = root.find("svg:rect", ns)
            self.assertIsNotNone(rect, f"Missing squircle rect in {name}")
            self.assertEqual(rect.attrib.get("rx"), "11", name)
            paths = root.findall("svg:path", ns)
            self.assertEqual(len(paths), 2, f"Expected shield + crescent paths in {name}")
            grad = root.find(".//svg:linearGradient", ns)
            self.assertIsNotNone(grad, f"Missing gradient in {name}")
            grad_id = grad.attrib.get("id")
            self.assertTrue(grad_id, f"Empty gradient id in {name}")
            self.assertEqual(rect.attrib.get("fill"), f"url(#{grad_id})", name)
            circle = root.find("svg:circle", ns)
            if name == "curfewd-tray-ok.svg":
                self.assertIsNotNone(circle, "Expected emerald status dot in curfewd-tray-ok.svg")
                self.assertEqual(circle.attrib.get("fill"), "#5cd69d")
            else:
                self.assertIsNone(circle, f"Unexpected status dot in {name}")

        for state in ENFORCEMENT_STATES:
            icon_name = TrayController.select_icon_name({"enforcement_state": state})
            self.assertIn(f"{icon_name}.svg", expected)
        self.assertEqual(TrayController.select_icon_name(None), "curfewd")

    def test_web_ui_html_assets_match_webkit_allowlist_and_packaging(self):
        import re
        import sys
        from pathlib import Path

        tests_dir = str(Path(__file__).resolve().parent)
        if tests_dir not in sys.path:
            sys.path.insert(0, tests_dir)

        from verify_deb import parse_debian_install

        from curfewd.ui.app import _ALLOWED_ASSETS, _MIME_TYPES, resolve_web_dir

        web_dir = resolve_web_dir()
        repo_root = Path(__file__).resolve().parent.parent
        with open(os.path.join(web_dir, "index.html"), encoding="utf-8") as fh:
            html = fh.read()
        install_map = parse_debian_install(repo_root)

        refs = re.findall(r'(?:src|href)="curfewd://app(/[^"]+)"', html)
        self.assertGreaterEqual(len(refs), 6)
        for uri_path in refs:
            self.assertIn(uri_path, _ALLOWED_ASSETS, f"Not allowlisted in ui/app.py: {uri_path}")
            rel = _ALLOWED_ASSETS[uri_path]
            self.assertIn(rel, _MIME_TYPES, f"Missing MIME type for {rel}")
            self.assertTrue(
                os.path.isfile(os.path.join(web_dir, rel)),
                f"Referenced UI asset missing on disk: {rel}",
            )
            src_rel = f"curfewd/ui/web/{rel}"
            self.assertIn(
                f"usr/share/curfewd/ui/{rel}",
                install_map.get(src_rel, set()),
                f"UI asset missing from debian/install: {rel}",
            )

    def test_systemd_units_autostart_and_postinst_packaging_invariants(self):
        repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
        with open(
            os.path.join(repo_root, "resource/server/systemd/curfewd.service"),
            encoding="utf-8",
        ) as fh:
            daemon_unit = fh.read()
        with open(
            os.path.join(repo_root, "resource/server/systemd/curfewd-tray.service"),
            encoding="utf-8",
        ) as fh:
            tray_unit = fh.read()
        with open(
            os.path.join(repo_root, "resource/launchers/curfewd-tray.desktop"),
            encoding="utf-8",
        ) as fh:
            tray_desktop = fh.read()
        with open(os.path.join(repo_root, "bin/curfewd-tray"), encoding="utf-8") as fh:
            tray_bin = fh.read()
        with open(os.path.join(repo_root, "debian/postinst"), encoding="utf-8") as fh:
            postinst = fh.read()

        # #9: StateDirectory=curfewd must preserve root:curfewd group ownership
        self.assertIn("Group=curfewd", daemon_unit)
        self.assertIn("chown -hR root:curfewd /var/lib/curfewd", postinst)

        # #8: Cross-DE tray supervision via --autostart handoff and non-blocking unit
        self.assertNotIn("PartOf=graphical-session.target", tray_unit)
        self.assertIn("Exec=/usr/bin/curfewd-tray --autostart", tray_desktop)
        self.assertIn('if [ "${1:-}" = "--autostart" ]', tray_bin)
        self.assertIn("curfewd-tray.service", postinst)

        # #10: dh_installsystemd manages curfewd.service lifecycle once (no manual double restart)
        self.assertNotIn("systemctl restart", postinst)
        self.assertNotIn("systemctl enable", postinst)
        self.assertIn("#DEBHELPER#", postinst)


class TestUIHostLifecycleAndDedup(unittest.TestCase):
    def test_rpc_worker_stop_joins_thread_and_closes_bus(self):
        import types
        from unittest import mock

        from curfewd.ui.app import _RPCWorker

        closed = {"bus": False}
        responses: list[tuple[str, dict]] = []

        class _FakeBus:
            def close(self) -> None:
                closed["bus"] = True

        fake_dbus = types.SimpleNamespace(
            SessionBus=lambda private=False: _FakeBus(),
            SystemBus=lambda private=False: _FakeBus(),
        )

        class _FakeBridge:
            def __init__(self, client=None) -> None:
                self.client = client

            def handle(self, action: str, params: dict) -> dict:
                return {"ok": True, "action": action, "params": params}

        with (
            mock.patch.dict("sys.modules", {"dbus": fake_dbus}),
            mock.patch("curfewd.ui.app.CurfewClient", side_effect=lambda **_kw: object()),
            mock.patch("curfewd.ui.app.UIBridge", _FakeBridge),
        ):
            worker = _RPCWorker(
                session_bus=True, on_response=lambda rid, res: responses.append((rid, res))
            )
            worker.submit("req-1", "ping", {"x": 1})
            worker.stop(timeout=2.0)

        self.assertFalse(worker._thread.is_alive())
        self.assertTrue(closed["bus"])
        self.assertEqual(responses, [("req-1", {"ok": True, "action": "ping", "params": {"x": 1}})])

    def _assert_unified_gtk_application(self, gtk_major: int) -> None:
        import signal
        import types

        from curfewd.ui.app import _run_gtk_application

        events: dict[str, object] = {
            "default_ctx_called": False,
            "contexts": [],
            "windows": [],
            "webviews": [],
            "signals": [],
            "backend_starts": 0,
            "backend_cleanups": 0,
        }

        class _FakeWebContext:
            def __init__(self) -> None:
                self.schemes: list[str] = []
                events["contexts"].append(self)  # type: ignore[union-attr]

            @classmethod
            def get_default(cls):
                events["default_ctx_called"] = True
                raise AssertionError("Must not register URI scheme on default WebContext")

            def register_uri_scheme(self, scheme: str, handler) -> None:
                self.schemes.append(scheme)

        class _FakeUCM:
            def __init__(self) -> None:
                self.handlers: list[tuple] = []
                self.connected: list[str] = []

            def register_script_message_handler(self, *args) -> None:
                self.handlers.append(args)

            def connect(self, sig: str, cb) -> None:
                self.connected.append(sig)

        class _FakeSettings:
            def set_enable_developer_extras(self, _v: bool) -> None:
                pass

            def set_javascript_can_open_windows_automatically(self, _v: bool) -> None:
                pass

            def set_allow_file_access_from_file_urls(self, _v: bool) -> None:
                pass

            def set_allow_universal_access_from_file_urls(self, _v: bool) -> None:
                pass

        class _FakeWebView:
            def __init__(self, *, web_context=None, user_content_manager=None) -> None:
                self.web_context = web_context
                self.ucm = user_content_manager
                self.uris: list[str] = []
                self.js_calls: list[str] = []
                self.signals: list[str] = []
                events["webviews"].append(self)  # type: ignore[union-attr]

            def get_settings(self):
                return _FakeSettings()

            def connect(self, sig: str, cb) -> None:
                self.signals.append(sig)

            def load_uri(self, uri: str) -> None:
                self.uris.append(uri)

            def evaluate_javascript(self, code: str, *_args) -> None:
                self.js_calls.append(code)

            def run_javascript(self, code: str, *_args) -> None:
                self.js_calls.append(code)

        class _FakeWindow:
            def __init__(self, application=None) -> None:
                self.application = application
                self.title = ""
                self.size = (0, 0)
                self.icon = ""
                self.child = None
                self.presented = 0
                self.shown_all = 0
                self.destroyed = False
                self.callbacks: dict[str, object] = {}
                events["windows"].append(self)  # type: ignore[union-attr]

            def set_title(self, t: str) -> None:
                self.title = t

            def set_default_size(self, w: int, h: int) -> None:
                self.size = (w, h)

            def set_icon_name(self, name: str) -> None:
                self.icon = name

            def set_child(self, w) -> None:
                self.child = w

            def add(self, w) -> None:
                self.child = w

            def show_all(self) -> None:
                self.shown_all += 1

            def present(self) -> None:
                self.presented += 1

            def connect(self, sig: str, cb) -> None:
                self.callbacks[sig] = cb

            def destroy(self) -> None:
                self.destroyed = True
                cb = self.callbacks.get("destroy")
                if callable(cb):
                    cb(self)

        class _FakeCmdLine:
            def __init__(self, args: list[str]) -> None:
                self._args = args

            def get_arguments(self) -> list[str]:
                return list(self._args)

        class _FakeApplication:
            def __init__(self, application_id: str, flags: int) -> None:
                self.application_id = application_id
                self.flags = flags
                self.handlers: dict[str, object] = {}
                self.quit_called = False

            def connect(self, sig: str, cb) -> None:
                self.handlers[sig] = cb

            def quit(self) -> None:
                self.quit_called = True

            def run(self, argv: list[str]) -> int:
                # Primary command-line launch -> creates single window
                self.handlers["command-line"](self, _FakeCmdLine(argv))  # type: ignore[operator]
                # Second launch with --ask-time -> must reuse & raise existing window!
                self.handlers["command-line"](self, _FakeCmdLine(["curfewd-ui", "--ask-time"]))  # type: ignore[operator]
                return 0

        fake_gtk = types.SimpleNamespace(
            Application=_FakeApplication,
            ApplicationWindow=_FakeWindow,
        )
        fake_webkit = types.SimpleNamespace(
            WebContext=_FakeWebContext,
            UserContentManager=_FakeUCM,
            WebView=_FakeWebView,
            PolicyDecisionType=types.SimpleNamespace(NAVIGATION_ACTION=1, NEW_WINDOW_ACTION=2),
        )
        fake_glib = types.SimpleNamespace(
            PRIORITY_DEFAULT=0,
            unix_signal_add=lambda _prio, sig, cb: events["signals"].append((sig, cb)),  # type: ignore[union-attr]
        )
        fake_gio = types.SimpleNamespace(
            ApplicationFlags=types.SimpleNamespace(HANDLES_COMMAND_LINE=8)
        )

        webview_ref: dict[str, object] = {"view": None, "win": None, "context": None}

        def _start_backend() -> None:
            events["backend_starts"] = int(events["backend_starts"]) + 1  # type: ignore[arg-type]

        def _cleanup_backend() -> None:
            events["backend_cleanups"] = int(events["backend_cleanups"]) + 1  # type: ignore[arg-type]

        rc = _run_gtk_application(
            gtk_major,
            fake_gtk,
            fake_webkit,
            fake_glib,
            fake_gio,
            web_dir="/tmp",
            my_mode=True,
            ask_time=False,
            webview_ref=webview_ref,
            on_rpc_message=lambda _raw: None,
            ensure_backend_started=_start_backend,
            cleanup_backend=_cleanup_backend,
        )

        self.assertEqual(rc, 0)
        self.assertFalse(events["default_ctx_called"])
        # Single private WebContext, single window, single WebView created despite 2 launches
        self.assertEqual(len(events["contexts"]), 1)  # type: ignore[arg-type]
        self.assertEqual(events["contexts"][0].schemes, ["curfewd"])  # type: ignore[index]
        self.assertEqual(len(events["windows"]), 1)  # type: ignore[arg-type]
        self.assertEqual(len(events["webviews"]), 1)  # type: ignore[arg-type]
        win = events["windows"][0]  # type: ignore[index]
        wv = events["webviews"][0]  # type: ignore[index]
        self.assertIs(wv.web_context, events["contexts"][0])  # type: ignore[index]
        self.assertEqual(win.presented, 2)
        self.assertEqual(win.shown_all, 0 if gtk_major == 4 else 1)
        self.assertTrue(any("openAskTimeModal" in js for js in wv.js_calls))
        # SIGINT & SIGTERM wired
        wired_sigs = {sig for sig, _ in events["signals"]}  # type: ignore[misc]
        self.assertEqual(wired_sigs, {signal.SIGINT, signal.SIGTERM})
        # Explicit teardown cleared references and cleaned backend
        self.assertIsNone(webview_ref["view"])
        self.assertIsNone(webview_ref["win"])
        self.assertIsNone(webview_ref["context"])
        self.assertEqual(events["backend_starts"], 1)
        self.assertEqual(events["backend_cleanups"], 1)

    def test_unified_gtk_application_private_webcontext_single_instance_and_signals(self):
        for gtk_major in (3, 4):
            with self.subTest(gtk_major=gtk_major):
                self._assert_unified_gtk_application(gtk_major)

    def test_ui_main_entrypoint_flushes_stdio_and_calls_os_exit(self):
        import runpy
        from unittest import mock

        exit_codes: list[int] = []
        with (
            mock.patch("curfewd.ui.app.main", return_value=0),
            mock.patch("os._exit", side_effect=exit_codes.append),
        ):
            runpy.run_module("curfewd.ui.__main__", run_name="__main__")
        self.assertEqual(exit_codes, [0])


if __name__ == "__main__":
    unittest.main()
