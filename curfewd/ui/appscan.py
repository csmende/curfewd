"""Installed desktop application scanner for the curfewd App Limits picker."""

import configparser
import os
import re
import shlex
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
    {"pattern": "minecraft|lunarclient|modrinth|prismlauncher", "label": "Minecraft", "category": "Gaming", "installed": False},
    {"pattern": "sober|roblox", "label": "Roblox", "category": "Gaming", "installed": False},
    {"pattern": "discord", "label": "Discord", "category": "Social", "installed": False},
    {"pattern": "spotify", "label": "Spotify", "category": "Media", "installed": False},
    {"pattern": "heroic", "label": "Heroic Games Launcher", "category": "Gaming", "installed": False},
]


def _default_desktop_dirs() -> list[str]:
    home = os.path.expanduser("~")
    return [
        "/var/lib/flatpak/exports/share/applications",
        os.path.join(home, ".local/share/flatpak/exports/share/applications"),
        "/usr/local/share/applications",
        "/usr/share/applications",
        os.path.join(home, ".local/share/applications"),
    ]


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
    if cats & {"graphics", "2dgraphics", "3dgraphics", "photography", "audioVideoediting", "videoediting"}:
        return "Creative"
    if cats & {"office", "development", "education", "science"}:
        return "Productivity"
    return "App"


def _extract_pattern_from_exec(exec_line: str, flatpak_id: str = "") -> tuple[str, str | None, str | None]:
    """Return (process_pattern, optional_label_override, optional_category_override)."""
    fp_low = flatpak_id.strip().lower()
    if fp_low in _KNOWN_FLATPAK_OVERRIDES:
        return _KNOWN_FLATPAK_OVERRIDES[fp_low]

    try:
        tokens = shlex.split(exec_line)
    except ValueError:
        tokens = exec_line.split()

    # Strip field codes (%u, %F, etc.) and leading `env VAR=val`
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

    # Handle `flatpak run [--flags] <app.id>`
    if cmd == "flatpak" and "run" in cleaned[1:3]:
        for arg in cleaned[2:]:
            if arg == "run" or arg.startswith("-") or arg.startswith("--") or arg.startswith("@@"):
                continue
            app_id = arg.strip()
            low_id = app_id.lower()
            if low_id in _KNOWN_FLATPAK_OVERRIDES:
                return _KNOWN_FLATPAK_OVERRIDES[low_id]
            # Use the trailing app segment (e.g. com.example.MyApp -> myapp)
            tail = app_id.rsplit(".", 1)[-1].lower()
            tail = re.sub(r"[^a-z0-9._-]", "", tail)
            if tail:
                return tail, None, None

    low_cmd = cmd.lower()
    if low_cmd in _KNOWN_BIN_OVERRIDES:
        lbl, cat = _KNOWN_BIN_OVERRIDES[low_cmd]
        pat = "steam" if low_cmd == "steam-runtime" else ("chrome" if "chrome" in low_cmd else low_cmd)
        return pat, lbl, cat

    pat = re.sub(r"[^a-z0-9._-]", "", low_cmd)
    return pat, None, None


def parse_desktop_file(path: str) -> dict[str, Any] | None:
    """Parse one freedesktop .desktop entry into an App Limits picker candidate."""
    if not os.path.isfile(path) or os.path.islink(path):
        return None
    cp = configparser.ConfigParser(interpolation=None, strict=False)
    try:
        cp.read(path, encoding="utf-8")
    except (configparser.Error, OSError, UnicodeDecodeError):
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
    # Skip system/settings utilities that parents wouldn't put app timers on
    cats_set = {c.strip().lower() for c in cats_raw.split(";") if c.strip()}
    if cats_set & {"settings", "desktopsettings", "hardwaresettings", "system", "screensaver"} and not (
        cats_set & {"game", "webbrowser", "audiovideo", "instantmessaging"}
    ):
        return None

    flatpak_id = sec.get("X-Flatpak", "").strip()
    if not flatpak_id:
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
        "installed": True,
    }


def scan_installed_apps(search_dirs: list[str] | None = None, include_curated_fallback: bool = True) -> list[dict[str, Any]]:
    """Scan installed desktop applications and return a sorted, deduplicated list."""
    dirs = search_dirs if search_dirs is not None else _default_desktop_dirs()
    seen_patterns: dict[str, dict[str, Any]] = {}

    for directory in dirs:
        if not os.path.isdir(directory):
            continue
        try:
            entries = sorted(os.listdir(directory))
        except OSError:
            continue
        for fname in entries:
            if not fname.endswith(".desktop"):
                continue
            item = parse_desktop_file(os.path.join(directory, fname))
            if item is None:
                continue
            key = item["pattern"].lower()
            if key not in seen_patterns:
                seen_patterns[key] = item

    # If very few installed apps were found (e.g. minimal environment), add curated suggestions not already present
    if include_curated_fallback:
        existing_labels = {v["label"].lower() for v in seen_patterns.values()}
        for curated in DEFAULT_CURATED_APPS:
            pat_key = curated["pattern"].lower()
            primary_tok = pat_key.split("|")[0]
            if pat_key in seen_patterns or primary_tok in seen_patterns:
                continue
            if curated["label"].lower() in existing_labels:
                continue
            if not seen_patterns:
                seen_patterns[pat_key] = dict(curated)

    results = list(seen_patterns.values())
    results.sort(
        key=lambda a: (
            0 if a.get("installed") else 1,
            _CATEGORY_ORDER.get(a.get("category", "App"), 99),
            a.get("label", "").lower(),
        )
    )
    return results
