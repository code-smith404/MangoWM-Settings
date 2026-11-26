# Mango Settings (MangoWM-Settings)

A modern, GNOME Settings–style graphical settings manager for the
**[MangoWM](https://github.com/mangowm/mango)** Wayland compositor — one
Python file, GTK 3, no bloat.  It is designed to work on **any Linux
distribution**: it adapts to whatever is installed (mmsg, gsettings,
wallpaper daemons, notification daemons) and every feature degrades
cleanly when a companion tool is missing.

## Features

- **Every option MangoWM parses** — the schema covers all 248 top-level
  keys of Mango's `option_defs.c` (verified by a test), grouped into the
  same tree as the official web settings
  ([mangowm.github.io/settings](https://mangowm.github.io/settings/)):
  root pages **Autostart**, **Environment Variables** and **Bindings**,
  plus collapsible groups — **Appearance** (Colors, Window Effects, Gaps
  & Borders, Cursor, Bars & Decorations, Jump Labels), **Layout**
  (Tiling, Dwindle, Scroller), **Input Devices** (Keyboard, Pointer,
  Trackpad, Device Rules), **Animations** (General, Timing, Curves),
  **Behaviour** (Focus, Drag & Floating, Scratchpad, Overview & Hotarea,
  Special Tag, Gestures), **Rules** (Monitor, Tag, Window, Layer rules)
  and **Desktop & System** (Theming, Background, Notifications, Config
  Files, Advanced, System Info)
- **Rule editors for Mango's `key:value` records** (`monitor`,
  `tag_rule`, `window_rule[_once]`, `layer_rule`, `device_rule`) with
  Mango's validation rules enforced (identifier required, mode and
  position fields together, ranges, boolean fields)
- **Web-style chrome**: slim header bar with sidebar toggle, page title,
  centered search pill, setup menu, undo/redo, save, reload and quit;
  drag the header to move the window
- **Real-time config awareness**: edits made outside the app are detected
  and can be reloaded with one click; every save first snapshots the
  original files to `~/.cache/mango-settings/backups/`
- **Backup manager, export and import**: browse and restore snapshots,
  pack `~/.config/mango` into a portable `tar.gz`, import it on another
  machine (the current state is backed up first; malicious archive paths
  are rejected)
- **Undo/redo** (`Ctrl+Z` / `Ctrl+Shift+Z`) for setting changes, resets
  and rule/env/autostart edits; a per-page **Reset to MangoWM defaults**
  button on every settings page
- **Customizable accent color** plus **system / dark / light** app themes
- **Inline validation**: invalid values are flagged on the widget (red
  outline + tooltip) and never written; no modal dialog spam
- **Search across settings** (`Ctrl+F`/`Ctrl+K`): filters the sidebar to
  pages that contain matches and the page to the matching rows; `Esc`
  clears it
- **Shortcuts**: `Ctrl+S` save, `Ctrl+R` reload MangoWM, `Ctrl+Q` quit
- **Safe config engine** that reads `~/.config/mango/config.conf` exactly
  like MangoWM (`source=`, `source_optional=`, `var=`), preserves comments,
  blank lines, ordering, symlinks, permissions, CRLF and missing final
  newlines, and writes atomically
- **Background page**: set the wallpaper live (swaybg, swww or feh) and
  persist it by updating the wallpaper line in `autostart.sh`
- **Status and diagnostics**: compositor version and running state,
  notification daemon detection, config-file health view with per-file
  error/warning counts, raw editor for every loaded file, `--check`
  headless validation for CI and dotfile repos, and a hidden `--selftest`
  that builds every page headlessly

## Install

### One-liner (works on Alpine, Void, Debian, Fedora, Arch, openSUSE)

```sh
curl -fsSL https://raw.githubusercontent.com/code-smith404/MangoWM-Settings/main/install.sh | sh
```

The installer installs into `~/.local` by default. Options:

| Flag | Meaning |
|---|---|
| `--system` | install to `/usr/local` |
| `--prefix DIR` | install to DIR |
| `--no-deps` | skip the PyGObject install prompt |
| `--print-deps` | print the package names for your distro and exit |
| `--ref BRANCH` | install from a branch/tag |
| `--from DIR` | install from a local checkout instead of downloading |
| `--uninstall` | remove everything (your MangoWM config is kept) |

### Distro packages

Native packaging lives in [`packaging/`](packaging) and [`debian/`](debian);
full build and submission instructions are in
[docs/PACKAGING.md](docs/PACKAGING.md).

| Distro / package manager | Install |
|---|---|
| Arch / Manjaro (pacman) | hosted repo below, or `cd packaging/arch && makepkg -si` |
| Debian / Ubuntu (apt) | `packaging/scripts/build-deb.sh` or the hosted repo below |
| Fedora / RHEL (dnf) | `packaging/scripts/build-rpm.sh` or the hosted repo below |
| Alpine (apk) | `packaging/alpine/APKBUILD` |
| Void (xbps) | `packaging/void/template` |
| openSUSE (zypper) | `packaging/opensuse/mango-settings.spec` |
| Gentoo (portage) | `packaging/gentoo/mango-settings-2.2.2.ebuild` |
| Solus (eopkg) | `packaging/solus/package.yml` |
| NixOS / flakes | `nix run github:code-smith404/MangoWM-Settings` |
| Any Linux (Flatpak) | `packaging/flatpak/` manifest |
| Anything | `make install PREFIX=~/.local` |

### One-command install (hosted repositories)

These add the hosted repository once and install the package with your normal
package manager:

```sh
# Arch / Manjaro
curl -fsSL https://code-smith404.github.io/MangoWM-Settings/arch/install.sh | sudo sh

# Debian / Ubuntu
curl -fsSL https://code-smith404.github.io/MangoWM-Settings/apt/install.sh | sudo sh

# Fedora / RHEL
curl -fsSL https://code-smith404.github.io/MangoWM-Settings/rpm/install.sh | sudo sh
```

After that, the plain commands work:

```sh
sudo pacman -S mango-settings     # Arch
sudo apt install mango-settings   # Debian/Ubuntu
sudo dnf install mango-settings   # Fedora/RHEL
```

The package is named `mango-settings` (Arch package names are lowercase).
Manual repository setup and signing details are in
[docs/PACKAGING.md](docs/PACKAGING.md#hosted-apt-and-rpm-repositories).

Dependencies are **python-gobject** and **gtk3** (names per distro:

- Arch: `sudo pacman -S python-gobject gtk3`
- Debian/Ubuntu: `sudo apt install python3-gi gir1.2-gtk-3.0`
- Fedora: `sudo dnf install python3-gobject gtk3`
- Alpine: `doas apk add py3-gobject3 gtk+3.0`
- Void: `sudo xbps-install python3-gobject gtk+3`
- openSUSE: `sudo zypper install python3-gobject gtk3`

## Usage

```sh
mango-settings                 # open the GUI
mango-settings --page Appearance
mango-settings --list-pages    # all page names
mango-settings --check         # validate config, exit 1 on errors (no GUI)
mango-settings --config FILE   # edit a different config
mango-settings --version
mango-settings --selftest      # headless smoke test (CI)
```

`--check` is safe to run in scripts and CI — it reports include problems
(cycles, missing files, depth loops) and exits non-zero on errors.
`--selftest` builds every page headlessly and verifies that widgets write
the intended config keys; it needs a (possibly headless) Wayland/X display
and is what the CI workflow runs.

A manual page is installed as `man mango-settings`.

## Safety model

- Every save first **backs up** the touched files to
  `~/.cache/mango-settings/backups/<timestamp>/` (restores and imports
  snapshot the current state as `pre-restore-*` / `pre-import-*` first, so
  they are reversible)
- Writes are **atomic** (temp file + rename), keep file permissions, write
  through symlinks, and preserve CRLF / final-newline quirks
- Files **modified externally** since loading are detected; the app asks
  before overwriting, and live edits raise a reload banner
- Values MangoWM cannot parse (newlines, >255 chars, bad ranges) are
  **refused** with actionable messages, flagged inline on the widget
- If there is **no config yet**, the app offers to copy MangoWM's default
- Rule records are **validated against Mango's parser** before anything is
  written; malformed or incomplete records are refused

## Portability

- No paths are hard-coded beyond MangoWM's standard `~/.config/mango` and
  the app's own XDG directories; `HOME` is always respected, so the app
  works for any user and in test sandboxes
- **mmsg** missing: reloading MangoWM in-app and monitor detection are
  disabled with a clear note; everything else keeps working
- **gsettings** missing: the Theming page is shown read-only with a note
- **swaybg / swww / feh** missing: the Background page says so and points
  at the package to install
- **xdg-open** missing: the setup menu falls back to launching through
  MangoWM's `mmsg exec`
- No particular distribution is assumed; the dependencies are just
  python-gobject and gtk3, and native packages exist for ten distros
  (see below)

## Development

```sh
python3 -m unittest discover -s tests   # 91 engine tests, no display needed
make test
sh tests/check-versions.sh              # version sync across every package
sh packaging/scripts/update-checksums.sh  # after tagging a release
# GUI smoke test (needs a Wayland/X session, can be headless):
WLR_BACKENDS=headless sway -c /dev/null &   # or any compositor
WAYLAND_DISPLAY=wayland-1 mango-settings --selftest
```

Packaging and release details: [docs/PACKAGING.md](docs/PACKAGING.md).

The config engine lives in the top half of `mango-settings.py` and never
imports GTK — the UI is generated from a declarative schema in the bottom
half.

## Troubleshooting

- **"mmsg not found"** — install MangoWM's `mmsg` for in-app reload and
  monitor detection; everything else works without it
- **"cannot open display"** — the GUI needs a running Wayland/X session;
  use `mango-settings --check` headlessly
- **PyGObject missing** — run `install.sh --print-deps` for your distro's
  exact package names
- **Config not detected** — MangoWM reads `~/.config/mango/config.conf`;
  use `--config` for custom locations
- **"Changed outside Mango Settings" banner** — the config files were
  edited by something else (e.g. a dotfile sync). Click *Reload* to pull
  the changes in before saving, or dismiss it to keep your edits
- **A rule shows a warning triangle** — the record does not match what
  MangoWM accepts (missing identifier, mode fields set apart, out-of-range
  value). Hover the triangle for the exact problem and edit the record
- **My monitor/tag/window rules from an older release look wrong** — old
  positional records were written by Mango Settings ≤ 2.3; current MangoWM
  parses `key:value` records. Re-add them from the rules pages (old lines
  are kept verbatim and flagged, nothing is lost)

## License

MIT — see [LICENSE](LICENSE).
