# AGENTS.md — curfewd Engineering & AI Agent Guidelines

This repository is developed using a **spec-driven design workflow** with **AI-assisted code generation**. Both human contributors and AI coding agents working in this codebase must follow these rules.

## 1. Spec-Driven Workflow

- Before implementing any new feature, UI redesign, config change, or behavioral change, read existing specifications in [`docs/specs/`](docs/specs/) and create or update the relevant spec (`docs/specs/NNN-<slug>.md`) first.
- Every spec should state:
  - **Problem & Motivation**
  - **Design & Architecture**
  - **Compatibility Impact** (config schema, D-Bus, CLI, systemd, `.deb` upgrade path)
  - **Security & Privilege Boundaries**
  - **Verification & Acceptance Criteria**
- Update the relevant spec and [`debian/changelog`](debian/changelog) when implementation changes behavior.

## 2. Core Platform Invariants (`v0.7.0+`)

- **Native `curfewd` namespace:** Primary service is `curfewd.service`, D-Bus bus is `org.curfewd.Daemon1` (`/org/curfewd/Daemon1`), config lives in `/etc/curfewd/daemon.json`, and state/history live in `/var/lib/curfewd/`.
- **Schema-versioned JSON & SQLite:** All persisted configs include `"schema_version"` (`SCHEMA_VERSION = 2`) and forward-migrate cleanly without data loss.
- **Unprivileged UI model:** Never require running desktop GUIs as `root` via `pkexec`; all admin actions authorize in-daemon via Polkit (`org.curfewd.policy`) or `root`/`curfewd` group membership.
- **License & attribution:** Preserve GPL-3.0-or-later licensing and attribution to Eduards Bezverhijs (`Timekpr-nExT`).

## 3. Security & Reliability Boundaries

- `curfewd` runs as `root`:
  - Never block the GLib main loop with slow I/O or subprocesses; run external lock helpers on deduplicated background threads with bounded timeouts.
  - Never touch shared `dbus-python` `SystemBus` proxies from worker threads; extract pure-Python metadata on the GLib thread first.
  - Always drop privileges (`user=uid`, `group=gid`, `extra_groups=[]`), use explicit `argv` lists (`shell=False`), resolve binaries strictly inside `/usr/bin:/bin:/usr/sbin:/sbin`, and pass only whitelisted environment variables (never copy `LD_PRELOAD`, `LD_LIBRARY_PATH`, or `PYTHONPATH` from `/proc`).
  - Always bind unprivileged D-Bus client calls to the caller's real Unix UID (`GetConnectionUnixUser(sender)`), open state/PID files with `O_NOFOLLOW`, write atomically (`mkstemp` + `fsync` + `os.replace`), and generate challenge tokens with `secrets` + `hmac.compare_digest`.
- Never commit secrets, credentials, host-specific IPs, or personal identifiers.

## 4. Verification Requirements

Before committing or opening a PR:

1. Byte-compile all sources: `/usr/bin/python3 -m compileall -q curfewd tests`
2. Run the full test suite: `/usr/bin/python3 -m unittest discover -s tests -v`
3. Run lint, format, typecheck, and JS syntax checks: `ruff check curfewd tests`, `ruff format --check curfewd tests`, `mypy curfewd`, and `for f in curfewd/ui/web/app.js curfewd/ui/web/js/*.js; do node --check "$f"; done`
4. Verify Debian metadata & security gates: `dpkg-parsechangelog`, Bandit (`0` Medium/High), ShellCheck (`0` warnings), and GitHub Actions `CI`, `Security`, and `CodeQL`.
