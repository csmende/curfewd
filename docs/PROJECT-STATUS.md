# `curfewd` — Project Status & Context Handoff

**Current Release:** `v1.1.0` (`main`, published `2026-11-26`)
**Repository:** `https://github.com/csmende/curfewd` (GPLv3, `Architecture: all` Debian/Ubuntu/Linux Mint `.deb`)

---

## Architecture & Completed Specs

| Spec | Title | Shipped In | Status |
|---|---|---|---|
| [Spec 001](specs/001-multi-de-screen-locking.md) | Reliable Multi-DE Screen Lock & X11 Fullscreen Grab Release | `v0.6.0` (ported to native engine in `v0.7.0`) | Shipped & Verified |
| [Spec 002](specs/002-curfewd-control-plane-and-cli.md) | Clean-Break `curfewd` Control Plane, Polkit D-Bus API, Atomic JSON/SQLite Storage, `curfewctl`, `curfewd-tray` | `v0.7.0` | Shipped & Verified |
| [Spec 003](specs/003-material-design-ui.md) | Native Unprivileged Material Design 3 Desktop UI (`curfewd-ui`), Today-Only Overrides & Individual Per-App Limits | `v1.0.0` (`v1.0.1`–`v1.0.4` patches) | Shipped & Verified |
| [Spec 004](specs/004-rebrand-and-app-limits-schema.md) | Complete `curfewd` Rebrand, Legacy Cleanup & Schema v2 (`app_limits`) | `v1.1.0` | Shipped & Verified |
| [Spec 005](specs/005-architecture-refactor-and-async-daemon.md) | Architectural Modularization, Async Admin RPCs & Single-Pass Enforcement Runtime | `main` (pending `v1.2.0`) | Implemented & Verified |

## Design & Implementation Archive

- [`docs/design/spec-002-implementation-plan.md`](design/spec-002-implementation-plan.md) — Spec 002 architectural plan and security model
- [`docs/design/v0.7.0-walkthrough.md`](design/v0.7.0-walkthrough.md) — Spec 002 verification walkthrough

## Release Timeline

- **`v0.6.0` / `v0.6.1`** — Multi-DE lock-screen enforcement + X11 grab release + security hardening on legacy Timekpr base.
- **`v0.7.0`** — Clean-break native `curfewd` daemon (`org.curfewd.Daemon1`), Polkit auth (`org.curfewd.{read,manage,lock}`), atomic JSON config/state (`/etc/curfewd`, `/var/lib/curfewd`), SQLite WAL usage history, hardened `curfewd.service`, `curfewctl --json`, auto-reconnecting `curfewd-tray`.
- **`v1.0.0`** — Native WebKitGTK Material Design 3 UI (`curfewd-ui`, GTK4/WebKit 6.0 with GTK3/WebKit2 4.1 fallback, offline `curfewd://` scheme), Today-only overrides (`SetTodayOverride` / `curfewctl today`), individual per-app daily limits (`GrantAppTime` / `curfewctl app-limits`), `ApplyDoctorFix`, passwordless `GetMyHistory(days)` self-view, Twilight Guard brand icon & AppStream metadata.
- **`v1.0.1`** — Live Mint 22.3 fixes: mainloop-free `CurfewClient._get_proxy()`, stale `0.6.x` `/etc/systemd/system/curfewd.service` alias symlink cleanup in `debian/postinst`, `StartLimit*` moved to `[Unit]`.
- **`v1.0.2`** — App scanner (`curfewd/ui/appscan.py`) resolves symlinked Flatpak `.desktop` exports and unwraps `sh -c '...'` launchers (e.g., Ubuntu/Mint Steam).
- **`v1.0.3` / `v1.0.4`** — Replaced legacy padlock tray icons with Material Design 3 Twilight Guard squircle + shield + crescent-moon status variants (signature indigo + emerald active badge dot, amber, crimson, and teal-emerald), updated AppStream metainfo, and cleaned up test fixtures/docs.
- **`v1.1.0`** — Spec 004 full rebrand & cleanup: renamed tray icons to `curfewd-tray-{ok,warning,alert,unlimited}.svg`, deleted 11 unused legacy icons, `spec/`, `docs/TIMEKPR_GUIDE.md`, and `curfewd/migrate.py` (`migrate-timekpr`), and migrated `playtime` → schema v2 `app_limits` across modules, JSON, D-Bus, SQLite (`app_limits_sec`), CLI, and UI with automatic v1 → v2 forward-migration.

## Local Dev, Verification & Release Commands

```bash
# Unit & D-Bus integration tests
python3 -m unittest discover -s tests -v

# Frontend JS syntax check
node --check curfewd/ui/web/app.js

# Static security & shell checks (matching .github/workflows/security.yml)
bandit -r curfewd -ll
shellcheck --severity=warning bin/curfewctl bin/curfewd bin/curfewd-tray bin/curfewd-ui debian/postinst debian/postrm extras/dconf-lock-screensaver.sh

# Build & lint .deb package
dpkg-buildpackage -us -uc -b
lintian ../curfewd_*_all.deb
```

Tagging and pushing `vX.Y.Z` triggers `.github/workflows/release.yml`, which runs tests, builds `curfewd_X.Y.Z_all.deb` + `SHA256SUMS`, and publishes the GitHub Release automatically.

## Potential Next Milestones (Ideas)

- **Wayland lock-screen testing:** Expand real-desktop verification for GNOME Wayland, KDE Plasma 6 Wayland, and Cinnamon Wayland sessions.
- **App Limits presets & process-tree matching:** Optional grouped categories or Flatpak sandbox ID (`/.flatpak-info`) matching in `curfewd/engine/applimits.py`.
- **Packaging / Distribution:** GPG-signed GitHub Pages APT repository so `apt upgrade` pulls new `curfewd` releases automatically.
