"""Pure parsers and formatters for durations, weekdays, curfew windows, and usernames."""

import re
from datetime import datetime, time, timedelta, timezone

WEEKDAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
WEEKDAY_TO_INDEX = {name: idx for idx, name in enumerate(WEEKDAYS)}
ISO_NUM_TO_WEEKDAY = {str(idx + 1): name for idx, name in enumerate(WEEKDAYS)}

_DAY_ALIASES = {
    "mon": "mon", "monday": "mon", "1": "mon",
    "tue": "tue", "tues": "tue", "tuesday": "tue", "2": "tue",
    "wed": "wed", "weds": "wed", "wednesday": "wed", "3": "wed",
    "thu": "thu", "thur": "thu", "thurs": "thu", "thursday": "thu", "4": "thu",
    "fri": "fri", "friday": "fri", "5": "fri",
    "sat": "sat", "saturday": "sat", "6": "sat",
    "sun": "sun", "sunday": "sun", "7": "sun",
}

_USERNAME_RE = re.compile(r"^[a-z_][a-z0-9._-]{0,31}$")
_DURATION_TOKEN_RE = re.compile(r"(\d+)\s*([dhms])", re.IGNORECASE)
_HHMM_RE = re.compile(r"^(\d{1,2}):(\d{2})$")
_WINDOW_RE = re.compile(r"^(!)?(\d{1,2}:\d{2})-(\d{1,2}:\d{2})$")

SECONDS_PER_DAY = 86400


def validate_username(username: str) -> str:
    """Validate a Linux username strictly to prevent path traversal or injection."""
    if not isinstance(username, str) or not _USERNAME_RE.match(username):
        raise ValueError(f"Invalid Linux username: {username!r}")
    return username


def parse_duration(raw: str) -> int:
    """Parse human durations like '1h30m', '45m', '90s', '2h', '3600', or 'unlimited' into seconds."""
    if isinstance(raw, int) and not isinstance(raw, bool):
        if raw < 0:
            raise ValueError(f"Duration must be non-negative: {raw}")
        return raw
    if not isinstance(raw, str):
        raise ValueError(f"Invalid duration: {raw!r}")
    text = raw.strip().lower()
    if not text:
        raise ValueError("Duration cannot be empty")
    if text in ("unlimited", "none", "inf"):
        return SECONDS_PER_DAY
    if text.isdigit():
        return int(text)

    total = 0
    pos = 0
    for match in _DURATION_TOKEN_RE.finditer(text):
        gap = text[pos:match.start()].strip()
        if gap:
            raise ValueError(f"Invalid duration format: {raw!r}")
        value = int(match.group(1))
        unit = match.group(2).lower()
        mult = {"d": 86400, "h": 3600, "m": 60, "s": 1}[unit]
        total += value * mult
        pos = match.end()

    if pos == 0 or text[pos:].strip():
        raise ValueError(f"Invalid duration format: {raw!r}")
    return total


def parse_delta(raw: str) -> tuple[str, int]:
    """Parse a balance adjustment like '+30m', '-15m', '=2h', or '30m' -> (mode, signed_seconds).

    Returns ("add", +1800), ("add", -900), or ("set", 7200).
    """
    if not isinstance(raw, str) or not raw.strip():
        raise ValueError(f"Invalid time delta: {raw!r}")
    text = raw.strip()
    if text.startswith("="):
        return "set", parse_duration(text[1:])
    if text.startswith("+"):
        return "add", parse_duration(text[1:])
    if text.startswith("-"):
        return "add", -parse_duration(text[1:])
    return "add", parse_duration(text)


def format_duration(seconds: int, include_seconds: bool = False) -> str:
    """Format a second count into concise human text like '1h 30m' or '45s'."""
    if seconds < 0:
        return "-" + format_duration(-seconds, include_seconds=include_seconds)
    if seconds == 0:
        return "0m" if not include_seconds else "0s"
    days, rem = divmod(int(seconds), 86400)
    hours, rem = divmod(rem, 3600)
    minutes, secs = divmod(rem, 60)
    parts = []
    if days:
        parts.append(f"{days}d")
    if hours:
        parts.append(f"{hours}h")
    if minutes:
        parts.append(f"{minutes}m")
    if (include_seconds and secs) or not parts:
        parts.append(f"{secs}s")
    return " ".join(parts)


def parse_days(raw: str) -> list[str]:
    """Parse day expressions like 'all', 'weekdays', 'weekends', 'mon-fri', 'mon-thu,sun'."""
    if not isinstance(raw, str) or not raw.strip():
        raise ValueError(f"Invalid day specification: {raw!r}")
    text = raw.strip().lower()
    if text in ("all", "everyday", "*"):
        return list(WEEKDAYS)
    if text in ("weekdays", "weekday"):
        return list(WEEKDAYS[:5])
    if text in ("weekends", "weekend"):
        return list(WEEKDAYS[5:])

    selected: list[str] = []
    for part in text.split(","):
        token = part.strip()
        if not token:
            continue
        if "-" in token:
            start_raw, end_raw = token.split("-", 1)
            start_day = _DAY_ALIASES.get(start_raw.strip())
            end_day = _DAY_ALIASES.get(end_raw.strip())
            if not start_day or not end_day:
                raise ValueError(f"Unknown weekday range: {token!r}")
            s_idx = WEEKDAY_TO_INDEX[start_day]
            e_idx = WEEKDAY_TO_INDEX[end_day]
            if s_idx <= e_idx:
                span = WEEKDAYS[s_idx:e_idx + 1]
            else:
                span = WEEKDAYS[s_idx:] + WEEKDAYS[:e_idx + 1]
            for d in span:
                if d not in selected:
                    selected.append(d)
        else:
            day = _DAY_ALIASES.get(token)
            if not day:
                raise ValueError(f"Unknown weekday: {token!r}")
            if day not in selected:
                selected.append(day)

    if not selected:
        raise ValueError(f"No valid weekdays parsed from {raw!r}")
    return selected


def parse_hhmm_to_minutes(raw: str) -> int:
    """Parse 'HH:MM' (00:00 through 24:00) into minutes from midnight."""
    match = _HHMM_RE.match(raw.strip())
    if not match:
        raise ValueError(f"Invalid HH:MM time: {raw!r}")
    hour = int(match.group(1))
    minute = int(match.group(2))
    if hour == 24 and minute == 0:
        return 1440
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        raise ValueError(f"Time out of range: {raw!r}")
    return hour * 60 + minute


def minutes_to_hhmm(minutes: int) -> str:
    """Format minutes from midnight (0..1440) as zero-padded 'HH:MM'."""
    if not (0 <= minutes <= 1440):
        raise ValueError(f"Minutes out of range: {minutes}")
    if minutes == 1440:
        return "24:00"
    h, m = divmod(minutes, 60)
    return f"{h:02d}:{m:02d}"


def parse_windows(raw: str) -> list[dict]:
    """Parse comma-separated windows like '08:00-22:00,!22:00-22:30' or 'none'/'all'."""
    if not isinstance(raw, str):
        raise ValueError(f"Invalid window specification: {raw!r}")
    text = raw.strip().lower()
    if text in ("none", "off", "blocked", ""):
        return []
    if text in ("all", "24/7", "always", "00:00-24:00"):
        return [{"start": "00:00", "end": "24:00", "unaccounted": False}]

    windows = []
    for part in raw.split(","):
        token = part.strip()
        if not token:
            continue
        match = _WINDOW_RE.match(token)
        if not match:
            raise ValueError(f"Invalid window {token!r}; expected [!]HH:MM-HH:MM")
        unaccounted = bool(match.group(1))
        start_min = parse_hhmm_to_minutes(match.group(2))
        end_min = parse_hhmm_to_minutes(match.group(3))
        if end_min <= start_min:
            raise ValueError(f"Window end must be after start: {token!r}")
        windows.append(
            {
                "start": minutes_to_hhmm(start_min),
                "end": minutes_to_hhmm(end_min),
                "unaccounted": unaccounted,
            }
        )

    windows.sort(key=lambda w: (parse_hhmm_to_minutes(w["start"]), parse_hhmm_to_minutes(w["end"])))
    # Reject overlapping windows to keep policy unambiguous
    for prev, curr in zip(windows, windows[1:]):
        if parse_hhmm_to_minutes(curr["start"]) < parse_hhmm_to_minutes(prev["end"]):
            raise ValueError(f"Overlapping windows: {prev['start']}-{prev['end']} and {curr['start']}-{curr['end']}")
    return windows


def parse_until_timestamp(raw: str, now_local: datetime | None = None) -> str:
    """Parse '--until' value ('HH:MM', 'tomorrow', or ISO-8601) into UTC ISO-8601 string."""
    now = now_local or datetime.now().astimezone()
    if now.tzinfo is None:
        now = now.astimezone()
    text = raw.strip()
    low = text.lower()
    if low == "today":
        # End of current local day (next local midnight)
        next_day = (now + timedelta(days=1)).date()
        target = datetime.combine(next_day, time(0, 0), tzinfo=now.tzinfo)
        return target.astimezone(timezone.utc).isoformat()
    if low == "tomorrow":
        next_day = (now + timedelta(days=1)).date()
        target = datetime.combine(next_day, time(8, 0), tzinfo=now.tzinfo)
        return target.astimezone(timezone.utc).isoformat()
    if _HHMM_RE.match(text):
        mins = parse_hhmm_to_minutes(text)
        if mins == 1440:
            next_day = (now + timedelta(days=1)).date()
            target = datetime.combine(next_day, time(0, 0), tzinfo=now.tzinfo)
        else:
            h, m = divmod(mins, 60)
            target = datetime.combine(now.date(), time(h, m), tzinfo=now.tzinfo)
            if target <= now:
                target += timedelta(days=1)
        return target.astimezone(timezone.utc).isoformat()
    parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=now.tzinfo)
    return parsed.astimezone(timezone.utc).isoformat()
