"""curfewd daemon entrypoint (`python3 -m curfewd.daemon`)."""

import argparse
import fcntl
import logging
import os
import signal
import sys

from curfewd import (
    DEFAULT_CONFIG_DIR,
    DEFAULT_RUNTIME_DIR,
    DEFAULT_STATE_DIR,
    __version__,
)

logger = logging.getLogger("curfewd")
_PID_LOCK_FH = None


def _configure_logging(level_name: str = "INFO") -> None:
    level = getattr(logging, level_name.upper(), logging.INFO)
    root = logging.getLogger()
    root.setLevel(level)
    if root.handlers:
        return
    try:
        from systemd.journal import JournalHandler

        handler: logging.Handler = JournalHandler(SYSLOG_IDENTIFIER="curfewd")
        handler.setFormatter(logging.Formatter("%(levelname)s %(name)s: %(message)s"))
    except ImportError:
        handler = logging.StreamHandler(sys.stderr)
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    root.addHandler(handler)


def acquire_pid_lock(runtime_dir: str = DEFAULT_RUNTIME_DIR) -> bool:
    """Acquire symlink-safe O_NOFOLLOW exclusive PID lock before truncating."""
    global _PID_LOCK_FH
    os.makedirs(runtime_dir, mode=0o755, exist_ok=True)
    pid_path = os.path.join(runtime_dir, "curfewd.pid")
    flags = os.O_WRONLY | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0)
    fh = None
    try:
        fd = os.open(pid_path, flags, 0o644)
        fh = os.fdopen(fd, "w")
        fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
        fh.truncate(0)
        fh.write(f"{os.getpid()}\n")
        fh.flush()
        _PID_LOCK_FH = fh
        return True
    except BlockingIOError:
        if fh is not None:
            fh.close()
        return False
    except OSError as exc:
        if fh is not None:
            fh.close()
        logger.error("cannot acquire PID lock at %s: %s", pid_path, exc)
        return False


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="curfewd", description="curfewd screen-time enforcement daemon")
    parser.add_argument("--config-dir", default=DEFAULT_CONFIG_DIR)
    parser.add_argument("--state-dir", default=DEFAULT_STATE_DIR)
    parser.add_argument("--runtime-dir", default=DEFAULT_RUNTIME_DIR)
    parser.add_argument("--session-bus", action="store_true", help="Bind to session D-Bus (development/testing)")
    parser.add_argument("--version", action="version", version=f"curfewd {__version__}")
    args = parser.parse_args(argv)

    from dbus.mainloop.glib import DBusGMainLoop

    DBusGMainLoop(set_as_default=True)

    import dbus
    from gi.repository import GLib

    from curfewd.dbus.service import CurfewDaemonService
    from curfewd.history import HistoryStore
    from curfewd.storage import AtomicJsonStore

    store = AtomicJsonStore(config_dir=args.config_dir, state_dir=args.state_dir)
    store.ensure_dirs()
    cfg = store.load_daemon_config()
    _configure_logging(cfg.log_level)

    if not acquire_pid_lock(args.runtime_dir):
        logger.error("curfewd is already running or PID file is locked/unsafe")
        return 1

    logger.info("--- starting curfewd v%s ---", __version__)

    bus = dbus.SessionBus() if args.session_bus else dbus.SystemBus()
    history = HistoryStore(os.path.join(args.state_dir, "history.db"))
    service = CurfewDaemonService(bus=bus, store=store, history=history)

    loop = GLib.MainLoop()

    def _shutdown(*_args) -> bool:
        logger.info("shutting down curfewd v%s", __version__)
        service.flush_states()
        history.prune(cfg.history_retention_days, cfg.event_retention_days)
        loop.quit()
        return False

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            GLib.unix_signal_add(GLib.PRIORITY_DEFAULT, sig, _shutdown)
        except AttributeError:
            signal.signal(sig, lambda _s, _f: _shutdown())

    # Run first tick shortly after startup, then every poll_interval_sec
    GLib.idle_add(lambda: (service.tick(), False)[1])
    GLib.timeout_add_seconds(cfg.poll_interval_sec, service.tick)

    try:
        loop.run()
    finally:
        service.flush_states()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
