# Spec 004: Complete `curfewd` Rebrand, Legacy Cleanup & Schema v2 (`app_limits`)

- **Status:** Implemented
- **Target Version:** `v1.1.0`
- **Depends On:** [Spec 002](002-curfewd-control-plane-and-cli.md), [Spec 003](003-material-design-ui.md)

---

## 1. Problem & Motivation

`v0.7.0` and `v1.0.x` replaced the legacy `Timekpr-nExT` daemon, CLI, packaging, and desktop UI with native `curfewd` components, leaving behind several transitional artifacts:

1. **Legacy filenames & dead assets:** Active Material Design 3 tray icons were still named `timekpr-padlock-*.svg`, 11 unused legacy PNG/SVG icons (~270 KB) remained in `resource/icons/`, an obsolete `v0.5.11` RPM `spec/` directory and 1,164-line `docs/TIMEKPR_GUIDE.md` remained in the repository, and `debian/copyright` still carried an outdated public-domain icon stanza.
2. **Transitional migration bridges:** `curfewd/migrate.py`, `curfewctl migrate-timekpr`, and `debian/postinst` `/etc/timekpr` auto-migration hooks added maintenance overhead without an active legacy user base to carry forward.
3. **Mixed `playtime` vs. `app_limits` terminology:** While `curfewd-ui` and `curfewctl app-limits` adopted the clear **App Limits** name in `v1.0.0`, internal modules (`curfewd/engine/playtime.py`), dataclasses (`PlayTimeApp`, `PlayTimePolicy`), persisted JSON keys (`playtime`, `playtime_enabled`, `playtime_status`), SQLite columns (`playtime_sec`), and a CLI alias (`playtime`) still reflected legacy Timekpr-nExT terminology.
4. **Stale documentation & issue templates:** `.github/ISSUE_TEMPLATE/bug_report.md`, `CONTRIBUTING.md`, `SECURITY.md`, and `README.md` still contained pre-`v1.0` references (`journalctl -u timekpr`, `curfewctl --userinfo`, `common client server` directories).

---

## 2. Design & Architecture

### 2.1 Native Tray Icon Filenames
All 4 Material Design 3 Twilight Guard tray status SVGs under `resource/icons/` (installed to `/usr/share/curfewd/icons/`) adopt native `curfewd-tray-*` names:

| Former Filename | Native Filename | Enforcement State(s) |
|---|---|---|
| `timekpr-padlock-limited-green.svg` | `curfewd-tray-ok.svg` | `ok` (indigo squircle + emerald status badge) |
| `timekpr-padlock-limited-yellow.svg` | `curfewd-tray-warning.svg` | `warning` (amber squircle) |
| `timekpr-padlock-limited-red.svg` | `curfewd-tray-alert.svg` | `countdown`, `locked_out` (crimson squircle) |
| `timekpr-padlock-unlimited-green.svg` | `curfewd-tray-unlimited.svg` | `paused`, `unrestricted` (teal-emerald squircle) |

All 11 unused legacy `timekpr-*.{png,svg}` files, `spec/`, `docs/TIMEKPR_GUIDE.md`, and `curfewd/migrate.py` are deleted.

### 2.2 Schema Version 2 (`app_limits` Everywhere)
`SCHEMA_VERSION` advances from `1` to `2`:

- **Module & classes:** `curfewd/engine/playtime.py` → `curfewd/engine/applimits.py` (`AppLimitsMonitor`), `PlayTimeApp` → `AppLimitRule`, `PlayTimePolicy` → `AppLimitsPolicy`.
- **Persisted JSON & D-Bus status keys:**
  - `/etc/curfewd/daemon.json`: `playtime_enabled` → `app_limits_enabled`
  - `/var/lib/curfewd/users/<user>.json`: `playtime` → `app_limits`
  - `/var/lib/curfewd/state/<user>.json`: `playtime_spent_today` / `playtime_balance` → `app_limits_spent_today` / `app_limits_balance`
  - `org.curfewd.Daemon1` `UserStatus`: `playtime_status` → `app_limits_status`
  - Audit event kind: `playtime_kill` → `app_limit_kill`
- **SQLite history (`history.db`):** `daily_usage.playtime_sec` → `daily_usage.app_limits_sec` (and returned JSON `app_limits_sec`).
- **CLI (`curfewctl`):** Removes `migrate-timekpr` and the legacy `playtime` subcommand alias (`curfewctl app-limits` is canonical).

### 2.3 Strategic Discoverability & GPL-3.0 Attribution
- **GPL-3.0-or-later attribution preserved:** `LICENSE`, `debian/copyright` (`Comment:` & `Files: *`), git history, and `README.md`'s **Upstream Attribution & License** section retain full credit to Eduards Bezverhijs (`Timekpr-nExT`).
- **Strategic discoverability preserved:** `curfewd.desktop` retains `timekpr` in `Keywords=`, `debian/control` mentions Timekpr-nExT lineage in the package description (and keeps `Conflicts:` against `timekpr*` packages so two screen-time daemons cannot fight on the same host), and GitHub repository topics retain `timekpr` / `timekpr-next`.

---

## 3. Compatibility Impact

- **Automatic forward-migration from `curfewd v0.7.0–v1.0.4` (`schema_version: 1` → `2`):**
  - `DaemonConfig.from_dict()`, `UserPolicy.from_dict()`, and `UserAccountingState.from_dict()` transparently accept v1 `playtime*` keys and persist v2 `app_limits*` keys on the next write.
  - `HistoryStore._init_db()` inspects `PRAGMA table_info(daily_usage)` and executes `ALTER TABLE daily_usage RENAME COLUMN playtime_sec TO app_limits_sec` in place when upgrading an existing v1 `history.db`, preserving 100% of historical usage data.
  - `debian/postinst` removes any stale `/usr/share/curfewd/icons/timekpr-padlock-*.svg` files on upgrade.
- **Breaking changes for external API consumers:**
  - Consumers parsing `curfewctl status --json` or D-Bus `UserStatus` / `GetUsageHistory` JSON must read `app_limits`, `app_limits_status`, `app_limits_enabled`, and `app_limits_sec` instead of `playtime*`.
  - `curfewctl migrate-timekpr` and the `curfewctl playtime` CLI alias are removed.

---

## 4. Security & Privilege Boundaries

No new privileges, D-Bus methods, Polkit actions, or root attack surfaces are introduced. Removing `curfewd/migrate.py` and legacy `postinst` `/etc/timekpr` hooks reduces root-executed code surface.

---

## 5. Verification & Acceptance Criteria

1. Zero legacy `timekpr*` files remain anywhere in the repository (`find . -not -path './.git/*' -iname '*timekpr*'`).
2. `curfewd` passes `/usr/bin/python3 -m compileall -q curfewd tests`, full `unittest` suite (including schema v1 → v2 JSON + SQLite column forward-migration tests and `TrayController.select_icon_name` icon mapping tests), `node --check curfewd/ui/web/app.js`, `dpkg-parsechangelog`, `bandit -r curfewd -ll`, and `shellcheck`.
3. CI `.deb` build, `lintian`, and smoke-test install confirm `/usr/share/curfewd/icons/curfewd-tray-{ok,warning,alert,unlimited}.svg` are packaged and zero `timekpr-padlock-*` files exist.
