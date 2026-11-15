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
mkdir -p "$work/aports/testing/mango-settings"
cp -f "$root/packaging/alpine/APKBUILD" "$work/aports/testing/mango-settings/APKBUILD"
git -C "$root" archive --prefix="MangoWM-Settings-$version/" HEAD \
    | gzip -9 > "$work/aports/testing/mango-settings/mango-settings-$version.tar.gz"
chown -R builder "$work"

su builder -s /bin/sh -c "abuild-keygen -a -n >/dev/null"
cp -f /home/builder/.abuild/*.rsa.pub /etc/apk/keys/
su builder -s /bin/sh -c "cd '$work/aports/testing/mango-settings' && abuild checksum && abuild -r"

mkdir -p "$out"
find "$work/packages" -name 'mango-settings-*.apk' -exec cp -f {} "$out/" \;
echo "==> Alpine package written to $out"
