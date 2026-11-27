"""Executable security hardening, exploit-regression, ISO/IEC 18004 QR decode,
Node.js frontend state, and Hypothesis property-based fuzz tests (Spec 006 hardening)."""

from __future__ import annotations

import contextlib
import json
import os
import shutil
import subprocess
import tempfile
import threading
import time
import unittest
from datetime import datetime, timedelta, timezone
from unittest import mock

from curfewd.auth_code import (
    _QR_SPECS,
    ParentAccessCodeManager,
    _gf_mul,
    _qr_format_bits,
    _qr_version_bits,
    compute_totp,
    encode_qr_matrix,
)
from curfewd.cli.main import _normalize_argv, run_cli
from curfewd.engine import doctor, screenlock
from curfewd.engine.applimits import (
    AppLimitsMonitor,
    read_proc_cgroup_app,
)
from curfewd.engine.evaluator import (
    apply_save_grace,
    apply_today_override,
    evaluate_tick,
)
from curfewd.engine.runtime import EnforcementRuntime
from curfewd.history import HistoryStore
from curfewd.models import (
    AppLimitRule,
    CategoryLimitRule,
    DaemonConfig,
    HolidayOverride,
    HomeworkModeState,
    TimeWindow,
    UserAccountingState,
    UserPolicy,
)
from curfewd.parsing import parse_duration, parse_hhmm_to_minutes
from curfewd.storage import AtomicJsonStore
from curfewd.tray.app import SessionTelemetryProbe, TrayController
from curfewd.ui.appscan import scan_installed_apps
from curfewd.ui.bridge import UIBridge

try:
    from hypothesis import given
    from hypothesis import settings as h_settings
    from hypothesis import strategies as st

    HAS_HYPOTHESIS = True
except ImportError:  # pragma: no cover - optional dev fuzzer
    HAS_HYPOTHESIS = False


# ---------------- Independent ISO/IEC 18004 QR Decoder (V1..V9, EC-L, Byte Mode) ---------------- #

_MASK_FNS = (
    lambda r, c: (r + c) % 2 == 0,
    lambda r, _c: r % 2 == 0,
    lambda _r, c: c % 3 == 0,
    lambda r, c: (r + c) % 3 == 0,
    lambda r, c: ((r // 2) + (c // 3)) % 2 == 0,
    lambda r, c: ((r * c) % 2) + ((r * c) % 3) == 0,
    lambda r, c: (((r * c) % 2) + ((r * c) % 3)) % 2 == 0,
    lambda r, c: (((r + c) % 2) + ((r * c) % 3)) % 2 == 0,
)


def _gf_pow(base: int, exp: int) -> int:
    res = 1
    cur = base
    e = exp
    while e > 0:
        if e & 1:
            res = _gf_mul(res, cur)
        cur = _gf_mul(cur, cur)
        e >>= 1
    return res


def _rs_syndromes(block: list[int], ec_len: int) -> list[int]:
    """Evaluate polynomial block(x) at alpha^0 .. alpha^(ec_len-1) in GF(256) mod 0x11D."""
    syndromes: list[int] = []
    for i in range(ec_len):
        root = _gf_pow(2, i)
        val = 0
        for coeff in block:
            val = _gf_mul(val, root) ^ coeff
        syndromes.append(val)
    return syndromes


def decode_qr_iso18004(matrix: list[list[int]]) -> tuple[int, int, bytes]:
    """Independently decode & verify an ISO/IEC 18004 Byte-mode EC-L QR matrix (V1..V9)."""
    size = len(matrix)
    if size < 21 or (size - 17) % 4 != 0:
        raise AssertionError(f"Invalid QR matrix dimension {size}")
    version = (size - 17) // 4
    spec_by_ver = {row[0]: row for row in _QR_SPECS}
    if version not in spec_by_ver:
        raise AssertionError(f"Unsupported QR version {version}")
    _, expected_size, total_data_cw, ec_len, num_blocks, align = spec_by_ver[version]
    if size != expected_size:
        raise AssertionError(f"Size mismatch for V{version}: {size} != {expected_size}")

    # 1. Verify dark module
    if matrix[size - 8][8] != 1:
        raise AssertionError("Dark module at (4*V+9, 8) is not dark")

    # 2. Verify all 3 finder patterns
    for r0, c0 in ((0, 0), (0, size - 7), (size - 7, 0)):
        for dr in range(7):
            for dc in range(7):
                expected = (
                    1 if (dr in (0, 6) or dc in (0, 6) or (2 <= dr <= 4 and 2 <= dc <= 4)) else 0
                )
                if matrix[r0 + dr][c0 + dc] != expected:
                    raise AssertionError(f"Finder pattern corrupted at ({r0 + dr}, {c0 + dc})")

    # 3. Verify ALL alignment patterns (including V7..V9 centers on row/col 6!)
    align_centers: list[tuple[int, int]] = []
    for ar in align:
        for ac in align:
            if (
                (ar == 6 and ac == 6)
                or (ar == 6 and ac == size - 7)
                or (ar == size - 7 and ac == 6)
            ):
                continue
            align_centers.append((ar, ac))
            for dr in range(-2, 3):
                for dc in range(-2, 3):
                    expected = 1 if max(abs(dr), abs(dc)) != 1 else 0
                    if matrix[ar + dr][ac + dc] != expected:
                        raise AssertionError(
                            f"Alignment pattern V{version} corrupted at ({ar + dr}, {ac + dc})"
                        )

    # 4. Read both copies of 15-bit format information (MSB bit 14 -> LSB bit 0)
    fmt1_coords = (
        [(8, c) for c in range(6)] + [(8, 7), (8, 8), (7, 8)] + [(r, 8) for r in range(5, -1, -1)]
    )
    fmt2_coords = [(size - 1 - i, 8) for i in range(7)] + [(8, size - 8 + i) for i in range(8)]
    fmt1 = 0
    for r, c in fmt1_coords:
        fmt1 = (fmt1 << 1) | matrix[r][c]
    fmt2 = 0
    for r, c in fmt2_coords:
        fmt2 = (fmt2 << 1) | matrix[r][c]
    if fmt1 != fmt2:
        raise AssertionError(f"Format info copies mismatch: {fmt1:#06x} != {fmt2:#06x}")

    unmasked_fmt = fmt1 ^ 0x5412
    # Verify BCH(15, 5) remainder == 0
    bch_rem = unmasked_fmt
    for bit in range(14, 9, -1):
        if bch_rem & (1 << bit):
            bch_rem ^= 0b10100110111 << (bit - 10)
    if bch_rem != 0:
        raise AssertionError(f"Format BCH error: raw={fmt1:#06x}, unmasked={unmasked_fmt:#06x}")

    ec_bits = (unmasked_fmt >> 13) & 0b11
    mask_id = (unmasked_fmt >> 10) & 0b111
    if ec_bits != 0b01:
        raise AssertionError(f"Expected EC-L (0b01), got {ec_bits:#b}")
    if fmt1 != _qr_format_bits(mask_id):
        raise AssertionError("Format bits mismatch reference")

    # 5. Verify Golay(18, 6) version info for V >= 7
    if version >= 7:
        expected_vbits = _qr_version_bits(version)
        v1 = 0
        v2 = 0
        for i in range(18):
            a = size - 11 + (i % 3)
            b = i // 3
            v1 |= matrix[a][b] << i
            v2 |= matrix[b][a] << i
        if v1 != expected_vbits or v2 != expected_vbits:
            raise AssertionError(f"Version info mismatch: {v1:#x}, {v2:#x} != {expected_vbits:#x}")

    # 6. Build exact function-module mask independently
    func = [[False] * size for _ in range(size)]
    for r0, c0 in ((0, 0), (0, size - 7), (size - 7, 0)):
        for dr in range(-1, 8):
            for dc in range(-1, 8):
                r, c = r0 + dr, c0 + dc
                if 0 <= r < size and 0 <= c < size:
                    func[r][c] = True
    for ar, ac in align_centers:
        for dr in range(-2, 3):
            for dc in range(-2, 3):
                func[ar + dr][ac + dc] = True
    for i in range(8, size - 8):
        func[6][i] = True
        func[i][6] = True
    for r, c in fmt1_coords + fmt2_coords:
        func[r][c] = True
    func[size - 8][8] = True
    if version >= 7:
        for i in range(18):
            a = size - 11 + (i % 3)
            b = i // 3
            func[a][b] = True
            func[b][a] = True

    # 7. Extract zigzag bitstream & unmask
    mask_fn = _MASK_FNS[mask_id]
    bits: list[int] = []
    col = size - 1
    upward = True
    while col > 0:
        if col == 6:
            col -= 1
        rows = range(size - 1, -1, -1) if upward else range(size)
        for r in rows:
            for dc in (0, -1):
                c = col + dc
                if not func[r][c]:
                    val = matrix[r][c] ^ (1 if mask_fn(r, c) else 0)
                    bits.append(val)
        col -= 2
        upward = not upward

    total_cw = total_data_cw + ec_len * num_blocks
    codewords = []
    for i in range(total_cw):
        byte = 0
        for b in bits[i * 8 : (i + 1) * 8]:
            byte = (byte << 1) | b
        codewords.append(byte)

    # 8. De-interleave blocks & verify Reed-Solomon syndromes == 0
    block_data_len = total_data_cw // num_blocks
    data_blocks: list[list[int]] = [[] for _ in range(num_blocks)]
    ec_blocks: list[list[int]] = [[] for _ in range(num_blocks)]
    idx = 0
    for _ in range(block_data_len):
        for b in range(num_blocks):
            data_blocks[b].append(codewords[idx])
            idx += 1
    for _ in range(ec_len):
        for b in range(num_blocks):
            ec_blocks[b].append(codewords[idx])
            idx += 1

    for b in range(num_blocks):
        synd = _rs_syndromes(data_blocks[b] + ec_blocks[b], ec_len)
        if any(s != 0 for s in synd):
            raise AssertionError(f"Non-zero RS syndromes in V{version} block {b}: {synd}")

    data_bytes = [cw for blk in data_blocks for cw in blk]
    data_bits = [(b >> shift) & 1 for b in data_bytes for shift in range(7, -1, -1)]
    mode = 0
    for b in data_bits[:4]:
        mode = (mode << 1) | b
    if mode != 0b0100:
        raise AssertionError(f"Expected Byte mode 0b0100, got {mode:#b}")
    length = 0
    for b in data_bits[4:12]:
        length = (length << 1) | b
    payload = bytearray()
    for i in range(length):
        start = 12 + i * 8
        val = 0
        for b in data_bits[start : start + 8]:
            val = (val << 1) | b
        payload.append(val)
    return version, mask_id, bytes(payload)


def _render_qr_pbm(matrix: list[list[int]], scale: int = 4, quiet: int = 4) -> bytes:
    """Render a QR matrix as a crisp binary PGM (P5) image suitable for zbarimg."""
    n = len(matrix)
    dim = (n + 2 * quiet) * scale
    header = f"P5\n{dim} {dim}\n255\n".encode("ascii")
    rows = bytearray()
    white_row = bytes([255]) * dim
    for _ in range(quiet * scale):
        rows.extend(white_row)
    for r in range(n):
        line = bytearray([255] * (quiet * scale))
        for c in range(n):
            px = 0 if matrix[r][c] else 255
            line.extend([px] * scale)
        line.extend([255] * (quiet * scale))
        for _ in range(scale):
            rows.extend(line)
    for _ in range(quiet * scale):
        rows.extend(white_row)
    return header + bytes(rows)


class TestQRISO18004AndParentCodeHardening(unittest.TestCase):
    """Proves P0-1, P0-2, P2-2, and P2-3 fixes with an independent ISO/IEC 18004 decoder + zbarimg."""

    def test_qr_encoder_decodes_cleanly_across_all_versions_1_through_9(self):
        # Payload byte lengths chosen to hit every version from V1 through V9
        sample_lengths = [10, 25, 45, 70, 95, 125, 145, 180, 220]
        seen_versions: set[int] = set()
        for n in sample_lengths:
            payload = (f"otpauth://totp/curfewd:host-{n}?secret=JBSWY3DPEHPK3PXP&p=" + "a" * n)[:n]
            mat = encode_qr_matrix(payload)
            ver, _mask, decoded = decode_qr_iso18004(mat)
            seen_versions.add(ver)
            self.assertEqual(decoded.decode("utf-8"), payload)
        self.assertEqual(seen_versions, set(range(1, 10)))

    @unittest.skipUnless(shutil.which("zbarimg"), "requires zbar-tools (zbarimg)")
    def test_real_zbarimg_scans_all_qr_versions_1_through_9(self):
        sample_lengths = [10, 25, 45, 70, 95, 125, 145, 180, 220]
        with tempfile.TemporaryDirectory() as tmp:
            for idx, n in enumerate(sample_lengths):
                payload = (f"otpauth://totp/curfewd:h{n}?secret=JBSWY3DPEHPK3PXP&x=" + "z" * n)[:n]
                mat = encode_qr_matrix(payload)
                pbm_path = os.path.join(tmp, f"qr_{idx}.pgm")
                with open(pbm_path, "wb") as fh:
                    fh.write(_render_qr_pbm(mat))
                proc = subprocess.run(
                    ["zbarimg", "--raw", "-q", pbm_path],
                    capture_output=True,
                    text=True,
                    timeout=5,
                    check=False,
                )
                self.assertEqual(
                    proc.returncode, 0, msg=f"zbarimg failed on len={n}: {proc.stderr}"
                )
                self.assertEqual(proc.stdout.strip(), payload)

    def test_parent_code_setup_no_silent_reexport_and_uri_bounded(self):
        with tempfile.TemporaryDirectory() as tmp:
            mgr = ParentAccessCodeManager(os.path.join(tmp, "parent_code.json"))
            # Overlong hostname/account stays <= 230 UTF-8 bytes and produces a scannable QR
            long_host = "family-desktop-" + ("é" * 120)
            enrolled = mgr.setup(enable=True, rotate=False, account=long_host)
            self.assertTrue(enrolled["enabled"])
            self.assertTrue(enrolled["secret_b32"])
            self.assertLessEqual(len(enrolled["otpauth_uri"].encode("utf-8")), 230)
            self.assertIn("<svg", enrolled["qr_svg"])
            _, _, decoded = decode_qr_iso18004(encode_qr_matrix(enrolled["otpauth_uri"]))
            self.assertEqual(decoded.decode("utf-8"), enrolled["otpauth_uri"])

            # Record a lockout failure, then call setup(enable=True, rotate=False) -> must NOT re-export or wipe lockout
            ok_bad, _ = mgr.verify_code("000000", now_ts=1_700_000_000.0)
            self.assertFalse(ok_bad)
            recheck = mgr.setup(enable=True, rotate=False, account=long_host)
            self.assertTrue(recheck["already_configured"])
            self.assertNotIn("secret_b32", recheck)
            self.assertNotIn("otpauth_uri", recheck)
            self.assertNotIn("qr_svg", recheck)
            # Secret was preserved (still verifies with original secret)
            step = 1_700_000_060 // 30
            code = compute_totp(enrolled["secret_b32"], step=step)
            ok_good, _ = mgr.verify_code(code, now_ts=1_700_000_060.0)
            self.assertTrue(ok_good)

    def test_approve_with_parent_code_validates_payload_before_consuming_totp_step(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = AtomicJsonStore(
                config_dir=os.path.join(tmp, "etc"),
                state_dir=os.path.join(tmp, "var"),
            )
            rt = EnforcementRuntime(
                store=store,
                history=HistoryStore(os.path.join(tmp, "history.db")),
            )
            rt.set_user_policy(
                "alice",
                UserPolicy(
                    username="alice",
                    daily_limits=dict.fromkeys(
                        ("mon", "tue", "wed", "thu", "fri", "sat", "sun"), 3600
                    ),
                ).to_dict(),
                actor="root",
            )
            enrolled = rt.parent_code.setup(enable=True, account="host")
            step = int(time.time() // 30)
            valid_code = compute_totp(enrolled["secret_b32"], step=step)

            # Unknown action must raise ValueError WITHOUT burning the TOTP step!
            with self.assertRaises(ValueError):
                rt.approve_with_parent_code(
                    "alice", {"code": valid_code, "action": "evil_drop_all"}
                )
            # Non-existent request_id must raise ValueError WITHOUT burning the TOTP step!
            with self.assertRaises(ValueError):
                rt.approve_with_parent_code("alice", {"code": valid_code, "request_id": 999999})

            # Now the SAME single-use code still succeeds for a valid action!
            res = rt.approve_with_parent_code(
                "alice", {"code": valid_code, "action": "grant_daily", "seconds": 900}
            )
            self.assertEqual(res["status"]["bonus_today"], 900)
            # Reusing the now-consumed code MUST fail (anti-replay)
            with self.assertRaises(PermissionError):
                rt.approve_with_parent_code(
                    "alice", {"code": valid_code, "action": "grant_daily", "seconds": 900}
                )


class TestAppScanAndDoctorSecurity(unittest.TestCase):
    """Proves P1-2, P2-1, and P3-6 defenses against symlink escape, FIFO DoS, and destructive writes."""

    def test_appscan_blocks_symlink_escape_fifo_dos_oversize_and_native_flatpak_tag(self):
        with tempfile.TemporaryDirectory() as tmp:
            child_home = os.path.join(tmp, "child_home")
            app_dir = os.path.join(child_home, ".local", "share", "applications")
            secret_dir = os.path.join(tmp, "parent_secret")
            os.makedirs(app_dir)
            os.makedirs(secret_dir)

            # 1. Symlink escaping allowed root into parent_secret
            secret_desktop = os.path.join(secret_dir, "secret.desktop")
            with open(secret_desktop, "w", encoding="utf-8") as fh:
                fh.write("[Desktop Entry]\nName=ParentSecretApp\nExec=/usr/bin/secret\n")
            os.symlink(secret_desktop, os.path.join(app_dir, "escape.desktop"))

            # 2. FIFO trap (would hang open() forever without O_NONBLOCK + S_ISREG check)
            os.mkfifo(os.path.join(app_dir, "hang_trap.desktop"))

            # 3. Oversized file (> 131072 bytes)
            with open(os.path.join(app_dir, "huge.desktop"), "w", encoding="utf-8") as fh:
                fh.write("[Desktop Entry]\nName=HugeApp\nExec=/usr/bin/huge\n#" + ("X" * 140000))

            # 4. Native reverse-DNS desktop file (must NOT get flatpak_id!)
            with open(os.path.join(app_dir, "org.kde.krita.desktop"), "w", encoding="utf-8") as fh:
                fh.write(
                    "[Desktop Entry]\nType=Application\nName=Krita\nExec=/usr/bin/krita\nCategories=Graphics;\n"
                )

            t0 = time.monotonic()
            apps = scan_installed_apps(
                search_dirs=[app_dir], include_curated_fallback=False, target_home=child_home
            )
            elapsed = time.monotonic() - t0
            self.assertLess(elapsed, 2.0, "FIFO trap stalled scan_installed_apps!")
            names = {a["label"] for a in apps}
            self.assertNotIn("ParentSecretApp", names)
            self.assertNotIn("HugeApp", names)
            self.assertIn("Krita", names)
            krita = next(a for a in apps if a["label"] == "Krita")
            self.assertEqual(krita["flatpak_id"], "")

    def test_doctor_preserves_existing_kde_xfce_config_and_rejects_symlinks(self):
        with tempfile.TemporaryDirectory() as tmp:
            kde_path = tmp + doctor.KDE_LOCKSCREEN_PATH
            os.makedirs(os.path.dirname(kde_path), exist_ok=True)
            with open(kde_path, "w", encoding="utf-8") as fh:
                fh.write("[Greeter]\nTheme=breeze-dark\n\n[Daemon]\nTimeout=300\n")
            res = doctor.apply_kde_lock_fix(root_prefix=tmp)
            self.assertTrue(res["applied"])
            with open(kde_path, encoding="utf-8") as fh:
                content = fh.read()
            self.assertIn("Theme=breeze-dark", content)
            self.assertIn("Timeout=300", content)
            self.assertIn("[Daemon][$i]", content)
            self.assertIn("Autolock=true", content)
            self.assertIn("LockOnResume=true", content)

            # Symlink rejection (O_NOFOLLOW)
            sym_root = os.path.join(tmp, "sym_root")
            sym_kde = sym_root + doctor.KDE_LOCKSCREEN_PATH
            os.makedirs(os.path.dirname(sym_kde), exist_ok=True)
            target = os.path.join(tmp, "sensitive_target")
            with open(target, "w", encoding="utf-8") as fh:
                fh.write("DO_NOT_TOUCH\n")
            os.symlink(target, sym_kde)
            with self.assertRaises(OSError):
                doctor.apply_kde_lock_fix(root_prefix=sym_root)
            with open(target, encoding="utf-8") as fh:
                self.assertEqual(fh.read(), "DO_NOT_TOUCH\n")

            # XFCE non-destructive XML merge
            xfce_path = tmp + doctor.XFCE_SCREENSAVER_PATH
            os.makedirs(os.path.dirname(xfce_path), exist_ok=True)
            with open(xfce_path, "w", encoding="utf-8") as fh:
                fh.write(
                    '<?xml version="1.0" encoding="UTF-8"?>\n'
                    '<channel name="xfce4-screensaver" version="1.0">\n'
                    '  <property name="saver" type="empty">\n'
                    '    <property name="themes" type="string" value="blank-only"/>\n'
                    "  </property>\n"
                    "</channel>\n"
                )
            res_xfce = doctor.apply_xfce_lock_fix(root_prefix=tmp)
            self.assertTrue(res_xfce["applied"])
            with open(xfce_path, encoding="utf-8") as fh:
                xfce_out = fh.read()
            self.assertIn('name="themes"', xfce_out)
            self.assertIn('name="lock"', xfce_out)
            self.assertIn('locked="*"', xfce_out)


class TestTelemetryTTLAndEnforcementHardening(unittest.TestCase):
    """Proves P0-4 (telemetry TTL, logout eviction, lockout immunity) & P1-1 (async Polkit)."""

    def test_spoofed_telemetry_expires_fail_closed_and_never_suppresses_lockout(self):
        with tempfile.TemporaryDirectory() as tmp:
            locked_calls: list[str] = []

            class _FakeSessionMon:
                def __init__(self) -> None:
                    self.logged_in = True

                def list_user_sessions(self, _tracked_types=None):
                    if not self.logged_in:
                        return {}
                    return {
                        "alice": [
                            {
                                "username": "alice",
                                "uid": 1000,
                                "active": True,
                                "locked": False,
                                "idle": False,
                                "session_type": "x11",
                                "session_id": "1",
                                "display": ":0",
                                "desktop": "X-Cinnamon",
                                "leader_pid": 1234,
                            }
                        ]
                    }

                def summarize_sessions(self, sessions):
                    return list(sessions)

                def enforce_lockout_action(self, action, username, _uid, _sessions, _config, **_kw):
                    locked_calls.append(f"{username}:{action}")

            now_utc = datetime(2026, 4, 6, 15, 0, 0, tzinfo=timezone.utc)  # Mon 15:00
            pol = UserPolicy(
                username="alice",
                daily_limits=dict.fromkeys(("mon", "tue", "wed", "thu", "fri", "sat", "sun"), 600),
                curfew_windows={
                    d: [TimeWindow("00:00", "24:00")]
                    for d in ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
                },
            )
            sess_mon = _FakeSessionMon()
            store = AtomicJsonStore(
                config_dir=os.path.join(tmp, "etc"),
                state_dir=os.path.join(tmp, "var"),
            )
            rt = EnforcementRuntime(
                store=store,
                history=HistoryStore(os.path.join(tmp, "history.db")),
                session_monitor=sess_mon,
                now_fn=lambda: now_utc,
            )
            rt.set_user_policy("alice", pol.to_dict(), actor="root")
            # Drain Alice's 600s completely so enforcement triggers lockout!
            st = rt.get_state("alice")
            st.date = "2026-04-06"
            st.spent_balance = 600
            st.last_checked_utc = (now_utc - timedelta(seconds=5)).isoformat()

            # Child spoofs screensaver_active=True & idle_sec=999999 & root PID 1!
            rt._pending_nonces["alice"] = "nonce-spoof-1"
            self.assertTrue(
                rt.verify_client_telemetry(
                    "alice",
                    "nonce-spoof-1",
                    {
                        "screensaver_active": True,
                        "idle_sec": 999999,
                        "active_window_pid": 1,
                    },
                )
            )
            # PID 1 is owned by root, NOT alice -> rejected to 0
            self.assertEqual(rt._tray_active_pid.get("alice"), 0)

            # Even while tray telemetry claims screensaver_active=True, logind says screen is UNLOCKED ->
            # _enforce_session_lockout MUST still lock!
            rt.config.final_countdown_sec = 0
            rt.tick()
            self.assertTrue(any("alice:lock" in c for c in locked_calls))

            # Verify TTL expiration (> 30s monotonic) fails closed to False / 0 / 0
            rt._tray_telemetry_mono["alice"] = time.monotonic() - 120.0
            s_active, idle_s, fg_pid = rt._fresh_telemetry("alice")
            self.assertFalse(s_active)
            self.assertEqual(idle_s, 0)
            self.assertEqual(fg_pid, 0)

            # Verify logout clears runtime caches
            rt._pending_nonces["alice"] = "nonce-spoof-2"
            rt.verify_client_telemetry(
                "alice",
                "nonce-spoof-2",
                {"screensaver_active": True, "idle_sec": 500, "active_window_pid": 0},
            )
            sess_mon.logged_in = False
            rt.tick()
            self.assertNotIn("alice", rt._tray_telemetry_mono)
            self.assertNotIn("alice", rt._tray_screensaver_active)

    @unittest.skipUnless(shutil.which("dbus-daemon"), "requires dbus-daemon")
    def test_async_polkit_does_not_block_glib_loop_and_malformed_telemetry_returns_validation_error(
        self,
    ):
        import getpass

        import dbus
        from dbus.mainloop.glib import DBusGMainLoop
        from gi.repository import GLib

        from curfewd import (
            DBUS_ADMIN_INTERFACE,
            DBUS_BUS_NAME,
            DBUS_CLIENT_INTERFACE,
            DBUS_OBJECT_PATH,
        )
        from curfewd.dbus.polkit import PolkitAuthorizer
        from curfewd.dbus.service import CurfewDaemonService

        # Run entirely on the main GLib thread with async D-Bus calls to avoid
        # libdbus/dbus-glib cross-thread BusConnection open/close race conditions.
        if dbus.get_default_main_loop() is None:
            DBusGMainLoop(set_as_default=True)
        with tempfile.TemporaryDirectory() as tmp:
            sock_path = os.path.join(tmp, "bus")
            bus_addr = f"unix:path={sock_path}"
            daemon = subprocess.Popen(
                ["dbus-daemon", "--session", f"--address={bus_addr}", "--nofork", "--nopidfile"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            svc_bus = None
            cli1_bus = None
            cli2_bus = None
            service = None
            hist = None
            try:
                for _ in range(50):
                    if os.path.exists(sock_path):
                        break
                    time.sleep(0.05)

                svc_bus = dbus.bus.BusConnection(bus_addr)
                cli1_bus = dbus.bus.BusConnection(bus_addr)
                cli2_bus = dbus.bus.BusConnection(bus_addr)
                store = AtomicJsonStore(
                    config_dir=os.path.join(tmp, "etc"),
                    state_dir=os.path.join(tmp, "var"),
                )
                hist = HistoryStore(os.path.join(tmp, "h.db"))
                cur_user = getpass.getuser()

                # Custom authorizer whose authorize_async simulates a 0.6s interactive Polkit dialog
                class _SlowAsyncAuthorizer(PolkitAuthorizer):
                    def authorize_async(self, sender, action_id, on_ok, on_err):
                        GLib.timeout_add(600, lambda: (on_ok(os.getuid(), cur_user), False)[1])

                auth = _SlowAsyncAuthorizer(bus=svc_bus)
                service = CurfewDaemonService(svc_bus, store, hist, authorizer=auth)
                service._runtime.set_user_policy(
                    cur_user, UserPolicy(username=cur_user).to_dict(), actor="root"
                )
                service._runtime._pending_nonces[cur_user] = "good-nonce-123"

                loop = GLib.MainLoop()
                admin_res: list[dict] = []
                status_res: list[dict] = []
                status_elapsed: list[float] = []
                status_before_admin: list[bool] = []
                telem_errors: list[str] = []
                unexpected_errors: list[str] = []

                def _maybe_quit() -> None:
                    if admin_res and status_res and telem_errors:
                        loop.quit()

                def _on_admin_ok(raw_json: str) -> None:
                    admin_res.append(json.loads(str(raw_json)))
                    _maybe_quit()

                def _on_admin_err(err: Exception) -> None:
                    unexpected_errors.append(f"admin: {err}")
                    loop.quit()

                def _fire_concurrent_user_calls() -> bool:
                    t0 = time.monotonic()

                    def _on_status_ok(raw_json: str) -> None:
                        status_elapsed.append(time.monotonic() - t0)
                        status_before_admin.append(not admin_res)
                        status_res.append(json.loads(str(raw_json)))
                        _maybe_quit()

                    def _on_status_err(err: Exception) -> None:
                        unexpected_errors.append(f"status: {err}")
                        loop.quit()

                    def _on_telem_ok(val: bool) -> None:
                        unexpected_errors.append(f"telemetry unexpectedly succeeded: {val}")
                        loop.quit()

                    def _on_telem_err(err: Exception) -> None:
                        telem_errors.append(
                            err.get_dbus_name() if hasattr(err, "get_dbus_name") else str(err)
                        )
                        _maybe_quit()

                    # While Admin Polkit dialog is suspended (0.6s), unprivileged GetMyStatus
                    # must reply immediately and malformed telemetry must return ValidationError.
                    cli2_bus.call_async(
                        DBUS_BUS_NAME,
                        DBUS_OBJECT_PATH,
                        DBUS_CLIENT_INTERFACE,
                        "GetMyStatus",
                        "",
                        (),
                        reply_handler=_on_status_ok,
                        error_handler=_on_status_err,
                    )
                    cli2_bus.call_async(
                        DBUS_BUS_NAME,
                        DBUS_OBJECT_PATH,
                        DBUS_CLIENT_INTERFACE,
                        "ReportClientTelemetry",
                        "ss",
                        ("good-nonce-123", json.dumps({"idle_sec": "not-a-number"})),
                        reply_handler=_on_telem_ok,
                        error_handler=_on_telem_err,
                    )
                    return False

                def _start_calls() -> bool:
                    cli1_bus.call_async(
                        DBUS_BUS_NAME,
                        DBUS_OBJECT_PATH,
                        DBUS_ADMIN_INTERFACE,
                        "GetDaemonConfig",
                        "",
                        (),
                        reply_handler=_on_admin_ok,
                        error_handler=_on_admin_err,
                    )
                    GLib.timeout_add(60, _fire_concurrent_user_calls)
                    return False

                timed_out = False

                def _safety_timeout() -> bool:
                    nonlocal timed_out
                    timed_out = True
                    unexpected_errors.append("timed out waiting for async D-Bus replies")
                    loop.quit()
                    return False

                GLib.idle_add(_start_calls)
                timeout_id = GLib.timeout_add(4000, _safety_timeout)
                loop.run()
                if not timed_out:
                    GLib.source_remove(timeout_id)

                self.assertEqual(unexpected_errors, [])
                self.assertEqual(len(admin_res), 1)
                self.assertEqual(len(status_res), 1)
                self.assertEqual(status_res[0]["username"], cur_user)
                self.assertTrue(
                    status_before_admin and status_before_admin[0],
                    "GetMyStatus did not complete before slow async Polkit finished!",
                )
                self.assertLess(
                    status_elapsed[0],
                    0.45,
                    f"GetMyStatus stalled {status_elapsed[0]:.2f}s behind slow Polkit!",
                )
                self.assertEqual(telem_errors, ["org.curfewd.Daemon1.Error.ValidationError"])
                # Malformed telemetry payload must NOT burn the pending nonce
                self.assertIn(cur_user, service._runtime._pending_nonces)
            finally:
                if service is not None:
                    with contextlib.suppress(Exception):
                        service.remove_from_connection()
                    service._bus_name = None
                    service._name = None
                    service.shutdown()
                if hist is not None:
                    hist.close()
                for b in (cli2_bus, cli1_bus, svc_bus):
                    if b is not None:
                        b.close()
                daemon.terminate()
                daemon.wait(timeout=5)


class TestEvaluatorAndAppLimitsHardening(unittest.TestCase):
    """Proves P1-4, P1-5, P1-6, P1-7, P1-8, P1-9, P2-5, P2-6, P2-8, P2-9, P3-1, P3-3."""

    def test_cmos_forward_jump_and_tick2_do_not_brick_rollover(self):
        cfg = DaemonConfig()
        pol = UserPolicy(
            username="alice",
            daily_limits=dict.fromkeys(("mon", "tue", "wed", "thu", "fri", "sat", "sun"), 3600),
        )
        t0 = datetime(2026, 4, 6, 12, 0, 0, tzinfo=timezone.utc)
        st = UserAccountingState(
            username="alice", date="2026-04-06", spent_balance=600, spent_today=600
        )
        st.last_checked_utc = t0.isoformat()

        # Tick #1: CMOS jumps forward 4 years to 2030!
        jumped = datetime(2030, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
        evaluate_tick(pol, st, cfg, jumped, elapsed_sec=5, is_active=True, screen_locked=False)
        self.assertEqual(st.date, "2026-04-06")
        self.assertEqual(st.last_checked_utc, t0.isoformat())

        # Tick #2: Still in 2030 (5s later) — MUST NOT roll state.date to 2030!
        evaluate_tick(
            pol,
            st,
            cfg,
            jumped + timedelta(seconds=5),
            elapsed_sec=5,
            is_active=True,
            screen_locked=False,
        )
        self.assertEqual(st.date, "2026-04-06")
        self.assertEqual(st.last_checked_utc, t0.isoformat())
        self.assertEqual(st.spent_today, 610)

        # NTP recovers to next day (2026-04-07): normal midnight rollover works!
        next_day = datetime(2026, 4, 7, 9, 0, 0, tzinfo=timezone.utc)
        evaluate_tick(pol, st, cfg, next_day, elapsed_sec=5, is_active=True, screen_locked=False)
        self.assertEqual(st.date, "2026-04-07")
        self.assertEqual(st.spent_today, 5)

    def test_homework_mode_enforces_bedtime_curfew_and_protects_always_allowed(self):
        cfg = DaemonConfig()
        pol = UserPolicy(
            username="alice",
            daily_limits=dict.fromkeys(("mon", "tue", "wed", "thu", "fri", "sat", "sun"), 3600),
            curfew_windows={
                d: [TimeWindow("08:00", "21:00")]
                for d in ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
            },
            homework_mode=HomeworkModeState(active=True, mode="indefinite", allowed_apps=["code"]),
        )
        pol.app_limits.enabled = (
            False  # Homework Mode must still block games even when app_limits disabled!
        )
        pol.app_limits.apps = [
            AppLimitRule(
                "firefox", "Firefox", daily_limit_sec=0, category="Gaming", always_allowed=True
            ),
            AppLimitRule("steam", "Steam", daily_limit_sec=3600, category="Gaming"),
        ]
        st = UserAccountingState(
            username="alice", date="2026-04-06", spent_balance=600, spent_today=600
        )
        inside_curfew = datetime(2026, 4, 6, 15, 0, 0, tzinfo=timezone.utc)
        st.last_checked_utc = (inside_curfew - timedelta(seconds=30)).isoformat()

        # Inside allowed hours: daily timer paused (spent_today stays 600), no kill if no blocked apps running
        res_ok = evaluate_tick(
            pol,
            st,
            cfg,
            inside_curfew,
            elapsed_sec=5,
            is_active=True,
            screen_locked=False,
            running_app_patterns={"firefox"},
        )
        self.assertEqual(res_ok.enforcement_state, "homework")
        self.assertFalse(res_ok.should_enforce)
        self.assertFalse(res_ok.app_limits_should_kill)
        self.assertNotIn("firefox", res_ok.expired_app_patterns)
        self.assertEqual(st.spent_today, 600)

        # Past bedtime (22:00): Homework Mode MUST STILL ENFORCE CURFEW!
        bedtime = datetime(2026, 4, 6, 22, 0, 0, tzinfo=timezone.utc)
        res_bed = evaluate_tick(
            pol, st, cfg, bedtime, elapsed_sec=5, is_active=True, screen_locked=False
        )
        self.assertEqual(res_bed.enforcement_state, "locked_out")
        self.assertTrue(res_bed.should_enforce)

    def test_save_grace_eligibility_midnight_persistence_and_zero_debt(self):
        cfg = DaemonConfig()
        pol = UserPolicy(
            username="alice",
            daily_limits=dict.fromkeys(("mon", "tue", "wed", "thu", "fri", "sat", "sun"), 3600),
            curfew_windows={
                d: [TimeWindow("08:00", "21:00")]
                for d in ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
            },
        )
        st = UserAccountingState(
            username="alice", date="2026-04-06", spent_balance=100, spent_today=100
        )

        # 1. Rejected while plenty of time left (> 60s)
        with self.assertRaises(ValueError):
            apply_save_grace(pol, st, now_local=datetime(2026, 4, 6, 15, 0, 0, tzinfo=timezone.utc))

        # 2. Rejected at 2:00 AM deep into bedtime curfew (anti-bypass!)
        with self.assertRaises(ValueError):
            apply_save_grace(pol, st, now_local=datetime(2026, 4, 6, 2, 0, 0, tzinfo=timezone.utc))

        # 3. Allowed in final minute before daily expiry; accrues ZERO debt past daily_limit!
        t_exp = datetime(2026, 4, 6, 16, 0, 0, tzinfo=timezone.utc)
        st.spent_balance = 3590
        st.spent_today = 3590
        st.last_checked_utc = t_exp.isoformat()
        apply_save_grace(pol, st, now_local=t_exp)
        for tick_idx in range(1, 21):
            evaluate_tick(
                pol,
                st,
                cfg,
                t_exp + timedelta(seconds=5 * tick_idx),
                elapsed_sec=5,
                is_active=True,
                screen_locked=False,
            )
        # spent_balance capped at daily_limit (3600), NOT 3690!
        self.assertEqual(st.spent_balance, 3600)
        self.assertEqual(st.spent_today, 3690)

        # 4. Survives midnight rollover when claimed at 23:59:50
        pol24 = UserPolicy(
            username="alice",
            daily_limits=dict.fromkeys(("mon", "tue", "wed", "thu", "fri", "sat", "sun"), 3600),
            curfew_windows={
                d: [TimeWindow("00:00", "24:00")]
                for d in ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
            },
        )
        st_mid = UserAccountingState(
            username="alice", date="2026-04-06", spent_balance=3595, spent_today=3595
        )
        t_late = datetime(2026, 4, 6, 23, 59, 50, tzinfo=timezone.utc)
        st_mid.last_checked_utc = t_late.isoformat()
        apply_save_grace(pol24, st_mid, now_local=t_late)
        evaluate_tick(
            pol24,
            st_mid,
            cfg,
            datetime(2026, 4, 7, 0, 0, 30, tzinfo=timezone.utc),
            elapsed_sec=5,
            is_active=True,
            screen_locked=False,
        )
        self.assertEqual(st_mid.date, "2026-04-07")
        self.assertIsNotNone(st_mid.save_grace_until_utc)

    def test_today_override_supersedes_holiday_pause_and_clears_bonus_note(self):
        cfg = DaemonConfig()
        pol = UserPolicy(
            username="alice",
            daily_limits=dict.fromkeys(("mon", "tue", "wed", "thu", "fri", "sat", "sun"), 3600),
            holidays=[
                HolidayOverride(
                    id="h1",
                    label="Spring Break",
                    start_date="2026-04-06",
                    end_date="2026-04-10",
                    mode="pause",
                )
            ],
        )
        st = UserAccountingState(
            username="alice", date="2026-04-06", bonus_today=900, last_bonus_note="Chores"
        )
        now = datetime(2026, 4, 6, 14, 0, 0, tzinfo=timezone.utc)
        apply_today_override(st, now_local=now, limit_sec=1800)
        self.assertEqual(st.last_bonus_note, "")
        res = evaluate_tick(pol, st, cfg, now, elapsed_sec=0, is_active=True, screen_locked=False)
        self.assertNotEqual(res.enforcement_state, "paused")
        self.assertEqual(res.daily_limit_today, 1800)

    def test_applimits_multiuser_sigkill_denylist_and_flatpak_intelligence(self):
        monitor = AppLimitsMonitor()
        # 1. Protected processes + self PID are never killed
        my_pid = os.getpid()
        protected_matches = [
            {"pid": my_pid, "name": "python3"},
            {"pid": 5001, "name": "cinnamon-screensaver"},
            {"pid": 5002, "name": "dbus-daemon"},
            {"pid": 5003, "name": "curfewd-tray"},
        ]
        killed = monitor.enforce_kill(1000, [], prematched=protected_matches)
        self.assertEqual(killed, [])

        # 2. Multi-user SIGTERM -> SIGKILL isolation
        events: list[tuple[int, str]] = []

        class _FakeProc:
            def __init__(self, pid: int):
                self._pid = pid
                self._uid = 1000 if pid == 7001 else 1001

            def uids(self):
                return mock.Mock(real=self._uid)

            def name(self):
                return "minecraft"

            def exe(self):
                return "/usr/bin/minecraft"

            def create_time(self):
                return 12345.0

            def terminate(self):
                events.append((self._pid, "TERM"))

            def kill(self):
                events.append((self._pid, "KILL"))

        with mock.patch("curfewd.engine.applimits.psutil") as fake_ps:
            fake_ps.Error = Exception
            fake_ps.Process.side_effect = _FakeProc
            for _ in range(4):
                monitor.enforce_kill(1000, [], prematched=[{"pid": 7001, "name": "minecraft"}])
                monitor.enforce_kill(1001, [], prematched=[{"pid": 7002, "name": "minecraft"}])
        # Both users must escalate to SIGKILL after grace ticks without clobbering each other!
        self.assertIn((7001, "KILL"), events)
        self.assertIn((7002, "KILL"), events)

        # 3. Cgroup \x2d unescaping & KDE hex-UUID scopes + canonical Flatpak classification
        with tempfile.TemporaryDirectory() as fake_proc:
            os.makedirs(os.path.join(fake_proc, "801"))
            with open(os.path.join(fake_proc, "801", "cgroup"), "w", encoding="utf-8") as fh:
                fh.write("0::/user.slice/app-flatpak-org.telegram.desktop-999.scope\n")
            os.makedirs(os.path.join(fake_proc, "802"))
            with open(os.path.join(fake_proc, "802", "cgroup"), "w", encoding="utf-8") as fh:
                fh.write(
                    "0::/user.slice/app-heroic\\x2dlauncher@a1b2c3d4e5f67890abcdef1234567890.service\n"
                )
            fp1, _ = read_proc_cgroup_app(801, proc_root=fake_proc)
            _, scope2 = read_proc_cgroup_app(802, proc_root=fake_proc)
            self.assertEqual(fp1, "org.telegram.desktop")
            self.assertEqual(scope2, "heroic-launcher")

            # Adversarial ReDoS payload must parse in linear time (< 50ms)
            os.makedirs(os.path.join(fake_proc, "803"))
            with open(os.path.join(fake_proc, "803", "cgroup"), "w", encoding="utf-8") as fh:
                fh.write("0::/user.slice/" + "app-flatpak--" * 300 + "\n")
            t0 = time.monotonic()
            self.assertEqual(read_proc_cgroup_app(803, proc_root=fake_proc), ("", ""))
            self.assertLess(time.monotonic() - t0, 0.05)

            # Flatpak container PID (99999 not in ppid_map) must NOT disable foreground_only match
            proc_tg = mock.Mock()
            proc_tg.info = {
                "pid": 801,
                "ppid": 1,
                "uids": mock.Mock(real=1000),
                "name": "telegram-bin",
            }
            proc_tg.exe.return_value = "/app/bin/telegram-bin"
            proc_tg.cmdline.return_value = ["telegram-bin"]
            mon2 = AppLimitsMonitor(proc_root=fake_proc)
            rule_tg = AppLimitRule(
                "telegram",
                "Telegram",
                daily_limit_sec=1800,
                category="Social",
                flatpak_id="org.telegram.desktop",
                foreground_only=True,
            )
            with mock.patch("curfewd.engine.applimits.psutil") as fake_ps:
                fake_ps.Error = Exception
                fake_ps.process_iter.return_value = [proc_tg]
                scan = mon2.scan_user_processes(
                    1000,
                    [rule_tg],
                    category_limits=[CategoryLimitRule("Social", 1800)],
                    active_window_pid=99999,
                )
            self.assertIn("telegram", scan.running_patterns)
            self.assertIn("Social", scan.running_categories)

    def test_screenlock_flag_injection_defense_for_leading_dash_message(self):
        dbus_cmds: list[list[str]] = []
        cli_cmds: list[list[str]] = []
        enforcer_dbus = screenlock.ScreenLockEnforcer(
            runner=lambda cmd, **_kw: (
                dbus_cmds.append(cmd),
                mock.Mock(returncode=0, stdout="", stderr=""),
            )[1],
            which_fn=lambda n: f"/usr/bin/{n}",
            sleep_fn=lambda _s: None,
        )
        ctx = screenlock.SessionContext(
            session_id="1",
            uid=1000,
            username="alice",
            gid=1000,
            home_dir="/home/alice",
            session_type="x11",
            display=":0",
            xauthority="/home/alice/.Xauthority",
            wayland_display="",
            xdg_runtime_dir="/run/user/1000",
            dbus_address="unix:path=/run/user/1000/bus",
            desktop="X-Cinnamon",
        )
        enforcer_dbus.invoke_backend_lock(
            ctx, screenlock.BACKENDS["cinnamon"], lock_message="--system"
        )
        self.assertIn("--", dbus_cmds[0])
        self.assertEqual(dbus_cmds[0][-1], "--system")

        enforcer_cli = screenlock.ScreenLockEnforcer(
            runner=lambda cmd, **_kw: (
                cli_cmds.append(cmd),
                mock.Mock(returncode=0, stdout="", stderr=""),
            )[1],
            which_fn=lambda n: None if n == "gdbus" else f"/usr/bin/{n}",
            sleep_fn=lambda _s: None,
        )
        enforcer_cli.invoke_backend_lock(
            ctx, screenlock.BACKENDS["cinnamon"], lock_message="--version"
        )
        self.assertIn("--away-message=--version", cli_cmds[0])

    def test_tray_wayland_idle_hud_categories_async_probe_and_bridge_type_error(self):
        # 1. Wayland idle_seconds returns 0 without running xprintidle
        probe = SessionTelemetryProbe()
        with (
            mock.patch.dict(os.environ, {"XDG_SESSION_TYPE": "wayland", "DISPLAY": ":0"}),
            mock.patch("curfewd.tray.app.subprocess.run") as run_mock,
        ):
            self.assertEqual(probe.idle_seconds(), 0)
            run_mock.assert_not_called()

        # 2. CountdownHUD hides on paused & shows expiring Category Budget
        hud = mock.Mock()
        client = mock.Mock()
        ctrl = TrayController(
            username="alice",
            client=client,
            screensaver=mock.Mock(is_active=lambda: False),
            notifier=mock.Mock(),
            hud=hud,
        )
        ctrl._update_hud(
            {
                "enforcement_state": "paused",
                "time_left_continuous": 30,
                "client_prefs": {"countdown_hud_enabled": True},
            }
        )
        hud.hide.assert_called_once()
        hud.reset_mock()
        ctrl._update_hud(
            {
                "enforcement_state": "ok",
                "time_left_continuous": 3600,
                "client_prefs": {"countdown_hud_enabled": True},
                "app_limits_status": {
                    "enabled": True,
                    "apps": [],
                    "categories": [{"category": "Gaming", "running": True, "time_left_today": 45}],
                },
            }
        )
        hud.show_countdown.assert_called_once_with("Gaming", 45, save_grace_available=False)

        # 3. Async probe offloads telemetry_probe off main thread and invokes idle_dispatch
        dispatched = threading.Event()
        dispatched_calls: list[tuple] = []

        def fake_idle_add(fn, *args):
            dispatched_calls.append((fn, args))
            fn(*args)
            dispatched.set()

        fake_probe = mock.Mock(idle_seconds=lambda: 12, active_window_pid=lambda: 4242)
        ctrl_async = TrayController(
            username="alice",
            client=client,
            screensaver=mock.Mock(is_active=lambda: False),
            notifier=mock.Mock(),
            telemetry_probe=fake_probe,
            idle_dispatch=fake_idle_add,
        )
        ctrl_async.on_screensaver_challenge("alice", "nonce-1")
        self.assertTrue(dispatched.wait(timeout=3.0))
        client.report_client_telemetry.assert_called_once_with(
            "nonce-1", {"screensaver_active": False, "idle_sec": 12, "active_window_pid": 4242}
        )

        # 4. UIBridge catches TypeError cleanly as validation_error
        bridge = UIBridge(client=mock.Mock())
        res = bridge.handle("grant_time", {"username": "alice", "delta_seconds": None})
        self.assertFalse(res["ok"])
        self.assertEqual(res["code"], "validation_error")

    def test_models_history_and_cli_edge_cases(self):
        # Negative weekly_limit raises ValueError; 0 preserved
        with self.assertRaises(ValueError):
            UserPolicy.from_dict({"username": "alice", "weekly_limit": -10})
        p0 = UserPolicy.from_dict({"username": "alice", "weekly_limit": 0, "monthly_limit": 0})
        self.assertEqual(p0.weekly_limit, 0)
        self.assertEqual(p0.monthly_limit, 0)

        # All-zero v1 playtime migrates to 0 (not 3600)
        pv1 = UserPolicy.from_dict(
            {
                "username": "alice",
                "playtime": {
                    "enabled": True,
                    "daily_limits": dict.fromkeys(
                        ("mon", "tue", "wed", "thu", "fri", "sat", "sun"), 0
                    ),
                    "apps": [{"pattern": "steam", "label": "Steam"}],
                },
            }
        )
        self.assertEqual(pv1.app_limits.apps[0].daily_limit_sec, 0)

        # History latest-date metadata & atomic time request claim
        with tempfile.TemporaryDirectory() as tmp:
            hist = HistoryStore(os.path.join(tmp, "h.db"))
            hist.record_daily_delta(
                "alice", "2026-04-01", app_deltas=[("steam", "ZzzOldName", "App", 60)]
            )
            hist.record_daily_delta(
                "alice", "2026-04-05", app_deltas=[("steam", "AaNewName", "Gaming", 60)]
            )
            summary = hist.get_app_usage_summary(
                "alice", days=30, now_local=datetime(2026, 4, 6, 12, 0, tzinfo=timezone.utc)
            )
            self.assertEqual(summary[0]["label"], "AaNewName")
            self.assertEqual(summary[0]["category"], "Gaming")

            req = hist.create_time_request("alice", "daily", 900, "", "Daily", "pls")
            first = hist.resolve_time_request(req["id"], True, "parent", granted_sec=900, note="ok")
            self.assertIsNotNone(first)
            assert first is not None
            self.assertEqual(first["status"], "approved")
            # Atomic WHERE status = 'pending' guarantees second concurrent resolve returns None
            self.assertIsNone(
                hist.resolve_time_request(req["id"], True, "parent", granted_sec=900, note="double")
            )

        # CLI _normalize_argv edge cases
        self.assertEqual(
            _normalize_argv(["grant", "grant", "-15m"]), ["grant", "grant", "--", "-15m"]
        )
        self.assertEqual(_normalize_argv(["grant", "-h"]), ["grant", "-h"])
        self.assertEqual(
            _normalize_argv(["grant", "alice", "--note", "-5m", "+15m"]),
            ["grant", "alice", "--note", "-5m", "+15m"],
        )
        # Overflow duration exits cleanly with usage code 2
        with mock.patch("sys.stderr"):
            rc = run_cli(["grant", "alice", "999999999999999999999999h"])
        self.assertEqual(rc, 2)


class TestWebUIStateJSInvariants(unittest.TestCase):
    """Runs real curfewd/ui/web/js/state.js in headless Node.js to prove P0-3, P1-11..14, P2-15, P3-7."""

    @unittest.skipUnless(shutil.which("node"), "requires node")
    def test_state_js_in_place_push_isolation_and_window_clamping(self):
        state_js = os.path.abspath(
            os.path.join(os.path.dirname(__file__), "..", "curfewd", "ui", "web", "js", "state.js")
        )
        script = r"""
const fs = require("fs");
const vm = require("vm");
const assert = require("assert");

const domNodes = {
  myRingMain: { textContent: "", style: {} },
  myRingArc: { attrs: {}, setAttribute(k, v) { this.attrs[k] = v; } },
  myUsedToday: { textContent: "" },
  unsavedBar: { classList: { toggle() {} } },
  btnSave: { disabled: false, textContent: "" },
  snackbar: { textContent: "", classList: { add() {}, remove() {} } },
};
const navLeftNodes = {
  parent1: { textContent: "Parent" },
  alice: { textContent: "1h" },
};
let renderCount = 0;

const ctx = {
  window: { matchMedia: () => ({ matches: true, addEventListener() {} }) },
  document: {
    documentElement: { dataset: {} },
    getElementById: (id) => domNodes[id] || null,
    querySelector: (sel) => {
      const m = sel.match(/data-navleft="([^"]+)"/);
      return m ? navLeftNodes[m[1]] || null : null;
    },
  },
  CSS: { escape: (s) => String(s) },
  Intl,
  Date,
  Math,
  JSON,
  Map,
  Set,
  Number,
  String,
  Boolean,
  Array,
  Object,
  setTimeout: () => 1,
  clearTimeout: () => {},
};
ctx.window.CurfewUI = { render: () => { renderCount++; } };
vm.createContext(ctx);
vm.runInContext(fs.readFileSync(process.argv[1], "utf8"), ctx);
const UI = ctx.window.CurfewUI;

// 1. P3-7: normalizeWindows clamps startMin=1425, endMin=1440
const normW = UI.normalizeWindows([{ startMin: 1430, endMin: 1435 }]);
assert.strictEqual(normW[0].startMin, 1425);
assert.strictEqual(normW[0].endMin, 1440);

// 2. P0-3 + P1-13: applyLiveStatusPush mutates existing object in place & preserves savedSnapshot when another user is dirty
UI.state.surface = "admin";
UI.state.nav = "user:alice";
const rawAlice = {
  username: "alice",
  full_name: "Alice",
  role: "child",
  logged_in: true,
  screen_locked: false,
  enforcement_state: "ok",
  time_spent_today: 600,
  time_left_today: 1800,
  daily_limit_today: 3600,
  policy: {
    username: "alice",
    role: "child",
    weekly_limit: 1800,
    daily_limits: { mon: 3600, tue: 3600, wed: 3600, thu: 3600, fri: 3600, sat: 3600, sun: 3600 },
    curfew_windows: { mon: [{ start: "08:00", end: "21:00" }], tue: [{ start: "08:00", end: "21:00" }], wed: [{ start: "08:00", end: "21:00" }], thu: [{ start: "08:00", end: "21:00" }], fri: [{ start: "08:00", end: "21:00" }], sat: [{ start: "08:00", end: "21:00" }], sun: [{ start: "08:00", end: "21:00" }] },
    app_limits: { enabled: true, apps: [], category_limits: [] },
  },
};
const rawParent = {
  username: "parent1",
  full_name: "Parent",
  role: "parent",
  logged_in: true,
  enforcement_state: "unrestricted",
  time_spent_today: 100,
  time_left_today: 86400,
  policy: { username: "parent1", role: "parent", daily_limits: {}, curfew_windows: {}, app_limits: { enabled: false, apps: [], category_limits: [] } },
};
UI.state.users.alice = UI.normalizeUserStatus(rawAlice);
UI.state.users.parent1 = UI.normalizeUserStatus(rawParent);
UI.state.users.alice.scheduleMode = "custom";
UI.snapshotState();
const origSnap = UI.state.savedSnapshot;

// P2-15: clean timeLeftSec honors daemon weekly cap (1800, not 3600-600=3000)
assert.strictEqual(UI.timeLeftSec(UI.state.users.alice), 1800);

// P0-3: clean live push preserves exact object identity AND custom scheduleMode
const refBefore = UI.state.users.alice;
UI.applyLiveStatusPush("alice", Object.assign({}, rawAlice, { time_spent_today: 660, time_left_today: 1740 }));
assert.strictEqual(UI.state.users.alice, refBefore, "state.users.alice reference was replaced!");
assert.strictEqual(UI.state.users.alice.scheduleMode, "custom");
assert.strictEqual(UI.state.users.alice.spentToday, 660);

// P1-13 + P2-15: while alice is dirty, clean push for parent1 must NOT overwrite savedSnapshot OR parent nav label
UI.state.dirtyPolicies.add("alice");
UI.state.users.alice.lockoutAction = "suspend";
UI.snapshotState = ((orig) => () => { throw new Error("snapshotState called while dirty!"); })(UI.snapshotState);
UI.applyLiveStatusPush("parent1", Object.assign({}, rawParent, { time_spent_today: 120 }));
assert.strictEqual(navLeftNodes.parent1.textContent, "Parent");
assert.strictEqual(UI.state.users.alice.lockoutAction, "suspend");

// 3. P1-11 + P1-12 + P1-14: my_status preserves parentCodeEnabled, isolates sibling TimeRequestUpdated, preserves "Study" ring text
UI.state.surface = "my_status";
UI.state.me = UI.normalizeUserStatus(Object.assign({}, rawAlice, { enforcement_state: "homework", parent_code_enabled: true }));
UI.state.myRequests = [];
UI.applyTimeRequestPush("bob", { id: 99, username: "bob", status: "pending", requested_sec: 900, target_label: "Daily" });
assert.strictEqual(UI.state.myRequests.length, 0, "Sibling time request leaked into alice!");

const pushWithoutFlag = Object.assign({}, rawAlice, { enforcement_state: "homework", time_spent_today: 665 });
delete pushWithoutFlag.parent_code_enabled;
UI.applyLiveStatusPush("alice", pushWithoutFlag);
assert.strictEqual(UI.state.me.parentCodeEnabled, true, "parentCodeEnabled was wiped!");
assert.strictEqual(domNodes.myRingMain.textContent, "Study", "Study label overwritten!");
assert.ok(domNodes.myRingArc.attrs["stroke-dashoffset"] !== undefined, "Ring arc not updated!");
console.log("ALL_JS_INVARIANTS_OK");
"""
        proc = subprocess.run(
            ["node", "-e", script, state_js],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        self.assertEqual(
            proc.returncode, 0, msg=f"Node assertion failed:\n{proc.stderr}\n{proc.stdout}"
        )
        self.assertIn("ALL_JS_INVARIANTS_OK", proc.stdout)


@unittest.skipUnless(HAS_HYPOTHESIS, "requires python3-hypothesis")
class TestHypothesisPropertyFuzzing(unittest.TestCase):
    """Property-based fuzzing across QR encoding, duration/time/policy parsers, and lock sanitizer."""

    @given(st.text(min_size=1, max_size=120))
    @h_settings(max_examples=40, deadline=None, database=None)
    def test_fuzz_qr_roundtrip_arbitrary_utf8(self, text: str):
        raw = text.encode("utf-8")
        if len(raw) > 230:
            return
        mat = encode_qr_matrix(text)
        _, _, decoded = decode_qr_iso18004(mat)
        self.assertEqual(decoded, raw)

    @given(st.text(max_size=120))
    @h_settings(max_examples=80, deadline=None, database=None)
    def test_fuzz_parsers_and_sanitizer_never_crash_unexpectedly(self, s: str):
        try:
            dur = parse_duration(s)
            self.assertIsInstance(dur, int)
        except (ValueError, OverflowError):
            pass

        try:
            mins = parse_hhmm_to_minutes(s)
            self.assertTrue(0 <= mins <= 1440)
        except ValueError:
            pass

        clean = screenlock.ScreenLockEnforcer._sanitize_lock_message(s)
        self.assertLessEqual(len(clean), 160)
        self.assertTrue(all(ch.isprintable() and ch not in "\r\n\t" for ch in clean))

    @given(
        st.dictionaries(
            st.text(min_size=1, max_size=20),
            st.one_of(st.none(), st.booleans(), st.integers(-1000, 100000), st.text(max_size=20)),
            max_size=12,
        )
    )
    @h_settings(max_examples=60, deadline=None, database=None)
    def test_fuzz_daemon_config_and_user_policy_from_dict(self, payload: dict):
        try:
            cfg = DaemonConfig.from_dict(payload)
            self.assertGreaterEqual(cfg.final_countdown_sec, 0)
        except (ValueError, TypeError):
            pass

        pol_in = dict(payload)
        pol_in["username"] = "alice"
        try:
            pol = UserPolicy.from_dict(pol_in)
            self.assertEqual(pol.username, "alice")
        except (ValueError, TypeError):
            pass


if __name__ == "__main__":
    unittest.main()
