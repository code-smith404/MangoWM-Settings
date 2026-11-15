#!/bin/sh
# Build the Debian/Ubuntu .deb from the current checkout.
#
#   packaging/scripts/build-deb.sh [output-dir]
#
# Requires: dpkg-buildpackage, debhelper (13), build-essential.
# `debian/` must stay at the repository root.
set -eu

root=$(cd "$(dirname "$0")/../.." && pwd)
out=${1:-$root/debs}
version=$(grep -m1 '^VERSION = ' "$root/mango-settings.py" | sed 's/VERSION = "\([^"]*\)"/\1/')
parent=$(dirname "$root")

tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT INT TERM

# Upstream tarball (3.0 quilt source format) without debian/.
mkdir -p "$tmp/orig/MangoWM-Settings-$version"
git -C "$root" archive HEAD | tar -x -C "$tmp/orig/MangoWM-Settings-$version"
rm -rf "$tmp/orig/MangoWM-Settings-$version/debian"
tar -C "$tmp/orig" -czf "$parent/mango-settings_$version.orig.tar.gz" \
    "MangoWM-Settings-$version"

cd "$root"
dpkg-buildpackage -us -uc -b

mkdir -p "$out"
for deb in "$parent"/mango-settings_*.deb; do
    cp -f "$deb" "$out/"
done
echo "==> Debian package written to $out"
