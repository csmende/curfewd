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

    def grant_app_time(self, username: str, pattern: str, delta_seconds: int):
        self._check(admin=True)
        self.calls.append(("grant_app_time", username, pattern, delta_seconds))
        return {"applied_sec": delta_seconds, "status": self._status(username)}

    def run_doctor(self):
        self._check(admin=True)
        return {
            "version": "1.0.0",
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
            self.assertNotIn("foo", patterns)
            self.assertNotIn("cinnamon-settings", patterns)
            steam = next(a for a in apps if a["pattern"] == "steam")
            self.assertEqual(steam["label"], "Steam")
            sober = next(a for a in apps if a["pattern"] == "sober")
            self.assertEqual(sober["label"], "Roblox (Sober)")
            self.assertEqual(sober["category"], "Gaming")

    def test_audiovideoediting_category_and_full_curated_fallback(self):
        from curfewd.ui.appscan import DEFAULT_CURATED_APPS, _map_category

        self.assertEqual(_map_category("AudioVideoEditing;"), "Creative")
        with tempfile.TemporaryDirectory() as empty_dir:
            apps = scan_installed_apps(search_dirs=[empty_dir], include_curated_fallback=True)
            self.assertEqual(len(apps), len(DEFAULT_CURATED_APPS))
            self.assertTrue(all(not a["installed"] for a in apps))


class TestUIBridgeAndPrefs(unittest.TestCase):
    def test_prefs_store_roundtrip_and_restrictive_mode(self):
        with tempfile.TemporaryDirectory() as tmp:
            prefs_path = os.path.join(tmp, "curfewd", "ui.json")
            store = UIPreferencesStore(path=prefs_path)
            self.assertEqual(store.load(), {"theme": "auto", "accent": "indigo"})
            saved = store.save(theme="dark", accent="emerald")
            self.assertEqual(saved, {"theme": "dark", "accent": "emerald"})
            self.assertEqual(store.load(), {"theme": "dark", "accent": "emerald"})
            mode = stat.S_IMODE(os.stat(prefs_path).st_mode)
            self.assertEqual(mode, 0o600)
            with self.assertRaises(ValueError):
                store.save(theme="neon")

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

        from curfewd.ui.app import _ALLOWED_ASSETS, _MIME_TYPES, resolve_web_dir

        web_dir = resolve_web_dir()
        repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
        with open(os.path.join(web_dir, "index.html"), encoding="utf-8") as fh:
            html = fh.read()
        with open(os.path.join(repo_root, "debian", "install"), encoding="utf-8") as fh:
            deb_install = fh.read()

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
            self.assertIn(
                f"curfewd/ui/web/{rel}",
                deb_install,
                f"UI asset missing from debian/install: {rel}",
            )


if __name__ == "__main__":
    unittest.main()
