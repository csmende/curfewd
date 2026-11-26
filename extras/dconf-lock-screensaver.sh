#!/bin/sh
# Enforce root-locked desktop screensaver lock-screen settings via dconf.
#
# Prevents non-admin desktop users (e.g. children's accounts) from disabling
# the lock screen in System Settings, which would otherwise let them bypass
# curfewd lockout_action="lock" enforcement.
#
# Covers Cinnamon (Linux Mint), GNOME (Ubuntu), and MATE schemas.
#
# Usage:
#   sudo /usr/share/doc/curfewd/examples/dconf-lock-screensaver.sh
set -eu

if [ "$(id -u)" -ne 0 ]; then
    echo "Error: run this script as root (e.g. via sudo)." >&2
    exit 1
fi

if ! command -v dconf >/dev/null 2>&1; then
    echo "Error: 'dconf' command not found. Install dconf-cli first." >&2
    exit 1
fi

install -d -m 0755 /etc/dconf/profile /etc/dconf/db/local.d /etc/dconf/db/local.d/locks

if [ ! -f /etc/dconf/profile/user ]; then
    cat > /etc/dconf/profile/user <<'EOF'
user-db:user
system-db:local
EOF
    chmod 0644 /etc/dconf/profile/user
elif ! grep -q '^system-db:local$' /etc/dconf/profile/user; then
    printf '\nsystem-db:local\n' >> /etc/dconf/profile/user
fi

cat > /etc/dconf/db/local.d/00-curfewd-lockscreen <<'EOF'
# Managed by curfewd dconf-lock-screensaver.sh
# Ensures desktop lock-screen enforcement cannot be disabled by non-admin users.

[org/cinnamon/desktop/screensaver]
lock-enabled=true

[org/cinnamon/desktop/lockdown]
disable-lock-screen=false

[org/gnome/desktop/screensaver]
lock-enabled=true

[org/gnome/desktop/lockdown]
disable-lock-screen=false

[org/mate/screensaver]
lock-enabled=true

[org/mate/desktop/lockdown]
disable-lock-screen=false
EOF
chmod 0644 /etc/dconf/db/local.d/00-curfewd-lockscreen

cat > /etc/dconf/db/local.d/locks/curfewd-lockscreen <<'EOF'
/org/cinnamon/desktop/screensaver/lock-enabled
/org/cinnamon/desktop/lockdown/disable-lock-screen
/org/gnome/desktop/screensaver/lock-enabled
/org/gnome/desktop/lockdown/disable-lock-screen
/org/mate/screensaver/lock-enabled
/org/mate/desktop/lockdown/disable-lock-screen
EOF
chmod 0644 /etc/dconf/db/local.d/locks/curfewd-lockscreen

dconf update
echo "curfewd: dconf lock-screen policy installed and updated."
