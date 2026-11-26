# Spec 001: Verified Multi-DE Screen Locking & Fullscreen X11 Grab Release

- **Status:** Implemented (`v0.6.0`)
- **Primary Modules:**
  - `server/interface/dbus/logind/screenlock.py`
  - `server/interface/dbus/logind/manager.py`
  - `server/interface/dbus/logind/user.py`
  - `client/interface/dbus/notifications.py`
  - `common/utils/config.py`

---

## 1. Problem & Motivation

In upstream `Timekpr-nExT` (`v0.5.11`), setting `LOCKOUT_TYPE = lock` fails silently on several popular Linux desktop setups:

1. **Linux Mint Cinnamon 30-second screensaver exit:** `timekprd` calls `org.freedesktop.login1.Manager.LockSession(session_id)`, which broadcasts a system D-Bus `Lock` signal. On Linux Mint Cinnamon, `cinnamon-screensaver` exits after 30 seconds of inactivity (`inactivity_timeout=30000`) and neither `cinnamon-session` nor `cinnamon-settings-daemon` listens to `logind`'s `Session.Lock` signal. At `00:00`, the signal vanishes with zero errors and the user continues using the desktop.
2. **XFCE / MATE session screensaver divergence:** Depending on whether `xfce4-screensaver`, `mate-screensaver`, or `light-locker` is installed, `logind` `LockSession` alone may not activate the active session locker.
3. **Fullscreen X11 games blocking screen lock:** Fullscreen games (Steam/Proton, SDL2, Minecraft, Roblox Sober) frequently hold X11 pointer/keyboard grabs. `cinnamon-screensaver`'s `cs-event-grabber.c` requires both grabs and silently aborts locking while returning D-Bus success / exit code `0`.
4. **Client lock-state blind spot on Cinnamon:** `timekprc` connects to `org.cinnamon.ScreenSaver` with `follow_name_owner_changes=False`, pinning the proxy to an ephemeral unique bus name (`:1.xxx`) that disappears after 30 seconds and causes `verifySessionAttributes()` to fail on `GetActive()`.

---

## 2. Goals & Non-Goals

### Goals
- Reliably lock active graphical sessions (`x11`, `wayland`, `mir`) across Cinnamon, XFCE, MATE, GNOME, KDE Plasma, LXQt, and generic XDG setups.
- Verify that the screensaver actually became active (`GetActive() == true`) rather than trusting exit code `0`.
- Recover non-destructively when a fullscreen X11 application holds an input grab (without killing user apps or losing unsaved progress).
- Never block `timekprd`'s GLib main loop or touch shared `SystemBus` objects from worker threads.
- Remain 100% backward-compatible with existing `/etc/timekpr/timekpr.conf` files and upstream CLI/D-Bus clients.

### Non-Goals
- Do **not** use `dm-tool switch-to-greeter` as a screen lock (switching VT without locking VT7 can be bypassed with `Ctrl+Alt+F7`).
- Do **not** kill fullscreen games during `LOCKOUT_TYPE = lock` (users who want hard termination select `terminate` or `kill`).

---

## 3. Design & Architecture

### 3.1 Three-Layer Lock Enforcement
1. **Layer 1 (`systemd-logind`):** Schedule `Manager.LockSession(session_id)` via `GLib.timeout_add(100, ...)` on the GLib thread (fixing upstream's `GLib.timeout_add_seconds(0.1, ...)` bug).
2. **Layer 2 (Verified Session D-Bus / CLI Activation):** Extract session metadata (`UID`, `Name`, `Display`, `Desktop`, `Leader`) on the GLib thread, then spawn a deduplicated background daemon thread (`ScreenLockEnforcer.enforce_session_lock_async`):
   - Wait briefly (`0.25s`) for native `logind` locking, then query `NameHasOwner` + `GetActive` over the user's session D-Bus. If already active, stop immediately.
   - Otherwise, invoke the detected DE's screensaver in priority order:
     - Cinnamon: `org.cinnamon.ScreenSaver.Lock("")` (D-Bus-activates after 30s timeout) → `cinnamon-screensaver-command --lock`
     - XFCE: `org.xfce.ScreenSaver.Lock` → `xfce4-screensaver-command --lock` → `xflock4`
     - MATE: `org.mate.ScreenSaver.Lock` → `mate-screensaver-command --lock`
     - GNOME / Unity / Budgie / Pantheon: `org.gnome.ScreenSaver.Lock` → `gnome-screensaver-command --lock`
     - KDE / LXQt / FreeDesktop: `org.freedesktop.ScreenSaver.Lock` (`/org/freedesktop/ScreenSaver` and `/ScreenSaver`) → `xdg-screensaver lock`
   - Wait (`0.60s`) and verify `GetActive()`.
3. **Layer 3 (Staged X11 Fullscreen Grab Release):** Triggered **only** when `TIMEKPR_RELEASE_X11_GRAB_ON_LOCK = True`, session type is `x11`, and verification confirms `GetActive() == False`:
   - **Stage 3a:** Run `xdotool key XF86Ungrab` (zero window disruption when Xorg `AllowDeactivateGrabs` is enabled) and retry lock + verify.
   - **Stage 3b:** If still unlocked, non-destructively run `wmctrl -r :ACTIVE: -b remove,fullscreen` and/or `xdotool getactivewindow windowminimize` so SDL2/Proton/GLFW apps release grabs on `FocusOut`/`UnmapNotify`, then retry lock + verify.

### 3.2 Client Screensaver Proxy Resilience
- Pass `follow_name_owner_changes=True` when binding the session screensaver proxy in `client/interface/dbus/notifications.py`.
- In `verifySessionAttributes()`, check `name_has_owner()` first (reporting `"False"` without spuriously waking sleeping daemons) and catch/reconnect on transient D-Bus errors.

---

## 4. Compatibility & Migration

- Adds two `[MISC]` options in `/etc/timekpr/timekpr.conf`:
  - `TIMEKPR_SESSION_LOCK_FALLBACK = True`
  - `TIMEKPR_RELEASE_X11_GRAB_ON_LOCK = True`
- Existing `v0.5.11` configurations auto-migrate on daemon startup while preserving every existing setting.
- Bundled optional hardening helpers under `/usr/share/doc/curfewd/examples/`:
  - `dconf-lock-screensaver.sh` (root-locks `lock-enabled=true` and `disable-lock-screen=false` in `dconf`)
  - `90-curfewd-ungrab.conf` (optional Xorg `AllowDeactivateGrabs` drop-in)

---

## 5. Security & Privacy Considerations

- **Privilege dropping:** All user-session helper commands run as the target session's `(uid, gid)` with `extra_groups=[]`.
- **Environment allowlist:** Only safe session keys (`DBUS_SESSION_BUS_ADDRESS`, `DISPLAY`, `XAUTHORITY`, `WAYLAND_DISPLAY`, `XDG_RUNTIME_DIR`, `XDG_CURRENT_DESKTOP`, `XDG_SESSION_TYPE`, `HOME`, `USER`, `LOGNAME`, `PATH`, `LC_ALL`) are passed; `LD_PRELOAD`, `LD_LIBRARY_PATH`, and `PYTHONPATH` are never propagated from `/proc`.
- **Trusted binary resolution:** Binaries are resolved strictly inside `/usr/bin:/bin:/usr/sbin:/sbin` (`shell=False`), preventing user `~/bin` hijacking.
- **Bounded execution:** Every helper subprocess enforces a strict timeout (`2.0–3.0s`) and per-session in-flight deduplication prevents thread/process accumulation.

---

## 6. Verification & Acceptance Criteria

- **Automated CI tests:** 22 unit tests (`tests/test_screenlock.py`, `tests/test_admin_and_client.py`) covering desktop selection, `/proc` discovery, env sanitization, privilege drop args, Cinnamon 30s recovery, Stages 3a & 3b grab release, unverifiable minimal lockers, timeout handling, in-flight deduplication, config migration, and client D-Bus reconnect.
- **Live D-Bus wire integration test:** Ephemeral `dbus-daemon` + `/usr/bin/gdbus` test exercising real `NameHasOwner`, `org.cinnamon.ScreenSaver.Lock("")`, and `GetActive` calls in CI.
- **Live desktop validation (verified `2026-11-25` across multiple Linux Mint 22.3 `Zena` Cinnamon `6.6.3` / LightDM / X11 systems):**
  - Drop-in `apt install ./curfewd_0.6.0_all.deb` cleanly replaced `timekpr-next 0.5.11`, installed `xdotool`, auto-migrated `/etc/timekpr/timekpr.conf`, preserved all user schedules/balances, and started `timekpr.service` + `curfewd.service` alias with zero errors.
  - Confirmed live `LOCKOUT_TYPE = lock` enforcement: screen locked in ~260 ms at `00:00` (`INFO: session ... is locked (verified via cinnamon)`), client `timekprc` verified `scrs,key,True`, and manual unlock triggered automatic re-lock within 3 seconds (`org.cinnamon.ScreenSaver.GetActive()` confirmed `(true,)`).
  - Confirmed `dconf-lock-screensaver.sh` overrode an existing user `lock-enabled=false` setting to `lock-enabled=true (writable=false)` and `disable-lock-screen=false (writable=false)`.
  - Confirmed `screenlock.py` dynamically loads cleanly from isolated Python virtualenvs (zero external dependencies) for external admin/automation tools.
