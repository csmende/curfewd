"""Installed desktop application scanner for the curfewd App Limits picker."""

import configparser
import os
import re
import shlex
import stat
from typing import Any

# Friendly metadata overrides for popular desktop/Flatpak apps and games
_KNOWN_FLATPAK_OVERRIDES: dict[str, tuple[str, str, str]] = {
    "org.vinegarhq.sober": ("sober", "Roblox (Sober)", "Gaming"),
    "com.lunarclient.lunarclient": ("lunarclient", "Minecraft (Lunar Client)", "Gaming"),
    "com.modrinth.modrinthapp": ("modrinth", "Minecraft (Modrinth)", "Gaming"),
    "org.prismlauncher.prismlauncher": ("prismlauncher", "Minecraft (Prism)", "Gaming"),
    "com.heroicgameslauncher.hgl": ("heroic", "Heroic Games Launcher", "Gaming"),
    "net.lutris.lutris": ("lutris", "Lutris", "Gaming"),
    "com.discordapp.discord": ("discord", "Discord", "Social"),
    "com.spotify.client": ("spotify", "Spotify", "Media"),
    "com.obsproject.studio": ("obs", "OBS Studio", "Creative"),
    "com.valvesoftware.steam": ("steam", "Steam", "Gaming"),
    "org.libretro.retroarch": ("retroarch", "RetroArch", "Gaming"),
    "sh.ppy.osu": ("osu", "osu!", "Gaming"),
    "com.brave.browser": ("brave", "Brave Browser", "Browser"),
    "org.mozilla.firefox": ("firefox", "Firefox", "Browser"),
    "com.google.chrome": ("chrome", "Google Chrome", "Browser"),
}

_KNOWN_BIN_OVERRIDES: dict[str, tuple[str, str]] = {
    "steam": ("Steam", "Gaming"),
    "steam-runtime": ("Steam", "Gaming"),
    "minecraft-launcher": ("Minecraft", "Gaming"),
    "discord": ("Discord", "Social"),
    "spotify": ("Spotify", "Media"),
    "google-chrome": ("Google Chrome", "Browser"),
    "google-chrome-stable": ("Google Chrome", "Browser"),
    "chromium": ("Chromium", "Browser"),
    "chromium-browser": ("Chromium", "Browser"),
    "firefox": ("Firefox", "Browser"),
    "brave-browser": ("Brave Browser", "Browser"),
    "retroarch": ("RetroArch", "Gaming"),
    "lutris": ("Lutris", "Gaming"),
    "obs": ("OBS Studio", "Creative"),
    "vlc": ("VLC Media Player", "Media"),
    "telegram-desktop": ("Telegram", "Social"),
}

_IGNORED_PATTERNS = {
    "curfewd",
    "curfewd-ui",
    "curfewd-tray",
    "curfewctl",
    "xdg-open",
    "sh",
    "bash",
    "dash",
    "env",
    "python",
    "python3",
    "pkexec",
    "sudo",
    "mintupdate",
    "gnome-control-center",
    "cinnamon-settings",
    "xfce4-settings-manager",
    "systemsettings",
}

_CATEGORY_ORDER = {
    "Gaming": 0,
    "Social": 1,
    "Media": 2,
    "Browser": 3,
    "Creative": 4,
    "Productivity": 5,
    "App": 6,
}

DEFAULT_CURATED_APPS: list[dict[str, Any]] = [
    {"pattern": "steam", "label": "Steam", "category": "Gaming", "installed": False},
    {
        "pattern": "minecraft|lunarclient|modrinth|prismlauncher",
        "label": "Minecraft",
        "category": "Gaming",
        "installed": False,
    },
    {"pattern": "sober|roblox", "label": "Roblox", "category": "Gaming", "installed": False},
    {"pattern": "discord", "label": "Discord", "category": "Social", "installed": False},
    {"pattern": "spotify", "label": "Spotify", "category": "Media", "installed": False},
    {
        "pattern": "heroic",
        "label": "Heroic Games Launcher",
        "category": "Gaming",
        "installed": False,
    },
]


_STEAM_RUNGAME_RE = re.compile(r"steam://rungameid/(\d+)", re.IGNORECASE)


_MAX_DESKTOP_BYTES = 131072
_MAX_ENTRIES_PER_DIR = 2000


def _default_desktop_dirs(target_home: str | None = None) -> list[str]:
    dirs = [
        "/var/lib/flatpak/exports/share/applications",
        "/usr/local/share/applications",
        "/usr/share/applications",
    ]
    homes: list[str] = []
    if target_home and os.path.isdir(target_home):
        homes.append(target_home)
    elif os.geteuid() != 0:
        caller_home = os.path.expanduser("~")
        if caller_home and os.path.isdir(caller_home):
            homes.append(caller_home)
    for home in homes:
        dirs.insert(1, os.path.join(home, ".local/share/flatpak/exports/share/applications"))
        dirs.append(os.path.join(home, ".local/share/applications"))
    return dirs


def _allowed_roots_for(search_dirs: list[str], target_home: str | None) -> tuple[str, ...]:
    roots = [
        os.path.realpath("/var/lib/flatpak"),
        os.path.realpath("/usr/share"),
        os.path.realpath("/usr/local/share"),
    ]
    if target_home and os.path.isdir(target_home):
        roots.append(os.path.realpath(target_home))
    for d in search_dirs:
        if os.path.isdir(d):
            roots.append(os.path.realpath(d))
    return tuple(dict.fromkeys(roots))


def _path_within_roots(real_path: str, allowed_roots: tuple[str, ...]) -> bool:
    for root in allowed_roots:
        try:
            if os.path.commonpath([real_path, root]) == root:
                return True
        except ValueError:
            continue
    return False


def _read_regular_desktop_bounded(real_path: str) -> str | None:
    """Open real_path non-blocking, fstat regular file + size cap, and read bounded bytes."""
    flags = os.O_RDONLY | getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_CLOEXEC", 0)
    try:
        fd = os.open(real_path, flags)
    except OSError:
        return None
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode) or st.st_size > _MAX_DESKTOP_BYTES:
            return None
        chunks: list[bytes] = []
        total = 0
        while True:
            chunk = os.read(fd, 8192)
            if not chunk:
                break
            total += len(chunk)
            if total > _MAX_DESKTOP_BYTES:
                return None
            chunks.append(chunk)
        return b"".join(chunks).decode("utf-8")
    except (OSError, UnicodeDecodeError):
        return None
    finally:
        os.close(fd)


def _is_flatpak_entry(path: str, exec_line: str) -> bool:
    if "flatpak" in path.lower():
        return True
    try:
        tokens = shlex.split(exec_line)
    except ValueError:
        tokens = exec_line.split()
    return bool(tokens and os.path.basename(tokens[0]) == "flatpak" and "run" in tokens[1:4])


def _map_category(raw_categories: str) -> str:
    cats = {c.strip().lower() for c in raw_categories.split(";") if c.strip()}
    if "game" in cats or "emulator" in cats:
        return "Gaming"
    if cats & {"instantmessaging", "chat", "ircclient", "telephony", "videoconference"}:
        return "Social"
    if cats & {"webbrowser"}:
        return "Browser"
    if cats & {"audiovideo", "audio", "video", "music", "player", "tv"}:
        return "Media"
    if cats & {
        "graphics",
        "2dgraphics",
        "3dgraphics",
        "photography",
        "audiovideoediting",
        "videoediting",
    }:
        return "Creative"
    if cats & {"office", "development", "education", "science"}:
        return "Productivity"
    return "App"


def _extract_pattern_from_exec(
    exec_line: str, flatpak_id: str = ""
) -> tuple[str, str | None, str | None]:
    """Return (process_pattern, optional_label_override, optional_category_override)."""
    fp_low = flatpak_id.strip().lower()
    if fp_low in _KNOWN_FLATPAK_OVERRIDES:
        return _KNOWN_FLATPAK_OVERRIDES[fp_low]

    try:
        tokens = shlex.split(exec_line)
    except ValueError:
        tokens = exec_line.split()

    cleaned: list[str] = []
    i = 0
    while i < len(tokens):
        tok = tokens[i]
        if tok.startswith("%"):
            i += 1
            continue
        if not cleaned and os.path.basename(tok) == "env":
            i += 1
            while i < len(tokens) and "=" in tokens[i] and not tokens[i].startswith("-"):
                i += 1
            continue
        cleaned.append(tok)
        i += 1

    if not cleaned:
        return "", None, None

    cmd = os.path.basename(cleaned[0])

    if cmd in ("sh", "bash", "dash") and len(cleaned) >= 3 and cleaned[1] == "-c":
        try:
            inner = shlex.split(cleaned[2])
        except ValueError:
            inner = cleaned[2].split()
        idx = 0
        while idx < len(inner) and (
            inner[idx].startswith("%") or ("=" in inner[idx] and not inner[idx].startswith("-"))
        ):
            idx += 1
        if idx < len(inner):
            cleaned = [tok for tok in inner[idx:] if not tok.startswith("%")]
            if not cleaned:
                return "", None, None
            cmd = os.path.basename(cleaned[0])

    if cmd == "flatpak" and "run" in cleaned[1:3]:
        for arg in cleaned[2:]:
            if arg == "run" or arg.startswith(("-", "@@")):
                continue
            app_id = arg.strip()
            low_id = app_id.lower()
            if low_id in _KNOWN_FLATPAK_OVERRIDES:
                return _KNOWN_FLATPAK_OVERRIDES[low_id]
            tail = app_id.rsplit(".", 1)[-1].lower()
            tail = re.sub(r"[^a-z0-9._-]", "", tail)
            if tail:
                return tail, None, None

    low_cmd = cmd.lower()
    if low_cmd in _KNOWN_BIN_OVERRIDES:
        lbl, cat = _KNOWN_BIN_OVERRIDES[low_cmd]
        pat = (
            "steam"
            if low_cmd == "steam-runtime"
            else ("chrome" if "chrome" in low_cmd else low_cmd)
        )
        return pat, lbl, cat

    pat = re.sub(r"[^a-z0-9._-]", "", low_cmd)
    return pat, None, None


def parse_desktop_file(
    path: str, allowed_roots: tuple[str, ...] | None = None
) -> dict[str, Any] | None:
    """Parse one freedesktop .desktop entry into an App Limits picker candidate."""
    real_path = os.path.realpath(path)
    if not real_path.endswith(".desktop"):
        return None
    if allowed_roots is not None and not _path_within_roots(real_path, allowed_roots):
        return None
    raw_text = _read_regular_desktop_bounded(real_path)
    if raw_text is None:
        return None

    cp = configparser.ConfigParser(interpolation=None, strict=False)
    try:
        cp.read_string(raw_text)
    except configparser.Error:
        return None

    if not cp.has_section("Desktop Entry"):
        return None
    sec = cp["Desktop Entry"]
    if sec.get("Type", "").strip() != "Application":
        return None
    if sec.get("NoDisplay", "").strip().lower() == "true":
        return None
    if sec.get("Hidden", "").strip().lower() == "true":
        return None
    if sec.get("Terminal", "").strip().lower() == "true":
        return None

    name = sec.get("Name", "").strip()
    exec_line = sec.get("Exec", "").strip()
    if not name or not exec_line:
        return None

    cats_raw = sec.get("Categories", "")
    cats_set = {c.strip().lower() for c in cats_raw.split(";") if c.strip()}
    if cats_set & {
        "settings",
        "desktopsettings",
        "hardwaresettings",
        "system",
        "screensaver",
    } and not (cats_set & {"game", "webbrowser", "audiovideo", "instantmessaging"}):
        return None

    # Handle individual Steam game shortcuts (`Exec=steam steam://rungameid/413150`)
    steam_match = _STEAM_RUNGAME_RE.search(exec_line)
    if steam_match:
        app_id = steam_match.group(1)
        return {
            "pattern": f"steam_app_{app_id}",
            "label": name,
            "category": "Gaming",
            "flatpak_id": "",
            "steam_app_id": app_id,
            "installed": True,
        }

    flatpak_id = sec.get("X-Flatpak", "").strip()
    if not flatpak_id and _is_flatpak_entry(path, exec_line):
        fname = os.path.basename(path)
        if fname.endswith(".desktop") and fname.count(".") >= 3:
            flatpak_id = fname[: -len(".desktop")]

    pattern, label_ovr, cat_ovr = _extract_pattern_from_exec(exec_line, flatpak_id=flatpak_id)
    if not pattern or pattern in _IGNORED_PATTERNS or len(pattern) < 2:
        return None

    category = cat_ovr or _map_category(cats_raw)
    label = label_ovr or name
    return {
        "pattern": pattern,
        "label": label,
        "category": category,
        "flatpak_id": flatpak_id,
        "steam_app_id": "",
        "installed": True,
    }


def scan_installed_apps(
    search_dirs: list[str] | None = None,
    include_curated_fallback: bool = True,
    target_home: str | None = None,
) -> list[dict[str, Any]]:
    """Scan installed desktop applications (including target child's home) sorted & deduplicated."""
    dirs = (
        search_dirs if search_dirs is not None else _default_desktop_dirs(target_home=target_home)
    )
    allowed_roots = _allowed_roots_for(dirs, target_home)
    seen_patterns: dict[str, dict[str, Any]] = {}

    for directory in dirs:
        if not os.path.isdir(directory):
            continue
        real_dir = os.path.realpath(directory)
        if not _path_within_roots(real_dir, allowed_roots):
            continue
        try:
            entries = sorted(os.listdir(real_dir))[:_MAX_ENTRIES_PER_DIR]
        except OSError:
            continue
        for fname in entries:
            if not fname.endswith(".desktop"):
                continue
            item = parse_desktop_file(os.path.join(real_dir, fname), allowed_roots=allowed_roots)
            if item is None:
                continue
            key = item["pattern"].lower()
            if key not in seen_patterns:
                seen_patterns[key] = item

    if include_curated_fallback and not seen_patterns:
        for curated in DEFAULT_CURATED_APPS:
            seen_patterns[curated["pattern"].lower()] = dict(curated)

    results = list(seen_patterns.values())
    results.sort(
        key=lambda a: (
            0 if a.get("installed") else 1,
            _CATEGORY_ORDER.get(a.get("category", "App"), 99),
            a.get("label", "").lower(),
        )
    )
    return results
