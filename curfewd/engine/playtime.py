"""PlayTime application/game process monitor and graceful termination."""

import logging
import re
from typing import Any

try:
    import psutil
except ImportError:  # pragma: no cover - optional in minimal test environments
    psutil = None

from curfewd.models import PlayTimeApp

logger = logging.getLogger("curfewd.playtime")


class PlayTimeMonitor:
    """Tracks per-user processes matching PlayTime regex/substring patterns."""

    def __init__(self, term_grace_ticks: int = 3) -> None:
        self._term_grace_ticks = max(1, int(term_grace_ticks))
        self._term_attempts: dict[int, int] = {}

    @staticmethod
    def _compile_patterns(apps: list[PlayTimeApp]) -> list[tuple[PlayTimeApp, re.Pattern[str]]]:
        compiled: list[tuple[PlayTimeApp, re.Pattern[str]]] = []
        for app in apps:
            try:
                rx = re.compile(app.pattern, re.IGNORECASE)
            except re.error:
                rx = re.compile(re.escape(app.pattern), re.IGNORECASE)
            compiled.append((app, rx))
        return compiled

    def find_matching_processes(
        self,
        uid: int,
        apps: list[PlayTimeApp],
    ) -> list[dict[str, Any]]:
        """Return list of {pid, name, label, pattern} currently running for uid."""
        if not apps or psutil is None:
            return []
        compiled = self._compile_patterns(apps)
        matches: list[dict[str, Any]] = []

        for proc in psutil.process_iter(attrs=["pid", "uids", "name", "exe", "cmdline"]):
            try:
                info = proc.info
                uids = info.get("uids")
                if not uids or uids.real != uid:
                    continue
                name = info.get("name") or ""
                exe = info.get("exe") or ""
                cmdline_list = info.get("cmdline") or []
                cmdline = " ".join(cmdline_list)[:512]
                haystack = f"{name} {exe} {cmdline}"
                for app, rx in compiled:
                    if rx.search(haystack):
                        matches.append(
                            {
                                "pid": int(info["pid"]),
                                "name": name,
                                "label": app.label,
                                "pattern": app.pattern,
                            }
                        )
                        break
            except (psutil.Error, OSError, KeyError, TypeError):
                continue

        return matches

    def enforce_kill(self, uid: int, apps: list[PlayTimeApp]) -> list[int]:
        """Send SIGTERM (escalating to SIGKILL after grace ticks) to matched processes."""
        matches = self.find_matching_processes(uid, apps)
        active_pids = {m["pid"] for m in matches}
        # Clean stale tracking entries
        for stale in list(self._term_attempts):
            if stale not in active_pids:
                self._term_attempts.pop(stale, None)

        signaled: list[int] = []
        for m in matches:
            pid = m["pid"]
            attempts = self._term_attempts.get(pid, 0) + 1
            self._term_attempts[pid] = attempts
            try:
                proc = psutil.Process(pid)
                if proc.uids().real != uid:
                    continue
                if attempts <= self._term_grace_ticks:
                    logger.warning("PlayTime expired: sending SIGTERM to pid=%d (%s) uid=%d", pid, m["label"], uid)
                    proc.terminate()
                else:
                    logger.warning("PlayTime expired: sending SIGKILL to pid=%d (%s) uid=%d", pid, m["label"], uid)
                    proc.kill()
                signaled.append(pid)
            except (psutil.Error, OSError, ValueError):
                continue
        return signaled
