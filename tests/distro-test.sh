#!/bin/sh
# Runs inside a fresh distro container as root (see .github/workflows/ci.yml):
#   docker run --rm -v "$PWD:/src" IMAGE sh /src/tests/distro-test.sh
set -eu
cd /src
# shellcheck disable=SC1091
. /etc/os-release
echo "=== ${PRETTY_NAME:-unknown}"

# Fresh container images ship without package databases; real systems have them.
case " ${ID:-} ${ID_LIKE:-} " in
  *" arch "*) pacman -Syu --noconfirm >/dev/null ;;  # old images + -Sy = partial upgrade
  *" void "*) xbps-install -Syu -y xbps >/dev/null ;;
esac

sh ./install.sh --print-deps
sh ./install.sh --deps --system
command -v mango-settings
mango-settings --version
mango-settings --list-pages >/dev/null

# The launcher points at the Python the installer chose; it must import GTK 3.
py="$(sed -n 's/^exec "\([^"]*\)".*/\1/p' /usr/local/bin/mango-settings)"
echo "launcher python: $py"
"$py" -c 'import gi; gi.require_version("Gtk", "3.0"); from gi.repository import Gtk; print("GTK", Gtk.get_major_version(), Gtk.get_minor_version())'
"$py" -m unittest discover tests
test -f /usr/local/share/applications/io.github.code_smith404.MangoSettings.desktop
test -f /usr/local/share/icons/hicolor/scalable/apps/io.github.code_smith404.MangoSettings.svg

sh ./install.sh --system --uninstall
if [ -e /usr/local/bin/mango-settings ] || [ -e /usr/local/share/mango-settings ]; then
  echo "still installed after uninstall"; exit 1
fi
echo "=== ${PRETTY_NAME:-unknown}: OK"
