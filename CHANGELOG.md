# Changelog

## 2.2.2 — blurred, translucent window

- **The window now gets a soft blur effect.** It is drawn as a translucent
  surface (RGBA visual + semi-transparent CSS), so MangoWM's own blur —
  `blur=1` in `config.conf` — blurs whatever is behind it, with rounded
  corners to match. Everything is derived from the GTK theme via
  `alpha(@theme_bg_color, 0.8)`, so it follows light/dark themes.
- **“Blur” checkbox** next to the theme selector in the sidebar turns the
  effect on/off; the choice is remembered in
  `~/.config/mango-settings/settings.ini` (`[ui] blur = 1|0`).
- Falls back to a fully opaque window when no compositor is present, so the
  app never renders a see-through screen in a bare X session.
- Sidebar rows, frames/cards and buttons got matching rounded, translucent
  styling; the selected sidebar row keeps the theme's own colours so the
  label stays readable.

## 2.2.1 — bug-fix release

- **Sidebar no longer disappears.** The window geometry saved on quit is now
  taken from the last *allocated* size instead of `get_size()` at destroy
  time, where GTK reports degenerate values (a saved `width = 1`,
  `sidebar = 1` restored as a collapsed sidebar). Loaded values are clamped,
  the paned position is kept at or above the sidebar's minimum width, and a
  re-fit on `size-allocate` recovers if it is ever clamped to 0.
- **Fixed pages that showed the previous page.** Several pages (Keybindings,
  Workspaces, Window rules, Monitors, Startup apps) never called
  `show_all()`, and `Gtk.Stack` refuses to switch to an invisible child — so
  selecting them kept the old page on screen. Pages are now shown
  explicitly when added to the stack.
- **Fixed the Config files page**, which crashed on open with
  `AttributeError: 'MangoApp' object has no attribute 'bind_doc'`. It now
  lists every loaded file with its line count and error/warning badges, has
  a "Re-check includes" button, and saves raw edits through the same
  backup + atomic-write path as everything else.
- **Fixed `--page` for multi-word titles.** `--page "Config files"`,
  `--page "Window behavior"`, `--page "Input devices"` and friends resolved
  to the title instead of the page key and silently did nothing; page
  resolution is now a testable helper that accepts keys, titles and
  dash/underscore spellings.
- 4 new unit tests (71 total).

## 2.2.0 — category icons, settings search, reset buttons, UI fixes

- **Icons for every category**: symbolic icon per page in the sidebar and in
  the page header, resolved against the active icon theme (symbolic first,
  plain name as fallback, neutral fallback if the theme has neither) so it
  works with Adwaita, Papirus and others.
- **Search now searches settings, not just page names** — typing filters the
  sidebar to the pages containing matches *and* the current page to the
  matching rows, with a "N of M settings match" hint. Esc clears it.
- **Reset-to-default button on every setting** (↺). It deletes the key's
  assignment lines from whichever file defined them, leaving comments,
  blank lines and ordering untouched, so MangoWM falls back to its default.
  Buttons are disabled for settings that are already at the default.
- **Explicit vs default is visible**: settings present in your config are
  bold with a "Set in <file> line N" tooltip; the rest are plain with a
  "using MangoWM's default: X" tooltip.
- **Unsaved-changes marker**: the Save button shows a bullet and the
  suggested-action colour while changes are pending.
- **Window geometry is remembered** (size plus sidebar width) in
  `~/.config/mango-settings/settings.ini`.
- **Fixed a layout bug** where the sidebar was allocated at a negative x
  (clipping the left edge) because `GtkPaned` was positioned narrower than
  the sidebar's minimum width. The paned position is now clamped to the
  sidebar's real minimum.
- 11 new unit tests (67 total).

## 2.1.0 — title-bar-free UI, background changer, real MangoWM keys

- **No title bar**: the header bar is gone. Page search, app theme and the
  Save / Reload Mango / Quit controls now live in the sidebar.
- **New Background page**: pick any image and set it as the wallpaper live
  via swaybg (or swww/feh when swaybg is missing), with fit mode
  (fill/fit/stretch/tile/center), preview, and persistence by rewriting the
  wallpaper line in `~/.config/mango/autostart.sh` (backed up first;
  comments/ordering preserved; a managed line is appended when none exists).
- **Schema corrected to MangoWM's real option keys** — the previous schema
  wrote Hyprland-style keys (`gaps_in`, `col_active_border`, …) that MangoWM
  ignores. Keys, defaults and ranges now come from MangoWM's
  `option_defs.c` and default `config.conf`: appearance (gaps, borders,
  opacity, blur, shadows), animations (types, durations, fades, curves),
  window behavior (focus, snap, drag, scratchpads), layouts (master-stack,
  scroller, dwindle, monocle/deck) and input (keyboard, cursor, trackpad,
  mouse). Options are grouped into titled sections per page.
- 8 new unit tests for the wallpaper persistence engine (56 total).

## 2.0.0 — full rewrite

Fresh, production rewrite of Mango Settings:

- Config engine rebuilt as a pure-Python core (no GTK imports):
  - `source=` / `source_optional=` includes with relative paths, `~/` and
    `$var` expansion, nested includes
  - repeated includes are re-read (last assignment wins), cycles and
    depth > 32 are reported, never followed
  - comments, inline comments, blank lines, ordering, unknown lines,
    symlinks, permissions, CRLF and missing final newlines all preserved
  - atomic writes (temp file + rename), session backups before the first
    change of any file, external-edit detection with confirm-before-overwrite
  - value validation against a schema: bool/int/float/choice/string/color/
    cursor, range checks, refusal of newlines and >255-char values
- Schema-driven GUI (GTK 3 + PyGObject) with 14 pages, sidebar navigation,
  dark/light/system app theme, Ctrl+F page search
- Keybinding editor with conflict detection; window rule / window_rule_once
  editor; monitor rules with "add connected displays" via mmsg; exec /
  exec_once startup editor; tag-bind workspace editor
- Theming page via GSettings (GTK theme, icon theme, cursor theme, color
  scheme); notification daemon detection (swaync, mako, dunst, fnott)
- Raw editor for every config file with per-file error/warning badges
- System info page with copy-to-clipboard
- CLI: `--page`, `--config`, `--check`, `--list-pages`, `--version`
- POSIX-sh installer for Alpine/Void/Debian/Fedora/Arch/openSUSE with
  `--system`, `--prefix`, `--deps/--no-deps`, `--print-deps`, `--ref`,
  `--uninstall`
- Packaging: Makefile, Nix flake, Arch PKGBUILD
- CI: unit tests on Python 3.8–3.13, installer tests in Arch/Debian/
  Fedora/Alpine/Ubuntu containers, version-sync check
- 48 unit tests for the config engine
