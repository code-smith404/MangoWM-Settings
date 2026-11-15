#!/bin/sh
# Build static APT and RPM repositories from built packages.
#
#   packaging/scripts/build-repos.sh <input-dir-with-deb-and-rpm> <output-dir>
#
# Output layout:
#   <output>/apt/dists/stable/...      APT repository
#   <output>/apt/pool/main/...         .deb files
#   <output>/apt/key.asc               public signing key (when signed)
#   <output>/rpm/noarch/...            RPM repository (createrepo_c)
#   <output>/rpm/mango-settings.repo   dnf/yum repo file
#
# Set GPG_KEY_ID (and optionally GPG_PASSPHRASE) to sign the repositories.
# Requires: dpkg-deb or dpkg-scanpackages, apt-ftparchive (apt-utils),
#           createrepo_c, gpg (when signing).
set -eu

if [ $# -ne 2 ]; then
    echo "usage: $0 <input-dir> <output-dir>" >&2
    exit 2
fi

input=$(cd "$1" && pwd)
output=$2
mkdir -p "$output"

apt_root="$output/apt"
rpm_root="$output/rpm"
pool="$apt_root/pool/main/m/mango-settings"
dist="$apt_root/dists/stable"
component="$dist/main"

mkdir -p "$pool" "$component/binary-all" "$component/binary-amd64" \
    "$component/binary-arm64" "$component/binary-armhf" "$rpm_root/noarch"

# ---------------------------------------------------------------- APT --------
debs=$(find "$input" -maxdepth 2 -name '*.deb' -type f)
if [ -n "$debs" ]; then
    echo "==> APT: copying .deb files"
    # shellcheck disable=SC2086  # intentional word splitting of find output
    for deb in $debs; do
        cp -f "$deb" "$pool/"
    done

    if command -v apt-ftparchive >/dev/null 2>&1; then
        ( cd "$apt_root" && apt-ftparchive packages pool ) > "$component/binary-all/Packages"
    elif command -v dpkg-scanpackages >/dev/null 2>&1; then
        ( cd "$apt_root" && dpkg-scanpackages pool /dev/null ) > "$component/binary-all/Packages"
    else
        echo "apt-ftparchive or dpkg-scanpackages is required" >&2
        exit 1
    fi

    gzip -9c "$component/binary-all/Packages" > "$component/binary-all/Packages.gz"
    for arch in amd64 arm64 armhf; do
        cp -f "$component/binary-all/Packages" "$component/binary-$arch/Packages"
        cp -f "$component/binary-all/Packages.gz" "$component/binary-$arch/Packages.gz"
    done

    cat > "$dist/Release" <<EOF
Origin: MangoWM-Settings
Label: Mango Settings
Suite: stable
Codename: stable
Architectures: all amd64 arm64 armhf
Components: main
Description: Mango Settings APT repository
EOF

    if command -v apt-ftparchive >/dev/null 2>&1; then
        (
            cd "$dist"
            apt-ftparchive \
                -o APT::FTPArchive::Release::Origin="MangoWM-Settings" \
                -o APT::FTPArchive::Release::Label="Mango Settings" \
                -o APT::FTPArchive::Release::Suite="stable" \
                -o APT::FTPArchive::Release::Codename="stable" \
                -o APT::FTPArchive::Release::Architectures="all amd64 arm64 armhf" \
                -o APT::FTPArchive::Release::Components="main" \
                -o APT::FTPArchive::Release::Description="Mango Settings APT repository" \
                release . > Release.new
            mv Release.new Release
        )
    fi

    if [ -n "${GPG_KEY_ID:-}" ]; then
        echo "==> APT: signing Release"
        gpg_args="--batch --yes --local-user $GPG_KEY_ID"
        if [ -n "${GPG_PASSPHRASE:-}" ]; then
            gpg_args="$gpg_args --pinentry-mode loopback --passphrase $GPG_PASSPHRASE"
        fi
        # shellcheck disable=SC2086
        gpg $gpg_args --armor --detach-sign -o "$dist/Release.gpg" "$dist/Release"
        # shellcheck disable=SC2086
        gpg $gpg_args --clearsign -o "$dist/InRelease" "$dist/Release"
        # shellcheck disable=SC2086
        gpg $gpg_args --armor --export "$GPG_KEY_ID" > "$apt_root/key.asc"
    fi
else
    echo "==> APT: no .deb files found, skipping"
fi

# ---------------------------------------------------------------- RPM --------
rpms=$(find "$input" -maxdepth 2 -name '*.rpm' -type f)
if [ -n "$rpms" ]; then
    echo "==> RPM: copying .rpm files"
    # shellcheck disable=SC2086  # intentional word splitting of find output
    for rpm in $rpms; do
        cp -f "$rpm" "$rpm_root/noarch/"
    done

    if command -v createrepo_c >/dev/null 2>&1; then
        createrepo_c "$rpm_root/noarch"
    elif command -v createrepo >/dev/null 2>&1; then
        createrepo "$rpm_root/noarch"
    else
        echo "createrepo_c or createrepo is required" >&2
        exit 1
    fi

    if [ -n "${GPG_KEY_ID:-}" ]; then
        echo "==> RPM: signing repomd.xml"
        gpg_args="--batch --yes --local-user $GPG_KEY_ID"
        if [ -n "${GPG_PASSPHRASE:-}" ]; then
            gpg_args="$gpg_args --pinentry-mode loopback --passphrase $GPG_PASSPHRASE"
        fi
        # shellcheck disable=SC2086
        gpg $gpg_args --armor --detach-sign \
            -o "$rpm_root/noarch/repodata/repomd.xml.asc" \
            "$rpm_root/noarch/repodata/repomd.xml"
        # shellcheck disable=SC2086
        gpg $gpg_args --armor --export "$GPG_KEY_ID" > "$rpm_root/RPM-GPG-KEY-mango-settings"
        gpgcheck=1
        gpgkey_line="gpgkey=file:///etc/pki/rpm-gpg/RPM-GPG-KEY-mango-settings"
    else
        gpgcheck=0
        gpgkey_line="# unsigned repository: packages are not GPG-signed"
    fi

    cat > "$rpm_root/mango-settings.repo" <<EOF
[mango-settings]
name=Mango Settings
baseurl=https://code-smith404.github.io/MangoWM-Settings/rpm/noarch
enabled=1
gpgcheck=$gpgcheck
$gpgkey_line
EOF
else
    echo "==> RPM: no .rpm files found, skipping"
fi

echo "==> Repositories written to $output"
