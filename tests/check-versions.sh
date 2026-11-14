#!/bin/sh
# Fails if the version in mango-settings.py, the packages and the changelog differ.
set -eu
cd "$(dirname "$0")/.."
app="$(sed -n 's/^__version__ = "\(.*\)"$/\1/p' mango-settings.py)"
nix="$(sed -n 's/^ *version = "\([^"]*\)";.*/\1/p' packaging/nix/package.nix)"
arch="$(sed -n 's/^pkgver=//p' packaging/arch/PKGBUILD)"
log="$(sed -n 's/^## \([0-9][^ ]*\).*/\1/p' CHANGELOG.md | head -n 1)"
echo "mango-settings.py: $app  package.nix: $nix  PKGBUILD: $arch  CHANGELOG: $log"
fail=0
for v in "$nix" "$arch" "$log"; do
  [ "$v" = "$app" ] || fail=1
done
if [ "$fail" -ne 0 ]; then
  echo "error: versions differ; update them all to $app" >&2
  exit 1
fi
