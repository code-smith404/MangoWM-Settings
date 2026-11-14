#!/bin/sh
# Mango Settings installer (POSIX sh, works with bash, dash, ash, busybox).
#
#   curl -fsSL https://raw.githubusercontent.com/code-smith404/MangoWM-Settings/main/install.sh | sh
#   curl -fsSL .../install.sh | sh -s -- --help
set -eu

REPO="code-smith404/MangoWM-Settings"
APP_ID="io.github.code_smith404.MangoSettings"
REF="${MANGO_SETTINGS_REF:-main}"
PREFIX="${PREFIX:-$HOME/.local}"
SYSTEM=0
ACTION="install"
DEPS="ask"
DRY_RUN=0

if [ -t 1 ]; then
  B='\033[1m'; G='\033[1;32m'; Y='\033[1;33m'; R='\033[1;31m'; N='\033[0m'
else
  B=''; G=''; Y=''; R=''; N=''
fi
bold() { printf "${B}%s${N}\n" "$*"; }
info() { printf "${Y}::${N} %s\n" "$*"; }
ok()   { printf "${G}ok${N} %s\n" "$*"; }
warn() { printf "${Y}warning:${N} %s\n" "$*" >&2; }
die()  { printf "${R}error:${N} %s\n" "$*" >&2; exit 1; }

usage() {
  cat <<'EOF'
Mango Settings installer

  curl -fsSL https://raw.githubusercontent.com/code-smith404/MangoWM-Settings/main/install.sh | sh
  curl -fsSL .../install.sh | sh -s -- [options]

Options:
  --system        install for all users into /usr/local (uses sudo/doas)
  --prefix DIR    install into DIR (default: ~/.local)
  --ref REF       branch or tag to install (default: main)
  --deps          install missing dependencies without asking
  --no-deps       don't check or install dependencies
  --print-deps    print the dependency command for this distro and exit
  --uninstall     remove Mango Settings
  -h, --help      show this help

Supported package managers: pacman (Arch, Manjaro, EndeavourOS, CachyOS,
Artix, Garuda), apt (Debian, Ubuntu, Mint, Pop!_OS, elementary, Zorin, Kali),
dnf/yum (Fedora, RHEL, CentOS Stream, Rocky, Alma, Nobara), rpm-ostree
(Fedora Atomic), zypper (openSUSE, SLE), xbps (Void), apk (Alpine,
postmarketOS, Chimera), emerge (Gentoo), eopkg (Solus) and Nix (NixOS).
EOF
}

while [ $# -gt 0 ]; do
  case "$1" in
    --system) SYSTEM=1; PREFIX="/usr/local" ;;
    --prefix) [ $# -ge 2 ] || die "--prefix needs a directory"; PREFIX="$2"; shift ;;
    --prefix=*) PREFIX="${1#*=}" ;;
    --ref) [ $# -ge 2 ] || die "--ref needs a branch or tag"; REF="$2"; shift ;;
    --ref=*) REF="${1#*=}" ;;
    --deps) DEPS="yes" ;;
    --no-deps) DEPS="skip" ;;
    --print-deps) DRY_RUN=1 ;;
    --uninstall) ACTION="uninstall" ;;
    -h|--help) usage; exit 0 ;;
    *) die "unknown option: $1 (see --help)" ;;
  esac
  shift
done

has() { command -v "$1" >/dev/null 2>&1; }

# --- privileges ----------------------------------------------------------------
if [ "$(id -u)" -eq 0 ]; then ROOT=""
elif has sudo; then ROOT="sudo"
elif has doas; then ROOT="doas"
elif has run0; then ROOT="run0"
else ROOT=""
fi
need_root() { [ "$(id -u)" -eq 0 ] || [ -n "$ROOT" ] || die "this needs root, but neither sudo, doas nor run0 was found"; }
# AS_ROOT is used for files under the prefix; only escalate for --system or unwritable prefixes.
AS_ROOT=""
if [ "$SYSTEM" -eq 1 ]; then need_root; AS_ROOT="$ROOT"
else
  p="$PREFIX"; while [ ! -e "$p" ]; do p="$(dirname "$p")"; done
  if [ ! -w "$p" ]; then need_root; AS_ROOT="$ROOT"; fi
fi
asroot() { if [ -n "$AS_ROOT" ]; then $AS_ROOT "$@"; else "$@"; fi; }

# --- distro detection ----------------------------------------------------------
ID=""; ID_LIKE=""; PRETTY_NAME=""
for f in /etc/os-release /usr/lib/os-release; do
  if [ -r "$f" ]; then
    # shellcheck disable=SC1090
    . "$f"; break
  fi
done
DISTRO="${PRETTY_NAME:-${ID:-unknown Linux}}"
IDS=" $(printf '%s %s' "${ID:-}" "${ID_LIKE:-}" | tr '[:upper:]' '[:lower:]') "
is_like() { for x in "$@"; do case "$IDS" in *" $x "*) return 0 ;; esac; done; return 1; }

# MANGO_SETTINGS_FAMILY=arch|debian|fedora|ostree|suse|void|alpine|gentoo|solus|nix overrides detection.
FAMILY="${MANGO_SETTINGS_FAMILY:-}"
if [ -n "$FAMILY" ]; then :
elif is_like nixos; then FAMILY=nix
elif [ -e /run/ostree-booted ] && has rpm-ostree; then FAMILY=ostree
elif is_like arch archlinux manjaro endeavouros cachyos artix garuda && has pacman; then FAMILY=arch
elif is_like debian ubuntu && has apt-get; then FAMILY=debian
elif is_like fedora rhel centos rocky almalinux nobara && { has dnf || has yum; }; then FAMILY=fedora
elif is_like suse opensuse opensuse-tumbleweed opensuse-leap sles && has zypper; then FAMILY=suse
elif is_like void && has xbps-install; then FAMILY=void
elif is_like alpine postmarketos chimera && has apk; then FAMILY=alpine
elif is_like gentoo funtoo && has emerge; then FAMILY=gentoo
elif is_like solus && has eopkg; then FAMILY=solus
# unknown or derivative distro: fall back to whichever package manager exists
elif has pacman; then FAMILY=arch
elif has apt-get; then FAMILY=debian
elif has dnf || has yum; then FAMILY=fedora
elif has zypper; then FAMILY=suse
elif has xbps-install; then FAMILY=void
elif has apk; then FAMILY=alpine
elif has emerge; then FAMILY=gentoo
elif has eopkg; then FAMILY=solus
elif has nix-shell; then FAMILY=nix
fi

DNF="dnf"; has dnf || DNF="yum"
# Package manager command (without sudo) for the detected family.
deps_cmd() {
  case "$FAMILY" in
    arch)   echo "pacman -S --needed --noconfirm python python-gobject gtk3" ;;
    debian) echo "apt-get install -y python3 python3-gi gir1.2-gtk-3.0" ;;
    fedora) echo "$DNF install -y python3 python3-gobject gtk3" ;;
    ostree) echo "rpm-ostree install --idempotent python3-gobject gtk3" ;;
    suse)   echo "zypper --non-interactive install python3 python3-gobject python3-gobject-Gdk typelib-1_0-Gtk-3_0" ;;
    void)   echo "xbps-install -Sy python3 python3-gobject gtk+3" ;;
    alpine) echo "apk add python3 py3-gobject3 gtk+3.0" ;;
    gentoo) echo "emerge --noreplace dev-python/pygobject:3 x11-libs/gtk+:3" ;;
    solus)  echo "eopkg install -y python3 python3-gobject gtk3" ;;
    *)      echo "" ;;
  esac
}
NIX_PKGS="gobject-introspection gtk3 'python3.withPackages (p: [ p.pygobject3 ])'"

if [ "$DRY_RUN" -eq 1 ]; then
  echo "Detected: $DISTRO (${FAMILY:-unsupported})"
  if [ "$FAMILY" = nix ]; then echo "nix-shell -p $NIX_PKGS"
  else
    c="$(deps_cmd)"
    if [ -n "$c" ]; then echo "${ROOT:+$ROOT }$c"; else echo "no known package manager"; fi
  fi
  exit 0
fi

# --- paths -----------------------------------------------------------------------
SHARE="$PREFIX/share/mango-settings"
BIN="$PREFIX/bin/mango-settings"
DESKTOP="$PREFIX/share/applications/$APP_ID.desktop"
ICON="$PREFIX/share/icons/hicolor/scalable/apps/$APP_ID.svg"
RAW="https://raw.githubusercontent.com/$REPO/$REF"

refresh_caches() {
  if has update-desktop-database; then asroot update-desktop-database -q "$PREFIX/share/applications" 2>/dev/null || true; fi
  if has gtk-update-icon-cache && [ -f "$PREFIX/share/icons/hicolor/index.theme" ]; then
    asroot gtk-update-icon-cache -q -t -f "$PREFIX/share/icons/hicolor" 2>/dev/null || true
  fi
  if has xdg-desktop-menu; then xdg-desktop-menu forceupdate >/dev/null 2>&1 || true; fi
}

if [ "$ACTION" = "uninstall" ]; then
  bold "Removing Mango Settings from $PREFIX"
  asroot rm -f "$BIN" "$DESKTOP" "$ICON"
  asroot rm -rf "$SHARE"
  rm -f "$HOME/.local/share/applications/mango-settings.desktop"  # 1.0 manual install
  refresh_caches
  ok "Uninstalled. Your MangoWM config and backups (~/.cache/mango-settings) were left alone."
  exit 0
fi

bold "Installing Mango Settings ($REF) on $DISTRO"

# --- find a Python that has PyGObject + GTK 3 -----------------------------------
gtk_ok() {
  "$1" -c 'import gi; gi.require_version("Gtk", "3.0"); from gi.repository import Gtk' >/dev/null 2>&1
}
find_python() {
  for py in ${MANGO_SETTINGS_PYTHON:-} /usr/bin/python3 /usr/local/bin/python3 /run/current-system/sw/bin/python3 \
            "$(command -v python3 2>/dev/null || true)" /usr/bin/python; do
    [ -n "$py" ] && [ -x "$py" ] && gtk_ok "$py" && { echo "$py"; return 0; }
  done
  return 1
}

LAUNCH=""   # how the wrapper starts the app
if PY="$(find_python)"; then
  LAUNCH="exec \"$PY\" \"$SHARE/mango-settings.py\" \"\$@\""
elif [ "$FAMILY" = nix ]; then
  has nix-shell || die "nix-shell not found"
  info "NixOS: the launcher will start Mango Settings through nix-shell (the first start downloads GTK)."
  info "For a native install use the flake: nix profile install github:$REPO"
  LAUNCH="[ -n \"\${NIX_PATH:-}\" ] || export NIX_PATH=nixpkgs=channel:nixos-unstable
exec nix-shell -p $NIX_PKGS --run \"python3 '$SHARE/mango-settings.py' \$*\""
  DEPS="skip"
elif [ "$DEPS" = "skip" ]; then
  warn "Python 3 with PyGObject and GTK 3 was not found; installing anyway (--no-deps)."
  LAUNCH="exec python3 \"$SHARE/mango-settings.py\" \"\$@\""
else
  c="$(deps_cmd)"
  [ -n "$c" ] || die "Mango Settings needs Python 3, PyGObject and GTK 3. Install them with your package manager and run this again."
  need_root
  full="${ROOT:+$ROOT }$c"
  info "Mango Settings needs Python 3, PyGObject and GTK 3."
  if [ "$DEPS" = "ask" ]; then
    if (exec </dev/tty) 2>/dev/null; then
      printf 'Install them now with:\n  %s\n[Y/n] ' "$full"
      read -r reply </dev/tty || reply=n
      case "$reply" in [nN]*) die "dependencies missing. Install them with: $full" ;; esac
    else
      die "dependencies missing. Install them with: $full  (or rerun with --deps)"
    fi
  fi
  if [ "$FAMILY" = debian ]; then ${ROOT:+$ROOT} apt-get update -qq || true; fi
  info "Running: $full"
  # shellcheck disable=SC2086
  ${ROOT:+$ROOT} $c
  if [ "$FAMILY" = ostree ]; then
    warn "rpm-ostree layered the packages. Reboot, then run this installer again."
    exit 0
  fi
  PY="$(find_python)" || die "PyGObject/GTK 3 still can't be imported. Check the output above."
  LAUNCH="exec \"$PY\" \"$SHARE/mango-settings.py\" \"\$@\""
fi
[ -n "${PY:-}" ] && ok "Using $PY ($("$PY" -c 'import sys; print("Python %d.%d" % sys.version_info[:2])'), GTK 3 found)"

# --- fetch files -------------------------------------------------------------------
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT INT TERM

HERE=""
case "$0" in
  */install.sh|install.sh) HERE="$(cd "$(dirname "$0")" 2>/dev/null && pwd || true)" ;;
esac
fetch() {  # fetch <path-in-repo> <dest>
  if [ -n "$HERE" ] && [ -f "$HERE/$1" ] && [ -z "${MANGO_SETTINGS_FORCE_DOWNLOAD:-}" ]; then
    cp "$HERE/$1" "$2"            # running from a clone
  elif has curl; then
    curl -fsSL --retry 2 "$RAW/$1" -o "$2" || die "download failed: $RAW/$1"
  elif has wget; then
    wget -q "$RAW/$1" -O "$2" || die "download failed: $RAW/$1"
  else
    die "curl or wget is required"
  fi
}
fetch mango-settings.py "$TMP/mango-settings.py"
fetch "data/$APP_ID.desktop" "$TMP/app.desktop"
fetch "data/$APP_ID.svg" "$TMP/app.svg"
# Plain sh only from here on: minimal systems (Nix containers, busybox) may lack sed/grep.
IFS= read -r first < "$TMP/mango-settings.py" || first=""
case "$first" in *python*) ;; *) die "downloaded file doesn't look like mango-settings.py" ;; esac
version=""
while IFS= read -r line; do
  case "$line" in '__version__ = "'*) version="${line#*\"}"; version="${version%\"*}"; break ;; esac
done < "$TMP/mango-settings.py"

cat > "$TMP/launcher" <<EOF
#!/bin/sh
# Launcher for Mango Settings, created by install.sh
$LAUNCH
EOF
while IFS= read -r line || [ -n "$line" ]; do
  case "$line" in Exec=*) printf 'Exec=%s\n' "$BIN" ;; *) printf '%s\n' "$line" ;; esac
done < "$TMP/app.desktop" > "$TMP/final.desktop"

# --- install -----------------------------------------------------------------------
asroot mkdir -p "$SHARE" "$(dirname "$BIN")" "$(dirname "$DESKTOP")" "$(dirname "$ICON")"
asroot cp "$TMP/mango-settings.py" "$SHARE/mango-settings.py"
asroot rm -f "$BIN"
asroot cp "$TMP/launcher" "$BIN"
asroot cp "$TMP/final.desktop" "$DESKTOP"
asroot cp "$TMP/app.svg" "$ICON"
asroot chmod 755 "$SHARE/mango-settings.py" "$BIN"
asroot chmod 644 "$DESKTOP" "$ICON"
rm -f "$HOME/.local/share/applications/mango-settings.desktop"  # old 1.0 entry
refresh_caches

ok "Mango Settings ${version} installed into $PREFIX"
echo "   launcher: $BIN"
echo "   menu:     $DESKTOP"

# --- optional tools -----------------------------------------------------------------
has mango || has mmsg || warn "MangoWM (mango/mmsg) not found. Settings still work, but Reload and monitor detection need mmsg."
has gsettings || info "Optional: install gsettings (glib2 / libglib2.0-bin) to use the Theming page."

case ":$PATH:" in
  *":$PREFIX/bin:"*) echo "   Run it with: mango-settings" ;;
  *) info "$PREFIX/bin is not in your PATH. Add  export PATH=\"$PREFIX/bin:\$PATH\"  to your shell profile, or run $BIN" ;;
esac
echo "   Update: run this installer again. Remove: add --uninstall"
