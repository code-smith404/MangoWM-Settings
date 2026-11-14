# Mango Settings (MangoWM-Settings)

A modern, GNOME Settings–style graphical settings manager for the
**[MangoWM](https://github.com/mangowm/mango)** Wayland compositor — one
Python file, GTK 3, no bloat.

## Features

- **Sidebar navigation** with 15 pages: Appearance, Animations, Window
  behavior, Layouts, Keybindings, Workspaces, Window rules, Monitors, Input
  devices, Startup apps, Theming, Background, Notifications, Config files,
  System info
- **Chromeless window** — no title bar; search, theme and the
  Save / Reload Mango / Quit controls live in the sidebar
- **Dark / light / system theme** for the app itself
- **Background page**: set the wallpaper live (swaybg, swww or feh) and
  persist it by updating the wallpaper line in `autostart.sh`
- **Live page search** (`Ctrl+F`) and shortcuts: `Ctrl+S` save, `Ctrl+R`
  reload MangoWM, `Ctrl+Q` quit
- **Safe config engine** that reads `~/.config/mango/config.conf` exactly
  like MangoWM (`source=`, `source_optional=`, `var=`), preserves comments,
  blank lines, ordering, symlinks, permissions, CRLF and missing final
  newlines, and writes atomically with session backups
- **Schema matches MangoWM exactly** — every option key, default and range
  comes from MangoWM's `option_defs.c` and default `config.conf`; options
  are grouped into titled sections (gaps, borders, opacity, blur, shadows,
  focus, scroller, dwindle, keyboard, trackpad, mouse, …)
- **Keybinding editor** with conflict detection, **monitor rules** with
  "add connected displays" via `mmsg`, `exec`/`exec_once` startup editor
- **Theming page** driving GTK/icon/cursor themes and color scheme through
  GSettings
- **Notification daemon detection** (swaync / mako / dunst / fnott)
- **Raw editor** for every loaded config file, with warnings
- **`--check` headless validation** for CI and dotfile repos

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
| `--uninstall` | remove everything (your MangoWM config is kept) |

### Distro packages

| Distro | Command |
|---|---|
| Arch / Manjaro | use `packaging/arch/PKGBUILD` (AUR-ready) |
| NixOS / flakes | `nix run github:code-smith404/MangoWM-Settings` |
| Anything | `make install PREFIX=~/.local` |

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
```

`--check` is safe to run in scripts and CI — it reports include problems
(cycles, missing files, depth loops) and exits non-zero on errors.

## Safety model

- Every save first **backs up** the touched files to
  `~/.cache/mango-settings/backups/<timestamp>/`
- Writes are **atomic** (temp file + rename), keep file permissions, write
  through symlinks, and preserve CRLF / final-newline quirks
- Files **modified externally** since loading are detected; the app asks
  before overwriting
- Values MangoWM cannot parse (newlines, >255 chars, bad ranges) are
  **refused** with actionable messages
- If there is **no config yet**, the app offers to copy MangoWM's default

## Development

```sh
python3 -m unittest discover -s tests   # config engine tests, no display needed
make test
sh tests/check-versions.sh              # version sync across PKGBUILD/flake
```

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

## License

MIT — see [LICENSE](LICENSE).
