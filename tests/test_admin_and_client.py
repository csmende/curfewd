"""Unit tests for curfewd CLI boolean formatting, config migration, and client screensaver resilience."""

import os
import sys
import tempfile
import types
import unittest
from unittest import mock

_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if "timekpr" not in sys.modules:
    _pkg = types.ModuleType("timekpr")
    _pkg.__path__ = [_REPO_ROOT]
    _pkg.__package__ = "timekpr"
    sys.modules["timekpr"] = _pkg


class _FakeDBusBoolean(int):
    """Emulates dbus.Boolean whose str() is '1' or '0'."""

    def __new__(cls, val):
        return super().__new__(cls, 1 if val else 0)


class TestAdminBooleanFormatting(unittest.TestCase):
    def test_print_user_config_formats_dbus_bools_and_strings_accurately(self):
        from timekpr.client.admin.adminprocessor import timekprAdminClient

        client = timekprAdminClient.__new__(timekprAdminClient)
        captured = []

        def fake_console_out(*args):
            captured.append(" ".join(str(a) for a in args))

        cfg = {
            "TRACK_INACTIVE": _FakeDBusBoolean(True),
            "HIDE_TRAY_ICON": _FakeDBusBoolean(False),
            "PLAYTIME_ENABLED": "False",
            "PLAYTIME_LIMIT_OVERRIDE_ENABLED": "True",
            "PLAYTIME_UNACCOUNTED_INTERVALS_ENABLED": "1",
        }
        with mock.patch("timekpr.client.admin.adminprocessor.log.consoleOut", side_effect=fake_console_out):
            rc, msg = client.printUserConfig("conrad", cfg)

        self.assertEqual(rc, 0)
        self.assertEqual(msg, "")
        rendered = dict(line.split(": ", 1) for line in captured if ": " in line)
        self.assertEqual(rendered["TRACK_INACTIVE"], "True")
        self.assertEqual(rendered["HIDE_TRAY_ICON"], "False")
        self.assertEqual(rendered["PLAYTIME_ENABLED"], "False")
        self.assertEqual(rendered["PLAYTIME_LIMIT_OVERRIDE_ENABLED"], "True")
        self.assertEqual(rendered["PLAYTIME_UNACCOUNTED_INTERVALS_ENABLED"], "True")


class TestConfigMigration(unittest.TestCase):
    def test_v0511_config_migrates_cleanly_with_new_lock_defaults(self):
        from timekpr.common.utils.config import timekprConfig

        legacy_conf = """[DOCUMENTATION]
#### legacy v0.5.11 config

[GENERAL]
TIMEKPR_LOGLEVEL = 2
TIMEKPR_POLLTIME = 3
TIMEKPR_SAVE_TIME = 30
TIMEKPR_TRACK_INACTIVE = False
TIMEKPR_TERMINATION_TIME = 15
TIMEKPR_FINAL_WARNING_TIME = 10
TIMEKPR_FINAL_NOTIFICATION_TIME = 60

[SESSION]
TIMEKPR_SESSION_TYPES_CTRL = x11;wayland;mir
TIMEKPR_SESSION_TYPES_EXCL = tty;unspecified
TIMEKPR_USERS_EXCL = testtimekpr;lightdm

[DIRECTORIES]
TIMEKPR_CONFIG_DIR = /var/lib/timekpr/config
TIMEKPR_WORK_DIR = /var/lib/timekpr/work
TIMEKPR_SHARED_DIR = /usr/share/timekpr
TIMEKPR_LOGFILE_DIR = /var/log

[MISC]
TIMEKPR_USE_LOCKED_HINT = True
TIMEKPR_SWITCH_TTY_ENABLED = False
TIMEKPR_KILL_LEFTOVER_PROCESSES_ENABLED = False

[PLAYTIME]
TIMEKPR_PLAYTIME_ENABLED = True
TIMEKPR_PLAYTIME_ENHANCED_ACTIVITY_MONITOR_ENABLED = False
"""
        with tempfile.TemporaryDirectory() as tmp:
            cfg_path = os.path.join(tmp, "timekpr.conf")
            with open(cfg_path, "w") as fh:
                fh.write(legacy_conf)

            cfg = timekprConfig()
            cfg._configFile = cfg_path
            cfg.loadMainConfiguration()

            # Existing values preserved
            self.assertEqual(cfg.getTimekprLogLevel(), 2)
            self.assertTrue(cfg.getTimekprPlayTimeEnabled())
            # New curfewd defaults enabled and written back
            self.assertTrue(cfg.getTimekprSessionLockFallbackEnabled())
            self.assertTrue(cfg.getTimekprReleaseX11GrabOnLockEnabled())

            with open(cfg_path, "r") as fh:
                migrated = fh.read()
            self.assertIn("TIMEKPR_SESSION_LOCK_FALLBACK = True", migrated)
            self.assertIn("TIMEKPR_RELEASE_X11_GRAB_ON_LOCK = True", migrated)
            self.assertIn("TIMEKPR_LOGLEVEL = 2", migrated)
            self.assertIn("TIMEKPR_PLAYTIME_ENABLED = True", migrated)


class TestClientNotificationsResilience(unittest.TestCase):
    def _make_notif(self):
        from timekpr.client.interface.dbus import notifications

        obj = notifications.timekprNotifications.__new__(notifications.timekprNotifications)
        obj.CL_CONN_SCR = "screensaver"
        obj.CL_IF = "primary interface"
        obj._scrBusName = "org.cinnamon.ScreenSaver"
        obj._scrObjPath = "/org/cinnamon/ScreenSaver"
        obj._userSessionBus = mock.MagicMock()
        obj._dbusConnections = {obj.CL_CONN_SCR: {obj.CL_IF: mock.MagicMock()}}
        obj.processUserSessionAttributes = mock.MagicMock()
        return obj

    def test_verify_reports_false_without_waking_absent_screensaver(self):
        from timekpr.common.constants import constants as cons

        notif = self._make_notif()
        notif._userSessionBus.name_has_owner.return_value = False

        notif.verifySessionAttributes(cons.TK_CTRL_SCR_N, "KEY123")

        notif._dbusConnections[notif.CL_CONN_SCR][notif.CL_IF].GetActive.assert_not_called()
        notif.processUserSessionAttributes.assert_called_once_with(cons.TK_CTRL_SCR_N, "KEY123", "False")

    def test_verify_reconnects_after_stale_proxy_error(self):
        from timekpr.common.constants import constants as cons

        notif = self._make_notif()
        notif._userSessionBus.name_has_owner.return_value = True
        notif._dbusConnections[notif.CL_CONN_SCR][notif.CL_IF].GetActive.side_effect = RuntimeError("ServiceUnknown")

        fresh_iface = mock.MagicMock()
        fresh_iface.GetActive.return_value = _FakeDBusBoolean(True)

        with mock.patch("timekpr.client.interface.dbus.notifications.dbus.Interface", return_value=fresh_iface):
            notif.verifySessionAttributes(cons.TK_CTRL_SCR_N, "KEY456")

        notif._userSessionBus.get_object.assert_called_once_with(
            "org.cinnamon.ScreenSaver",
            "/org/cinnamon/ScreenSaver",
            follow_name_owner_changes=True,
        )
        notif.processUserSessionAttributes.assert_called_once_with(cons.TK_CTRL_SCR_N, "KEY456", "True")


if __name__ == "__main__":
    unittest.main()
