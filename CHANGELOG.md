# Changelog

All notable changes to **curfewd** are documented here.
For historical upstream releases (`v0.5.11` and earlier), see [`debian/changelog`](debian/changelog).

## [2.0.0] - 2026-11-26

Family ergonomics, fair enforcement, Ask for More Time + offline Parent Access Code, Homework Mode, smart app intelligence, and multi-DE hardening (**Spec 006**, **Schema v3**).

### Added
- **Tactical Parent Controls & UI Parity:** 1-click `+15m`, `+30m`, and positive-note **Bonus** grants, **Pause / Resume**, **Homework Mode**, **Today Override** reset badge, **Weekly/Monthly Caps**, **Holidays & Vacation Overrides**, and an inline **Recent Activity** timeline in `curfewd-ui`.
- **Ask for More Time & Walk-Up Parent Approval:** Kid-initiated time requests from `curfewd-tray` and `My Screen Time`, an **Offline 6-Digit Parent Access Code (RFC 6238 TOTP)** with 100% offline SVG QR enrollment (`curfewd/auth_code.py`, brute-force rate limit + single-use anti-replay), and a non-cached `org.curfewd.quick_grant` (`auth_admin`) Polkit action closing the shared-session `auth_admin_keep` cliff.
- **Kid-Side Fairness & Anti-Rage UX:** Advance per-app & category warnings (`10m`/`5m`/`1m`) before termination, `logind IdleHint` + tray idle-pause accounting (`idle_pause_sec = 300`), informative Cinnamon lock-screen away messages, optional audio chimes and a non-focus-stealing 60s corner Countdown HUD (`curfewd/tray/hud.py`), one-time-per-day **2-Minute Save Grace**, and **Homework Mode** (pauses daily countdown while blocking Gaming/Social).
- **Schedule Polish & First-Run Setup Wizard:** 3-way schedule mode (*Every Day / School Days & Weekends / Custom*), lossless multi-window split timelines (`+ Break` / `Block`), `Copy day`, Managed Child vs. Parent account roles, locale-aware 12h/24h time format, and a 60-second First-Run Setup Wizard (auto-skipped on existing v1.x upgrades).
- **App Intelligence & Multi-DE Parity:** Single-pass `/proc` Flatpak cgroup, Steam `SteamAppId`, process ancestry, and optional foreground-window matching; shared **Category Budgets** + `0m` Always Blocked + weekend app limits; SQLite `daily_app_usage` rollups powering **Top Apps Used** with 1-click *Set Limit*; target-user home `.desktop` app scanning; KDE Plasma Kiosk & XFCE `doctor --fix` hardening; Wayland-aware System Health + `TestLock`; and a self-healing `systemd --user` unit (`curfewd-tray.service`).
- **CLI Parity (`curfewctl`):** Added `--note` to `grant`, plus `role`, `homework`, `holiday`, `requests`, `parent-code`, `insights`, `test-lock`, and `app-limits category`.

### Fixed
- **UI Live Updates & Slider/Modal Polish:** Replaced full 3-second `#root.innerHTML` re-renders with surgical `patchLiveStatusDOM()`, fixed App Limit slider `oninput` DOM teardown during drags, fixed Add-App modal search resetting the limit dropdown, and stopped `extractRange()` from collapsing split multi-window schedules.
- **Backward Clock-Jump Guard:** `rollover_state()` now rolls counters forward only (`date_str > state.date`), preventing dual-boot UTC drift or BIOS rollbacks from resetting `spent_today`.
- **Root-Verified Lockout Deduplication (`#7`):** Tracked root-verified per-session lock state from `ScreenLockEnforcer` callbacks (strictly isolated from untrusted client telemetry) to stop duplicate `lockout` event/counter inflation and journald spam on Cinnamon/XFCE/MATE desktops without `logind` `LockedHint`, backing off to lightweight 30s re-verification.
- **Cross-DE Systemd Tray Supervision & Upgrade Migration (`#8`):** Added `curfewd-tray --autostart` handoff to `curfewd-tray.service` for desktops where `graphical-session.target` is inactive, removed hard `PartOf=graphical-session.target`, and migrated active graphical sessions from pre-systemd tray processes in `debian/postinst`.
- **Packaging, Group Permissions & Role Migration (`#9`, `#10`, `#11`):** Added `Group=curfewd` to `curfewd.service` so `StateDirectory=curfewd` retains `root:curfewd` ownership, removed duplicate manual `curfewd.service` restarts in `debian/postinst`, and defaulted missing `"role"` keys on v1/v2 migrated policies to `"parent"` for local admin accounts.
- **Passive App Insights, Diagnostics & WAL Efficiency (`#12`, `#13`):** Added symlink-safe Steam `appmanifest_*.acf` title discovery and a non-blocking cached app catalog for passive insights, foreground-window gating plus wallpaper/tray filtering (`passive_app_ignore_list`), clear `screen_locked` vs. `logind_locked` output in `curfewctl doctor`, batched SQLite daily usage writes (`save_interval_sec`) with `wal_autocheckpoint=200` + `TRUNCATE` checkpoints, and RC pre-release version stamping in `release.yml`.
- **Pre-GA Hardening (`v2.0.0-rc6`, `F1`–`F4`):** Fixed `curfewd-ui` `SIGSEGV` on window close via private `WebKit.WebContext()`, explicit teardown, `_RPCWorker.join()`, and flushed `os._exit(rc)` in `curfewd/ui/__main__.py` while unifying GTK3/GTK4 onto single-instance `Gtk.Application` + `SIGTERM`; fixed `screenlock` `gdbus` timeout races and owned-bus fallback pile-ups and cut Cinnamon unlock-to-relock latency from 10–19s to <2s via 5s locked-out re-verify + fail-closed `ActiveChanged(false)` → `NotifySessionUnlocked()`; added graceful `curfewd-tray` logout/shutdown exit handlers, OSD banner dismissal, and unreachable-display exit `0`; and switched `debian/postinst` user-bus `systemctl` calls to `runuser -u` to eliminate Ubuntu 24.04 / Mint 22.3 `pam_lastlog.so` errors.

## [1.2.0] - 2026-11-26

Architectural modularization, non-blocking async admin D-Bus RPCs, single-pass enforcement runtime, runtime bug fixes, and automated Ruff/Mypy quality gates (**Spec 005**).

### Added
- **Non-Blocking Async Admin D-Bus RPCs:** `LockUser`, `RunDoctor`, and `ApplyDoctorFix` now execute blocking subprocesses on a bounded `ThreadPoolExecutor(max_workers=2)` via `dbus-python` `async_callbacks` while keeping all `SystemBus` access strictly on the GLib main thread.
- **Live Daemon Config Reload:** `SetDaemonConfig` dynamically updates the root logger level (`log_level`) and reschedules the GLib tick timer (`poll_interval_sec`) without restarting `curfewd.service`.
- **Automated Quality Gates & Expanded Tests:** Added `pyproject.toml` (Ruff targeting Python 3.10+ and Mypy `check_untyped_defs = true`), `.editorconfig`, `.prettierrc.json`, a dedicated CI `lint` job, `tests/test_applimits.py`, `tests/test_runtime.py`, live D-Bus async concurrency & config-reload tests, and Web UI allowlist/packaging integrity tests (`65` tests total).

### Changed
- **Single-Pass Enforcement Runtime (`curfewd/engine/runtime.py`):** Extracted D-Bus-free `EnforcementRuntime` out of `CurfewDaemonService`, eliminating duplicate `evaluate_tick()` calls per active user and duplicate `/proc` scans when terminating expired apps.
- **Evaluator, CLI & Offline Web UI Decomposition:** Decomposed `evaluate_tick` into 5 focused pure helpers (CC `54 → 17`), split terminal printers into `curfewd/cli/formatters.py` and replaced `run_cli`'s giant `if`-chain with `_COMMAND_HANDLERS` (CC `102 → 11`), and modularized the zero-bundler Web UI into `curfewd/ui/web/js/{rpc,state,views,events}.js` + thin `app.js`.
- **SQLite & Storage Hardening:** Replaced unclosed per-call SQLite connections in `HistoryStore` with a single `RLock`-guarded WAL connection + explicit `close()`, reused `write_json_atomic` in `UIPreferencesStore.save`, and removed legacy `/var/log` from `curfewd.service` `ReadWritePaths`.

### Fixed
- **Curfew Warning Spam & Cap Crossings:** Fixed warning threshold detection in `evaluate_tick` to compare against `prev_time_left_continuous` across daily, weekly, monthly, and curfew window limits so warnings fire once instead of repeating every 3 seconds before curfew.
- **App Scanner (`appscan.py`):** Fixed `"audioVideoediting"` casing typo in `_map_category` and fixed the empty-install curated fallback loop so all 6 curated app suggestions load on minimal environments.

### Removed
- Dropped unused legacy model fields (`AppLimitsPolicy.override_main`, `UserAccountingState.app_limits_balance`) while keeping `from_dict()` tolerant of existing JSON files, removed empty `debian/curfewd.lintian-overrides`, and deleted the superseded `docs/design/curfewd-ui-prototype-rev9.html` mockup.

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
