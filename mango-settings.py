#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Harikesh Verma (code-smith404)
"""Mango Settings — a GNOME-Settings-style settings manager for MangoWM.

Single-file GTK 3 / PyGObject application. It reads your MangoWM config the
same way the compositor does (following ``source=`` includes, last value
wins) and writes changes back in place, preserving comments, symlinks and
file permissions.
"""

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from collections import namedtuple

__version__ = "1.1.0"
APP_ID = "io.github.code_smith404.MangoSettings"
REPO_URL = "https://github.com/code-smith404/MangoWM-Settings"
DOCS_URL = "https://github.com/mangowm/mango/tree/main/docs"

HOME = os.path.expanduser("~")
MANGO_DIR = os.path.join(HOME, ".config", "mango")
SYSTEM_CONFIGS = (
    "/etc/mango/config.conf",
    "/usr/local/etc/mango/config.conf",
    "/usr/etc/mango/config.conf",                    # openSUSE vendor configs
    "/run/current-system/sw/etc/mango/config.conf",  # NixOS
    "/usr/share/mango/config.conf",
    "/usr/share/doc/mango/config.conf",
)


def system_default_config():
    """Find MangoWM's sample config, wherever the distro package put it."""
    candidates = list(SYSTEM_CONFIGS)
    binary = shutil.which("mango")
    if binary:  # <prefix>/bin/mango -> <prefix>/etc/mango (Nix store, /opt, custom prefixes)
        prefix = os.path.dirname(os.path.dirname(os.path.realpath(binary)))
        candidates.insert(0, os.path.join(prefix, "etc", "mango", "config.conf"))
    return next((p for p in candidates if os.path.isfile(p)), None)
SESSION_STAMP = time.strftime("%Y%m%d-%H%M%S")


# ---------------------------------------------------------------------------
# Settings schema
#
# Every key, default and range below was checked against MangoWM's parser
# (src/config/option_defs.c). Item format:
#   (key, label, kind, default, options)
# kinds: bool | int (lo, hi, step) | float (lo, hi, step, digits)
#        | choice [values] | string | cursor
# A default of None means "unset" — MangoWM picks its own behaviour.
# ---------------------------------------------------------------------------

ANIM_TYPES = ["slide", "zoom", "fade", "none"]

CONFIG_SCHEMA = {
    "Appearance": [
        ("Gaps", None, [
            ("gap_inner_horizontal", "Inner gap (horizontal)", "int", 5, (0, 1000, 1)),
            ("gap_inner_vertical", "Inner gap (vertical)", "int", 5, (0, 1000, 1)),
            ("gap_outer_horizontal", "Outer gap (horizontal)", "int", 10, (0, 1000, 1)),
            ("gap_outer_vertical", "Outer gap (vertical)", "int", 10, (0, 1000, 1)),
        ]),
        ("Windows", None, [
            ("border_radius", "Corner radius", "int", 0, (0, 100, 1)),
            ("focused_opacity", "Focused window opacity", "float", 1.0, (0.0, 1.0, 0.05, 2)),
            ("unfocused_opacity", "Unfocused window opacity", "float", 1.0, (0.0, 1.0, 0.05, 2)),
        ]),
        ("Borders", None, [
            ("border_px", "Border width", "int", 4, (0, 50, 1)),
            ("focus_color", "Focused border color (0xRRGGBBAA)", "string", "0xc66b25ff", None),
            ("border_color", "Unfocused border color (0xRRGGBBAA)", "string", "0x444444ff", None),
        ]),
        ("Blur", None, [
            ("blur", "Blur behind transparent windows", "bool", 0, None),
            ("blur_layer", "Blur behind bars and launchers", "bool", 0, None),
        ]),
        ("Shadows", None, [
            ("shadows", "Shadows", "bool", 0, None),
            ("shadows_size", "Shadow size", "int", 10, (0, 100, 1)),
            ("shadows_blur", "Shadow blur", "int", 15, (0, 100, 1)),
        ]),
    ],
    "Animations": [
        ("General", None, [
            ("animations", "Window animations", "bool", 1, None),
            ("layer_animations", "Layer-shell animations (bars, launchers)", "bool", 0, None),
        ]),
        ("Style", None, [
            ("animation_type_open", "Open animation", "choice", None, ANIM_TYPES),
            ("animation_type_close", "Close animation", "choice", None, ANIM_TYPES),
            ("animation_fade_in", "Fade in", "bool", 1, None),
            ("animation_fade_out", "Fade out", "bool", 1, None),
        ]),
        ("Durations (ms)", None, [
            ("animation_duration_open", "Open", "int", 400, (0, 5000, 10)),
            ("animation_duration_close", "Close", "int", 300, (0, 5000, 10)),
            ("animation_duration_move", "Move", "int", 500, (0, 5000, 10)),
            ("animation_duration_tag", "Tag switch", "int", 300, (0, 5000, 10)),
        ]),
    ],
    "Window behavior": [
        ("Focus", None, [
            ("sloppy_focus", "Focus follows mouse", "bool", 1, None),
            ("focus_on_activate", "Focus windows that request activation", "bool", 1, None),
            ("warp_cursor", "Warp cursor to focused window", "bool", 1, None),
            ("focus_cross_monitor", "Directional focus crosses monitors", "bool", 0, None),
        ]),
        ("Tiling and floating", None, [
            ("new_is_master", "New window becomes master", "bool", 1, None),
            ("drag_tile_to_tile", "Drag tiled windows onto other tiles", "bool", 1, None),
            ("enable_floating_snap", "Snap floating windows to edges", "bool", 0, None),
            ("snap_distance", "Snap distance (px)", "int", 30, (0, 500, 1)),
        ]),
        ("Borders and gaps", None, [
            ("smart_gaps", "Smart gaps (no gaps with a single window)", "bool", 0, None),
            ("no_border_when_single", "No border with a single window", "bool", 0, None),
        ]),
        ("Configuration", None, [
            ("auto_reload_config", "Reload MangoWM automatically when config files change", "bool", 1, None),
        ]),
    ],
    "Layouts": [
        ("Master-stack", None, [
            ("default_master_factor", "Master area width", "float", 0.55, (0.05, 0.95, 0.05, 2)),
            ("default_master_count", "Windows in master area", "int", 1, (1, 20, 1)),
        ]),
        ("Scroller", None, [
            ("scroller_default_proportion", "Default column width", "float", 0.9, (0.1, 1.0, 0.05, 2)),
            ("scroller_focus_center", "Keep focused column centered", "bool", 0, None),
        ]),
        ("Dwindle", None, [
            ("dwindle_smart_split", "Smart split", "bool", 0, None),
            ("dwindle_preserve_split", "Preserve split ratio", "bool", 0, None),
        ]),
        ("Cycling", "Comma-separated, e.g. tile,scroller,grid,deck,monocle,center_tile", [
            ("circle_layout", "Layouts to cycle through", "string", "", None),
        ]),
    ],
    "Input devices": [
        ("Keyboard", None, [
            ("repeat_rate", "Key repeat rate (per second)", "int", 25, (1, 1000, 1)),
            ("repeat_delay", "Key repeat delay (ms)", "int", 600, (1, 20000, 10)),
            ("numlock_on", "Num Lock on at startup", "bool", 0, None),
            ("xkb_rules_layout", "Keyboard layouts (e.g. us,de)", "string", "", None),
        ]),
        ("Cursor", None, [
            ("cursor_theme", "Cursor theme", "cursor", "", None),
            ("cursor_size", "Cursor size", "int", 24, (4, 512, 1)),
        ]),
        ("Trackpad", "Some trackpad and mouse options apply after logging in again.", [
            ("disable_trackpad", "Disable trackpad", "bool", 0, None),
            ("tap_to_click", "Tap to click", "bool", 1, None),
            ("tap_and_drag", "Tap and drag", "bool", 1, None),
            ("drag_lock", "Drag lock", "bool", 1, None),
            ("trackpad_natural_scrolling", "Natural scrolling", "bool", 0, None),
            ("trackpad_disable_while_typing", "Disable while typing", "bool", 1, None),
        ]),
        ("Mouse", None, [
            ("mouse_natural_scrolling", "Natural scrolling", "bool", 0, None),
            ("mouse_accel_speed", "Pointer acceleration", "float", 0.0, (-1.0, 1.0, 0.05, 2)),
        ]),
    ],
}


# ---------------------------------------------------------------------------
# Config file handling (no GTK needed — this part is unit-testable)
# ---------------------------------------------------------------------------

Entry = namedtuple("Entry", "path idx key value")


def read_lines(path):
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            return fh.read().splitlines()
    except (FileNotFoundError, NotADirectoryError):
        return []


def file_stamp(path):
    try:
        st = os.stat(path)
        return (st.st_mtime_ns, st.st_size)
    except OSError:
        return None


_backed_up = set()


def backup_dir():
    base = os.environ.get("XDG_CACHE_HOME") or os.path.join(HOME, ".cache")
    return os.path.join(base, "mango-settings", "backups")


def backup_once(real):
    """Copy a file to ~/.cache/mango-settings/backups/<session>/ once per run."""
    if real in _backed_up or not os.path.isfile(real):
        return
    _backed_up.add(real)
    try:
        dest = os.path.join(backup_dir(), SESSION_STAMP)
        os.makedirs(dest, exist_ok=True)
        name = os.path.relpath(real, HOME) if real.startswith(HOME) else real
        shutil.copy2(real, os.path.join(dest, name.strip(os.sep).replace(os.sep, "__")))
    except OSError:
        pass


def write_text(path, text, executable=False):
    """Atomically write *text* to *path*.

    * follows symlinks, so dotfile managers (GNU Stow, chezmoi symlinks, …)
      keep working — the link target is updated, not replaced;
    * keeps the original permission bits (new files get 0644 / 0755);
    * creates the parent directory if needed and backs up the old file.
    """
    real = os.path.realpath(path)
    folder = os.path.dirname(real)
    os.makedirs(folder, exist_ok=True)
    backup_once(real)
    fd, tmp = tempfile.mkstemp(dir=folder, prefix=".%s." % os.path.basename(real),
                               suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
        if os.path.exists(real):
            shutil.copymode(real, tmp)
        else:
            umask = os.umask(0)
            os.umask(umask)
            os.chmod(tmp, (0o777 if executable else 0o666) & ~umask)
        os.replace(tmp, real)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def write_lines(path, lines):
    write_text(path, "\n".join(lines) + "\n")


def split_comment(line):
    """Split off an inline comment the way MangoWM does: a ``#`` that follows
    whitespace and is not inside quotes. Returns (body, comment)."""
    quote = None
    for i, ch in enumerate(line):
        if quote is None:
            if ch in "'\"":
                quote = ch
            elif ch == "#" and i > 0 and line[i - 1].isspace():
                j = i
                while j > 0 and line[j - 1].isspace():
                    j -= 1
                return line[:j], line[j:]
        elif ch == quote:
            quote = None
    return line, ""


def strip_quotes(value):
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
        return value[1:-1]
    return value


def split_line(line):
    """Parse ``key=value`` → (key, value, comment, indent), or None."""
    stripped = line.strip()
    if not stripped or stripped.startswith("#") or "=" not in stripped:
        return None
    body, comment = split_comment(line)
    key, value = body.split("=", 1)
    indent = line[:len(line) - len(line.lstrip())]
    key = key.strip()
    if not key:
        return None
    return key, strip_quotes(value.strip()), comment, indent


def format_line(key, value, template=None):
    """Build a config line, keeping indentation and inline comment of *template*."""
    indent, comment = "", ""
    if template is not None:
        parsed = split_line(template)
        if parsed:
            indent, comment = parsed[3], parsed[2]
    return "%s%s=%s%s" % (indent, key, value, comment)


def resolve_source(value, root):
    """Resolve a ``source=`` path exactly like MangoWM's resolve_config_path()."""
    value = value.strip()
    if value.startswith("./"):
        return os.path.join(os.path.dirname(root), value[2:])
    if value == "~" or value.startswith("~/"):
        return HOME + value[1:]
    return value


def detect_root_config(explicit=None):
    """Return (path, kind) of the config MangoWM will load.

    kind is "conf", "toml" (unsupported, read-only) or "new" (nothing exists yet).
    Mirrors the lookup order in MangoWM's src/config/load.c.
    """
    if explicit:
        path = os.path.abspath(os.path.expanduser(explicit))
        kind = "toml" if path.lower().endswith(".toml") else "conf"
        return path, kind
    conf = os.path.join(MANGO_DIR, "config.conf")
    toml = os.path.join(MANGO_DIR, "config.toml")
    if os.path.exists(conf):
        return conf, "conf"
    if os.path.exists(toml):
        return toml, "toml"
    return conf, "new"


class ConfigTree:
    """The MangoWM config: a root file plus everything it ``source``s.

    MangoWM parses top to bottom, recursing into ``source=`` and
    ``source_optional=`` lines, and the last assignment of a key wins. We
    always re-read from disk before changing anything, so edits made in an
    editor (or by another page) are never clobbered.
    """

    def __init__(self, root):
        self.root = root

    def scan(self):
        """Return (entries, files) in MangoWM parse order."""
        entries, files, seen = [], [], set()

        def walk(path, depth):
            real = os.path.realpath(path)
            if depth > 16 or real in seen or not os.path.isfile(path):
                return
            seen.add(real)
            files.append(path)
            for idx, line in enumerate(read_lines(path)):
                parsed = split_line(line)
                if not parsed:
                    continue
                key, value = parsed[0], parsed[1]
                if key in ("source", "source_optional"):
                    walk(resolve_source(value, self.root), depth + 1)
                    continue
                entries.append(Entry(path, idx, key, value))

        walk(self.root, 0)
        return entries, files

    def files(self):
        return self.scan()[1]

    def values(self):
        """Effective value of each key (last one wins)."""
        return {e.key: e.value for e in self.scan()[0]}

    def get(self, key, default=None):
        return self.values().get(key, default)

    def set(self, key, value):
        value = str(value)
        hits = [e for e in self.scan()[0] if e.key == key]
        if hits:
            e = hits[-1]
            lines = read_lines(e.path)
            if lines[e.idx] == format_line(key, value, lines[e.idx]):
                return e.path
            lines[e.idx] = format_line(key, value, lines[e.idx])
            write_lines(e.path, lines)
            return e.path
        lines = read_lines(self.root)
        lines.append("%s=%s" % (key, value))
        write_lines(self.root, lines)
        return self.root

    def unset(self, key):
        """Remove every assignment of *key* so MangoWM falls back to its default."""
        by_file = {}
        for e in self.scan()[0]:
            if e.key == key:
                by_file.setdefault(e.path, []).append(e.idx)
        for path, idxs in by_file.items():
            lines = read_lines(path)
            for i in sorted(idxs, reverse=True):
                del lines[i]
            write_lines(path, lines)

    def is_sourced(self, path):
        real = os.path.realpath(path)
        return any(os.path.realpath(f) == real for f in self.files())

    def add_source(self, path):
        """Append ``source=./name`` (or an absolute path) to the root config."""
        rel = os.path.relpath(path, os.path.dirname(self.root))
        line = "source=./%s" % rel if not rel.startswith("..") else "source=%s" % path
        lines = read_lines(self.root)
        lines.append(line)
        write_lines(self.root, lines)


def parse_bool(value, default=0):
    try:
        return int(float(str(value).strip())) != 0  # MangoWM uses atoi()
    except (TypeError, ValueError):
        return bool(default)


def format_number(value, kind, digits=2):
    if kind == "int":
        return str(int(round(value)))
    text = ("%%.%df" % digits) % value
    text = text.rstrip("0")
    return text + "0" if text.endswith(".") else text


def normalize_mods(mods):
    parts = sorted(p.strip().upper() for p in mods.split("+") if p.strip())
    parts = [p for p in parts if p != "NONE"] or ["NONE"]
    return "+".join(parts)


# ---------------------------------------------------------------------------
# System helpers
# ---------------------------------------------------------------------------

def read_first(path, default=""):
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            return fh.read()
    except OSError:
        return default


def run_quick(argv, timeout=2):
    if not shutil.which(argv[0]):
        return None
    try:
        res = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.SubprocessError):
        return None
    return (res.stdout or res.stderr).strip() or None


def mango_version():
    out = run_quick(["mmsg", "get", "version"])
    if out:
        try:
            return json.loads(out).get("version") or out
        except (ValueError, AttributeError):
            pass
    out = run_quick(["mango", "-v"])
    return out.splitlines()[0] if out else "not found"


def mango_monitors():
    out = run_quick(["mmsg", "get", "all-monitors"])
    if not out:
        return []
    try:
        return json.loads(out).get("monitors", [])
    except (ValueError, AttributeError):
        return []


def format_uptime():
    try:
        secs = int(float(read_first("/proc/uptime", "0").split()[0]))
    except (ValueError, IndexError):
        return "unknown"
    days, rem = divmod(secs, 86400)
    hours, rem = divmod(rem, 3600)
    mins = rem // 60
    parts = []
    if days:
        parts.append("%d day%s" % (days, "s" if days != 1 else ""))
    if hours:
        parts.append("%d hour%s" % (hours, "s" if hours != 1 else ""))
    parts.append("%d minute%s" % (mins, "s" if mins != 1 else ""))
    return ", ".join(parts)


def memory_usage():
    info = {}
    for line in read_first("/proc/meminfo").splitlines():
        name, _, rest = line.partition(":")
        try:
            info[name] = int(rest.split()[0])
        except (ValueError, IndexError):
            pass
    if "MemTotal" not in info:
        return "unknown"
    used = info["MemTotal"] - info.get("MemAvailable", info.get("MemFree", 0))
    return "%.1f GiB / %.1f GiB" % (used / 1048576, info["MemTotal"] / 1048576)


def os_name():
    text = read_first("/etc/os-release") or read_first("/usr/lib/os-release")
    for line in text.splitlines():
        if line.startswith("PRETTY_NAME="):
            return strip_quotes(line.split("=", 1)[1].strip())
    return "Linux"


def cpu_name():
    info = read_first("/proc/cpuinfo")
    for field in ("model name", "hardware", "cpu model", "cpu"):  # x86, ARM, MIPS, POWER
        for line in info.splitlines():
            name, _, value = line.partition(":")
            if name.strip().lower() == field and value.strip():
                return value.strip()
    model = read_first("/proc/device-tree/model").strip("\x00\n ")  # Raspberry Pi and other boards
    return model or os.uname().machine


def data_dirs():
    dirs = [os.environ.get("XDG_DATA_HOME") or os.path.join(HOME, ".local", "share")]
    dirs += (os.environ.get("XDG_DATA_DIRS") or "/usr/local/share:/usr/share").split(":")
    # Places some distros keep themes that may be missing from XDG_DATA_DIRS
    # when the app is started from a launcher: Nix profiles and Flatpak exports.
    user = os.environ.get("USER") or os.path.basename(HOME)
    dirs += [os.path.join(HOME, ".nix-profile", "share"), "/etc/profiles/per-user/%s/share" % user,
             "/run/current-system/sw/share", "/usr/share",
             os.path.join(HOME, ".local", "share", "flatpak", "exports", "share"),
             "/var/lib/flatpak/exports/share"]
    out = []
    for d in dirs:
        if d and d not in out:
            out.append(d)
    return out


def _list_theme_dirs(bases, predicate):
    found = set()
    for base in bases:
        try:
            names = os.listdir(base)
        except OSError:
            continue
        for name in names:
            path = os.path.join(base, name)
            if not name.startswith(".") and os.path.isdir(path) and predicate(path):
                found.add(name)
    return sorted(found, key=str.lower)


def gtk_themes():
    bases = [os.path.join(HOME, ".themes")] + [os.path.join(d, "themes") for d in data_dirs()]
    found = _list_theme_dirs(bases, lambda p: os.path.isdir(os.path.join(p, "gtk-3.0")))
    for builtin in ("Adwaita", "HighContrast", "HighContrastInverse"):  # compiled into GTK
        if builtin not in found:
            found.append(builtin)
    return sorted(found, key=str.lower)


def icon_bases():
    return [os.path.join(HOME, ".icons")] + [os.path.join(d, "icons") for d in data_dirs()]


def icon_themes():
    def is_icon_theme(path):
        if not os.path.isfile(os.path.join(path, "index.theme")):
            return False
        return "Directories=" in read_first(os.path.join(path, "index.theme"))
    return [t for t in _list_theme_dirs(icon_bases(), is_icon_theme) if t != "default"]


def cursor_themes():
    return _list_theme_dirs(icon_bases(), lambda p: os.path.isdir(os.path.join(p, "cursors")))


# ---------------------------------------------------------------------------
# GTK user interface
# ---------------------------------------------------------------------------

try:
    import gi
    gi.require_version("Gtk", "3.0")
    gi.require_version("Gdk", "3.0")
    from gi.repository import Gdk, Gio, GLib, Gtk, Pango
    HAVE_GTK = True
except (ImportError, ValueError) as _gtk_error:
    HAVE_GTK = False
    GTK_ERROR = _gtk_error

if HAVE_GTK:

    APP_CSS = b"""
    headerbar.mango {
      background-image: none;
      background-color: shade(@theme_selected_bg_color, 0.95);
      border-color: shade(@theme_selected_bg_color, 0.8);
    }
    headerbar.mango .title, headerbar.mango .subtitle,
    headerbar.mango button, headerbar.mango button label,
    headerbar.mango button image { color: white; }
    headerbar.mango button {
      background-image: none;
      background-color: alpha(white, 0.16);
      border-color: alpha(white, 0.22);
      box-shadow: none;
      text-shadow: none;
      -gtk-icon-shadow: none;
    }
    headerbar.mango button:hover { background-color: alpha(white, 0.28); }

    .sidebar { border-right: 1px solid alpha(@theme_fg_color, 0.12); }
    .sidebar list { background-color: transparent; }
    .sidebar list row { border-radius: 8px; margin: 1px 6px; padding: 2px 4px; }
    .sidebar list row:selected {
      background-color: @theme_selected_bg_color;
      color: @theme_selected_fg_color;
    }
    .sidebar list row:selected image, .sidebar list row:selected label {
      color: @theme_selected_fg_color;
    }

    .page-title { font-weight: 800; font-size: 20px; }
    .section-title { font-weight: 700; margin-top: 6px; }
    .dim-label { opacity: 0.6; }
    .key-name { font-family: monospace; font-size: 85%; opacity: 0.55; }

    list.card {
      border: 1px solid alpha(@theme_fg_color, 0.14);
      border-radius: 10px;
      background-color: @theme_base_color;
    }
    list.card row { padding: 6px 12px; border-bottom: 1px solid alpha(@theme_fg_color, 0.07); }
    list.card row:last-child { border-bottom: none; }
    list.card row.explicit .setting-label { font-weight: 600; }

    .welcome {
      background-color: alpha(@theme_selected_bg_color, 0.10);
      border: 1px solid alpha(@theme_selected_bg_color, 0.25);
      border-radius: 12px;
      padding: 16px;
    }
    .toast {
      background-color: alpha(black, 0.82);
      color: white;
      border-radius: 999px;
      padding: 8px 18px;
      margin: 18px 18px 64px 18px;
    }
    .toast.error { background-color: #b3261e; }
    .warning-text { color: #c77c02; font-weight: 700; }
    """

    def open_path(path, app=None):
        """Open a file or folder with the default application."""
        try:
            Gio.AppInfo.launch_default_for_uri(GLib.filename_to_uri(path, None), None)
            return True
        except GLib.Error as err:
            if shutil.which("xdg-open"):
                subprocess.Popen(["xdg-open", path], stdout=subprocess.DEVNULL,
                                 stderr=subprocess.DEVNULL)
                return True
            if app:
                app.toast("Couldn't open %s: %s" % (path, err.message), error=True)
            return False

    def run_async(argv, on_done=None):
        """Run a command without blocking the UI. on_done(ok, output)."""
        if not shutil.which(argv[0]):
            if on_done:
                on_done(False, "%s is not installed" % argv[0])
            return
        try:
            proc = Gio.Subprocess.new(argv, Gio.SubprocessFlags.STDOUT_PIPE
                                      | Gio.SubprocessFlags.STDERR_PIPE)
        except GLib.Error as err:
            if on_done:
                on_done(False, err.message)
            return

        def finished(p, res):
            try:
                _ok, out, err = p.communicate_utf8_finish(res)
            except GLib.Error as e:
                out, err = "", e.message
            if on_done:
                on_done(p.get_successful(), (out or err or "").strip())
        proc.communicate_utf8_async(None, None, finished)

    def interface_settings():
        source = Gio.SettingsSchemaSource.get_default()
        if source is None:
            return None
        schema = source.lookup("org.gnome.desktop.interface", True)
        if schema is None:
            return None
        return Gio.Settings.new("org.gnome.desktop.interface"), schema

    def section_label(text):
        lab = Gtk.Label(label=text, xalign=0)
        lab.get_style_context().add_class("section-title")
        return lab

    def dim_label(text, wrap=True, markup=False):
        lab = Gtk.Label(xalign=0, wrap=wrap)
        lab.set_line_wrap_mode(Pango.WrapMode.WORD_CHAR)
        if markup:
            lab.set_markup(text)
        else:
            lab.set_text(text)
        lab.get_style_context().add_class("dim-label")
        return lab

    class Debouncer:
        """Call fn(*args) once input has been quiet for *ms* milliseconds."""

        def __init__(self, ms, fn):
            self.ms, self.fn, self.src, self.args = ms, fn, None, None

        def __call__(self, *args):
            self.args = args
            if self.src:
                GLib.source_remove(self.src)
            self.src = GLib.timeout_add(self.ms, self._fire)

        def _fire(self):
            self.src = None
            self.fn(*self.args)
            return False

        def flush(self):
            if self.src:
                GLib.source_remove(self.src)
                self._fire()

    # -- pages -------------------------------------------------------------

    class Page(Gtk.Box):
        """Base class. Pages may implement refresh(), save() and is_dirty()."""
        edits_mango_config = True
        keywords = ()

        def __init__(self, app, title, subtitle=None):
            super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=12)
            self.app = app
            self.title = title
            self.set_border_width(24)
            head = Gtk.Label(label=title, xalign=0)
            head.get_style_context().add_class("page-title")
            self.pack_start(head, False, False, 0)
            if subtitle:
                self.pack_start(dim_label(subtitle, markup=True), False, False, 0)

        def refresh(self):
            pass

        def is_dirty(self):
            return False

        def matches(self, query):
            text = " ".join((self.title,) + tuple(self.keywords)).lower()
            return query in text

        def filter(self, query):
            pass

    class SettingsPage(Page):
        """Switches, spin buttons and dropdowns bound to MangoWM options."""

        def __init__(self, app, title, sections):
            super().__init__(app, title,
                             "Changes are written to your MangoWM config as you make them. "
                             "<b>Bold</b> settings are set in your config; the others use "
                             "MangoWM's default.")
            self.sections = sections
            self.items = {}
            self._loading = False
            self.keywords = tuple(w for _s, _n, items in sections for it in items
                                  for w in (it[0], it[1]))
            self.debounced = {}
            for sec_title, note, items in sections:
                block = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
                block.pack_start(section_label(sec_title), False, False, 0)
                if note:
                    block.pack_start(dim_label(note), False, False, 0)
                lb = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
                lb.get_style_context().add_class("card")
                for item in items:
                    lb.add(self._build_row(item, block))
                block.pack_start(lb, False, False, 0)
                self.pack_start(block, False, False, 0)
            self.refresh()

        def _build_row(self, item, block):
            key, label, kind, default, opts = item
            row = Gtk.ListBoxRow(activatable=False)
            box = Gtk.Box(spacing=12)
            texts = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=1)
            lab = Gtk.Label(label=label, xalign=0, wrap=True)
            lab.get_style_context().add_class("setting-label")
            keylab = Gtk.Label(label=key, xalign=0)
            keylab.get_style_context().add_class("key-name")
            keylab.set_selectable(True)
            texts.pack_start(lab, False, False, 0)
            texts.pack_start(keylab, False, False, 0)
            box.pack_start(texts, True, True, 0)

            if kind == "bool":
                w = Gtk.Switch(valign=Gtk.Align.CENTER)
                w.connect("notify::active", lambda sw, _p: self._changed(key))
            elif kind in ("int", "float"):
                lo, hi, step = opts[0], opts[1], opts[2]
                w = Gtk.SpinButton.new_with_range(lo, hi, step)
                w.set_digits(0 if kind == "int" else opts[3])
                w.set_valign(Gtk.Align.CENTER)
                w.set_width_chars(7)
                w.connect("value-changed", lambda *_: self._changed(key, debounce=True))
            elif kind == "choice":
                w = Gtk.ComboBoxText(valign=Gtk.Align.CENTER)
                if default is None:
                    w.append("", "MangoWM default")
                for opt in opts:
                    w.append(opt, opt)
                w.connect("changed", lambda *_: self._changed(key))
            elif kind == "cursor":
                w = Gtk.ComboBoxText.new_with_entry()
                w.set_valign(Gtk.Align.CENTER)
                for name in cursor_themes():
                    w.append_text(name)
                w.get_child().set_placeholder_text("system default")
                w.connect("changed", lambda *_: self._changed(key, debounce=True))
            else:
                w = Gtk.Entry(width_chars=22, valign=Gtk.Align.CENTER)
                w.set_placeholder_text("not set")
                w.connect("changed", lambda *_: self._changed(key, debounce=True))
            box.pack_start(w, False, False, 0)

            reset = Gtk.Button.new_from_icon_name("edit-undo-symbolic", Gtk.IconSize.BUTTON)
            reset.set_relief(Gtk.ReliefStyle.NONE)
            reset.set_valign(Gtk.Align.CENTER)
            reset.set_tooltip_text("Reset to MangoWM default (%s)" % (
                "unset" if default in (None, "") else default))
            reset.connect("clicked", lambda *_: self._reset(key))
            box.pack_start(reset, False, False, 0)
            row.add(box)
            self.items[key] = dict(item=item, widget=w, row=row, reset=reset,
                                   block=block, text=(key + " " + label).lower())
            return row

        # value <-> widget
        def _read_widget(self, key):
            info = self.items[key]
            _k, _l, kind, _d, opts = info["item"]
            w = info["widget"]
            if kind == "bool":
                return "1" if w.get_active() else "0"
            if kind in ("int", "float"):
                return format_number(w.get_value(), kind, 0 if kind == "int" else opts[3])
            if kind == "choice":
                return w.get_active_id()
            if kind == "cursor":
                return w.get_child().get_text().strip()
            return w.get_text().strip()

        def _write_widget(self, key, value):
            info = self.items[key]
            _k, _l, kind, default, opts = info["item"]
            w = info["widget"]
            if value is None:
                value = default
            if kind == "bool":
                w.set_active(parse_bool(value, default))
            elif kind in ("int", "float"):
                try:
                    num = float(value)
                except (TypeError, ValueError):
                    num = float(default or 0)
                w.set_value(num)
            elif kind == "choice":
                val = value or ""
                if not w.set_active_id(val):
                    if val:
                        w.append(val, "%s (unknown value)" % val)
                        w.set_active_id(val)
                    else:
                        w.set_active(-1)
            elif kind == "cursor":
                w.get_child().set_text(value or "")
            else:
                w.set_text(value or "")

        def refresh(self):
            values = self.app.tree.values()
            self._loading = True
            try:
                for key, info in self.items.items():
                    self._write_widget(key, values.get(key))
                    self._mark(key, key in values)
            finally:
                self._loading = False

        def _mark(self, key, explicit):
            info = self.items[key]
            ctx = info["row"].get_style_context()
            (ctx.add_class if explicit else ctx.remove_class)("explicit")
            info["reset"].set_sensitive(explicit)

        def _changed(self, key, debounce=False):
            if self._loading:
                return
            if debounce:
                if key not in self.debounced:
                    self.debounced[key] = Debouncer(500, self._save_key)
                    self.app.pending.append(self.debounced[key])
                self.debounced[key](key)
            else:
                self._save_key(key)

        def _save_key(self, key):
            value = self._read_widget(key)
            kind = self.items[key]["item"][2]
            try:
                if kind == "choice" and not value:
                    self.app.tree.unset(key)
                    self._mark(key, False)
                else:
                    path = self.app.tree.set(key, value)
                    self._mark(key, True)
                    self.app.saved(os.path.basename(path))
            except OSError as err:
                self.app.toast("Couldn't save %s: %s" % (key, err.strerror), error=True)

        def _reset(self, key):
            try:
                self.app.tree.unset(key)
            except OSError as err:
                self.app.toast("Couldn't reset %s: %s" % (key, err.strerror), error=True)
                return
            self._loading = True
            self._write_widget(key, None)
            self._loading = False
            self._mark(key, False)
            self.app.saved(None, "%s reset to default" % key)

        def filter(self, query):
            visible_blocks = set()
            for info in self.items.values():
                show = not query or query in info["text"] or query in self.title.lower()
                info["row"].set_visible(show)
                if show:
                    visible_blocks.add(info["block"])
            for info in self.items.values():
                info["block"].set_visible(info["block"] in visible_blocks)

    class RuleListPage(Page):
        """Editable table for repeated keys (bind, exec_once, monitor_rule …).

        Entries are collected from every file MangoWM loads, edited in place
        and written back to the file/line they came from. Nothing is written
        until you press Save.
        """
        title = "Rules"
        subtitle = None
        key_re = r"^$"
        key_choices = ()
        columns = (("Rule", True),)
        preferred_file = None
        add_label = "Add"

        # subclasses customise these
        def split(self, key, value):
            return [value]

        def join(self, key, cols):
            return cols[0]

        def new_entry(self):
            return self.key_choices[0], [""] * len(self.columns)

        def annotate(self, rows):
            return [""] * len(rows)

        def __init__(self, app):
            super().__init__(app, self.title, self.subtitle)
            self._key_re = re.compile(self.key_re)
            self.ncols = len(self.columns)
            # path, idx, key, cols..., warn, file-label, context
            types = [str, int, str] + [str] * self.ncols + [str, str, str]
            self.store = Gtk.ListStore(*types)
            self.c_warn = 3 + self.ncols
            self.c_file = self.c_warn + 1
            self.c_ctx = self.c_file + 1
            self.query = ""
            self.filtered = self.store.filter_new()
            self.filtered.set_visible_func(self._visible)
            self.dirty = False

            bar = Gtk.Box(spacing=6)
            self.search = Gtk.SearchEntry(placeholder_text="Filter…")
            self.search.connect("search-changed", self._on_filter)
            bar.pack_start(self.search, False, False, 0)
            for label, icon, cb in ((self.add_label, "list-add-symbolic", self.on_add),
                                    ("Delete", "list-remove-symbolic", self.on_delete)):
                b = Gtk.Button(label=label, image=Gtk.Image.new_from_icon_name(icon, Gtk.IconSize.BUTTON),
                               always_show_image=True)
                b.connect("clicked", cb)
                bar.pack_start(b, False, False, 0)
            self.extra_buttons(bar)
            self.pack_start(bar, False, False, 0)

            self.view = Gtk.TreeView(model=self.filtered, enable_search=False)
            self.view.set_grid_lines(Gtk.TreeViewGridLines.HORIZONTAL)
            if len(self.key_choices) > 1:
                kinds = Gtk.ListStore(str)
                for k in self.key_choices:
                    kinds.append([k])
                cell = Gtk.CellRendererCombo(model=kinds, text_column=0, has_entry=True,
                                             editable=True)
                cell.connect("edited", self._on_key_edited)
                col = Gtk.TreeViewColumn("Type", cell, text=2)
                col.set_resizable(True)
                self.view.append_column(col)
            for i, (title, expand) in enumerate(self.columns):
                cell = Gtk.CellRendererText(editable=True, ellipsize=Pango.EllipsizeMode.END)
                cell.connect("edited", self._on_cell_edited, 3 + i)
                col = Gtk.TreeViewColumn(title, cell, text=3 + i)
                col.set_resizable(True)
                col.set_expand(expand)
                col.set_min_width(140 if title == "Modifiers" else 80)
                self.view.append_column(col)
            self.ctx_col = Gtk.TreeViewColumn("Mode", Gtk.CellRendererText(), text=self.c_ctx)
            self.ctx_col.set_visible(False)
            self.view.append_column(self.ctx_col)
            fcell = Gtk.CellRendererText(foreground="gray")
            self.view.append_column(Gtk.TreeViewColumn("File", fcell, text=self.c_file))
            wcell = Gtk.CellRendererText(foreground="#c77c02", weight=700)
            self.view.append_column(Gtk.TreeViewColumn("", wcell, text=self.c_warn))

            sc = Gtk.ScrolledWindow(shadow_type=Gtk.ShadowType.IN, vexpand=True)
            sc.set_min_content_height(260)
            sc.add(self.view)
            self.pack_start(sc, True, True, 0)

            bottom = Gtk.Box(spacing=6)
            self.status = dim_label("", wrap=False)
            bottom.pack_start(self.status, True, True, 0)
            self.revert_btn = Gtk.Button(label="Revert")
            self.revert_btn.connect("clicked", lambda *_: self.refresh(force=True))
            self.save_btn = Gtk.Button(label="Save")
            self.save_btn.get_style_context().add_class("suggested-action")
            self.save_btn.connect("clicked", lambda *_: self.save())
            bottom.pack_end(self.save_btn, False, False, 0)
            bottom.pack_end(self.revert_btn, False, False, 0)
            self.pack_start(bottom, False, False, 0)
            self.after_build()
            self.refresh(force=True)

        def extra_buttons(self, bar):
            pass

        def after_build(self):
            pass

        # loading
        def refresh(self, force=False):
            if self.dirty and not force:
                return
            entries, files = self.app.tree.scan()
            self.store.clear()
            self.originals = {}
            self.stamps = {f: file_stamp(f) for f in files}
            self.files = files
            mode = "default"
            rows = []
            cache = {}
            for e in entries:
                if e.key == "key_mode":
                    mode = e.value or "default"
                    continue
                if not self._key_re.match(e.key):
                    continue
                if e.path not in cache:
                    cache[e.path] = read_lines(e.path)
                self.originals[(e.path, e.idx)] = cache[e.path][e.idx]
                rows.append([e.path, e.idx, e.key] + self._pad(self.split(e.key, e.value))
                            + ["", self._file_label(e.path), mode])
            for row in rows:
                self.store.append(row)
            self.ctx_col.set_visible(any(r[self.c_ctx] != "default" for r in rows))
            self._set_dirty(False)
            self._reannotate()

        def _pad(self, cols):
            cols = list(cols)[:self.ncols]
            return cols + [""] * (self.ncols - len(cols))

        def _file_label(self, path):
            if not path:
                return "(new)"
            try:
                rel = os.path.relpath(path, os.path.dirname(self.app.tree.root))
            except ValueError:
                rel = path
            return path if rel.startswith("..") else rel

        def _rows(self):
            return [list(r) for r in self.store]

        def _reannotate(self):
            rows = self._rows()
            notes = self.annotate(rows)
            for r, note in zip(self.store, notes):
                if r[self.c_warn] != note:
                    r[self.c_warn] = note
            n = len(self.store)
            warn = sum(1 for x in notes if x)
            text = "%d entr%s" % (n, "y" if n == 1 else "ies")
            if warn:
                text += " · %d warning%s" % (warn, "" if warn == 1 else "s")
            if self.dirty:
                text += " · unsaved changes"
            self.status.set_text(text)

        def _set_dirty(self, dirty):
            self.dirty = dirty
            self.save_btn.set_sensitive(dirty)
            self.revert_btn.set_sensitive(dirty)

        def is_dirty(self):
            return self.dirty

        def _changed(self):
            self._set_dirty(True)
            self._reannotate()

        # filtering
        def _visible(self, model, it, _data=None):
            if not self.query:
                return True
            row = model[it]
            return any(self.query in str(row[i]).lower()
                       for i in [2] + list(range(3, 3 + self.ncols)) + [self.c_file])

        def _on_filter(self, entry):
            self.query = entry.get_text().strip().lower()
            self.filtered.refilter()

        def _child_iter(self, path):
            it = self.filtered.get_iter(Gtk.TreePath(path))
            return self.filtered.convert_iter_to_child_iter(it)

        # editing
        def _on_cell_edited(self, _cell, path, text, col):
            it = self._child_iter(path)
            if self.store[it][col] != text:
                self.store[it][col] = text
                self._changed()

        def _on_key_edited(self, _cell, path, text):
            text = text.strip()
            if not self._key_re.match(text):
                self.app.toast("“%s” isn't a valid type here" % text, error=True)
                return
            it = self._child_iter(path)
            if self.store[it][2] != text:
                self.store[it][2] = text
                self._changed()

        def add_row(self, key, cols, start_editing=True):
            it = self.store.append(["", -1, key] + self._pad(cols)
                                   + ["", "(new)", "default"])
            self._changed()
            if start_editing:
                self.search.set_text("")
                ok, fit = self.filtered.convert_child_iter_to_iter(it)
                if ok:
                    path = self.filtered.get_path(fit)
                    first = self.view.get_column(1 if len(self.key_choices) > 1 else 0)
                    self.view.set_cursor(path, first, True)
            return it

        def on_add(self, _b):
            key, cols = self.new_entry()
            self.add_row(key, cols)

        def on_delete(self, _b):
            model, it = self.view.get_selection().get_selected()
            if it is None:
                self.app.toast("Select an entry to delete first")
                return
            self.store.remove(model.convert_iter_to_child_iter(it))
            self._changed()

        # saving
        def target_file(self):
            if self.preferred_file:
                pref = os.path.join(os.path.dirname(self.app.tree.root), self.preferred_file)
                if self.app.tree.is_sourced(pref):
                    return pref
            last = [r[0] for r in self.store if r[1] >= 0]
            return last[-1] if last else self.app.tree.root

        def save(self):
            if not self.dirty:
                return True
            changed = [f for f, st in self.stamps.items() if file_stamp(f) != st]
            if changed:
                dlg = Gtk.MessageDialog(
                    transient_for=self.get_toplevel(), modal=True,
                    message_type=Gtk.MessageType.WARNING, buttons=Gtk.ButtonsType.OK_CANCEL,
                    text="Config changed on disk")
                dlg.format_secondary_text(
                    "%s changed since this page was loaded. Reload it from disk? "
                    "Your unsaved edits on this page will be lost."
                    % ", ".join(os.path.basename(f) for f in changed))
                resp = dlg.run()
                dlg.destroy()
                if resp == Gtk.ResponseType.OK:
                    self.refresh(force=True)
                return False

            edits = {}  # path -> {idx: new_text or None}
            present = set()
            new_rows = []
            for r in self.store:
                path, idx, key = r[0], r[1], r[2]
                cols = [r[3 + i] for i in range(self.ncols)]
                if idx < 0:
                    new_rows.append("%s=%s" % (key, self.join(key, cols)))
                    continue
                present.add((path, idx))
                orig = self.originals[(path, idx)]
                text = format_line(key, self.join(key, cols), orig)
                if text != orig:
                    edits.setdefault(path, {})[idx] = text
            for (path, idx) in self.originals:
                if (path, idx) not in present:
                    edits.setdefault(path, {})[idx] = None

            target = self.target_file()
            try:
                for path in set(edits) | ({target} if new_rows else set()):
                    lines = read_lines(path)
                    insert_at = len(lines)
                    if path == target and new_rows:
                        hits = [i for i, l in enumerate(lines)
                                if split_line(l) and self._key_re.match(split_line(l)[0])]
                        insert_at = hits[-1] + 1 if hits else len(lines)
                    out = []
                    for i, line in enumerate(lines):
                        if i == insert_at and path == target:
                            out.extend(new_rows)
                        change = edits.get(path, {}).get(i, line)
                        if change is not None:
                            out.append(change)
                    if path == target and insert_at >= len(lines):
                        out.extend(new_rows)
                    write_lines(path, out)
            except OSError as err:
                self.app.toast("Couldn't save: %s" % err.strerror, error=True)
                return False
            self._set_dirty(False)
            self.refresh(force=True)
            self.app.saved(None, "%s saved" % self.title)
            return True

    class KeybindsPage(RuleListPage):
        title = "Keybindings"
        subtitle = ("Every <tt>bind</tt> line MangoWM loads, from all included files. "
                    "Double-click a cell to edit. Modifiers: SUPER, CTRL, ALT, SHIFT or NONE "
                    "(combine with +). Key names come from <tt>wev</tt>.")
        key_re = r"^bind[slrpc]*$"
        key_choices = ("bind", "binds", "bindl", "bindr", "bindp", "bindsl")
        columns = (("Modifiers", False), ("Key", False), ("Action", True))
        preferred_file = "bind.conf"
        keywords = ("shortcuts", "hotkeys", "bind", "keys")
        add_label = "Add shortcut"

        def split(self, key, value):
            parts = [p.strip() for p in value.split(",", 2)]
            return parts + [""] * (3 - len(parts))

        def join(self, key, cols):
            return "%s,%s,%s" % (cols[0].strip(), cols[1].strip(), cols[2].strip())

        def new_entry(self):
            return "bind", ["SUPER", "", "spawn,foot"]

        def annotate(self, rows):
            seen, notes = {}, []
            for r in rows:
                mods, key = r[3], r[4]
                if not key.strip():
                    notes.append("⚠ no key")
                    continue
                if not r[5].strip():
                    notes.append("⚠ no action")
                    continue
                combo = (r[self.c_ctx], normalize_mods(mods), key.strip().lower(),
                         "r" in r[2][4:])
                notes.append("")
                if "c" not in r[2][4:]:
                    seen.setdefault(combo, []).append(len(notes) - 1)
            for idxs in seen.values():
                if len(idxs) > 1:
                    for i in idxs:
                        notes[i] = "⚠ conflict"
            return notes

    class StartupPage(RuleListPage):
        title = "Startup apps"
        subtitle = ("<tt>exec_once</tt> runs when MangoWM starts; <tt>exec</tt> runs on every "
                    "config reload. Commands go through the shell.")
        key_re = r"^exec(_once)?$"
        key_choices = ("exec_once", "exec")
        columns = (("Command", True),)
        keywords = ("autostart", "exec", "login", "waybar", "wallpaper")
        add_label = "Add app"

        def new_entry(self):
            return "exec_once", [""]

        def after_build(self):
            self.script_bar = Gtk.InfoBar(message_type=Gtk.MessageType.INFO, show_close_button=True)
            self.script_bar.connect("response", self._on_script_bar)
            self.script_bar.get_content_area().add(Gtk.Label(
                label="autostart.sh exists but MangoWM doesn't run it. "
                      "MangoWM has no built-in autostart script; it needs an exec_once line.",
                wrap=True, xalign=0))
            self.script_bar.add_button("Run it at startup", 1)
            self.script_bar.add_button("Edit script", 2)
            self.script_bar.set_no_show_all(True)
            self.pack_start(self.script_bar, False, False, 0)
            self.reorder_child(self.script_bar, 2)

        def _script(self):
            return os.path.join(os.path.dirname(self.app.tree.root), "autostart.sh")

        def refresh(self, force=False):
            super().refresh(force)
            if not hasattr(self, "script_bar"):
                return
            script = self._script()
            used = any("autostart.sh" in r[3] for r in self.store)
            if os.path.isfile(script) and not used:
                self.script_bar.show()
                for child in self.script_bar.get_content_area().get_children():
                    child.show()
            else:
                self.script_bar.hide()

        def _on_script_bar(self, bar, resp):
            if resp == 1:
                script = self._script()
                try:
                    os.chmod(script, os.stat(script).st_mode | 0o111)
                except OSError:
                    pass
                self.add_row("exec_once", [script.replace(HOME, "~", 1)], start_editing=False)
                bar.hide()
            elif resp == 2:
                open_path(self._script(), self.app)
            else:
                bar.hide()

    class MonitorsPage(RuleListPage):
        title = "Monitors"
        subtitle = ("One <tt>monitor_rule</tt> per display, written as <tt>parameter:value</tt> "
                    "pairs, e.g. <tt>name:^DP-1$,width:2560,height:1440,refresh:144,x:0,y:0,scale:1</tt>. "
                    "Other parameters: make, model, vrr, rr (rotation 0-7), disable, primary.")
        key_re = r"^monitor_rule$"
        key_choices = ("monitor_rule",)
        columns = (("Rule", True),)
        preferred_file = "monitor.conf"
        keywords = ("display", "screen", "resolution", "refresh", "scale", "output")
        add_label = "Add rule"

        def new_entry(self):
            return "monitor_rule", ["name:^eDP-1$,width:1920,height:1080,refresh:60,x:0,y:0,scale:1"]

        def extra_buttons(self, bar):
            b = Gtk.Button(label="Add connected displays")
            b.set_tooltip_text("Create rules for the monitors MangoWM currently drives (uses mmsg)")
            b.connect("clicked", self.on_detect)
            bar.pack_start(b, False, False, 0)

        def on_detect(self, _b):
            mons = mango_monitors()
            if not mons:
                self.app.toast("No monitors reported — is MangoWM running and mmsg installed?",
                               error=True)
                return
            refresh = {}
            display = Gdk.Display.get_default()
            for i in range(display.get_n_monitors() if display else 0):
                m = display.get_monitor(i)
                g = m.get_geometry()
                refresh[(g.x, g.y)] = m.get_refresh_rate() / 1000.0
            existing = " ".join(r[3] for r in self.store)
            added = 0
            for m in mons:
                name = m.get("name")
                if not name or ("^%s$" % name) in existing:
                    continue
                scale = float(m.get("scale") or 1)
                parts = ["name:^%s$" % name,
                         "width:%d" % round(m.get("width", 0) * scale),
                         "height:%d" % round(m.get("height", 0) * scale)]
                hz = refresh.get((m.get("x"), m.get("y")))
                if hz:
                    parts.append("refresh:%g" % round(hz, 3))
                parts += ["x:%d" % m.get("x", 0), "y:%d" % m.get("y", 0),
                          "scale:%g" % scale]
                self.add_row("monitor_rule", [",".join(parts)], start_editing=False)
                added += 1
            self.app.toast("Added %d monitor rule%s — review and Save" % (added, "" if added == 1 else "s")
                           if added else "All connected monitors already have rules")

    class WorkspacesPage(RuleListPage):
        title = "Workspaces"
        subtitle = ("Per-tag rules, e.g. <tt>id:1,layout_name:scroller</tt>. Layouts: tile, scroller, "
                    "grid, deck, monocle, center_tile, vertical_tile, vertical_scroller. Other "
                    "parameters: monitor_name, master_count, master_factor, no_hide, open_as_floating.")
        key_re = r"^tag_rule$"
        key_choices = ("tag_rule",)
        columns = (("Rule", True),)
        preferred_file = "tag.conf"
        keywords = ("tags", "tag_rule", "layout")
        add_label = "Add rule"

        def new_entry(self):
            ids = []
            for r in self.store:
                m = re.search(r"(?:^|,)id:(\d+)", r[3])
                if m:
                    ids.append(int(m.group(1)))
            return "tag_rule", ["id:%d,layout_name:tile" % (max(ids) + 1 if ids else 1)]

    class WindowRulesPage(RuleListPage):
        title = "Window rules"
        subtitle = ("Match windows by <tt>app_id</tt> or <tt>title</tt> (regex) and set properties, "
                    "e.g. <tt>is_floating:1,width:1000,height:700,app_id:pavucontrol</tt>. "
                    "<tt>window_rule_once</tt> applies only when the window opens.")
        key_re = r"^window_rule(_once)?$"
        key_choices = ("window_rule", "window_rule_once")
        columns = (("Rule", True),)
        keywords = ("floating", "app_id", "rules", "window_rule")
        add_label = "Add rule"

        def new_entry(self):
            return "window_rule", ["is_floating:1,app_id:"]

    class ConfigFilesPage(Page):
        """Plain-text editor for any file in the config tree."""
        keywords = ("advanced", "edit", "text", "source", "include", "backup", "conf")

        def __init__(self, app):
            super().__init__(app, "Config files",
                             "Edit your config files directly. MangoWM loads them in this order.")
            self.path = None
            bar = Gtk.Box(spacing=6)
            self.combo = Gtk.ComboBoxText()
            self.combo.connect("changed", self._on_pick)
            bar.pack_start(self.combo, True, True, 0)
            for label, cb in (("Open in editor", self._open_external),
                              ("Backups", lambda *_: self._open_backups())):
                b = Gtk.Button(label=label)
                b.connect("clicked", cb)
                bar.pack_start(b, False, False, 0)
            self.pack_start(bar, False, False, 0)

            self.orphan_bar = Gtk.InfoBar(message_type=Gtk.MessageType.WARNING)
            self.orphan_label = Gtk.Label(wrap=True, xalign=0)
            self.orphan_bar.get_content_area().add(self.orphan_label)
            self.orphan_bar.add_button("Include it", 1)
            self.orphan_bar.connect("response", self._include)
            self.orphan_bar.set_no_show_all(True)
            self.pack_start(self.orphan_bar, False, False, 0)

            sc = Gtk.ScrolledWindow(shadow_type=Gtk.ShadowType.IN, vexpand=True, hexpand=True)
            sc.set_min_content_height(300)
            self.buf = Gtk.TextBuffer()
            self.buf.connect("modified-changed", lambda *_: self._update_buttons())
            tv = Gtk.TextView(buffer=self.buf, monospace=True, wrap_mode=Gtk.WrapMode.NONE,
                              left_margin=8, top_margin=6)
            sc.add(tv)
            self.pack_start(sc, True, True, 0)
            bottom = Gtk.Box(spacing=6)
            self.info = dim_label("", wrap=False)
            bottom.pack_start(self.info, True, True, 0)
            self.save_btn = Gtk.Button(label="Save")
            self.save_btn.get_style_context().add_class("suggested-action")
            self.save_btn.connect("clicked", lambda *_: self.save())
            bottom.pack_end(self.save_btn, False, False, 0)
            self.pack_start(bottom, False, False, 0)
            self.refresh()

        def _choices(self):
            files = self.app.tree.files() or [self.app.tree.root]
            folder = os.path.dirname(self.app.tree.root)
            orphans = []
            try:
                for name in sorted(os.listdir(folder)):
                    p = os.path.join(folder, name)
                    if name.endswith(".conf") and os.path.isfile(p) and \
                            not any(os.path.realpath(p) == os.path.realpath(f) for f in files):
                        orphans.append(p)
            except OSError:
                pass
            return files, orphans

        def refresh(self, force=False):
            if self.is_dirty() and not force:
                return
            files, orphans = self._choices()
            self._orphans = set(orphans)
            current = self.path
            self.combo.handler_block_by_func(self._on_pick)
            self.combo.remove_all()
            for p in files:
                self.combo.append(p, self._label(p))
            for p in orphans:
                self.combo.append(p, "%s  (not loaded by MangoWM)" % self._label(p))
            self.combo.handler_unblock_by_func(self._on_pick)
            if not self.combo.set_active_id(current or "") and (files or orphans):
                self.combo.set_active(0)
            else:
                self._load(self.combo.get_active_id())

        def _label(self, p):
            return p.replace(HOME, "~", 1)

        def _on_pick(self, combo):
            pid = combo.get_active_id()
            if pid == self.path:
                return
            if self.is_dirty():
                dlg = Gtk.MessageDialog(transient_for=self.get_toplevel(), modal=True,
                                        message_type=Gtk.MessageType.QUESTION,
                                        buttons=Gtk.ButtonsType.YES_NO,
                                        text="Discard unsaved changes to %s?" % os.path.basename(self.path))
                resp = dlg.run()
                dlg.destroy()
                if resp != Gtk.ResponseType.YES:
                    combo.handler_block_by_func(self._on_pick)
                    combo.set_active_id(self.path)
                    combo.handler_unblock_by_func(self._on_pick)
                    return
            self._load(pid)

        def _load(self, path):
            self.path = path
            self.buf.set_text(read_first(path) if path else "")
            self.buf.set_modified(False)
            self.stamp = file_stamp(path) if path else None
            if path in getattr(self, "_orphans", ()):
                self.orphan_label.set_text(
                    "%s isn't included from your main config, so MangoWM ignores it."
                    % os.path.basename(path))
                self.orphan_bar.show()
                for c in self.orphan_bar.get_content_area().get_children():
                    c.show()
            else:
                self.orphan_bar.hide()
            self._update_buttons()

        def _update_buttons(self):
            self.save_btn.set_sensitive(bool(self.path) and self.buf.get_modified())
            self.info.set_text(("Unsaved changes · " if self.buf.get_modified() else "")
                               + (self._label(self.path) if self.path else ""))

        def is_dirty(self):
            return bool(self.path) and self.buf.get_modified()

        def save(self):
            if not self.is_dirty():
                return True
            if file_stamp(self.path) != self.stamp:
                dlg = Gtk.MessageDialog(transient_for=self.get_toplevel(), modal=True,
                                        message_type=Gtk.MessageType.WARNING,
                                        buttons=Gtk.ButtonsType.OK_CANCEL,
                                        text="%s changed on disk" % os.path.basename(self.path))
                dlg.format_secondary_text("Overwrite it with your version?")
                resp = dlg.run()
                dlg.destroy()
                if resp != Gtk.ResponseType.OK:
                    return False
            start, end = self.buf.get_bounds()
            try:
                write_text(self.path, self.buf.get_text(start, end, False))
            except OSError as err:
                self.app.toast("Couldn't save: %s" % err.strerror, error=True)
                return False
            self.buf.set_modified(False)
            self.stamp = file_stamp(self.path)
            self.app.saved(os.path.basename(self.path))
            return True

        def _include(self, _bar, _resp):
            try:
                self.app.tree.add_source(self.path)
            except OSError as err:
                self.app.toast("Couldn't update config: %s" % err.strerror, error=True)
                return
            self.app.saved(None, "%s is now included" % os.path.basename(self.path))
            self.refresh(force=True)

        def _open_external(self, _b):
            if self.path:
                open_path(self.path, self.app)

        def _open_backups(self):
            folder = backup_dir()
            os.makedirs(folder, exist_ok=True)
            open_path(folder, self.app)

    class ThemingPage(Page):
        edits_mango_config = False
        keywords = ("gtk", "theme", "icons", "font", "dark", "color scheme", "appearance")

        def __init__(self, app):
            super().__init__(app, "Theming",
                             "GTK theme, icons and fonts for applications (stored in GSettings).")
            res = interface_settings()
            if res is None:
                self.pack_start(dim_label(
                    "GSettings schemas for org.gnome.desktop.interface aren't installed. "
                    "Install gsettings-desktop-schemas to use this page."), False, False, 0)
                return
            self.settings, schema = res
            lb = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
            lb.get_style_context().add_class("card")
            self._busy = True
            self.theme = self._combo(lb, "Application theme", gtk_themes(), "gtk-theme")
            self.icons = self._combo(lb, "Icon theme", icon_themes(), "icon-theme")
            self.cursor = self._combo(lb, "Cursor theme (applications)", cursor_themes(), "cursor-theme")
            if schema.has_key("color-scheme"):
                self.scheme = self._combo(lb, "Color scheme", ["default", "prefer-dark", "prefer-light"],
                                          "color-scheme")
            font = Gtk.FontButton(font=self.settings.get_string("font-name"), valign=Gtk.Align.CENTER)
            font.connect("font-set", lambda b: self._set("font-name", b.get_font()))
            self._row(lb, "Interface font", font)
            self._busy = False
            self.pack_start(lb, False, False, 0)
            self.pack_start(dim_label(
                "Tip: set the MangoWM cursor on the Input devices page too, so it matches "
                "everywhere. Wallpapers are usually started from Startup apps (e.g. swaybg)."),
                False, False, 0)

        def _row(self, lb, label, widget):
            row = Gtk.ListBoxRow(activatable=False)
            box = Gtk.Box(spacing=12)
            box.pack_start(Gtk.Label(label=label, xalign=0), True, True, 0)
            box.pack_start(widget, False, False, 0)
            row.add(box)
            lb.add(row)

        def _combo(self, lb, label, values, key):
            combo = Gtk.ComboBoxText(valign=Gtk.Align.CENTER)
            current = self.settings.get_string(key)
            if current and current not in values:
                values = [current] + list(values)
            for v in values:
                combo.append(v, v)
            combo.set_active_id(current)
            combo.connect("changed", lambda c: self._set(key, c.get_active_id()))
            self._row(lb, label, combo)
            return combo

        def _set(self, key, value):
            if self._busy or not value:
                return
            self.settings.set_string(key, value)
            Gio.Settings.sync()
            self.app.toast("%s set to %s" % (key, value))

    NOTIFIERS = (
        ("SwayNotificationCenter", "swaync", ["~/.config/swaync/config.json", "~/.config/swaync/style.css"],
         [["swaync-client", "--reload-config"], ["swaync-client", "--reload-css"]]),
        ("mako", "mako", ["~/.config/mako/config"], [["makoctl", "reload"]]),
        ("dunst", "dunst", ["~/.config/dunst/dunstrc"], [["dunstctl", "reload"]]),
        ("fnott", "fnott", ["~/.config/fnott/fnott.ini"], []),
    )

    class NotificationsPage(Page):
        edits_mango_config = False
        keywords = ("swaync", "mako", "dunst", "notification daemon")

        def __init__(self, app):
            super().__init__(app, "Notifications",
                             "Detected notification daemons. Start yours from Startup apps.")
            found = False
            for name, binary, configs, reloads in NOTIFIERS:
                configs = [os.path.expanduser(c) for c in configs]
                installed = shutil.which(binary) is not None
                existing = [c for c in configs if os.path.exists(c)]
                if not installed and not existing:
                    continue
                found = True
                lb = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
                lb.get_style_context().add_class("card")
                row = Gtk.ListBoxRow(activatable=False)
                box = Gtk.Box(spacing=8)
                texts = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
                texts.pack_start(Gtk.Label(label=name, xalign=0), False, False, 0)
                status = ("installed" if installed else "not installed") + \
                    (" · config: %s" % ", ".join(c.replace(HOME, "~") for c in existing)
                     if existing else " · no config file")
                texts.pack_start(dim_label(status), False, False, 0)
                box.pack_start(texts, True, True, 0)
                for cfg in existing:
                    b = Gtk.Button(label="Edit %s" % os.path.basename(cfg), valign=Gtk.Align.CENTER)
                    b.connect("clicked", lambda _b, c=cfg: open_path(c, app))
                    box.pack_start(b, False, False, 0)
                if reloads and installed:
                    b = Gtk.Button(label="Reload", valign=Gtk.Align.CENTER)
                    b.connect("clicked", lambda _b, cmds=reloads, n=name: self._reload(cmds, n))
                    box.pack_start(b, False, False, 0)
                row.add(box)
                lb.add(row)
                self.pack_start(lb, False, False, 0)
            if not found:
                self.pack_start(dim_label(
                    "No notification daemon found. Install one (swaync, mako or dunst) and "
                    "add it to Startup apps, e.g. exec_once=swaync."), False, False, 0)
            test = Gtk.Button(label="Send a test notification", halign=Gtk.Align.START)
            test.connect("clicked", lambda *_: self._test())
            self.pack_start(test, False, False, 0)

        def _reload(self, cmds, name):
            def done(ok, out):
                self.app.toast("%s reloaded" % name if ok else "Reload failed: %s" % out, error=not ok)
            for cmd in cmds:
                run_async(cmd, done)

        def _test(self):
            def finished(conn, res):
                try:
                    conn.call_finish(res)
                    self.app.toast("Test notification sent")
                except GLib.Error as err:
                    self.app.toast("No notification daemon answered: %s" % err.message, error=True)
            try:
                bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
            except GLib.Error as err:
                self.app.toast("No session bus: %s" % err.message, error=True)
                return
            bus.call("org.freedesktop.Notifications", "/org/freedesktop/Notifications",
                     "org.freedesktop.Notifications", "Notify",
                     GLib.Variant("(susssasa{sv}i)", (
                         "Mango Settings", 0, "preferences-desktop", "Test notification",
                         "If you can read this, notifications work.", [], {}, 5000)),
                     None, Gio.DBusCallFlags.NONE, 3000, None, finished)

    class SystemInfoPage(Page):
        edits_mango_config = False
        keywords = ("about", "version", "kernel", "memory", "uptime")

        def __init__(self, app):
            super().__init__(app, "System info")
            welcome = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
            welcome.get_style_context().add_class("welcome")
            wlab = Gtk.Label(xalign=0, wrap=True)
            wlab.set_markup("<big><b>Welcome to Mango Settings %s</b></big>\n"
                            "Manage your MangoWM setup: appearance, keybindings, monitors "
                            "and more. Settings are written straight into your config files, "
                            "and MangoWM reloads them automatically." % __version__)
            welcome.pack_start(wlab, False, False, 0)
            self.pack_start(welcome, False, False, 0)
            self.grid = Gtk.Grid(column_spacing=24, row_spacing=8)
            self.pack_start(self.grid, False, False, 0)
            bar = Gtk.Box(spacing=6)
            for label, cb in (("Refresh", lambda *_: self.refresh()),
                              ("Copy to clipboard", lambda *_: self._copy())):
                b = Gtk.Button(label=label)
                b.connect("clicked", cb)
                bar.pack_start(b, False, False, 0)
            self.pack_start(bar, False, False, 0)
            self._rows = []
            self.refresh()

        def info(self):
            session = os.environ.get("XDG_SESSION_TYPE", "unknown")
            if os.environ.get("WAYLAND_DISPLAY"):
                session += " (%s)" % os.environ["WAYLAND_DISPLAY"]
            return [
                ("MangoWM", mango_version()),
                ("Operating system", os_name()),
                ("Kernel", os.uname().release),
                ("Desktop", os.environ.get("XDG_CURRENT_DESKTOP", "unknown")),
                ("Session", session),
                ("Processor", cpu_name()),
                ("Memory", memory_usage()),
                ("Uptime", format_uptime()),
                ("Config", self.app.tree.root.replace(HOME, "~", 1)),
                ("Mango Settings", __version__),
            ]

        def refresh(self, force=False):
            for child in self.grid.get_children():
                self.grid.remove(child)
            self._rows = self.info()
            for i, (name, value) in enumerate(self._rows):
                lab = Gtk.Label(xalign=0)
                lab.set_markup("<b>%s</b>" % GLib.markup_escape_text(name))
                val = Gtk.Label(label=value, xalign=0, selectable=True, wrap=True)
                self.grid.attach(lab, 0, i, 1, 1)
                self.grid.attach(val, 1, i, 1, 1)
            self.grid.show_all()

        def _copy(self):
            text = "\n".join("%s: %s" % r for r in self._rows)
            Gtk.Clipboard.get(Gdk.SELECTION_CLIPBOARD).set_text(text, -1)
            self.app.toast("System info copied")

    # -- window ------------------------------------------------------------

    PAGES = [
        # name, icon, factory
        ("Appearance", "preferences-desktop-appearance-symbolic", None),
        ("Animations", "media-playback-start-symbolic", None),
        ("Window behavior", "focus-windows-symbolic", None),
        ("Layouts", "view-grid-symbolic", None),
        ("Keybindings", "input-keyboard-symbolic", KeybindsPage),
        ("Workspaces", "view-paged-symbolic", WorkspacesPage),
        ("Window rules", "window-new-symbolic", WindowRulesPage),
        ("Monitors", "preferences-desktop-display-symbolic", MonitorsPage),
        ("Input devices", "input-touchpad-symbolic", None),
        ("Startup apps", "system-run-symbolic", StartupPage),
        ("Theming", "preferences-color-symbolic", ThemingPage),
        ("Notifications", "preferences-system-notifications-symbolic", NotificationsPage),
        ("Config files", "document-edit-symbolic", ConfigFilesPage),
        ("System info", "dialog-information-symbolic", SystemInfoPage),
    ]

    class Prefs:
        """Small INI file for the app's own preferences."""

        def __init__(self):
            self.path = os.path.join(GLib.get_user_config_dir(), "mango-settings", "settings.ini")
            self.kf = GLib.KeyFile()
            try:
                self.kf.load_from_file(self.path, GLib.KeyFileFlags.NONE)
            except GLib.Error:
                pass

        def get(self, key, default):
            try:
                if isinstance(default, int):
                    return self.kf.get_integer("app", key)
                return self.kf.get_string("app", key)
            except GLib.Error:
                return default

        def set(self, key, value):
            if isinstance(value, int):
                self.kf.set_integer("app", key, value)
            else:
                self.kf.set_string("app", key, value)

        def save(self):
            try:
                os.makedirs(os.path.dirname(self.path), exist_ok=True)
                self.kf.save_to_file(self.path)
            except (GLib.Error, OSError):
                pass

    class MainWindow(Gtk.ApplicationWindow):

        def __init__(self, application, tree, kind, start_page=None):
            super().__init__(application=application, title="Mango Settings")
            self.tree, self.kind = tree, kind
            self.prefs = Prefs()
            self.pending = []
            self._toast_src = None
            self.set_default_size(self.prefs.get("width", 1040), self.prefs.get("height", 720))
            theme = Gtk.IconTheme.get_default()
            self.set_icon_name(APP_ID if theme.has_icon(APP_ID) else "preferences-desktop")

            provider = Gtk.CssProvider()
            provider.load_from_data(APP_CSS)
            Gtk.StyleContext.add_provider_for_screen(
                Gdk.Screen.get_default(), provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)

            self.iface = interface_settings()
            self._build_header()
            self._build_body()
            self.apply_theme(self.prefs.get("theme", "system"))
            self.connect("delete-event", self._on_close)
            self._install_shortcuts()
            self.show_all()
            for bar in self.bars:
                if not bar.revealed:
                    bar.hide()
            self.select_page(start_page or self.prefs.get("page", "Appearance"))

        # header
        def _build_header(self):
            version = mango_version()
            hb = Gtk.HeaderBar(show_close_button=True, title="Mango Settings",
                               subtitle="MangoWM %s" % version if version != "not found"
                               else "Settings for MangoWM")
            hb.get_style_context().add_class("mango")
            self.set_titlebar(hb)

            reload_btn = Gtk.Button(label="Reload MangoWM")
            reload_btn.set_tooltip_text("Apply the config now (Ctrl+R)")
            reload_btn.connect("clicked", lambda *_: self.reload_mango())
            hb.pack_end(reload_btn)

            menu_btn = Gtk.MenuButton(image=Gtk.Image.new_from_icon_name(
                "open-menu-symbolic", Gtk.IconSize.BUTTON))
            menu_btn.set_tooltip_text("Menu")
            pop = Gtk.Popover()
            pbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2, margin=8)
            pbox.pack_start(dim_label("Window style", wrap=False), False, False, 2)
            group = None
            self.theme_radios = {}
            for label, val in (("Follow system", "system"), ("Light", "light"), ("Dark", "dark")):
                rb = Gtk.RadioButton.new_with_label_from_widget(group, label)
                group = group or rb
                rb.connect("toggled", self._on_theme_radio, val)
                self.theme_radios[val] = rb
                pbox.pack_start(rb, False, False, 0)
            pbox.pack_start(Gtk.Separator(margin_top=6, margin_bottom=6), False, False, 0)
            for label, cb in (("Open config folder", lambda: open_path(os.path.dirname(self.tree.root), self)),
                              ("MangoWM documentation", lambda: open_path_uri(DOCS_URL)),
                              ("Keyboard shortcuts", self._show_shortcuts),
                              ("About Mango Settings", self._about)):
                b = Gtk.ModelButton(text=label)
                b.connect("clicked", lambda _b, f=cb: f())
                pbox.pack_start(b, False, False, 0)
            pbox.show_all()
            pop.add(pbox)
            menu_btn.set_popover(pop)
            hb.pack_end(menu_btn)

        # body
        def _build_body(self):
            outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
            self.bars = []
            self._add_banners(outer)

            paned = Gtk.Paned(orientation=Gtk.Orientation.HORIZONTAL)
            sidebar = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
            sidebar.get_style_context().add_class("sidebar")
            sidebar.set_size_request(220, -1)
            self.search = Gtk.SearchEntry(placeholder_text="Search settings", margin=8)
            self.search.connect("search-changed", self.on_search)
            sidebar.pack_start(self.search, False, False, 0)

            self.stack = Gtk.Stack(transition_type=Gtk.StackTransitionType.CROSSFADE,
                                   transition_duration=120)
            self.stack.connect("notify::visible-child", self._on_page_shown)
            self.listbox = Gtk.ListBox(selection_mode=Gtk.SelectionMode.SINGLE)
            self.listbox.connect("row-selected", self._on_row_selected)
            self.listbox.set_filter_func(self._row_visible)
            self.pages, self.rows = {}, {}
            for name, icon, factory in PAGES:
                page = factory(self) if factory else SettingsPage(self, name, CONFIG_SCHEMA[name])
                if self.kind == "toml" and page.edits_mango_config:
                    page.set_sensitive(False)
                self.pages[name] = page
                sc = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER)
                sc.add(page)
                self.stack.add_named(sc, name)
                row = Gtk.ListBoxRow()
                row.page_name = name
                hbox = Gtk.Box(spacing=10, margin=6)
                hbox.pack_start(Gtk.Image.new_from_icon_name(icon, Gtk.IconSize.MENU), False, False, 0)
                hbox.pack_start(Gtk.Label(label=name, xalign=0), True, True, 0)
                row.add(hbox)
                self.listbox.add(row)
                self.rows[name] = row
            lb_sc = Gtk.ScrolledWindow(vexpand=True, hscrollbar_policy=Gtk.PolicyType.NEVER)
            lb_sc.add(self.listbox)
            sidebar.pack_start(lb_sc, True, True, 0)
            paned.pack1(sidebar, False, False)
            paned.pack2(self.stack, True, False)

            overlay = Gtk.Overlay()
            overlay.add(paned)
            self.toast_rev = Gtk.Revealer(transition_type=Gtk.RevealerTransitionType.SLIDE_UP,
                                          halign=Gtk.Align.CENTER, valign=Gtk.Align.END)
            self.toast_label = Gtk.Label(wrap=True, max_width_chars=70)
            self.toast_label.get_style_context().add_class("toast")
            self.toast_rev.add(self.toast_label)
            overlay.add_overlay(self.toast_rev)
            overlay.set_overlay_pass_through(self.toast_rev, True)
            outer.pack_start(overlay, True, True, 0)
            self.add(outer)

        def _banner(self, outer, kind, text, buttons=(), on_response=None, show=True):
            bar = Gtk.InfoBar(message_type=kind, show_close_button=True)
            bar.get_content_area().add(Gtk.Label(label=text, wrap=True, xalign=0))
            for label, rid in buttons:
                bar.add_button(label, rid)
            bar.connect("response", on_response or (lambda b, _r: b.hide()))
            bar.revealed = show
            outer.pack_start(bar, False, False, 0)
            self.bars.append(bar)
            return bar

        def _add_banners(self, outer):
            root = self.tree.root.replace(HOME, "~", 1)
            if self.kind == "toml":
                self._banner(outer, Gtk.MessageType.WARNING,
                             "Your MangoWM config is in TOML format (%s). Mango Settings edits the "
                             ".conf format only, so config pages are read-only to keep your setup "
                             "safe. Theming, notifications and system info still work." % root)
            elif self.kind == "new":
                system = system_default_config()
                text = "No MangoWM config found yet. Your settings will be saved to %s." % root
                buttons = []
                if system:
                    text += " You can start from the system default config (%s)." % system
                    buttons = [("Copy default config", 1)]

                def respond(bar, rid):
                    if rid == 1 and system:
                        try:
                            write_text(self.tree.root, read_first(system))
                        except OSError as err:
                            self.toast("Couldn't copy: %s" % err.strerror, error=True)
                            return
                        self.kind = "conf"
                        self.refresh_all(force=True)
                        self.toast("Copied %s" % system)
                    bar.hide()
                self._banner(outer, Gtk.MessageType.INFO, text, buttons, respond)

        # pages
        def _row_visible(self, row):
            q = self.search.get_text().strip().lower()
            return not q or self.pages[row.page_name].matches(q)

        def on_search(self, entry):
            q = entry.get_text().strip().lower()
            self.listbox.invalidate_filter()
            for page in self.pages.values():
                page.filter(q)
            sel = self.listbox.get_selected_row()
            if q and (sel is None or not self._row_visible(sel)):
                for name, _i, _f in PAGES:
                    if self._row_visible(self.rows[name]):
                        self.listbox.select_row(self.rows[name])
                        break

        def _on_row_selected(self, _lb, row):
            if row is not None:
                self.stack.set_visible_child_name(row.page_name)

        def _on_page_shown(self, *_):
            name = self.stack.get_visible_child_name()
            if name:
                self.pages[name].refresh()

        def select_page(self, name):
            match = next((n for n, _i, _f in PAGES if n.lower() == str(name).lower()), "Appearance")
            self.listbox.select_row(self.rows[match])

        def current_page(self):
            return self.pages.get(self.stack.get_visible_child_name())

        def refresh_all(self, force=False):
            for page in self.pages.values():
                if self.kind != "toml" or not page.edits_mango_config:
                    page.set_sensitive(True)
                page.refresh(force) if isinstance(page, RuleListPage) else page.refresh()

        # feedback
        def toast(self, msg, error=False):
            ctx = self.toast_label.get_style_context()
            (ctx.add_class if error else ctx.remove_class)("error")
            self.toast_label.set_text(msg)
            self.toast_rev.set_reveal_child(True)
            if self._toast_src:
                GLib.source_remove(self._toast_src)

            def hide():
                self._toast_src = None
                self.toast_rev.set_reveal_child(False)
                return False
            self._toast_src = GLib.timeout_add(4500 if error else 3000, hide)

        def saved(self, filename, msg=None):
            auto = parse_bool(self.tree.get("auto_reload_config", "1"), 1)
            msg = msg or "Saved to %s" % filename
            self.toast(msg + (" — MangoWM reloads it automatically" if auto
                              else " — press Reload MangoWM to apply"))

        def reload_mango(self):
            if not shutil.which("mmsg"):
                self.toast("mmsg not found — it ships with MangoWM. You can also press SUPER+R.",
                           error=True)
                return

            def done(ok, out):
                self.toast("MangoWM reloaded" if ok else "Reload failed: %s" % (out or "is MangoWM running?"),
                           error=not ok)
            run_async(["mmsg", "dispatch", "reload_config"], done)

        # theme
        def _on_theme_radio(self, rb, mode):
            if rb.get_active():
                self.apply_theme(mode)

        def apply_theme(self, mode):
            if mode not in ("system", "light", "dark"):
                mode = "system"
            self.theme_mode = mode
            rb = self.theme_radios[mode]
            if not rb.get_active():
                rb.set_active(True)
            self.prefs.set("theme", mode)
            settings = Gtk.Settings.get_default()
            sys_theme = self.iface[0].get_string("gtk-theme") if self.iface else "Adwaita"
            installed = gtk_themes()
            if sys_theme not in installed:
                sys_theme = "Adwaita"  # GTK falls back to this anyway
            base = re.sub(r"[-_]dark$", "", sys_theme, flags=re.I)
            if base not in installed:
                base = "Adwaita"
            if mode == "system":
                scheme = ""
                if self.iface and self.iface[1].has_key("color-scheme"):
                    scheme = self.iface[0].get_string("color-scheme")
                dark = scheme == "prefer-dark" or "dark" in sys_theme.lower()
                name = sys_theme
            elif mode == "dark":
                # "<theme>-dark" if it exists as its own theme, else the built-in
                # dark variant selected through prefer-dark (Adwaita has one).
                dark = True
                name = base + "-dark" if base + "-dark" in installed else base
            else:
                dark = False
                name = base
            settings.set_property("gtk-theme-name", name)
            settings.set_property("gtk-application-prefer-dark-theme", dark)
            if self.iface and not getattr(self, "_iface_watch", None):
                self._iface_watch = self.iface[0].connect(
                    "changed", lambda _s, key: key in ("gtk-theme", "color-scheme")
                    and self.theme_mode == "system" and self.apply_theme("system"))

        # shortcuts and dialogs
        def _install_shortcuts(self):
            accel = Gtk.AccelGroup()
            self.add_accel_group(accel)

            def bind(accel_str, fn):
                key, mods = Gtk.accelerator_parse(accel_str)
                accel.connect(key, mods, Gtk.AccelFlags.VISIBLE, lambda *_: (fn(), True)[1])
            bind("<Control>f", lambda: self.search.grab_focus())
            bind("<Control>r", self.reload_mango)
            bind("<Control>s", self._save_current)
            bind("<Control>q", lambda: self.close())

        def _save_current(self):
            page = self.current_page()
            if page is not None and hasattr(page, "save"):
                page.save()

        def _show_shortcuts(self):
            dlg = Gtk.MessageDialog(transient_for=self, modal=True, buttons=Gtk.ButtonsType.CLOSE,
                                    text="Keyboard shortcuts")
            dlg.format_secondary_markup(
                "<b>Ctrl+F</b>  search settings\n<b>Ctrl+S</b>  save the current page\n"
                "<b>Ctrl+R</b>  reload MangoWM\n<b>Ctrl+Q</b>  quit")
            dlg.run()
            dlg.destroy()

        def _about(self):
            dlg = Gtk.AboutDialog(transient_for=self, modal=True, program_name="Mango Settings",
                                  version=__version__, website=REPO_URL,
                                  comments="A settings manager for the MangoWM Wayland compositor.",
                                  license_type=Gtk.License.MIT_X11,
                                  authors=["Harikesh Verma (code-smith404)"],
                                  logo_icon_name=self.get_icon_name())
            dlg.run()
            dlg.destroy()

        def _on_close(self, *_):
            for d in self.pending:
                d.flush()
            dirty = [p for p in self.pages.values() if p.is_dirty()]
            if dirty:
                dlg = Gtk.MessageDialog(transient_for=self, modal=True,
                                        message_type=Gtk.MessageType.QUESTION,
                                        text="Save changes before closing?")
                dlg.format_secondary_text("Unsaved changes on: %s" % ", ".join(p.title for p in dirty))
                dlg.add_buttons("Discard", Gtk.ResponseType.NO, "Cancel", Gtk.ResponseType.CANCEL,
                                "Save", Gtk.ResponseType.YES)
                resp = dlg.run()
                dlg.destroy()
                if resp == Gtk.ResponseType.CANCEL or resp == Gtk.ResponseType.DELETE_EVENT:
                    return True
                if resp == Gtk.ResponseType.YES and not all(p.save() for p in dirty):
                    return True
            w, h = self.get_size()
            self.prefs.set("width", w)
            self.prefs.set("height", h)
            self.prefs.set("page", self.stack.get_visible_child_name() or "Appearance")
            self.prefs.save()
            return False

    def open_path_uri(uri):
        try:
            Gio.AppInfo.launch_default_for_uri(uri, None)
        except GLib.Error:
            if shutil.which("xdg-open"):
                subprocess.Popen(["xdg-open", uri])

    class MangoSettingsApp(Gtk.Application):
        def __init__(self, args):
            super().__init__(application_id=APP_ID, flags=Gio.ApplicationFlags.FLAGS_NONE)
            self.args = args
            self.window = None

        def do_activate(self):
            if self.window is None:
                root, kind = detect_root_config(self.args.config)
                self.window = MainWindow(self, ConfigTree(root), kind, self.args.page)
            self.window.present()


def main(argv=None):
    parser = argparse.ArgumentParser(prog="mango-settings",
                                     description="Settings manager for MangoWM.")
    parser.add_argument("--version", action="version", version="%(prog)s " + __version__)
    parser.add_argument("--page", help="open a specific page, e.g. Keybindings")
    parser.add_argument("--config", help="use another root config file (like `mango -c`)")
    parser.add_argument("--list-pages", action="store_true", help="print page names and exit")
    args = parser.parse_args(argv)
    if not HAVE_GTK:
        sys.stderr.write(
            "Mango Settings needs PyGObject with GTK 3 (%s).\n"
            "  Arch/Manjaro:  sudo pacman -S python-gobject gtk3\n"
            "  Debian/Ubuntu: sudo apt install python3-gi gir1.2-gtk-3.0\n"
            "  Fedora:        sudo dnf install python3-gobject gtk3\n" % GTK_ERROR)
        return 1
    if args.list_pages:
        print("\n".join(name for name, _i, _f in PAGES))
        return 0
    GLib.set_application_name("Mango Settings")
    GLib.set_prgname("mango-settings")
    return MangoSettingsApp(args).run([sys.argv[0]])


if __name__ == "__main__":
    sys.exit(main())
