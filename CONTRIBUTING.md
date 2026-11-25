# Contributing to curfewd

Thank you for your interest in **curfewd**! This project is actively developed for real-world multi-machine Linux households and welcomes bug reports, design specs, UI/usability improvements, and pull requests.

## Spec-Driven & AI-Assisted Development Policy

`curfewd` is openly developed using a **spec-driven design approach** with **AI-assisted code generation**.

- **Specs come first:** Non-trivial features, behavioral changes, and UI/usability redesigns begin with a concise specification in [`docs/specs/`](docs/specs/) outlining the problem statement, design decisions, compatibility guarantees, security boundaries, and acceptance criteria before implementation starts.
- **Transparency on AI assistance:** AI coding agents are used to help draft code, tests, packaging, and documentation in this repository. External contributors are also welcome to use AI coding tools, provided they:
  1. Understand, review, and take responsibility for every line submitted.
  2. Follow the spec-driven process for new features or behavior changes.
  3. Verify their changes with automated tests and (where applicable) real Linux desktop testing.
  4. Disclose AI assistance in the pull request description when significant code was AI-generated.

## Engineering Guardrails

See [`AGENTS.md`](AGENTS.md) for the full set of architectural invariants and security rules. At a high level:

1. **Drop-in compatibility:** Preserve compatibility with existing `/etc/timekpr/timekpr.conf`, `/var/lib/timekpr/`, `com.timekpr.server` D-Bus interfaces, `timekpra` CLI flags, and `timekpr.service` unless a spec explicitly defines a migration path.
2. **Least privilege:** The daemon runs as `root`. Any interaction with a desktop user's session must drop privileges to the target `(uid, gid)`, use a strict environment allowlist, avoid `shell=True`, resolve binaries from trusted system paths, and enforce bounded timeouts.
3. **Tests required:** Add or update unit/integration tests under `tests/` for bug fixes and new behavior.

## Local Development & Testing

```bash
# Install runtime and D-Bus test dependencies (Debian / Ubuntu / Linux Mint)
sudo apt install python3-dbus python3-gi gir1.2-gtk-3.0 python3-psutil libglib2.0-bin dbus

# Syntax check all Python files
/usr/bin/python3 -m compileall -q common client server tests

# Run unit + live ephemeral D-Bus integration tests
/usr/bin/python3 -m unittest discover -s tests -v
```

## License

All contributions are licensed under the **GNU General Public License v3.0 or later (GPL-3.0-or-later)**, consistent with upstream Timekpr-nExT (see [`LICENSE`](LICENSE)).
