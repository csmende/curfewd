"""curfewd — Modern Linux screen-time enforcement daemon and control plane."""

__version__ = "2.0.0"

SCHEMA_VERSION = 3

# Native D-Bus coordinates
DBUS_BUS_NAME = "org.curfewd.Daemon1"
DBUS_OBJECT_PATH = "/org/curfewd/Daemon1"
DBUS_ADMIN_INTERFACE = "org.curfewd.Daemon1.Admin"
DBUS_CLIENT_INTERFACE = "org.curfewd.Daemon1.Client"
DBUS_EVENTS_INTERFACE = "org.curfewd.Daemon1.Events"

# Polkit action IDs
POLKIT_ACTION_READ = "org.curfewd.read"
POLKIT_ACTION_MANAGE = "org.curfewd.manage"
POLKIT_ACTION_LOCK = "org.curfewd.lock"
POLKIT_ACTION_QUICK_GRANT = "org.curfewd.quick_grant"

# System paths
DEFAULT_CONFIG_DIR = "/etc/curfewd"
DEFAULT_STATE_DIR = "/var/lib/curfewd"
DEFAULT_RUNTIME_DIR = "/run/curfewd"
DEFAULT_ADMIN_GROUP = "curfewd"

# CLI exit codes
EXIT_OK = 0
EXIT_ERROR = 1
EXIT_USAGE = 2
EXIT_PERMISSION_DENIED = 3
EXIT_DAEMON_UNAVAILABLE = 4
