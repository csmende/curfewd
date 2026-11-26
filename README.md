# curfewd

[![CI](https://github.com/csmende/curfewd/actions/workflows/ci.yml/badge.svg)](https://github.com/csmende/curfewd/actions/workflows/ci.yml)
[![Security](https://github.com/csmende/curfewd/actions/workflows/security.yml/badge.svg)](https://github.com/csmende/curfewd/actions/workflows/security.yml)
[![License: GPL v3](https://img.shields.io/badge/License-GPLv3-blue.svg)](LICENSE)
[![Spec-Driven & AI-Assisted](https://img.shields.io/badge/workflow-spec--driven%20%2B%20AI--assisted-6f42c1.svg)](docs/specs/README.md)

**A modern Linux screen-time enforcement daemon, Polkit-authorized D-Bus control plane, and ergonomic CLI — evolved from [Timekpr-nExT](https://launchpad.net/timekpr-next).**

`curfewd` started as a fork of Timekpr-nExT to fix broken screen locking on modern Linux Mint Cinnamon, XFCE, MATE, and fullscreen X11 games (`v0.6.0`), and has now grown into a clean, modern Linux parental-control & screen-time platform (`v0.7.0+`) built for reliable multi-desktop enforcement, scriptable automation, and an upcoming Material Design UI (`Spec 003`).

---

## Why `curfewd`?

| Capability | Upstream `Timekpr-nExT` (`v0.5.11`) | `curfewd` (`v0.7.0`) |
|---|---|---|
| **Cinnamon / XFCE / MATE screen lock** | Silent failure after `cinnamon-screensaver` 30s timeout | **Verified 3-layer lock:** `logind` → DE session D-Bus/CLI → active check |
| **Fullscreen X11 games (Steam/Proton/Minecraft/Roblox)** | Input grab causes lock to abort silently (`rc=0`) | **Staged X11 grab release** (`XF86Ungrab` → non-destructive unfullscreen/minimize) + retry |
| **Admin authorization** | Root GUI via `pkexec` or static group only | **In-daemon Polkit (`org.curfewd.policy`)** — unprivileged UIs & `root`/`curfewd` group CLI |
| **D-Bus API & Client isolation** | Legacy `com.timekpr.server`, client passes username string | **Versioned `org.curfewd.Daemon1` JSON API** + strict D-Bus **sender-UID binding** |
| **Configuration & History** | Semicolon INI strings (`7;8;...;20[0-30]`), no history | **Atomic schema-v1 JSON** (`/etc/curfewd`, `/var/lib/curfewd`) + **SQLite WAL usage history** |
| **Pause / Resume** | Manual schedule overwrite | **Native non-destructive `pause` / `resume`** (indefinite or timed auto-expiry) |
| **CLI & Automation** | Positional legacy flags, text-only output | **Modern `curfewctl` subcommands** with human durations (`+30m`, `08:00-22:00`) & **`--json`** |
| **Tray companion** | Detaches on daemon restart, 1,894-line Glade XML | **Lean auto-reconnecting `curfewd-tray`** (`NameOwnerChanged` recovery, zero Glade) |
| **Diagnostics & Security** | Manual debugging, bare systemd unit | **`curfewctl doctor [--fix]`**, hardened `curfewd.service`, `0` Bandit/ShellCheck findings |

### Roadmap
- **Spec 001 (`v0.6.0`, Implemented):** Verified multi-DE screen locking, X11 fullscreen grab release, `dconf` lock hardening.
- **Spec 002 (`v0.7.0`, Implemented):** Native `org.curfewd.Daemon1` D-Bus + Polkit control plane, atomic JSON storage, SQLite usage history, native `pause`/`resume`/`lock`/`doctor`, modern `curfewctl` CLI, and auto-reconnecting `curfewd-tray`.
- **Spec 003 (Next):** Public-grade **Material Design Admin & Status UI** running completely unprivileged via Polkit + `CurfewClient` SDK.

---

## Installation (Debian / Ubuntu / Linux Mint)

Download the latest `.deb` from [GitHub Releases](https://github.com/csmende/curfewd/releases):

```bash
curl -fsSLO https://github.com/csmende/curfewd/releases/latest/download/curfewd_0.7.0_all.deb
curl -fsSLO https://github.com/csmende/curfewd/releases/latest/download/SHA256SUMS
sha256sum -c SHA256SUMS
sudo apt install -y ./curfewd_0.7.0_all.deb
```

> **Upgrading from `curfewd 0.6.x` or `timekpr-next`?**
> `v0.7.0` introduces a clean native layout (`/etc/curfewd`, `/var/lib/curfewd`, `curfewd.service`, `org.curfewd.Daemon1`). On first install, if `/etc/timekpr/timekpr.conf` is found, package setup automatically runs `curfewctl migrate-timekpr` (non-destructively importing schedules and balances while leaving `/etc/timekpr` intact as a backup). You can also preview or re-run it anytime with `sudo curfewctl migrate-timekpr --dry-run`.

### Verify & Harden Host

```bash
# Check daemon, D-Bus, Polkit, dconf lock policy, and helper binaries
sudo curfewctl doctor

# Root-lock desktop screensaver settings (prevents users disabling lock screen in Settings)
sudo curfewctl doctor --fix
```

---

## Quick Start (`curfewctl`)

Every subcommand supports `--json` for scripts, dashboards, and automation:

```bash
# Overview of all tracked desktop users (or detailed view for one user)
sudo curfewctl status
sudo curfewctl status alice --json

# Set daily limits & curfew windows with human-readable syntax
sudo curfewctl limit alice 1h30m --days mon-fri
sudo curfewctl limit alice 2h30m --days sat,sun
sudo curfewctl window alice 08:00-21:00 --days sun-thu
sudo curfewctl window alice 08:00-22:30 --days fri,sat
sudo curfewctl lockout alice lock

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

# Manage per-app PlayTime limits
sudo curfewctl playtime alice enable
sudo curfewctl playtime alice add steam "Steam"
sudo curfewctl playtime alice limit 1h --days all

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

Licensed under the **GNU General Public License v3.0 or later (GPL-3.0-or-later)** — see [`LICENSE`](LICENSE) and [`debian/copyright`](debian/copyright). Icon SVG artwork remains in the Public Domain per upstream.
