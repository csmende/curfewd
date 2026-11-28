"""Packaging integrity tests for debian/install, AST import audits, and isolated imports."""

from __future__ import annotations

import shutil
import sys
import tempfile
import unittest
from pathlib import Path

TESTS_DIR = Path(__file__).resolve().parent
REPO_ROOT = TESTS_DIR.parent
if str(TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(TESTS_DIR))

from verify_deb import (  # noqa: E402
    audit_internal_imports,
    expected_deb_targets,
    parse_debian_install,
    verify_isolated_imports,
)


def _stage_debian_install(repo_root: Path, dest_root: Path) -> Path:
    """Copy all files declared in debian/install into a simulated rootfs directory."""
    install_map = parse_debian_install(repo_root)
    for src_rel, targets in install_map.items():
        src_path = repo_root / src_rel
        for target_rel in targets:
            out_path = dest_root / target_rel
            out_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src_path, out_path)
    return dest_root / "usr" / "lib" / "python3" / "dist-packages"


class TestDebianPackaging(unittest.TestCase):
    def test_debian_install_covers_all_tracked_python_modules_and_assets(self):
        install_map = parse_debian_install(REPO_ROOT)
        expected = expected_deb_targets(REPO_ROOT)

        self.assertIn("curfewd/tray/osd.py", install_map)
        self.assertIn(
            "usr/lib/python3/dist-packages/curfewd/tray/osd.py",
            install_map["curfewd/tray/osd.py"],
        )

        missing: list[str] = []
        for src_rel, expected_targets in sorted(expected.items()):
            actual_targets = install_map.get(src_rel, set())
            if not actual_targets:
                missing.append(f"{src_rel} (not listed in debian/install)")
                continue
            for target in sorted(expected_targets):
                if target not in actual_targets:
                    missing.append(f"{src_rel} -> expected {target}, got {sorted(actual_targets)}")

        self.assertEqual(missing, [], f"debian/install coverage gaps: {missing}")

    def test_debian_install_uses_directory_globs_for_python_and_js(self):
        raw_lines = [
            line.strip()
            for line in (REPO_ROOT / "debian" / "install").read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.strip().startswith("#")
        ]
        expected_globs = [
            "curfewd/ui/web/js/*.js usr/share/curfewd/ui/js/",
            "curfewd/*.py usr/lib/python3/dist-packages/curfewd/",
            "curfewd/cli/*.py usr/lib/python3/dist-packages/curfewd/cli/",
            "curfewd/dbus/*.py usr/lib/python3/dist-packages/curfewd/dbus/",
            "curfewd/engine/*.py usr/lib/python3/dist-packages/curfewd/engine/",
            "curfewd/tray/*.py usr/lib/python3/dist-packages/curfewd/tray/",
            "curfewd/ui/*.py usr/lib/python3/dist-packages/curfewd/ui/",
        ]
        for entry in expected_globs:
            self.assertIn(entry, raw_lines)

    def test_staged_debian_install_passes_ast_audit_and_isolated_imports(self):
        with tempfile.TemporaryDirectory(prefix="curfewd-stage-test-") as tmpdir:
            dist_root = _stage_debian_install(REPO_ROOT, Path(tmpdir))
            self.assertEqual(audit_internal_imports(dist_root), [])
            verify_isolated_imports(dist_root, REPO_ROOT, python_bin=sys.executable)

    def test_audit_and_isolated_probe_catch_missing_curfewd_tray_osd(self):
        with tempfile.TemporaryDirectory(prefix="curfewd-missing-osd-") as tmpdir:
            dist_root = _stage_debian_install(REPO_ROOT, Path(tmpdir))
            osd_path = dist_root / "curfewd" / "tray" / "osd.py"
            self.assertTrue(osd_path.is_file())
            osd_path.unlink()

            ast_errors = audit_internal_imports(dist_root)
            self.assertTrue(
                any("curfewd.tray.osd" in err for err in ast_errors),
                f"Expected AST audit to flag curfewd.tray.osd, got: {ast_errors}",
            )

            with self.assertRaises(RuntimeError) as ctx:
                verify_isolated_imports(dist_root, REPO_ROOT, python_bin=sys.executable)
            self.assertIn("curfewd.tray.osd", str(ctx.exception))

    def test_postinst_resets_failed_tray_service_before_restart(self):
        postinst = (REPO_ROOT / "debian" / "postinst").read_text(encoding="utf-8")
        reset_idx = postinst.find("reset-failed curfewd-tray.service")
        restart_idx = postinst.find("restart curfewd-tray.service")
        self.assertGreater(reset_idx, 0)
        self.assertGreater(restart_idx, reset_idx)

    def test_postinst_uses_runuser_for_user_bus_and_tray_imports_session_id(self):
        postinst = (REPO_ROOT / "debian" / "postinst").read_text(encoding="utf-8")
        self.assertIn("run_user_systemctl()", postinst)
        self.assertIn('runuser -u "${_target_user}" -- env', postinst)
        self.assertIn('XDG_RUNTIME_DIR="/run/user/${_target_uid}"', postinst)
        self.assertIn('DBUS_SESSION_BUS_ADDRESS="unix:path=/run/user/${_target_uid}/bus"', postinst)
        # Only the runuser-missing fallback inside run_user_systemctl may reference --machine
        self.assertEqual(postinst.count("--machine="), 1)

        tray_bin = (REPO_ROOT / "bin" / "curfewd-tray").read_text(encoding="utf-8")
        self.assertIn("XDG_SESSION_ID", tray_bin)


if __name__ == "__main__":
    unittest.main()
