"""Unit tests for curfewd.auth_code (offline RFC 6238 TOTP, rate limit, anti-replay, SVG QR)."""

import base64
import os
import stat
import tempfile
import unittest
import xml.etree.ElementTree as ET

from curfewd.auth_code import (
    ParentAccessCodeManager,
    build_otpauth_uri,
    compute_totp,
    encode_qr_matrix,
    render_qr_svg,
)


class TestParentAccessCodeAndQR(unittest.TestCase):
    def test_rfc6238_totp_vector_and_otpauth_uri(self):
        # Standard RFC 6238 SHA-1 test secret ("12345678901234567890")
        secret_b32 = base64.b32encode(b"12345678901234567890").decode("ascii").rstrip("=")
        # At T = 59s -> step 1 -> RFC 6238 8-digit = 94287082 -> 6-digit = 287082
        self.assertEqual(compute_totp(secret_b32, step=1, digits=6), "287082")
        uri = build_otpauth_uri(secret_b32, account="home-pc")
        self.assertTrue(uri.startswith("otpauth://totp/curfewd:home-pc?secret="))
        self.assertIn("issuer=curfewd", uri)

    def test_qr_encoder_and_svg_output_for_otpauth_uri(self):
        uri = build_otpauth_uri("JBSWY3DPEHPK3PXPJBSWY3DPEHPK3PXP", account="living-room-pc")
        mat = encode_qr_matrix(uri)
        self.assertEqual(len(mat), len(mat[0]))
        self.assertGreaterEqual(len(mat), 21)
        # Verify top-left 7x7 finder box outer border is dark
        for i in range(7):
            self.assertEqual(mat[0][i], 1)
            self.assertEqual(mat[6][i], 1)
        svg = render_qr_svg(uri)
        root = ET.fromstring(svg)  # nosec B314 - self-generated test SVG
        self.assertTrue(root.tag.endswith("svg"))
        self.assertIn("<path", svg)

    def test_manager_setup_verify_antireplay_rate_limit_and_permissions(self):
        with tempfile.TemporaryDirectory() as tmp:
            auth_path = os.path.join(tmp, "parent_auth.json")
            mgr = ParentAccessCodeManager(auth_path=auth_path)
            self.assertFalse(mgr.is_enabled())

            enrolled = mgr.setup(enable=True, rotate=False, account="testbox")
            self.assertTrue(enrolled["enabled"])
            self.assertTrue(enrolled["secret_b32"])
            self.assertIn("<svg", enrolled["qr_svg"])
            mode = stat.S_IMODE(os.stat(auth_path).st_mode)
            self.assertEqual(mode, 0o600)

            # status() must NEVER leak the secret
            st = mgr.status()
            self.assertTrue(st["enabled"])
            self.assertNotIn("secret_b32", st)

            # Valid code succeeds once, replay in same step is rejected
            step = 50000000
            ts = step * 30.0 + 5.0
            code = compute_totp(enrolled["secret_b32"], step=step)
            ok, _ = mgr.verify_code(code, now_ts=ts, now_mono=100.0)
            self.assertTrue(ok)
            ok_replay, msg_replay = mgr.verify_code(code, now_ts=ts, now_mono=101.0)
            self.assertFalse(ok_replay)
            self.assertIn("already used", msg_replay.lower())

            # Next step succeeds
            next_code = compute_totp(enrolled["secret_b32"], step=step + 1)
            ok2, _ = mgr.verify_code(next_code, now_ts=ts + 30.0, now_mono=130.0)
            self.assertTrue(ok2)

            # 5 consecutive failures trigger lockout even for a valid code
            for i in range(5):
                bad_ok, _ = mgr.verify_code("000000", now_ts=ts + 60.0, now_mono=200.0 + i)
                self.assertFalse(bad_ok)
            valid_later = compute_totp(enrolled["secret_b32"], step=step + 3)
            locked_ok, locked_msg = mgr.verify_code(valid_later, now_ts=ts + 90.0, now_mono=210.0)
            self.assertFalse(locked_ok)
            self.assertIn("too many failed", locked_msg.lower())

            # Disable clears secret & state
            off = mgr.setup(enable=False)
            self.assertFalse(off["enabled"])
            self.assertFalse(mgr.is_enabled())


if __name__ == "__main__":
    unittest.main()
