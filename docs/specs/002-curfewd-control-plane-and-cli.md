# Spec 002: Native `curfewd` Control Plane, `org.curfewd.Daemon1` D-Bus API & Modern `curfewctl` CLI

- **Status:** Implemented (`v0.7.0`)
- **Target Version:** `v0.7.0`
- **Primary Modules:**
  - `curfewd/models.py`, `curfewd/parsing.py`, `curfewd/storage.py`, `curfewd/history.py`, `curfewd/migrate.py`
  - `curfewd/engine/` (`evaluator.py`, `screenlock.py`, `sessions.py`, `playtime.py`, `doctor.py`)
  - `curfewd/dbus/` (`polkit.py`, `service.py`, `client.py`)
  - `curfewd/cli/` (`main.py`), `curfewd/tray/` (`app.py`), `curfewd/daemon.py`

---

## 1. Problem & Motivation

`curfewd` `v0.6.x` established reliable multi-DE screen locking (Spec 001) and baseline security hardening, but remained tightly coupled to upstream `Timekpr-nExT`'s 2018 architecture:

1. **Monolithic legacy internals:** ~5,500 LOC of procedural INI parsing and global state (`common/utils/config.py`, `server/config/configprocessor.py`, `server/interface/dbus/daemon.py`) with semicolon-encoded schedules (`7;8;9;...;20[0-30]`) and minimal testability.
2. **Root-GUI anti-pattern:** Admin D-Bus access relied solely on `root` or the static `timekpr` group, forcing the 7,273-line GTK3/Glade admin GUI to run as `root` via `pkexec` — incompatible with modern Wayland security, user theming, and unprivileged UI toolkits.
3. **Missing control-plane primitives:** No `--json` output, no non-destructive Pause/Resume, no D-Bus verified Lock Now endpoint, no SQLite usage history for charts/analytics, no `doctor` health command, and a tray client that detached permanently when the daemon restarted.

Spec 002 performs a **clean architectural break** (`v0.7.0`) to give `curfewd` a modern, Polkit-authorized, JSON-first control plane and CLI designed to power Spec 003's Material Design UI and any public Linux desktop deployment.

---

## 2. Goals & Non-Goals

### Goals
- Clean standalone `curfewd` Python package (Python 3.10+ stdlib + system `dbus`/`gi`/`psutil`, zero third-party PyPI dependencies).
- Versioned D-Bus API (`org.curfewd.Daemon1` at `/org/curfewd/Daemon1`) with JSON payloads, live change signals, **Polkit authorization** for unprivileged admin UIs, and **sender-UID binding** on client methods.
- Atomic, symlink-safe (`O_NOFOLLOW`) schema-versioned JSON storage under `/etc/curfewd/` and `/var/lib/curfewd/`, plus SQLite WAL usage history at `/var/lib/curfewd/history.db`.
- First-class **Pause/Resume** (indefinite or auto-expiring `until_utc`) that never mutates saved schedules.
- First-class **`LockUser`** endpoint exposing Spec 001's verified multi-DE + X11 grab-release `ScreenLockEnforcer` with structured `LockResult` telemetry.
- Modern `curfewctl` subcommand CLI with `--json`, human duration/window syntax, `history`, `doctor [--fix]`, and one-way `migrate-timekpr`.
- Lean, Glade-free, auto-reconnecting `curfewd-tray` desktop companion (`NameOwnerChanged` recovery).
- Hardened `curfewd.service` systemd unit.

### Non-Goals
- Preserving legacy `timekpr` / `timekpra` / `timekprc` / `timekprd` / `com.timekpr.server` / `timekpr.service` compatibility shims (a one-way config importer `curfewctl migrate-timekpr` handles upgrades cleanly).
- Building the new Material Design Admin UI (deferred to **Spec 003** on top of `CurfewClient` + `org.curfewd.Daemon1`).
- Automated GitHub Pages signed APT repository (planned as a focused follow-up after `v0.7.0`).

---

## 3. Design & Architecture

### 3.1 Storage Layout (Schema v1)
- `/etc/curfewd/daemon.json` (`0644 root:root`): daemon-wide polling, warning thresholds, lockout countdown, screenlock fallback, X11 grab release, PlayTime toggle, excluded users.
- `/var/lib/curfewd/users/<username>.json` (`0640 root:curfewd`): per-user `UserPolicy` (`daily_limits`, `weekly_limit`, `monthly_limit`, `curfew_windows`, `lockout_action` defaulting to `"lock"`, `track_inactive`, `hide_tray_icon`, `pause`, `playtime`).
- `/var/lib/curfewd/state/<username>.json` (`0640 root:curfewd`): live accounting counters (`date`, `week_key`, `month_key`, `spent_today`, `spent_balance`, `spent_week`, `spent_month`, `bonus_today`, `playtime_spent_today`, `playtime_balance`, `last_checked_utc`).
- `/var/lib/curfewd/history.db` (`0640 root:curfewd`): SQLite WAL database storing `daily_usage` rollups and `events` audit trail (`lockout`, `grant`, `pause`, `resume`, `policy_update`, `lock_manual`).

### 3.2 D-Bus Contract (`org.curfewd.Daemon1`)
- **Object Path:** `/org/curfewd/Daemon1`
- **`org.curfewd.Daemon1.Admin`:**
  - `ListUsers() -> s` (`org.curfewd.read`)
  - `GetUserStatus(user) -> s` (`org.curfewd.read`)
  - `SetUserPolicy(user, policy_json) -> s` (`org.curfewd.manage`)
  - `GrantTime(user, delta_sec, mode) -> s` (`org.curfewd.manage`)
  - `PauseUser(user, until_utc, reason, hide_tray) -> s` (`org.curfewd.manage`)
  - `ResumeUser(user) -> s` (`org.curfewd.manage`)
  - `LockUser(user) -> s` (`org.curfewd.lock`)
  - `GetUsageHistory(user, days) -> s` (`org.curfewd.read`)
  - `GetDaemonConfig() -> s` (`org.curfewd.read`)
  - `SetDaemonConfig(config_json) -> s` (`org.curfewd.manage`)
  - `RunDoctor() -> s` (`org.curfewd.read`)
- **`org.curfewd.Daemon1.Client` (strictly scoped to D-Bus caller's Unix UID):**
  - `GetMyStatus() -> s`
  - `ReportScreensaverState(nonce, active) -> b`
- **`org.curfewd.Daemon1.Events` (signals):**
  - `UserStatusChanged(username, status_json)`
  - `PolicyChanged(username)`
  - `ClientNotification(username, payload_json)`
  - `ScreensaverChallenge(username, nonce)`

### 3.3 `curfewctl` CLI
Subcommands with global `--json` flag and standardized exit codes (`0` OK, `1` error, `2` validation/usage, `3` permission denied, `4` daemon unavailable):
- `status [user]`, `users`, `grant <user> <+30m|-15m|=2h>`, `limit <user> <dur> [--days ...] [--week ...] [--month ...]`
- `window <user> <HH:MM-HH:MM[,...]> [--days ...]`, `lockout <user> <lock|suspend|terminate|kill|shutdown>`
- `pause <user> [--for ...|--until ...|--today|--indefinite] [--reason ...] [--keep-tray]`, `resume <user>`, `lock <user>`
- `history <user> [--days N]`, `playtime <user> ...`, `config <show|set>`
- `doctor [--fix]`, `migrate-timekpr [--dry-run]`

---

## 4. Compatibility & Migration

- **Breaking change (`v0.7.0`):** Replaces `timekpr.service` with `curfewd.service`, `com.timekpr.server` with `org.curfewd.Daemon1`, `/etc/timekpr` + `/var/lib/timekpr` with `/etc/curfewd` + `/var/lib/curfewd`, and removes the legacy GTK3/Glade Admin UI (`admingui.py` / `admin.glade`).
- **One-way migration:** `curfewctl migrate-timekpr` converts existing `/etc/timekpr/timekpr.conf`, `/var/lib/timekpr/config/timekpr.<user>.conf`, and `/var/lib/timekpr/work/<user>.time` into schema-v1 JSON without deleting original files. Package `postinst` automatically invokes migration when `/etc/timekpr/timekpr.conf` exists and `/etc/curfewd/daemon.json` does not.

---

## 5. Security & Privacy Considerations

- **No root GUIs:** Unprivileged UIs authorize per-action via Polkit (`org.curfewd.policy`, `auth_admin_keep`), while `root` and group `curfewd` pass directly for headless CLI/SSH.
- **Sender-UID isolation:** Client D-Bus calls resolve the caller via `org.freedesktop.DBus.GetConnectionUnixUser(sender)`, eliminating cross-user spoofing.
- **Cryptographic challenge nonces:** `ScreensaverChallenge` uses `secrets.token_urlsafe(16)` and constant-time `hmac.compare_digest` with single-use consumption.
- **Symlink & path safety:** Strict username validation regex (`^[a-z_][a-z0-9._-]{0,31}$`), `O_NOFOLLOW` on file opens/locks, atomic `mkstemp` + `fsync` + `os.replace`, and zero `/tmp` fallback for root PID/log files.
- **Systemd sandboxing:** `ProtectSystem=strict`, `ProtectHome=read-only`, `PrivateTmp=yes`, `ProtectKernelTunables=yes`, `ProtectKernelModules=yes`, `ProtectControlGroups=yes`, `LockPersonality=yes`, `RestrictRealtime=yes`, `MemoryMax=256M`, `TasksMax=64`, while preserving `CAP_SETUID`/`CAP_SETGID`/`CAP_KILL` for privilege-dropped lock helpers.
- **Static security gates:** Bandit SAST (`0` High/Medium/Low), ShellCheck (`0` warnings), CodeQL (`extended` + `remote_and_local`).

---

## 6. Verification & Acceptance Criteria

- Pure unit tests covering parsers, models, atomic storage, symlink refusal, SQLite history, deterministic clock-injected policy evaluation, pause/resume auto-expiry, multi-DE screenlock, `migrate-timekpr`, `doctor`, and `curfewctl` CLI.
- Live ephemeral `dbus-daemon` integration tests verifying `org.curfewd.Daemon1` Admin/Client/Events, Polkit deny enforcement, sender-UID isolation, CSPRNG nonce replay rejection, and `curfewd-tray` auto-reconnect after daemon restart.
- Clean `.deb` build (`dpkg-buildpackage`), `lintian` (`0` errors), package install smoke test, Bandit (`0` findings), ShellCheck (`0` warnings), and CodeQL passing in GitHub Actions.
