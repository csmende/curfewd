# curfewd

**Modern Linux screen-time enforcement & parental controls — extending [Timekpr-nExT](https://launchpad.net/timekpr-next) in capability, interface, and day-to-day usability.**

[![CI](https://github.com/csmende/curfewd/actions/workflows/ci.yml/badge.svg)](https://github.com/csmende/curfewd/actions/workflows/ci.yml)
[![Security](https://github.com/csmende/curfewd/actions/workflows/security.yml/badge.svg)](https://github.com/csmende/curfewd/actions/workflows/security.yml)
[![Release](https://img.shields.io/github/v/release/csmende/curfewd?include_prereleases&label=release)](https://github.com/csmende/curfewd/releases)
[![License: GPL v3](https://img.shields.io/badge/License-GPLv3-blue.svg)](LICENSE)
[![Platform](https://img.shields.io/badge/Linux-Mint%20%7C%20Ubuntu%20%7C%20Debian-brightgreen)](#installation)
[![Development: Spec-Driven · AI-Assisted](https://img.shields.io/badge/Development-Spec--Driven%20%C2%B7%20AI--Assisted-6f42c1)](#development-approach-spec-driven--ai-assisted)

`curfewd` started as a fork of [Timekpr-nExT](https://launchpad.net/timekpr-next) and is actively being taken further to expand its capabilities, modernize its user interface, and improve day-to-day usability. I run and rely on it across multiple Linux systems and am building the fixes and enhancements the original maintainer simply didn't have a need for — while keeping `curfewd` a clean, drop-in `.deb` replacement that preserves existing `/etc/timekpr` and `/var/lib/timekpr` configurations.

> **Hit the bug where Timekpr-nExT's `LOCKOUT_TYPE=lock` countdown hits `00:00` on Linux Mint Cinnamon, XFCE, MATE, or during fullscreen games without actually locking the screen?**
> That was our first milestone — fixed out of the box in `v0.6.0` — and it's just the starting point.

---

## Why `curfewd`?

[Timekpr-nExT](https://launchpad.net/timekpr-next) is one of the best open-source parental control and screen-time limit tools for Linux desktops, with daily/weekly/monthly budgets, allowed curfew hours, per-app **PlayTime** limits, and a friendly GTK GUI + CLI.

However, modern Linux desktops — especially **Linux Mint (Cinnamon)**, **XFCE**, **MATE**, and gaming rigs running **Steam / Proton / Minecraft / Roblox (Sober)** — hit several frustrating bugs in upstream `Timekpr-nExT`:

| Issue / Capability | Upstream `Timekpr-nExT` (`v0.5.11`) | `curfewd` (`v0.6.0+`) |
|---|---|---|
| **`LOCKOUT_TYPE = lock` on Linux Mint Cinnamon** | Countdown hits `00:00` and user can keep playing (`cinnamon-screensaver` exits after 30s, so `logind` `LockSession` signals go unheard) | **Fixed** — directly activates & verifies `org.cinnamon.ScreenSaver.Lock("")` over user session D-Bus |
| **`LOCKOUT_TYPE = lock` on XFCE & MATE** | Often ignored depending on `xfce4-screensaver` / `mate-screensaver` / `light-locker` wiring | **Fixed** — auto-detects DE and invokes `org.xfce.ScreenSaver`, `org.mate.ScreenSaver`, `org.gnome.ScreenSaver`, or `org.freedesktop.ScreenSaver` + CLI fallbacks |
| **Fullscreen games blocking screen lock** | Steam/Proton, SDL2, Minecraft, and Roblox hold X11 pointer/keyboard grabs; `cinnamon-screensaver` silently aborts locking | **Fixed** — verifies `GetActive()`; if blocked on X11, sends `XF86Ungrab` and (if needed) non-destructively un-fullscreens/minimizes the game so it drops grabs without losing progress, then locks |
| **Cinnamon client lock-state tracking** | `timekprc` binds to a static unique D-Bus name that dies after 30s, breaking `GetActive()` verification | **Fixed** — follows D-Bus name owner changes, checks `NameHasOwner` before polling, and auto-reconnects |
| **`timekpra --userinfo` boolean display bug** | Prints `False` for `HIDE_TRAY_ICON`, `TRACK_INACTIVE`, `PLAYTIME_ENABLED`, etc. when set to `True` | **Fixed** — accurately formats `dbus.Boolean`, `bool`, and string values |
| **Packaging & Distribution** | Launchpad PPA (`Architecture: any`) | **Automated GitHub Releases** (`Architecture: all` `.deb`), CI test suite with live D-Bus wire tests |
| **Modern CLI & systemd naming** | `timekpra`, `timekpr.service` | **`curfewctl`** & **`curfewd.service`** aliases + **100% backward compatibility** with `timekpra` & `timekpr.service` |
| **Parent lock-screen hardening** | Manual `dconf` setup | **Bundled helper script** (`dconf-lock-screensaver.sh`) preventing kids from turning off the lock screen in Settings |

### Beyond the Initial Fixes: UI, Usability & Capability Roadmap

`curfewd` is not a one-off patch fork. Planned enhancements — driven by real-world use across multiple Linux machines — include:

- **Modernized Admin & Client UI:** Cleaner GTK layout, simpler schedule/curfew editing, improved dark-theme and HiDPI ergonomics, and clearer remaining-time/countdown feedback.
- **First-Class CLI & Automation (`curfewctl`):** Structured JSON output, friendlier flags, and easier scripting/remote-control hooks for households managing multiple Linux desktops.
- **Stronger Multi-DE & Wayland Support:** Ongoing hardening across Cinnamon, XFCE, MATE, GNOME, KDE Plasma, X11, and Wayland compositors.

---

## Supported Desktop Environments & Distros

Tested and designed for Debian/Ubuntu-family systems with `systemd-logind`:

- **Distros:** Linux Mint 21 / 22 (`Cinnamon`, `XFCE`, `MATE`), Ubuntu 22.04 / 24.04+, Debian 12+, Pop!_OS, Zorin OS, MX Linux (with systemd)
- **Desktops:** Cinnamon, XFCE, MATE, GNOME / Ubuntu Desktop, KDE Plasma, LXQt, Unity, Budgie, Wayland & X11 sessions
- **Architectures:** `all` (`amd64`, `arm64` / Raspberry Pi, etc.)

---

## Installation

### Drop-in `.deb` Install (Debian / Ubuntu / Linux Mint)

Download the latest `.deb` from the [GitHub Releases](https://github.com/csmende/curfewd/releases) page and install it with `apt`:

```bash
# Download the latest release package (replace version as needed)
curl -LO https://github.com/csmende/curfewd/releases/latest/download/curfewd_0.6.1_all.deb

# Install (automatically replaces timekpr-next while keeping all user configs & balances!)
sudo apt install ./curfewd_0.6.1_all.deb
```

> **Upgrading from `timekpr-next`?**
> `curfewd` declares `Provides/Replaces/Conflicts: timekpr-next`, keeps `/etc/timekpr/timekpr.conf`, `/var/lib/timekpr/config/`, and `/var/lib/timekpr/work/` untouched, and auto-migrates new config options with safe defaults. Existing scripts calling `timekpra` or `systemctl status timekpr` keep working with zero changes.
>
> If you previously added the upstream Launchpad PPA (`ppa:mjasnik/ppa`), disable or remove it so `apt upgrade` does not try to switch back:
> ```bash
> sudo rm -f /etc/apt/sources.list.d/*mjasnik*
> ```

### Verify the Service Is Running

```bash
systemctl status curfewd
# (or: systemctl status timekpr)
```

---

## Quick Start (`curfewctl` / `timekpra`)

`curfewctl` is a clean CLI alias for `timekpra` (both commands are installed and identical).

```bash
# List tracked users
sudo curfewctl --userlist

# Inspect a user's live configuration, screen-time balance, and curfew schedule
sudo curfewctl --userinfo alice

# Set lockout behavior to screen lock (instead of killing open apps/games)
sudo curfewctl --setlockouttype alice lock

# Grant 30 minutes of bonus screen time today (or subtract with "-")
sudo curfewctl --settimeleft alice + 1800

# Set daily limits (Mon–Fri 2h, Sat–Sun 3h)
sudo curfewctl --setalloweddays alice "1;2;3;4;5;6;7"
sudo curfewctl --settimelimits alice "7200;7200;7200;7200;7200;10800;10800"

# Set curfew hours (e.g. allowed 07:00–20:30 on Monday)
sudo curfewctl --setallowedhours alice 1 "7;8;9;10;11;12;13;14;15;16;17;18;19;20[0-30]"

# Watch live enforcement logs (systemd logs under primary unit timekpr.service)
sudo journalctl -u timekpr -f
```

For the full GTK Administration Control Panel, tray indicator, PlayTime per-app game limits, and every CLI flag, see the complete reference manual in [`docs/TIMEKPR_GUIDE.md`](docs/TIMEKPR_GUIDE.md).

---

## How Multi-DE Screen Locking Works

When a user with `LOCKOUT_TYPE = lock` hits `00:00` (or crosses curfew):

```text
curfewd (root systemd service)
  │
  ├─► Layer 1: systemd-logind Manager.LockSession(session_id)
  │            (native lock for GNOME, KDE Plasma, swayidle/hypridle)
  │
  ├─► Layer 2: Verify via NameHasOwner + GetActive on user's session D-Bus
  │            (privilege-dropped to target UID/GID, sanitized env, 3s timeout)
  │            └─► If unlocked: invoke DE screensaver directly
  │                  • Cinnamon: org.cinnamon.ScreenSaver.Lock("")  ← wakes after 30s exit!
  │                  • XFCE:     org.xfce.ScreenSaver.Lock / xfce4-screensaver-command / xflock4
  │                  • MATE:     org.mate.ScreenSaver.Lock / mate-screensaver-command
  │                  • GNOME:    org.gnome.ScreenSaver.Lock
  │                  • KDE/XDG:  org.freedesktop.ScreenSaver.Lock / xdg-screensaver
  │
  └─► Layer 3: Verify GetActive() == true
               └─► If still unlocked on X11 (e.g. fullscreen Steam/Proton/Minecraft grab):
                     3a. xdotool key XF86Ungrab → retry Lock()
                     3b. wmctrl remove,fullscreen / xdotool windowminimize (non-destructive)
                         → game drops X11 grab on FocusOut → retry Lock() & verify
```

### Security & Stability Design
- **Non-blocking:** Lock enforcement runs on a deduplicated per-session background thread so `curfewd`'s GLib/D-Bus main loop never stalls.
- **Least privilege:** All user-session helpers run with credentials dropped to the target session's `(uid, gid)` with empty supplementary groups (`extra_groups=[]`), a strict environment allowlist (never copying `LD_PRELOAD`, `LD_LIBRARY_PATH`, or `PYTHONPATH` from `/proc`), and trusted system binary paths only (`/usr/bin:/bin`).
- **Tamper-resistant:** Even if a user terminates the desktop tray process (`timekprc`), `curfewd` still queries the session screensaver directly and re-locks the screen within seconds if unlocked before time is granted.

---

## Parent Hardening (Recommended)

### 1. Prevent Kids from Disabling the Lock Screen in Settings

On Cinnamon, GNOME, and MATE, a standard user can normally open **System Settings → Screensaver** and toggle off **"Lock the computer when put to sleep / screensaver"**, which causes `cinnamon-screensaver` to ignore lock requests (`user_can_lock() == False`).

Run the bundled hardening script once to root-lock `lock-enabled = true` and `disable-lock-screen = false` via `dconf`:

```bash
sudo /usr/share/doc/curfewd/examples/dconf-lock-screensaver.sh
```

### 2. Optional: Zero-Minimize Fullscreen Game Locking on X11

Out of the box, `curfewd` breaks fullscreen X11 game grabs non-destructively by un-fullscreening/minimizing the active window before retrying the lock.

If you prefer Xorg to break grabs instantly via `XF86Ungrab` without minimizing the game window, install the optional Xorg drop-in and reboot/re-login:

```bash
sudo install -m 0644 -D /usr/share/doc/curfewd/examples/90-curfewd-ungrab.conf \
    /etc/X11/xorg.conf.d/90-curfewd-ungrab.conf
```

---

## Configuration (`/etc/timekpr/timekpr.conf`)

`curfewd` adds two options under `[MISC]` (both default to `True` and are auto-added on upgrade):

```ini
[MISC]
# Directly invoke the desktop screensaver over user session D-Bus/CLI when
# logind LockSession does not engage (fixes Linux Mint Cinnamon, XFCE, MATE).
TIMEKPR_SESSION_LOCK_FALLBACK = True

# If an X11 screen lock does not engage (e.g. fullscreen game holding an X11
# input grab), release the grab / unfullscreen the active window and retry.
TIMEKPR_RELEASE_X11_GRAB_ON_LOCK = True
```

---

## Building & Testing from Source

```bash
# Install runtime & test dependencies (Debian / Ubuntu / Linux Mint)
sudo apt install python3-dbus python3-gi gir1.2-gtk-3.0 python3-psutil libglib2.0-bin dbus

# Run unit + live ephemeral D-Bus integration tests
/usr/bin/python3 -m unittest discover -s tests -v

# Build a local .deb package
sudo apt install build-essential debhelper dh-python dpkg-dev po-debconf
dpkg-buildpackage -us -uc -b
```

---

## Development Approach (Spec-Driven & AI-Assisted)

`curfewd` is openly developed using a **spec-driven design workflow** with **AI-assisted code generation**:

1. **Specification first ([`docs/specs/`](docs/specs/)):** Non-trivial features, behavioral changes, and UI/usability redesigns start as written design specs defining motivation, compatibility guarantees, security boundaries, and acceptance criteria before code is written.
2. **AI-assisted, human-directed engineering:** AI coding agents assist with drafting code, tests, packaging, and documentation under human direction and review.
3. **Verified on CI and live Linux desktops:** Every change must pass automated unit + live D-Bus wire tests in GitHub Actions and is validated across real multi-machine Linux deployments.

See [`CONTRIBUTING.md`](CONTRIBUTING.md) for contributor guidelines and AI disclosure policy, and [`AGENTS.md`](AGENTS.md) for the project's engineering invariants and agent instructions.

---

## Upstream Attribution & License

**curfewd** is forked from **[Timekpr-nExT](https://launchpad.net/timekpr-next)** (original author: **Eduards Bezverhijs / mjasnik**), preserving full upstream git history, translations, and configuration compatibility.

Licensed under the **GNU General Public License v3.0 or later (GPL-3.0-or-later)** — see [`LICENSE`](LICENSE) and [`debian/copyright`](debian/copyright). Artwork SVG files remain in the Public Domain per upstream.
