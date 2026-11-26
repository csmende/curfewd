# Changelog

All notable changes to **curfewd** are documented here.
For historical upstream releases (`v0.5.11` and earlier), see [`debian/changelog`](debian/changelog).

## [Unreleased]

### Documentation & Governance
- Added spec-driven and AI-assisted development documentation in `README.md`, `CONTRIBUTING.md`, `AGENTS.md`, and `docs/specs/` (starting with `001-multi-de-screen-locking.md`).
- Updated `README.md` intro and UI/usability roadmap to reflect active multi-system development beyond initial Timekpr-nExT fixes.
- Corrected log-viewing command in `README.md` to `journalctl -u timekpr -f` (systemd logs under the primary unit `timekpr.service` rather than the `curfewd.service` alias) following live validation on Linux Mint 22.3 Cinnamon.
- Recorded live multi-system Linux Mint 22.3 Cinnamon verification notes in `docs/specs/001-multi-de-screen-locking.md`.

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
