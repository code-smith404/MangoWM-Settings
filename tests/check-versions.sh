#!/bin/sh
# Verify that VERSION is identical in every place it is declared, and that
# release checksums have been filled in.
#
#   sh tests/check-versions.sh            # version sync (+ checksum warnings)
#   sh tests/check-versions.sh --strict   # also fail on placeholder checksums
set -eu

here=$(cd "$(dirname "$0")/.." && pwd)
strict=0
[ "${1:-}" = "--strict" ] && strict=1
fail=0

py=$(grep -m1 '^VERSION = ' "$here/mango-settings.py" | sed 's/VERSION = "\([^"]*\)"/\1/')
[ -n "$py" ] || { echo "no version found in mango-settings.py" >&2; exit 1; }

# label|version
versions=$(cat <<EOF
PKGBUILD             |$(grep -m1 '^pkgver=' "$here/packaging/arch/PKGBUILD" | cut -d= -f2)
PKGBUILD-git         |$(grep -m1 '^pkgver=' "$here/packaging/arch/PKGBUILD-git" | cut -d= -f2 | sed 's/\.r.*//')
flake.nix            |$(grep -m1 'version = ' "$here/flake.nix" | sed 's/.*version = "\([^"]*\)".*/\1/')
debian/changelog     |$(head -n1 "$here/debian/changelog" | sed 's/.*(\([^)-]*\).*/\1/')
fedora.spec          |$(grep -m1 '^Version:' "$here/packaging/fedora/mango-settings.spec" | awk '{print $2}')
opensuse.spec        |$(grep -m1 '^Version:' "$here/packaging/opensuse/mango-settings.spec" | awk '{print $2}')
APKBUILD             |$(grep -m1 '^pkgver=' "$here/packaging/alpine/APKBUILD" | cut -d= -f2)
void/template        |$(grep -m1 '^version=' "$here/packaging/void/template" | cut -d= -f2)
gentoo ebuild        |$(basename "$here"/packaging/gentoo/mango-settings-*.ebuild | sed 's/.*-\([0-9][0-9.]*\)\.ebuild/\1/')
solus/package.yml    |$(grep -m1 '^version *:' "$here/packaging/solus/package.yml" | awk '{print $3}')
flatpak manifest     |$(grep -m1 'archive/refs/tags/v' "$here/packaging/flatpak/io.github.code_smith404.MangoSettings.yml" | sed 's|.*tags/v\([^.]*\.[^.]*\.[^.]*\)\.tar\.gz.*|\1|')
metainfo.xml         |$(grep -m1 '<release version=' "$here/data/io.github.code_smith404.MangoSettings.metainfo.xml" | sed 's/.*version="\([^"]*\)".*/\1/')
man page             |$(head -n1 "$here/data/mango-settings.1" | sed 's/.*mango-settings \([^ ]*\)".*/\1/')
EOF
)

printf '%-20s %s\n' "mango-settings.py" "$py"
echo "$versions" | while IFS='|' read -r label ver; do
    printf '%-20s %s\n' "$label" "$ver"
done

echo "$versions" | while IFS='|' read -r label ver; do
    [ -n "$ver" ] || { echo "no version found for $label" >&2; exit 1; }
    [ "$ver" = "$py" ] || { echo "$label version ($ver) != mango-settings.py ($py)" >&2; exit 1; }
done || fail=1

# --- checksums --------------------------------------------------------------
placeholders=""
for f in \
    "$here/packaging/arch/PKGBUILD" \
    "$here/packaging/alpine/APKBUILD" \
    "$here/packaging/void/template" \
    "$here/packaging/solus/package.yml" \
    "$here/packaging/flatpak/io.github.code_smith404.MangoSettings.yml"
do
    if grep -q 'REPLACE_ME\|^sha512sums="SKIP"\|^checksum=SKIP' "$f"; then
        placeholders="$placeholders $f"
    fi
done

if [ -n "$placeholders" ]; then
    echo "warning: release checksums not filled in:$placeholders" >&2
    echo "         run packaging/scripts/update-checksums.sh after tagging" >&2
    [ "$strict" = 0 ] || fail=1
fi

if [ "$fail" = 0 ]; then
    echo "versions in sync"
else
    exit 1
fi
