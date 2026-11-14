#!/bin/sh
# Verify that VERSION is identical in every place it is declared:
# mango-settings.py, packaging/arch/PKGBUILD and flake.nix.
set -eu

here=$(cd "$(dirname "$0")/.." && pwd)
fail=0

py=$(grep -m1 '^VERSION = ' "$here/mango-settings.py" | sed 's/VERSION = "\([^"]*\)"/\1/')
pkg=$(grep -m1 '^pkgver=' "$here/packaging/arch/PKGBUILD" | sed 's/pkgver=\(.*\)/\1/')
nix=$(grep -m1 'version = ' "$here/flake.nix" | sed 's/.*version = "\([^"]*\)".*/\1/')

echo "mango-settings.py : $py"
echo "PKGBUILD          : $pkg"
echo "flake.nix         : $nix"

[ -n "$py" ] || { echo "no version found in mango-settings.py" >&2; fail=1; }
[ "$py" = "$pkg" ] || { echo "PKGBUILD version ($pkg) != mango-settings.py ($py)" >&2; fail=1; }
[ "$py" = "$nix" ] || { echo "flake.nix version ($nix) != mango-settings.py ($py)" >&2; fail=1; }

[ "$fail" = 0 ] && echo "versions in sync" || exit 1
