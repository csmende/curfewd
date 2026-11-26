"""Offline RFC 6238 Parent Access Code (TOTP) manager and pure-Python SVG QR encoder (Spec 006)."""

import base64
import hashlib
import hmac
import json
import os
import secrets
import socket
import struct
import threading
import time
from typing import Any
from urllib.parse import quote

from curfewd import DEFAULT_CONFIG_DIR, SCHEMA_VERSION
from curfewd.models import utc_now_iso
from curfewd.storage import _read_json_nofollow, write_json_atomic

DEFAULT_TOTP_DIGITS = 6
DEFAULT_TOTP_PERIOD = 30
_MAX_FAILURES = 5
_FAILURE_WINDOW_SEC = 300.0
_LOCKOUT_SEC = 300.0


def generate_totp_secret(num_bytes: int = 20) -> str:
    """Return an unpadded uppercase Base32 CSPRNG secret (160-bit default)."""
    raw = secrets.token_bytes(max(16, int(num_bytes)))
    return base64.b32encode(raw).decode("ascii").rstrip("=")


def compute_totp(
    secret_b32: str,
    step: int,
    digits: int = DEFAULT_TOTP_DIGITS,
) -> str:
    """Compute an RFC 6238 / RFC 4226 TOTP code for a given time-step counter."""
    clean = str(secret_b32).strip().upper().replace(" ", "")
    pad_len = (-len(clean)) % 8
    key = base64.b32decode(clean + ("=" * pad_len), casefold=True)
    msg = struct.pack(">Q", int(step))
    digest = hmac.new(key, msg, hashlib.sha1).digest()  # nosec B303 - RFC 6238 standard TOTP HMAC-SHA1
    offset = digest[-1] & 0x0F
    code_int = struct.unpack(">I", digest[offset : offset + 4])[0] & 0x7FFFFFFF
    return str(code_int % (10**digits)).zfill(digits)


def build_otpauth_uri(
    secret_b32: str,
    account: str = "",
    issuer: str = "curfewd",
    digits: int = DEFAULT_TOTP_DIGITS,
    period: int = DEFAULT_TOTP_PERIOD,
) -> str:
    """Build a standard otpauth://totp/ URI compatible with major authenticator apps."""
    acct = (account or socket.gethostname() or "desktop").strip()[:48]
    label = f"{quote(issuer, safe='')}:{quote(acct, safe='')}"
    return (
        f"otpauth://totp/{label}?secret={quote(secret_b32, safe='')}"
        f"&issuer={quote(issuer, safe='')}&digits={int(digits)}&period={int(period)}"
    )


# ---------------- Pure-Python Offline QR Encoder (Versions 1..9, EC-L, Byte Mode) ---------------- #

# (version, size, total_data_cw, ec_cw_per_block, num_blocks, align_centers)
_QR_SPECS: tuple[tuple[int, int, int, int, int, tuple[int, ...]], ...] = (
    (1, 21, 19, 7, 1, ()),
    (2, 25, 34, 10, 1, (6, 18)),
    (3, 29, 55, 15, 1, (6, 22)),
    (4, 33, 80, 20, 1, (6, 26)),
    (5, 37, 108, 26, 1, (6, 30)),
    (6, 41, 136, 18, 2, (6, 34)),
    (7, 45, 156, 20, 2, (6, 22, 38)),
    (8, 49, 194, 24, 2, (6, 24, 42)),
    (9, 53, 232, 30, 2, (6, 26, 46)),
)


def _gf_mul(a: int, b: int) -> int:
    res = 0
    for _ in range(8):
        if b & 1:
            res ^= a
        hi = a & 0x80
        a = (a << 1) & 0xFF
        if hi:
            a ^= 0x1D  # primitive poly 0x11D
        b >>= 1
    return res


def _rs_generator(degree: int) -> list[int]:
    poly = [1]
    root = 1
    for _ in range(degree):
        nxt = [0] * (len(poly) + 1)
        for idx, coeff in enumerate(poly):
            nxt[idx] ^= coeff
            nxt[idx + 1] ^= _gf_mul(coeff, root)
        poly = nxt
        root = _gf_mul(root, 2)
    return poly


def _rs_remainder(data: list[int], ec_len: int) -> list[int]:
    gen = _rs_generator(ec_len)
    rem = [0] * ec_len
    for byte in data:
        factor = byte ^ rem[0]
        rem = [*rem[1:], 0]
        if factor:
            for idx in range(ec_len):
                rem[idx] ^= _gf_mul(gen[idx + 1], factor)
    return rem


def _qr_format_bits(mask: int) -> int:
    """Compute 15-bit BCH format word for EC-L (01) and mask (0..7)."""
    data = (0b01 << 3) | (mask & 0x7)
    rem = data << 10
    for bit in range(14, 9, -1):
        if rem & (1 << bit):
            rem ^= 0b10100110111 << (bit - 10)
    return ((data << 10) | rem) ^ 0b101010000010010


def _qr_version_bits(version: int) -> int:
    """Compute 18-bit Golay version word for versions >= 7."""
    rem = version << 12
    for bit in range(17, 11, -1):
        if rem & (1 << bit):
            rem ^= 0b1111100100101 << (bit - 12)
    return (version << 12) | rem


def _place_finder(grid: list[list[int]], func: list[list[bool]], row: int, col: int) -> None:
    size = len(grid)
    for dr in range(-1, 8):
        for dc in range(-1, 8):
            r, c = row + dr, col + dc
            if not (0 <= r < size and 0 <= c < size):
                continue
            in_box = 0 <= dr <= 6 and 0 <= dc <= 6
            border = dr in (0, 6) or dc in (0, 6)
            center = 2 <= dr <= 4 and 2 <= dc <= 4
            grid[r][c] = 1 if (in_box and (border or center)) else 0
            func[r][c] = True


def _place_alignment(grid: list[list[int]], func: list[list[bool]], r0: int, c0: int) -> None:
    for dr in range(-2, 3):
        for dc in range(-2, 3):
            dist = max(abs(dr), abs(dc))
            grid[r0 + dr][c0 + dc] = 1 if dist != 1 else 0
            func[r0 + dr][c0 + dc] = True


def _encode_qr_codewords(
    payload: bytes, total_data_cw: int, ec_len: int, num_blocks: int
) -> list[int]:
    bits: list[int] = []
    # Mode 0100 (Byte), 8-bit length (valid for versions 1..9)
    for val, width in ((0b0100, 4), (len(payload), 8)):
        for shift in range(width - 1, -1, -1):
            bits.append((val >> shift) & 1)
    for b in payload:
        for shift in range(7, -1, -1):
            bits.append((b >> shift) & 1)

    max_bits = total_data_cw * 8
    term = min(4, max_bits - len(bits))
    bits.extend([0] * term)
    while len(bits) % 8:
        bits.append(0)

    data_cw = []
    for idx in range(0, len(bits), 8):
        byte = 0
        for b in bits[idx : idx + 8]:
            byte = (byte << 1) | b
        data_cw.append(byte)

    pad_bytes = (0xEC, 0x11)
    pad_idx = 0
    while len(data_cw) < total_data_cw:
        data_cw.append(pad_bytes[pad_idx & 1])
        pad_idx += 1

    block_size = total_data_cw // num_blocks
    data_blocks: list[list[int]] = []
    ec_blocks: list[list[int]] = []
    for b_idx in range(num_blocks):
        chunk = data_cw[b_idx * block_size : (b_idx + 1) * block_size]
        data_blocks.append(chunk)
        ec_blocks.append(_rs_remainder(chunk, ec_len))

    interleaved: list[int] = []
    for i in range(block_size):
        for blk in data_blocks:
            interleaved.append(blk[i])
    for i in range(ec_len):
        for blk in ec_blocks:
            interleaved.append(blk[i])
    return interleaved


def _write_format_and_version(
    grid: list[list[int]], func: list[list[bool]], version: int, mask: int
) -> None:
    size = len(grid)
    fmt = _qr_format_bits(mask)
    for i in range(6):
        bit = (fmt >> (14 - i)) & 1
        grid[8][i] = bit
        grid[size - 1 - i][8] = bit
        func[8][i] = True
        func[size - 1 - i][8] = True
    for i, (r1, c1, r2, c2) in enumerate(
        (
            (8, 7, size - 7, 8),
            (8, 8, 8, size - 8),
            (7, 8, 8, size - 7),
        ),
        start=6,
    ):
        bit = (fmt >> (14 - i)) & 1
        grid[r1][c1] = bit
        grid[r2][c2] = bit
        func[r1][c1] = True
        func[r2][c2] = True
    for i in range(9, 15):
        bit = (fmt >> (14 - i)) & 1
        r1 = 14 - i
        c2 = size - 15 + i
        grid[r1][8] = bit
        grid[8][c2] = bit
        func[r1][8] = True
        func[8][c2] = True
    # Dark module
    grid[size - 8][8] = 1
    func[size - 8][8] = True

    if version >= 7:
        vbits = _qr_version_bits(version)
        for i in range(18):
            bit = (vbits >> i) & 1
            a = size - 11 + (i % 3)
            b = i // 3
            grid[a][b] = bit
            grid[b][a] = bit
            func[a][b] = True
            func[b][a] = True


def _place_qr_function_patterns(
    grid: list[list[int]], func: list[list[bool]], version: int, align: tuple[int, ...], mask: int
) -> None:
    size = len(grid)
    _place_finder(grid, func, 0, 0)
    _place_finder(grid, func, 0, size - 7)
    _place_finder(grid, func, size - 7, 0)

    # Alignment patterns must be placed BEFORE timing patterns so V7-V9 centers on row/col 6
    # are not skipped while finder corners remain protected.
    for r0 in align:
        for c0 in align:
            if func[r0][c0]:
                continue
            _place_alignment(grid, func, r0, c0)

    for i in range(8, size - 8):
        bit = 1 if i % 2 == 0 else 0
        grid[6][i] = bit
        grid[i][6] = bit
        func[6][i] = True
        func[i][6] = True

    _write_format_and_version(grid, func, version, mask)


def encode_qr_matrix(text: str) -> list[list[int]]:
    """Encode UTF-8 text into a scannable QR matrix (Version 1..9, EC-L)."""
    payload = text.encode("utf-8")
    chosen = None
    for spec in _QR_SPECS:
        if len(payload) <= spec[2] - 2:
            chosen = spec
            break
    if chosen is None:
        raise ValueError("QR payload exceeds Version 9 byte-mode capacity (230 bytes)")

    version, size, total_data_cw, ec_len, num_blocks, align = chosen
    grid = [[0] * size for _ in range(size)]
    func = [[False] * size for _ in range(size)]
    mask = 0
    _place_qr_function_patterns(grid, func, version, align, mask)

    # Place interleaved data + EC codewords in zigzag columns
    codewords = _encode_qr_codewords(payload, total_data_cw, ec_len, num_blocks)
    stream_bits: list[int] = []
    for cw in codewords:
        for shift in range(7, -1, -1):
            stream_bits.append((cw >> shift) & 1)

    bit_idx = 0
    col = size - 1
    upward = True
    while col > 0:
        if col == 6:
            col -= 1
        rows = range(size - 1, -1, -1) if upward else range(size)
        for r in rows:
            for dc in (0, -1):
                c = col + dc
                if func[r][c]:
                    continue
                val = stream_bits[bit_idx] if bit_idx < len(stream_bits) else 0
                bit_idx += 1
                if (r + c) % 2 == 0:
                    val ^= 1
                grid[r][c] = val
        upward = not upward
        col -= 2

    return grid


def render_qr_svg(text: str, quiet_zone: int = 4) -> str:
    """Render a compact, crisp, 100% offline SVG QR code for text."""
    matrix = encode_qr_matrix(text)
    n = len(matrix)
    total = n + quiet_zone * 2
    path_parts: list[str] = []
    for r, row in enumerate(matrix):
        for c, val in enumerate(row):
            if val:
                path_parts.append(f"M{c + quiet_zone},{r + quiet_zone}h1v1h-1z")
    d_attr = "".join(path_parts)
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {total} {total}" '
        f'width="196" height="196" shape-rendering="crispEdges" role="img" aria-label="Parent Access Code QR">'
        f'<rect width="{total}" height="{total}" fill="#ffffff"/>'
        f'<path d="{d_attr}" fill="#11131a"/>'
        f"</svg>"
    )


class ParentAccessCodeManager:
    """Manages root-only offline TOTP Parent Access Code storage, anti-replay, and rate limiting."""

    def __init__(self, auth_path: str | None = None) -> None:
        self.auth_path = auth_path or os.path.join(DEFAULT_CONFIG_DIR, "parent_auth.json")
        self._lock = threading.RLock()
        self._failures: list[float] = []
        self._locked_until_mono: float = 0.0

    def _load_raw(self) -> dict[str, Any]:
        try:
            data = _read_json_nofollow(self.auth_path)
        except (OSError, ValueError, json.JSONDecodeError):
            data = None
        if not isinstance(data, dict):
            return {
                "schema_version": SCHEMA_VERSION,
                "enabled": False,
                "digits": DEFAULT_TOTP_DIGITS,
                "period": DEFAULT_TOTP_PERIOD,
                "issuer": "curfewd",
                "account": "",
                "created_utc": "",
                "last_used_step": 0,
            }
        return data

    def _save_raw(self, payload: dict[str, Any]) -> None:
        out = dict(payload)
        out["schema_version"] = SCHEMA_VERSION
        write_json_atomic(self.auth_path, out, mode=0o600, dir_mode=0o755)

    def _lockout_remaining(self, now_mono: float) -> int:
        if now_mono < self._locked_until_mono:
            return int(self._locked_until_mono - now_mono) + 1
        return 0

    def status(self) -> dict[str, Any]:
        """Return safe public status without ever leaking the TOTP secret."""
        with self._lock:
            raw = self._load_raw()
            enabled = bool(raw.get("enabled") and raw.get("secret_b32"))
            return {
                "enabled": enabled,
                "created_utc": str(raw.get("created_utc", "")) if enabled else "",
                "digits": int(raw.get("digits", DEFAULT_TOTP_DIGITS)),
                "period": int(raw.get("period", DEFAULT_TOTP_PERIOD)),
                "lockout_remaining_sec": self._lockout_remaining(time.monotonic()),
            }

    def is_enabled(self) -> bool:
        return bool(self.status()["enabled"])

    @staticmethod
    def _build_bounded_uri(secret: str, raw_account: str) -> tuple[str, str]:
        """Return (account, otpauth_uri) bounded within Version 9 QR UTF-8 capacity (230 bytes)."""
        acct = (raw_account or "desktop").strip()[:48] or "desktop"
        while True:
            uri = build_otpauth_uri(
                secret,
                account=acct,
                issuer="curfewd",
                digits=DEFAULT_TOTP_DIGITS,
                period=DEFAULT_TOTP_PERIOD,
            )
            if len(uri.encode("utf-8")) <= 230 or len(acct) <= 1:
                return acct, uri
            acct = acct[:-1]

    def setup(
        self,
        enable: bool = True,
        rotate: bool = False,
        account: str = "",
    ) -> dict[str, Any]:
        """Enable, rotate, or disable the offline TOTP Parent Access Code."""
        with self._lock:
            raw = self._load_raw()
            if not enable:
                raw["enabled"] = False
                raw.pop("secret_b32", None)
                raw["created_utc"] = ""
                raw["last_used_step"] = 0
                self._failures.clear()
                self._locked_until_mono = 0.0
                self._save_raw(raw)
                return {
                    "enabled": False,
                    "created_utc": "",
                }

            secret = str(raw.get("secret_b32") or "")
            already_enabled = bool(raw.get("enabled") and secret)
            if already_enabled and not rotate:
                # Never silently re-export an active TOTP secret or reset brute-force lockouts.
                return {
                    "enabled": True,
                    "already_configured": True,
                    "created_utc": str(raw.get("created_utc") or ""),
                }

            secret = generate_totp_secret()
            raw["last_used_step"] = 0
            raw["created_utc"] = utc_now_iso()

            acct_seed = (account or raw.get("account") or socket.gethostname() or "desktop").strip()
            acct, uri = self._build_bounded_uri(secret, acct_seed)
            # Render QR SVG BEFORE persisting state so capacity/encoding failures never leave partial state.
            qr_svg = render_qr_svg(uri)

            raw["enabled"] = True
            raw["secret_b32"] = secret
            raw["digits"] = DEFAULT_TOTP_DIGITS
            raw["period"] = DEFAULT_TOTP_PERIOD
            raw["issuer"] = "curfewd"
            raw["account"] = acct
            self._failures.clear()
            self._locked_until_mono = 0.0
            self._save_raw(raw)

            return {
                "enabled": True,
                "already_configured": False,
                "created_utc": raw["created_utc"],
                "secret_b32": secret,
                "otpauth_uri": uri,
                "qr_svg": qr_svg,
            }

    def check_code(
        self,
        code: str,
        now_ts: float | None = None,
        now_mono: float | None = None,
    ) -> tuple[bool, str, int | None]:
        """Validate a 6-digit TOTP code without burning its anti-replay step yet."""
        with self._lock:
            mono = time.monotonic() if now_mono is None else float(now_mono)
            rem_lock = self._lockout_remaining(mono)
            if rem_lock > 0:
                return False, f"Too many failed attempts; try again in {rem_lock}s", None

            raw = self._load_raw()
            secret = str(raw.get("secret_b32") or "")
            if not (raw.get("enabled") and secret):
                return False, "Parent Access Code is not enabled", None

            digits = int(raw.get("digits", DEFAULT_TOTP_DIGITS))
            period = max(15, int(raw.get("period", DEFAULT_TOTP_PERIOD)))
            cleaned = "".join(ch for ch in str(code or "") if ch.isdigit())
            if len(cleaned) != digits:
                self._record_failure(mono)
                return False, f"Enter a {digits}-digit Parent Access Code", None

            ts = time.time() if now_ts is None else float(now_ts)
            cur_step = int(ts // period)
            last_used = int(raw.get("last_used_step", 0))

            matched_step: int | None = None
            for delta in (0, -1, 1):
                candidate_step = cur_step + delta
                if candidate_step < 0:
                    continue
                expected = compute_totp(secret, candidate_step, digits=digits)
                if hmac.compare_digest(cleaned, expected):
                    matched_step = candidate_step
                    break

            if matched_step is None:
                self._record_failure(mono)
                return False, "Invalid Parent Access Code", None

            if matched_step <= last_used:
                self._record_failure(mono)
                return False, "This code was already used; wait for the next 30-second code", None

            return True, "ok", matched_step

    def consume_step(self, step: int) -> None:
        """Persist a verified TOTP step to enforce single-use anti-replay."""
        with self._lock:
            raw = self._load_raw()
            raw["last_used_step"] = max(int(raw.get("last_used_step", 0)), int(step))
            self._save_raw(raw)
            self._failures.clear()
            self._locked_until_mono = 0.0

    def verify_code(
        self,
        code: str,
        now_ts: float | None = None,
        now_mono: float | None = None,
    ) -> tuple[bool, str]:
        """Verify a 6-digit TOTP code with rate limiting and single-use anti-replay."""
        ok, msg, step = self.check_code(code, now_ts=now_ts, now_mono=now_mono)
        if not ok or step is None:
            return False, msg
        self.consume_step(step)
        return True, "ok"

    def _record_failure(self, now_mono: float) -> None:
        cutoff = now_mono - _FAILURE_WINDOW_SEC
        self._failures = [t for t in self._failures if t >= cutoff]
        self._failures.append(now_mono)
        if len(self._failures) >= _MAX_FAILURES:
            self._locked_until_mono = now_mono + _LOCKOUT_SEC
            self._failures.clear()
