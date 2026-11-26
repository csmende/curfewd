# Spec 006: Family Ergonomics, Fair Enforcement & App Intelligence (`curfewd v2.0.0`)

- **Status:** Implemented
- **Target Version:** `v2.0.0`
- **Depends On:** [Spec 003](003-material-design-ui.md), [Spec 004](004-rebrand-and-app-limits-schema.md), [Spec 005](005-architecture-refactor-and-async-daemon.md)

---

## 1. Problem & Motivation

By `v1.2.0`, `curfewd` solved the hard Linux systems-engineering challenges of reliable multi-DE screen locking, fullscreen X11 game grab release, non-blocking async Polkit D-Bus RPCs, and an unprivileged Material Design 3 desktop UI. However, real-world household usability exposed four major friction areas:

1. **Daily parent friction & UI parity gaps:** Common 5-second actions (`+15m` / `+30m` bonus grants with positive notes, Pause/Resume, Today override reset, Activity timeline, Weekly/Monthly caps) were either CLI-only or absent from `curfewd-ui`. Meanwhile, `curfewd-ui` had concrete bugs: `extractRange()` collapsed multi-window schedules, 3-second live D-Bus pushes replaced `#root.innerHTML` (wiping scroll/focus/tooltips), per-app slider drags re-rendered the full DOM, and `appscan` only scanned the parent's home directory.
2. **Walk-up approval & shared-session Polkit security cliff:** When a parent authenticates in `curfewd-ui` inside a child's desktop session, Polkit's `auth_admin_keep` caches admin rights for ~5 minutes—leaving a window where the child can modify their own limits. Parents also had no kid-initiated "Ask for More Time" flow or Family Link–style offline Parent Access Code.
3. **Abrupt kid-side enforcement:** Per-app limits sent `SIGTERM`/`SIGKILL` with zero prior warning; `logind IdleHint` was collected but ignored (draining time while away from the keyboard); fullscreen games hid standard notifications; Cinnamon lock screens received an empty lock message instead of explaining *why* and *when* access resumes; and kids had no Homework Mode or 2-minute save grace.
4. **App-tracking & multi-DE hardening gaps:** Substring-only process matching drained timers for minimized Steam/Discord windows while missing child game processes (e.g., Minecraft `java` after launcher exit), `history.db` lacked per-app historical rollups ("Top Apps Used This Week"), `doctor --fix` only hardened `dconf` (omitting KDE Plasma Kiosk and XFCE), and pure Wayland hosts showed false warnings for missing X11 tools.

---

## 2. Goals & Non-Goals

### Goals
- **Schema v3 (`SCHEMA_VERSION = 3`)** with lossless automatic forward-migration from schema v1 and v2 JSON files and SQLite `history.db`.
- **Pillar 1 (Tactical Controls & Bug Fixes):** Per-app/category advance warnings (`10m`/`5m`/`1m`), `IdleHint` idle-pause accounting, backward clock-jump guard in `rollover_state`, informative Cinnamon lock-screen away messages, multi-window UI support, surgical live DOM updates, fixed App Limit slider & Add-App search modal, target-user app scanning, and full Overview controls (`+15m`/`+30m`/bonus note, Pause/Resume, Reset Today, Activity timeline).
- **Pillar 2 (5-Second Parent Workflow):** Kid "Ask for More Time" request queue, offline RFC 6238 **6-digit TOTP Parent Access Code** (with offline SVG QR enrollment, anti-replay, and brute-force rate limiting), non-cached `org.curfewd.quick_grant` (`auth_admin`) Polkit action, 3-way schedule mode (*Every Day / School Days & Weekends / Custom by Day*) + Copy Day + locale-aware 12h/24h formatting, Holiday/Vacation date overrides, Managed Child vs. Parent account roles, and a 60-second First-Run Setup Wizard.
- **Pillar 3 (Kid Fairness & Anti-Rage UX):** Optional soft audio chimes and non-focus-stealing 60-second corner countdown HUD pill in `curfewd-tray`, parent-configurable one-time-per-day 2-minute "Save Your Work" grace, **Homework / Study Mode** (pauses daily countdown while blocking Gaming/Social and allowing educational apps), and positive bonus notes.
- **Pillar 4 (App Intelligence & Multi-DE Parity):** Passive `daily_app_usage` SQLite rollups powering "Top Apps Used This Week" with 1-click *Set Limit*, single-pass `/proc` cgroup/Flatpak/Steam `SteamAppId`/ancestry/foreground-PID matching, shared **Category Budgets** + `0m` (Always Blocked) + weekend app rules, KDE Plasma Kiosk & XFCE `doctor --fix` hardening, Wayland-aware System Health + `TestLock`, and a self-healing `systemd --user` unit for `curfewd-tray`.

### Non-Goals
- **No network listeners, cloud sync, MQTT/Home Assistant bridge, or LAN web server** (Pillar 5 is intentionally deferred; `curfewd` remains 100% offline with zero open TCP/UDP ports).
- **No Node.js build step or JavaScript bundler**; WebKitGTK continues serving strictly allowlisted static files over `curfewd://`.

---

## 3. Design & Architecture

### 3.1 Schema v3 Models & Migration
- `UserPolicy`: adds `role` (`"child" | "parent"`), `holidays: list[HolidayOverride]`, `homework_mode: HomeworkModeState`, `save_grace_enabled: bool = True`.
- `AppLimitRule`: adds `weekend_limit_sec`, `flatpak_id`, `steam_app_id`, `always_allowed`, `foreground_only`; `AppLimitsPolicy` adds `category_limits: list[CategoryLimitRule]`.
- `UserAccountingState`: adds `category_spent_today`, `category_bonus_today`, `save_grace_used_today`, `save_grace_until_utc`, `last_bonus_note`.
- `DaemonConfig`: adds `idle_pause_sec` (default `300`), `app_warning_thresholds_sec` (default `[600, 300, 60]`), `warning_chime_enabled`, `countdown_hud_enabled`, `passive_app_insights_enabled`, `setup_completed`.
- `ENFORCEMENT_STATES`: adds `"homework"`.

### 3.2 SQLite Additions (`history.db`)
- `daily_app_usage(date, username, app_key, label, category, active_sec)` — updated in the same single transaction as `daily_usage`.
- `time_requests(id, username, kind, target, target_label, requested_sec, reason, status, created_utc, resolved_utc, resolved_by, granted_sec, note)` — persistent request queue and audit trail.

### 3.3 Offline Parent Access Code (`curfewd/auth_code.py`)
- Pure-stdlib RFC 6238 TOTP (SHA-1, 6 digits, 30s period, `±1` step skew window) + pure-Python offline SVG QR encoder.
- Persisted atomically in `/etc/curfewd/parent_auth.json` (`0o600`, root-only, `O_NOFOLLOW`).
- Enforces `hmac.compare_digest`, single-use monotonic `last_used_step` anti-replay, and in-daemon rate limiting (5 failures → 300s lockout).
- Scoped strictly to walk-up approvals (`ApproveWithParentCode`) for the caller's own Unix UID—never authorizes weekly schedule edits or arbitrary system actions.

### 3.4 D-Bus & Polkit Extensions (`org.curfewd.Daemon1`)
- New Polkit action `org.curfewd.quick_grant` (`<allow_active>auth_admin</allow_active>` — never cached).
- New Admin methods: `GrantTimeDetailed`, `SetHomeworkMode`, `ListTimeRequests`, `ResolveTimeRequest`, `QuickApproveRequest`, `GetParentCodeStatus`, `SetupParentCode`, `ListInstalledApps` (async), `TestLock` (async).
- New Client methods (sender-UID bound): `RequestMoreTime`, `ListMyTimeRequests`, `ClaimSaveGrace`, `ApproveWithParentCode`, `ReportClientTelemetry`.
- New Signal: `Events.TimeRequestUpdated(username, request_json)`.

### 3.5 Multi-DE Hardening & Tray Reliability
- `curfewd.service` appends `-/etc/xdg` to `ReadWritePaths` under `ProtectSystem=strict` so `ApplyDoctorFix` can also write `/etc/xdg/kscreenlockerrc` (KDE Kiosk `[$i]`) and `/etc/xdg/xfce4/xfconf/xfce-perchannel-xml/xfce4-screensaver.xml` when KDE or XFCE is installed.
- `curfewd-tray` adds a single-instance runtime flock, optional `curfewd-tray.service` systemd user unit, X11 active-window/idle probing via `ReportClientTelemetry`, soft audio chimes, and a non-focus-stealing 60s corner countdown HUD (`curfewd/tray/hud.py`).

---

## 4. Compatibility & Migration

- **From `v0.7.x`–`v1.2.x`:** All schema v1 and v2 JSON files load seamlessly via `from_dict()`, forward-migrating to `SCHEMA_VERSION = 3` on next save. Existing `history.db` files automatically create `daily_app_usage` and `time_requests` on startup.
- **Upgrade UX:** If any configured user policy already exists on disk, `EnforcementRuntime` automatically sets `setup_completed = True` so upgraded households skip the First-Run Setup Wizard.
- **Wire & CLI Compatibility:** All existing `org.curfewd.Daemon1` methods/signals and `curfewctl` flags/exit codes remain 100% backwards-compatible.

---

## 5. Security & Privacy Considerations

- **No lingering admin privilege on child sessions:** Walk-up approvals use either offline single-use TOTP (`ApproveWithParentCode`, scoped strictly to caller UID) or `org.curfewd.quick_grant` (`auth_admin`, non-cached).
- **Root-only secret isolation:** `/etc/curfewd/parent_auth.json` is mode `0o600`, opened with `O_NOFOLLOW`, and never returned by `GetParentCodeStatus` or `GetDaemonConfig`—provisioning secret/QR is returned solely during Polkit-authorized `SetupParentCode` initial enrollment or explicit rotation, and `"secret_b32"` is deleted on disable and omitted from non-enrollment payloads.
- **Safe target-user app scanning:** Runs inside the daemon's bounded async worker pool reading `.desktop` entries with strict symlink/size checks and zero code execution.
- **No sensitive `/proc` leakage:** Steam matching inspects only the `SteamAppId=` key in `/proc/<pid>/environ`; `LD_PRELOAD` and other env vars are never copied or logged.
- **Offline UI integrity:** Every `curfewd://app/...` asset (including new `/js/views_schedule.js`) is strictly allowlisted in `_ALLOWED_ASSETS` and verified by unit tests; all dynamic values are HTML-escaped.

---

## 6. Verification & Acceptance Criteria

1. Full unit & live ephemeral D-Bus integration test suite passes (`/usr/bin/python3 -m unittest discover -s tests -v`), covering TOTP RFC 6238 vectors & anti-replay/rate-limit, per-app/category warnings, idle pause, backward clock-jump guard, holidays, Homework Mode, Save Grace, Flatpak/Steam/foreground app matching, passive app usage insights, `TimeRequest` lifecycle, KDE/XFCE/Wayland doctor, target-user appscan, and all new UI bridge RPCs & asset allowlist parity.
2. Static, format, type, JS, packaging, and security gates pass with zero errors/warnings:
   - `/usr/bin/python3 -m compileall -q curfewd tests`
   - `ruff check curfewd tests` & `ruff format --check curfewd tests` & `C901` complexity check
   - `mypy curfewd`
   - `node --check` & `prettier --check` on all web UI assets
   - `bandit -r curfewd` (`0` Low/Medium/High findings)
   - `shellcheck --severity=warning` on all scripts
   - `dpkg-parsechangelog` & `.deb` packaging verification.
