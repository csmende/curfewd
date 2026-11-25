# AGENTS.md — curfewd Engineering & AI Agent Guidelines

This repository is developed using a **spec-driven design workflow** with **AI-assisted code generation**. Both human contributors and AI coding agents working in this codebase must follow these rules.

## 1. Spec-Driven Workflow

- Before implementing any new feature, UI redesign, config change, or behavioral change, read existing specifications in [`docs/specs/`](docs/specs/) and create or update the relevant spec (`docs/specs/NNN-<slug>.md`) first.
- Every spec should state:
  - **Problem & Motivation**
  - **Design & Architecture**
  - **Compatibility Impact** (config, D-Bus, CLI, systemd, `.deb` upgrade path)
  - **Security & Privilege Boundaries**
  - **Verification & Acceptance Criteria**
- Update the relevant spec and [`CHANGELOG.md`](CHANGELOG.md) when implementation changes behavior.

## 2. Drop-In Compatibility Invariants

`curfewd` is deployed across multiple Linux systems and serves as a drop-in upgrade over upstream `Timekpr-nExT`:

- Keep `/etc/timekpr/timekpr.conf`, `/var/lib/timekpr/config/`, and `/var/lib/timekpr/work/` backward-compatible; auto-migrate new config keys with safe defaults in [`common/utils/config.py`](common/utils/config.py).
- Preserve `timekpra`, `timekprc`, `timekprd`, `timekpr.service` (with `Alias=curfewd.service`), and the `com.timekpr.server` D-Bus API alongside `curfewctl` / `curfewd`.
- Preserve upstream GPL-3.0 licensing and attribution to Eduards Bezverhijs (`Timekpr-nExT`).

## 3. Security & Reliability Boundaries

- `timekprd` runs as `root`:
  - Never block the GLib main loop with slow I/O or subprocesses; run external lock helpers on deduplicated background threads with bounded timeouts.
  - Never touch shared `dbus-python` `SystemBus` proxies from worker threads; extract pure-Python metadata on the GLib thread first.
  - Always drop privileges (`user=uid`, `group=gid`, `extra_groups=[]`), use explicit `argv` lists (`shell=False`), resolve binaries strictly inside `/usr/bin:/bin:/usr/sbin:/sbin`, and pass only whitelisted environment variables (never copy `LD_PRELOAD`, `LD_LIBRARY_PATH`, or `PYTHONPATH` from `/proc`).
- Never commit secrets, credentials, host-specific IPs, or personal identifiers.

## 4. Verification Requirements

Before committing or opening a PR:

1. Byte-compile all sources: `/usr/bin/python3 -m compileall -q common client server tests`
2. Run the full test suite: `/usr/bin/python3 -m unittest discover -s tests -v`
3. Verify Debian metadata when packaging changes: `dpkg-parsechangelog` and GitHub Actions `CI` (`.deb` build, `lintian`, and install smoke test).
