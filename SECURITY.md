# Security Policy

## Supported Versions

| Version | Supported |
|---|---|
| `0.6.x` (`main`) | Yes |
| `< 0.6.0` (upstream `timekpr-next`) | See [upstream Launchpad](https://bugs.launchpad.net/timekpr-next) |

## Threat Model & Scope

Because `curfewd` runs its enforcement daemon (`timekprd` / `curfewd`) as **`root`** while interacting with unprivileged desktop user sessions, we treat the following classes of bugs as high-priority security vulnerabilities:

- **Privilege escalation** from an unprivileged desktop user to `root` (including via D-Bus interfaces, `/proc` environment handling, `PATH`/`LD_PRELOAD` injection, or Debian maintainer scripts).
- **Command or shell injection** in daemon lock helpers, CLI wrappers, or packaging scripts.
- **Screen-lock bypasses** allowing an unprivileged user to defeat `LOCKOUT_TYPE = lock` enforcement under documented configurations (assuming root-locked `dconf` settings via `dconf-lock-screensaver.sh`).
- **Symlink / temp-file / config-file tampering** in `/etc/timekpr`, `/var/lib/timekpr`, or `/var/log`.

## Reporting a Vulnerability

Please **do not** open a public GitHub issue for suspected security vulnerabilities.

Instead, report vulnerabilities privately through **GitHub Private Vulnerability Reporting**:

- **[Submit a private security advisory](https://github.com/csmende/curfewd/security/advisories/new)**

Please include:
- `curfewd` version, distro/desktop environment, and display server (X11/Wayland)
- Clear reproduction steps or proof-of-concept
- Expected vs. actual security impact

We aim to acknowledge reports promptly and coordinate a fix and public release before disclosure.

## Automated Security Scanning

This repository runs continuous automated security checks on every push, pull request, and weekly schedule:

- **GitHub CodeQL** (`extended` security & quality query suite for Python and GitHub Actions)
- **Bandit** Python SAST (uploaded as SARIF to GitHub Code Scanning)
- **ShellCheck** static analysis on shell wrappers, Debian maintainer scripts, and hardening scripts
- **GitHub Secret Scanning** with push protection & **Dependabot** automated security updates
