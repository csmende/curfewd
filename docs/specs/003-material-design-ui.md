# Spec 003: Native Material Design 3 Desktop UI (`curfewd-ui`) & Per-App Limits

- **Status:** Implemented
- **Target Version:** `v1.0.0`
- **Depends On:** [Spec 001](001-multi-de-screen-locking.md), [Spec 002](002-curfewd-control-plane-and-cli.md)

---

## 1. Problem & Motivation

`curfewd v0.7.0` replaced the legacy `Timekpr-nExT` daemon and CLI with a hardened, Polkit-authorized control plane (`org.curfewd.Daemon1`), atomic JSON storage, SQLite history, and `curfewctl`, while intentionally removing the unmaintained GTK3/Glade desktop GUI.

Parents and desktop users still need a first-class, approachable native desktop application on Linux (Ubuntu, Linux Mint, Fedora, Debian, Arch) that:

1. **Never runs as `root`:** Launches as the logged-in user and relies strictly on `org.curfewd.Daemon1` + Polkit (`org.curfewd.read`, `org.curfewd.manage`, `org.curfewd.lock`) for administrative actions, while exposing a passwordless self-view (`curfewd-ui --my-status`) bound to the caller's Linux UID.
2. **Speaks human, consumer-grade language:** Eliminates system jargon (`dconf`, `D-Bus`, `Polkit`, `systemd`, `X11`/`Wayland`, `SQLite`, raw regexes, `PlayTime`, `Unaccounted`) in favor of clear, family-friendly controls.
3. **Simplifies daily adjustments, weekly schedules, and app limits:**
   - Removes the confusing legacy `unaccounted` ("freeride") time-window concept.
   - Adds non-destructive **Today-only overrides** (`Today's Time and Schedule`) that automatically reset at local midnight without rewriting the recurring weekly template.
   - Upgrades shared `PlayTime` pools to **individual per-app daily limits** so each game or app has its own allowance, live progress bar, `+15m` bonus button, and isolated termination when it reaches `0m`.
4. **Ships with zero web build toolchain and zero open network ports:** Uses WebKitGTK (`gir1.2-webkit-6.0` on GTK4 with automatic fallback to `gir1.2-webkit2-4.1` on GTK3), a custom `curfewd://` URI scheme serving bundled offline assets, and an in-process JS↔Python script-message bridge.

---

## 2. Goals & Non-Goals

### Goals
- Ship `curfewd-ui` (`/usr/bin/curfewd-ui`) + desktop launcher (`curfewd.desktop`) + AppStream metadata (`org.curfewd.curfewd.metainfo.xml`) + Twilight Guard brand icon (`curfewd.svg`).
- Provide three focused surfaces:
  1. **Admin Control Center:** Left navigation drawer (users with live status + remaining time, pinned `System Health` & `Settings`), unified **Overview** card (`Current Status` with 3 horizontal stat pills, `Today's Time and Schedule` thin hours slider + draggable 24h allowed-window bar, and inline 7/14-day `Screen-Time & App Limits History` chart), **Schedule** tab (per-day thin `Daily time limit` slider + draggable 24h allowed-window bar + `Same schedule every day` checkbox + lockout behavior selector), **App Limits** tab (per-app daily limits + installed `.desktop` app picker), **System Health** (with 1-click **Protect Lock Screen** remediation), and **Settings** (screen-lock toggles + Light/Dark/Auto + 4 Material 3 accent palettes persisted in `~/.config/curfewd/ui.json`).
  2. **My Screen Time (`curfewd-ui --my-status`):** Passwordless self-view reachable from the `curfewd-tray` menu or the auth gate, showing countdown ring, Used Today, Curfew, read-only Today's Allowed Hours bar, per-app remaining breakdown, and Past 7 Days chart.
  3. **Authentication Required Gate:** Minimal card displayed when a standard user opens `curfewd-ui` without Polkit admin approval, offering **Authenticate as Administrator** or **View My Own Screen Time (No Password)**.
- Queue schedule, today-override, app-limit, and daemon-setting edits behind a floating **Unsaved Changes (`Discard` / `Save Changes`)** bar so mid-drag changes never accidentally lock out an active user; keep **Lock Now** (with confirmation dialog) and per-app **`+15m`** immediate.
- Extend `org.curfewd.Daemon1` with `SetTodayOverride`, `GrantAppTime`, `ApplyDoctorFix`, and passwordless sender-UID-bound `GetMyHistory`.

### Non-Goals
- No HTTP server, localhost TCP port, Electron runtime, or Node.js/npm build step.
- No remote cloud sync or external CDN/font fetches (100% offline operation).
- No root GUI launcher (`pkexec` / `sudo`) for `curfewd-ui`.

---

## 3. Design & Architecture

### 3.1 Process & Security Architecture

```text
curfewd-ui (unprivileged desktop user)
  ├── GTK4 + WebKit 6.0 (fallback: GTK3 + WebKit2 4.1)
  ├── Custom URI scheme: curfewd://app/{index.html,styles.css,app.js}
  │     (strict filename allowlist; external navigation & devtools blocked)
  ├── In-process JS ↔ Python bridge (window.webkit.messageHandlers.curfewd)
  │     └── Worker thread with private D-Bus connection (keeps UI responsive during Polkit prompts)
  └── Main-loop D-Bus signal listener (UserStatusChanged → live UI telemetry push)
            │
            ▼ System D-Bus (org.curfewd.Daemon1)
curfewd.service (root, ProtectSystem=strict, ReadWritePaths incl. -/etc/dconf)
  ├── Polkit authorization for Admin methods (read / manage / lock)
  └── Sender-UID binding for Client methods (GetMyStatus, GetMyHistory, ReportScreensaverState)
```

### 3.2 Backend & Schema Upgrades (`v1.0.0`)

1. **Removal of `unaccounted` Windows:**
   - `TimeWindow` stores only `start` and `end` (`"HH:MM"`). Legacy `unaccounted` keys in JSON or `!hour` tokens in Timekpr-nExT migration inputs are accepted gracefully as normal allowed windows so upgrades never fail.
2. **Per-App Daily Limits (`PlayTimeApp` & `UserAccountingState`):**
   - `PlayTimeApp` gains `daily_limit_sec: int = 3600` and `category: str = ""`.
   - `UserAccountingState` gains `app_spent_today: dict[str, int]` and `app_bonus_today: dict[str, int]` (keyed by app `pattern`, cleared on local midnight rollover).
   - Each daemon tick charges elapsed seconds only to apps currently matching running processes for that UID and terminates **only** the specific app(s) whose `(daily_limit_sec + app_bonus_today) - app_spent_today <= 0`, leaving other open apps untouched.
3. **Today-Only Override (`UserAccountingState`):**
   - Adds `today_limit_override: int | None` and `today_windows_override: list[dict] | None`, evaluated ahead of the recurring weekly schedule for the current local date and cleared automatically on midnight rollover.
4. **D-Bus API Additions (`org.curfewd.Daemon1`):**
   - `Admin.SetTodayOverride(username: s, override_json: s) -> s` (Polkit `org.curfewd.manage`)
   - `Admin.GrantAppTime(username: s, pattern: s, delta_seconds: i) -> s` (Polkit `org.curfewd.manage`)
   - `Admin.ApplyDoctorFix() -> s` (Polkit `org.curfewd.manage`, requires `-/etc/dconf` in `curfewd.service` `ReadWritePaths`)
   - `Client.GetMyHistory(days: i) -> s` (sender-UID bound, no Polkit prompt, clamped `1..30` days)

### 3.3 Installed Application Scanner (`curfewd/ui/appscan.py`)

Scans freedesktop `.desktop` entries across `/usr/share/applications`, `/usr/local/share/applications`, `/var/lib/flatpak/exports/share/applications`, and user application directories to build a curated, deduplicated picker with friendly app names, consumer categories (`Gaming`, `Social`, `Media`, `Browser`, `Creative`, `Productivity`), and normalized process patterns (supporting native binaries and Flatpak IDs such as Steam, Sober/Roblox, Lunar Client, Modrinth, Discord, Spotify, and Heroic).

### 3.4 Tray Companion Integration (`curfewd-tray`)

`curfewd-tray` adds an **"Open My Screen Time"** menu item launching `/usr/bin/curfewd-ui --my-status` without a password prompt, updates its launcher/notification icon to `curfewd`, and removes obsolete `unaccounted` icon logic.

---

## 4. Compatibility & Migration

- **From `curfewd 0.7.0`:** Schema v1 JSON files load directly; obsolete `unaccounted` / `allow_unaccounted` / `playtime.daily_limits` fields are gracefully ignored or migrated to per-app `daily_limit_sec`.
- **From `curfewd 0.6.x` / `Timekpr-nExT`:** `curfewctl migrate-timekpr` continues to import legacy `/etc/timekpr` and `/var/lib/timekpr` configs automatically on first install, merging legacy `!hour` tokens into regular allowed windows and seeding migrated PlayTime apps with their legacy daily limit.
- **Headless Systems:** `gir1.2-webkit-6.0 | gir1.2-webkit2-4.1` is listed in `Recommends:` rather than hard `Depends:` so headless or CLI-only hosts remain lightweight.

---

## 5. Security & Privacy Considerations

- **Zero Elevated UI Process:** `curfewd-ui` runs exclusively with the caller's desktop UID; every privileged write crosses Polkit.
- **No Network Exposure:** Custom `curfewd://` scheme serves only an explicit allowlist of static files (`index.html`, `styles.css`, `app.js`); external URL navigations and popups are denied.
- **XSS Prevention:** All dynamic fields (usernames, full names, scanned `.desktop` labels, categories, error strings) are HTML-escaped prior to rendering.
- **Sender-UID Isolation:** `Client.GetMyHistory` derives the target username strictly from the D-Bus sender UID via `GetConnectionUnixUser` and never accepts a username argument.
- **Scoped Filesystem Write Access:** `curfewd.service` keeps `ProtectSystem=strict` and appends only `-/etc/dconf` to `ReadWritePaths` so `ApplyDoctorFix()` can install root `dconf` screensaver locks safely.

---

## 6. Verification & Acceptance Criteria

1. Unit & integration tests cover:
   - Per-app daily limit accounting, `+15m` per-app grants, midnight reset, and isolated expiration/kill targeting only expired apps.
   - `SetTodayOverride` applying today-only allowance & window overrides without altering weekly templates, and automatic reset on midnight rollover.
   - Live D-Bus tests for `SetTodayOverride`, `GrantAppTime`, `ApplyDoctorFix` (Polkit-gated), and `GetMyHistory` (sender-UID bound, works when Polkit denies admin).
   - `curfewd.ui.appscan` parsing native and Flatpak `.desktop` files safely.
   - `curfewd.ui.bridge` handling admin bootstrap, auth-gate fallback on `PermissionDeniedError`, passwordless `my_status` mode, batched `save_changes`, and user preferences persistence.
2. Static analysis & packaging gates pass with zero errors:
   - `python3 -m unittest discover -s tests -v` — 100% pass.
   - `bandit -r curfewd` — 0 findings.
   - `shellcheck` on all scripts including `bin/curfewd-ui` — 0 warnings.
   - `node --check curfewd/ui/web/app.js` — 0 syntax errors.
   - `dpkg-buildpackage -us -uc -b` + `lintian` — 0 errors.
