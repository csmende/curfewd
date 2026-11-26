# curfewd

[![CI](https://github.com/csmende/curfewd/actions/workflows/ci.yml/badge.svg)](https://github.com/csmende/curfewd/actions/workflows/ci.yml)
[![Security](https://github.com/csmende/curfewd/actions/workflows/security.yml/badge.svg)](https://github.com/csmende/curfewd/actions/workflows/security.yml)
[![License: GPL v3](https://img.shields.io/badge/License-GPLv3-blue.svg)](LICENSE)
[![Spec-Driven & AI-Assisted](https://img.shields.io/badge/workflow-spec--driven%20%2B%20AI--assisted-6f42c1.svg)](docs/specs/README.md)

**A modern Linux screen-time enforcement daemon, native Material Design 3 desktop UI, Polkit-authorized D-Bus control plane, and ergonomic CLI — built for real-world desktop & gaming households (a modern successor to [Timekpr-nExT](https://launchpad.net/timekpr-next)).**

`curfewd` delivers reliable screen-time limits, curfews, and individual per-app daily timers across Linux Mint Cinnamon, Ubuntu GNOME, XFCE, MATE, and KDE — including fullscreen X11 games that defeat traditional lock screens — without ever running its desktop UI as `root`.

---

## Key Capabilities (`v1.1.0`)

- **Native Unprivileged Material Design 3 UI (`curfewd-ui`):** WebKitGTK desktop app (GTK4/WebKit 6.0 with automatic GTK3/WebKit2 4.1 fallback) over a 100% offline `curfewd://` scheme and Polkit D-Bus bridge, plus a passwordless **My Screen Time** self-view (`curfewd-ui --my-status`).
- **Verified Multi-DE Screen Locking:** 3-layer lock enforcement (`systemd-logind` → DE session D-Bus/CLI across Cinnamon, XFCE, MATE, GNOME, and KDE → active verification) that reliably wakes `cinnamon-screensaver` even after its 30-second inactivity exit.
- **Fullscreen X11 Game Grab Release:** Non-destructive staged grab release (`XF86Ungrab` → unfullscreen/minimize) and retry so Steam/Proton, SDL2, Minecraft, and Roblox (Sober) cannot silently block screen locks.
- **Individual Per-App Daily Limits (`app-limits`):** Per-app daily timers, installed `.desktop` / Flatpak / Steam app picker, instant `+15m` per-app bonuses, and isolated per-app termination when an app hits `0m`.
- **Today-Only Overrides (`Today's Time and Schedule`):** Adjust today's allowance or allowed hours without editing the recurring weekly schedule; resets automatically at local midnight.
- **Polkit-Authorized D-Bus Control Plane (`org.curfewd.Daemon1`):** Fine-grained Polkit actions (`org.curfewd.read`, `org.curfewd.manage`, `org.curfewd.lock`), strict D-Bus **sender-UID binding** on client methods, atomic schema-v2 JSON storage (`/etc/curfewd`, `/var/lib/curfewd`), and SQLite WAL usage history & audit logs.
- **Modern CLI & Tray Companion:** Scriptable `curfewctl` with human durations (`+30m`, `08:00-22:00`) and global `--json`, plus a lean, auto-reconnecting `curfewd-tray` (`NameOwnerChanged` recovery) with Material Design 3 Twilight Guard status icons.
- **1-Click Lock-Screen Protection & Hardening:** Built-in `curfewctl doctor --fix` (and 1-click UI button) root-locks desktop screensaver settings via `dconf`; hardened `curfewd.service` (`ProtectSystem=strict`, `PrivateTmp=yes`); `0` Bandit/ShellCheck findings and continuous GitHub CodeQL scanning.

### Specifications
- **Spec 001 (`v0.6.0`, Implemented):** [Multi-DE Screen Locking & X11 Fullscreen Grab Release](docs/specs/001-multi-de-screen-locking.md)
- **Spec 002 (`v0.7.0`, Implemented):** [Native Control Plane, `org.curfewd.Daemon1` D-Bus API & Modern `curfewctl` CLI](docs/specs/002-curfewd-control-plane-and-cli.md)
- **Spec 003 (`v1.0.0`, Implemented):** [Native Material Design 3 Desktop UI (`curfewd-ui`) & Per-App Limits](docs/specs/003-material-design-ui.md)
- **Spec 004 (`v1.1.0`, Implemented):** [Complete `curfewd` Rebrand, Legacy Cleanup & Schema v2 (`app_limits`)](docs/specs/004-rebrand-and-app-limits-schema.md)

---

## Installation (Debian / Ubuntu / Linux Mint)

Download the latest `.deb` from [GitHub Releases](https://github.com/csmende/curfewd/releases):

```bash
curl -fsSLO https://github.com/csmende/curfewd/releases/latest/download/curfewd_1.1.0_all.deb
curl -fsSLO https://github.com/csmende/curfewd/releases/latest/download/SHA256SUMS
sha256sum -c SHA256SUMS
sudo apt install -y ./curfewd_1.1.0_all.deb
```

> **Desktop UI Runtime:** `curfewd` recommends `gir1.2-webkit-6.0 | gir1.2-webkit2-4.1` so `apt install ./curfewd_1.1.0_all.deb` automatically installs WebKitGTK on desktop systems while staying lightweight on headless hosts.
>
> **Upgrading from `curfewd 0.7.x`–`1.0.x`?** Existing schema v1 JSON policies/configs and SQLite usage history (`history.db`) automatically forward-migrate to schema v2 (`app_limits`) without data loss.

### Launch the Desktop UI

- Open **curfewd** from your desktop application menu, or run:
  ```bash
  curfewd-ui               # Admin Control Center (Polkit-authenticated)
  curfewd-ui --my-status   # Passwordless My Screen Time view for current user
  ```

### Verify & Harden Host

```bash
# Check daemon, D-Bus, Polkit, dconf lock policy, and helper binaries
sudo curfewctl doctor

# Root-lock desktop screensaver settings (also available as a 1-click button in System Health)
sudo curfewctl doctor --fix
```

---

## Quick Start (`curfewctl`)

Every subcommand supports `--json` for scripts, dashboards, and automation:

```bash
# Overview of all tracked desktop users (or detailed view for one user)
sudo curfewctl status
sudo curfewctl status alice --json

# Set weekly limits & curfew windows with human-readable syntax
sudo curfewctl limit alice 1h30m --days mon-fri
sudo curfewctl limit alice 2h30m --days sat,sun
sudo curfewctl window alice 08:00-21:00 --days sun-thu
sudo curfewctl window alice 08:00-22:30 --days fri,sat
sudo curfewctl lockout alice lock

# Override today's allowance or allowed window without altering the weekly schedule
sudo curfewctl today alice --limit 2h --window 08:00-22:30
sudo curfewctl today alice --reset

# Grant or adjust bonus time today (+30m, -15m, or =2h)
sudo curfewctl grant alice +30m
sudo curfewctl grant alice -15m

# Pause enforcement temporarily without altering saved schedules
sudo curfewctl pause alice --for 2h --reason "school project"
sudo curfewctl pause alice --today
sudo curfewctl resume alice

# Immediately lock active graphical session(s) with verified multi-DE feedback
sudo curfewctl lock alice --json

# View 7-day screen-time history and audit log
sudo curfewctl history alice --days 7

# Manage individual per-app daily limits
sudo curfewctl app-limits alice enable
sudo curfewctl app-limits alice add steam "Steam" --limit 1h --category Gaming
sudo curfewctl app-limits alice grant steam +15m
sudo curfewctl app-limits alice limit steam 45m

# Watch live enforcement logs
sudo journalctl -u curfewd -f
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
