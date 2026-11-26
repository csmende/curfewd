# curfewd

[![CI](https://github.com/csmende/curfewd/actions/workflows/ci.yml/badge.svg)](https://github.com/csmende/curfewd/actions/workflows/ci.yml)
[![Security](https://github.com/csmende/curfewd/actions/workflows/security.yml/badge.svg)](https://github.com/csmende/curfewd/actions/workflows/security.yml)
[![License: GPL v3](https://img.shields.io/badge/License-GPLv3-blue.svg)](LICENSE)
[![Spec-Driven & AI-Assisted](https://img.shields.io/badge/workflow-spec--driven%20%2B%20AI--assisted-6f42c1.svg)](docs/specs/README.md)

**A modern Linux screen-time enforcement daemon, native Material Design 3 desktop UI, Polkit-authorized D-Bus control plane, and ergonomic CLI — built for real-world desktop & gaming households (a modern successor to [Timekpr-nExT](https://launchpad.net/timekpr-next)).**

`curfewd` delivers reliable screen-time limits, curfews, and individual per-app daily timers across Linux Mint Cinnamon, Ubuntu GNOME, XFCE, MATE, and KDE — including fullscreen X11 games that defeat traditional lock screens — without ever running its desktop UI as `root`.

---

## Key Capabilities (`v2.0.0`)

- **Native Unprivileged Material Design 3 UI (`curfewd-ui`):** WebKitGTK desktop app (GTK4/WebKit 6.0 with automatic GTK3/WebKit2 4.1 fallback) over a 100% offline `curfewd://` scheme with a 60-second First-Run Setup Wizard, surgical live DOM updates, 1-click `+15m`/`+30m`/bonus-note grants, Pause/Resume, Homework Mode, 3-way multi-window schedules, Holidays, Top Apps Insights, Activity Timeline, and a passwordless **My Screen Time** kid self-view (`curfewd-ui --my-status`).
- **Ask for More Time & Offline Parent Access Code:** Kids can request extra daily or app time right from the tray or `My Screen Time`; parents approve in-app, via a **100% offline 6-digit TOTP Parent Access Code** (scannable SVG QR code in Settings—never typing `sudo` passwords in front of kids), or via a single-use non-cached Polkit action (`org.curfewd.quick_grant`).
- **Fair, Anti-Rage Enforcement:** Advance per-app & category warnings (`10m`/`5m`/`1m`), automatic idle pause (`logind IdleHint`), soft audio chimes & a non-focus-stealing 60s corner Countdown HUD for fullscreen games, one-time-per-day **2-Minute Save Grace**, **Homework Mode**, and informative lock-screen away messages.
- **Smart App Limits & Passive Usage Insights:** Individual per-app limits (`0m` Always Blocked up to `4h`, weekday/weekend caps, foreground-only & study-allowed flags), shared **Category Budgets** (e.g., *All Gaming: 1h*), Flatpak cgroup / Steam `SteamAppId` / process-ancestry matching, target-user app picker, and **Top Apps Used** SQLite insights with 1-click *Set Limit*.
- **Verified Multi-DE Screen Locking & Hardening:** 3-layer lock enforcement (`systemd-logind` → DE session D-Bus/CLI across Cinnamon, XFCE, MATE, GNOME, and KDE → active verification) with staged X11 fullscreen grab release, `TestLock` verification, Wayland-aware `curfewctl doctor`, and 1-click lock-screen hardening across `dconf`, KDE Plasma Kiosk, and XFCE.
- **Hardened Async Control Plane & Modern CLI:** Polkit-authorized `org.curfewd.Daemon1` D-Bus API, atomic schema-v3 JSON storage (lossless forward-migration from v1/v2), SQLite WAL history/insights/requests, scriptable `curfewctl --json`, and self-healing `curfewd-tray` (`curfewd-tray.service` + autostart).

### Specifications
- **Spec 001 (`v0.6.0`, Implemented):** [Multi-DE Screen Locking & X11 Fullscreen Grab Release](docs/specs/001-multi-de-screen-locking.md)
- **Spec 002 (`v0.7.0`, Implemented):** [Native Control Plane, `org.curfewd.Daemon1` D-Bus API & Modern `curfewctl` CLI](docs/specs/002-curfewd-control-plane-and-cli.md)
- **Spec 003 (`v1.0.0`, Implemented):** [Native Material Design 3 Desktop UI (`curfewd-ui`) & Per-App Limits](docs/specs/003-material-design-ui.md)
- **Spec 004 (`v1.1.0`, Implemented):** [Complete `curfewd` Rebrand, Legacy Cleanup & Schema v2 (`app_limits`)](docs/specs/004-rebrand-and-app-limits-schema.md)
- **Spec 005 (`v1.2.0`, Implemented):** [Architectural Modularization, Async Admin RPCs & Single-Pass Enforcement Runtime](docs/specs/005-architecture-refactor-and-async-daemon.md)
- **Spec 006 (`v2.0.0`, Implemented):** [Family Ergonomics, Fair Enforcement & App Intelligence](docs/specs/006-family-ergonomics-and-app-intelligence.md)

---

## Installation (Debian / Ubuntu / Linux Mint)

Download the latest `.deb` from [GitHub Releases](https://github.com/csmende/curfewd/releases):

```bash
curl -fsSLO https://github.com/csmende/curfewd/releases/latest/download/curfewd_2.0.0_all.deb
curl -fsSLO https://github.com/csmende/curfewd/releases/latest/download/SHA256SUMS
sha256sum -c SHA256SUMS
sudo apt install -y ./curfewd_2.0.0_all.deb
```

> **Desktop UI Runtime:** `curfewd` recommends `gir1.2-webkit-6.0 | gir1.2-webkit2-4.1` so `apt install ./curfewd_2.0.0_all.deb` automatically installs WebKitGTK on desktop systems while staying lightweight on headless hosts.
>
> **Upgrading from `curfewd 0.7.x`–`1.2.x`?** Existing schema v1/v2 JSON policies/configs and SQLite usage history (`history.db`) automatically forward-migrate to schema v3 without data loss, and configured installs skip the First-Run Setup Wizard automatically.

### Launch the Desktop UI

- Open **curfewd** from your desktop application menu, or run:
  ```bash
  curfewd-ui                       # Admin Control Center (Polkit-authenticated)
  curfewd-ui --my-status           # Passwordless My Screen Time view for current user
  curfewd-ui --my-status --ask-time # Open Ask for More Time dialog directly
  ```

### Verify & Harden Host

```bash
# Check daemon, D-Bus, Polkit, multi-DE lock policy, Wayland/X11 sessions, and helper binaries
sudo curfewctl doctor

# Root-lock desktop screensaver settings (dconf + KDE Kiosk + XFCE; also 1-click in System Health)
sudo curfewctl doctor --fix
```

---

## Quick Start (`curfewctl`)

Every subcommand supports `--json` for scripts, dashboards, and automation:

```bash
# Overview of all tracked desktop users (or detailed view for one user)
sudo curfewctl status
sudo curfewctl status alice --json

# Set weekly limits, split windows, holidays, and roles
sudo curfewctl role alice child
sudo curfewctl limit alice 1h30m --days mon-fri --week 12h
sudo curfewctl limit alice 2h30m --days sat,sun
sudo curfewctl window alice 07:00-08:00,15:30-21:00 --days mon-fri
sudo curfewctl holiday alice add 2026-12-21 2027-01-03 --mode weekend --label "Winter Break"

# Tactical controls: bonus time with chore note, Today overrides, Pause & Homework Mode
sudo curfewctl grant alice +30m --note "Cleaned room"
sudo curfewctl today alice --limit 2h --window 08:00-22:30
sudo curfewctl pause alice --for 2h --reason "family movie"
sudo curfewctl homework alice on --for 1h --reason "math assignment"

# Ask for More Time requests & offline Parent Access Code (TOTP)
sudo curfewctl requests list
sudo curfewctl requests approve 1 --grant 15m
sudo curfewctl parent-code enable

# App Limits, shared Category Budgets & Top Apps Usage Insights
sudo curfewctl app-limits alice enable
sudo curfewctl app-limits alice category set Gaming 1h --weekend 2h
sudo curfewctl app-limits alice add steam "Steam" --limit 45m --weekend 1h30m --category Gaming --foreground-only
sudo curfewctl insights alice --days 7

# Immediately lock or verify screen lock on active graphical session(s)
sudo curfewctl lock alice --json
sudo curfewctl test-lock alice --json
```

---

## Building & Testing from Source

```bash
sudo apt install python3 python3-dbus python3-gi python3-psutil libglib2.0-bin dbus
/usr/bin/python3 -m compileall -q curfewd tests
/usr/bin/python3 -m unittest discover -s tests -v

# Build a local .deb package
sudo apt install build-essential debhelper dh-python dpkg-dev
dpkg-buildpackage -us -uc -b
```

---

## Development Approach (Spec-Driven & AI-Assisted)

`curfewd` is openly developed using a **spec-driven design workflow** with **AI-assisted code generation**:

1. **Specification first ([`docs/specs/`](docs/specs/)):** Non-trivial features, architectural breaks, and UI designs start as written specs defining goals, breaking changes, security boundaries, and acceptance criteria.
2. **AI-assisted, human-directed engineering:** AI coding agents assist with drafting code, tests, packaging, and documentation under human direction and review.
3. **Verified continuously:** Every push runs unit + live ephemeral D-Bus integration tests, `.deb` packaging smoke tests, Bandit Python SAST, ShellCheck, and GitHub CodeQL (`extended` + `remote_and_local`).

See [`CONTRIBUTING.md`](CONTRIBUTING.md) and [`AGENTS.md`](AGENTS.md) for guidelines.

---

## Upstream Attribution & License

**curfewd** originated as a fork of **[Timekpr-nExT](https://launchpad.net/timekpr-next)** (original author: **Eduards Bezverhijs / mjasnik**), retaining full upstream git history and GPL-3.0 licensing.

Licensed under the **GNU General Public License v3.0 or later (GPL-3.0-or-later)** — see [`LICENSE`](LICENSE) and [`debian/copyright`](debian/copyright).
