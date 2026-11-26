"""App Limits & passive usage process monitor with Flatpak/Steam/cgroup/foreground awareness (Spec 006)."""

import logging
import os
import re
from dataclasses import dataclass, field
from typing import Any

try:
    import psutil
except ImportError:  # pragma: no cover - optional in minimal test environments
    psutil = None

from curfewd.models import AppLimitRule, CategoryLimitRule

logger = logging.getLogger("curfewd.applimits")

# Support systemd \x2d escaped hyphens, hex-UUID .scope, and @<hex>.service units.
_FLATPAK_CGROUP_RE = re.compile(r"app-flatpak-([A-Za-z0-9_.\\-]+)-[0-9a-fA-F]+\.scope")
_SYSTEMD_APP_CGROUP_RE = re.compile(
    r"app-(?:gnome|kde|glib|flatpak)?-?([A-Za-z0-9_.\\-]+)(?:-[0-9a-fA-F]+\.scope|@[0-9a-fA-F]+\.service)"
)
_SYSTEMD_ESC_RE = re.compile(r"\\x([0-9a-fA-F]{2})")

# Critical session, compositor, locker, and curfewd processes that enforce_kill must NEVER signal.
_PROTECTED_PROCESS_NAMES = frozenset(
    {
        "systemd",
        "dbus-daemon",
        "dbus-broker",
        "pipewire",
        "wireplumber",
        "pulseaudio",
        "Xorg",
        "Xwayland",
        "cinnamon",
        "cinnamon-session",
        "cinnamon-screensaver",
        "gnome-shell",
        "gnome-session-binary",
        "gnome-screensaver",
        "plasmashell",
        "kwin_x11",
        "kwin_wayland",
        "kscreenlocker_greet",
        "ksmserver",
        "xfwm4",
        "xfce4-session",
        "xfce4-screensaver",
        "mate-session",
        "mate-screensaver",
        "light-locker",
        "xscreensaver",
        "sway",
        "swaylock",
        "hyprland",
        "hyprlock",
        "wayfire",
        "curfewd",
        "curfewd-tray",
        "curfewd-ui",
        "curfewctl",
        "polkit-gnome-authentication-agent-1",
        "polkit-kde-authentication-agent-1",
        "polkit-mate-authentication-agent-1",
        "lxqt-policykit-agent",
    }
)

# Built-in category hints (native names + canonical Flatpak IDs) for passive insights & categories
_BUILTIN_PROCESS_CATALOG: dict[str, tuple[str, str, str]] = {
    "steam": ("steam", "Steam", "Gaming"),
    "com.valvesoftware.steam": ("steam", "Steam", "Gaming"),
    "sober": ("sober", "Roblox (Sober)", "Gaming"),
    "org.vinegarhq.sober": ("sober", "Roblox (Sober)", "Gaming"),
    "lunarclient": ("lunarclient", "Minecraft (Lunar Client)", "Gaming"),
    "com.lunarclient.lunarclient": ("lunarclient", "Minecraft (Lunar Client)", "Gaming"),
    "modrinth": ("modrinth", "Minecraft (Modrinth)", "Gaming"),
    "com.modrinth.modrinthapp": ("modrinth", "Minecraft (Modrinth)", "Gaming"),
    "prismlauncher": ("prismlauncher", "Minecraft (Prism)", "Gaming"),
    "org.prismlauncher.prismlauncher": ("prismlauncher", "Minecraft (Prism)", "Gaming"),
    "minecraft": ("minecraft", "Minecraft", "Gaming"),
    "heroic": ("heroic", "Heroic Games Launcher", "Gaming"),
    "com.heroicgameslauncher.hgl": ("heroic", "Heroic Games Launcher", "Gaming"),
    "lutris": ("lutris", "Lutris", "Gaming"),
    "net.lutris.lutris": ("lutris", "Lutris", "Gaming"),
    "retroarch": ("retroarch", "RetroArch", "Gaming"),
    "org.libretro.retroarch": ("retroarch", "RetroArch", "Gaming"),
    "sh.ppy.osu": ("osu", "osu!", "Gaming"),
    "discord": ("discord", "Discord", "Social"),
    "com.discordapp.discord": ("discord", "Discord", "Social"),
    "telegram-desktop": ("telegram-desktop", "Telegram", "Social"),
    "telegram-deskto": ("telegram-desktop", "Telegram", "Social"),
    "org.telegram.desktop": ("telegram-desktop", "Telegram", "Social"),
    "spotify": ("spotify", "Spotify", "Media"),
    "com.spotify.client": ("spotify", "Spotify", "Media"),
    "vlc": ("vlc", "VLC Media Player", "Media"),
    "org.videolan.vlc": ("vlc", "VLC Media Player", "Media"),
    "firefox": ("firefox", "Firefox", "Browser"),
    "org.mozilla.firefox": ("firefox", "Firefox", "Browser"),
    "chrome": ("chrome", "Google Chrome", "Browser"),
    "com.google.chrome": ("chrome", "Google Chrome", "Browser"),
    "chromium": ("chromium", "Chromium", "Browser"),
    "brave": ("brave", "Brave Browser", "Browser"),
    "com.brave.browser": ("brave", "Brave Browser", "Browser"),
    "obs": ("obs", "OBS Studio", "Creative"),
    "com.obsproject.studio": ("obs", "OBS Studio", "Creative"),
    "blender": ("blender", "Blender", "Creative"),
    "gimp": ("gimp", "GIMP", "Creative"),
    "code": ("code", "VS Code", "Productivity"),
    "soffice.bin": ("libreoffice", "LibreOffice", "Productivity"),
    "anki": ("anki", "Anki", "Productivity"),
    "net.ankiweb.anki": ("anki", "Anki", "Productivity"),
}


@dataclass
class ProcessScanResult:
    """Single-pass /proc scan result powering app limits, category budgets, homework blocks, and insights."""

    rule_matches: list[dict[str, Any]] = field(default_factory=list)
    category_matches: list[dict[str, Any]] = field(default_factory=list)
    passive_apps: list[tuple[str, str, str]] = field(default_factory=list)
    exempt_pids: set[int] = field(default_factory=set)

    @property
    def running_patterns(self) -> list[str]:
        return sorted({m["pattern"] for m in self.rule_matches})

    @property
    def running_labels(self) -> list[str]:
        return sorted({m["label"] for m in self.rule_matches})

    @property
    def running_categories(self) -> list[str]:
        cats = {m["category"] for m in self.category_matches if m.get("category")}
        cats.update(
            m["category"]
            for m in self.rule_matches
            if m.get("category") and not m.get("always_allowed")
        )
        return sorted(cats)


def _unescape_systemd_unit(token: str) -> str:
    return _SYSTEMD_ESC_RE.sub(lambda m: chr(int(m.group(1), 16)), token)


def _is_protected_process(pid: int, name: str, exe: str = "") -> bool:
    if pid <= 1 or pid == os.getpid():
        return True
    base_name = str(name or "").strip()
    base_exe = os.path.basename(str(exe or "").strip())
    return (
        base_name in _PROTECTED_PROCESS_NAMES
        or base_name.lower() in _PROTECTED_PROCESS_NAMES
        or base_exe in _PROTECTED_PROCESS_NAMES
        or base_exe.lower() in _PROTECTED_PROCESS_NAMES
    )


def read_proc_cgroup_app(pid: int, proc_root: str = "/proc") -> tuple[str, str]:
    """Return (flatpak_id, systemd_scope_app) extracted safely from /proc/<pid>/cgroup."""
    if not pid or pid <= 0:
        return "", ""
    path = os.path.join(proc_root, str(pid), "cgroup")
    try:
        with open(path, encoding="utf-8", errors="ignore") as fh:
            text = fh.read(4096)
    except OSError:
        return "", ""

    fp_match = _FLATPAK_CGROUP_RE.search(text)
    if fp_match:
        return _unescape_systemd_unit(fp_match.group(1)), ""
    sc_match = _SYSTEMD_APP_CGROUP_RE.search(text)
    if sc_match:
        return "", _unescape_systemd_unit(sc_match.group(1))
    return "", ""


def read_proc_steam_appid(pid: int, proc_root: str = "/proc") -> str:
    """Inspect ONLY the SteamAppId= variable in /proc/<pid>/environ (never exposes other env)."""
    if not pid or pid <= 0:
        return ""
    path = os.path.join(proc_root, str(pid), "environ")
    try:
        with open(path, "rb") as fh:
            raw = fh.read(131072)
    except OSError:
        return ""
    for entry in raw.split(b"\x00"):
        if entry.startswith((b"SteamAppId=", b"SteamGameId=")):
            val = entry.split(b"=", 1)[1].decode("ascii", errors="ignore").strip()
            if val.isdigit() and val != "0":
                return val
    return ""


def _is_pid_related(pid: int, target_pid: int, ppid_map: dict[int, int]) -> bool:
    """Return True if pid and target_pid are identical or share an ancestor chain (within 8 hops)."""
    if pid <= 0 or target_pid <= 0:
        return False
    if pid == target_pid:
        return True
    cur = target_pid
    for _ in range(8):
        parent = ppid_map.get(cur, 0)
        if parent <= 1 or parent == cur:
            break
        if parent == pid:
            return True
        cur = parent
    cur = pid
    for _ in range(8):
        parent = ppid_map.get(cur, 0)
        if parent <= 1 or parent == cur:
            break
        if parent == target_pid:
            return True
        cur = parent
    return False


def _rule_matches_proc(
    app: AppLimitRule,
    rx: re.Pattern[str],
    proc_rec: dict[str, Any],
) -> bool:
    if (
        app.flatpak_id
        and proc_rec["flatpak_id"]
        and app.flatpak_id.lower() == proc_rec["flatpak_id"].lower()
    ):
        return True
    if (
        app.steam_app_id
        and proc_rec["steam_app_id"]
        and app.steam_app_id == proc_rec["steam_app_id"]
    ):
        return True
    return bool(rx.search(proc_rec["haystack"]))


class AppLimitsMonitor:
    """Tracks per-user processes for App Limits, Category Budgets, Homework Mode, and Insights."""

    def __init__(self, term_grace_ticks: int = 3, proc_root: str = "/proc") -> None:
        self._term_grace_ticks = max(1, int(term_grace_ticks))
        self._proc_root = proc_root
        # Keyed by (uid, pid, create_time) to isolate multi-user attempts and guard PID reuse
        self._term_attempts: dict[tuple[int, int, float], int] = {}

    @staticmethod
    def _compile_patterns(apps: list[AppLimitRule]) -> list[tuple[AppLimitRule, re.Pattern[str]]]:
        compiled: list[tuple[AppLimitRule, re.Pattern[str]]] = []
        for app in apps:
            try:
                rx = re.compile(app.pattern, re.IGNORECASE)
            except re.error:
                rx = re.compile(re.escape(app.pattern), re.IGNORECASE)
            compiled.append((app, rx))
        return compiled

    @staticmethod
    def _compile_raw_patterns(patterns: list[str] | None) -> list[re.Pattern[str]]:
        compiled: list[re.Pattern[str]] = []
        for raw in patterns or []:
            pat = str(raw or "").strip()
            if not pat:
                continue
            try:
                compiled.append(re.compile(pat, re.IGNORECASE))
            except re.error:
                compiled.append(re.compile(re.escape(pat), re.IGNORECASE))
        return compiled

    def _collect_user_procs(
        self, uid: int, *, read_steam_env: bool = True
    ) -> tuple[list[dict[str, Any]], dict[int, int]]:
        if psutil is None:
            return [], {}
        records: list[dict[str, Any]] = []
        ppid_map: dict[int, int] = {}
        # Lightweight first pass: fetch pid/ppid/uids/name, then lazily inspect target uid only
        for proc in psutil.process_iter(attrs=["pid", "ppid", "uids", "name"]):
            try:
                info = proc.info
                uids = info.get("uids")
                if not uids or uids.real != uid:
                    continue
                pid = int(info["pid"])
                ppid = int(info.get("ppid") or 0)
                ppid_map[pid] = ppid
                name = str(info.get("name") or "")
                exe = str(info.get("exe") or "")
                if not exe and hasattr(proc, "exe"):
                    try:
                        exe = str(proc.exe() or "")
                    except (psutil.Error, OSError):
                        exe = ""
                cmdline_list = info.get("cmdline")
                if cmdline_list is None and hasattr(proc, "cmdline"):
                    try:
                        cmdline_list = proc.cmdline()
                    except (psutil.Error, OSError):
                        cmdline_list = []
                cmdline = " ".join(cmdline_list or [])[:512]
                flatpak_id, scope_app = read_proc_cgroup_app(pid, proc_root=self._proc_root)
                steam_app_id = (
                    read_proc_steam_appid(pid, proc_root=self._proc_root) if read_steam_env else ""
                )
                haystack = f"{name} {exe} {cmdline} {flatpak_id} {scope_app}"
                records.append(
                    {
                        "pid": pid,
                        "ppid": ppid,
                        "name": name,
                        "exe": exe,
                        "cmdline": cmdline,
                        "flatpak_id": flatpak_id,
                        "scope_app": scope_app,
                        "steam_app_id": steam_app_id,
                        "haystack": haystack,
                    }
                )
            except (psutil.Error, OSError, KeyError, TypeError):
                continue
        return records, ppid_map

    @staticmethod
    def _classify_passive_app(
        rec: dict[str, Any],
        catalog_by_key: dict[str, tuple[str, str, str]],
    ) -> tuple[str, str, str] | None:
        if rec["steam_app_id"]:
            return (f"steam:{rec['steam_app_id']}", f"Steam Game ({rec['steam_app_id']})", "Gaming")
        fp = rec["flatpak_id"].lower()
        if fp:
            if fp in catalog_by_key:
                return catalog_by_key[fp]
            tail = fp.rsplit(".", 1)[-1]
            if tail in catalog_by_key:
                return catalog_by_key[tail]
        low_name = rec["name"].lower()
        if low_name in catalog_by_key:
            return catalog_by_key[low_name]
        low_exe = os.path.basename(rec["exe"]).lower() if rec["exe"] else ""
        if low_exe in catalog_by_key:
            return catalog_by_key[low_exe]
        # Catch Minecraft launched via Java after launcher exits
        if low_name == "java" and (
            "minecraft" in rec["cmdline"].lower() or "mojang" in rec["cmdline"].lower()
        ):
            return ("minecraft", "Minecraft", "Gaming")
        if fp:
            tail = fp.rsplit(".", 1)[-1]
            return (fp, tail.title() or fp, "App")
        return None

    @staticmethod
    def _build_catalog_map(
        catalog: list[dict[str, Any]] | None,
    ) -> dict[str, tuple[str, str, str]]:
        catalog_map: dict[str, tuple[str, str, str]] = dict(_BUILTIN_PROCESS_CATALOG)
        for item in catalog or []:
            pat = str(item.get("pattern") or "").strip().lower()
            lbl = str(item.get("label") or pat).strip()
            cat = str(item.get("category") or "App").strip()
            if pat:
                catalog_map[pat] = (pat, lbl, cat)
            fp = str(item.get("flatpak_id") or "").strip().lower()
            if fp:
                catalog_map[fp] = (pat or fp, lbl, cat)
        return catalog_map

    def scan_user_processes(
        self,
        uid: int,
        apps: list[AppLimitRule],
        category_limits: list[CategoryLimitRule] | None = None,
        catalog: list[dict[str, Any]] | None = None,
        active_window_pid: int | None = None,
        homework_blocked_categories: list[str] | None = None,
        collect_passive: bool = True,
        exempt_patterns: list[str] | None = None,
    ) -> ProcessScanResult:
        """Perform a single /proc pass for all rule, category, homework, and passive insights."""
        need_steam_env = bool(collect_passive or any(a.steam_app_id for a in apps))
        records, ppid_map = self._collect_user_procs(uid, read_steam_env=need_steam_env)
        result = ProcessScanResult()
        if not records:
            return result

        # If active_window_pid isn't in host ppid_map (e.g. Flatpak container PID), treat as unknown
        effective_fg_pid = (
            active_window_pid if (active_window_pid and active_window_pid in ppid_map) else None
        )
        compiled = self._compile_patterns(apps)
        compiled_exempt = self._compile_raw_patterns(exempt_patterns)
        catalog_map = self._build_catalog_map(catalog)

        tracked_cats_lower = {c.category.lower(): c.category for c in (category_limits or [])}
        for hc in homework_blocked_categories or []:
            tracked_cats_lower.setdefault(hc.lower(), hc)

        direct_rule_by_pid: dict[int, list[AppLimitRule]] = {}
        seen_passive: dict[str, tuple[str, str, str]] = {}

        for rec in records:
            pid = rec["pid"]
            if _is_protected_process(pid, rec["name"], rec["exe"]):
                continue
            matched_rules = [app for app, rx in compiled if _rule_matches_proc(app, rx, rec)]
            if matched_rules:
                direct_rule_by_pid[pid] = matched_rules
            if any(rx.search(rec["haystack"]) for rx in compiled_exempt):
                result.exempt_pids.add(pid)

        for rec in records:
            pid = rec["pid"]
            if _is_protected_process(pid, rec["name"], rec["exe"]):
                continue
            rules_here = list(direct_rule_by_pid.get(pid, []))
            if (
                not rules_here
                and rec["ppid"] in direct_rule_by_pid
                and pid not in result.exempt_pids
            ):
                child_passive = self._classify_passive_app(rec, catalog_map)
                parent_rules = direct_rule_by_pid[rec["ppid"]]
                if child_passive is None:
                    rules_here = list(parent_rules)
                else:
                    child_cat = child_passive[2].lower()
                    rules_here = [
                        r
                        for r in parent_rules
                        if r.category.lower() == child_cat or child_cat == "app"
                    ]

            is_fg = (
                True if not effective_fg_pid else _is_pid_related(pid, effective_fg_pid, ppid_map)
            )
            always_allowed_here = (
                pid in result.exempt_pids
                or rec["ppid"] in result.exempt_pids
                or any(r.always_allowed for r in rules_here)
            )
            if always_allowed_here:
                result.exempt_pids.add(pid)

            logged_passive_for_pid = False
            for app in rules_here:
                if app.foreground_only and effective_fg_pid and not is_fg:
                    continue
                result.rule_matches.append(
                    {
                        "pid": pid,
                        "name": rec["name"],
                        "label": app.label,
                        "pattern": app.pattern,
                        "category": app.category,
                        "always_allowed": app.always_allowed or always_allowed_here,
                    }
                )
                if collect_passive:
                    pkey = f"steam:{app.steam_app_id}" if app.steam_app_id else app.pattern.lower()
                    seen_passive.setdefault(pkey, (pkey, app.label, app.category or "App"))
                    logged_passive_for_pid = True

            passive = self._classify_passive_app(rec, catalog_map)
            if passive is not None:
                app_key, label, cat = passive
                if collect_passive and not logged_passive_for_pid:
                    seen_passive.setdefault(app_key, (app_key, label, cat))
                if cat.lower() in tracked_cats_lower and not always_allowed_here:
                    result.category_matches.append(
                        {
                            "pid": pid,
                            "name": rec["name"],
                            "label": label,
                            "pattern": app_key,
                            "category": tracked_cats_lower[cat.lower()],
                        }
                    )

        result.passive_apps = list(seen_passive.values())
        return result

    def find_matching_processes(
        self,
        uid: int,
        apps: list[AppLimitRule],
    ) -> list[dict[str, Any]]:
        """Backwards-compatible wrapper returning matched process records for apps."""
        if not apps or psutil is None:
            return []
        return self.scan_user_processes(uid, apps, collect_passive=False).rule_matches

    def enforce_kill(
        self,
        uid: int,
        apps: list[AppLimitRule],
        prematched: list[dict[str, Any]] | None = None,
        exempt_pids: set[int] | None = None,
    ) -> list[int]:
        """Send SIGTERM (escalating to SIGKILL after grace ticks) to matched processes."""
        matches = prematched if prematched is not None else self.find_matching_processes(uid, apps)
        skip_pids = exempt_pids or set()
        by_pid: dict[int, dict[str, Any]] = {}
        for m in matches:
            pid = int(m["pid"])
            if pid in skip_pids or _is_protected_process(pid, str(m.get("name") or "")):
                continue
            by_pid.setdefault(pid, m)

        signaled: list[int] = []
        active_keys: set[tuple[int, int, float]] = set()
        for pid, m in by_pid.items():
            try:
                proc = psutil.Process(pid)
                if proc.uids().real != uid:
                    continue
                pname = str(proc.name() or m.get("name") or "") if hasattr(proc, "name") else ""
                pexe = ""
                if hasattr(proc, "exe"):
                    try:
                        pexe = str(proc.exe() or "")
                    except (psutil.Error, OSError):
                        pexe = ""
                if _is_protected_process(pid, pname, pexe):
                    continue
                ctime = float(proc.create_time()) if hasattr(proc, "create_time") else 0.0
                key = (uid, pid, ctime)
                active_keys.add(key)
                attempts = self._term_attempts.get(key, 0) + 1
                self._term_attempts[key] = attempts
                if attempts <= self._term_grace_ticks:
                    logger.warning(
                        "App limit expired: sending SIGTERM to pid=%d (%s) uid=%d",
                        pid,
                        m.get("label", "app"),
                        uid,
                    )
                    proc.terminate()
                else:
                    logger.warning(
                        "App limit expired: sending SIGKILL to pid=%d (%s) uid=%d",
                        pid,
                        m.get("label", "app"),
                        uid,
                    )
                    proc.kill()
                signaled.append(pid)
            except (psutil.Error, OSError, ValueError):
                continue

        # Evict stale attempt counters strictly for THIS uid so multi-user escalation stays intact
        for key in list(self._term_attempts):
            if key[0] == uid and key not in active_keys:
                self._term_attempts.pop(key, None)

        return signaled
