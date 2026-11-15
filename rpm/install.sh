#!/bin/sh
# One-command Fedora / RHEL installer for Mango Settings.
#
#   curl -fsSL https://code-smith404.github.io/MangoWM-Settings/rpm/install.sh | sudo sh
#
# It adds the hosted DNF repository once and installs the package. After
# that, plain dnf works:
#
#   sudo dnf install mango-settings
#
# Uninstall (keeps your MangoWM config):
#   curl -fsSL .../rpm/install.sh | sudo sh -s -- --uninstall
set -eu

REPO_FILE=/etc/yum.repos.d/mango-settings.repo

if [ "$(id -u)" != 0 ]; then
    echo "Run this installer as root (curl ... | sudo sh)." >&2
    exit 1
fi

if [ "${1:-}" = "--uninstall" ]; then
    dnf remove -y mango-settings || true
    rm -f "$REPO_FILE"
    echo "Removed mango-settings and its DNF repository."
    exit 0
fi

mkdir -p "$(dirname "$REPO_FILE")"
cat > "$REPO_FILE" <<'EOF'
[mango-settings]
name=Mango Settings
baseurl=https://code-smith404.github.io/MangoWM-Settings/rpm/noarch
enabled=1
gpgcheck=0
EOF

dnf install -y mango-settings
echo
echo "Installed. Run: mango-settings"
