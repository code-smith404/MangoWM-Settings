#!/bin/sh
# Refresh every release-tarball checksum from the tagged GitHub archive.
# Run after pushing a v<VERSION> tag:
#   packaging/scripts/update-checksums.sh
set -eu

root=$(cd "$(dirname "$0")/../.." && pwd)
cd "$root"

version=$(grep -m1 '^VERSION = ' mango-settings.py | sed 's/VERSION = "\([^"]*\)"/\1/')
url="https://github.com/code-smith404/MangoWM-Settings/archive/refs/tags/v$version.tar.gz"

tmp=$(mktemp)
trap 'rm -f "$tmp"' EXIT INT TERM

echo "==> Downloading $url"
if command -v curl >/dev/null 2>&1; then
    curl -fsSL "$url" -o "$tmp"
elif command -v wget >/dev/null 2>&1; then
    wget -qO "$tmp" "$url"
else
    echo "curl or wget is required" >&2
    exit 1
fi

sha256=$(sha256sum "$tmp" | cut -d' ' -f1)
sha512=$(sha512sum "$tmp" | cut -d' ' -f1)

echo "==> sha256 $sha256"
echo "==> sha512 $sha512"

# Arch (AUR)
sed -i "s|^sha256sums=.*|sha256sums=('$sha256')|" packaging/arch/PKGBUILD

# Alpine
sed -i "s|^sha512sums=.*|sha512sums=\"$sha512\"|" packaging/alpine/APKBUILD

# Void Linux
sed -i "s|^checksum=.*|checksum=$sha256|" packaging/void/template

# Solus
sed -i "s|\(archive/refs/tags/v$version.tar.gz : \).*|\1$sha256|" packaging/solus/package.yml

# Flatpak / Flathub
sed -i "s|^\( *\)sha256: .*|\1sha256: $sha256|" \
    packaging/flatpak/io.github.code_smith404.MangoSettings.yml

echo "==> Checksums updated. Verify with: sh tests/check-versions.sh"
