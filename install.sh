#!/usr/bin/env bash
# Mango Settings installer.
#
# Online (nothing to clone):
#   curl -fsSL https://raw.githubusercontent.com/code-smith404/MangoWM-Settings/main/install.sh | bash
#
# Options (pass after `bash -s --` when piping, e.g. `| bash -s -- --uninstall`):
#   --system        install for all users into /usr/local (uses sudo)
#   --prefix DIR    install into DIR (default: ~/.local)
#   --ref REF       branch or tag to install (default: main)
#   --deps          install missing dependencies with your package manager
#   --no-deps       don't check dependencies
#   --uninstall     remove Mango Settings
#   -h, --help      show this help
set -euo pipefail

REPO="code-smith404/MangoWM-Settings"
APP_ID="io.github.code_smith404.MangoSettings"
REF="${MANGO_SETTINGS_REF:-main}"
PREFIX="${PREFIX:-$HOME/.local}"
SUDO=""
ACTION="install"
DEPS="check"

bold() { printf '\033[1m%s\033[0m\n' "$*"; }
info() { printf '\033[1;33m::\033[0m %s\n' "$*"; }
ok()   { printf '\033[1;32m✓\033[0m %s\n' "$*"; }
die()  { printf '\033[1;31merror:\033[0m %s\n' "$*" >&2; exit 1; }

usage() {
  cat <<'EOF'
Mango Settings installer

  curl -fsSL https://raw.githubusercontent.com/code-smith404/MangoWM-Settings/main/install.sh | bash
  curl -fsSL .../install.sh | bash -s -- [options]

Options:
  --system        install for all users into /usr/local (uses sudo)
  --prefix DIR    install into DIR (default: ~/.local)
  --ref REF       branch or tag to install (default: main)
  --deps          install missing dependencies with your package manager
  --no-deps       don't check dependencies
  --uninstall     remove Mango Settings
  -h, --help      show this help
EOF
}

while [ $# -gt 0 ]; do
  case "$1" in
    --system) PREFIX="/usr/local"; SUDO="sudo" ;;
    --prefix) [ $# -ge 2 ] || die "--prefix needs a directory"; PREFIX="$2"; shift ;;
    --prefix=*) PREFIX="${1#*=}" ;;
    --ref) [ $# -ge 2 ] || die "--ref needs a branch or tag"; REF="$2"; shift ;;
    --ref=*) REF="${1#*=}" ;;
    --deps) DEPS="install" ;;
    --no-deps) DEPS="skip" ;;
    --uninstall) ACTION="uninstall" ;;
    -h|--help) usage; exit 0 ;;
    *) die "unknown option: $1 (see --help)" ;;
  esac
  shift
done

[ "$(id -u)" -eq 0 ] && SUDO=""
[ -n "$SUDO" ] && ! command -v sudo >/dev/null && die "sudo is required for --system"

SHARE="$PREFIX/share/mango-settings"
BIN="$PREFIX/bin/mango-settings"
DESKTOP="$PREFIX/share/applications/$APP_ID.desktop"
ICON="$PREFIX/share/icons/hicolor/scalable/apps/$APP_ID.svg"
RAW="https://raw.githubusercontent.com/$REPO/$REF"

refresh_caches() {
  if command -v update-desktop-database >/dev/null; then
    $SUDO update-desktop-database -q "$PREFIX/share/applications" 2>/dev/null || true
  fi
  if command -v gtk-update-icon-cache >/dev/null; then
    $SUDO gtk-update-icon-cache -q -t -f "$PREFIX/share/icons/hicolor" 2>/dev/null || true
  fi
}

if [ "$ACTION" = "uninstall" ]; then
  bold "Removing Mango Settings from $PREFIX"
  $SUDO rm -f "$BIN" "$DESKTOP" "$ICON"
  $SUDO rm -rf "$SHARE"
  rm -f "$HOME/.local/share/applications/mango-settings.desktop"  # 1.0 manual install
  refresh_caches
  ok "Uninstalled. Your MangoWM config and backups (~/.cache/mango-settings) were left alone."
  exit 0
fi

# --- dependencies ----------------------------------------------------------
have_gtk() {
  python3 - <<'PY' 2>/dev/null
import gi
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk  # noqa: F401
PY
}

dep_command() {
  if command -v pacman >/dev/null; then echo "sudo pacman -S --needed python python-gobject gtk3"
  elif command -v apt-get >/dev/null; then echo "sudo apt-get install -y python3 python3-gi gir1.2-gtk-3.0"
  elif command -v dnf >/dev/null; then echo "sudo dnf install -y python3 python3-gobject gtk3"
  elif command -v zypper >/dev/null; then echo "sudo zypper install -y python3 python3-gobject python3-gobject-Gdk typelib-1_0-Gtk-3_0"
  elif command -v xbps-install >/dev/null; then echo "sudo xbps-install -Sy python3 python3-gobject gtk+3"
  elif command -v apk >/dev/null; then echo "sudo apk add python3 py3-gobject3 gtk+3.0"
  elif command -v emerge >/dev/null; then echo "sudo emerge --ask dev-python/pygobject x11-libs/gtk+:3"
  elif command -v nix-env >/dev/null; then echo "nix-shell -p 'python3.withPackages (p: [p.pygobject3])' gtk3 gobject-introspection"
  else echo ""
  fi
}

if [ "$DEPS" != "skip" ]; then
  if ! command -v python3 >/dev/null || ! have_gtk; then
    cmd="$(dep_command)"
    info "Mango Settings needs Python 3 with PyGObject and GTK 3."
    if [ "$DEPS" = "install" ] && [ -n "$cmd" ]; then
      info "Running: $cmd"
      eval "$cmd" </dev/tty
    elif [ -n "$cmd" ] && [ -r /dev/tty ] && { true </dev/tty; } 2>/dev/null; then
      printf 'Install them now with:\n  %s\n[Y/n] ' "$cmd"
      read -r reply </dev/tty || reply="n"
      case "$reply" in [nN]*) die "dependencies missing — install them and run this again" ;; esac
      eval "$cmd" </dev/tty
    else
      die "dependencies missing. Install them with: ${cmd:-your package manager (python3, PyGObject, GTK 3)}"
    fi
    have_gtk || die "PyGObject/GTK 3 still not importable from python3"
  fi
  ok "Python $(python3 -c 'import sys; print(".".join(map(str, sys.version_info[:3])))') with GTK 3 found"
fi

# --- fetch files -----------------------------------------------------------
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

fetch() {  # fetch <path-in-repo> <dest>
  local here
  here="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" 2>/dev/null && pwd || true)"
  if [ -n "$here" ] && [ -f "$here/$1" ] && [ -z "${MANGO_SETTINGS_FORCE_DOWNLOAD:-}" ]; then
    cp "$here/$1" "$2"            # running from a clone
  elif command -v curl >/dev/null; then
    curl -fsSL --retry 2 "$RAW/$1" -o "$2" || die "download failed: $RAW/$1"
  elif command -v wget >/dev/null; then
    wget -q "$RAW/$1" -O "$2" || die "download failed: $RAW/$1"
  else
    die "curl or wget is required"
  fi
}

bold "Installing Mango Settings ($REF) into $PREFIX"
fetch mango-settings.py "$TMP/mango-settings.py"
fetch "data/$APP_ID.desktop" "$TMP/app.desktop"
fetch "data/$APP_ID.svg" "$TMP/app.svg"
python3 -m py_compile "$TMP/mango-settings.py" || die "downloaded file is not valid Python"
rm -rf "$TMP/__pycache__"
version="$(sed -n 's/^__version__ = "\(.*\)"/\1/p' "$TMP/mango-settings.py")"

$SUDO install -Dm755 "$TMP/mango-settings.py" "$SHARE/mango-settings.py"
$SUDO mkdir -p "$(dirname "$BIN")"
$SUDO ln -sf "$SHARE/mango-settings.py" "$BIN"
sed "s|^Exec=.*|Exec=$BIN|" "$TMP/app.desktop" > "$TMP/final.desktop"
$SUDO install -Dm644 "$TMP/final.desktop" "$DESKTOP"
$SUDO install -Dm644 "$TMP/app.svg" "$ICON"
rm -f "$HOME/.local/share/applications/mango-settings.desktop"  # old 1.0 entry
refresh_caches

ok "Mango Settings ${version:-} installed"
echo "   launcher: $BIN"
echo "   menu:     $DESKTOP"
case ":$PATH:" in
  *":$PREFIX/bin:"*) echo "   Run it with: mango-settings" ;;
  *) info "$PREFIX/bin is not in your PATH. Add it, or run: $BIN" ;;
esac
echo "   Update: run this installer again · Remove: add --uninstall"
