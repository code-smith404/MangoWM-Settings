# Packaging Mango Settings for Linux distributions

Mango Settings is a single-file Python 3 + GTK 3 application, so it packages
the same way everywhere: install `mango-settings.py` under
`/usr/share/mango-settings/`, provide `/usr/bin/mango-settings`, and install
the desktop entry, icon, man page and AppStream metainfo. `make install`
already does all of that.

This repository ships native packaging for every major community, plus CI
that builds the packages and publishes an APT and an RPM repository on
GitHub Pages.

## Package matrix

| Family | Package manager | Files | Install (end user) | Status |
|---|---|---|---|---|
| Arch / Manjaro / EndeavourOS | pacman (AUR) | `packaging/arch/PKGBUILD` | `cd packaging/arch && makepkg -si` | AUR publish step runs when `AUR_SSH_PRIVATE_KEY` is configured |
| Arch (development) | pacman (AUR) | `packaging/arch/PKGBUILD-git` | `git clone --depth=1 https://github.com/code-smith404/MangoWM-Settings` then use `PKGBUILD-git` | AUR publish step runs when `AUR_SSH_PRIVATE_KEY` is configured |
| Debian 12+ / Ubuntu 22.04+ / Mint / Pop!_OS | apt | `debian/`, hosted repo | see [APT repository](#apt-repository) | Repo published by CI; official archive needs an ITP |
| Fedora / RHEL / AlmaLinux / Rocky | dnf | `packaging/fedora/mango-settings.spec`, hosted repo | see [RPM repository](#rpm-repository) | Repo published by CI; official repos need a review |
| openSUSE Tumbleweed / Leap | zypper | `packaging/opensuse/mango-settings.spec` | `zypper addrepo ...` / OBS | Spec ready; submit to OBS |
| Alpine Linux | apk | `packaging/alpine/APKBUILD` | `abuild` package | APKBUILD ready; aports merge request needed |
| Void Linux | xbps | `packaging/void/template` | `xbps-src` | Template ready; void-packages PR needed |
| Gentoo | portage | `packaging/gentoo/mango-settings-2.2.2.ebuild` | `emerge` | Ebuild ready; GURU/PR needed |
| Solus | eopkg | `packaging/solus/package.yml` | `eopkg` | package.yml ready; Solus PR needed |
| NixOS / any Linux | nix | `flake.nix` | `nix run github:code-smith404/MangoWM-Settings` | Works today |
| Any Linux (sandboxed) | Flatpak | `packaging/flatpak/...yml` | `flatpak install flathub ...` | Manifest ready; Flathub PR needed |
| Any Linux | install.sh | `install.sh` | `curl ... | sh` | Works today |

"Ready" means the packaging file is complete and CI-built. Getting into an
official distribution archive requires a maintainer account and the
submission step described below; this repository cannot do that on its own.

## Requirements

Every package needs the same runtime:

| Distro family | Dependencies |
|---|---|
| Arch | `python python-gobject gtk3` |
| Debian/Ubuntu | `python3 python3-gi python3-gi-cairo gir1.2-gtk-3.0` |
| Fedora/RHEL | `python3 python3-gobject gtk3` |
| openSUSE | `python3 python3-gobject gtk3` |
| Alpine | `python3 py3-gobject3 gtk+3.0` |
| Void | `python3 python3-gobject gtk+3` |
| Gentoo | `dev-python/pygobject:3 x11-libs/gtk+:3[introspection]` |

Optional: `mmsg` (reload MangoWM / detect monitors), `swaybg`, `swww` or
`feh` (wallpaper page).

## Building each package locally

### Arch (pacman/AUR)

```sh
cd packaging/arch
updpkgsums          # after the v2.2.2 tag exists
makepkg -si
```

For development builds from the checkout, use `packaging/scripts/build-arch.sh`;
it creates a source tarball with `git archive` and adjusts the checksum.

### Debian / Ubuntu (apt)

`debian/` lives at the repository root, as `dpkg-buildpackage` expects.

```sh
version=$(sed -n 's/^VERSION = "\(.*\)"/\1/p' mango-settings.py)
tar --exclude-vcs --exclude=debian -czf ../mango-settings_${version}.orig.tar.gz \
    --transform "s,^\.,MangoWM-Settings-${version}," .
dpkg-buildpackage -us -uc -b
sudo apt install ../mango-settings_${version}-1_all.deb
```

`debian/watch` tracks upstream tags for `uscan`.

### Fedora / RHEL (rpm)

```sh
version=2.2.2
mkdir -p ~/rpmbuild/{SOURCES,SPECS}
curl -L "https://github.com/code-smith404/MangoWM-Settings/archive/refs/tags/v$version.tar.gz" \
    -o ~/rpmbuild/SOURCES/v$version.tar.gz
cp packaging/fedora/mango-settings.spec ~/rpmbuild/SPECS/
rpmbuild -ba ~/rpmbuild/SPECS/mango-settings.spec
sudo dnf install ~/rpmbuild/RPMS/noarch/mango-settings-$version-1*.rpm
```

### openSUSE (rpm/OBS)

```sh
rpmbuild -ba packaging/opensuse/mango-settings.spec
```

To publish, create an OBS account, a home project, and add the spec and
tarball; OBS builds Tumbleweed and Leap automatically and serves a `zypper`
repository.

### Alpine (apk)

```sh
cd packaging/alpine
abuild-keygen -a -i     # once
abuild checksum         # after the v2.2.2 tag exists
abuild -r
doas apk add --repository ~/packages/packaging/alpine mango-settings
```

For an `aports` merge request, put `APKBUILD` under
`testing/mango-settings/` in an Alpine git checkout, run `abuild checksum`,
then `git checkout -b mango-settings && git commit && git push` and open an
MR on GitLab.

### Void Linux (xbps)

```sh
git clone --depth=1 https://github.com/void-linux/void-packages
cp packaging/void/template void-packages/srcpkgs/mango-settings/template
cd void-packages
./xbps-src pkg mango-settings
sudo xbps-install --repository=hostdir/binpkgs mango-settings
```

For upstream inclusion, open a PR against `void-linux/void-packages`.

### Gentoo (portage)

```sh
mkdir -p /var/db/repos/local/mango-settings
cp packaging/gentoo/mango-settings-2.2.2.ebuild /var/db/repos/local/mango-settings/
cd /var/db/repos/local/mango-settings
ebuild mango-settings-2.2.2.ebuild manifest
sudo emerge mango-settings
```

For inclusion, open a PR against the `GURU` overlay or request a project
repository on bugs.gentoo.org.

### Solus (eopkg)

```sh
sudo eopkg it -c system.devel
cd packaging/solus
sudo solbuild build package.yml
```

For inclusion, open a PR against `getsolus/packages`.

### NixOS / flakes

```sh
nix run github:code-smith404/MangoWM-Settings
# or install
nix profile install github:code-smith404/MangoWM-Settings
```

To land it in nixpkgs, add a `mango-settings` derivation under
`pkgs/by-name/ma/mango-settings/` and open a PR. `flake.nix` is already a
working derivation to copy.

### Flatpak

```sh
flatpak install flathub org.gnome.Platform//48 org.gnome.Sdk//48
flatpak-builder --user --install --force-clean build-dir \
    packaging/flatpak/io.github.code_smith404.MangoSettings.yml
flatpak run io.github.code_smith404.MangoSettings
```

For Flathub, fork `flathub/flathub`, create a `new-pr` branch with the
manifest, and open a pull request. Flathub requires the AppStream metainfo in
`data/`, which is installed by `make install`.

## Hosted APT and RPM repositories

The release workflow builds `.deb` and `.rpm` files and publishes them to the
`gh-pages` branch, so users get updates without waiting for a distribution
archive:

### APT repository

```sh
sudo install -d /usr/share/keyrings
curl -fsSL https://code-smith404.github.io/MangoWM-Settings/apt/key.asc \
  | sudo gpg --dearmor -o /usr/share/keyrings/mango-settings.gpg
echo "deb [signed-by=/usr/share/keyrings/mango-settings.gpg] https://code-smith404.github.io/MangoWM-Settings/apt stable main" \
  | sudo tee /etc/apt/sources.list.d/mango-settings.list
sudo apt update
sudo apt install mango-settings
```

If the repository is unsigned (no `GPG_PRIVATE_KEY` secret configured), use
`[trusted=yes]` instead of `signed-by=...`.

### RPM repository

```sh
sudo curl -fsSL https://code-smith404.github.io/MangoWM-Settings/rpm/mango-settings.repo \
  -o /etc/yum.repos.d/mango-settings.repo
sudo dnf install mango-settings
```

To enable signature checking, also install
`https://code-smith404.github.io/MangoWM-Settings/rpm/RPM-GPG-KEY-mango-settings`
into `/etc/pki/rpm-gpg/`.

`packaging/scripts/build-repos.sh <input> <output>` builds both repositories
locally; it is the same script CI uses.

## Release process

1. Bump the version in `mango-settings.py`, `flake.nix`,
   `packaging/arch/PKGBUILD`, `packaging/alpine/APKBUILD`,
   `packaging/void/template`, `packaging/solus/package.yml`, both RPM specs,
   `debian/changelog`, the metainfo release list, the man page and the Gentoo
   ebuild name.
2. Run `sh tests/check-versions.sh` — every declared version must match.
   Release checksums may still be placeholders at this point.
3. Commit, tag and push: `git tag v2.2.2 && git push origin v2.2.2`.
4. The `Release` workflow builds `.deb`, `.rpm`, `.apk`, Arch and Flatpak
   artifacts, attaches them to the GitHub release, rebuilds the APT and RPM
   repositories on `gh-pages`, and pushes the updated PKGBUILDs to the AUR.
5. The workflow's `checksums` job then downloads the new tag archive, fills
   the real checksums in the packaging files, commits them to `main`, and
   the AUR job runs after that. You can reproduce the step locally with
   `packaging/scripts/update-checksums.sh` once the tag is on GitHub.

Because the packaging files contain checksums of the tagged archive, the
tag itself still carries the previous placeholders; `main` (and therefore
the AUR, Flatpak, Solus and Void packaging) always has the correct values.
Never rebuild the AUR package from files in the tag tree.

## Repository setup for maintainers

Enable GitHub Pages for this repository with the `gh-pages` branch as the
source. The release workflow creates that branch on the first release.

Configure these repository secrets to enable publishing. Without `AUR_SSH_PRIVATE_KEY` the AUR job is skipped, so Arch users build `packaging/arch/PKGBUILD` locally until the key is added:

| Secret | Purpose |
|---|---|
| `AUR_SSH_PRIVATE_KEY` | Push `mango-settings` and `mango-settings-git` to the AUR |
| `GPG_PRIVATE_KEY` | Sign APT `Release`/`InRelease` and RPM `repomd.xml` |
| `GPG_PASSPHRASE` | Passphrase for the GPG key (optional) |
| `GPG_KEY_ID` | Key id/fingerprint for signing (required with the key) |

Without these secrets the workflow still builds everything and creates the
GitHub release; it simply skips AUR publishing and leaves the repositories
unsigned.

## CI

- `.github/workflows/ci.yml` — unit tests, CLI checks, installer tests on
  many distro containers and `nix flake check`.
- `.github/workflows/packages.yml` — builds the Debian, RPM, Alpine and Arch
  packages on every push/PR and validates the remaining packaging files.
- `.github/workflows/release.yml` — on a `v*` tag: builds every package,
  creates the GitHub release, publishes the APT/RPM repositories and updates
  the AUR.

## Troubleshooting

- **`makepkg` checksum failure** — run `updpkgsums` (or
  `packaging/scripts/update-checksums.sh`) after the tag exists. GitHub's
  generated tarballs are not byte-identical to `git archive` output.
- **`abuild` checksum failure** — run `abuild checksum` inside
  `packaging/alpine` after the tag exists.
- **`dh_missing` / lintian errors** — the Debian package installs everything
  from the Makefile; check `debian/rules` still calls `make install`.
- **Flatpak cannot see the config** — the manifest grants
  `xdg-config/mango`, `xdg-config/mango-settings` and
  `xdg-cache/mango-settings`; add paths if your `--config` lives elsewhere.
