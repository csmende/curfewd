# Security Policy

## Supported Versions

| Version | Supported |
|---|---|
| `1.x` (`main`) | Yes |
| `0.7.x` | Upgrade to `1.x` |
| `< 0.7.0` (legacy `timekpr` compat) | Unsupported |

## Threat Model & Scope

Because `curfewd` runs its enforcement daemon (`curfewd.service`) as **`root`** while interacting with unprivileged desktop user sessions, we treat the following classes of bugs as high-priority security vulnerabilities:

- **Privilege escalation** from an unprivileged desktop user to `root` (including via D-Bus `org.curfewd.Daemon1`, Polkit policy bypass, `/proc` environment handling, `PATH`/`LD_PRELOAD` injection, or Debian maintainer scripts).
- **Cross-user spoofing** on unprivileged client D-Bus methods (`GetMyStatus`, `ReportScreensaverState` must strictly bind to `GetConnectionUnixUser(sender)`).
- **Command or shell injection** in daemon lock helpers, CLI wrappers, or packaging scripts.
- **Screen-lock bypasses** allowing an unprivileged user to defeat `lockout_action = "lock"` enforcement under documented configurations (assuming root-locked `dconf` settings via `curfewctl doctor --fix`).
- **Symlink / temp-file / config-file tampering** in `/etc/curfewd`, `/var/lib/curfewd`, or `/run/curfewd`.

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

- **GitHub CodeQL** (`extended` security & quality query suite, `remote_and_local` threat model for Python and GitHub Actions)
- **Bandit** Python SAST (uploaded as SARIF to GitHub Code Scanning, gated on Medium/High severity)
- **ShellCheck** static analysis on shell wrappers, Debian maintainer scripts, and hardening scripts
- **GitHub Secret Scanning** with push protection & **Dependabot** automated security updates
