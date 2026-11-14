<p align="center">
  <img src="data/io.github.code_smith404.MangoSettings.svg" width="96" alt="Mango Settings icon">
</p>

<h1 align="center">Mango Settings</h1>

<p align="center">
  A GNOME-style settings app for the <a href="https://github.com/mangowm/mango">MangoWM</a> Wayland compositor.<br>
  One Python file, built with GTK 3 and PyGObject.
</p>

<p align="center">
  <a href="#install">Install</a> ·
  <a href="#try-it-without-installing">Try it</a> ·
  <a href="#pages">Pages</a> ·
  <a href="#how-your-config-is-handled">Config handling</a> ·
  <a href="#troubleshooting">Troubleshooting</a>
</p>

![Appearance page](docs/screenshots/appearance.png)

## Install

One command, no clone needed:

```bash
curl -fsSL https://raw.githubusercontent.com/code-smith404/MangoWM-Settings/main/install.sh | bash
```

<details>
<summary>Using <code>wget</code> instead</summary>

```bash
wget -qO- https://raw.githubusercontent.com/code-smith404/MangoWM-Settings/main/install.sh | bash
```
</details>

The installer:

1. checks for Python 3, PyGObject and GTK 3, and offers to install them if they're missing (pacman, apt, dnf, zypper, xbps, apk),
2. installs the app into `~/.local` with no root needed:
   - `~/.local/share/mango-settings/mango-settings.py`
   - `~/.local/bin/mango-settings`
   - a launcher entry and icon, so it shows up in fuzzel, wofi, rofi and other app launchers
3. tells you if `~/.local/bin` isn't in your `PATH`.

Then run `mango-settings`, or open **Mango Settings** from your launcher. To update, run the same command again.

### Installer options

Pass options with `bash -s --`:

```bash
curl -fsSL https://raw.githubusercontent.com/code-smith404/MangoWM-Settings/main/install.sh | bash -s -- --system
```

| Option | What it does |
| --- | --- |
| `--system` | Install for all users into `/usr/local` (uses `sudo`) |
| `--prefix DIR` | Install into `DIR` instead of `~/.local` |
| `--ref REF` | Install a specific branch or tag (default `main`) |
| `--deps` | Install missing dependencies without asking |
| `--no-deps` | Skip the dependency check |
| `--uninstall` | Remove Mango Settings (use the same `--prefix`/`--system` you installed with) |

### Uninstall

```bash
curl -fsSL https://raw.githubusercontent.com/code-smith404/MangoWM-Settings/main/install.sh | bash -s -- --uninstall
```

Your MangoWM config is never touched by uninstalling.

### Try it without installing

Run it straight from GitHub; nothing is written to disk except your own config changes:

```bash
curl -fsSL https://raw.githubusercontent.com/code-smith404/MangoWM-Settings/main/mango-settings.py | python3 -
```

You can pass flags too: `... | python3 - --page Keybindings`.

### Manual install

```bash
git clone https://github.com/code-smith404/MangoWM-Settings.git
cd MangoWM-Settings
./mango-settings.py        # run from the clone
./install.sh               # or install it (uses the local files)
```

### Requirements

MangoWM, Python 3.8+, PyGObject and GTK 3. `mmsg` (part of MangoWM) is used for reloading and for detecting monitors.

| Distro | Command |
| --- | --- |
| Arch / Manjaro / EndeavourOS | `sudo pacman -S --needed python-gobject gtk3` |
| Debian / Ubuntu | `sudo apt install python3-gi gir1.2-gtk-3.0` |
| Fedora | `sudo dnf install python3-gobject gtk3` |
| openSUSE | `sudo zypper install python3-gobject python3-gobject-Gdk typelib-1_0-Gtk-3_0` |
| Void | `sudo xbps-install python3-gobject gtk+3` |
| Alpine | `sudo apk add py3-gobject3 gtk+3.0` |

## Pages

| Page | What you can change |
| --- | --- |
| **Appearance** | Gaps, corner radius, opacity, border width and colors, blur, shadows |
| **Animations** | Open/close animation types (`none`, `fade`, `slide`, `zoom`), fades, durations |
| **Window behavior** | Sloppy focus, cursor warp, new-window placement, floating snap, smart gaps, auto reload |
| **Layouts** | Master factor and count, scroller proportion, dwindle options, layout cycling |
| **Keybindings** | Every `bind` line from all included files, with conflict warnings and key modes |
| **Workspaces** | `tag_rule` entries, such as the layout for each tag |
| **Window rules** | `window_rule` and `window_rule_once` entries |
| **Monitors** | `monitor_rule` entries, plus "Add connected displays" through `mmsg` |
| **Input devices** | Keyboard layout and repeat, Num Lock, cursor theme and size, touchpad, mouse |
| **Startup apps** | `exec_once` and `exec` commands |
| **Theming** | GTK theme, icon theme, cursor theme, color scheme and font (via GSettings) |
| **Notifications** | Detects swaync, mako, dunst or fnott; edit, reload and send a test notification |
| **Config files** | Raw editor for every config file, warnings for files that aren't included |
| **System info** | MangoWM version, OS, kernel, session, CPU, memory, uptime; copy to clipboard |

<table>
  <tr>
    <td><img src="docs/screenshots/keybindings.png" alt="Keybindings page"></td>
    <td><img src="docs/screenshots/animations-dark.png" alt="Animations page in dark mode"></td>
  </tr>
  <tr>
    <td align="center">Keybindings</td>
    <td align="center">Animations, dark mode</td>
  </tr>
</table>

Settings in **bold** are set in your config. The others show MangoWM's built-in default, and the reset button next to a setting removes it from your config again. Simple settings save as you change them. List pages (keybindings, rules, monitors, startup apps) have **Save** and **Revert** buttons.

## Usage

```
mango-settings [--page PAGE] [--config FILE] [--list-pages] [--version]
```

| Flag | Meaning |
| --- | --- |
| `--page Keybindings` | Open a specific page (see `--list-pages`) |
| `--config FILE` | Edit a different root config, like `mango -c FILE` |
| `--list-pages` | Print the page names |
| `--version` | Print the version |

Keyboard shortcuts: <kbd>Ctrl</kbd>+<kbd>F</kbd> search settings, <kbd>Ctrl</kbd>+<kbd>S</kbd> save the current page, <kbd>Ctrl</kbd>+<kbd>R</kbd> reload MangoWM, <kbd>Ctrl</kbd>+<kbd>Q</kbd> quit.

The app's own light/dark/system appearance is in the menu (☰).

Example MangoWM keybinding to open it:

```ini
bind=SUPER,i,spawn,mango-settings
```

## How your config is handled

- **Same lookup as MangoWM.** It uses `~/.config/mango/config.conf`, or the file passed with `--config`.
- **Included files are followed.** `source=` and `source_optional=` lines are read in order, with `./` relative to the config folder and `~/` meaning your home folder. A setting is changed where MangoWM actually reads it. If a key appears more than once, the last one wins, just like in MangoWM.
- **Your files stay yours.** Comments, blank lines, ordering and inline comments are kept, and only the lines you change are rewritten. Symlinks (for example from GNU Stow or a dotfiles repo) and file permissions are preserved. Writes are atomic, so a crash never leaves a half-written config.
- **Backups.** Before the first change to a file in a session, a copy goes to `~/.cache/mango-settings/backups/<date-time>/`. You can open it from the **Config files** page.
- **External edits are detected.** Files are re-read before every write. If a file changed on disk while you had unsaved list edits, the app asks before overwriting.
- **Applying changes.** MangoWM reloads its config automatically when `auto_reload_config` is on (the default). Otherwise press **Reload MangoWM**, which runs `mmsg dispatch reload_config`.
- **TOML configs** (`config.toml`) are shown read-only for now. Use the **Config files** page to edit them.
- **No config yet?** The app offers to copy MangoWM's default config into `~/.config/mango/`.

## Troubleshooting

<details>
<summary><code>ModuleNotFoundError: No module named 'gi'</code> or <code>Namespace Gtk not available</code></summary>

PyGObject or GTK 3 is missing. Install them with the command for your distro from [Requirements](#requirements). If you use pyenv, conda or a virtualenv, run the app with your system Python (`/usr/bin/python3`), because PyGObject is installed there.
</details>

<details>
<summary><code>mango-settings: command not found</code></summary>

`~/.local/bin` is not in your `PATH`. Add this to your shell profile, or run `~/.local/bin/mango-settings`:

```bash
export PATH="$HOME/.local/bin:$PATH"
```
</details>

<details>
<summary>Changes don't take effect</summary>

Make sure MangoWM is running and `mmsg` is installed, then press <kbd>Ctrl</kbd>+<kbd>R</kbd>. Some options, such as monitor modes and input devices, only apply to newly connected devices or after restarting MangoWM.
</details>

<details>
<summary>Something went wrong with my config</summary>

Restore the copy from `~/.cache/mango-settings/backups/`. Every file is backed up before its first change in a session.
</details>

## Development

```bash
python3 -m unittest discover tests      # config engine tests, no display needed
python3 -m pyflakes mango-settings.py   # lint
shellcheck install.sh
```

The config engine (parsing, `source` resolution, writing) is plain Python and has no GTK dependency, so it can be tested on its own. Bug reports and pull requests are welcome.

## License

[MIT](LICENSE) © 2026 Harikesh Verma ([code-smith404](https://github.com/code-smith404))
