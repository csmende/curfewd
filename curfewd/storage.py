"""Symlink-safe, atomic JSON persistence for curfewd configs and accounting state."""

import json
import os
import tempfile
from typing import Any

from curfewd import DEFAULT_CONFIG_DIR, DEFAULT_STATE_DIR
from curfewd.models import DaemonConfig, UserAccountingState, UserPolicy
from curfewd.parsing import validate_username

_NOFOLLOW = getattr(os, "O_NOFOLLOW", 0)


def _read_json_nofollow(path: str) -> dict[str, Any] | None:
    """Read a JSON object without following symlinks; returns None if missing."""
    try:
        fd = os.open(path, os.O_RDONLY | _NOFOLLOW)
    except FileNotFoundError:
        return None
    with os.fdopen(fd, "r", encoding="utf-8") as fh:
        data = json.load(fh)
    if not isinstance(data, dict):
        raise ValueError(f"Expected JSON object in {path}")
    return data


def _write_json_atomic(path: str, payload: dict[str, Any], mode: int = 0o640) -> None:
    """Write JSON atomically via mkstemp + fsync + fchmod + os.replace (never follows symlinks)."""
    parent = os.path.dirname(os.path.abspath(path))
    os.makedirs(parent, mode=0o750, exist_ok=True)
    # Refuse if destination is already a symlink
    if os.path.islink(path):
        raise OSError(f"Refusing to overwrite symlink: {path}")

    encoded = (json.dumps(payload, indent=2, sort_keys=True) + "\n").encode("utf-8")
    fd, tmp_path = tempfile.mkstemp(prefix=".curfewd-tmp-", dir=parent)
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(encoded)
            fh.flush()
            os.fsync(fh.fileno())
        os.chmod(tmp_path, mode)
        os.replace(tmp_path, path)
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


class AtomicJsonStore:
    """Manages /etc/curfewd/daemon.json and /var/lib/curfewd/{users,state}/."""

    def __init__(
        self,
        config_dir: str = DEFAULT_CONFIG_DIR,
        state_dir: str = DEFAULT_STATE_DIR,
    ) -> None:
        self.config_dir = config_dir
        self.state_dir = state_dir
        self.users_dir = os.path.join(state_dir, "users")
        self.accounting_dir = os.path.join(state_dir, "state")

    @property
    def daemon_config_path(self) -> str:
        return os.path.join(self.config_dir, "daemon.json")

    def user_policy_path(self, username: str) -> str:
        safe = validate_username(username)
        return os.path.join(self.users_dir, f"{safe}.json")

    def user_state_path(self, username: str) -> str:
        safe = validate_username(username)
        return os.path.join(self.accounting_dir, f"{safe}.json")

    def ensure_dirs(self) -> None:
        os.makedirs(self.config_dir, mode=0o755, exist_ok=True)
        os.makedirs(self.state_dir, mode=0o750, exist_ok=True)
        os.makedirs(self.users_dir, mode=0o750, exist_ok=True)
        os.makedirs(self.accounting_dir, mode=0o750, exist_ok=True)

    # --- Daemon Config ---

    def load_daemon_config(self) -> DaemonConfig:
        data = _read_json_nofollow(self.daemon_config_path)
        if data is None:
            return DaemonConfig()
        return DaemonConfig.from_dict(data)

    def save_daemon_config(self, config: DaemonConfig) -> None:
        os.makedirs(self.config_dir, mode=0o755, exist_ok=True)
        _write_json_atomic(self.daemon_config_path, config.to_dict(), mode=0o644)

    # --- User Policy ---

    def list_configured_users(self) -> list[str]:
        if not os.path.isdir(self.users_dir):
            return []
        users: list[str] = []
        for entry in sorted(os.listdir(self.users_dir)):
            if not entry.endswith(".json") or entry.startswith("."):
                continue
            name = entry[:-5]
            try:
                validate_username(name)
            except ValueError:
                continue
            users.append(name)
        return users

    def has_user_policy(self, username: str) -> bool:
        path = self.user_policy_path(username)
        return os.path.isfile(path) and not os.path.islink(path)

    def load_user_policy(self, username: str) -> UserPolicy:
        safe = validate_username(username)
        data = _read_json_nofollow(self.user_policy_path(safe))
        if data is None:
            return UserPolicy(username=safe)
        data["username"] = safe
        return UserPolicy.from_dict(data)

    def save_user_policy(self, policy: UserPolicy) -> None:
        os.makedirs(self.users_dir, mode=0o750, exist_ok=True)
        _write_json_atomic(self.user_policy_path(policy.username), policy.to_dict(), mode=0o640)

    # --- User Accounting State ---

    def load_user_state(self, username: str) -> UserAccountingState:
        safe = validate_username(username)
        data = _read_json_nofollow(self.user_state_path(safe))
        if data is None:
            return UserAccountingState(username=safe)
        data["username"] = safe
        return UserAccountingState.from_dict(data)

    def save_user_state(self, state: UserAccountingState) -> None:
        os.makedirs(self.accounting_dir, mode=0o750, exist_ok=True)
        _write_json_atomic(self.user_state_path(state.username), state.to_dict(), mode=0o640)
