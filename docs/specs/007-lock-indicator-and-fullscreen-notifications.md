# Spec 007: Lock-Screen Time-Granted Indicator & Fullscreen Game Notifications

- **Status:** Implemented (`v2.0.0-rc4` / `v2.0.0`)
- **Master Tracker ID:** [`TBD-BELLE-006`](https://drive.google.com/file/d/1fhDXbthqZ13lfV-6iLf2y7OgGBjtgVgV/view) (`future-projects-tbd.md`)
- **Target Version:** `v2.0.0` (`v2.0.0-rc4`)
- **Primary Modules:**
  - `curfewd/engine/screenlock.py`
  - `curfewd/engine/sessions.py`
  - `curfewd/engine/runtime.py`
  - `curfewd/tray/osd.py`
  - `curfewd/tray/app.py`

---

## 1. Problem & Motivation

Two usability gaps remained in `v2.0.0-rc3`:

1. **No lock-screen indicator when time is granted:**
   - While `v2.0.0-rc3` added an informative Cinnamon away message at initial lockout (`_format_lock_message`), granting bonus time (`grant_time()`), pausing (`pause_user()`), or updating today's schedule (`set_today_override()`) while the user was already at the lock screen only emitted `ClientNotification`, which is hidden behind the lock screen.
2. **Desktop notifications hidden during fullscreen games:**
   - Standard `org.freedesktop.Notifications` popups are frequently suppressed by DND/fullscreen inhibition or rendered underneath unredirected / `override-redirect` X11 fullscreen game windows (e.g., Muffin `unredirect-fullscreen-windows`).

---

## 2. Goals & Non-Goals

### Goals
- Update the active Cinnamon lock-screen away message when bonus time is granted, paused, or overridden while locked, and include `session_locked` + `resident`/critical D-Bus hints for GNOME/KDE lock screens.
- Detect X11 fullscreen windows (`_NET_WM_STATE_FULLSCREEN`) and surface warnings/countdowns/alerts via a non-focus-stealing GTK3 `override-redirect` (`Gtk.WindowType.POPUP`) OSD banner, critical/DND-override D-Bus hints, and `AudioChimePlayer` cues.

### Non-Goals
- Never auto-unlock a password-protected session or lock an unlocked session when updating the lock-screen message.
- Never inject `LD_PRELOAD` graphics hooks into game processes.

---

## 3. Design & Architecture

### 3.1 Lock-Screen Time-Granted Indicator
- `ScreenLockEnforcer.update_lock_message()` verifies `is_screensaver_active() is True` first (refusing to lock an unlocked session), then invokes `invoke_backend_lock(ctx, backend, lock_message=...)` on Cinnamon (`org.cinnamon.ScreenSaver.Lock(message)` / `cinnamon-screensaver-command --lock --away-message=...`) on a deduplicated background thread (`update_lock_message_async`).
- `EnforcementRuntime._maybe_notify_lock_screen()` triggers `LogindSessionMonitor.notify_lock_screen()` on `grant_time`, `pause_user`, and `set_today_override` when `screen_locked` is `True` and time is now available, and sets `"session_locked": True` on `ClientNotification`.

### 3.2 Fullscreen Game Notification Delivery
- `curfewd.tray.osd.is_fullscreen_window_active()` checks X11 `_NET_ACTIVE_WINDOW` + `_NET_WM_STATE_FULLSCREEN` via `xprop` (with `Gdk.WindowState.FULLSCREEN` fallback).
- `FullscreenOsdBanner` renders a top-center, non-focus-stealing GTK3 `Gtk.WindowType.POPUP` (`override_redirect=True`, `set_keep_above(True)`, `set_accept_focus(False)`) banner above unredirected fullscreen windows.
- `DesktopNotifier.send()` elevates `urgency=2`, sets `transient=False` and `x-cinnamon-override-dnd=True`, shows `FullscreenOsdBanner` when fullscreen is active, and `TrayController` triggers `AudioChimePlayer.play()`. `DesktopNotifier.dismiss()` cancels pending OSD timeout sources and destroys active banners cleanly on tray shutdown.

---

## 4. Compatibility & Migration

- Additive `ClientNotification` field (`session_locked`); zero schema or D-Bus breaking changes.

---

## 5. Security & Privacy Considerations

- `update_lock_message()` never locks an unlocked session and never bypasses lock-screen authentication.
- `is_fullscreen_window_active()` runs `xprop` strictly via `TRUSTED_BIN_PATH` (`shell=False`, `0.5s` timeout).

---

## 6. Verification & Acceptance Criteria

- Unit tests in `tests/test_screenlock.py` (`TestLockScreenMessage`), `tests/test_runtime.py`, and `tests/test_tray_osd.py` covering lock-screen refusal when unlocked, Cinnamon D-Bus/CLI away-message updates, `grant_time`/`pause_user` lock-screen triggers, X11 fullscreen detection, `FullscreenOsdBanner`, critical/resident hints, and chime alerts.
- Packaging invariants in `tests/test_packaging.py` and `tests/verify_deb.py` (gated in both `.github/workflows/ci.yml` and `.github/workflows/release.yml` before publishing) verifying:
  - `debian/install` uses directory globs covering every tracked Python module (`curfewd/**/*.py`), including `curfewd/tray/osd.py`, and every Web UI/runtime asset.
  - Every tracked `curfewd/**/*.py` module appears in `dpkg-deb -c curfewd_*.deb`, passes an AST internal-import audit on the extracted archive, and imports cleanly in isolated `/usr/bin/python3 -I` runs from `/` (never falling back to the repository working directory).
