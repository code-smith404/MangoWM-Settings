#!/bin/sh
# Build the Arch package from the current checkout (no tag required).
#
#   packaging/scripts/build-arch.sh [output-dir]
#
# Produces mango-settings-<version>-<rel>-any.pkg.tar.zst using makepkg.
# Intended for CI and local smoke tests; the released package is built from
# the tagged tarball by .github/workflows/release.yml and published to the AUR.
set -eu

root=$(cd "$(dirname "$0")/../.." && pwd)
out=${1:-$PWD/arch-out}
mkdir -p "$out"

version=$(grep -m1 '^VERSION = ' "$root/mango-settings.py" | sed 's/VERSION = "\([^"]*\)"/\1/')

tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT INT TERM

mkdir -p "$tmp/build"
git -C "$root" archive --prefix="MangoWM-Settings-$version/" HEAD \
    | gzip -9 > "$tmp/build/mango-settings-$version.tar.gz"

cp "$root/packaging/arch/PKGBUILD" "$tmp/build/PKGBUILD"
sha=$(sha256sum "$tmp/build/mango-settings-$version.tar.gz" | cut -d' ' -f1)
sed -i \
    -e "s|^source=.*|source=(\"mango-settings-$version.tar.gz\")|" \
    -e "s|^sha256sums=.*|sha256sums=('$sha')|" \
    "$tmp/build/PKGBUILD"

cd "$tmp/build"
makepkg --force --cleanbuild --noconfirm
cp ./*.pkg.tar.* "$out/"
echo "==> Package written to $out"
