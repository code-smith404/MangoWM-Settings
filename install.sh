#!/bin/sh
# Mango Settings installer — POSIX sh, works on Alpine, Void, Debian (dash),
# Fedora, Arch, openSUSE and NixOS (via nix-shell launcher).
#   curl -fsSL https://raw.githubusercontent.com/code-smith404/MangoWM-Settings/main/install.sh | sh
set -u

REPO_URL="https://github.com/code-smith404/MangoWM-Settings"
RAW_BASE="https://raw.githubusercontent.com/code-smith404/MangoWM-Settings"
REF="main"
PREFIX="${HOME}/.local"
SYSTEM=0
DO_DEPS=1
PRINT_DEPS=0
UNINSTALL=0

# --- argument parsing -------------------------------------------------------
while [ $# -gt 0 ]; do
    case "$1" in
        --system)    SYSTEM=1 ;;
        --prefix)    PREFIX="$2"; shift ;;
        --prefix=*)  PREFIX="${1#--prefix=}" ;;
        --ref)       REF="$2"; shift ;;
        --ref=*)     REF="${1#--ref=}" ;;
        --deps)      DO_DEPS=1 ;;
        --no-deps)   DO_DEPS=0 ;;
        --print-deps) PRINT_DEPS=1 ;;
        --uninstall) UNINSTALL=1 ;;
        -h|--help)
            echo "usage: install.sh [--system | --prefix DIR] [--ref BRANCH]"
            echo "                  [--deps | --no-deps | --print-deps] [--uninstall]"
            exit 0 ;;
        *) echo "install.sh: unknown option: $1" >&2; exit 2 ;;
    esac
    shift
done

if [ "$SYSTEM" = 1 ]; then
    PREFIX="/usr/local"
fi

BIN_DIR="$PREFIX/bin"
SHARE_DIR="$PREFIX/share/mango-settings"
APP_DIR="$PREFIX/share/applications"
ICON_DIR="$PREFIX/share/icons/hicolor/scalable/apps"
DESKTOP_ID="io.github.code_smith404.MangoSettings"

# --- dependency database -----------------------------------------------------
# one line per package manager: name<TAB>packages
DEPS_PACMAN="python-gobject gtk3"
DEPS_APT="python3-gi python3-gi-cairo gir1.2-gtk-3.0"
DEPS_DNF="python3-gobject gtk3"
DEPS_YUM="python3-gobject gtk3"
DEPS_APK="py3-gobject3 gtk+3.0"
DEPS_XBPS="python3-gobject gtk+3"
DEPS_ZYPPER="python3-gobject gtk3"
DEPS_EOPKG="python3-gobject python3-gtk3"
DEPS_NIX=""

print_deps_for() {
    printf 'Arch/Manjaro : pacman -S %s\n' "$DEPS_PACMAN"
    printf 'Debian/Ubuntu: apt install %s\n' "$DEPS_APT"
    printf 'Fedora       : dnf install %s\n' "$DEPS_DNF"
    printf 'Alpine       : apk add %s\n' "$DEPS_APK"
    printf 'Void         : xbps-install %s\n' "$DEPS_XBPS"
    printf 'openSUSE     : zypper install %s\n' "$DEPS_ZYPPER"
    printf 'NixOS        : use the flake (nix run github:code-smith404/MangoWM-Settings)\n'
}

if [ "$PRINT_DEPS" = 1 ]; then
    print_deps_for
    exit 0
fi

# --- distro detection ---------------------------------------------------------
PM=""
detect_pm() {
    for tool in pacman apt-get dnf yum xbps-install zypper apk emerge eopkg; do
        if command -v "$tool" >/dev/null 2>&1; then
            PM="$tool"; return 0
        fi
    done
    return 1
}

install_deps() {
    detect_pm || { echo "No supported package manager found — install PyGObject and GTK 3 manually:"; print_deps_for; return 1; }
    echo "==> Installing runtime dependencies with $PM (you may be asked for your password)"
    SUDO=""
    if [ "$(id -u)" != 0 ]; then
        for su in sudo doas run0; do
            if command -v "$su" >/dev/null 2>&1; then SUDO="$su"; break; fi
        done
    fi
    case "$PM" in
        pacman)       $SUDO pacman -S --needed --noconfirm $DEPS_PACMAN ;;
        apt-get)      $SUDO apt-get update && $SUDO apt-get install -y $DEPS_APT ;;
        dnf|yum)      $SUDO $PM install -y $DEPS_DNF ;;
        xbps-install) $SUDO xbps-install -Sy $DEPS_XBPS ;;
        zypper)       $SUDO zypper install -y $DEPS_ZYPPER ;;
        apk)          $SUDO apk add $DEPS_APK ;;
        emerge)       $SUDO emerge -av dev-python/pygobject x11-libs/gtk+:3 ;;
        eopkg)        $SUDO eopkg install -y python3-gobject python3-gtk3 ;;
        *) echo "unknown package manager"; return 1 ;;
    esac
}

# --- download ------------------------------------------------------------------
fetch() {
    url="$1"; dest="$2"
    if command -v curl >/dev/null 2>&1; then
        curl -fsSL "$url" -o "$dest" || return 1
    elif command -v wget >/dev/null 2>&1; then
        wget -qO "$dest" "$url" || return 1
    else
        echo "Neither curl nor wget found — install one and retry." >&2
        return 1
    fi
}

# --- uninstall -------------------------------------------------------------------
if [ "$UNINSTALL" = 1 ]; then
    echo "==> Removing Mango Settings from $PREFIX"
    rm -f "$BIN_DIR/mango-settings" "$APP_DIR/$DESKTOP_ID.desktop"
    rm -f "$ICON_DIR/$DESKTOP_ID.svg"
    rm -rf "$SHARE_DIR"
    for d in "$HOME/.config/mango-settings"; do [ -d "$d" ] && rm -rf "$d"; done
    echo "Done. Your MangoWM config in ~/.config/mango was kept."
    exit 0
fi

# --- install ----------------------------------------------------------------------
mkdir -p "$BIN_DIR" "$SHARE_DIR" "$APP_DIR" "$ICON_DIR"

echo "==> Downloading mango-settings.py (ref: $REF)"
fetch "$RAW_BASE/$REF/mango-settings.py" "$SHARE_DIR/mango-settings.py" || {
    echo "Download failed — check your network connection or --ref value." >&2; exit 1; }
fetch "$RAW_BASE/$REF/data/$DESKTOP_ID.svg" "$ICON_DIR/$DESKTOP_ID.svg" || true

# sanity check: valid python?
if command -v python3 >/dev/null 2>&1; then
    python3 -m py_compile "$SHARE_DIR/mango-settings.py" 2>/dev/null || {
        echo "Downloaded file is not valid Python — aborting." >&2; exit 1; }
else
    echo "python3 not found — install Python 3.8+ first." >&2
    print_deps_for
    exit 1
fi

chmod +x "$SHARE_DIR/mango-settings.py"

cat > "$BIN_DIR/mango-settings" <<LAUNCHER
#!/bin/sh
exec python3 "$SHARE_DIR/mango-settings.py" "\$@"
LAUNCHER
chmod +x "$BIN_DIR/mango-settings"

# Generate the desktop entry with absolute Exec/TryExec paths.  Launchers
# like rofi silently drop entries whose TryExec is not found on *their*
# PATH, and compositor sessions often do not include ~/.local/bin in PATH.
cat > "$APP_DIR/$DESKTOP_ID.desktop" <<DESKTOP
[Desktop Entry]
Type=Application
Name=Mango Settings
GenericName=Settings Manager
Comment=Settings manager for the MangoWM Wayland compositor
Exec=$BIN_DIR/mango-settings
TryExec=$BIN_DIR/mango-settings
Icon=$DESKTOP_ID
Terminal=false
Categories=Settings;DesktopSettings;
Keywords=mango;mangowm;wayland;settings;
StartupNotify=false
DESKTOP
chmod 644 "$APP_DIR/$DESKTOP_ID.desktop"
command -v update-desktop-database >/dev/null 2>&1 && update-desktop-database "$APP_DIR" 2>/dev/null || true

if [ "$DO_DEPS" = 1 ] && ! python3 -c "import gi" 2>/dev/null; then
    if [ -t 0 ]; then
        printf "PyGObject is not installed. Install it now? [Y/n] "
        read -r answer
        case "$answer" in n|N|no|No) ;; *) install_deps ;; esac
    else
        echo "PyGObject is not installed. Install deps with:"; print_deps_for
    fi
fi

echo "==> Installed:"
echo "    $BIN_DIR/mango-settings"
echo "    $SHARE_DIR/mango-settings.py"
echo "    $APP_DIR/$DESKTOP_ID.desktop"
echo "Run: mango-settings    (start 'mango-settings --check' to validate your config)"
