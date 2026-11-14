# Changelog

## 1.1.1 (2026-11-14)

### Fixed

- A file included more than once was only read the first time, so the app could show and edit a value that MangoWM overrides later. Includes are now read every time, in MangoWM's order, and the last assignment wins.
- Include cycles were silently skipped, but MangoWM would hang on them. They are now reported, along with missing `source=` files and includes nested deeper than 32 levels.
- `source` and `source_optional` keys, `$var` expansion, quote stripping, comment handling and `key=` lines now follow MangoWM's parser exactly. TOML files included from a `.conf` file are no longer parsed as `.conf`.
- Values containing ` #` were cut off by MangoWM after saving. They are now quoted. Values MangoWM can't read (over 255 characters or with line breaks) are refused, and existing quotes are kept.
- Files with non-UTF-8 bytes were corrupted on save, CRLF line endings and missing final newlines were changed, and form feeds were treated as line breaks. Files are now written back byte-for-byte except for the changed lines.
- A failed backup was ignored and the file was changed anyway. A failed backup now stops the save. Backups are no longer hidden files.
- Saving into an unwritable included file (for example a system file) failed. The setting is now added to your own config instead.
- Removing a setting from a file that is included twice could delete the wrong line. The list pages (rules, monitors) rewrote lines you hadn't touched.
- Unreadable included files crashed the app. Error messages are now always readable.
- "Copy default config" could overwrite a config created in the meantime.
- The window size was saved while maximized.
- `--list-pages` needed GTK. A clear message is shown when there is no display.

### Installer

- A relative or `~` prefix produced a broken launcher. Paths with spaces or quotes are now quoted correctly, and the Nix launcher keeps arguments intact.
- Ctrl-C didn't stop the installer. Interrupted installs no longer leave half-installed files, and downloaded files are checked before installing.
- `--uninstall` removed whatever was at the target path. It now only removes files it installed and leaves distro packages alone.

### Added

- `--check` lists the files MangoWM reads and any include problems, without a display.
- `make check-versions` and a CI check that the app, package and changelog versions match.

## 1.1.0 (2026-11-14)

### Fixed

- Settings changed on one page could be lost when another page saved, because each page kept its own stale copy of the config. All pages now share one config model that re-reads files from disk before every write.
- Saving replaced symlinked configs (GNU Stow, dotfiles repos) with plain files and reset permissions to 0600. Writes now go through symlinks atomically and keep the original mode.
- "Reload" called `mangoctl`, which doesn't exist. It now runs `mmsg dispatch reload_config`.
- The Config files page crashed when opening a file (`open_file` was missing).
- Theming page: crash when `gsettings` produced no output, and a polling timer that was never stopped.
- Animation dropdowns offered types MangoWM doesn't support. Only `none`, `fade`, `slide` and `zoom` are offered now.
- Wrong fallback defaults for several options. Defaults now match the MangoWM source.
- Whole numbers were written as `12.0`.
- Wrong monitor rule format hint, and wrong `isfloating` key in the window rule example (`is_floating`).
- `bind.conf`, `tag.conf` and similar files were edited even when the main config didn't `source` them, so changes had no effect. The app now follows `source=`/`source_optional=` like MangoWM and offers to include stray files.
- `config.toml` setups were ignored and a new `config.conf` was silently created next to them. TOML configs are now detected and shown read-only.
- Every keystroke in a field triggered a save. Saves are debounced now.
- Toast notifications were invisible.
- Keybindings were reordered on save.
- Session/autostart editor wrote invalid shell syntax.
- Theme lists only looked in a few folders. They now include `~/.themes`, `~/.icons` and all XDG data dirs.
- Long pages couldn't scroll.
- Dark mode used a non-existent `Adwaita-dark` theme name.
- The desktop file had a hardcoded `/home/code_smith/...` path.

### Added

- Online installer (`install.sh`): `curl ... | sh`, with `--system`, `--prefix`, `--ref`, `--deps`, `--print-deps` and `--uninstall`.
- Multi-distro support:
  - the installer is POSIX `sh`, detects the distro from `/etc/os-release` (including derivatives) and installs dependencies with pacman, apt, dnf/yum, rpm-ostree, zypper, xbps, apk, emerge or eopkg, using `sudo`, `doas` or `run0`
  - the launcher always uses a Python that can load GTK 3, so pyenv or conda on `PATH` can't break it
  - NixOS: a flake (`nix run github:code-smith404/MangoWM-Settings`) and a `nix-shell` launcher fallback
  - a Makefile with `PREFIX`/`DESTDIR` for packagers, and an Arch `PKGBUILD`
  - the default MangoWM config is found on more distros (`/usr/etc` on openSUSE, NixOS, custom prefixes); CPU names on ARM boards; themes from Nix profiles and Flatpak
  - CI installs the app in Arch, Manjaro, Debian, Ubuntu, Fedora, AlmaLinux, openSUSE, Void and Alpine containers
- Run without installing: `curl ... | python3 -`.
- App icon and a proper desktop entry (`io.github.code_smith404.MangoSettings`).
- Per-setting reset buttons. Settings set in your config are shown in bold.
- Window rules page, Startup apps based on `exec_once`, "Add connected displays" for monitors, keybinding conflict warnings and key-mode column.
- Border width/colors and blur settings.
- Light/Dark/System appearance for the app itself, a search filter, keyboard shortcuts, and remembered window size and page.
- Automatic backups in `~/.cache/mango-settings/backups/`.
- Command-line flags `--page`, `--config`, `--list-pages`, `--version`.
- Unit tests for the config engine and a CI workflow.

## 1.0.0

- Initial release.
