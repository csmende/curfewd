# Changelog

All notable changes to **curfewd** are documented here.
For historical upstream releases (`v0.5.11` and earlier), see [`debian/changelog`](debian/changelog).

## [1.1.0] - 2026-11-26

Complete `curfewd` rebrand, legacy asset/bridge cleanup, and schema v2 (`app_limits`) migration (**Spec 004**).

### Changed
- **Native Tray Icon Filenames:** Renamed the 4 Material Design 3 Twilight Guard tray status SVGs from `timekpr-padlock-*.svg` to `curfewd-tray-{ok,warning,alert,unlimited}.svg`.
- **Schema v2 (`app_limits` Everywhere):** Renamed internal `playtime` module (`curfewd/engine/applimits.py`), dataclasses (`AppLimitRule`, `AppLimitsPolicy`), persisted JSON keys (`app_limits`, `app_limits_enabled`, `app_limits_spent_today`, `app_limits_balance`), D-Bus status key (`app_limits_status`), audit event (`app_limit_kill`), and SQLite column (`daily_usage.app_limits_sec`). Existing `curfewd` schema v1 JSON files and SQLite `history.db` databases forward-migrate automatically on load without data loss.
- **Docs & Metadata Refresh:** Refreshed `README.md`, `CONTRIBUTING.md`, `SECURITY.md`, `.github/ISSUE_TEMPLATE/bug_report.md`, `extras/dconf-lock-screensaver.sh`, `debian/copyright`, and `debian/control` while retaining GPL-3.0 upstream attribution and strategic search discoverability.

### Removed
- Deleted 11 unused legacy `timekpr-*.{png,svg}` icons (~270 KB), the obsolete `spec/` RPM directory, and `docs/TIMEKPR_GUIDE.md`.
- Removed `curfewd/migrate.py`, `curfewctl migrate-timekpr`, the legacy `curfewctl playtime` CLI alias, and legacy `/etc/timekpr` hooks from `debian/postinst`.

## [1.0.4] - 2026-11-26

### Changed
- **Refined Material Design 3 Tray Status Icons:** Updated `timekpr-padlock-limited-green.svg` to the signature Twilight Guard indigo squircle (`#3f51b5` → `#6c63ff`) with a `#5cd69d` emerald active status dot, alongside refined amber (`#b45309` → `#f59e0b`), crimson (`#b91c1c` → `#ef4444`), and teal-emerald (`#0f766e` → `#10b981`) state SVGs.

## [1.0.3] - 2026-11-26

### Changed
- **Material Design 3 Tray Status Icons:** Replaced the 4 legacy padlock tray icons (`resource/icons/timekpr-padlock-{limited-green,limited-yellow,limited-red,unlimited-green}.svg`) with Material Design 3 Twilight Guard squircle + shield + crescent-moon status variants (emerald, amber, crimson, and teal) matching `curfewd.svg` and `curfewd-ui`.
- **Metadata & Docs Polish:** Updated AppStream metainfo (`1.0.1`–`1.0.3`), README release references, Spec 003 tray iconography notes, and anonymized test fixture usernames.

## [1.0.2] - 2026-11-26

### Fixed
- **App Limits Picker (Flatpak & Steam discovery):** Updated `curfewd.ui.appscan` to resolve exported Flatpak `.desktop` symlinks (`/var/lib/flatpak/exports/share/applications/*.desktop`) and unwrap `sh -c` desktop wrappers (`steam.desktop`).

## [1.0.1] - 2026-11-26

### Fixed
- **One-shot `curfewctl` CLI D-Bus connection:** Fixed `RuntimeError` in `CurfewClient._get_proxy()` when running synchronous CLI or worker-thread calls without a GLib main loop attached.
- **Seamless `curfewd 0.6.x → 1.0.x` upgrade:** `debian/postinst` now removes the stale `/etc/systemd/system/curfewd.service -> timekpr.service` alias symlink left behind by `v0.6.x` before reloading and enabling `curfewd.service`.
- **Clean systemd unit:** Relocated `StartLimitIntervalSec` and `StartLimitBurst` into `[Unit]` in `curfewd.service`.

## [1.0.0] - 2026-11-26

First `1.0` release delivering **Spec 003**: a native, unprivileged Material Design 3 desktop application, Today-only schedule overrides, and individual per-app daily limits.

### Added
- **Native Material Design 3 Desktop UI (`curfewd-ui`):**
  - Runs completely unprivileged via WebKitGTK (`gir1.2-webkit-6.0` on GTK4 with automatic fallback to `gir1.2-webkit2-4.1` on GTK3), custom offline `curfewd://` URI scheme, and worker-thread Polkit D-Bus RPC bridge (zero open TCP ports, zero Node/npm build step).
  - **Admin Control Center:** Left user drawer with live status dots & remaining time, unified **Overview** (`Current Status` three horizontal stat pills, `Today's Time and Schedule` slider + draggable 24h allowed-window bar, and 7/14-day `Screen-Time & App Limits History` chart), per-day **Weekly Schedule** with `Same schedule every day` checkbox, **App Limits** with installed `.desktop`/Flatpak app picker, **System Health** with 1-click **Protect Lock Screen**, and **Settings** (Light/Dark/Auto + 4 Material 3 accent palettes saved to `~/.config/curfewd/ui.json`).
  - **Passwordless `My Screen Time` (`curfewd-ui --my-status`):** Self-view accessible from `curfewd-tray` or the minimal **Authentication Required** gate using sender-UID-bound `GetMyStatus` and `GetMyHistory`.
  - Floating **Unsaved Changes (`Discard` / `Save Changes`)** bar so mid-drag schedule edits never interrupt active users.
- **Today-Only Overrides (`SetTodayOverride` / `curfewctl today`):** Override today's allowance and/or allowed window without altering the recurring weekly template; automatically resets at local midnight.
- **Individual Per-App Daily Limits (`curfewctl app-limits` / `GrantAppTime`):** Each tracked app or game maintains its own daily allowance, live usage counter, `+15m` bonus grant, and isolated termination when it reaches `0m`.
- **1-Click Lock-Screen Protection (`ApplyDoctorFix`):** Polkit-authorized D-Bus endpoint backed by `ReadWritePaths=... -/etc/dconf` in `curfewd.service`.
- **Brand & Desktop Metadata:** Twilight Guard SVG icon (`curfewd.svg`), unprivileged `curfewd.desktop`, and AppStream metadata (`org.curfewd.curfewd.metainfo.xml`).

### Changed / Removed
- Removed the legacy `unaccounted` ("freeride") time-window concept from models, evaluator, CLI, tray, and UI (legacy `!hour` tokens are imported cleanly as normal allowed windows by `curfewctl migrate-timekpr`).

## [0.7.0] - 2026-11-26

Breaking control-plane overhaul (**Spec 002**): replaced legacy `timekpr.service` / `com.timekpr.server` / `/etc/timekpr` with hardened `curfewd.service`, Polkit-authorized `org.curfewd.Daemon1` D-Bus API, atomic JSON storage, SQLite usage history, native `pause`/`resume`/`lock`/`doctor`, modern `curfewctl` CLI with `--json`, and auto-reconnecting `curfewd-tray`.

## [0.6.1] - 2026-11-26

Security hardening release: CSPRNG verification keys with `hmac.compare_digest`, `O_NOFOLLOW` + lock-before-truncate PID/log writes, root `/tmp` avoidance, Bandit + ShellCheck + CodeQL CI workflows.

## [0.6.0] - 2026-11-25

Initial public release of **curfewd** — a modern, actively maintained drop-in fork of [Timekpr-nExT](https://launchpad.net/timekpr-next) (branched from `v0.5.11` with upstream bugfixes cherry-picked and preserved).

### Added
- **Verified multi-DE screen lock enforcement** (`server/interface/dbus/logind/screenlock.py`):
  - After sending the standard `systemd-logind` `LockSession` signal, `curfewd` verifies whether the user's desktop screensaver actually engaged and, if not, directly activates it over the user's session D-Bus (or DE CLI fallback) with dropped privileges, strict environment sanitization, and bounded timeouts.
  - Supports **Cinnamon** (`org.cinnamon.ScreenSaver`), **XFCE** (`org.xfce.ScreenSaver`, `xfce4-screensaver-command`, `xflock4`), **MATE** (`org.mate.ScreenSaver`), **GNOME / Unity / Budgie** (`org.gnome.ScreenSaver`), and **KDE Plasma / LXQt / FreeDesktop** (`org.freedesktop.ScreenSaver`, `xdg-screensaver`).
- **Fullscreen game X11 grab-release retry** (`TIMEKPR_RELEASE_X11_GRAB_ON_LOCK = True`):
  - Detects when a screensaver returns exit code 0 but fails to lock because a fullscreen X11 game (Steam/Proton, SDL2, Minecraft, Roblox Sober, etc.) holds an input grab.
  - Escalates non-destructively: first tries `xdotool key XF86Ungrab`, then falls back to un-fullscreening / minimizing the active window (`wmctrl` / `xdotool`) so the game voluntarily drops its grab without losing progress, then retries locking.
- **New `[MISC]` configuration options** in `/etc/timekpr/timekpr.conf` (auto-migrated from existing `v0.5.11` configs):
  - `TIMEKPR_SESSION_LOCK_FALLBACK = True`
  - `TIMEKPR_RELEASE_X11_GRAB_ON_LOCK = True`
- **Modern CLI & systemd aliases**:
  - Added `/usr/bin/curfewctl` (alias for `timekpra`) and `/usr/bin/curfewd` (alias for `timekprd`).
  - Added `Alias=curfewd.service` to `timekpr.service` so `systemctl status curfewd` and `systemctl status timekpr` both work.
- **Parent hardening examples** (`/usr/share/doc/curfewd/examples/`):
  - `dconf-lock-screensaver.sh`: root-locks `lock-enabled=true` and `disable-lock-screen=false` across Cinnamon, GNOME, and MATE so non-admin users cannot disable screen locking in System Settings.
  - `90-curfewd-ungrab.conf`: optional Xorg `AllowDeactivateGrabs` snippet for zero-minimize fullscreen locking.
- **Automated testing & `.deb` CI/CD**:
  - 22 unit and live ephemeral `dbus-daemon` + `gdbus` integration tests (`tests/`).
  - GitHub Actions CI + automated `.deb` release builds (`Architecture: all`, drop-in replacement for `timekpr-next`).

### Fixed
- **Linux Mint Cinnamon `LOCKOUT_TYPE=lock` silently failing at `00:00`**: fixes the mismatch between `systemd-logind`'s `Session.Lock` broadcast and `cinnamon-screensaver`'s 30-second inactivity auto-exit.
- **Cinnamon client screensaver tracking crash**: passes `follow_name_owner_changes=True` on the screensaver D-Bus proxy, checks `name_has_owner()` before calling `GetActive()`, and safely reconnects on transient D-Bus errors.
- **`timekpra --userinfo` boolean output bug**: accurately prints `True`/`False` for both `dbus.Boolean` and string values (`HIDE_TRAY_ICON`, `TRACK_INACTIVE`, `PLAYTIME_*`).
- **GLib sub-second timer bug**: replaces `GLib.timeout_add_seconds(0.1, ...)` with `GLib.timeout_add(100, ...)`.
- **GTK dark-mode contrast guard**: protects `lookup_color()` in `admingui.py` for themes lacking symbolic color definitions.
- **Cherry-picked upstream fixes** (authored by Eduards Bezverhijs): PlayTime balance reset when user is logged out (`#2172687`), GTK hour interval contrast (`#2172098`), and `groupadd -r timekpr` packaging fix (`#2172412`).
