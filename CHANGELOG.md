# Changelog

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
