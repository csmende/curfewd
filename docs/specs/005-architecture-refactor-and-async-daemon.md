# Spec 005: Architectural Modularization, Async Admin RPCs & Single-Pass Enforcement Runtime

- **Status:** Implemented
- **Target Version:** `v1.2.0`

## 1. Problem & Motivation

By `v1.1.0`, `curfewd` had a complete feature set and clean security/lint gates (`3497475`), but four structural bottlenecks remained:

1. **Blocking subprocesses on the GLib main loop ([`curfewd/dbus/service.py`](../../curfewd/dbus/service.py)):**
   - `LockUser` invoked `lock_user_sessions(..., async_mode=False)` synchronously on the GLib main loop, blocking ticks and status queries during multi-second X11 grab-release/verification retries.
   - `RunDoctor` (`systemctl is-active`, up to 3s timeout) and `ApplyDoctorFix` (`dconf update`, up to 10s timeout) also ran synchronously on the GLib main loop.
   - This contradicted the core reliability boundary in [`AGENTS.md`](../../AGENTS.md) §3.
2. **Coupled daemon runtime & redundant per-tick work:**
   - `CurfewDaemonService` (680 LOC) mixed D-Bus wire glue + Polkit authorization with enforcement state caches, countdown tracking, CSPRNG screensaver nonces, and tick orchestration.
   - Every 3-second tick called `evaluate_tick()` twice per active user (once for accounting, once inside `_compute_status()`), and `enforce_kill()` re-scanned `/proc` via `psutil.process_iter()` even though `tick()` had just matched running processes moments earlier.
   - `SetDaemonConfig` persisted `poll_interval_sec` and `log_level` without applying them live until service restart.
3. **High-complexity monoliths:**
   - `curfewd/cli/main.py::run_cli` (330 LOC, cyclomatic complexity 102) used one giant `if cmd == ...` chain.
   - `curfewd/engine/evaluator.py::evaluate_tick` (240 LOC, CC=54) duplicated daily/weekly/monthly cap logic and mixed pause expiry, counter updates, per-app evaluation, and state classification.
   - `curfewd/ui/web/app.js` (1,580 LOC) bundled RPC transport, state normalization, HTML rendering, and a 395-line `wireEvents()` function into one file.
4. **Unused legacy model remnants:**
   - `AppLimitsPolicy.override_main` (unexposed legacy Timekpr flag) and `UserAccountingState.app_limits_balance` (write-only remnant from the pre-per-app shared pool) added noise to models and evaluator logic.

## 2. Goals & Non-Goals

### Goals
- Separate pure-Python enforcement orchestration (`EnforcementRuntime` in `curfewd/engine/runtime.py`) from the thin D-Bus transport/Polkit adapter (`CurfewDaemonService`).
- Offload blocking `LockUser`, `RunDoctor`, and `ApplyDoctorFix` subprocess work onto a bounded `ThreadPoolExecutor(max_workers=2)` via `dbus-python` `async_callbacks` while keeping all `SystemBus` access strictly on the GLib main thread.
- Eliminate duplicate `evaluate_tick()` calls and duplicate `/proc` scans on every daemon tick.
- Apply `SetDaemonConfig` updates (`log_level`, `poll_interval_sec`) dynamically at runtime without restarting `curfewd.service`.
- Decompose `evaluate_tick`, `run_cli` (+ `curfewd/cli/formatters.py`), and `curfewd/ui/web/` into cohesive, low-complexity modules while preserving zero-bundler offline packaging.
- Drop unused `override_main` and `app_limits_balance` model fields cleanly while remaining tolerant of existing schema v1/v2 JSON files on disk.

### Non-Goals
- No breaking changes to `org.curfewd.Daemon1` method/signal signatures, `curfewctl` flags/exit codes, `curfewd.service`, or `SCHEMA_VERSION = 2`.
- No Node.js build step or JavaScript bundler — Web UI assets remain plain static files served from `/usr/share/curfewd/ui/`.

## 3. Design & Architecture

### 3.1 EnforcementRuntime & Async D-Bus Service
- **`curfewd/engine/runtime.py::EnforcementRuntime`**:
  - Owns policy/state/session/limited-app caches, countdown timers, lock retry throttling, CSPRNG screensaver challenge nonces, and periodic disk flush.
  - Exposes `build_status_from_eval(...)` so `tick()` builds status broadcasts directly from its single `EvaluationResult`.
  - Passes already-matched expired process records to `AppLimitsMonitor.enforce_kill(..., prematched=expired_matches)`.
  - Emits events through injected callbacks (`on_status_changed`, `on_policy_changed`, `on_client_notification`, `on_screensaver_challenge`), making the entire enforcement loop directly unit-testable without D-Bus.
- **`curfewd/engine/sessions.py::LogindSessionMonitor`**:
  - Splits session locking into `prepare_session_locks(sessions)` (GLib main thread: Layer 1 `logind.LockSession` + pure-Python `SessionContext` construction) and `execute_fallback_locks(contexts, config)` (worker-thread-safe: Layers 2–3 privilege-dropped helpers with zero `SystemBus` access).
- **`curfewd/dbus/service.py::CurfewDaemonService`**:
  - Thin D-Bus + Polkit adapter wrapping `EnforcementRuntime` and a bounded `ThreadPoolExecutor(max_workers=2, thread_name_prefix="curfewd-rpc")`.
  - Uses `@dbus.service.method(..., async_callbacks=("reply_cb", "error_cb"))` for `LockUser`, `RunDoctor`, and `ApplyDoctorFix`:
    1. GLib thread: authorize, validate, extract pure-Python snapshots (and Layer 1 logind lock).
    2. Worker thread: execute blocking subprocesses only.
    3. GLib thread (`GLib.idle_add`): record SQLite audit event (for `LockUser`) and invoke `reply_cb` / `error_cb`.
  - Invokes optional `on_config_changed(cfg)` callback when `SetDaemonConfig` commits, wired in `curfewd/daemon.py` to update the root logger level and reschedule the GLib `timeout_add_seconds` tick source live.

### 3.2 Evaluator & CLI Decomposition
- **`curfewd/engine/evaluator.py`**: Extract `_evaluate_pause`, `_compute_time_left_today`, `_advance_accounting`, `_evaluate_app_limits`, and `_classify_enforcement_state`. Target `evaluate_tick` cyclomatic complexity ≤ 12.
- **`curfewd/cli/formatters.py` & `curfewd/cli/main.py`**: Move terminal/JSON formatters to `formatters.py`; replace the `run_cli` `if`-chain with `_CLIContext` and a `_COMMAND_HANDLERS` dispatch table. Target `run_cli` cyclomatic complexity ≤ 10.

### 3.3 Zero-Bundler Offline Web UI Modularization
- Split `curfewd/ui/web/app.js` into ordered classic scripts sharing `window.CurfewUI` (avoiding WebKitGTK custom-scheme CORS restrictions on `<script type="module">`):
  - `curfewd/ui/web/js/rpc.js` — WebKitGTK JSON RPC bridge
  - `curfewd/ui/web/js/state.js` — constants, central state, formatters, normalizers, dirty tracking, doctor translation
  - `curfewd/ui/web/js/views.js` — pure HTML renderers
  - `curfewd/ui/web/js/events.js` — view-scoped event binders & Add App modal
  - `curfewd/ui/web/app.js` — thin render/bootstrap entrypoint
- Explicitly allowlist `/js/{rpc,state,views,events}.js` in `curfewd/ui/app.py::_ALLOWED_ASSETS` and install to `/usr/share/curfewd/ui/js/` via `debian/install`.

## 4. Compatibility & Migration
- **D-Bus & CLI:** 100% wire-compatible (`org.curfewd.Daemon1` method/signal signatures unchanged; `curfewctl` flags, JSON keys, and exit codes unchanged).
- **JSON Config & State (`SCHEMA_VERSION = 2`):** Existing files containing legacy `override_main`, `app_limits_balance`, or v1 `playtime*` keys continue to load cleanly via `from_dict()` without errors; newly written files omit the removed unused fields.
- **Debian Packaging:** New Python modules (`curfewd/engine/runtime.py`, `curfewd/cli/formatters.py`) and static scripts (`curfewd/ui/web/js/*.js`) are added to `debian/install` and verified by CI `.deb` smoke tests.

## 5. Security & Privilege Boundaries
- Preserves all [`AGENTS.md`](../../AGENTS.md) §3 invariants and strengthens GLib main-loop responsiveness:
  - Worker pool is capped at `max_workers=2`, preventing concurrent RPCs from approaching `TasksMax=64` in `curfewd.service`.
  - Worker threads never access shared `dbus-python` `SystemBus` proxies or `HistoryStore` concurrently with D-Bus dispatch; all D-Bus replies and `HistoryStore` writes finalize on the GLib main thread.
  - WebKitGTK custom URI scheme (`curfewd://app/...`) remains strictly allowlisted (no wildcard directory serving or path traversal).

## 6. Verification & Acceptance Criteria
- `/usr/bin/python3 -m compileall -q curfewd tests` passes.
- `/usr/bin/python3 -m unittest discover -s tests -v` passes, including:
  - `tests/test_runtime.py` proving single `evaluate_tick` + single `find_matching_processes` per tick.
  - Live D-Bus concurrency test proving `GetUserStatus` responds in < 100ms while a slow `LockUser` executes on the RPC worker thread.
  - Live D-Bus test proving `SetDaemonConfig` fires `on_config_changed`.
  - Asset-integrity test verifying every `curfewd://app/...` reference in `index.html` is allowlisted in `_ALLOWED_ASSETS` and exists on disk.
- `ruff check curfewd tests`, `ruff format --check curfewd tests`, `mypy curfewd`, `ruff check curfewd --select C901 --config 'lint.mccabe.max-complexity=25'` (with `run_cli`, `evaluate_tick`, and `tick` all ≤ 15), `node --check` on all JS files, `prettier --check`, `bandit -r curfewd -ll`, and `shellcheck` all pass with 0 errors.
