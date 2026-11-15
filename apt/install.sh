#!/bin/sh
# One-command Debian / Ubuntu installer for Mango Settings.
#
#   curl -fsSL https://code-smith404.github.io/MangoWM-Settings/apt/install.sh | sudo sh
#
# It adds the hosted APT repository once and installs the package. After
# that, plain apt works:
#
#   sudo apt install mango-settings
#
# Uninstall (keeps your MangoWM config):
#   curl -fsSL .../apt/install.sh | sudo sh -s -- --uninstall
set -eu

LIST=/etc/apt/sources.list.d/mango-settings.list
REPO_LINE="deb [trusted=yes] https://code-smith404.github.io/MangoWM-Settings/apt stable main"

if [ "$(id -u)" != 0 ]; then
    echo "Run this installer as root (curl ... | sudo sh)." >&2
    exit 1
fi

if [ "${1:-}" = "--uninstall" ]; then
    apt-get remove -y mango-settings || true
    rm -f "$LIST"
    apt-get update -qq || true
    echo "Removed mango-settings and its APT repository."
    exit 0
fi

printf '%s\n' "$REPO_LINE" > "$LIST"
apt-get update -qq
DEBIAN_FRONTEND=noninteractive apt-get install -y mango-settings
echo
echo "Installed. Run: mango-settings"
