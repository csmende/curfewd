# curfewd Design Specifications

`curfewd` follows a **spec-driven design workflow**: non-trivial features, architectural changes, and UI/usability overhauls are specified here before implementation.

## Specifications Index

| ID | Spec | Status | Target / Version |
|---|---|---|---|
| `001` | [Multi-DE Screen Locking & X11 Fullscreen Grab Release](001-multi-de-screen-locking.md) | Implemented | `v0.6.0` |
| `002` | [Native Control Plane, `org.curfewd.Daemon1` D-Bus API & Modern `curfewctl` CLI](002-curfewd-control-plane-and-cli.md) | Implemented | `v0.7.0` |

## Adding a New Spec

1. Copy the template below into `docs/specs/NNN-short-slug.md`.
2. Define clear problem statement, constraints, security boundaries, compatibility impact, and testable acceptance criteria.
3. Reference the spec in commits and PRs implementing it.

```markdown
# Spec NNN: <Title>

- **Status:** Draft | In Progress | Implemented
- **Target Version:** vX.Y.Z

## 1. Problem & Motivation
## 2. Goals & Non-Goals
## 3. Design & Architecture
## 4. Compatibility & Migration
## 5. Security & Privacy Considerations
## 6. Verification & Acceptance Criteria
```
