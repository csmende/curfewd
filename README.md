# curfewd

[![CI](https://github.com/csmende/curfewd/actions/workflows/ci.yml/badge.svg)](https://github.com/csmende/curfewd/actions/workflows/ci.yml)
[![Security](https://github.com/csmende/curfewd/actions/workflows/security.yml/badge.svg)](https://github.com/csmende/curfewd/actions/workflows/security.yml)
[![License: GPL v3](https://img.shields.io/badge/License-GPLv3-blue.svg)](LICENSE)
[![Spec-Driven & AI-Assisted](https://img.shields.io/badge/workflow-spec--driven%20%2B%20AI--assisted-6f42c1.svg)](docs/specs/README.md)

**A modern Linux screen-time enforcement daemon, native Material Design 3 desktop UI, Polkit-authorized D-Bus control plane, and ergonomic CLI — evolved from [Timekpr-nExT](https://launchpad.net/timekpr-next).**

`curfewd` started as a fork of Timekpr-nExT to fix broken screen locking on modern Linux Mint Cinnamon, XFCE, MATE, and fullscreen X11 games (`v0.6.0`), rebuilt the daemon & CLI into a hardened Polkit-authorized control plane (`v0.7.0`), and now ships **`v1.0.0`** with a native, unprivileged **Material Design 3 desktop application (`curfewd-ui`)**, **Today-only overrides**, and **individual per-app daily limits**.

---

## Why `curfewd`?

| Capability | Upstream `Timekpr-nExT` (`v0.5.11`) | `curfewd` (`v1.0.0`) |
|---|---|---|
| **Desktop UI** | Legacy root GTK3/Glade (`pkexec`) | **Native unprivileged Material Design 3 UI (`curfewd-ui`)** over WebKitGTK + Polkit, plus passwordless **My Screen Time** (`--my-status`) |
| **Cinnamon / XFCE / MATE screen lock** | Silent failure after `cinnamon-screensaver` 30s timeout | **Verified 3-layer lock:** `logind` → DE session D-Bus/CLI → active check |
| **Fullscreen X11 games (Steam/Proton/Minecraft/Roblox)** | Input grab causes lock to abort silently (`rc=0`) | **Staged X11 grab release** (`XF86Ungrab` → non-destructive unfullscreen/minimize) + retry |
| **App Limits** | Single shared PlayTime pool & manual regexes | **Individual per-app daily limits**, installed `.desktop`/Flatpak picker, per-app `+15m` bonuses, isolated termination |
| **Today-only schedule changes** | Edits recurring weekly schedule | **Today's Time and Schedule** overrides today's allowance & curfew window and auto-resets at local midnight |
| **D-Bus API & Client isolation** | Legacy `com.timekpr.server`, client passes username string | **Versioned `org.curfewd.Daemon1` JSON API** + Polkit (`org.curfewd.policy`) + strict D-Bus **sender-UID binding** |
| **Configuration & History** | Semicolon INI strings (`7;8;...;20[0-30]`), no history | **Atomic schema-v1 JSON** (`/etc/curfewd`, `/var/lib/curfewd`) + **SQLite WAL usage history** |
| **CLI & Automation** | Positional legacy flags, text-only output | **Modern `curfewctl` subcommands** with human durations (`+30m`, `08:00-22:00`) & **`--json`** |
| **Tray companion** | Detaches on daemon restart, 1,894-line Glade XML | **Lean auto-reconnecting `curfewd-tray`** (`NameOwnerChanged` recovery, opens **My Screen Time**) |
| **Diagnostics & Security** | Manual debugging, bare systemd unit | **1-click Lock-Screen Protection** (`curfewctl doctor --fix` / UI), hardened `curfewd.service`, `0` Bandit/ShellCheck findings |

### Specifications
- **Spec 001 (`v0.6.0`, Implemented):** Verified multi-DE screen locking, X11 fullscreen grab release, `dconf` lock hardening.
- **Spec 002 (`v0.7.0`, Implemented):** Native `org.curfewd.Daemon1` D-Bus + Polkit control plane, atomic JSON storage, SQLite usage history, native `pause`/`resume`/`lock`/`doctor`, modern `curfewctl` CLI, and auto-reconnecting `curfewd-tray`.
- **Spec 003 (`v1.0.0`, Implemented):** Native unprivileged Material Design 3 Desktop UI (`curfewd-ui`), Today-only overrides, individual per-app daily limits, installed app scanner, and 1-click lock-screen hardening.

---

## Installation (Debian / Ubuntu / Linux Mint)

Download the latest `.deb` from [GitHub Releases](https://github.com/csmende/curfewd/releases):

```bash
curl -fsSLO https://github.com/csmende/curfewd/releases/latest/download/curfewd_1.0.1_all.deb
curl -fsSLO https://github.com/csmende/curfewd/releases/latest/download/SHA256SUMS
sha256sum -c SHA256SUMS
sudo apt install -y ./curfewd_1.0.1_all.deb
```

> **Desktop UI Runtime:** `curfewd` recommends `gir1.2-webkit-6.0 | gir1.2-webkit2-4.1` so `apt install ./curfewd_1.0.1_all.deb` automatically installs WebKitGTK on desktop systems while staying lightweight on headless hosts.
>
> **Upgrading from `curfewd 0.6.x` or `timekpr-next`?**
> On first install, if `/etc/timekpr/timekpr.conf` is found, package setup automatically runs `curfewctl migrate-timekpr` (non-destructively importing schedules and balances while leaving `/etc/timekpr` intact as a backup).

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

Licensed under the **GNU General Public License v3.0 or later (GPL-3.0-or-later)** — see [`LICENSE`](LICENSE) and [`debian/copyright`](debian/copyright). Icon SVG artwork remains in the Public Domain / GPL-3.0-or-later.
