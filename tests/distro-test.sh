#!/bin/sh
# Distro installer test, run inside a container by CI.
# Usage: sh tests/distro-test.sh [prefix]
#   1. installs runtime deps for the detected package manager
#   2. runs install.sh --from this checkout into a throwaway prefix
#   3. checks the installed files exist and are executable
#   4. uninstalls and verifies removal
set -eu

PREFIX="${1:-/tmp/mango-settings-test-prefix}"
here=$(cd "$(dirname "$0")/.." && pwd)

detect_pm() {
    for tool in pacman apt-get dnf apk zypper xbps-install; do
        if command -v "$tool" >/dev/null 2>&1; then
            echo "$tool"
            return 0
        fi
    done
    echo unknown
}

install_deps_for() {
    # best effort: container images sometimes need updates first
    case "${PKG_PM:-$(detect_pm)}" in
        pacman)       pacman -Syu --noconfirm --needed python-gobject gtk3 ;;
        apt)          apt-get update -qq && apt-get install -y -qq python3-gi gir1.2-gtk-3.0 >/dev/null ;;
        dnf)          dnf install -y python3-gobject gtk3 >/dev/null ;;
        apk)          apk add --no-cache py3-gobject3 gtk+3.0 >/dev/null ;;
        zypper)       zypper --non-interactive --gpg-auto-import-keys refresh >/dev/null &&
                      zypper --non-interactive install python3 python3-gobject gtk3 >/dev/null ;;
        xbps-install) xbps-install -Sy python3 python3-gobject gtk+3 >/dev/null ;;
        *)            echo "unknown package manager; trying plain install" ;;
    esac
}

echo "== install deps =="
install_deps_for || true

echo "== install app =="
sh "$here/install.sh" --no-deps --from "$here" --prefix "$PREFIX"

echo "== verify =="
test -x "$PREFIX/bin/mango-settings"
test -f "$PREFIX/share/mango-settings/mango-settings.py"
test -f "$PREFIX/share/applications/io.github.code_smith404.MangoSettings.desktop"
"$PREFIX/bin/mango-settings" --version
"$PREFIX/bin/mango-settings" --list-pages | grep -q "Appearance"
PYTHONPATH="$PREFIX/share/mango-settings" python3 - <<PY
import importlib.util, sys
spec = importlib.util.spec_from_file_location("ms", "$PREFIX/share/mango-settings/mango-settings.py")
ms = importlib.util.module_from_spec(spec); spec.loader.exec_module(ms)
assert ms.VERSION, "version missing"
print("import OK, VERSION =", ms.VERSION)
PY

echo "== uninstall =="
sh "$here/install.sh" --uninstall --prefix "$PREFIX"
test ! -e "$PREFIX/bin/mango-settings"
test ! -e "$PREFIX/share/mango-settings"

echo "distro test passed"
