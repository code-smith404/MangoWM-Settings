# Changelog

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

- Online installer (`install.sh`): `curl ... | bash`, with `--system`, `--prefix`, `--ref`, `--deps` and `--uninstall`, plus a dependency check for major distros.
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
