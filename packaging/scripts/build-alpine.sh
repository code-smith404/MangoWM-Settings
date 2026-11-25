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
rm -rf "$work"
mkdir -p "$work/aports/testing/mango-settings" "$work/srcdest"
cp -f "$root/packaging/alpine/APKBUILD" "$work/aports/testing/mango-settings/APKBUILD"

# Build the source tarball exactly like `git archive` for the release tag
# (deterministic, no timestamps) and record its real sha512 in the APKBUILD so
# `abuild checksum` never has to download the - not yet existing - release tag.
git -C "$root" archive --prefix="MangoWM-Settings-$version/" HEAD \
    | gzip -n > "$work/aports/testing/mango-settings/mango-settings-$version.tar.gz"
sum=$(sha512sum "$work/aports/testing/mango-settings/mango-settings-$version.tar.gz" \
    | cut -d" " -f1)
sed -i "s|^sha512sums=.*|sha512sums=\"$sum\"|" \
    "$work/aports/testing/mango-settings/APKBUILD"
cp -f "$work/aports/testing/mango-settings/mango-settings-$version.tar.gz" \
    "$work/srcdest/"

chown -R builder "$work"
su builder -s /bin/sh -c "abuild-keygen -a -n >/dev/null"
cp -f /home/builder/.abuild/*.rsa.pub /etc/apk/keys/

# Serve the tarball to abuild from a local mirror so nothing is fetched over
# the network: DISTFILES_MIRROR is tried first by abuild (plain cp) and
# SRCDEST is where the cached copy is validated against sha512sums.
printf 'SRCDEST=%s\nDISTFILES_MIRROR=%s\n' "$work/srcdest" "$work/srcdest" \
    > /home/builder/.abuild/abuild.conf
chown -R builder "$work" /home/builder/.abuild

su builder -s /bin/sh -c "export REPODEST='$work/packages' DISTFILES_MIRROR='$work/srcdest'; cd '$work/aports/testing/mango-settings' && abuild checksum && abuild -r"

mkdir -p "$out"
find "$work/packages" -name 'mango-settings-*.apk' -exec cp -f {} "$out/" \;
echo "==> Alpine package written to $out"
