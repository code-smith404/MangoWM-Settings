#!/bin/sh
# Build the RPM from the current checkout. Run on Fedora/openSUSE (or in a
# fedora:latest container) with rpm-build, git, gzip and make installed.
#
#   packaging/scripts/build-rpm.sh [fedora|opensuse] [output-dir]
set -eu

root=$(cd "$(dirname "$0")/../.." && pwd)
flavour=${1:-fedora}
out=${2:-$root/rpms}
version=$(grep -m1 '^VERSION = ' "$root/mango-settings.py" | sed 's/VERSION = "\([^"]*\)"/\1/')

case "$flavour" in
    fedora)   spec="$root/packaging/fedora/mango-settings.spec" ;;
    opensuse) spec="$root/packaging/opensuse/mango-settings.spec" ;;
    *) echo "usage: $0 [fedora|opensuse] [output-dir]" >&2; exit 2 ;;
esac

top=${RPMBUILD:-$HOME/rpmbuild}
mkdir -p "$top/BUILD" "$top/BUILDROOT" "$top/RPMS" \
    "$top/SOURCES" "$top/SPECS" "$top/SRPMS"

git -C "$root" archive --prefix="MangoWM-Settings-$version/" HEAD \
    | gzip -9 > "$top/SOURCES/v$version.tar.gz"
cp -f "$spec" "$top/SPECS/mango-settings.spec"

rpmbuild -ba "$top/SPECS/mango-settings.spec"
mkdir -p "$out"
find "$top/RPMS" -name '*.rpm' -exec cp -f {} "$out/" \;
echo "==> RPM package written to $out"
