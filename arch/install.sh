#!/bin/sh
# shellcheck disable=SC2016  # $arch is written literally into pacman.conf
# One-command Arch / Manjaro installer for Mango Settings.
#
#   curl -fsSL https://code-smith404.github.io/MangoWM-Settings/arch/install.sh | sudo sh
#
# It adds the hosted pacman repository to /etc/pacman.conf once and then
# installs the package with pacman. After that, plain pacman works:
#
#   sudo pacman -S mango-settings
#
# Uninstall (keeps your MangoWM config):
#   curl -fsSL .../arch/install.sh | sudo sh -s -- --uninstall
set -eu

REPO_NAME="mango-settings"
REPO_URL="https://code-smith404.github.io/MangoWM-Settings/arch"
CONF="${MANGO_PACMAN_CONF:-/etc/pacman.conf}"

if [ "$(id -u)" != 0 ]; then
    echo "Run this installer as root (curl ... | sudo sh)." >&2
    exit 1
fi

if [ ! -f "$CONF" ]; then
    echo "Not an Arch-style system: $CONF not found." >&2
    exit 1
fi

remove_repo() {
    # Remove the [mango-settings] section up to the next section header.
    awk -v name="[$REPO_NAME]" '
        $0 == name { skip = 1; next }
        skip && /^\[/ { skip = 0 }
        !skip
    ' "$CONF" > "$CONF.mango-new"
    cat "$CONF.mango-new" > "$CONF"
    rm -f "$CONF.mango-new"
}

if [ "${1:-}" = "--uninstall" ]; then
    pacman -Rns --noconfirm "$REPO_NAME" 2>/dev/null || true
    remove_repo
    pacman -Syu --noconfirm >/dev/null 2>&1 || true
    echo "Removed mango-settings and its pacman repository."
    exit 0
fi

if ! grep -qx "\[$REPO_NAME\]" "$CONF"; then
    printf '\n[%s]\nSigLevel = Optional TrustAll\nServer = %s/$arch\n' \
        "$REPO_NAME" "$REPO_URL" >> "$CONF"
    echo "Added the [$REPO_NAME] repository to $CONF"
fi

pacman -Syu --needed --noconfirm "$REPO_NAME"
echo
echo "Installed. Run: mango-settings"
