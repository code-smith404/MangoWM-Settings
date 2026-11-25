#!/bin/sh
# Build the Alpine .apk from the current checkout. Run as root inside an
# Alpine environment (or an alpine:latest container) with alpine-sdk,
# python3, py3-gobject3 and gtk+3.0 installed.
#
#   packaging/scripts/build-alpine.sh [output-dir]
set -eu

root=$(cd "$(dirname "$0")/../.." && pwd)
out=${1:-$root/apks}
version=$(grep -m1 '^VERSION = ' "$root/mango-settings.py" | sed 's/VERSION = "\([^"]*\)"/\1/')

[ "$(id -u)" = 0 ] || { echo "build-alpine.sh must run as root" >&2; exit 1; }
command -v abuild >/dev/null 2>&1 || { echo "abuild not found (apk add alpine-sdk)" >&2; exit 1; }

if ! id builder >/dev/null 2>&1; then
    adduser -D builder
    addgroup builder abuild
fi

work=${ALPINE_BUILD_DIR:-/tmp/mango-abuild}
pkgdir=$work/aports/testing/mango-settings
rm -rf "$work"
mkdir -p "$pkgdir"
cp -f "$root/packaging/alpine/APKBUILD" "$pkgdir/APKBUILD"

# The release tag does not exist before the release, so the APKBUILD's source
# URL would 404 (abuild retries the network even when a cached tarball is
# present). Build the tarball from HEAD with `git archive` instead, keep it
# next to the APKBUILD and point source= at the local file: both
# `abuild checksum` and `abuild -r` then run fully offline.
git -C "$root" archive --prefix="MangoWM-Settings-$version/" HEAD \
    | gzip -n > "$pkgdir/mango-settings-$version.tar.gz"
sum=$(sha512sum "$pkgdir/mango-settings-$version.tar.gz" | cut -d" " -f1)
sed -i \
    -e "s|^source=.*|source=\"mango-settings-$version.tar.gz\"|" \
    -e "s|^sha512sums=.*|sha512sums=\"$sum\"|" \
    "$pkgdir/APKBUILD"

chown -R builder "$work"
# abuild-keygen -a stores PACKAGER_PRIVKEY in ~/.abuild/abuild.conf; that file
# must be left intact or `abuild -r` cannot sign the package index.
su builder -s /bin/sh -c "abuild-keygen -a -n >/dev/null"
cp -f /home/builder/.abuild/*.rsa.pub /etc/apk/keys/

su builder -s /bin/sh -c "export REPODEST='$work/packages'; cd '$pkgdir' && abuild checksum && abuild -r"

mkdir -p "$out"
find "$work/packages" -name 'mango-settings-*.apk' -exec cp -f {} "$out/" \;
echo "==> Alpine package written to $out"
