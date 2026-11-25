#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Mango Settings (MangoWM-Settings) — a graphical settings manager for MangoWM.

Single-file GTK 3 + PyGObject application.  The config engine at the top of
this file is pure Python (no GTK imports) so it can be unit-tested headless.

  mango-settings [--page PAGE] [--config FILE] [--check] [--list-pages] [--version]

MIT licensed.  https://github.com/code-smith404/MangoWM-Settings
"""

import argparse
import datetime
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import time

VERSION = "2.3.0"
APP_ID = "io.github.code_smith404.MangoSettings"
APP_NAME = "Mango Settings"

DEFAULT_CONFIG_PATH = "~/.config/mango/config.conf"
DEFAULT_BIND_PATH = "~/.config/mango/bind.conf"
DEFAULT_RULE_PATH = "~/.config/mango/rule.conf"
DEFAULT_MONITOR_PATH = "~/.config/mango/monitor.conf"
DEFAULT_AUTOSTART_PATH = "~/.config/mango/autostart.sh"

CACHE_ROOT = "~/.cache/mango-settings"
BACKUP_ROOT = os.path.join(CACHE_ROOT, "backups")
APP_SETTINGS_FILE = os.path.join("~/.config", "mango-settings", "settings.ini")

MAX_INCLUDE_DEPTH = 32
MAX_VALUE_LENGTH = 255          # MangoWM refuses longer values
CHOICE_ANIMATIONS = ["none", "fade", "slide", "zoom"]
# Layout names accepted by MangoWM (see src/layout/arrange.c in mango).
CHOICE_LAYOUTS = ["tile", "scroller", "grid", "deck", "monocle", "center_tile",
                  "right_tile", "vertical_tile", "vertical_scroller",
                  "vertical_grid", "vertical_deck", "dwindle", "fair",
                  "vertical_fair"]
CHOICE_WALLPAPER_MODES = ["fill", "fit", "stretch", "tile", "center"]


# ---------------------------------------------------------------------------
# Errors and issues
# ---------------------------------------------------------------------------

class ConfigError(Exception):
    """Raised for anything the user must be told about in actionable words."""


class Issue(object):
    """A problem found while loading the config (shown by --check and GUI)."""

    ERROR = "error"
    WARNING = "warning"

    __slots__ = ("severity", "path", "lineno", "message")

    def __init__(self, severity, message, path=None, lineno=None):
        self.severity = severity
        self.message = message
        self.path = path
        self.lineno = lineno

    def __str__(self):
        where = ""
        if self.path is not None:
            where = self.path
            if self.lineno is not None:
                where += ":%d" % self.lineno
        return "%s: %s%s: %s" % (
            "error" if self.severity == Issue.ERROR else "warning",
            " (%s)" % where if where else "",
            "",  # kept for layout symmetry
            self.message,
        ).replace("  ", " ").replace(": :", ": ") if False else "%s%s%s: %s" % (
            "error" if self.severity == Issue.ERROR else "warning",
            " [%s]" % where if where else "",
            "", self.message,
        )


def _add_issue(issues, severity, message, path=None, lineno=None):
    issues.append(Issue(severity, message, path, lineno))


# ---------------------------------------------------------------------------
# Line model — every line of a config file is one of these, so comments,
# blank lines, ordering and unknown content survive a load/save round trip.
# ---------------------------------------------------------------------------

def _split_inline_comment(text):
    """Split ``key = value  # comment`` into (body, comment).  ``#`` inside
    double or single quotes does not start a comment."""
    in_single = in_double = False
    for i, ch in enumerate(text):
        if ch == "'" and not in_double:
            in_single = not in_single
        elif ch == '"' and not in_single:
            in_double = not in_double
        elif ch == "#" and not in_single and not in_double:
            return text[:i], text[i:]
    return text, ""


def _strip_quotes(token):
    token = token.strip()
    if len(token) >= 2 and token[0] == token[-1] and token[0] in "\"'":
        return token[1:-1]
    return token


class Line(object):
    raw = ""
    dirty = False

    def render(self):
        return self.raw

    def is_editable(self):
        return False


class BlankLine(Line):
    pass


class CommentLine(Line):
    pass


class UnknownLine(Line):
    """A line we do not understand.  It is kept verbatim so a save never
    deletes content the app did not recognise."""


class VarLine(Line):
    """``var = NAME value`` (also accepted: ``var = NAME=value``)."""

    def __init__(self, name, value, comment=""):
        self.name = name
        self.value = value
        self.comment = comment

    def render(self):
        out = "var = %s %s" % (self.name, self.value)
        if self.comment:
            out += "  " + self.comment
        return out

    def is_editable(self):
        return True


class OptionLine(Line):
    """``key = value`` — the bread and butter of config.conf."""

    def __init__(self, key, value, comment=""):
        self.key = key
        self.value = value
        self.comment = comment
        self.dirty = False

    def render(self):
        out = "%s = %s" % (self.key, self.value)
        if self.comment:
            out += "  " + self.comment
        return out

    def set_value(self, value):
        self.value = value
        self.dirty = True

    def is_editable(self):
        return True


class IncludeLine(Line):
    """``source = PATH`` / ``source_optional = PATH``."""

    def __init__(self, target, optional, comment=""):
        self.target = target
        self.optional = optional
        self.comment = comment

    def render(self):
        out = "%s = %s" % ("source_optional" if self.optional else "source", self.target)
        if self.comment:
            out += "  " + self.comment
        return out


# ---------------------------------------------------------------------------
# Parsing one file
# ---------------------------------------------------------------------------

_VAR_RE = re.compile(r"^var\s*=\s*([A-Za-z_][A-Za-z0-9_]*)[=\s](.*)$")
_OPT_RE = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)$")


def parse_lines(text, path=None, issues=None):
    """Parse the text of one config file into Line objects."""
    lines = []
    pieces = text.split("\n")
    if text.endswith("\n"):
        pieces.pop()          # the final newline is not a blank line
    for lineno, raw in enumerate(pieces, start=1):
        raw = raw.rstrip("\r")
        stripped = raw.strip()
        if not stripped:
            lines.append(BlankLine()); lines[-1].raw = raw
            continue
        if stripped.startswith("#"):
            c = CommentLine(); c.raw = raw; lines.append(c); continue
        body, comment = _split_inline_comment(stripped)
        body = body.strip()
        m = _VAR_RE.match(body)
        if m:
            name, value = m.group(1), m.group(2).strip()
            if value.startswith("="):
                value = value[1:].strip()
            lines.append(VarLine(name, value, comment))
            continue
        if body.startswith("source_optional"):
            m2 = re.match(r"^source_optional\s*=\s*(.*)$", body)
            if m2:
                lines.append(IncludeLine(_strip_quotes(m2.group(1)), True, comment))
                continue
        if body.startswith("source"):
            m2 = re.match(r"^source\s*=\s*(.*)$", body)
            if m2:
                lines.append(IncludeLine(_strip_quotes(m2.group(1)), False, comment))
                continue
        m = _OPT_RE.match(body)
        if m:
            value = _strip_quotes(m.group(2))
            lines.append(OptionLine(m.group(1), value, comment))
            continue
        # Unknown syntax: keep verbatim, warn once per file by caller.
        u = UnknownLine(); u.raw = raw
        lines.append(u)
        if issues is not None and stripped:
            _add_issue(issues, Issue.WARNING,
                       "unrecognised line kept verbatim: %r" % stripped[:60],
                       path, lineno)
    return lines


# ---------------------------------------------------------------------------
# Document — one file on disk
# ---------------------------------------------------------------------------

class Document(object):
    """A loaded config file plus everything needed to write it back
    byte-faithfully when untouched and safely when edited."""

    def __init__(self, path):
        self.path = os.path.abspath(os.path.expanduser(path))
        self.real = os.path.realpath(self.path)
        self.lines = []
        self.dirty = False
        self.newline = "\n"
        self.final_newline = True
        self.decoded = True
        self.mtime_ns = None
        self.size = None

    # -- loading -----------------------------------------------------------
    def load(self):
        st = os.stat(self.real)
        self.mtime_ns, self.size = st.st_mtime_ns, st.st_size
        with open(self.real, "rb") as fh:
            data = fh.read()
        # surrogateescape makes the utf-8 decode lossless for any byte stream,
        # so a save round-trips non-UTF-8 files byte-for-byte.
        text = data.decode("utf-8", errors="surrogateescape")
        self.decoded = True
        self.newline = "\r\n" if "\r\n" in text else "\n"
        self.final_newline = text.endswith("\n") or text == ""
        body = text[:-len(self.newline)] if (self.final_newline and text) else text
        self.lines = parse_lines(body, self.path)
        return self

    # -- rendering ----------------------------------------------------------
    def render_text(self):
        body = self.newline.join(line.render() for line in self.lines)
        text = body + self.newline if self.final_newline else body
        if not self.lines:
            text = "" if self.final_newline is False and self.size == 0 else text
        return text

    # -- helpers ------------------------------------------------------------
    def options(self):
        return [l for l in self.lines if isinstance(l, OptionLine)]

    def append(self, line):
        # keep the file tidy: no two trailing blanks
        if self.lines and isinstance(self.lines[-1], BlankLine):
            self.lines.insert(len(self.lines) - 1, line)
        else:
            if self.lines and not self.final_newline:
                self.final_newline = True  # we are adding content after old EOF
            self.lines.append(line)
        self.dirty = True


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

class OptDef(object):
    """Definition of one MangoWM option: type, default, range, choices."""

    BOOL, INT, FLOAT, CHOICE, STRING, COLOR, CURSOR = (
        "bool", "int", "float", "choice", "string", "color", "cursor")

    def __init__(self, key, label, type_, default, min_=None, max_=None,
                 choices=None, desc="", multi=False, group=None):
        self.key = key
        self.label = label
        self.type = type_
        self.default = default
        self.min = min_
        self.max = max_
        self.choices = list(choices) if choices else None
        self.choice_labels = None     # optional value -> display label
        self.desc = desc
        self.multi = multi            # keys that may repeat: exec, exec_once
        self.group = group            # section heading shown on the page

    def validate(self, value):
        """Return (ok, error-message).  value is a str as it appears on disk."""
        if "\n" in value or "\r" in value:
            return False, "newlines are not allowed (MangoWM cannot parse them)"
        if len(value) > MAX_VALUE_LENGTH:
            return False, "value longer than %d characters (MangoWM refuses it)" % MAX_VALUE_LENGTH
        if self.type == OptDef.BOOL:
            if value not in ("0", "1"):
                return False, "boolean options must be 0 or 1, got %r" % value
            return True, ""
        if self.type == OptDef.INT:
            if not re.match(r"^-?\d+$", value.strip()):
                return False, "expected an integer, got %r" % value
            n = int(value)
            if self.min is not None and n < self.min:
                return False, "value %d is below the minimum %d" % (n, self.min)
            if self.max is not None and n > self.max:
                return False, "value %d is above the maximum %d" % (n, self.max)
            return True, ""
        if self.type == OptDef.FLOAT:
            if not re.match(r"^-?\d+(\.\d+)?$", value.strip()):
                return False, "expected a number, got %r" % value
            f = float(value)
            if self.min is not None and f < self.min:
                return False, "value %s is below the minimum %s" % (value, self.min)
            if self.max is not None and f > self.max:
                return False, "value %s is above the maximum %s" % (value, self.max)
            return True, ""
        if self.type == OptDef.CHOICE:
            if value not in (self.choices or []):
                return False, "must be one of: %s (got %r)" % (
                    ", ".join(self.choices or []), value)
            return True, ""
        # STRING / COLOR / CURSOR — loosely validated
        if self.type == OptDef.COLOR and value and not re.match(
                r"^0x[0-9a-fA-F]{6,8}$|^#[0-9a-fA-F]{6,8}$|"
                r"^rgb\([0-9a-fA-F]{6,8}\)$", value):
            return False, ("colour must look like 0xRRGGBB[AA], #RRGGBB[AA] "
                           "or rgb(RRGGBB[AA]) — got %r" % value)
        return True, ""


# ---------------------------------------------------------------------------
# Navigation tree
#
# Mirrors the official MangoWM settings layout (mangowm.github.io/settings):
# root pages, collapsible groups and the option-to-subpage mapping.
# ---------------------------------------------------------------------------

# (page_id, parent_id, title, icon candidates, description, kind)
# kind: "group"  — collapsible sidebar container
#       "autostart" / "env" / "binds" / "records" / "schema" / "special"
NAV_PAGES = [
    ("autostart", None, "Autostart",
     ["application-x-executable", "utilities-terminal"],
     "Applications and scripts that run when mangowm starts or reloads.",
     "autostart"),
    ("environment", None, "Environment Variables",
     ["application-add", "utilities-terminal"],
     "Environment variables passed on when mangowm starts applications.",
     "env"),
    ("bindings", None, "Bindings",
     ["preferences-desktop-keyboard-shortcuts", "preferences-desktop-keyboard",
      "input-keyboard"],
     "Keybindings: modifiers, keys, actions and their arguments.",
     "binds"),

    ("appearance", None, "Appearance",
     ["preferences-desktop-theme", "applications-graphics"], "", "group"),
    ("colors", "appearance", "Colors",
     ["applications-graphics", "color-select"],
     "Window, border and bar colors as 0xRRGGBBAA values.", "schema"),
    ("window-effects", "appearance", "Window Effects",
     ["applications-graphics", "preferences-desktop-theme"],
     "Opacity, blur and shadows for windows and layer-shell surfaces.",
     "schema"),
    ("gaps-borders", "appearance", "Gaps & Borders",
     ["preferences-desktop-wallpaper", "window-new"],
     "Spacing between windows and the border drawn around them.",
     "schema"),
    ("cursor", "appearance", "Cursor",
     ["input-mouse", "preferences-desktop-peripherals"],
     "Cursor theme, size and auto-hide behaviour.", "schema"),

    ("layout", None, "Layout",
     ["view-grid", "view-paged"], "", "group"),
    ("layout-tiling", "layout", "Tiling",
     ["view-grid", "preferences-desktop"],
     "Master-stack tiling: master area, behavior of new windows.", "schema"),
    ("layout-dwindle", "layout", "Dwindle",
     ["view-grid", "preferences-desktop"],
     "Spiral tiling: how splits are chosen and resized.", "schema"),
    ("layout-scroller", "layout", "Scroller",
     ["view-grid", "preferences-desktop"],
     "Scrolling column layout and window proportions.", "schema"),

    ("input-devices", None, "Input Devices",
     ["input-mouse", "preferences-desktop-peripherals"], "", "group"),
    ("keyboard", "input-devices", "Keyboard",
     ["input-keyboard", "preferences-desktop-keyboard"],
     "Repeat rate, numlock and XKB rules.", "schema"),
    ("pointer", "input-devices", "Pointer",
     ["input-mouse", "preferences-desktop-peripherals"],
     "Mouse acceleration, handedness and natural scrolling.", "schema"),
    ("trackpad", "input-devices", "Trackpad",
     ["input-touchpad", "input-tablet", "input-mouse"],
     "Tapping, scrolling and disable-while-typing.", "schema"),

    ("animations", None, "Animations",
     ["media-playback-start", "preferences-system-windows"], "", "group"),
    ("animations-general", "animations", "General",
     ["media-playback-start", "preferences-system-windows"],
     "Master switches and animation styles for windows and layers.",
     "schema"),
    ("animations-timing", "animations", "Timing",
     ["media-playback-start", "preferences-system-windows"],
     "How long each animation runs and how it fades.", "schema"),
    ("animations-curves", "animations", "Curves",
     ["media-playback-start", "preferences-system-windows"],
     "Easing curves, expressed as cubic-bezier values.", "schema"),

    ("behaviour", None, "Behaviour",
     ["preferences-system-windows", "window-new"], "", "group"),
    ("focus", "behaviour", "Focus",
     ["preferences-desktop-focus", "preferences-system-windows"],
     "Sloppy focus, cross-monitor and cross-tag movement.", "schema"),
    ("drag", "behaviour", "Drag & Floating",
     ["window-drag-and-drop", "window-new"],
     "Dragging windows, snapping and floating placement.", "schema"),
    ("scratchpad", "behaviour", "Scratchpad",
     ["window-new", "preferences-system-windows"],
     "The hidden scratchpad window and its size.", "schema"),

    ("rules", None, "Rules",
     ["window-new", "text-x-generic"], "", "group"),
    ("monitor-rules", "rules", "Monitor Rules",
     ["video-display", "computer"],
     "Per-monitor placement, mode and scale.", "records"),
    ("tag-rules", "rules", "Tag Rules",
     ["view-paged", "view-grid"],
     "Per-tag layout and monitor assignment.", "records-tag"),
    ("window-rules", "rules", "Window Rules",
     ["window-new", "preferences-system-windows"],
     "Apply tags, floating state and monitors to windows by class/title.",
     "records-window"),
    ("layer-rules", "rules", "Layer Rules",
     ["layer-shell", "window-new"],
     "Rules for layer-shell surfaces (bars, launchers, notifications).",
     "records-layer"),

    ("desktop", None, "Desktop & System",
     ["preferences-desktop", "computer"], "", "group"),
    ("theming", "desktop", "Theming",
     ["applications-graphics", "preferences-desktop-theme"],
     "GTK, icon and cursor themes for applications.", "theming"),
    ("background", "desktop", "Background",
     ["preferences-desktop-wallpaper", "applications-graphics"],
     "Set the wallpaper and remember it in autostart.sh.", "background"),
    ("notifications", "desktop", "Notifications",
     ["preferences-system-notifications", "dialog-information"],
     "Which notification daemon is running and how it is configured.",
     "notifications"),
    ("files", "desktop", "Config Files",
     ["text-x-generic", "document-open", "accessories-text-editor"],
     "Open and edit every loaded configuration file as text.", "files"),
    ("system", "desktop", "System Info",
     ["computer", "dialog-information"],
     "Compositor version, monitors and detected tools.", "system"),
]

PAGE_ORDER = [p[0] for p in NAV_PAGES]
PAGE_TITLES = dict((p[0], p[2]) for p in NAV_PAGES)
PAGE_PARENT = dict((p[0], p[1]) for p in NAV_PAGES)
PAGE_DESCRIPTIONS = dict((p[0], p[4]) for p in NAV_PAGES)
PAGE_KIND = dict((p[0], p[5]) for p in NAV_PAGES)
PAGE_ICON_CANDIDATES = dict((p[0], p[3]) for p in NAV_PAGES)

# Old page keys and titles (pre-2.3) mapped onto the new tree so --page
# arguments and bookmarks keep working.
LEGACY_PAGE_ALIAS = {
    "appearance": "gaps-borders",
    "behavior": "focus",
    "layouts": "layout-tiling",
    "keybindings": "bindings",
    "workspaces": "tag-rules",
    "windowrules": "window-rules",
    "monitors": "monitor-rules",
    "input": "keyboard",
    "startup": "autostart",
}

LEGACY_TITLE_ALIAS = {
    "window behavior": "focus",
    "window rules": "window-rules",
    "input devices": "keyboard",
    "startup apps": "autostart",
    "window rules": "window-rules",
}

# Every schema option mapped to its page in the tree above.  The mapping
# follows the official web settings' field lists.
KEY_PAGE = {
    # appearance — colors
    "root_color": "colors", "border_color": "colors", "focus_color": "colors",
    "urgent_color": "colors", "shadows_color": "colors",
    # appearance — window effects
    "blur": "window-effects", "blur_layer": "window-effects",
    "blur_optimized": "window-effects", "blur_params_num_passes": "window-effects",
    "blur_params_radius": "window-effects", "blur_params_noise": "window-effects",
    "blur_params_brightness": "window-effects", "blur_params_contrast": "window-effects",
    "blur_params_saturation": "window-effects",
    "focused_opacity": "window-effects", "unfocused_opacity": "window-effects",
    "shadows": "window-effects", "layer_shadows": "window-effects",
    "shadow_only_floating": "window-effects", "shadows_size": "window-effects",
    "shadows_blur": "window-effects", "shadows_position_x": "window-effects",
    "shadows_position_y": "window-effects",
    # appearance — gaps & borders
    "gap_inner_horizontal": "gaps-borders", "gap_inner_vertical": "gaps-borders",
    "gap_outer_horizontal": "gaps-borders", "gap_outer_vertical": "gaps-borders",
    "smart_gaps": "gaps-borders", "border_px": "gaps-borders",
    "border_radius": "gaps-borders", "no_border_when_single": "gaps-borders",
    "no_radius_when_single": "gaps-borders",
    # appearance — cursor
    "cursor_theme": "cursor", "cursor_size": "cursor",
    "cursor_hide_timeout": "cursor",
    # layout — tiling
    "circle_layout": "layout-tiling", "default_master_count": "layout-tiling",
    "default_master_factor": "layout-tiling", "new_is_master": "layout-tiling",
    "monocle_no_border": "layout-tiling", "monocle_no_gap": "layout-tiling",
    "monocle_no_radius": "layout-tiling", "monocle_tab_mode": "layout-tiling",
    "deck_tab_mode": "layout-tiling",
    # layout — dwindle
    "dwindle_smart_split": "layout-dwindle", "dwindle_smart_resize": "layout-dwindle",
    "dwindle_manual_split": "layout-dwindle", "dwindle_preserve_split": "layout-dwindle",
    "dwindle_horizontal_split": "layout-dwindle",
    "dwindle_vertical_split": "layout-dwindle",
    "dwindle_drop_simple_split": "layout-dwindle",
    # layout — scroller
    "scroller_structs": "layout-scroller",
    "scroller_default_proportion": "layout-scroller",
    "scroller_default_proportion_single": "layout-scroller",
    "scroller_ignore_proportion_single": "layout-scroller",
    "scroller_prefer_center": "layout-scroller", "scroller_focus_center": "layout-scroller",
    "scroller_proportion_preset": "layout-scroller",
    "edge_scroller_pointer_focus": "layout-scroller",
    # input — keyboard
    "numlock_on": "keyboard", "repeat_delay": "keyboard", "repeat_rate": "keyboard",
    "xkb_rules_layout": "keyboard", "xkb_rules_variant": "keyboard",
    "xkb_rules_options": "keyboard",
    # input — pointer
    "mouse_accel_profile": "pointer", "mouse_accel_speed": "pointer",
    "mouse_left_handed": "pointer", "mouse_middle_button_emulation": "pointer",
    "mouse_natural_scrolling": "pointer",
    # input — trackpad
    "disable_trackpad": "trackpad", "tap_to_click": "trackpad",
    "tap_and_drag": "trackpad", "drag_lock": "trackpad",
    "trackpad_natural_scrolling": "trackpad",
    "trackpad_disable_while_typing": "trackpad",
    "trackpad_left_handed": "trackpad",
    "trackpad_middle_button_emulation": "trackpad",
    "swipe_min_threshold": "trackpad",
    # animations — general
    "animations": "animations-general", "layer_animations": "animations-general",
    "animation_type_open": "animations-general",
    "animation_type_close": "animations-general",
    "layer_animation_type_open": "animations-general",
    "layer_animation_type_close": "animations-general",
    "tag_animation_direction": "animations-general",
    "zoom_initial_ratio": "animations-general", "zoom_end_ratio": "animations-general",
    # animations — timing
    "animation_duration_open": "animations-timing",
    "animation_duration_close": "animations-timing",
    "animation_duration_move": "animations-timing",
    "animation_duration_focus": "animations-timing",
    "animation_duration_tag": "animations-timing",
    "animation_fade_in": "animations-timing", "animation_fade_out": "animations-timing",
    "fade_in_begin_opacity": "animations-timing",
    "fade_out_begin_opacity": "animations-timing",
    # animations — curves
    "animation_curve_open": "animations-curves",
    "animation_curve_close": "animations-curves",
    "animation_curve_move": "animations-curves",
    "animation_curve_focus": "animations-curves",
    "animation_curve_tag": "animations-curves",
    # behaviour — focus
    "sloppy_focus": "focus", "focus_cross_monitor": "focus",
    "focus_cross_tag": "focus", "focus_on_activate": "focus",
    "warp_cursor": "focus", "auto_reload_config": "focus",
    # behaviour — drag & floating
    "drag_warp_cursor": "drag", "drag_tile_to_tile": "drag",
    "drag_tile_small": "drag", "enable_floating_snap": "drag",
    "snap_distance": "drag", "float_full_to_top": "drag",
    # behaviour — scratchpad
    "single_scratchpad": "scratchpad", "scratchpad_cross_monitor": "scratchpad",
    "scratchpad_width_ratio": "scratchpad", "scratchpad_height_ratio": "scratchpad",
}

def child_pages(parent_id):
    """Page ids whose parent is ``parent_id``, in display order."""
    return [p[0] for p in NAV_PAGES if p[1] == parent_id]


# Kept for backwards compatibility / external scripts that import it.
PAGE_ICONS = dict((k, v[0]) for k, v in PAGE_ICON_CANDIDATES.items())


def _P(key, label, type_, default, min_=None, max_=None, choices=None,
       desc="", group=None):
    """Small helper so every schema entry can carry a section title."""
    return OptDef(key, label, type_, default, min_, max_, choices, desc,
                  group=group)


def build_schema():
    """The MangoWM option database the UI is generated from.

    Every key, default and range below matches MangoWM itself — see
    ``src/config/option_defs.c`` (parse_option) and ``assets/config.conf``
    in the mango repository.  The defaults are MangoWM's, so options the
    user has not set are shown as MangoWM would use them.
    """
    P = _P
    appearance = [
        # Gaps
        P("gap_inner_horizontal", "Inner gap (horizontal)", OptDef.INT, "5", 0, 500,
          group="Gaps"),
        P("gap_inner_vertical", "Inner gap (vertical)", OptDef.INT, "5", 0, 500,
          group="Gaps"),
        P("gap_outer_horizontal", "Outer gap (horizontal)", OptDef.INT, "10", 0, 500,
          group="Gaps"),
        P("gap_outer_vertical", "Outer gap (vertical)", OptDef.INT, "10", 0, 500,
          group="Gaps"),
        P("smart_gaps", "Smart gaps", OptDef.BOOL, "0",
          desc="No gaps when only one window is shown", group="Gaps"),
        # Borders
        P("border_px", "Border width", OptDef.INT, "4", 0, 100, group="Borders"),
        P("border_radius", "Corner radius", OptDef.INT, "6", 0, 100,
          group="Borders"),
        P("no_border_when_single", "No border when single", OptDef.BOOL, "0",
          group="Borders"),
        P("no_radius_when_single", "No radius when single", OptDef.BOOL, "0",
          group="Borders"),
        P("focus_color", "Focused border color (0xRRGGBBAA)", OptDef.COLOR,
          "0xc9b890ff", group="Borders"),
        P("border_color", "Unfocused border color (0xRRGGBBAA)", OptDef.COLOR,
          "0x444444ff", group="Borders"),
        P("root_color", "Root/desktop background color", OptDef.COLOR,
          "0x201b14ff", group="Borders"),
        P("urgent_color", "Urgent window border color", OptDef.COLOR,
          "0xad401fff", group="Borders"),
        # Opacity
        P("focused_opacity", "Focused window opacity", OptDef.FLOAT, "1.0", 0.0, 1.0,
          group="Opacity"),
        P("unfocused_opacity", "Unfocused window opacity", OptDef.FLOAT, "1.0",
          0.0, 1.0, group="Opacity"),
        # Blur
        P("blur", "Blur", OptDef.BOOL, "0", group="Blur"),
        P("blur_layer", "Blur layer-shell surfaces", OptDef.BOOL, "0",
          desc="Blur bars/overlays (waybar, rofi, …)", group="Blur"),
        P("blur_optimized", "Optimized blur", OptDef.BOOL, "1", group="Blur"),
        P("blur_params_num_passes", "Blur passes", OptDef.INT, "2", 1, 10,
          group="Blur"),
        P("blur_params_radius", "Blur radius", OptDef.INT, "5", 0, 100,
          group="Blur"),
        P("blur_params_noise", "Blur noise", OptDef.FLOAT, "0.02", 0.0, 1.0,
          group="Blur"),
        P("blur_params_brightness", "Blur brightness", OptDef.FLOAT, "0.9", 0.0, 2.0,
          group="Blur"),
        P("blur_params_contrast", "Blur contrast", OptDef.FLOAT, "0.9", 0.0, 2.0,
          group="Blur"),
        P("blur_params_saturation", "Blur saturation", OptDef.FLOAT, "1.2", 0.0, 2.0,
          group="Blur"),
        # Shadows
        P("shadows", "Shadows", OptDef.BOOL, "0", group="Shadows"),
        P("layer_shadows", "Shadows on layer-shell surfaces", OptDef.BOOL, "0",
          group="Shadows"),
        P("shadow_only_floating", "Shadows only for floating windows",
          OptDef.BOOL, "1", group="Shadows"),
        P("shadows_size", "Shadow size", OptDef.INT, "10", 0, 100,
          group="Shadows"),
        P("shadows_blur", "Shadow blur", OptDef.INT, "15", 0, 100,
          group="Shadows"),
        P("shadows_position_x", "Shadow offset X", OptDef.INT, "0", -100, 100,
          group="Shadows"),
        P("shadows_position_y", "Shadow offset Y", OptDef.INT, "0", -100, 100,
          group="Shadows"),
        P("shadows_color", "Shadow color (0xRRGGBBAA)", OptDef.COLOR,
          "0x000000ff", group="Shadows"),
    ]
    animations = [
        P("animations", "Animations", OptDef.BOOL, "1",
          desc="Master switch for window animations", group="Basics"),
        P("layer_animations", "Layer-shell animations", OptDef.BOOL, "1",
          desc="Animate bars/overlays too (rofi, …)", group="Basics"),
        P("animation_type_open", "Open animation", OptDef.CHOICE, "slide",
          choices=CHOICE_ANIMATIONS, group="Basics"),
        P("animation_type_close", "Close animation", OptDef.CHOICE, "slide",
          choices=CHOICE_ANIMATIONS, group="Basics"),
        P("layer_animation_type_open", "Layer open animation", OptDef.CHOICE,
          "slide", choices=CHOICE_ANIMATIONS, group="Basics"),
        P("layer_animation_type_close", "Layer close animation", OptDef.CHOICE,
          "slide", choices=CHOICE_ANIMATIONS, group="Basics"),
        P("animation_fade_in", "Fade in", OptDef.BOOL, "1", group="Fading"),
        P("animation_fade_out", "Fade out", OptDef.BOOL, "1", group="Fading"),
        P("fade_in_begin_opacity", "Fade-in start opacity", OptDef.FLOAT, "0.5",
          0.0, 1.0, group="Fading"),
        P("fade_out_begin_opacity", "Fade-out start opacity", OptDef.FLOAT, "0.8",
          0.0, 1.0, group="Fading"),
        P("animation_duration_open", "Open duration (ms)", OptDef.INT, "400",
          0, 5000, group="Durations (ms)"),
        P("animation_duration_close", "Close duration (ms)", OptDef.INT, "800",
          0, 5000, group="Durations (ms)"),
        P("animation_duration_move", "Move duration (ms)", OptDef.INT, "500",
          0, 5000, group="Durations (ms)"),
        P("animation_duration_tag", "Tag switch duration (ms)", OptDef.INT, "350",
          0, 5000, group="Durations (ms)"),
        P("animation_duration_focus", "Focus duration (ms)", OptDef.INT, "0",
          0, 5000, group="Durations (ms)"),
        P("zoom_initial_ratio", "Zoom initial ratio", OptDef.FLOAT, "0.4", 0.0, 2.0,
          desc="Start scale for zoom animation", group="Zoom"),
        P("zoom_end_ratio", "Zoom end ratio", OptDef.FLOAT, "0.8", 0.0, 2.0,
          group="Zoom"),
        P("tag_animation_direction", "Tag animation direction",
          OptDef.INT, "1", 0, 1, desc="1 = horizontal, 0 = vertical",
          group="Zoom"),
        # Curves: cubic-bezier control points as comma-separated floats.
        P("animation_curve_open", "Open curve", OptDef.STRING,
          "0.46,1.0,0.29,1", desc="Cubic bezier x1,y1,x2,y2",
          group="Easing curves"),
        P("animation_curve_close", "Close curve", OptDef.STRING,
          "0.08,0.92,0,1", group="Easing curves"),
        P("animation_curve_move", "Move curve", OptDef.STRING,
          "0.46,1.0,0.29,1", group="Easing curves"),
        P("animation_curve_tag", "Tag curve", OptDef.STRING,
          "0.46,1.0,0.29,1", group="Easing curves"),
        P("animation_curve_focus", "Focus curve", OptDef.STRING,
          "0.46,1.0,0.29,1", group="Easing curves"),
    ]
    behavior = [
        P("sloppy_focus", "Sloppy focus", OptDef.BOOL, "1",
          desc="Focus follows the mouse", group="Focus"),
        P("warp_cursor", "Cursor warp", OptDef.BOOL, "1",
          desc="Warp cursor to the focused window", group="Focus"),
        P("focus_on_activate", "Focus on activate", OptDef.BOOL, "1",
          desc="Focus a window when it requests activation", group="Focus"),
        P("focus_cross_monitor", "Focus across monitors", OptDef.BOOL, "0",
          group="Focus"),
        P("focus_cross_tag", "Focus across tags", OptDef.BOOL, "0",
          group="Focus"),
        P("focus_direction_only_zone_overlap", "Directional focus needs overlap",
          OptDef.BOOL, "1", desc="focusdir only targets overlapping windows",
          group="Focus"),
        P("auto_reload_config", "Auto reload config", OptDef.BOOL, "1",
          desc="Reload MangoWM when a config file changes on disk",
          group="Focus"),
        # Placement / floating
        P("enable_floating_snap", "Floating snap", OptDef.BOOL, "0",
          desc="Snap floating windows to other windows and edges",
          group="Floating / placement"),
        P("snap_distance", "Snap distance", OptDef.INT, "30", 0, 200,
          group="Floating / placement"),
        P("float_full_to_top", "Fullscreen floats above bars", OptDef.BOOL, "0",
          group="Floating / placement"),
        P("drag_tile_to_tile", "Drag tile to tile", OptDef.BOOL, "1",
          desc="Swap tiles by dragging one onto another",
          group="Floating / placement"),
        P("drag_tile_small", "Drag tile onto small window swaps", OptDef.BOOL, "1",
          group="Floating / placement"),
        P("drag_warp_cursor", "Warp cursor while dragging", OptDef.BOOL, "1",
          group="Floating / placement"),
        # Scratchpads
        P("single_scratchpad", "Single shared scratchpad", OptDef.BOOL, "1",
          group="Scratchpads"),
        P("scratchpad_width_ratio", "Scratchpad width ratio", OptDef.FLOAT, "0.8",
          0.1, 1.0, group="Scratchpads"),
        P("scratchpad_height_ratio", "Scratchpad height ratio", OptDef.FLOAT, "0.9",
          0.1, 1.0, group="Scratchpads"),
        P("scratchpad_cross_monitor", "Scratchpads follow monitors", OptDef.BOOL,
          "1", group="Scratchpads"),
    ]
    layouts = [
        P("default_master_factor", "Master factor", OptDef.FLOAT, "0.55", 0.05, 0.95,
          desc="Width fraction of the master column",
          group="Master-stack (tile)"),
        P("default_master_count", "Master windows", OptDef.INT, "1", 1, 10,
          group="Master-stack (tile)"),
        P("new_is_master", "New window becomes master", OptDef.BOOL, "1",
          group="Master-stack (tile)"),
        P("circle_layout", "Layout cycle list", OptDef.STRING, "",
          desc="Comma list of layouts cycled by switch_layout, e.g. tile,scroller (empty = all)",
          group="Master-stack (tile)"),
        P("scroller_structs", "Scroller slot count", OptDef.INT, "20", 1, 100,
          group="Scroller"),
        P("scroller_default_proportion", "Default slot proportion", OptDef.FLOAT,
          "0.8", 0.05, 4.0, group="Scroller"),
        P("scroller_default_proportion_single", "Single slot proportion",
          OptDef.FLOAT, "1.0", 0.05, 4.0, group="Scroller"),
        P("scroller_proportion_preset", "Proportion presets", OptDef.STRING,
          "0.5,0.8,1.0", desc="Comma list cycled by switch_proportion_preset",
          group="Scroller"),
        P("scroller_focus_center", "Keep focused slot centered", OptDef.BOOL, "0",
          group="Scroller"),
        P("scroller_prefer_center", "Prefer centered placement", OptDef.BOOL, "0",
          group="Scroller"),
        P("scroller_ignore_proportion_single", "Ignore proportion when single",
          OptDef.BOOL, "1", group="Scroller"),
        P("edge_scroller_pointer_focus", "Focus slot under cursor at edge",
          OptDef.BOOL, "1", group="Scroller"),
        P("dwindle_smart_split", "Dwindle smart split", OptDef.BOOL, "0",
          group="Dwindle"),
        P("dwindle_drop_simple_split", "Dwindle drop simple split", OptDef.BOOL,
          "1", desc="Remove splits of single-child nodes",
          group="Dwindle"),
        P("dwindle_manual_split", "Manual split direction", OptDef.BOOL, "0",
          group="Dwindle"),
        P("dwindle_horizontal_split", "Allow horizontal split", OptDef.BOOL, "1",
          group="Dwindle"),
        P("dwindle_vertical_split", "Allow vertical split", OptDef.BOOL, "1",
          group="Dwindle"),
        P("dwindle_preserve_split", "Preserve split on window close", OptDef.BOOL,
          "0", group="Dwindle"),
        P("dwindle_smart_resize", "Dwindle smart resize", OptDef.BOOL, "0",
          group="Dwindle"),
        P("monocle_tab_mode", "Monocle tab bar", OptDef.BOOL, "1",
          group="Monocle / deck"),
        P("monocle_no_border", "Monocle: no borders", OptDef.BOOL, "0",
          group="Monocle / deck"),
        P("monocle_no_gap", "Monocle: no gaps", OptDef.BOOL, "0",
          group="Monocle / deck"),
        P("monocle_no_radius", "Monocle: no corner radius", OptDef.BOOL, "0",
          group="Monocle / deck"),
        P("deck_tab_mode", "Deck tab bar", OptDef.BOOL, "1",
          group="Monocle / deck"),
    ]
    input_ = [
        P("repeat_rate", "Key repeat rate (Hz)", OptDef.INT, "25", 1, 200,
          group="Keyboard"),
        P("repeat_delay", "Key repeat delay (ms)", OptDef.INT, "600", 0, 2000,
          group="Keyboard"),
        P("numlock_on", "NumLock on at startup", OptDef.BOOL, "0",
          group="Keyboard"),
        P("xkb_rules_layout", "Keyboard layout(s)", OptDef.STRING, "us",
          desc="XKB layout, comma list for multiple, e.g. us,de",
          group="Keyboard"),
        P("xkb_rules_variant", "Layout variant", OptDef.STRING, "",
          group="Keyboard"),
        P("xkb_rules_options", "XKB options", OptDef.STRING, "",
          desc="e.g. ctrl:nocaps or grp:alt_shift_toggle", group="Keyboard"),
        P("cursor_theme", "Cursor theme", OptDef.CURSOR, "",
          desc="Empty = system default", group="Cursor"),
        P("cursor_size", "Cursor size", OptDef.INT, "24", 8, 256, group="Cursor"),
        P("cursor_hide_timeout", "Cursor hide timeout (s, 0 = never)",
          OptDef.INT, "0", 0, 3600, group="Cursor"),
        P("disable_trackpad", "Disable trackpad", OptDef.BOOL, "0",
          desc="Needs relogin to apply", group="Trackpad"),
        P("tap_to_click", "Tap to click", OptDef.BOOL, "1", group="Trackpad"),
        P("tap_and_drag", "Tap and drag", OptDef.BOOL, "1", group="Trackpad"),
        P("drag_lock", "Drag lock", OptDef.BOOL, "1", group="Trackpad"),
        P("trackpad_natural_scrolling", "Natural scrolling", OptDef.BOOL, "0",
          group="Trackpad"),
        P("trackpad_disable_while_typing", "Disable while typing", OptDef.BOOL,
          "1", group="Trackpad"),
        P("trackpad_left_handed", "Left-handed", OptDef.BOOL, "0",
          group="Trackpad"),
        P("trackpad_middle_button_emulation", "Middle-button emulation",
          OptDef.BOOL, "0", group="Trackpad"),
        P("swipe_min_threshold", "Swipe minimum threshold", OptDef.INT, "1", 0, 100,
          group="Trackpad"),
        P("mouse_natural_scrolling", "Natural scrolling", OptDef.BOOL, "0",
          group="Mouse"),
        P("mouse_accel_profile", "Acceleration profile", OptDef.INT, "-1", -1, 2,
          desc="-1 = default, 0 = flat, 1 = adaptive", group="Mouse"),
        P("mouse_accel_speed", "Acceleration speed", OptDef.FLOAT, "0.0", -1.0, 1.0,
          group="Mouse"),
        P("mouse_left_handed", "Left-handed", OptDef.BOOL, "0", group="Mouse"),
        P("mouse_middle_button_emulation", "Middle-button emulation",
          OptDef.BOOL, "0", group="Mouse"),
    ]
    return {
        "appearance": appearance,
        "animations": animations,
        "behavior": behavior,
        "layouts": layouts,
        "input": input_,
        # These pages are list-editors or custom pages; empty option lists.
        "keybindings": [], "workspaces": [], "windowrules": [],
        "monitors": [], "startup": [], "theming": [], "background": [],
        "notifications": [], "files": [], "system": [],
    }


SCHEMA = build_schema()

# Schema options grouped per page in the new navigation tree (built once at
# import; options not covered by KEY_PAGE fall back to Gaps & Borders).
PAGE_SCHEMA = {}
for _page, _opts in SCHEMA.items():
    for _opt in _opts:
        PAGE_SCHEMA.setdefault(KEY_PAGE.get(_opt.key, "gaps-borders"), []).append(_opt)

# Record names behind the list pages, used by the search filter.
PAGE_RECORD = {
    "autostart": "exec_once", "environment": "env", "bindings": "bind",
    "monitor-rules": "monitor", "tag-rules": "tag_rule",
    "window-rules": "window_rule", "layer-rules": "layer_rule",
}

WEB_FIELDS = {
    'allow_lock_transparent': ('Allow Lock Transparent', 'Permit transparent/translucent lockscreens.', None),
    'allow_shortcuts_inhibit': ('Allow Shortcut Inhibition', 'Let apps suspend compositor keybindings.', None),
    'allow_tearing': ('Allow Tearing', 'Tearing policy: 0 off, 1 any window, 2 fullscreen only.', [('0', 'Disabled'), ('1', 'Always'), ('2', 'Fullscreen only')]),
    'always_show_group_bar': ('Always Show Group Bar', 'Show the group bar on every window.', None),
    'animation_curve_close': ('Close Curve', 'Cubic bezier curve for window close animations (x1,y1,x2,y2).', None),
    'animation_curve_focus': ('Focus Curve', 'Bezier curve for the focus opacity transition (x1,y1,x2,y2).', None),
    'animation_curve_move': ('Move Curve', 'Cubic bezier curve for move/resize animations (x1,y1,x2,y2).', None),
    'animation_curve_opacity_fade_in': ('Fade In Curve', 'Cubic bezier curve for fade-in opacity animations (x1,y1,x2,y2).', None),
    'animation_curve_opacity_fade_out': ('Fade Out Curve', 'Cubic bezier curve for fade-out opacity animations (x1,y1,x2,y2).', None),
    'animation_curve_open': ('Open Curve', 'Cubic bezier curve for window open animations (x1,y1,x2,y2).', None),
    'animation_curve_tag': ('Tag Switch Curve', 'Cubic bezier curve for tag-switch animations (x1,y1,x2,y2).', None),
    'animation_duration_close': ('Close Duration', 'Duration of window close animations in milliseconds (0-50000).', None),
    'animation_duration_focus': ('Focus Duration', 'Opacity transition duration when focus changes, in milliseconds (0-50000).', None),
    'animation_duration_move': ('Move Duration', 'Duration of window move/resize animations in milliseconds (0-50000).', None),
    'animation_duration_open': ('Open Duration', 'Duration of window open animations in milliseconds (0-50000).', None),
    'animation_duration_tag': ('Tag Switch Duration', 'Duration of tag-switch animations in milliseconds (0-50000).', None),
    'animation_fade_in': ('Fade In', 'Fade windows in when they appear.', None),
    'animation_fade_out': ('Fade Out', 'Fade windows out when they close.', None),
    'animation_type_close': ('Close Animation Type', 'Close animation for windows. Unset uses zoom; none disables it.', [('', 'Unset'), ('none', 'None'), ('fade', 'Fade'), ('slide', 'Slide'), ('zoom', 'Zoom')]),
    'animation_type_open': ('Open Animation Type', 'Open animation for windows. Unset uses slide; none disables it.', [('', 'Unset'), ('none', 'None'), ('fade', 'Fade'), ('slide', 'Slide'), ('zoom', 'Zoom')]),
    'animations': ('Enable Animations', 'Toggle window animations. Gesture transitions still animate when this is off.', None),
    'auto_reload_config': ('Auto Reload Config', 'Reload when the main config or a sourced file changes.', None),
    'axis_bind_apply_timeout': ('Axis Bind Apply Timeout', 'Minimum milliseconds between axis binding calls (0-1000). Closer scroll events are ignored.', None),
    'axis_scroll_factor': ('Axis Scroll Factor', 'Multiplier for axis (wheel) scroll speed (0.1-10.0).', None),
    'blur': ('Enable Blur', 'Toggle background blur behind windows.', None),
    'blur_layer': ('Blur Layer Surfaces', 'Apply blur to layer-shell surfaces (panels, notifications). Requires blur.', None),
    'blur_optimized': ('Optimized Blur', 'Cache the wallpaper and background for faster blur.', None),
    'blur_params_brightness': ('Blur Brightness', 'Brightness multiplier for the blurred layer.', None),
    'blur_params_contrast': ('Blur Contrast', 'Contrast multiplier for the blurred layer.', None),
    'blur_params_noise': ('Blur Noise', 'Noise level added to the blur.', None),
    'blur_params_num_passes': ('Blur Passes', 'Number of blur iterations.', None),
    'blur_params_radius': ('Blur Radius', 'Pixel radius of the blur kernel.', None),
    'blur_params_saturation': ('Blur Saturation', 'Saturation multiplier for the blurred layer.', None),
    'border_color': ('Inactive Border Color', 'Border color for unfocused windows.', None),
    'border_px': ('Border Width', 'Thickness of window borders in pixels.', None),
    'border_radius': ('Border Radius', 'Corner radius in pixels.', None),
    'border_resize_size': ('Border Resize Hitbox', 'Thickness in pixels of the resize grab strip. 0 uses the border width.', None),
    'button_map': ('Button Map', 'Trackpad tap-to-button mapping: 0 left/right/middle, 1 left/middle/right.', [('0', 'Left / right / middle'), ('1', 'Left / middle / right')]),
    'center_master_overspread': ('Center Master Overspread', 'In center_tile, let the master area fill the screen when there are no stack windows.', None),
    'center_when_single_stack': ('Center Master With One Stack Window', 'Center the master when the stack has only one window.', None),
    'circle_layout': ('Circle Layout', 'Ordered layouts cycled by switch_layout.', None),
    'cursor_hide_on_keypress': ('Hide Cursor on Keypress', 'Auto-hide the cursor whenever any keyboard key is pressed.', None),
    'cursor_hide_timeout': ('Cursor Hide Timeout', 'Seconds of inactivity before auto-hiding the cursor (0 = never).', None),
    'cursor_size': ('Cursor Size', 'XCursor size in pixels.', None),
    'cursor_theme': ('Cursor Theme', 'XCursor theme name. Empty uses the system XCursor theme.', None),
    'deck_tab_mode': ('Deck Tab Mode', "Merge the deck layout's stack windows into a tab.", None),
    'default_master_count': ('Number of Masters', 'Number of windows in the master area.', None),
    'default_master_factor': ('Master Area Factor', 'Master area size: width in horizontal layouts, height in vertical layouts (0.1-0.9).', None),
    'dim_enable': ('Dim Enable', 'Enable the dim overlay on windows.', None),
    'dim_focused_color': ('Dim Focused Color', 'Dim color of the focused window.', None),
    'dim_unfocused_color': ('Dim Unfocused Color', 'Dim color for unfocused windows.', None),
    'disable_middle_paste': ('Disable Middle Paste', 'Disable middle-click paste by turning off primary selection for Wayland apps.', None),
    'disable_trackpad': ('Disable Trackpad', 'Disable all trackpad devices.', None),
    'drag_corner': ('Resize Corner', 'Floating window resize corner: 0 top-left, 1 top-right, 2 bottom-left, 3 bottom-right, 4 nearest.', None),
    'drag_lock': ('Drag Lock', 'Keep dragging after lifting the finger.', None),
    'drag_tile_small': ('Small Tile Preview', 'Shrink dragged tile to a small thumbnail.', None),
    'drag_tile_to_tile': ('Tile-to-Tile Drag', 'Reorder tiled windows by dragging.', None),
    'drag_warp_cursor': ('Warp Cursor on Drag Resize', 'Warp the pointer to the chosen corner when a resize starts from the window body.', None),
    'drop_color': ('Drop Target Color', 'Color of the drop target rectangle shown while dragging a tiled window.', None),
    'dwindle_drop_simple_split': ('Dwindle Drop Simple Split', 'Use a 2-zone drop preview that follows dwindle_split_ratio instead of four quadrants.', None),
    'dwindle_horizontal_split': ('Horizontal Split', 'Where a new window goes in a horizontal split: 0 follows the cursor, 1 right, 2 left.', [('0', 'Follow cursor'), ('1', 'Right'), ('2', 'Left')]),
    'dwindle_manual_split': ('Manual Split', 'Use the direction set by dwindle_toggle_split_direction for new splits.', None),
    'dwindle_preserve_split': ('Dwindle Preserve Split', 'Keep the sibling split geometry when a window closes.', None),
    'dwindle_smart_resize': ('Smart Resize', 'When dragging a split, move it toward the cursor regardless of which side was grabbed.', None),
    'dwindle_smart_split': ('Dwindle Smart Split', 'Choose the split direction from the cursor position inside the focused window.', None),
    'dwindle_split_ratio': ('Split Ratio', 'Proportion of space for the first child when splitting.', None),
    'dwindle_vertical_split': ('Vertical Split', 'Where a new window goes in a vertical split: 0 follows the cursor, 1 below, 2 above.', [('0', 'Follow cursor'), ('1', 'Below'), ('2', 'Above')]),
    'edge_scroller_focus_allow_speed': ('Edge Focus Speed', 'Minimum pointer speed required before edge focus triggers (0-1000).', None),
    'edge_scroller_pointer_focus': ('Edge Pointer Focus', 'Focus scroller windows that are partly off-screen.', None),
    'enable_border_resize': ('Enable Border Resize', 'Drag a window border with the left mouse button to resize. Tiled windows show only shared edges.', None),
    'enable_floating_snap': ('Floating Snap', 'Snap floating windows to screen edges and other visible floating windows.', None),
    'enable_hotarea': ('Enable Hotarea', 'Activate overview by moving the cursor to a screen corner.', None),
    'exchange_cross_monitor': ('Cross-Monitor Exchange', 'Let exchange_client and move_client cross monitor boundaries.', None),
    'fade_in_begin_opacity': ('Fade In Start Opacity', 'Initial opacity for fade-in animations (0.0-1.0).', None),
    'fade_out_begin_opacity': ('Fade Out Start Opacity', 'Initial opacity for fade-out animations (0.0-1.0).', None),
    'float_full_to_top': ('Shared Top Layer', 'One layer for fullscreen, floating, and layer-shell top windows. The last raised window is on top.', None),
    'focus_color': ('Active Border Color', 'Border color of the focused window on the selected monitor.', None),
    'focus_cross_monitor': ('Cross-Monitor Focus', 'Let directional focus cross monitor boundaries.', None),
    'focus_cross_tag': ('Cross-Tag Focus', 'Let directional focus cross tag boundaries.', None),
    'focus_direction_only_zone_overlap': ('Focus Direction Zone Overlap', 'Directional focus only picks windows overlapping the current window on the perpendicular axis.', None),
    'focus_on_activate': ('Focus on Activate', 'Focus windows immediately when they request activation.', None),
    'focused_opacity': ('Focused Opacity', 'Opacity of the currently focused window.', None),
    'gap_inner_horizontal': ('Inner Gap Horizontal', 'Horizontal gap between tiled windows.', None),
    'gap_inner_vertical': ('Inner Gap Vertical', 'Vertical gap between tiled windows.', None),
    'gap_outer_horizontal': ('Outer Gap Horizontal', 'Horizontal gap between windows and screen edges.', None),
    'gap_outer_vertical': ('Outer Gap Vertical', 'Vertical gap between windows and screen edges.', None),
    'gesture_live': ('Live Gestures', 'Update tag, focus, and overview transitions while fingers move, not only after release.', None),
    'gesture_swipe_cancel_ratio': ('Swipe Cancel Ratio', 'Fraction of the last page to drag before release commits the swipe.', None),
    'gesture_swipe_distance': ('Swipe Distance', 'Finger travel (px) that corresponds to one full page transition.', None),
    'gesture_swipe_min_speed_to_force': ('Swipe Force Speed', 'Average speed per event (px) that commits the swipe below the cancel ratio.', None),
    'global_color': ('Global Border Color', 'Border color for a focused global window.', None),
    'group_bar_button_color': ('Close Button Color', 'Close button color.', None),
    'group_bar_button_margin': ('Close Button Margin', 'Close button inset from the right edge in px.', None),
    'group_bar_button_size': ('Close Button Size', 'Close button size in px.', None),
    'group_bar_close_button_enable': ('Group Bar Close Button', 'Show a close button on the group bar.', None),
    'group_bar_decorate_bg_color': ('Group Bar Background', 'Background color of the group bar.', None),
    'group_bar_decorate_border_color': ('Group Bar Border Color', 'Border color of the group bar.', None),
    'group_bar_decorate_border_width': ('Group Bar Border Width', 'Border width of the group bar in pixels.', None),
    'group_bar_decorate_corner_radius': ('Group Bar Corner Radius', 'Corner radius of the group bar in pixels.', None),
    'group_bar_decorate_fg_color': ('Group Bar Text Color', 'Text color of the group bar.', None),
    'group_bar_decorate_focus_bg_color': ('Group Bar Focus Background', 'Background color of the focused group bar entry.', None),
    'group_bar_decorate_focus_fg_color': ('Group Bar Focus Text Color', 'Text color of the focused group bar entry.', None),
    'group_bar_decorate_font_desc': ('Group Bar Font', 'Pango font description for the group bar.', None),
    'group_bar_decorate_padding_x': ('Group Bar Horizontal Padding', 'Horizontal padding inside the group bar in pixels.', None),
    'group_bar_decorate_padding_y': ('Group Bar Vertical Padding', 'Vertical padding inside the group bar in pixels.', None),
    'group_bar_height': ('Group Bar Height', 'Height of the per-window group bar in pixels.', None),
    'hdr_depth': ('HDR Render Depth', 'HDR render depth: 0 auto, 1 force 8-bit, 2 force 10-bit.', [('0', 'Auto'), ('1', '8-bit'), ('2', '10-bit')]),
    'hotarea_corner': ('Hotarea Corner', 'Screen corner for the overview hotarea: 0 top-left, 1 top-right, 2 bottom-left, 3 bottom-right.', [('0', 'Top Left'), ('1', 'Top Right'), ('2', 'Bottom Left'), ('3', 'Bottom Right')]),
    'hotarea_disable_on_fullscreen': ('Hotarea Disable On Fullscreen', 'Do not trigger the hot area while a fullscreen window is focused.', None),
    'hotarea_size': ('Hotarea Size', 'Size of the corner activation zone in pixels (1-1000).', None),
    'idle_inhibit_ignore_visible': ('Allow Hidden Clients to Inhibit Idle', 'Let clients that are not visible inhibit idle.', None),
    'idle_inhibit_when_fullscreen': ('Inhibit Idle When Fullscreen', 'Keep idle inhibited while a fullscreen window is focused.', None),
    'jump_label_decorate_bg_color': ('Jump Label Background', 'Background color of the jump label.', None),
    'jump_label_decorate_border_color': ('Jump Label Border Color', 'Border color of the jump label.', None),
    'jump_label_decorate_border_width': ('Jump Label Border Width', 'Border width of the jump label in pixels.', None),
    'jump_label_decorate_corner_radius': ('Jump Label Corner Radius', 'Corner radius of the jump label in pixels.', None),
    'jump_label_decorate_fg_color': ('Jump Label Text Color', 'Text color of the jump label.', None),
    'jump_label_decorate_focus_bg_color': ('Jump Label Focus Background', 'Background color of the focused jump label entry.', None),
    'jump_label_decorate_focus_fg_color': ('Jump Label Focus Text Color', 'Text color of the focused jump label entry.', None),
    'jump_label_decorate_font_desc': ('Jump Label Font', 'Pango font description for the jump label.', None),
    'jump_label_decorate_padding_x': ('Jump Label Horizontal Padding', 'Horizontal padding inside the jump label in pixels.', None),
    'jump_label_decorate_padding_y': ('Jump Label Vertical Padding', 'Vertical padding inside the jump label in pixels.', None),
    'jump_labels': ('Jump Label Characters', 'Characters used to label windows in jump label mode, in order.', None),
    'layer_animation_type_close': ('Layer Close Animation Type', 'Close animation for layer surfaces. Unset only fades; none disables it.', [('', 'Unset'), ('none', 'None'), ('fade', 'Fade'), ('slide', 'Slide'), ('zoom', 'Zoom')]),
    'layer_animation_type_open': ('Layer Open Animation Type', 'Open animation for layer surfaces. Unset only fades; none disables it.', [('', 'Unset'), ('none', 'None'), ('fade', 'Fade'), ('slide', 'Slide'), ('zoom', 'Zoom')]),
    'layer_animations': ('Layer Animations', 'Animate layer-shell surfaces such as panels and notifications. Requires animations.', None),
    'layer_shadows': ('Layer Shadows', 'Draw shadows under layer-shell surfaces. Requires shadows.', None),
    'maximized_screen_color': ('Maximized Border', 'Border color for a focused maximized window.', None),
    'monocle_no_border': ('Monocle No Border', 'Remove borders in monocle layout.', None),
    'monocle_no_gap': ('Monocle No Gap', 'Remove gaps in monocle layout.', None),
    'monocle_no_radius': ('Monocle No Radius', 'Remove corner radius in monocle layout.', None),
    'monocle_right_button_close': ('Right Click to Close', 'Right click on the tab bar closes that window.', None),
    'monocle_tab_mode': ('Monocle Tab Mode', 'Merge stacked windows into tabs in monocle layout.', None),
    'mouse_accel_profile': ('Mouse Acceleration Profile', 'Mouse acceleration profile: 0 none, 1 flat, 2 adaptive.', [('0', 'None'), ('1', 'Flat'), ('2', 'Adaptive')]),
    'mouse_accel_speed': ('Mouse Acceleration Speed', 'Mouse acceleration speed from -1.0 to 1.0.', None),
    'mouse_click_method': ('Mouse Click Method', 'Click method: 0 no emulation, 1 button areas, 2 clickfinger.', [('0', 'No emulation'), ('1', 'Button areas'), ('2', 'Clickfinger')]),
    'mouse_left_handed': ('Mouse Left Handed', 'Swap left and right mouse buttons.', None),
    'mouse_middle_button_emulation': ('Mouse Middle Button Emulation', 'Emit a middle click when both buttons are pressed together.', None),
    'mouse_natural_scrolling': ('Mouse Natural Scrolling', 'Invert mouse scroll direction.', None),
    'mouse_scroll_button': ('Mouse Scroll Button', 'Button used for button scrolling on the mouse (272-279).', None),
    'mouse_scroll_method': ('Mouse Scroll Method', 'Scroll method: 0 disabled, 1 two-finger, 2 edge, 4 button.', [('0', 'Disabled'), ('1', 'Two-finger'), ('2', 'Edge'), ('4', 'Button')]),
    'mouse_send_events_mode': ('Mouse Send Events Mode', 'Send events: 0 enabled, 1 disabled, 2 disabled with an external pointer.', [('0', 'Enabled'), ('1', 'Disabled'), ('2', 'Disabled with external pointer')]),
    'new_is_master': ('New Windows as Master', 'New windows open in the master area instead of the stack.', None),
    'no_border_when_single': ('No Border When Single', 'Remove borders when only one window is visible.', None),
    'no_radius_when_single': ('No Corner Radius When Single', 'Remove corner radius when only one window is visible.', None),
    'numlock_on': ('NumLock on Startup', 'Turn on NumLock at startup.', None),
    'overcircle_center_ratio': ('Overcircle Center Ratio', 'Focused window width as a fraction of usable screen width (0.1-0.9).', None),
    'overlay_color': ('Overlay Color', 'Border color for overlay windows.', None),
    'overview_gap_inner': ('Overview Inner Gap', 'Gap between overview cards (0-1000).', None),
    'overview_gap_outer': ('Overview Outer Gap', 'Gap around the overview layout (0-1000).', None),
    'repeat_delay': ('Repeat Delay', 'Delay before key repeat starts, in milliseconds.', None),
    'repeat_rate': ('Repeat Rate', 'Keyboard repeat rate in key presses per second.', None),
    'root_color': ('Root Background', 'Desktop background behind all windows.', None),
    'scratchpad_color': ('Scratchpad Border Color', 'Border color for a focused scratchpad window.', None),
    'scratchpad_cross_monitor': ('Scratchpad Cross Monitor', 'Share the scratchpad pool across all monitors.', None),
    'scratchpad_height_ratio': ('Scratchpad Height Ratio', "Scratchpad height as a fraction of the monitor (0-1), used when the rule does not set a height. A value of 0 keeps the application's requested height.", None),
    'scratchpad_width_ratio': ('Scratchpad Width Ratio', "Scratchpad width as a fraction of the monitor (0-1), used when the rule does not set a width. A value of 0 keeps the application's requested width.", None),
    'scroller_default_proportion': ('Default Proportion', 'Default width proportion for new windows in the scroller layout.', None),
    'scroller_default_proportion_single': ('Single Window Proportion', 'Width proportion for a tag with one window. Requires scroller_ignore_proportion_single=0.', None),
    'scroller_focus_center': ('Scroller Focus Center', 'Always center the focused window in the scroller viewport.', None),
    'scroller_ignore_proportion_single': ('Ignore Single-Window Proportion', 'Ignore scroller_default_proportion_single when the tag has one window.', None),
    'scroller_prefer_center': ('Scroller Prefer Center', 'Center the focused window only when it was outside the viewport.', None),
    'scroller_prefer_overspread': ('Prefer Overspread', 'Focused edge window fills unused space. Overrides scroller_focus_center and scroller_prefer_center.', None),
    'scroller_proportion_preset': ('Proportion Presets', 'Preset width proportions (0.1-1.0) cycled by switch_proportion_preset.', None),
    'scroller_structs': ('Scroller Side Margin', 'Pixels reserved on each side of the scroller area (0-1000).', None),
    'send_events_mode': ('Send Events Mode', 'Touch and tablet events: 0 enabled, 1 disabled, 2 disabled with an external pointer.', [('0', 'Enabled'), ('1', 'Disabled'), ('2', 'Disabled with external pointer')]),
    'shadow_only_floating': ('Floating Shadows Only', 'Only draw shadows for floating windows.', None),
    'shadows': ('Enable Shadows', 'Render drop shadows behind windows.', None),
    'shadows_blur': ('Shadow Softness', 'Gaussian blur sigma for softer shadows.', None),
    'shadows_color': ('Shadow Color', 'Drop shadow color for windows.', None),
    'shadows_position_x': ('Shadow Offset X', 'Horizontal shadow offset.', None),
    'shadows_position_y': ('Shadow Offset Y', 'Vertical shadow offset.', None),
    'shadows_size': ('Shadow Size', 'How far the shadow extends beyond window edges.', None),
    'single_scratchpad': ('Single Scratchpad', 'Show only one scratchpad window (named or standard) at a time.', None),
    'sloppy_focus': ('Sloppy Focus', 'Focus follows the mouse pointer.', None),
    'smart_gaps': ('Smart Gaps', 'Disable gaps when only one tiling window is visible.', None),
    'snap_distance': ('Snap Distance', 'Maximum snap distance in pixels for floating windows (0-99999).', None),
    'special_dim': ('Special Tag Dim', 'Background dim level while the special tag is active.', None),
    'special_gap_inner_horizontal': ('Special Inner Gap Horizontal', 'Horizontal inner gap for windows on the special tag.', None),
    'special_gap_inner_vertical': ('Special Inner Gap Vertical', 'Vertical inner gap for windows on the special tag.', None),
    'special_gap_outer_horizontal': ('Special Outer Gap Horizontal', 'Horizontal outer gap for windows on the special tag.', None),
    'special_gap_outer_vertical': ('Special Outer Gap Vertical', 'Vertical outer gap for windows on the special tag.', None),
    'special_tag_cross_monitor': ('Special Tag Cross Monitor', "Move windows from other monitors' special tags to the monitor where the special tag is activated and apply the same special-tag layout. Floating windows are rescaled and centered.", None),
    'split_color': ('Split Indicator', 'Color of the dwindle manual split guide line.', None),
    'swipe_min_threshold': ('Swipe Minimum Threshold', 'Minimum distance in pixels to trigger a swipe gesture (1-1000).', None),
    'sync_obj_enable': ('Sync Objects', 'Use DRM sync object timelines. Requires a restart.', None),
    'tab_bar_decorate_bg_color': ('Tab Bar Background', 'Background color of the tab bar.', None),
    'tab_bar_decorate_border_color': ('Tab Bar Border Color', 'Border color of the tab bar.', None),
    'tab_bar_decorate_border_width': ('Tab Bar Border Width', 'Border width of the tab bar in pixels.', None),
    'tab_bar_decorate_corner_radius': ('Tab Bar Corner Radius', 'Corner radius of the tab bar in pixels.', None),
    'tab_bar_decorate_fg_color': ('Tab Bar Text Color', 'Text color of the tab bar.', None),
    'tab_bar_decorate_focus_bg_color': ('Tab Bar Focus Background', 'Background color of the focused tab bar entry.', None),
    'tab_bar_decorate_focus_fg_color': ('Tab Bar Focus Text Color', 'Text color of the focused tab bar entry.', None),
    'tab_bar_decorate_font_desc': ('Tab Bar Font', 'Pango font description for the tab bar.', None),
    'tab_bar_decorate_padding_x': ('Tab Bar Horizontal Padding', 'Horizontal padding inside the tab bar in pixels.', None),
    'tab_bar_decorate_padding_y': ('Tab Bar Vertical Padding', 'Vertical padding inside the tab bar in pixels.', None),
    'tab_bar_height': ('Tab Bar Height', 'Height of the tab bar in pixels.', None),
    'tag_animation_direction': ('Tag Animation Direction', 'Tag-switch animation direction: 1 horizontal, 0 vertical.', [('1', 'Horizontal'), ('0', 'Vertical')]),
    'tag_carousel': ('Tag Carousel', 'Tags wrap around in a carousel.', None),
    'tag_gather': ('Gather Tags', 'Compact occupied tags to consecutive numbers starting at 1. The current view follows.', None),
    'tag_num': ('Number of Tags', 'Number of tags (1-31).', None),
    'tap_and_drag': ('Tap and Drag', 'Tap and hold to drag items.', None),
    'tap_to_click': ('Tap to Click', 'Tap the trackpad to click.', None),
    'title_message_bar_decorate_bg_color': ('Title Bar Background', 'Background color of the title message bar.', None),
    'title_message_bar_decorate_border_color': ('Title Bar Border Color', 'Border color of the title message bar.', None),
    'title_message_bar_decorate_border_width': ('Title Bar Border Width', 'Border width of the title message bar in pixels.', None),
    'title_message_bar_decorate_corner_radius': ('Title Bar Corner Radius', 'Corner radius of the title message bar in pixels.', None),
    'title_message_bar_decorate_fg_color': ('Title Bar Text Color', 'Text color of the title message bar.', None),
    'title_message_bar_decorate_focus_bg_color': ('Title Bar Focus Background', 'Background color of the focused title message bar entry.', None),
    'title_message_bar_decorate_focus_fg_color': ('Title Bar Focus Text Color', 'Text color of the focused title message bar entry.', None),
    'title_message_bar_decorate_font_desc': ('Title Bar Font', 'Pango font description for the title message bar.', None),
    'title_message_bar_decorate_padding_x': ('Title Bar Horizontal Padding', 'Horizontal padding inside the title message bar in pixels.', None),
    'title_message_bar_decorate_padding_y': ('Title Bar Vertical Padding', 'Vertical padding inside the title message bar in pixels.', None),
    'title_message_bar_gap': ('Title Message Bar Gap', 'Gap between the title message bar and the focused overview card or switcher panel.', None),
    'title_message_bar_height': ('Title Message Bar Height', 'Height of the title message bar in overcircle and the switcher. 0 hides it.', None),
    'touch_enable': ('Enable Touch', 'Turn on touchscreen input.', None),
    'touch_enable_mouse_emulation': ('Touch Mouse Emulation', 'Emulate left-button clicks and drags on surfaces that do not accept touch.', None),
    'trackpad_accel_profile': ('Trackpad Acceleration Profile', 'Trackpad acceleration profile: 0 none, 1 flat, 2 adaptive.', [('0', 'None'), ('1', 'Flat'), ('2', 'Adaptive')]),
    'trackpad_accel_speed': ('Trackpad Acceleration Speed', 'Trackpad acceleration speed from -1.0 to 1.0.', None),
    'trackpad_click_method': ('Trackpad Click Method', 'Click method: 0 no emulation, 1 button areas, 2 clickfinger.', [('0', 'No emulation'), ('1', 'Button areas'), ('2', 'Clickfinger')]),
    'trackpad_disable_while_typing': ('Disable While Typing', 'Temporarily disable the trackpad while typing.', None),
    'trackpad_left_handed': ('Trackpad Left Handed', 'Swap left and right trackpad buttons.', None),
    'trackpad_middle_button_emulation': ('Trackpad Middle Button Emulation', 'Emit a middle click when both buttons are pressed together.', None),
    'trackpad_natural_scrolling': ('Trackpad Natural Scrolling', 'Invert trackpad scroll direction.', None),
    'trackpad_scroll_button': ('Trackpad Scroll Button', 'Button used for button scrolling on the trackpad (272-279).', None),
    'trackpad_scroll_factor': ('Trackpad Scroll Factor', 'Multiplier for trackpad scroll speed (0.1-10.0).', None),
    'trackpad_scroll_method': ('Trackpad Scroll Method', 'Scroll method: 0 disabled, 1 two-finger, 2 edge, 4 button.', [('0', 'Disabled'), ('1', 'Two-finger'), ('2', 'Edge'), ('4', 'Button')]),
    'trackpad_send_events_mode': ('Trackpad Send Events Mode', 'Send events: 0 enabled, 1 disabled, 2 disabled with an external pointer.', [('0', 'Enabled'), ('1', 'Disabled'), ('2', 'Disabled with external pointer')]),
    'unfocused_opacity': ('Unfocused Opacity', 'Opacity of unfocused windows.', None),
    'urgent_color': ('Urgent Border Color', 'Border color for urgent windows.', None),
    'view_current_to_back': ('View Current to Back', 'Viewing the current tag switches to the previously viewed tag.', None),
    'warp_cursor': ('Warp Cursor', 'Warp the pointer to the center of the focused window when focus changes by keybinding.', None),
    'xkb_rules_layout': ('XKB Layout', "Keyboard layout codes, for example 'us' or 'us,ru'.", None),
    'xkb_rules_model': ('XKB Model', 'Keyboard model for XKB.', None),
    'xkb_rules_options': ('XKB Options', "XKB options, for example 'ctrl:nocaps'.", None),
    'xkb_rules_rules': ('XKB Rules', 'XKB rules file.', None),
    'xkb_rules_variant': ('XKB Variant', 'Keyboard layout variant.', None),
    'xwayland_ignore_scale': ('Ignore XWayland Scale', 'Make XWayland use physical pixels instead of the output scale.', None),
    'xwayland_persistence': ('XWayland Persistence', 'Keep XWayland running even when no X11 clients are active.', None),
    'zoom_end_ratio': ('Zoom End Ratio', 'Final scale for zoom animations (0.1-1.0).', None),
    'zoom_initial_ratio': ('Zoom Initial Ratio', 'Initial scale for zoom animations (0.1-1.0).', None),
}

BINDING_CATEGORIES = {
    'centerwin': 'window',
    'chvt': 'system',
    'comboview': 'tag',
    'create_virtual_output': 'system',
    'destroy_all_virtual_output': 'system',
    'disable_monitor': 'monitor',
    'dwindle_split_horizontal': 'layout',
    'dwindle_split_vertical': 'layout',
    'dwindle_toggle_current_split': 'layout',
    'dwindle_toggle_split_direction': 'layout',
    'enable_monitor': 'monitor',
    'enteroverview': 'layout',
    'exchange_client': 'focus',
    'exchange_stack_client': 'focus',
    'focus_window_or_workspace': 'focus',
    'focusdir': 'focus',
    'focusid': 'focus',
    'focuslast': 'focus',
    'focusmon': 'monitor',
    'focusstack': 'focus',
    'groupfocus': 'group',
    'groupjoin': 'group',
    'groupleave': 'group',
    'incgaps': 'appearance',
    'incnmaster': 'layout',
    'killclient': 'window',
    'leaveoverview': 'layout',
    'load_config_file': 'system',
    'minimized': 'scratchpad',
    'move_client': 'focus',
    'moveresize': 'floating',
    'movewin': 'floating',
    'overcircle': 'layout',
    'quit': 'system',
    'reload_config': 'system',
    'resizewin': 'floating',
    'restore_minimized': 'scratchpad',
    'scroller_stack': 'layout',
    'set_proportion': 'layout',
    'setkeymode': 'bindings',
    'setlayout': 'layout',
    'setmfact': 'layout',
    'setoption': 'system',
    'sleep_monitor': 'monitor',
    'sleep_toggle_monitor': 'monitor',
    'smartkillclient': 'window',
    'smartmovewin': 'floating',
    'smartresizewin': 'floating',
    'spawn': 'system',
    'spawn_on_empty': 'system',
    'spawn_shell': 'system',
    'switch_keyboard_layout': 'input',
    'switch_layout': 'layout',
    'switch_proportion_preset': 'layout',
    'switcher': 'focus',
    'tag': 'tag',
    'tag_special_silent': 'tag',
    'tag_special_tag': 'tag',
    'tagcrossmon': 'tag',
    'tagmon': 'monitor',
    'tagsilent': 'tag',
    'tagtoleft': 'tag',
    'tagtoright': 'tag',
    'toggle_all_floating': 'window',
    'toggle_monitor': 'monitor',
    'toggle_named_scratchpad': 'scratchpad',
    'toggle_render_border': 'appearance',
    'toggle_scratchpad': 'scratchpad',
    'toggle_special_tag': 'tag',
    'toggle_trackpad_enable': 'input',
    'togglefakefullscreen': 'window',
    'togglefloating': 'window',
    'togglefullscreen': 'window',
    'togglegaps': 'appearance',
    'toggleglobal': 'window',
    'togglegroupbar': 'group',
    'togglehdr': 'monitor',
    'togglejump': 'layout',
    'togglemaximizescreen': 'window',
    'toggleoverlay': 'window',
    'toggleoverview': 'layout',
    'toggletag': 'tag',
    'toggleview': 'tag',
    'view': 'tag',
    'view_insert': 'tag',
    'viewcrossmon': 'tag',
    'viewnext_have_client': 'tag',
    'viewprev_have_client': 'tag',
    'viewtoleft': 'tag',
    'viewtoleft_have_client': 'tag',
    'viewtoright': 'tag',
    'viewtoright_have_client': 'tag',
    'wakeup_monitor': 'monitor',
    'zoom': 'focus',
}

BINDING_CATEGORY_ORDER = ['window', 'scratchpad', 'focus', 'floating', 'group', 'tag', 'monitor', 'layout', 'input', 'bindings', 'appearance', 'system']

BINDING_CATEGORY_LABELS = {
    'window': 'Window Management',
    'scratchpad': 'Scratchpad',
    'focus': 'Focus & Movement',
    'floating': 'Floating Windows',
    'group': 'Window Groups',
    'tag': 'Tags',
    'monitor': 'Monitors',
    'layout': 'Layouts',
    'input': 'Input',
    'bindings': 'Bindings',
    'appearance': 'Appearance',
    'system': 'System',
}

# Web-UI units shown after slider readouts.
WEB_UNITS = {
    "cursor_size": "px",
    "repeat_rate": "cps",
    "repeat_delay": "ms",
    "gap_inner_horizontal": "px", "gap_inner_vertical": "px",
    "gap_outer_horizontal": "px", "gap_outer_vertical": "px",
    "border_px": "px", "border_radius": "px",
    "blur_params_radius": "px", "blur_params_num_passes": "",
    "shadows_size": "px", "shadows_blur": "px",
    "shadows_position_x": "px", "shadows_position_y": "px",
    "animation_duration_open": "ms", "animation_duration_close": "ms",
    "animation_duration_move": "ms", "animation_duration_focus": "ms",
    "animation_duration_tag": "ms",
    "scratchpad_width_ratio": "", "scratchpad_height_ratio": "",
}

# Mango int-enums the web UI renders as labelled dropdowns.
WEB_ENUM_KEYS = {
    "mouse_accel_profile", "trackpad_accel_profile",
    "mouse_scroll_method", "trackpad_scroll_method",
    "mouse_scroll_button", "mouse_click_method", "trackpad_click_method",
    "mouse_send_events_mode",
}


def apply_web_labels():
    """Adopt the official web settings' labels, descriptions and enum
    dropdowns (option labels while the config keeps storing values)."""
    for opts in SCHEMA.values():
        for opt in opts:
            field = WEB_FIELDS.get(opt.key)
            if field is None:
                continue
            label, desc, options = field
            if label:
                opt.label = label
            if desc:
                opt.desc = desc
            if opt.type == OptDef.CHOICE and options:
                values = [v for v, _l in options]
                # keep the engine's validation list in sync with web options
                if values:
                    opt.choices = values
                    opt.choice_labels = dict(options)
            elif opt.key in WEB_ENUM_KEYS and options:
                # Mango stores 0/1/2; the web renders labelled dropdowns.
                if opt.type == OptDef.INT:
                    opt.type = OptDef.CHOICE
                    opt.min = None
                    opt.max = None
                opt.choices = [v for v, _l in options]
                labels = dict(options)
                # Older MangoWM defaults (e.g. accel profile -1) must remain
                # selectable and valid.
                if str(opt.default) not in opt.choices:
                    opt.choices.insert(0, str(opt.default))
                    labels[str(opt.default)] = "Default"
                opt.choice_labels = labels


apply_web_labels()

SCHEMA_OPTIONS = {}
for _opts in SCHEMA.values():
    for _o in _opts:
        SCHEMA_OPTIONS[_o.key] = _o

MULTI_KEYS = {"exec", "exec_once"}          # may appear many times in config.conf
# Options handled on other pages / by other files — hidden from Appearance etc.
HIDDEN_OPTION_KEYS = {"exec", "exec_once"}


def page_matches(key, needle):
    """True when a page should stay visible for the Ctrl+F search text.

    A page matches when its title/key matches or when any of its settings
    (label, key, section or description) does — so searching "gap" keeps
    Gaps & Borders in the sidebar.
    """
    if not needle:
        return True
    needle = needle.lower().strip()
    if not needle:
        return True
    if needle in PAGE_TITLES.get(key, "").lower() or needle in key:
        return True
    for opt in PAGE_SCHEMA.get(key, []):
        haystack = " ".join((opt.label or "", opt.key or "",
                             opt.group or "", opt.desc or "")).lower()
        if needle in haystack:
            return True
    # List pages match on their record names (bind, window_rule, …).
    record = PAGE_RECORD.get(key)
    if record and needle in record:
        return True
    return False


def option_matches(opt, needle):
    """True when one setting matches the search text (see page_matches)."""
    if not needle:
        return True
    needle = needle.lower().strip()
    haystack = " ".join((opt.label or "", opt.key or "",
                         opt.group or "", opt.desc or "")).lower()
    return needle in haystack


def resolve_page(name):
    """Map a ``--page`` argument to a page key, or None.

    Accepts the internal key (``files``), the display title (``Config
    Files``) and dash/underscore spellings (``window-rules``), all
    case-insensitively.  Pre-2.3 page keys (``windowrules``, ``startup``…)
    are aliased onto the new tree so old commands keep working.
    """
    if not name:
        return None
    raw = name.strip().lower()
    if raw in PAGE_ORDER:
        return raw
    if raw in LEGACY_PAGE_ALIAS:
        return LEGACY_PAGE_ALIAS[raw]
    normalised = raw.replace("-", " ").replace("_", " ")
    for key in PAGE_ORDER:
        title = PAGE_TITLES[key].lower()
        if normalised in (title, title.replace(" ", ""), key.replace("-", " ")):
            return key
    if normalised in LEGACY_TITLE_ALIAS:
        return LEGACY_TITLE_ALIAS[normalised]
    for old, new in LEGACY_PAGE_ALIAS.items():
        if normalised in (old, old.replace("-", " ")):
            return new
    return None


# ---------------------------------------------------------------------------
# MangoConfig — the full config graph (root file + includes)
# ---------------------------------------------------------------------------

class MangoConfig(object):
    """Loads ~/.config/mango/config.conf exactly like MangoWM does:

    * ``source=`` / ``source_optional=`` includes, resolved relative to the
      including file, with ``~/`` and ``$var`` expansion;
    * repeated includes are read again (MangoWM re-reads them; last
      assignment wins);
    * cycles and depth > MAX_INCLUDE_DEPTH are reported, never followed;
    * variables (``var = NAME value``) are global in include order.
    """

    def __init__(self, root=None, schema=None, confirm_external=None):
        self.root_path = os.path.abspath(os.path.expanduser(root or DEFAULT_CONFIG_PATH))
        self.schema = schema or SCHEMA
        self.schema_by_key = dict((o.key, o) for opts in self.schema.values() for o in opts)
        self.confirm_external = confirm_external  # callable([Document]) -> bool
        self.docs = {}            # realpath -> Document (first-seen order)
        self.order = []           # realpaths in include traversal order
        self.issues = []
        self.vars = {}            # NAME -> value, include order
        self.values = {}          # key -> [(doc, line)] last entry wins
        self.exists = False
        self._loaded_stats = {}   # realpath -> (mtime_ns, size)
        self._backed_up = set()
        self.backup_root = os.path.expanduser(BACKUP_ROOT)

    # -- loading ------------------------------------------------------------
    def load(self):
        self.docs, self.order, self.issues = {}, [], []
        self.vars, self.values = {}, {}
        self._loaded_stats = {}
        if not os.path.exists(self.root_path):
            self.exists = False
            _add_issue(self.issues, Issue.WARNING,
                       "config file not found: %s (MangoWM will use its built-in defaults)" % self.root_path)
            return self
        self.exists = True
        self._load_file(self.root_path, depth=0, stack=[], mandatory=True)
        return self

    def _read_doc(self, path):
        doc = Document(path)
        if doc.real in self.docs:
            return self.docs[doc.real]
        try:
            doc.load()
        except OSError as exc:
            _add_issue(self.issues, Issue.ERROR,
                       "cannot read config file: %s" % exc.strerror or str(exc), doc.path)
            return None
        self.docs[doc.real] = doc
        return doc

    def _load_file(self, path, depth, stack, mandatory):
        real = os.path.realpath(os.path.expanduser(path))
        if real in stack:
            chain = " -> ".join(stack[stack.index(real):] + [real])
            _add_issue(self.issues, Issue.ERROR,
                       "include cycle detected: %s" % chain, path)
            return None
        if depth > MAX_INCLUDE_DEPTH:
            _add_issue(self.issues, Issue.ERROR,
                       "include depth exceeds %d (stuck include loop?)" % MAX_INCLUDE_DEPTH, path)
            return None
        doc = self._read_doc(path)
        if doc is None:
            if mandatory:
                _add_issue(self.issues, Issue.ERROR, "missing mandatory include", path)
            return None
        self.order.append(doc.real)
        self._loaded_stats.setdefault(doc.real, (doc.mtime_ns, doc.size))
        stack = stack + [real]
        # Walk the file sequentially: vars defined before an include line are
        # visible to it, exactly like MangoWM.
        for line in doc.lines:
            if isinstance(line, VarLine):
                self.vars[line.name] = line.value
            elif isinstance(line, IncludeLine):
                self._walk_include(doc, line, depth, stack)
            elif isinstance(line, OptionLine):
                expanded = self._expand_vars(line.value)
                self.values.setdefault(line.key, []).append((doc, line, expanded))
        return doc

    def _expand_vars(self, token):
        def sub(match):
            return self.vars.get(match.group(1), match.group(0))
        return re.sub(r"\$([A-Za-z_][A-Za-z0-9_]*)", sub, token)

    def _walk_include(self, doc, line, depth, stack):
        target = self._expand_vars(line.target.strip())
        if target.startswith("~"):
            target = os.path.expanduser(target)
        if not os.path.isabs(target):
            target = os.path.join(os.path.dirname(doc.real), target)
        target = os.path.normpath(target)
        if os.path.exists(target):
            self._load_file(target, depth + 1, stack, mandatory=not line.optional)
        elif line.optional:
            _add_issue(self.issues, Issue.WARNING,
                       "optional include not found (ignored): %s" % target, doc.path)
        else:
            _add_issue(self.issues, Issue.ERROR,
                       "mandatory include not found: %s" % target, doc.path, None)
            _add_issue(self.issues, Issue.ERROR, "this is the line that includes it", doc.path,
                       lineno=None)
            line.comment = (line.comment + "  # MISSING: " + target).strip()

    # -- reading values -------------------------------------------------------
    def get(self, key, default=None):
        """Last-wins value for key, or default.  $vars are already expanded."""
        entries = self.values.get(key)
        if not entries:
            return default
        return entries[-1][2]

    def get_all(self, key):
        return [entry[2] for entry in self.values.get(key, [])]

    def has(self, key):
        return bool(self.values.get(key))

    def unknown_options(self):
        return sorted(k for k in self.values if k not in self.schema_by_key)

    # -- writing values -------------------------------------------------------
    def set(self, key, value):
        """Validate and set an option.  Creates the line in the root file if
        it is not defined anywhere yet."""
        if not isinstance(value, str):
            raise ConfigError("value must be a string, got %r" % (value,))
        opt = self.schema_by_key.get(key)
        if opt is not None:
            ok, err = opt.validate(value)
            if not ok:
                raise ConfigError("%s: %s" % (key, err))
        if not self.exists:
            raise ConfigError("no config file loaded (load() first, or create one)")
        if key in MULTI_KEYS:
            return self.add_value(key, value)
        entries = self.values.get(key)
        if entries:
            doc, line = entries[-1][0], entries[-1][1]
            line.set_value(value)
            doc.dirty = True
            self.values[key][-1] = (doc, line, value)
        else:
            root = self.docs[os.path.realpath(self.root_path)]
            line = OptionLine(key, value)
            root.append(line)
            self.values[key] = [(root, line, value)]
        return True

    def add_value(self, key, value):
        """Append another occurrence of a multi-value option (exec/exec_once)."""
        opt = self.schema_by_key.get(key)
        if opt is not None:
            ok, err = opt.validate(value)
            if not ok:
                raise ConfigError("%s: %s" % (key, err))
        if not self.exists:
            raise ConfigError("no config file loaded")
        root = self.docs[os.path.realpath(self.root_path)]
        line = OptionLine(key, value)
        root.append(line)
        self.values.setdefault(key, []).append((root, line, value))

    def remove_value(self, key, index=-1):
        entries = self.values.get(key)
        if not entries:
            return False
        doc, line = entries[index][0], entries[index][1]
        doc.lines.remove(line)
        doc.dirty = True
        entries.pop(index if index != -1 else -1)
        return True

    def unset(self, key):
        """Delete every assignment of ``key`` so MangoWM uses its default.

        Only that key's lines are removed; comments, blank lines and the
        ordering of everything else are untouched.  Returns True when at
        least one line was removed.
        """
        entries = self.values.get(key)
        if not entries:
            return False
        removed = 0
        seen = set()                      # repeated includes list a line twice
        for doc, line, _value in entries:
            if id(line) in seen:
                continue
            seen.add(id(line))
            try:
                doc.lines.remove(line)
            except ValueError:
                continue
            doc.dirty = True
            removed += 1
        if removed:
            self._reindex()
        return bool(removed)

    def _reindex(self):
        """Rebuild ``vars``/``values`` from the in-memory documents.

        ``self.order`` already holds the flattened include traversal (with
        repeats), so walking it reproduces MangoWM's "last assignment wins"
        and variable expansion rules without touching the disk.
        """
        self.vars = {}
        self.values = {}
        for real in self.order:
            doc = self.docs.get(real)
            if doc is None:
                continue
            for line in doc.lines:
                if isinstance(line, VarLine):
                    self.vars[line.name] = line.value
                elif isinstance(line, OptionLine):
                    self.values.setdefault(line.key, []).append(
                        (doc, line, self._expand_vars(line.value)))

    def validate(self, key, value):
        opt = self.schema_by_key.get(key)
        if opt is None:
            return True, ""
        return opt.validate(value)

    # -- saving ----------------------------------------------------------------
    def changed_files(self):
        return [self.docs[r] for r in self.order if self.docs[r].dirty]

    def save(self):
        """Atomically write every modified file, preserving permissions,
        symlinks, CRLF and final-newline state.  Backs up files before the
        first change of the session."""
        dirty = self.changed_files()
        if not dirty:
            return []
        self._backup(dirty)
        externally_edited = [d for d in dirty if self._changed_on_disk(d)]
        if externally_edited:
            proceed = (self.confirm_external(externally_edited)
                       if self.confirm_external else False)
            if not proceed:
                raise ConfigError(
                    "save cancelled: %s changed outside Mango Settings" %
                    ", ".join(d.path for d in externally_edited))
        for doc in dirty:
            self._write_doc(doc)
        return dirty

    def _changed_on_disk(self, doc):
        try:
            st = os.stat(doc.real)
        except OSError:
            return True
        return (st.st_mtime_ns, st.st_size) != self._loaded_stats.get(doc.real)

    def _backup(self, docs):
        stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
        bdir = os.path.join(self.backup_root, stamp)
        for doc in docs:
            if doc.real in self._backed_up:
                continue
            os.makedirs(bdir, exist_ok=True)
            base = os.path.basename(doc.real)
            dest, n = os.path.join(bdir, base), 1
            while os.path.exists(dest):
                stem, ext = os.path.splitext(base)
                dest = os.path.join(bdir, "%s_%d%s" % (stem, n, ext)); n += 1
            shutil.copy2(doc.real, dest, follow_symlinks=True)
            self._backed_up.add(doc.real)

    def _write_doc(self, doc):
        target = doc.real  # write through symlinks to their target
        text = doc.render_text()
        data = text.encode("utf-8", "surrogateescape")
        directory = os.path.dirname(target) or "."
        try:
            mode = stat.S_IMODE(os.stat(target).st_mode)
        except OSError:
            mode = 0o644
        fd, tmp = tempfile.mkstemp(prefix=".mango-settings-", dir=directory)
        try:
            os.fchmod(fd, mode)
            with os.fdopen(fd, "wb") as fh:
                fh.write(data)
            os.replace(tmp, target)
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise
        st = os.stat(target)
        doc.mtime_ns, doc.size = st.st_mtime_ns, st.st_size
        self._loaded_stats[doc.real] = (doc.mtime_ns, doc.size)
        doc.dirty = False

    # -- misc --------------------------------------------------------------------
    def summary(self):
        errors = [i for i in self.issues if i.severity == Issue.ERROR]
        warnings = [i for i in self.issues if i.severity != Issue.ERROR]
        return errors, warnings


# ---------------------------------------------------------------------------
# List files: bind.conf, rule.conf, monitor.conf
#    bind    = MODS, key, action, params...
#    monitor = NAME, enabled, x, y, width, height, scale, ...
#    window_rule[_once] = class, title, tags, float, ...
# Values may contain commas inside the last field, so only the first three
# commas of a bind (mods, key, action) and a bounded number of fields are split.
# ---------------------------------------------------------------------------

class RuleFile(object):
    """A config file consisting of ``name = field, field, ...`` records."""

    def __init__(self, path):
        self.path = os.path.abspath(os.path.expanduser(path))
        self.real = os.path.realpath(self.path)
        self.exists = os.path.exists(self.real)
        self.raw_lines = []     # (kind, payload)  kind: blank/comment/unknown/record
        self.dirty = False
        self._stats = None
        if self.exists:
            self.load()

    def load(self):
        with open(self.real, "rb") as fh:
            text = fh.read().decode("utf-8", errors="surrogateescape")
        self._stats = (os.stat(self.real).st_mtime_ns, os.stat(self.real).st_size)
        self.raw_lines = []
        for raw in text.split("\n"):
            s = raw.strip()
            if not s or s.startswith("#"):
                self.raw_lines.append(["pass", raw])
                continue
            body, _c = _split_inline_comment(s)
            m = _OPT_RE.match(body.strip())
            if m:
                fields = [f.strip() for f in m.group(2).split(",")]
                self.raw_lines.append(["record", (m.group(1), fields, s)])
            else:
                self.raw_lines.append(["pass", raw])
        return self

    def records(self, name):
        """[(index, fields)] for every record named ``name``."""
        return [(i, payload[1][1]) for i, payload in enumerate(self.raw_lines)
                if payload[0] == "record" and payload[1][0] == name]

    def add(self, name, fields):
        self.raw_lines.append(["record", (name, fields,
                                          "%s = %s" % (name, ", ".join(fields)))])
        self.dirty = True

    def update(self, index, fields):
        payload = self.raw_lines[index][1]
        name = payload[0]
        self.raw_lines[index] = ["record", (name, fields,
                                            "%s = %s" % (name, ", ".join(fields)))]
        self.dirty = True

    def remove(self, index):
        del self.raw_lines[index]
        self.dirty = True

    def render_text(self):
        out = []
        for kind, payload in self.raw_lines:
            out.append(payload if kind == "pass" else payload[2])
        return "\n".join(out) + "\n"

    def save(self):
        if not self.dirty:
            return
        data = self.render_text().encode("utf-8", "surrogateescape")
        directory = os.path.dirname(self.real) or "."
        mode = stat.S_IMODE(os.stat(self.real).st_mode) if self.exists else 0o644
        fd, tmp = tempfile.mkstemp(prefix=".mango-settings-", dir=directory)
        os.fchmod(fd, mode)
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
        os.replace(tmp, self.real)
        self.dirty = False
        self.exists = True
        self.load()

    # -- domain helpers ----------------------------------------------------
    @staticmethod
    def bind_conflicts(records):
        """records: list of (index, fields).  Two binds with the same
        modifiers+key are a conflict."""
        seen, out = {}, []
        for index, fields in records:
            if len(fields) < 3:
                out.append((index, "malformed bind (need MODS, key, action)"))
                continue
            ident = (fields[0].lower().replace(" ", ""), fields[1].lower().replace(" ", ""))
            if ident in seen:
                out.append((index, "conflicts with line for %s %s" % (fields[0], fields[1])))
            else:
                seen[ident] = index
        return out


# ---------------------------------------------------------------------------
# System helpers (no GTK): mmsg, system info, notification daemons
# ---------------------------------------------------------------------------

def run_cmd(argv, timeout=5):
    """Run a command; return (returncode, stdout).  Missing tools give rc -1."""
    try:
        proc = subprocess.run(argv, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                              timeout=timeout)
        return proc.returncode, proc.stdout.decode("utf-8", "replace").strip()
    except (OSError, subprocess.TimeoutExpired) as exc:
        return -1, str(exc)


def find_tool(name):
    return shutil.which(name)


def mmsg_available():
    return find_tool("mmsg") is not None


def reload_mango():
    """Ask MangoWM to reload via mmsg (falls back to mangoctl-free IPC)."""
    if mmsg_available():
        rc, out = run_cmd(["mmsg", "reload"])
        if rc == 0:
            return True, ""
        return False, out or "mmsg reload failed"
    return False, "mmsg not found (install MangoWM's mmsg to reload from here)"


def connected_monitors():
    """Best-effort connected display list via mmsg.  Returns [(name, desc)]."""
    if not mmsg_available():
        return []
    for argv in (["mmsg", "monitors"], ["mmsg", "--monitors"], ["mmsg", "list-monitors"]):
        rc, out = run_cmd(argv)
        if rc == 0 and out:
            monitors = []
            for line in out.splitlines():
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                parts = line.split(None, 1)
                name = parts[0]
                desc = parts[1] if len(parts) > 1 else ""
                if re.match(r"^[A-Z]+\d*[-0-9]*$", name):   # DP-1, HDMI-A-1, eDP-1...
                    monitors.append((name, desc))
            if monitors:
                return monitors
    return []


def mango_version():
    """MangoWM's version via mmsg, cleaned for display in the sidebar."""
    if mmsg_available():
        for argv in (["mmsg", "version"], ["mmsg", "--version"]):
            rc, out = run_cmd(argv)
            if rc != 0 or not out:
                continue
            first = out.splitlines()[0].strip()
            # mmsg answers with JSON (or an error object) in some versions.
            if first.startswith("{"):
                try:
                    import json
                    data = json.loads(first)
                    text = (data.get("version") or data.get("mango")
                            or data.get("output") or "")
                    if text and not data.get("error"):
                        return str(text).strip()
                except ValueError:
                    pass
                continue
            if "error" in first.lower() or "unknown" in first.lower():
                continue
            return first
    return "unknown (mmsg not found)"


def notification_daemons():
    """Detect running notification daemons.  Returns [(name, running, config)]."""
    candidates = [
        ("swaync", ["swaync"], "~/.config/swaync/config.json"),
        ("mako", ["mako"], "~/.config/mako/config"),
        ("dunst", ["dunst"], "~/.config/dunst/dunstrc"),
        ("fnott", ["fnott"], "~/.config/fnott/fnott.ini"),
    ]
    found = []
    for name, proc_names, cfg in candidates:
        rc, _ = run_cmd(["pgrep", "-x", proc_names[0]], timeout=2)
        running = rc == 0 or any(find_tool(p) for p in proc_names)
        found.append((name, running, os.path.expanduser(cfg)))
    return found


def system_info():
    info = []
    info.append(("MangoWM / mmsg", mango_version()))
    if os.path.exists("/etc/os-release"):
        data = {}
        with open("/etc/os-release") as fh:
            for line in fh:
                if "=" in line:
                    k, _, v = line.partition("=")
                    data[k.strip()] = v.strip().strip('"')
        info.append(("OS", data.get("PRETTY_NAME", data.get("NAME", "?"))))
    rc, kernel = run_cmd(["uname", "-r"])
    info.append(("Kernel", kernel if rc == 0 else "?"))
    try:
        with open("/proc/cpuinfo") as fh:
            model = next((l.split(":", 1)[1].strip() for l in fh
                          if l.startswith("model name")), "?")
    except OSError:
        model = "?"
    info.append(("CPU", model))
    try:
        with open("/proc/meminfo") as fh:
            total = next(l.split(":", 1)[1].strip() for l in fh if l.startswith("MemTotal"))
            avail = next(l.split(":", 1)[1].strip() for l in fh if l.startswith("MemAvailable"))
        info.append(("Memory", "%s total / %s available" % (total, avail)))
    except (OSError, StopIteration):
        info.append(("Memory", "?"))
    try:
        with open("/proc/uptime") as fh:
            secs = int(float(fh.read().split()[0]))
        info.append(("Uptime", "%d d %d h %d m" % (secs // 86400, secs % 86400 // 3600,
                                                   secs % 3600 // 60)))
    except (OSError, ValueError, IndexError):
        info.append(("Uptime", "?"))
    info.append(("Config", os.path.expanduser(DEFAULT_CONFIG_PATH)))
    return info


# ---------------------------------------------------------------------------
# GSettings helpers (theming page / app theme)
# ---------------------------------------------------------------------------

def gsettings_get(schema, key):
    rc, out = run_cmd(["gsettings", "get", schema, key])
    if rc != 0:
        return None
    return out.strip().strip("'\"")


def gsettings_set(schema, key, value):
    rc, out = run_cmd(["gsettings", "set", schema, key, value])
    return rc == 0, out


GTK_THEME_SCHEMA = "org.gnome.desktop.interface"


def write_text_atomic(path, text, backup=True, backup_root=None):
    """Write ``text`` to ``path`` atomically, preserving permissions.

    Symlinks are followed (the target is replaced, not the link), a copy of
    the old contents is kept under the session backup directory, and the
    new file only appears via ``os.replace`` so a crash cannot truncate a
    config file.

    Returns (ok, message).
    """
    real = os.path.realpath(os.path.expanduser(path))
    try:
        mode = os.stat(real).st_mode & 0o7777
    except OSError:
        mode = 0o644
    if backup:
        try:
            root = os.path.expanduser(backup_root or BACKUP_ROOT)
            stamp = time.strftime("%Y%m%d-%H%M%S")
            bdir = os.path.join(root, stamp)
            os.makedirs(bdir, exist_ok=True)
            with open(real, "rb") as fh:
                old = fh.read()
            with open(os.path.join(bdir, os.path.basename(real)), "wb") as fh:
                fh.write(old)
        except OSError:
            pass                      # never block a save on backup trouble
    tmp = real + ".mango-settings-tmp"
    try:
        with open(tmp, "w", errors="surrogateescape", newline="") as fh:
            fh.write(text)
        os.chmod(tmp, mode)
        os.replace(tmp, real)
    except OSError as exc:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        return False, "Could not write %s: %s" % (path, exc)
    return True, ""


# ---------------------------------------------------------------------------
# Wallpaper (background) helpers — no GTK imports needed
# ---------------------------------------------------------------------------

WALLPAPER_BACKENDS = {
    "swaybg": ["swaybg", "-i", "{path}", "-m", "{mode}"],
    "swww": ["swww", "img", "{path}"],
    "feh": ["feh", "--bg-{mode2}", "{path}"],   # X11 only
}

# Matches a wallpaper line in autostart.sh: swaybg -i <file>, swww img <file>,
# feh --bg-... <file> — only non-comment lines.
_WALLPAPER_LINE_RE = re.compile(
    r"^\s*((?:[^#\n]*?)\b(?:swaybg|swww)\b[^\n]*|[^#\n]*\bfeh\b[^\n]*--bg[^\n]*)$",
    re.IGNORECASE)


def detect_wallpaper_backends():
    """Return the wallpaper backends available on this system."""
    found = []
    for name in WALLPAPER_BACKENDS:
        rc, _out = run_cmd(["which", name], timeout=2)
        if rc == 0:
            found.append(name)
    return found


def apply_wallpaper(path, mode="fill", backend="swaybg"):
    """Apply a wallpaper immediately, replacing any running instance.

    Returns (ok, message).  The spawned daemon is detached from this
    process so the wallpaper survives the settings app closing.
    """
    template = WALLPAPER_BACKENDS.get(backend)
    if not template:
        return False, "Unknown wallpaper backend %r" % backend
    rc, out = run_cmd(["which", backend], timeout=2)
    if rc != 0:
        return False, "%s is not installed" % backend
    fullpath = os.path.realpath(os.path.expanduser(path))
    if not os.path.isfile(fullpath):
        return False, "No such image file: %s" % path

    # Stop the previous daemon first so the new wallpaper is visible.
    # (Older swaybg/swww instances stay mapped and hide the new one.)
    run_cmd(["pkill", "-x", "swaybg"], timeout=2)
    run_cmd(["pkill", "-f", "swww"], timeout=2)

    argv = [a.format(path=fullpath, mode=mode,
                     mode2={"fill": "fill", "fit": "max",
                            "stretch": "scale", "tile": "tile",
                            "center": "center"}.get(mode, "max"))
            for a in template]
    try:
        subprocess.Popen(argv, stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL, start_new_session=True)
    except OSError as exc:
        return False, "Could not start %s: %s" % (backend, exc)
    return True, ""


def autostart_wallpaper_line(path, mode, backend="swaybg"):
    """The shell line written into autostart.sh for the chosen wallpaper."""
    marker = {"swaybg": "swaybg -i {p} -m {m}",
              "swww": "swww img {p}",
              "feh": "feh --bg-{m2} {p}"}[backend]
    m2 = {"fill": "fill", "fit": "max", "stretch": "scale",
          "tile": "tile", "center": "center"}.get(mode, "max")
    return ("%s >/dev/null 2>&1 &"
            % marker.format(p=path, m=mode, m2=m2))


def set_autostart_wallpaper(path, mode, backend="swaybg",
                            autostart_path=DEFAULT_AUTOSTART_PATH):
    """Persist the wallpaper by editing the user's autostart script.

    Replaces the first existing wallpaper line (swaybg / swww / feh), or
    appends a managed line when none exists.  Preserves everything else:
    comments, blank lines, ordering, permissions and the trailing newline.
    A backup is saved before the first modification.

    Returns (ok, message).
    """
    fullpath = os.path.expanduser(autostart_path)
    target = os.path.realpath(fullpath)
    if not os.path.isfile(target):
        return False, ("Autostart script not found at %s — MangoWM runs it via "
                       "exec_once, so the wallpaper would not persist." %
                       fullpath)
    try:
        with open(target, "r", errors="replace") as fh:
            text = fh.read()
    except OSError as exc:
        return False, "Cannot read %s: %s" % (fullpath, exc)

    new_line = autostart_wallpaper_line(path, mode, backend)
    lines = text.splitlines(keepends=True)
    replaced = False
    for i, line in enumerate(lines):
        if _WALLPAPER_LINE_RE.match(line):
            eol = "\n" if line.endswith("\n") else ""
            lines[i] = new_line + eol
            replaced = True
            break
    if not replaced:
        if text and not text.endswith("\n"):
            text += "\n"
        text += ("\n# wallpaper (managed by Mango Settings)\n%s\n" % new_line)
    else:
        text = "".join(lines)

    # Session backup before touching the user's script.
    try:
        stamp = time.strftime("%Y%m%d-%H%M%S")
        bdir = os.path.expanduser(os.path.join(BACKUP_ROOT, stamp))
        os.makedirs(bdir, exist_ok=True)
        with open(target, "rb") as fh:
            data = fh.read()
        with open(os.path.join(bdir, "autostart.sh"), "wb") as fh:
            fh.write(data)
        # Atomic write, preserving permissions.
        st = os.stat(target)
        tmp = target + ".mango-settings-tmp"
        with open(tmp, "w", errors="replace") as fh:
            fh.write(text)
        os.chmod(tmp, st.st_mode & 0o7777)
        os.replace(tmp, target)
    except OSError as exc:
        return False, "Could not write %s: %s" % (fullpath, exc)
    return True, ""


# ---------------------------------------------------------------------------
# Color helpers (0xRRGGBBAA <-> Gdk.RGBA) for the web-style color picker
# ---------------------------------------------------------------------------

def _parse_mango_color(text):
    """Parse MangoWM's 0xRRGGBB[AA] / #RRGGBB[AA] into a Gdk.RGBA-like tuple.

    Returns (r, g, b, a) floats in 0..1, or None when unparsable.  Pure
    Python so it stays testable without GTK.
    """
    if not text:
        return None
    m = re.match(r"^(?:0x|#|rgb\()?([0-9a-fA-F]{6})([0-9a-fA-F]{2})?\)?$", text.strip())
    if not m:
        return None
    hexpart, aapart = m.group(1), m.group(2)
    r = int(hexpart[0:2], 16) / 255.0
    g = int(hexpart[2:4], 16) / 255.0
    b = int(hexpart[4:6], 16) / 255.0
    a = (int(aapart, 16) / 255.0) if aapart else 1.0
    return (r, g, b, a)


def _mango_color_from_rgba(rgba):
    """Format a Gdk.RGBA as MangoWM's 0xRRGGBBAA string."""
    def byte(v):
        return max(0, min(255, int(round(v * 255.0))))
    return "0x%02x%02x%02x%02x" % (byte(rgba.red), byte(rgba.green),
                                   byte(rgba.blue), byte(rgba.alpha))

# ---------------------------------------------------------------------------
# GTK user interface
# ---------------------------------------------------------------------------

def run_gui(args):
    try:
        import gi  # noqa: F401
        gi.require_version("Gtk", "3.0")
        from gi.repository import Gtk, Gdk, Gio, GLib  # noqa: F401
    except (ImportError, ValueError, KeyError) as exc:
        sys.stderr.write(
            "Mango Settings needs GTK 3 and PyGObject:\n"
            "  Arch/Manjaro: sudo pacman -S python-gobject gtk3\n"
            "  Debian/Ubuntu: sudo apt install python3-gi gir1.2-gtk-3.0\n"
            "  Fedora: sudo dnf install python3-gobject gtk3\n"
            "  Alpine:  apk add py3-gobject3 gtk+3.0\n"
            "  Void:    xbps-install python3-gobject gtk+3\n"
            "  (%s)\n" % exc)
        return 1

    # Icon themes differ: Adwaita ships symbolic icons, Papirus mostly plain
    # ones.  Resolve once per page, preferring the symbolic variant (it follows
    # the app theme colour), then the plain name, then a neutral fallback.
    _ICON_CACHE = {}

    def page_icon(key, size=24):
        if key in _ICON_CACHE:
            return _ICON_CACHE[key]
        theme = Gtk.IconTheme.get_default()
        names = PAGE_ICON_CANDIDATES.get(key, [])
        # Symbolic variants of every candidate first (they follow the theme
        # colour), then the plain names, then neutral fallbacks.
        ordered = ([n + "-symbolic" for n in names] + list(names) +
                   ["preferences-other-symbolic", "preferences-other",
                    "emblem-system-symbolic"])
        for candidate in ordered:
            if theme.lookup_icon(candidate, size, 0) is not None:
                _ICON_CACHE[key] = candidate
                return candidate
        _ICON_CACHE[key] = "image-missing"
        return "image-missing"

    # ------------------------------------------------------------------ pages
    class PageBase(Gtk.Box):
        """Common scaffolding: a web-style page header (title + description)
        and a scrollable content column that reports changes upward."""

        page_key = None

        def __init__(self, app):
            super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=6)
            self.app = app
            self.set_border_width(18)
            self.changed = False
            # Panel header: large title + muted description, like the web
            # settings' PanelHeader.
            head = Gtk.Label(xalign=0)
            head.set_markup("<span size='x-large' weight='bold'>%s</span>"
                            % GLib.markup_escape_text(PAGE_TITLES[self.page_key]))
            head.get_style_context().add_class("ms-page-title")
            self.pack_start(head, False, False, 0)
            if PAGE_DESCRIPTIONS.get(self.page_key):
                desc = Gtk.Label(xalign=0)
                desc.set_markup("<span size='small' alpha='75%%'>%s</span>"
                                % GLib.markup_escape_text(
                                    PAGE_DESCRIPTIONS[self.page_key]))
                desc.get_style_context().add_class("ms-page-desc")
                self.pack_start(desc, False, False, 0)
            self.content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
            scroll = Gtk.ScrolledWindow()
            # Horizontal scrolling is the fallback for very narrow windows
            # (MangoWM may size floating windows below GTK's minimum).
            scroll.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
            scroll.add(self.content)
            self.pack_start(scroll, True, True, 4)
            self.build()

        def build(self):
            pass

        def mark_changed(self, *_a):
            self.changed = True
            self.app.on_page_changed(self)

        def unsaved(self):
            return self.changed

        def clear_dirty(self):
            self.changed = False

        def add_note(self, text):
            label = Gtk.Label(xalign=0)
            label.set_markup("<small>%s</small>" % GLib.markup_escape_text(text))
            label.set_line_wrap(True)
            label.get_style_context().add_class("ms-note")
            self.content.pack_start(label, False, False, 2)

        def add_card(self, title=None):
            """A rounded titled card with a divider and a row container.

            Mirrors the web settings' SectionCard primitive.  Returns the
            body box that rows (or any content) are packed into.
            """
            card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
            card.get_style_context().add_class("ms-card")
            if title:
                head = Gtk.Label(xalign=0)
                head.set_markup(GLib.markup_escape_text(title))
                head.get_style_context().add_class("ms-card-title")
                head.set_margin_start(14)
                head.set_margin_top(10)
                head.set_margin_bottom(8)
                card.pack_start(head, False, False, 0)
                card.pack_start(Gtk.Separator(), False, False, 0)
            body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
            body.set_border_width(8)
            body.get_style_context().add_class("ms-card-body")
            card.pack_start(body, False, False, 0)
            self.content.pack_start(card, False, False, 0)
            return body

        def add_frame(self, title=None):
            """Backwards-compatible alias for :meth:`add_card`."""
            return self.add_card(title)

    class SchemaPage(PageBase):
        """Generated from the schema: web-style rows inside titled cards."""

        def __init__(self, app, page_key):
            self.page_key = page_key
            super().__init__(app)

        def build(self):
            current_group = None
            self.widgets = {}
            self.row_labels = {}
            needle = (getattr(self.app, "search_needle", "") or "").lower().strip()
            # A page-title match shows the whole page; otherwise only matching
            # rows are listed so Ctrl+F can search *settings*, not just pages.
            title_match = bool(needle and needle in PAGE_TITLES[self.page_key].lower())
            opts = PAGE_SCHEMA.get(self.page_key, [])
            total = len(opts)

            if needle and not title_match:
                matched = [o for o in opts if self._matches(o, needle)]
                if matched:
                    label = Gtk.Label(xalign=0)
                    label.set_markup("<small>%d of %d settings match \u201c%s\u201d</small>"
                                     % (len(matched), total,
                                        GLib.markup_escape_text(needle)))
                    label.get_style_context().add_class("ms-note")
                    self.content.pack_start(label, False, False, 2)
                else:
                    self.add_note("No settings on this page match \u201c%s\u201d."
                                  % needle)

            for opt in opts:
                if opt.key in HIDDEN_OPTION_KEYS:
                    continue
                if needle and not title_match and not self._matches(opt, needle):
                    continue
                # Start a new titled card whenever the section changes.
                if opt.group != current_group:
                    body = self.add_card(opt.group)
                    listbox = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
                    listbox.get_style_context().add_class("ms-rows")
                    body.pack_start(listbox, False, False, 0)
                    current_group = opt.group
                listbox.add(self._make_row(opt))
            if not self.widgets:
                self.add_note("Nothing to configure here.")
            self.show_all()

        def rebuild(self):
            """Re-render the page from the config (used after a reset)."""
            for child in list(self.content.get_children()):
                self.content.remove(child)
            self.build()

        @staticmethod
        def _matches(opt, needle):
            return option_matches(opt, needle)

        def _make_row(self, opt):
            """One web-style settings row: label + description on the left,
            control on the right, hover-visible reset button."""
            row = Gtk.ListBoxRow()
            row.get_style_context().add_class("ms-row")
            row.set_activatable(False)
            box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
            box.set_margin_start(8)
            box.set_margin_end(8)
            box.set_margin_top(8)
            box.set_margin_bottom(8)
            row.add(box)

            # Left: label + muted description.
            left = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
            label = Gtk.Label(xalign=0, hexpand=True, ellipsize=3)
            self._decorate_label(label, opt)
            label.get_style_context().add_class("ms-row-label")
            left.pack_start(label, False, False, 0)
            if opt.desc:
                desc = Gtk.Label(xalign=0)
                desc.set_markup("<span size='small' alpha='70%%'>%s</span>"
                                % GLib.markup_escape_text(opt.desc))
                desc.get_style_context().add_class("ms-row-desc")
                desc.set_line_wrap(True)
                left.pack_start(desc, False, False, 0)
            box.pack_start(left, True, True, 0)
            self.row_labels[opt.key] = label

            # Right: control + reset.
            right = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6,
                            valign=Gtk.Align.CENTER)
            widget = self._make_widget(opt)
            self.widgets[opt.key] = (opt, widget)
            right.pack_start(widget, False, False, 0)
            reset = Gtk.Button.new_from_icon_name(
                "edit-undo-symbolic", Gtk.IconSize.MENU)
            reset.set_relief(Gtk.ReliefStyle.NONE)
            reset.get_style_context().add_class("ms-reset")
            reset.set_tooltip_text("Reset to MangoWM default (%s)" % opt.default)
            reset.set_sensitive(self.app.config.get(opt.key) is not None)
            reset.connect("clicked", self.on_reset, opt)
            right.pack_start(reset, False, False, 0)
            box.pack_end(right, False, False, 0)
            return row

        def _decorate_label(self, label, opt):
            """Bold = explicitly set in the config; plain = MangoWM default."""
            entries = self.app.config.values.get(opt.key)
            title = GLib.markup_escape_text(opt.label)
            if entries:
                doc, line, _value = entries[-1]
                label.set_markup(title)
                label.get_style_context().add_class("set-in-config")
                label.set_tooltip_text("Set in %s%s" % (
                    os.path.basename(doc.path),
                    (" line %d" % (doc.lines.index(line) + 1))
                    if line in doc.lines else ""))
            else:
                label.set_text(opt.label)
                label.get_style_context().remove_class("set-in-config")
                label.set_tooltip_text("Not in your config \u2014 using MangoWM's "
                                       "default: %s" % opt.default)

        def on_reset(self, _btn, opt):
            old = self.app.config.get(opt.key)
            if not self.app.config.unset(opt.key):
                return
            self.mark_changed()
            self.rebuild()
            self.app.record_config_undo(opt.key, old)

        def _current(self, opt):
            val = self.app.config.get(opt.key)
            if val is None:
                val = opt.default
            return val

        def _make_widget(self, opt):
            if opt.type == OptDef.BOOL:
                # Web-style toggle with an On/Off text indicator.
                wrap = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
                state = Gtk.Label()
                active = (self._current(opt) or "0") == "1"
                state.set_text("On" if active else "Off")
                state.get_style_context().add_class(
                    "ms-state-on" if active else "ms-state-off")
                wrap.pack_start(state, False, False, 0)
                w = Gtk.Switch(valign=Gtk.Align.CENTER)
                w.set_active(active)
                w.connect("notify::active", self._on_bool, opt, state)
                wrap.pack_start(w, False, False, 0)
                return wrap
            if opt.type in (OptDef.INT, OptDef.FLOAT):
                # Web-style slider with a numeric readout.
                wrap = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
                lo = float(opt.min if opt.min is not None else 0)
                hi = float(opt.max if opt.max is not None else 100000)
                cur = float(self._current(opt) or opt.default)
                step = 1.0 if opt.type == OptDef.INT else max(0.01, (hi - lo) / 100.0)
                adj = Gtk.Adjustment(cur, lo, hi, step, step * 10, 0)
                w = Gtk.Scale(orientation=Gtk.Orientation.HORIZONTAL,
                              adjustment=adj, draw_value=False, width_request=110)
                w.set_digits(0 if opt.type == OptDef.INT else 2)
                readout = Gtk.Label(
                    label=(("%d" % round(cur)) if opt.type == OptDef.INT
                           else ("%.2f" % cur).rstrip("0").rstrip("."))
                          + ((" " + WEB_UNITS[opt.key])
                             if WEB_UNITS.get(opt.key) else ""))
                readout.set_width_chars(5)
                readout.get_style_context().add_class("ms-readout")
                wrap.pack_start(w, False, False, 0)
                wrap.pack_start(readout, False, False, 0)

                def update_readout(_adj):
                    v = adj.get_value()
                    text = (("%d" % round(v)) if opt.type == OptDef.INT
                            else ("%.2f" % v).rstrip("0").rstrip("."))
                    unit = WEB_UNITS.get(opt.key)
                    if unit:
                        text += " " + unit
                    readout.set_text(text)

                def commit(_src):
                    update_readout(adj)
                    self._on_number(adj, opt)

                adj.connect("value-changed", update_readout)
                w.connect("button-release-event",
                          lambda _s, _e: (commit(None), False)[1])
                w.connect("key-press-event", lambda _s, e:
                          (commit(None), False)[1]
                          if e.keyval in (Gdk.KEY_Return, Gdk.KEY_KP_Enter)
                          else False)
                w.connect("scroll-event", lambda _s, _e:
                          (GLib.timeout_add(400, commit, None), False)[1])
                return wrap
            if opt.type == OptDef.CHOICE:
                # Labelled dropdown: the combo shows display labels while the
                # config keeps storing the raw values (web-settings style).
                if opt.choice_labels:
                    store = Gtk.ListStore(str, str)
                    for value in opt.choices:
                        store.append([opt.choice_labels.get(value, value), value])
                    w = Gtk.ComboBox(model=store)
                    cell = Gtk.CellRendererText()
                    w.pack_start(cell, True)
                    w.add_attribute(cell, "text", 0)
                    cur = self._current(opt) or opt.default
                    if cur not in opt.choices:
                        cur = opt.default
                    index = opt.choices.index(cur) if cur in opt.choices else 0
                    w.set_active(index)
                    w.connect("changed", self._on_choice, opt)
                    return w
                w = Gtk.ComboBoxText()
                for c in opt.choices:
                    w.append_text(c)
                cur = self._current(opt) or opt.default
                if opt.choices and cur not in opt.choices:
                    cur = opt.default
                w.set_active(opt.choices.index(cur) if cur in opt.choices else 0)
                w.connect("changed", self._on_choice, opt)
                return w
            if opt.type == OptDef.COLOR:
                wrap = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
                cur = self._current(opt) or ""
                w = Gtk.Entry(width_chars=10)
                w.set_text(cur)
                w.connect("changed", self._on_text, opt)
                wrap.pack_start(w, False, False, 0)
                picker = Gtk.ColorButton()
                picker.set_tooltip_text("Pick a color (saved as 0xRRGGBBAA)")
                parsed = _parse_mango_color(cur)
                if parsed is not None:
                    rgba = Gdk.RGBA()
                    rgba.red, rgba.green, rgba.blue, rgba.alpha = parsed
                    picker.set_rgba(rgba)
                picker.connect("color-set", self._on_color_pick, opt, w)
                wrap.pack_start(picker, False, False, 0)
                return wrap
            w = Gtk.Entry(width_chars=16)
            w.set_text(self._current(opt) or "")
            w.connect("changed", self._on_text, opt)
            return w

        def _apply(self, opt, value):
            try:
                old = self.app.config.get(opt.key)
                self.app.config.set(opt.key, value)
                self.mark_changed()
                self.app.record_config_undo(opt.key, old)
                # Keep the "set in file" marker in sync.
                label = self.row_labels.get(opt.key)
                if label is not None:
                    self._decorate_label(label, opt)
            except ConfigError as exc:
                self.app.error_dialog(str(exc))

        def _on_bool(self, sw, _pspec, opt, state):
            active = sw.get_active()
            state.set_text("On" if active else "Off")
            ctx = state.get_style_context()
            ctx.remove_class("ms-state-on")
            ctx.remove_class("ms-state-off")
            ctx.add_class("ms-state-on" if active else "ms-state-off")
            self._apply(opt, "1" if active else "0")

        def _on_number(self, adj, opt):
            v = adj.get_value()
            if opt.type == OptDef.INT:
                self._apply(opt, str(int(round(v))))
            else:
                self._apply(opt, ("%f" % v).rstrip("0").rstrip("."))

        def _on_choice(self, combo, opt):
            if opt.choice_labels:
                model = combo.get_model()
                value = model[combo.get_active()][1]
                if value is not None:
                    self._apply(opt, value)
                return
            text = combo.get_active_text()
            if text:
                self._apply(opt, text)

        def _on_color_pick(self, button, opt, entry):
            if opt is None:
                return
            text = _mango_color_from_rgba(button.get_rgba())
            entry.handler_block_by_func(self._on_text)
            entry.set_text(text)
            entry.handler_unblock_by_func(self._on_text)
            self._apply(opt, text)

        def _on_text(self, entry, opt):
            self._apply(opt, entry.get_text())

    class RuleListPage(PageBase):
        """Editor for a RuleFile: list of records with add/edit/delete,
        shown as rows inside a web-style card with a terminal-style add row."""

        record_name = None

        def __init__(self, app):
            super().__init__(app)

        def rules(self):
            return self.app.rule_file(self.rule_file_attr()).records(self.record_name)

        def build(self):
            self.listbox = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
            self.listbox.set_selection_mode(Gtk.SelectionMode.NONE)
            self.listbox.get_style_context().add_class("ms-rows")
            body = self.add_card(self.card_title())
            body.pack_start(self.listbox, False, False, 0)
            body.pack_start(self.make_add_row(), False, False, 4)
            self.rebuild()

        def card_title(self):
            return self.record_name

        def placeholder(self):
            return ""

        def make_add_row(self):
            """Terminal-style input row: $ [entry] [Add]."""
            bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
            bar.get_style_context().add_class("ms-add-row")
            prompt = Gtk.Label(label="$")
            prompt.get_style_context().add_class("ms-prompt")
            bar.pack_start(prompt, False, False, 0)
            self.add_entry = Gtk.Entry(hexpand=True)
            self.add_entry.set_placeholder_text(self.placeholder())
            self.add_entry.connect("activate", self.on_entry_activate)
            bar.pack_start(self.add_entry, True, True, 0)
            add = Gtk.Button.new_from_icon_name("list-add-symbolic",
                                                Gtk.IconSize.BUTTON)
            add.set_tooltip_text("Add %s" % self.record_name)
            add.get_style_context().add_class("ms-add-button")
            add.connect("clicked", self.on_entry_activate)
            bar.pack_start(add, False, False, 0)
            return bar

        def on_entry_activate(self, _widget, *_a):
            self.add_from_entry()

        def add_from_entry(self):
            """Turn the add row's text into a new record; subclasses decide."""
            raise NotImplementedError

        def rule_file_attr(self):
            raise NotImplementedError

        def field_labels(self):
            raise NotImplementedError

        def edit_dialog(self, fields=None, on_ok=None):
            dlg = Gtk.Dialog(title=self.record_name.capitalize(),
                             transient_for=self.get_toplevel(), modal=True)
            dlg.add_buttons("_Cancel", Gtk.ResponseType.CANCEL,
                            "_Save", Gtk.ResponseType.OK)
            grid = Gtk.Grid(column_spacing=8, row_spacing=8)
            grid.set_border_width(12)
            entries = []
            labels = self.field_labels()
            for i, label in enumerate(labels):
                grid.attach(Gtk.Label(label=label, xalign=0), 0, i, 1, 1)
                entry = Gtk.Entry(width_chars=32)
                entry.set_text(fields[i] if fields and i < len(fields) else "")
                grid.attach(entry, 1, i, 1, 1)
                entries.append(entry)
            dlg.get_content_area().add(grid)
            dlg.show_all()
            if dlg.run() == Gtk.ResponseType.OK:
                values = [e.get_text().strip() for e in entries]
                dlg.destroy()
                on_ok(values)
                return True
            dlg.destroy()
            return False

        def rebuild(self):
            for child in self.listbox.get_children():
                self.listbox.remove(child)
            records = self.rules()
            conflicts = dict(RuleFile.bind_conflicts(records)
                             if self.record_name == "bind" else [])
            for index, fields in records:
                row = Gtk.ListBoxRow()
                row.get_style_context().add_class("ms-row")
                box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
                box.set_margin_start(10)
                box.set_margin_end(10)
                box.set_margin_top(6)
                box.set_margin_bottom(6)
                text = ", ".join(fields)[:96]
                label = Gtk.Label(label=text, xalign=0, hexpand=True, ellipsize=3)
                label.get_style_context().add_class("ms-row-label")
                box.pack_start(label, True, True, 8)
                if index in conflicts:
                    warn = Gtk.Label()
                    warn.set_markup("<span foreground='orange'>\u26a0 %s</span>"
                                    % GLib.markup_escape_text(conflicts[index]))
                    box.pack_start(warn, False, False, 4)
                edit = Gtk.Button.new_from_icon_name("document-edit-symbolic",
                                                     Gtk.IconSize.MENU)
                edit.set_relief(Gtk.ReliefStyle.NONE)
                edit.set_tooltip_text("Edit")
                edit.connect("clicked", self.on_edit, index)
                box.pack_start(edit, False, False, 2)
                delete = Gtk.Button.new_from_icon_name("user-trash-symbolic",
                                                       Gtk.IconSize.MENU)
                delete.set_relief(Gtk.ReliefStyle.NONE)
                delete.get_style_context().add_class("destructive-action")
                delete.set_tooltip_text("Delete")
                delete.connect("clicked", self.on_delete, index)
                box.pack_start(delete, False, False, 2)
                row.add(box)
                self.listbox.add(row)
            self.listbox.show_all()
            if not records:
                self.add_note("No %s entries yet." % self.record_name)

        def _apply_and_record(self, mutation):
            """Apply a RuleFile mutation and push an undoable change."""
            rf = self.app.rule_file(self.rule_file_attr())
            before = list(rf.raw_lines)
            mutation()
            after = list(rf.raw_lines)
            self.app.record_change(
                lambda: self.app.rule_restore(self.rule_file_attr(), before),
                lambda: self.app.rule_restore(self.rule_file_attr(), after))
            rf.save()
            self.clear_dirty()
            self.rebuild()

        def on_add(self, _btn):
            self.edit_dialog(on_ok=self._added)

        def _added(self, values):
            self._apply_and_record(
                lambda: self.app.rule_file(self.rule_file_attr())
                        .add(self.record_name, values))

        def on_edit(self, _btn, index):
            fields = self.app.rule_file(self.rule_file_attr()).raw_lines[index][1][1]
            self.edit_dialog(fields=list(fields),
                             on_ok=lambda v: self._edited(index, v))

        def _edited(self, index, values):
            self._apply_and_record(
                lambda: self.app.rule_file(self.rule_file_attr())
                        .update(index, values))

        def on_delete(self, _btn, index):
            self._apply_and_record(
                lambda: self.app.rule_file(self.rule_file_attr()).remove(index))

    class KeybindsPage(PageBase):
        """Web-style bindings browser: category filter chips, action badges,
        command chips and keycap chips (Super + R), grouped by dispatcher
        category."""

        page_key = "bindings"
        record_name = "bind"

        def rule_file_attr(self):
            return "bind"

        def field_labels(self):
            return ["Modifiers (SUPER, SHIFT\u2026)", "Key", "Action", "Arguments"]

        def build(self):
            self.selected_category = None       # None = All
            self.search_text = ""

            # Top bar: "search in all modes" + orange New Binding button.
            bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
            self.filter_entry = Gtk.SearchEntry(
                placeholder_text="Search in all modes\u2026")
            self.filter_entry.connect("search-changed", self.on_filter_changed)
            bar.pack_start(self.filter_entry, True, True, 0)
            new_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
            new_box.pack_start(Gtk.Image.new_from_icon_name(
                "list-add-symbolic", Gtk.IconSize.BUTTON), False, False, 0)
            new_box.pack_start(Gtk.Label(label="New Binding"), False, False, 0)
            new_btn = Gtk.Button()
            new_btn.add(new_box)
            new_btn.get_style_context().add_class("suggested-action")
            new_btn.set_tooltip_text("Add a keybinding")
            new_btn.connect("clicked", self.on_add)
            bar.pack_end(new_btn, False, False, 0)
            self.content.pack_start(bar, False, False, 0)

            # Category filter chips.
            self.chips_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL,
                                     spacing=6)
            self.content.pack_start(self.chips_box, False, False, 0)

            self.count_label = Gtk.Label(xalign=0)
            self.count_label.get_style_context().add_class("ms-count")
            self.content.pack_start(self.count_label, False, False, 0)

            self.cards_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL,
                                     spacing=12)
            self.content.pack_start(self.cards_box, True, True, 0)
            self.add_note("Ctrl+R sends the reload to MangoWM after saving. "
                          "Conflicting binds (same modifiers and key) are "
                          "flagged with \u26a0.")
            self.rebuild()

        # -- helpers -------------------------------------------------------
        def binds(self):
            return self.app.rule_file("bind").records("bind")

        def category_of(self, fields):
            action = fields[2] if len(fields) > 2 else ""
            return BINDING_CATEGORIES.get(action, "system")

        def on_filter_changed(self, entry):
            self.search_text = entry.get_text().lower().strip()
            self.rebuild()

        def on_chip_toggled(self, btn, category):
            if not btn.get_active():
                return
            # Single-select chips: switch every other chip off.
            for child in self.chips_box.get_children():
                if child is not btn and isinstance(child, Gtk.ToggleButton) \
                        and child.get_active():
                    child.set_active(False)
            self.selected_category = None if category is None else category
            self.rebuild()

        # -- rendering -----------------------------------------------------
        def rebuild(self):
            records = self.binds()
            conflicts = dict(RuleFile.bind_conflicts(records))
            needle = self.search_text

            def matches(fields):
                if needle and not needle in ",".join(fields).lower():
                    return False
                if self.selected_category and \
                        self.category_of(fields) != self.selected_category:
                    return False
                return True

            shown = [rec for rec in records if matches(rec[1])]

            # Category chips (All + every category that exists in the file).
            present = []
            for rec in records:
                cat = self.category_of(rec[1])
                if cat not in present:
                    present.append(cat)
            for child in list(self.chips_box.get_children()):
                self.chips_box.remove(child)
            self._make_chip(None, "All", True)
            for cat in BINDING_CATEGORY_ORDER:
                if cat in present:
                    self._make_chip(cat, BINDING_CATEGORY_LABELS.get(cat, cat),
                                    False)
            for cat in present:
                if cat not in BINDING_CATEGORY_ORDER:
                    self._make_chip(cat, BINDING_CATEGORY_LABELS.get(cat, cat),
                                    False)
            self.chips_box.show_all()

            self.count_label.set_markup(
                "<small>Showing %d binding%s%s</small>"
                % (len(shown), "" if len(shown) == 1 else "s",
                   (" for \u201c%s\u201d" % GLib.markup_escape_text(needle))
                   if needle else ""))

            for child in list(self.cards_box.get_children()):
                self.cards_box.remove(child)
            if not shown:
                empty = Gtk.Label(xalign=0)
                empty.set_markup("<span alpha='60%%'>%s</span>" %
                                 GLib.markup_escape_text(
                                     "No bindings match." if needle or
                                     self.selected_category
                                     else "No bindings defined yet."))
                self.cards_box.pack_start(empty, False, False, 8)
            grouped = {}
            for index, fields in shown:
                grouped.setdefault(self.category_of(fields), []).append(
                    (index, fields))
            for cat in BINDING_CATEGORY_ORDER + [c for c in grouped
                                                 if c not in BINDING_CATEGORY_ORDER]:
                if cat not in grouped:
                    continue
                card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
                card.get_style_context().add_class("ms-card")
                head = Gtk.Label(xalign=0)
                head.set_markup(GLib.markup_escape_text(
                    BINDING_CATEGORY_LABELS.get(cat, cat).upper()))
                head.get_style_context().add_class("ms-group-title")
                head.set_margin_start(14)
                head.set_margin_top(10)
                head.set_margin_bottom(8)
                card.pack_start(head, False, False, 0)
                card.pack_start(Gtk.Separator(), False, False, 0)
                listbox = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
                listbox.get_style_context().add_class("ms-rows")
                card.pack_start(listbox, False, False, 0)
                for index, fields in grouped[cat]:
                    listbox.add(self._make_bind_row(index, fields, conflicts))
                self.cards_box.pack_start(card, False, False, 0)
            self.cards_box.show_all()

        def _make_chip(self, category, label, is_all):
            btn = Gtk.ToggleButton(label=label)
            btn.get_style_context().add_class("ms-chip")
            if (is_all and self.selected_category is None) or \
                    (not is_all and self.selected_category == category):
                btn.set_active(True)
            btn.connect("toggled", self.on_chip_toggled,
                        None if is_all else category)
            self.chips_box.pack_start(btn, False, False, 0)

        def _make_bind_row(self, index, fields, conflicts):
            row = Gtk.ListBoxRow()
            row.get_style_context().add_class("ms-row")
            box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
            box.set_margin_start(10)
            box.set_margin_end(10)
            box.set_margin_top(7)
            box.set_margin_bottom(7)
            row.add(box)

            # Action badge.
            action = fields[2] if len(fields) > 2 else ""
            badge = Gtk.Label(label=action or "?")
            badge.get_style_context().add_class("ms-badge")
            box.pack_start(badge, False, False, 0)

            # Argument chips ("command" + the command for spawn-like binds).
            args = fields[3:] if len(fields) > 3 else []
            args = [a for a in args if a != ""]
            if args:
                if action in ("spawn", "spawn_once"):
                    lbl = Gtk.Label(label="command")
                    lbl.get_style_context().add_class("ms-cmd-label")
                    box.pack_start(lbl, False, False, 0)
                for arg in args[:3]:
                    chip = Gtk.Label(label=arg[:48])
                    chip.get_style_context().add_class("ms-cmd")
                    box.pack_start(chip, False, False, 0)

            spacer = Gtk.Box()
            spacer.set_hexpand(True)
            box.pack_start(spacer, True, True, 0)

            if index in conflicts:
                warn = Gtk.Label()
                warn.set_markup("<span foreground='orange'>\u26a0</span>")
                warn.set_tooltip_text(conflicts[index])
                box.pack_start(warn, False, False, 0)

            # Keycaps: modifiers + key as chips joined with "+".
            mods = fields[0] if fields and fields[0] else ""
            key = fields[1] if len(fields) > 1 and fields[1] else ""
            tokens = [t for t in re.split(r"[ +]", mods) if t] + [key] if key else \
                [t for t in re.split(r"[ +]", mods) if t]
            for i, token in enumerate(tokens):
                if i:
                    plus = Gtk.Label(label="+")
                    plus.get_style_context().add_class("ms-plus")
                    box.pack_start(plus, False, False, 0)
                cap = Gtk.Label(label=token.capitalize()
                                if token.lower() in ("super", "shift", "ctrl",
                                                     "alt", "print", "lock")
                                else token)
                cap.get_style_context().add_class("ms-key")
                box.pack_start(cap, False, False, 0)

            edit = Gtk.Button.new_from_icon_name("document-edit-symbolic",
                                                 Gtk.IconSize.MENU)
            edit.set_relief(Gtk.ReliefStyle.NONE)
            edit.get_style_context().add_class("ms-reset")
            edit.set_tooltip_text("Edit")
            edit.connect("clicked", self.on_edit, index)
            box.pack_start(edit, False, False, 0)
            delete = Gtk.Button.new_from_icon_name("user-trash-symbolic",
                                                   Gtk.IconSize.MENU)
            delete.set_relief(Gtk.ReliefStyle.NONE)
            delete.get_style_context().add_class("ms-reset")
            delete.set_tooltip_text("Delete")
            delete.connect("clicked", self.on_delete, index)
            box.pack_start(delete, False, False, 0)
            return row

        # -- editing -------------------------------------------------------
        def edit_dialog(self, fields=None, on_ok=None):
            dlg = Gtk.Dialog(title="Binding",
                             transient_for=self.get_toplevel(), modal=True)
            dlg.add_buttons("_Cancel", Gtk.ResponseType.CANCEL,
                            "_Save", Gtk.ResponseType.OK)
            grid = Gtk.Grid(column_spacing=8, row_spacing=8)
            grid.set_border_width(12)
            entries = []
            for i, label in enumerate(self.field_labels()):
                grid.attach(Gtk.Label(label=label, xalign=0), 0, i, 1, 1)
                entry = Gtk.Entry(width_chars=32)
                entry.set_text(fields[i] if fields and i < len(fields) else "")
                grid.attach(entry, 1, i, 1, 1)
                entries.append(entry)
            dlg.get_content_area().add(grid)
            dlg.show_all()
            if dlg.run() == Gtk.ResponseType.OK:
                values = [e.get_text().strip() for e in entries]
                dlg.destroy()
                on_ok(values)
                return True
            dlg.destroy()
            return False

        def on_add(self, _btn):
            self.edit_dialog(on_ok=self._added)

        def _added(self, values):
            self._apply_and_record(
                lambda: self.app.rule_file("bind").add("bind", values))

        def on_edit(self, _btn, index):
            fields = self.app.rule_file("bind").raw_lines[index][1][1]
            self.edit_dialog(fields=list(fields),
                             on_ok=lambda v: self._edited(index, v))

        def _edited(self, index, values):
            self._apply_and_record(
                lambda: self.app.rule_file("bind").update(index, values))

        def on_delete(self, _btn, index):
            self._apply_and_record(
                lambda: self.app.rule_file("bind").remove(index))

        def _apply_and_record(self, mutation):
            """Apply a bind mutation and push an undoable change."""
            rf = self.app.rule_file("bind")
            before = list(rf.raw_lines)
            mutation()
            after = list(rf.raw_lines)
            self.app.record_change(
                lambda: self.app.rule_restore("bind", before),
                lambda: self.app.rule_restore("bind", after))
            rf.save()
            self.clear_dirty()
            self.rebuild()

    class WindowRulesPage(RuleListPage):
        page_key = "window-rules"
        record_name = "window_rule"

        def rule_file_attr(self):
            return "rule"

        def field_labels(self):
            return ["Window class", "Title", "Tags", "Float (0/1)", "Monitor"]

        def placeholder(self):
            return "e.g. foot, , 2, 1, 0"

        def add_from_entry(self):
            text = self.add_entry.get_text().strip()
            if not text:
                return
            parts = [p.strip() for p in text.split(",") if p.strip()]
            while len(parts) < 5:
                parts.append("")
            self.add_entry.set_text("")
            self._added(parts)

        def build(self):
            super().build()
            btn_once = Gtk.Button(label="Add window_rule_once")
            btn_once.get_style_context().add_class("ms-secondary-button")
            btn_once.connect("clicked", self.on_add_once)
            self.content.pack_start(btn_once, False, False, 2)

        def on_add_once(self, _btn):
            self.edit_dialog(on_ok=self._added_once)

        def _added_once(self, values):
            self._apply_and_record(
                lambda: self.app.rule_file("rule").add("window_rule_once",
                                                        values))

    class MonitorsPage(RuleListPage):
        page_key = "monitor-rules"
        record_name = "monitor"

        def rule_file_attr(self):
            return "monitor"

        def field_labels(self):
            return ["Name", "Enabled (0/1)", "X", "Y", "Width", "Height",
                    "Scale", "Refresh", "Transform"]

        def placeholder(self):
            return "e.g. eDP-1"

        def add_from_entry(self):
            name = self.add_entry.get_text().strip()
            if not name:
                return
            self.add_entry.set_text("")
            self._added([name, "1", "0", "0", "-1", "-1", "1", "0", "normal"])

        def build(self):
            super().build()
            self.detect_btn = Gtk.Button(label="Add connected displays (mmsg)")
            self.detect_btn.get_style_context().add_class("ms-secondary-button")
            self.detect_btn.connect("clicked", self.on_detect)
            self.content.pack_start(self.detect_btn, False, False, 2)
            if not mmsg_available():
                self.add_note("mmsg not found \u2014 'Add connected displays' needs "
                              "MangoWM's mmsg. You can still add monitors manually.")

        def on_detect(self, _btn):
            monitors = connected_monitors()
            if not monitors:
                self.app.error_dialog("mmsg reported no connected displays "
                                      "(or mmsg is not installed).")
                return
            rf = self.app.rule_file("monitor")
            existing = set(rec[1][0] for rec in rf.records("monitor") if rec[1])
            new = [name for name, _desc in monitors if name not in existing]
            if not new:
                return

            def add_all():
                for name in new:
                    rf.add("monitor", [name, "1", "0", "0", "-1", "-1", "1",
                                       "0", "normal"])

            self._apply_and_record(add_all)

    class TagRulesPage(RuleListPage):
        page_key = "tag-rules"
        record_name = "tag_rule"

        def rule_file_attr(self):
            return "rule"

        def field_labels(self):
            return ["Tag ID (0 = special, * = all)",
                    "Layout (tile, scroller, monocle, deck\u2026)",
                    "Monitor name (optional)"]

        def placeholder(self):
            return "e.g. 1, scroller"

        def add_from_entry(self):
            text = self.add_entry.get_text().strip()
            if not text:
                return
            parts = [p.strip() for p in text.split(",") if p.strip()]
            while len(parts) < 3:
                parts.append("")
            self.add_entry.set_text("")
            self._added(parts)

    class LayerRulesPage(RuleListPage):
        page_key = "layer-rules"
        record_name = "layer_rule"

        def rule_file_attr(self):
            return "rule"

        def field_labels(self):
            return ["Layer name (regex)", "Open anim (fade/slide/zoom/none)",
                    "Close anim (fade/slide/zoom/none)"]

        def placeholder(self):
            return "e.g. rofi, slide, fade"

        def add_from_entry(self):
            text = self.add_entry.get_text().strip()
            if not text:
                return
            parts = [p.strip() for p in text.split(",") if p.strip()]
            while len(parts) < 3:
                parts.append("")
            self.add_entry.set_text("")
            self._added(parts)

    class AutostartPage(PageBase):
        """exec_once / exec editors, one card each (like the web settings)."""

        page_key = "autostart"

        CARDS = [("exec_once", "Run once per session",
                  "Commands that run only when mangowm starts \u2014 not on reload.",
                  "e.g., waybar &"),
                 ("exec", "Run on every start and reload",
                  "Commands that run again whenever the config is reloaded.",
                  "e.g., swaybg -i ~/wallpaper.png")]

        def build(self):
            self.lists = {}
            self.entries = {}
            for key, title, _desc, placeholder in self.CARDS:
                body = self.add_card(key)
                listbox = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
                listbox.get_style_context().add_class("ms-rows")
                body.pack_start(listbox, False, False, 0)
                self.lists[key] = listbox
                bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
                bar.set_border_width(6)
                prompt = Gtk.Label(label="$")
                prompt.get_style_context().add_class("ms-prompt")
                bar.pack_start(prompt, False, False, 0)
                entry = Gtk.Entry(hexpand=True)
                entry.set_placeholder_text(placeholder)
                entry.connect("activate", self.on_entry_activate, key)
                bar.pack_start(entry, True, True, 0)
                add = Gtk.Button(label="Add")
                add.get_style_context().add_class("ms-add-button")
                add.connect("clicked", self.on_entry_activate, key)
                bar.pack_start(add, False, False, 0)
                body.pack_start(bar, False, False, 2)
                self.entries[key] = entry
            self.rebuild()

        def rules(self):
            out = []
            for key in ("exec_once", "exec"):
                for value in self.app.config.get_all(key):
                    out.append((key, value))
            return out

        def rebuild(self):
            entries = self.rules()
            for key, listbox in self.lists.items():
                for child in listbox.get_children():
                    listbox.remove(child)
            for i, (key, value) in enumerate(entries):
                listbox = self.lists[key]
                row = Gtk.ListBoxRow()
                row.get_style_context().add_class("ms-row")
                box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
                box.set_margin_start(10)
                box.set_margin_end(10)
                box.set_margin_top(6)
                box.set_margin_bottom(6)
                label = Gtk.Label(label=value[:90], xalign=0, hexpand=True,
                                  ellipsize=3)
                label.get_style_context().add_class("ms-row-label")
                box.pack_start(label, True, True, 8)
                delete = Gtk.Button.new_from_icon_name("user-trash-symbolic",
                                                       Gtk.IconSize.MENU)
                delete.set_relief(Gtk.ReliefStyle.NONE)
                delete.get_style_context().add_class("destructive-action")
                delete.set_tooltip_text("Delete")
                delete.connect("clicked", self.on_delete, i)
                box.pack_start(delete, False, False, 2)
                row.add(box)
                listbox.add(row)
                listbox.show_all()
            if not entries:
                self.add_note("No startup commands yet.")

        def on_entry_activate(self, _widget, key):
            entry = self.entries[key]
            text = entry.get_text().strip()
            if not text:
                return
            old = list(self.app.config.get_all(key))
            try:
                self.app.config.add_value(key, text)
            except ConfigError as exc:
                self.app.error_dialog(str(exc))
                return
            entry.set_text("")
            self.rebuild()
            self.app.record_multi_undo(key, old, list(self.app.config.get_all(key)))
            # Startup commands apply on save; keep the file written immediately.
            self.app.config.save()
            self.clear_dirty()

        def on_delete(self, _btn, index):
            key, _value = self.rules()[index]
            all_key = self.app.config.get_all(key)
            old = list(all_key)
            sub = index if key == "exec_once" else index - len(
                self.app.config.get_all("exec_once"))
            try:
                self.app.config.remove_value(key, sub)
            except ConfigError as exc:
                self.app.error_dialog(str(exc))
                return
            self.rebuild()
            self.app.record_multi_undo(key, old, list(self.app.config.get_all(key)))
            self.app.config.save()
            self.clear_dirty()

    class EnvPage(PageBase):
        """Editor for ``env = KEY, value`` records in config.conf."""

        page_key = "environment"

        def build(self):
            self.listbox = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
            self.listbox.get_style_context().add_class("ms-rows")
            body = self.add_card("env")
            body.pack_start(self.listbox, False, False, 0)
            bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
            bar.set_border_width(6)
            prompt = Gtk.Label(label="$")
            prompt.get_style_context().add_class("ms-prompt")
            bar.pack_start(prompt, False, False, 0)
            self.entry = Gtk.Entry(hexpand=True)
            self.entry.set_placeholder_text("e.g., XCURSOR_SIZE, 24  (or NAME=value)")
            self.entry.connect("activate", self.on_entry_activate)
            bar.pack_start(self.entry, True, True, 0)
            add = Gtk.Button(label="Add")
            add.get_style_context().add_class("ms-add-button")
            add.connect("clicked", self.on_entry_activate)
            bar.pack_start(add, False, False, 0)
            body.pack_start(bar, False, False, 2)
            self.rebuild()

        def split_value(self, raw):
            """'KEY, value' (or 'KEY=value') -> [KEY, value]."""
            raw = raw.strip()
            if "=" in raw and "," not in raw.split("=")[0]:
                name, _, val = raw.partition("=")
                return [name.strip(), val.strip()]
            parts = [p.strip() for p in raw.split(",")]
            if len(parts) == 1:
                return [parts[0], ""]
            return [parts[0], ", ".join(parts[1:])]

        def rebuild(self):
            for child in self.listbox.get_children():
                self.listbox.remove(child)
            values = self.app.config.get_all("env")
            for i, value in enumerate(values):
                name, val = self.split_value(value)
                row = Gtk.ListBoxRow()
                row.get_style_context().add_class("ms-row")
                box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
                box.set_margin_start(10)
                box.set_margin_end(10)
                box.set_margin_top(6)
                box.set_margin_bottom(6)
                name_label = Gtk.Label(label=name, xalign=0)
                name_label.get_style_context().add_class("ms-row-label")
                box.pack_start(name_label, False, False, 0)
                val_label = Gtk.Label(label=val, xalign=0, hexpand=True, ellipsize=3)
                val_label.get_style_context().add_class("ms-row-desc")
                box.pack_start(val_label, True, True, 8)
                delete = Gtk.Button.new_from_icon_name("user-trash-symbolic",
                                                       Gtk.IconSize.MENU)
                delete.set_relief(Gtk.ReliefStyle.NONE)
                delete.get_style_context().add_class("destructive-action")
                delete.set_tooltip_text("Delete")
                delete.connect("clicked", self.on_delete, i)
                box.pack_start(delete, False, False, 2)
                row.add(box)
                self.listbox.add(row)
            self.listbox.show_all()
            if not values:
                self.add_note("No environment variables set.")

        def on_entry_activate(self, _widget, *_a):
            text = self.entry.get_text().strip()
            if not text:
                return
            name, val = self.split_value(text)
            if not name:
                self.app.error_dialog("Need a variable name first.")
                return
            value = "%s, %s" % (name, val) if val else name
            old = list(self.app.config.get_all("env"))
            try:
                self.app.config.add_value("env", value)
            except ConfigError as exc:
                self.app.error_dialog(str(exc))
                return
            self.entry.set_text("")
            self.rebuild()
            self.app.record_multi_undo("env", old, list(self.app.config.get_all("env")))
            self.app.config.save()
            self.clear_dirty()

        def on_delete(self, _btn, index):
            old = list(self.app.config.get_all("env"))
            try:
                self.app.config.remove_value("env", index)
            except ConfigError as exc:
                self.app.error_dialog(str(exc))
                return
            self.rebuild()
            self.app.record_multi_undo("env", old, list(self.app.config.get_all("env")))
            self.app.config.save()
            self.clear_dirty()

    class ConfigFilesPage(PageBase):
        page_key = "files"

        def build(self):
            self.listbox = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
            self.listbox.set_selection_mode(Gtk.SelectionMode.NONE)
            frame = self.add_frame()
            frame.add(self.listbox)
            btns = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
            refresh = Gtk.Button(label="Re-check includes")
            refresh.set_tooltip_text("Re-read all files and report missing or cyclic includes")
            refresh.connect("clicked", self.on_refresh)
            btns.pack_start(refresh, False, False, 0)
            self.content.pack_start(btns, False, False, 4)
            self.rebuild()

        def on_refresh(self, _btn):
            self.app.reload_config()
            self.rebuild()

        def rebuild(self):
            for child in self.listbox.get_children():
                self.listbox.remove(child)
            cfg = self.app.config
            errors, warnings = cfg.summary()
            by_file = {}
            for issue in errors + warnings:
                by_file.setdefault(os.path.realpath(os.path.expanduser(issue.path or "")),
                                   []).append(issue)
            docs = [cfg.docs[r] for r in cfg.order if r in cfg.docs]
            for doc in docs:
                problems = by_file.get(os.path.realpath(doc.path), [])
                row = Gtk.ListBoxRow()
                row.set_activatable(True)
                row.connect("activate", self.on_open, doc)
                box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
                box.set_border_width(6)
                icon = Gtk.Image.new_from_icon_name("text-x-generic-symbolic",
                                                   Gtk.IconSize.BUTTON)
                box.pack_start(icon, False, False, 0)
                name = Gtk.Label(label=doc.path.replace(os.path.expanduser("~"), "~"),
                                 xalign=0, hexpand=True, ellipsize=3)
                box.pack_start(name, True, True, 0)
                link = Gtk.Label(label="%d line%s" % (len(doc.lines),
                                                      "" if len(doc.lines) == 1 else "s"))
                link.get_style_context().add_class("dim-label")
                box.pack_start(link, False, False, 0)
                if problems:
                    errs = [p for p in problems if p.severity == Issue.ERROR]
                    color = "red" if errs else "orange"
                    if errs:
                        text = "%d error%s" % (len(errs), "s" if len(errs) != 1 else "")
                    else:
                        text = "%d warning%s" % (len(problems),
                                                  "s" if len(problems) != 1 else "")
                    warn = Gtk.Label()
                    warn.set_markup("<span foreground='%s'>%s</span>" % (color, text))
                    box.pack_start(warn, False, False, 0)
                edit = Gtk.Image.new_from_icon_name("document-edit-symbolic",
                                                    Gtk.IconSize.BUTTON)
                box.pack_start(edit, False, False, 0)
                row.add(box)
                self.listbox.add(row)
            self.listbox.show_all()
            if not docs:
                self.add_note("No config files loaded.")
            elif warnings or errors:
                self.add_note("%d error(s), %d warning(s) across all files. Click a file "
                              "to view and edit it raw." % (len(errors), len(warnings)))
            else:
                self.add_note("Includes are healthy \u2014 every sourced file was found.")

        def on_open(self, _row, doc):
            path = os.path.realpath(os.path.expanduser(doc.path))
            try:
                with open(path, "rb") as fh:
                    data = fh.read()
                text = data.decode("utf-8", errors="surrogateescape")
                lossless = text.encode("utf-8", "surrogateescape") == data
            except OSError as exc:
                self.app.error_dialog("Cannot open %s: %s" % (doc.path, exc.strerror))
                return
            dlg = Gtk.Dialog(title=os.path.basename(doc.path),
                             transient_for=self.get_toplevel(), modal=True)
            dlg.add_buttons("_Cancel", Gtk.ResponseType.CANCEL, "_Save", Gtk.ResponseType.OK)
            dlg.set_default_size(720, 520)
            scroll = Gtk.ScrolledWindow()
            view = Gtk.TextView()
            view.set_monospace(True)
            buf = view.get_buffer()
            buf.set_text(text)
            if not lossless:
                view.set_editable(False)
                self.app.warning_in_dialog(dlg, "File is not valid UTF-8 — opened read-only.")
            scroll.add(view)
            scroll.set_vexpand(True)
            dlg.get_content_area().pack_start(scroll, True, True, 0)
            dlg.show_all()
            if dlg.run() == Gtk.ResponseType.OK and lossless:
                new_text = buf.get_text(*buf.get_bounds(), include_hidden_chars=True)
                if new_text != text:
                    ok, msg = write_text_atomic(path, new_text)
                    if not ok:
                        self.app.error_dialog(msg)
                    else:
                        self.app.reload_config()
                        self.rebuild()
            dlg.destroy()

    class ThemingPage(PageBase):
        page_key = "theming"

        KEYS = [("gtk-theme", "GTK theme"),
                ("icon-theme", "Icon theme"),
                ("cursor-theme", "Cursor theme"),
                ("color-scheme", "Color scheme (default/prefer-dark/prefer-light)")]

        def build(self):
            self.entries = {}
            grid = Gtk.Grid(column_spacing=12, row_spacing=8)
            if gsettings_get(GTK_THEME_SCHEMA, "gtk-theme") is None:
                self.add_note("gsettings (glib2) is not available — theming page is "
                              "read-only on this system.")
            for i, (key, label) in enumerate(self.KEYS):
                grid.attach(Gtk.Label(label=label, xalign=0), 0, i, 1, 1)
                entry = Gtk.Entry(width_chars=28)
                current = gsettings_get(GTK_THEME_SCHEMA, key)
                if current:
                    entry.set_text(current)
                entry.connect("activate", self.on_set, key, entry)
                btn = Gtk.Button(label="Apply")
                btn.connect("clicked", self.on_set, key, entry)
                box = Gtk.Box(spacing=6)
                box.add(entry); box.add(btn)
                grid.attach(box, 1, i, 1, 1)
                self.entries[key] = entry
            frame = self.add_frame()
            frame.add(grid)
            self.add_note("Changes apply immediately via gsettings. Press Enter or "
                          "click Apply.")
            self.show_all()

        def on_set(self, _btn, key, entry):
            value = entry.get_text().strip()
            if value:
                ok, out = gsettings_set(GTK_THEME_SCHEMA, key, value)
                if not ok:
                    self.app.error_dialog("gsettings failed: %s" % out)

    class NotificationsPage(PageBase):
        page_key = "notifications"

        def build(self):
            self.grid = Gtk.Grid(column_spacing=12, row_spacing=8)
            frame = self.add_frame()
            frame.add(self.grid)
            btn = Gtk.Button(label="Refresh")
            btn.connect("clicked", lambda *_: self.rebuild())
            self.content.pack_start(btn, False, False, 4)
            self.rebuild()

        def rebuild(self):
            for child in self.grid.get_children():
                self.grid.remove(child)
            found = notification_daemons()
            active = [(n, c) for n, running, c in found if running]
            self.grid.attach(Gtk.Label(label="Detected daemon", xalign=0), 0, 0, 1, 1)
            self.grid.attach(Gtk.Label(label="Config file", xalign=0), 1, 0, 1, 1)
            if not active:
                self.grid.attach(Gtk.Label(label="None running"), 0, 1, 1, 1)
            for i, (name, cfg) in enumerate(active, start=1):
                self.grid.attach(Gtk.Label(label=name, xalign=0), 0, i, 1, 1)
                self.grid.attach(Gtk.Label(
                    label=cfg.replace(os.path.expanduser("~"), "~") +
                    (" (not created yet)" if not os.path.exists(cfg) else ""),
                    xalign=0), 1, i, 1, 1)
            self.grid.show_all()
            if not active:
                self.add_note("No notification daemon detected. Install one of: "
                              "swaync, mako, dunst, fnott.")

    class BackgroundPage(PageBase):
        """Wallpaper / background changer.

        Applies the image live with the best available backend (swaybg,
        swww, feh) and persists the choice by editing the ``swaybg`` line
        in ``~/.config/mango/autostart.sh`` so it survives logout.
        """

        page_key = "background"

        def build(self):
            backends = detect_wallpaper_backends()
            self.backend = "swaybg" if "swaybg" in backends else (
                backends[0] if backends else "swaybg")

            grid = Gtk.Grid(column_spacing=12, row_spacing=8)
            grid.set_margin_top(6)

            grid.attach(Gtk.Label(label="Image", xalign=0), 0, 0, 1, 1)
            self.file_btn = Gtk.FileChooserButton(
                title="Choose a wallpaper", action=Gtk.FileChooserAction.OPEN)
            filt = Gtk.FileFilter()
            filt.set_name("Images")
            for pat in ("*.png", "*.jpg", "*.jpeg", "*.webp", "*.gif", "*.bmp"):
                filt.add_pattern(pat)
            self.file_btn.add_filter(filt)
            self.file_btn.set_hexpand(True)
            self.file_btn.connect("file-set", self.on_file_set)
            grid.attach(self.file_btn, 1, 0, 1, 1)

            grid.attach(Gtk.Label(label="Fit mode", xalign=0), 0, 1, 1, 1)
            self.mode_combo = Gtk.ComboBoxText()
            for m in CHOICE_WALLPAPER_MODES:
                self.mode_combo.append_text(m)
            self.mode_combo.set_active(0)
            grid.attach(self.mode_combo, 1, 1, 1, 1)

            grid.attach(Gtk.Label(label="Preview", xalign=0), 0, 2, 1, 1)
            self.preview = Gtk.Image()
            self.preview.set_size_request(240, 135)
            grid.attach(self.preview, 1, 2, 1, 1)

            frame = self.add_frame()
            frame.add(grid)

            btns = Gtk.ButtonBox(spacing=6)
            btns.set_layout(Gtk.ButtonBoxStyle.START)
            apply = Gtk.Button(label="Set wallpaper")
            apply.get_style_context().add_class("suggested-action")
            apply.connect("clicked", self.on_apply)
            btns.add(apply)
            self.content.pack_start(btns, False, False, 4)

            self.status = Gtk.Label(xalign=0)
            self.content.pack_start(self.status, False, False, 2)

            if not backends:
                self.add_note("No wallpaper backend found — install swaybg "
                              "(Arch: sudo pacman -S swaybg).")
            else:
                self.add_note("Detected backends: %s. Applies immediately and "
                              "is saved to ~/.config/mango/autostart.sh so it "
                              "survives logout." % ", ".join(backends))
            self.show_all()

        def on_file_set(self, btn):
            path = btn.get_filename()
            if not path:
                return
            self.preview.set_from_file(path)
            # Scale the preview down (the file may be 4K).
            try:
                from gi.repository import GdkPixbuf
                pb = GdkPixbuf.Pixbuf.new_from_file_at_scale(
                    path, 240, 135, True)
                self.preview.set_from_pixbuf(pb)
            except Exception:
                pass
            self.show_all()

        def on_apply(self, _btn):
            path = self.file_btn.get_filename()
            if not path:
                self.app.error_dialog("Choose an image first.")
                return
            mode = self.mode_combo.get_active_text() or "fill"
            ok, msg = apply_wallpaper(path, mode, backend=self.backend)
            if not ok:
                self.app.error_dialog(msg)
                return
            ok2, msg2 = set_autostart_wallpaper(path, mode, backend=self.backend)
            if ok2:
                self.status.set_text(
                    "Wallpaper set and saved to ~/.config/mango/autostart.sh")
            else:
                self.status.set_text(
                    "Applied for this session, but NOT saved: %s" % msg2)
            self.show_all()

    class SystemInfoPage(PageBase):
        page_key = "system"

        def build(self):
            self.grid = Gtk.Grid(column_spacing=12, row_spacing=6)
            frame = self.add_frame()
            frame.add(self.grid)
            self.rebuild()

        def rebuild(self):
            for child in self.grid.get_children():
                self.grid.remove(child)
            for i, (k, v) in enumerate(system_info()):
                self.grid.attach(Gtk.Label(label=k, xalign=0), 0, i, 1, 1)
                label = Gtk.Label(label=v, xalign=0, selectable=True)
                label.set_selectable(True)
                self.grid.attach(label, 1, i, 1, 1)
            self.grid.show_all()
            btn = Gtk.Button(label="Copy all")
            btn.connect("clicked", self.on_copy)
            self.content.pack_start(btn, False, False, 4)

        def on_copy(self, _btn):
            text = "\n".join("%s: %s" % kv for kv in system_info())
            clipboard = Gtk.Clipboard.get(Gdk.SELECTION_CLIPBOARD)
            clipboard.set_text(text, -1)
            self.app.error_dialog("System info copied to clipboard.")

    # ---------------------------------------------------------------- window
    class SettingsWindow(Gtk.ApplicationWindow):
        """Chromeless window: a slim header bar (web-style) above a sidebar +
        content split.  Dragging the header moves the window."""

        def __init__(self, app):
            try:
                width = int(app.read_ui_pref("width", 1020))
                height = int(app.read_ui_pref("height", 700))
            except (TypeError, ValueError):
                width, height = 1020, 700
            # Clamp junk from older versions (a destroyed window reports 1x1).
            width = width if 480 <= width <= 6000 else 1020
            height = height if 320 <= height <= 4000 else 700
            super().__init__(application=app.gtk_app,
                             title=APP_NAME, default_width=width,
                             default_height=height)
            self.app = app
            self.translucent = False
            # --- translucent surface (MangoWM blurs what is behind it) -------
            try:
                screen = self.get_screen()
                visual = screen.get_rgba_visual()
                if visual is not None and screen.is_composited():
                    self.set_visual(visual)
                    self.get_style_context().add_class("app-window")
                    self.set_app_paintable(True)
                    self.translucent = True
            except Exception:
                pass
            self.surface = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
            self.surface.get_style_context().add_class("app-surface")
            self.add(self.surface)
            self._last_size = (width, height)
            self._last_pos = 0
            self.connect("size-allocate", self.on_size_allocate)
            self.connect("destroy", self.on_destroy)

            # -- header bar ----------------------------------------------------
            header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
            header.get_style_context().add_class("ms-header")
            header.set_margin_start(8)
            header.set_margin_end(8)
            header.set_margin_top(6)
            header.set_margin_bottom(2)
            # Window dragging: press anywhere on the header to move.
            header.add_events(Gdk.EventMask.BUTTON_PRESS_MASK)
            header.connect("button-press-event", self.on_header_press)
            self.header_box = header

            self.sidebar_toggle = Gtk.Button.new_from_icon_name(
                "open-menu-symbolic", Gtk.IconSize.BUTTON)
            self._sidebar_icon_alternatives()
            self.sidebar_toggle.set_relief(Gtk.ReliefStyle.NONE)
            self.sidebar_toggle.set_tooltip_text("Toggle sidebar")
            self.sidebar_toggle.connect("clicked", self.on_toggle_sidebar)
            header.pack_start(self.sidebar_toggle, False, False, 2)

            sep = Gtk.Separator(orientation=Gtk.Orientation.VERTICAL)
            header.pack_start(sep, False, False, 4)

            self.title_label = Gtk.Label(label=PAGE_TITLES[PAGE_ORDER[0]], xalign=0)
            self.title_label.get_style_context().add_class("ms-header-title")
            header.pack_start(self.title_label, False, False, 2)

            # Centered search pill (Ctrl+K / Ctrl+F both focus it).
            self.search = Gtk.SearchEntry(placeholder_text="Search settings\u2026")
            self.search.set_width_chars(26)
            self.search.get_style_context().add_class("ms-search")
            self.search.set_halign(Gtk.Align.CENTER)
            self.search.set_valign(Gtk.Align.CENTER)
            self.search.connect("search-changed", self.on_search)
            self.search.connect("stop-search", lambda e: e.set_text(""))
            center = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
            center.pack_start(self.search, True, True, 0)
            header.pack_start(center, True, True, 0)
            self.search.set_tooltip_text("Search settings (Ctrl+K or Ctrl+F)")

            # Right: quit is outermost, then save/reload/history/setup/files
            # (web-settings order, left to right).
            close_btn = Gtk.Button.new_from_icon_name("window-close-symbolic",
                                                      Gtk.IconSize.BUTTON)
            close_btn.set_relief(Gtk.ReliefStyle.NONE)
            close_btn.set_tooltip_text("Quit (Ctrl+Q)")
            close_btn.connect("clicked", lambda *_: app.action_quit())
            header.pack_end(close_btn, False, False, 2)

            self.save_btn = Gtk.Button()
            self._save_icon = Gtk.Image.new_from_icon_name(
                "document-save-symbolic", Gtk.IconSize.BUTTON)
            self.save_btn.set_image(self._save_icon)
            self.save_btn.set_relief(Gtk.ReliefStyle.NONE)
            self.save_btn.set_tooltip_text("Save all changed pages (Ctrl+S)")
            self.save_btn.connect("clicked", lambda *_: app.action_save())
            header.pack_end(self.save_btn, False, False, 2)

            reload_btn = Gtk.Button.new_from_icon_name("view-refresh-symbolic",
                                                       Gtk.IconSize.BUTTON)
            reload_btn.set_relief(Gtk.ReliefStyle.NONE)
            reload_btn.set_tooltip_text("Reload MangoWM (Ctrl+R)")
            reload_btn.connect("clicked", lambda *_: app.action_reload_mango())
            header.pack_end(reload_btn, False, False, 2)

            self.redo_btn = Gtk.Button.new_from_icon_name("edit-redo-symbolic",
                                                          Gtk.IconSize.BUTTON)
            self.redo_btn.set_relief(Gtk.ReliefStyle.NONE)
            self.redo_btn.set_tooltip_text("Redo (Ctrl+Shift+Z)")
            self.redo_btn.connect("clicked", lambda *_: app.action_redo())
            header.pack_end(self.redo_btn, False, False, 2)

            self.undo_btn = Gtk.Button.new_from_icon_name("edit-undo-symbolic",
                                                          Gtk.IconSize.BUTTON)
            self.undo_btn.set_relief(Gtk.ReliefStyle.NONE)
            self.undo_btn.set_tooltip_text("Undo (Ctrl+Z)")
            self.undo_btn.connect("clicked", lambda *_: app.action_undo())
            header.pack_end(self.undo_btn, False, False, 2)

            self.setup_btn = self._make_setup_menu(app)
            header.pack_end(self.setup_btn, False, False, 2)

            files_btn = Gtk.Button.new_from_icon_name("text-x-generic-symbolic",
                                                      Gtk.IconSize.BUTTON)
            files_btn.set_relief(Gtk.ReliefStyle.NONE)
            files_btn.set_tooltip_text("View Config Files")
            files_btn.connect("clicked", lambda *_: self.goto("files"))
            header.pack_end(files_btn, False, False, 2)

            self.surface.pack_start(header, False, False, 0)

            # -- sidebar -------------------------------------------------------
            self.paned = Gtk.Paned()
            self.surface.pack_start(self.paned, True, True, 0)
            side = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
            side.set_border_width(8)
            side.get_style_context().add_class("app-sidebar")

            # Brand row, like the web sidebar's logo header.
            brand = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
            brand.set_margin_start(4)
            brand.set_margin_bottom(4)
            icon = Gtk.Image.new_from_icon_name("mangowm", Gtk.IconSize.DND)
            if Gtk.IconTheme.get_default().lookup_icon("mangowm", 24, 0) is None:
                icon = Gtk.Image.new_from_icon_name("applications-graphics",
                                                    Gtk.IconSize.DND)
            brand.pack_start(icon, False, False, 0)
            brand_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
            name = Gtk.Label(xalign=0)
            name.set_markup("<b>mango settings</b>")
            name.get_style_context().add_class("ms-brand-name")
            brand_box.pack_start(name, False, False, 0)
            version = Gtk.Label(xalign=0)
            version.set_markup(
                "<small>compositor %s</small>" % GLib.markup_escape_text(
                    mango_version() or "unknown"))
            version.get_style_context().add_class("ms-brand-version")
            brand_box.pack_start(version, False, False, 0)
            brand.pack_start(brand_box, True, True, 0)
            side.pack_start(brand, False, False, 0)

            self.sidebar = Gtk.ListBox()
            self.sidebar.set_selection_mode(Gtk.SelectionMode.SINGLE)
            self.sidebar.get_style_context().add_class("ms-nav")
            self.sidebar.connect("row-selected", self.on_row_selected)
            scroll = Gtk.ScrolledWindow()
            scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
            scroll.add(self.sidebar)
            side.pack_start(scroll, True, True, 0)

            # Footer: theme selector + blur toggle (kept from the old sidebar).
            footer = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
            footer.set_margin_top(4)
            theme_label = Gtk.Label(label="Theme", xalign=0)
            theme_label.get_style_context().add_class("ms-note")
            footer.pack_start(theme_label, False, False, 0)
            self.theme_combo = Gtk.ComboBoxText()
            for choice in ("system", "dark", "light"):
                self.theme_combo.append_text(choice)
            self.theme_combo.set_active(0)
            self.theme_combo.connect("changed", app.on_theme_changed)
            footer.pack_start(self.theme_combo, True, True, 0)
            footer.pack_start(self._make_blur_check(), False, False, 0)
            side.pack_start(footer, False, False, 2)

            self.paned.add1(side)
            self.paned.set_position(self._sidebar_position(side))

            # content stack
            self.stack = Gtk.Stack()
            self.stack.set_transition_type(Gtk.StackTransitionType.NONE)
            self.paned.add2(self.stack)

            # Sidebar rows: groups (collapsible, chevron) + leaf rows.
            self.page_rows = {}
            self.group_rows = {}
            self.group_revealed = set()
            try:
                saved = (app.read_ui_pref("expanded") or "").split(",")
                self.group_revealed = set(s for s in saved if s in PAGE_TITLES)
            except Exception:
                self.group_revealed = set()
            if not self.group_revealed:
                self.group_revealed = {"appearance"}
            for key in PAGE_ORDER:
                parent = PAGE_PARENT[key]
                if parent is not None and parent not in self.page_rows:
                    # Guarantee the parent row exists even if NAV_PAGES were
                    # ever reordered.
                    self._make_group_row(parent)
                if PAGE_KIND[key] == "group":
                    self._make_group_row(key)
                else:
                    self._make_leaf_row(key, parent)
            self._apply_expansion()
            self.goto("autostart")

        def _sidebar_icon_alternatives(self):
            """Prefer a panel icon; fall back per icon theme."""
            theme = Gtk.IconTheme.get_default()
            for name in ("pan-end-symbolic", "sidebar-show-symbolic",
                         "emblem-system-symbolic"):
                if theme.lookup_icon(name, 16, 0) is not None:
                    self.sidebar_toggle.set_image(
                        Gtk.Image.new_from_icon_name(name, Gtk.IconSize.BUTTON))
                    return

        def _make_group_row(self, key):
            if key in self.group_rows:
                return
            row = Gtk.ListBoxRow()
            row.set_selectable(False)
            row.get_style_context().add_class("ms-nav-row")
            row.get_style_context().add_class("ms-nav-group")
            box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
            box.set_margin_start(6)
            box.set_margin_end(6)
            box.set_margin_top(6)
            box.set_margin_bottom(6)
            icon = Gtk.Image.new_from_icon_name(
                page_icon(key, 16), Gtk.IconSize.BUTTON)
            box.pack_start(icon, False, False, 0)
            label = Gtk.Label(label=PAGE_TITLES[key], xalign=0, hexpand=True)
            label.get_style_context().add_class("ms-nav-label")
            box.pack_start(label, True, True, 0)
            chevron = Gtk.Image.new_from_icon_name("pan-end-symbolic",
                                                   Gtk.IconSize.MENU)
            if Gtk.IconTheme.get_default().lookup_icon("pan-end-symbolic",
                                                       16, 0) is None:
                chevron = Gtk.Image.new_from_icon_name("go-next-symbolic",
                                                       Gtk.IconSize.MENU)
            chevron.get_style_context().add_class("ms-nav-chevron")
            box.pack_start(chevron, False, False, 0)
            row.add(box)
            row._ms_page = key
            row._ms_chevron = chevron
            row.set_selectable(False)
            row.add_events(Gdk.EventMask.BUTTON_PRESS_MASK)
            row.connect("button-press-event", self.on_group_pressed, key)
            self.sidebar.add(row)
            self.page_rows[key] = row
            self.group_rows[key] = row

        def _make_leaf_row(self, key, parent):
            row = Gtk.ListBoxRow()
            row.get_style_context().add_class("ms-nav-row")
            if parent is not None:
                row.get_style_context().add_class("ms-nav-sub")
            box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
            if parent is None:
                box.set_margin_start(6)
            else:
                box.set_margin_start(28)
            box.set_margin_end(6)
            box.set_margin_top(5)
            box.set_margin_bottom(5)
            icon = Gtk.Image.new_from_icon_name(
                page_icon(key, 16), Gtk.IconSize.BUTTON)
            box.pack_start(icon, False, False, 0)
            label = Gtk.Label(label=PAGE_TITLES[key], xalign=0, hexpand=True)
            label.get_style_context().add_class("ms-nav-label")
            box.pack_start(label, True, True, 0)
            row.add(box)
            row._ms_page = key
            self.sidebar.add(row)
            self.page_rows[key] = row

        def _apply_expansion(self):
            """Show/hide child rows and rotate the group chevrons."""
            for key, row in self.page_rows.items():
                if PAGE_KIND[key] == "group":
                    expanded = key in self.group_revealed
                    ctx = row._ms_chevron.get_style_context()
                    if expanded:
                        ctx.add_class("ms-open")
                        ctx.remove_class("ms-closed")
                    else:
                        ctx.remove_class("ms-open")
                        ctx.add_class("ms-closed")
            self.sidebar.show_all()
            for key, row in self.page_rows.items():
                parent = PAGE_PARENT[key]
                if parent is not None:
                    row.set_visible(parent in self.group_revealed)
            self.app.write_ui_pref(expanded=",".join(sorted(self.group_revealed)))

        def _make_setup_menu(self, app):
            btn = Gtk.MenuButton()
            btn.set_image(Gtk.Image.new_from_icon_name("emblem-system-symbolic",
                                                       Gtk.IconSize.BUTTON))
            btn.set_relief(Gtk.ReliefStyle.NONE)
            btn.set_tooltip_text("Setup")
            menu = Gtk.Menu.new()
            for label, handler in (
                    ("Create default config", lambda *_: app.setup_create_default()),
                    ("Open config folder", lambda *_: app.setup_open_folder()),
                    ("Back up config", lambda *_: app.setup_backup())):
                item = Gtk.MenuItem(label=label)
                item.connect("activate", handler)
                menu.append(item)
            menu.show_all()
            btn.set_popup(menu)
            return btn

        def _make_blur_check(self):
            """Small "Blur" toggle next to the theme selector."""
            check = Gtk.CheckButton(label="Blur")
            check.set_tooltip_text(
                "Translucent window \u2014 MangoWM blurs whatever is behind it "
                "(needs blur=1 in config.conf)")
            check.set_active(self.app.blur_enabled())
            check.connect("toggled", self.app.on_blur_toggled)
            self.blur_check = check
            return check

        def on_header_press(self, widget, event):
            """Drag the chromeless window from the header; double-click
            maximizes."""
            if event.button != 1:
                return False
            if event.type == Gdk.EventType._2BUTTON_PRESS:
                if self.is_maximized():
                    self.unmaximize()
                else:
                    self.maximize()
                return True
            if event.window == self.get_window():
                self.begin_move_drag(event.button, int(event.x_root),
                                     int(event.y_root), event.time)
            return True

        def on_toggle_sidebar(self, _btn):
            child = self.paned.get_child1()
            if child is None:
                return
            hidden = not child.get_visible()
            child.set_visible(hidden)
            self.paned.set_position(
                self._sidebar_position(child) if hidden else 0)

        def _sidebar_min_width(self):
            """Minimum width the sidebar needs so nothing is clipped."""
            try:
                return max(180, self.paned.get_child1().get_preferred_width()[0])
            except Exception:
                return 200

        def _sidebar_position(self, side):
            """Sidebar width that never clips its content."""
            try:
                min_width = side.get_preferred_width()[0]
            except Exception:
                min_width = 200
            try:
                saved = int(self.app.read_ui_pref("sidebar", 0))
            except (TypeError, ValueError):
                saved = 0
            width = saved if saved >= min_width else min_width + 24
            return max(min_width, min(width, max(420, min_width)))

        def on_size_allocate(self, _widget, alloc):
            """Keep the sidebar visible and track a sane window geometry."""
            if alloc.width >= 480 and alloc.height >= 320:
                self._last_size = (alloc.width, alloc.height)
            min_w = self._sidebar_min_width()
            pos = self.paned.get_position()
            if pos >= min_w:
                self._last_pos = pos
            elif alloc.width > min_w * 2:
                new_pos = self._sidebar_position(self.paned.get_child1())
                if new_pos != pos:
                    self.paned.set_position(new_pos)

        def on_group_pressed(self, row, event, key):
            """Single click on a group header toggles its expansion."""
            if event.button != 1:
                return False
            if key in self.group_revealed:
                self.group_revealed.discard(key)
            else:
                self.group_revealed.add(key)
            self._apply_expansion()
            return True

        def on_row_selected(self, _box, row):
            """Single click on a leaf row opens its page."""
            if row is None:
                return
            key = row._ms_page
            if PAGE_KIND[key] == "group":
                return
            child = self.stack.get_child_by_name(key)
            if child is not None:
                self.stack.remove(child)
            page = self.app.make_page(key)
            self.stack.add_named(page, key)
            page.show_all()
            self.stack.set_visible_child_name(key)
            self.title_label.set_text(PAGE_TITLES[key])

        def highlight(self, row):
            for r in self.sidebar.get_children():
                ctx = r.get_style_context()
                if r is row:
                    ctx.add_class("ms-selected")
                else:
                    ctx.remove_class("ms-selected")

        def on_search(self, entry):
            needle = entry.get_text().lower().strip()
            self.app.search_needle = needle
            # Keep every group header visible if any child matches.
            matching = {}
            for key, row in self.page_rows.items():
                if PAGE_KIND[key] == "group":
                    continue
                matching[key] = (not needle) or self.app.page_matches(key, needle)
            for key, row in self.page_rows.items():
                if PAGE_KIND[key] == "group":
                    kids = [k for k in child_pages(key)]
                    row.set_visible(not needle or any(matching.get(k) for k in kids))
                else:
                    parent = PAGE_PARENT[key]
                    ok = matching[key]
                    if needle and parent is not None:
                        ok = ok and parent in self.group_revealed
                    row.set_visible(ok)
            # Re-render the current schema page so its rows are filtered too.
            row = self.sidebar.get_selected_row()
            if row is None:
                return
            key = row._ms_page
            if PAGE_KIND.get(key) != "schema":
                return
            child = self.stack.get_child_by_name(key)
            if isinstance(child, SchemaPage):
                self.stack.remove(child)
                page = self.app.make_page(key)
                self.stack.add_named(page, key)
                page.show_all()
                self.stack.set_visible_child_name(key)

        def set_dirty(self, dirty):
            """Reflect unsaved changes on the Save button."""
            ctx = self.save_btn.get_style_context()
            if dirty:
                ctx.add_class("suggested-action")
                self.save_btn.set_tooltip_text("Save all changed pages (Ctrl+S) \u2014 "
                                               "unsaved changes!")
            else:
                ctx.remove_class("suggested-action")
                self.save_btn.set_tooltip_text("Save all changed pages (Ctrl+S)")

        def current_key(self):
            row = self.sidebar.get_selected_row()
            return row._ms_page if row is not None else None

        def goto(self, key):
            row = self.page_rows.get(key)
            if row is None:
                return
            parent = PAGE_PARENT.get(key)
            if parent is not None and parent not in self.group_revealed:
                self.group_revealed.add(parent)
                self._apply_expansion()
            if row is not self.sidebar.get_selected_row():
                self.sidebar.select_row(row)
            else:
                self.on_row_selected(self.sidebar, row)

        def rebuild_current(self):
            """Rebuild the visible page (after undo/redo)."""
            key = self.current_key()
            if key is None:
                return
            child = self.stack.get_child_by_name(key)
            if child is not None:
                self.stack.remove(child)
            page = self.app.make_page(key)
            self.stack.add_named(page, key)
            page.show_all()
            self.stack.set_visible_child_name(key)

        def fit_sidebar(self):
            """Re-apply the sidebar width once widgets are realized."""
            try:
                self.paned.set_position(
                    self._sidebar_position(self.paned.get_child1()))
            except Exception:
                pass

        def on_destroy(self, *_a):
            """Persist the window geometry for the next launch."""
            try:
                width, height = getattr(self, "_last_size", (0, 0))
                pos = getattr(self, "_last_pos", 0)
                if width >= 480 and height >= 320:
                    self.app.write_ui_pref(width=width, height=height, sidebar=pos)
            except Exception:
                pass

    # ------------------------------------------------------------------ app
    class MangoApp(object):
        def __init__(self, args):
            self.args = args
            self.window = None
            self.config = None
            self._rule_files = {}
            self.settings_ini = os.path.expanduser(APP_SETTINGS_FILE)
            self.search_needle = ""      # active Ctrl+F text (settings search)
            # Session undo/redo stacks (mirrors the web settings' history).
            self._undo_stack = []
            self._redo_stack = []

        # -- wiring ---------------------------------------------------------
        def run(self):
            self.gtk_app = Gtk.Application(application_id=APP_ID,
                                           flags=Gio.ApplicationFlags.NON_UNIQUE)
            self.gtk_app.connect("activate", self.on_activate)
            self.gtk_app.register(None)
            self.gtk_app.run([])
            return 0

        def on_activate(self, *_a):
            self.load_config()
            self.window = SettingsWindow(self)
            self.gtk_app.add_window(self.window)
            self._install_accelerators()
            self.window.show_all()
            self.window._apply_expansion()
            self.window.fit_sidebar()
            self.apply_theme(self.read_theme_pref())
            self.window.theme_combo.handler_block_by_func(self.on_theme_changed)
            self.window.theme_combo.set_active(
                ["system", "dark", "light"].index(self.read_theme_pref()))
            self.window.theme_combo.handler_unblock_by_func(self.on_theme_changed)
            self.update_history_buttons()
            if self.args.page:
                self.window.goto(self.args.page)
            if not self.config.exists:
                self.offer_default_config()

        def _install_accelerators(self):
            actions = {
                "save": ("save", ["<Ctrl>s"], self.action_save),
                "reload-mango": ("reload-mango", ["<Ctrl>r"], self.action_reload_mango),
                "quit-app": ("quit-app", ["<Ctrl>q"], self.action_quit),
                "find": ("find", ["<Ctrl>f", "<Ctrl>k"], self.action_find),
                "undo": ("undo", ["<Ctrl>z"], self.action_undo),
                "redo": ("redo", ["<Ctrl><Shift>z"], self.action_redo),
            }
            self.win_actions = {}
            for name, (label, accels, handler) in actions.items():
                act = Gio.SimpleAction.new(label, None)
                act.connect("activate", lambda a, p, h=handler: h())
                self.window.add_action(act)
                # In GTK 3 accelerators are registered on the Gtk.Application,
                # with the action scope prefix ("win." for window-scoped actions).
                self.gtk_app.set_accels_for_action("win." + label, accels)
                self.win_actions[label] = act

        # -- config plumbing ---------------------------------------------------
        def load_config(self):
            self.config = MangoConfig(root=self.args.config)
            self.config.load()
            self._rule_files = {}

        def rule_file(self, which):
            if which in self._rule_files:
                return self._rule_files[which]
            paths = {"bind": DEFAULT_BIND_PATH, "rule": DEFAULT_RULE_PATH,
                     "monitor": DEFAULT_MONITOR_PATH,
                     "config": self.config.root_path}
            if which == "config":
                rf = RuleFile(self.config.root_path)
                rf.exists = self.config.exists
                rf.load() if self.config.exists else None
            else:
                rf = RuleFile(paths[which])
                if not rf.exists:
                    rf.raw_lines = []
                    _add_issue(self.config.issues, Issue.WARNING,
                               "%s not found (using an in-memory copy; it is created "
                               "on first edit)" % paths[which])
            self._rule_files[which] = rf
            return rf

        def make_page(self, key):
            kind = PAGE_KIND.get(key)
            if kind == "schema":
                return SchemaPage(self, key)
            classes = {
                "autostart": AutostartPage, "environment": EnvPage,
                "bindings": KeybindsPage,
                "monitor-rules": MonitorsPage, "tag-rules": TagRulesPage,
                "window-rules": WindowRulesPage, "layer-rules": LayerRulesPage,
                "theming": ThemingPage, "background": BackgroundPage,
                "notifications": NotificationsPage, "files": ConfigFilesPage,
                "system": SystemInfoPage,
            }
            cls = classes.get(key)
            if cls is None:
                return SchemaPage(self, "gaps-borders")
            return cls(self)

        def on_page_changed(self, page):
            if self.window is not None:
                self.window.set_dirty(True)

        def page_matches(self, key, needle):
            return page_matches(key, needle)

        def reload_config(self):
            self.load_config()
            if self.window:
                self.window.queue_draw()

        def offer_default_config(self):
            default = "/usr/share/mango/config.conf"
            dlg = Gtk.MessageDialog(transient_for=self.window, modal=True,
                                    message_type=Gtk.MessageType.QUESTION,
                                    text="No MangoWM config found")
            dlg.format_secondary_text(
                "There is no config at %s yet. Copy MangoWM's default config "
                "now, or start with an empty one?" % os.path.expanduser(DEFAULT_CONFIG_PATH))
            dlg.add_buttons("Start empty", Gtk.ResponseType.REJECT,
                            "Copy default", Gtk.ResponseType.ACCEPT, "_Quit", Gtk.ResponseType.CLOSE)
            resp = dlg.run(); dlg.destroy()
            if resp == Gtk.ResponseType.CLOSE:
                self.action_quit(); return
            if resp == Gtk.ResponseType.ACCEPT and os.path.exists(default):
                os.makedirs(os.path.dirname(self.config.root_path), exist_ok=True)
                shutil.copy2(default, self.config.root_path)
            elif resp == Gtk.ResponseType.ACCEPT:
                self.error_dialog("Default config not found at %s — starting empty. "
                                  "MangoWM's defaults will be used until you save."
                                  % default)
                return
            else:
                os.makedirs(os.path.dirname(self.config.root_path), exist_ok=True)
                open(self.config.root_path, "a").close()
            self.load_config()

        # -- setup menu ----------------------------------------------------
        def setup_create_default(self):
            """Replace the config with MangoWM's default (with a confirm)."""
            default = "/usr/share/mango/config.conf"
            if not os.path.exists(default):
                self.error_dialog("MangoWM's default config was not found at %s."
                                  % default)
                return
            if self.config.exists:
                dlg = Gtk.MessageDialog(transient_for=self.window, modal=True,
                                        message_type=Gtk.MessageType.WARNING,
                                        text="Replace configuration?")
                dlg.format_secondary_text(
                    "The current configuration will be backed up, then replaced "
                    "with the default.")
                dlg.add_buttons("_Cancel", Gtk.ResponseType.CANCEL,
                                "_Replace", Gtk.ResponseType.ACCEPT)
                resp = dlg.run(); dlg.destroy()
                if resp != Gtk.ResponseType.ACCEPT:
                    return
            os.makedirs(os.path.dirname(self.config.root_path), exist_ok=True)
            if self.config.exists:
                try:
                    self.config.save()
                except ConfigError:
                    pass
            shutil.copy2(default, self.config.root_path)
            self.load_config()
            self.error_dialog("Default config restored.", error=False)

        def setup_open_folder(self):
            folder = os.path.dirname(os.path.expanduser(self.config.root_path))
            for argv in (["xdg-open", folder], ["mmsg", "exec", "xdg-open %s" % folder]):
                try:
                    subprocess.Popen(argv)
                    return
                except OSError:
                    continue
            self.error_dialog("Could not open %s (no xdg-open)." % folder)

        def setup_backup(self):
            try:
                saved = self.config.save()
            except ConfigError as exc:
                self.error_dialog(str(exc))
                return
            for rf in self._rule_files.values():
                rf.save()
            self.error_dialog("Backed up %d file(s) to %s." % (
                len(saved), self.config.backup_root), error=False)

        # -- undo / redo -----------------------------------------------------
        def record_change(self, undo_fn, redo_fn):
            """Push an undoable change onto the session history."""
            self._undo_stack.append((undo_fn, redo_fn))
            self._redo_stack = []
            self.update_history_buttons()

        def record_config_undo(self, key, old_value):
            """Undo helper for a single option edit (set or reset)."""
            had = self.config.has(key)
            new_value = self.config.get(key)

            def undo():
                if had and old_value is not None:
                    self.config.set(key, old_value)
                else:
                    self.config.unset(key)

            def redo():
                if new_value is not None:
                    self.config.set(key, new_value)
                else:
                    self.config.unset(key)

            self.record_change(undo, redo)

        def record_multi_undo(self, key, old_values, new_values):
            """Undo helper for multi-value keys (exec, exec_once, env)."""
            def restore(values):
                self.config.unset(key)
                for v in values:
                    try:
                        self.config.add_value(key, v)
                    except ConfigError:
                        pass

            self.record_change(
                lambda: restore(list(old_values)),
                lambda: restore(list(new_values)))

        # -- rule-file snapshots for undo ------------------------------------
        def rule_snapshot(self, which):
            return list(self.rule_file(which).raw_lines)

        def rule_restore(self, which, snap):
            rf = self.rule_file(which)
            rf.raw_lines = list(snap)
            rf.dirty = True

        def rule_change(self, which):
            """Record an undoable RuleFile mutation (call around it)."""
            before = self.rule_snapshot(which)

            def commit():
                after = self.rule_snapshot(which)
                self.record_change(
                    lambda: self.rule_restore(which, before),
                    lambda: self.rule_restore(which, after))

            return commit

        def action_undo(self, *_a):
            if not self._undo_stack:
                return
            undo_fn, redo_fn = self._undo_stack.pop()
            try:
                undo_fn()
            except ConfigError as exc:
                self.error_dialog(str(exc))
                return
            self._redo_stack.append((undo_fn, redo_fn))
            if self.window is not None:
                self.window.set_dirty(True)
                self.window.rebuild_current()
            self.update_history_buttons()

        def action_redo(self, *_a):
            if not self._redo_stack:
                return
            undo_fn, redo_fn = self._redo_stack.pop()
            try:
                redo_fn()
            except ConfigError as exc:
                self.error_dialog(str(exc))
                return
            self._undo_stack.append((undo_fn, redo_fn))
            if self.window is not None:
                self.window.set_dirty(True)
                self.window.rebuild_current()
            self.update_history_buttons()

        def update_history_buttons(self):
            if self.window is None:
                return
            self.window.undo_btn.set_sensitive(bool(self._undo_stack))
            self.window.redo_btn.set_sensitive(bool(self._redo_stack))

        # -- actions ------------------------------------------------------------
        def action_save(self, *_a):
            try:
                saved = self.config.save()
                for rf in self._rule_files.values():
                    rf.save()
            except ConfigError as exc:
                self.error_dialog(str(exc))
                return
            for key in self.window.page_rows:
                child = self.window.stack.get_child_by_name(key)
                if child is not None and hasattr(child, "clear_dirty"):
                    child.clear_dirty()
            self.window.set_dirty(False)
            self.error_dialog("Saved %d file(s)." % (len(saved) +
                             sum(1 for rf in self._rule_files.values() if rf.dirty)),
                              error=False)
            self.load_config()

        def action_reload_mango(self, *_a):
            try:
                self.config.save()
                for rf in self._rule_files.values():
                    rf.save()
            except ConfigError as exc:
                self.error_dialog(str(exc)); return
            ok, message = reload_mango()
            if ok:
                self.error_dialog("MangoWM reloaded.", error=False)
            else:
                self.error_dialog("Reload failed: %s" % message)

        def action_quit(self, *_a):
            dirty = []
            if self.config:
                dirty = self.config.changed_files()
                dirty += [rf for rf in self._rule_files.values() if rf.dirty]
            if dirty:
                dlg = Gtk.MessageDialog(transient_for=self.window, modal=True,
                                        message_type=Gtk.MessageType.WARNING,
                                        text="Save unsaved changes before quitting?")
                dlg.add_buttons("_Discard", Gtk.ResponseType.REJECT,
                                "_Save", Gtk.ResponseType.ACCEPT,
                                "_Cancel", Gtk.ResponseType.CANCEL)
                resp = dlg.run(); dlg.destroy()
                if resp == Gtk.ResponseType.CANCEL:
                    return
                if resp == Gtk.ResponseType.ACCEPT:
                    self.action_save()
            self.gtk_app.quit()

        def action_find(self, *_a):
            self.window.search.grab_focus()

        # -- dialogs / theme ----------------------------------------------------
        def error_dialog(self, message, error=True):
            dlg = Gtk.MessageDialog(transient_for=self.window, modal=True,
                                    message_type=Gtk.MessageType.ERROR if error
                                    else Gtk.MessageType.INFO,
                                    buttons=Gtk.ButtonsType.OK,
                                    text=message[:400])
            dlg.run(); dlg.destroy()

        def warning_in_dialog(self, dlg, message):
            dlg.get_content_area().pack_start(
                Gtk.Label(label=message), False, False, 4)
            dlg.show_all()

        def read_theme_pref(self):
            try:
                import configparser
                cp = configparser.ConfigParser()
                cp.read(self.settings_ini)
                val = cp.get("ui", "theme", fallback="system")
                return val if val in ("system", "dark", "light") else "system"
            except Exception:
                return "system"

        def write_theme_pref(self, theme):
            self.write_ui_pref(theme=theme)

        def read_ui_pref(self, key, default=None):
            """Read a value from the [ui] section of ~/.config/mango-settings."""
            try:
                import configparser
                cp = configparser.ConfigParser()
                cp.read(self.settings_ini)
                return cp.get("ui", key, fallback=default)
            except Exception:
                return default

        def write_ui_pref(self, **values):
            """Merge values into the [ui] section, keeping other keys."""
            try:
                import configparser
                os.makedirs(os.path.dirname(self.settings_ini), exist_ok=True)
                cp = configparser.ConfigParser()
                cp.read(self.settings_ini)
                if not cp.has_section("ui"):
                    cp.add_section("ui")
                for key, value in values.items():
                    cp.set("ui", key, str(value))
                with open(self.settings_ini, "w") as fh:
                    cp.write(fh)
            except OSError:
                pass

        def on_theme_changed(self, combo):
            theme = combo.get_active_text()
            if theme:
                self.apply_theme(theme)
                self.write_theme_pref(theme)

        def apply_theme(self, theme):
            prefer_dark = theme == "dark" or (theme == "system" and
                          (gsettings_get(GTK_THEME_SCHEMA, "color-scheme") == "prefer-dark"))
            Gtk.Settings.get_default().set_property(
                "gtk-application-prefer-dark-theme", bool(prefer_dark))
            self._prefer_dark = bool(prefer_dark)
            # Re-render the stylesheet for the new light/dark palette.
            self.apply_blur()

        # -- translucent / blurred surface ------------------------------------
        def blur_enabled(self):
            return self.read_ui_pref("blur", "1") != "0"

        def apply_blur(self):
            """(Re)load the app stylesheet with or without translucency.

            Colors are the MangoWM web settings' semantic palette (warm
            cream/mango light, warm dark), applied literally so the app looks
            the same on every GTK theme.  With blur enabled the surface is
            semi-transparent so MangoWM's blur shows through.
            """
            on = self.blur_enabled() and getattr(self.window, "translucent", False)
            dark = bool(getattr(self, "_prefer_dark", False))
            if dark:
                bg, fg, card = "#090703", "#f3f2ed", "#191510"
                border, primary, primary_fg = "rgba(255,255,255,0.10)", "#f3b01d", "#1a0f01"
                muted_fg, sidebar = "#bbb7ae", "#191510"
                destructive = "#ff6467"
            else:
                bg, fg, card = "#fefdfa", "#291d0f", "#ffffff"
                border, primary, primary_fg = "#e6e1d3", "#f5aa00", "#412705"
                muted_fg, sidebar = "#635646", "#fcfaf3"
                destructive = "#e7000b"
            bg_a = "0.80" if on else "1.0"
            card_a = "0.82" if on else "1.0"
            side_a = "0.75" if on else "1.0"
            radius = "14px" if on else "0px"
            css = """
                @define-color ms_fg %(fg)s;
                @define-color ms_primary %(primary)s;
                @define-color ms_destructive %(destructive)s;

                window.app-window { background-color: transparent; }
                .app-surface {
                    background-color: alpha(%(bg)s, %(bg_a)s);
                    border-radius: %(radius)s;
                }
                .ms-header {
                    background-color: transparent;
                    border-bottom: 1px solid alpha(%(border)s, 0.9);
                }
                .ms-header-title { font-weight: 600; color: %(fg)s; }
                .ms-search entry, entry.ms-search {
                    background-color: alpha(%(fg)s, 0.05);
                    border: 1px solid alpha(%(border)s, 0.8);
                    border-radius: 8px;
                    color: %(fg)s;
                }
                .ms-search image { color: alpha(%(muted_fg)s, 0.9); }

                .app-sidebar {
                    background-color: alpha(%(sidebar)s, %(side_a)s);
                    border-right: 1px solid alpha(%(border)s, 0.55);
                }
                .ms-brand-name { color: %(fg)s; font-size: 105%%; }
                .ms-brand-version { color: alpha(%(muted_fg)s, 0.8); }
                list.ms-nav { background-color: transparent; }
                .ms-nav-row { border-radius: 8px; }
                .ms-nav-row:hover { background-color: alpha(%(fg)s, 0.06); }
                .ms-nav-row.ms-selected {
                    background-color: alpha(%(primary)s, 0.20);
                }
                .ms-nav-row.ms-selected label { color: %(fg)s; font-weight: 600; }
                .ms-nav-label { color: %(fg)s; }
                .ms-nav-group label { font-weight: 600; }
                .ms-nav-chevron { color: alpha(%(muted_fg)s, 0.9); }
                .ms-nav-chevron.ms-open  { -gtk-icon-transform: rotate(0.25turn); }
                .ms-nav-chevron.ms-closed { -gtk-icon-transform: rotate(0turn); }

                /* Cards and rows */
                .ms-card {
                    background-color: alpha(%(card)s, %(card_a)s);
                    border: 1px solid alpha(%(border)s, 0.85);
                    border-radius: 12px;
                }
                .ms-card-title { font-weight: 600; color: %(fg)s; }
                .ms-page-title { color: %(fg)s; }
                .ms-page-desc { color: alpha(%(muted_fg)s, 0.9); }
                .ms-note { color: alpha(%(muted_fg)s, 0.85); }
                list.ms-rows { background-color: transparent; }
                .ms-row { background-color: transparent; }
                .ms-row:hover { background-color: alpha(%(fg)s, 0.045); }
                .ms-row-label { color: %(fg)s; font-weight: 500; }
                .ms-row-label.set-in-config { font-weight: 700; }
                .ms-row-desc { color: alpha(%(muted_fg)s, 0.75); }
                .ms-state-on { color: %(primary)s; font-weight: 700; }
                .ms-state-off { color: alpha(%(muted_fg)s, 0.45); font-weight: 700; }
                .ms-count { color: alpha(%(muted_fg)s, 0.85); }
                .ms-chip {
                    background-color: transparent;
                    border: 1px solid alpha(%(border)s, 0.9);
                    border-radius: 9999px;
                    color: %(muted_fg)s;
                    padding: 2px 10px;
                    font-size: 92%%;
                }
                .ms-chip:hover { background-color: alpha(%(fg)s, 0.06); }
                .ms-chip:checked {
                    background-color: %(primary)s;
                    color: %(primary_fg)s;
                    border-color: %(primary)s;
                    font-weight: 600;
                }
                .ms-badge {
                    background-color: alpha(%(primary)s, 0.22);
                    color: %(primary)s;
                    border-radius: 6px;
                    padding: 2px 8px;
                    font-family: monospace;
                    font-weight: 600;
                }
                .ms-cmd-label { color: alpha(%(muted_fg)s, 0.9); font-size: 92%%; }
                .ms-cmd {
                    background-color: alpha(%(fg)s, 0.07);
                    border-radius: 6px;
                    padding: 2px 8px;
                    font-family: monospace;
                    font-size: 92%%;
                    color: %(fg)s;
                }
                .ms-key {
                    background-color: alpha(%(fg)s, 0.10);
                    border: 1px solid alpha(%(border)s, 0.9);
                    border-radius: 6px;
                    padding: 1px 7px;
                    font-family: monospace;
                    font-size: 92%%;
                    color: %(fg)s;
                }
                .ms-plus { color: alpha(%(muted_fg)s, 0.55); }
                .ms-group-title {
                    color: %(muted_fg)s;
                    font-weight: 700;
                    font-size: 88%%;
                }
                .ms-readout { color: %(fg)s; font-family: monospace; }
                .ms-reset { color: alpha(%(muted_fg)s, 0.7); padding: 3px;
                            background: transparent; border: none;
                            border-radius: 6px; }
                .ms-reset:hover { color: %(fg)s;
                                  background: alpha(%(fg)s, 0.08); }
                .ms-reset:disabled { color: alpha(%(muted_fg)s, 0.25); }
                .ms-prompt { color: %(primary)s; font-family: monospace;
                             font-weight: 700; }
                .ms-add-button { padding: 2px 6px; }
                .ms-secondary-button { padding: 2px 10px; }
                .ms-add-row entry, .app-sidebar entry,
                spinbutton, entry, combobox button,
                combobox entry {
                    background-color: alpha(%(fg)s, 0.04);
                    border: 1px solid alpha(%(border)s, 0.8);
                    border-radius: 8px;
                    color: %(fg)s;
                    box-shadow: none;
                }
                scale highlight { background-color: %(primary)s; }
                scale slider {
                    background-color: %(card)s;
                    border: 1px solid alpha(%(border)s, 1.0);
                }
                switch { color: %(primary_fg)s; }
                switch:checked { background-color: %(primary)s; }
                button.suggested-action {
                    background-color: %(primary)s;
                    color: %(primary_fg)s;
                    border: none;
                    border-radius: 8px;
                }
                .destructive-action { color: %(destructive)s; }
                tooltip { background-color: %(fg)s; color: %(bg)s; border-radius: 8px; }
            """ % {
                "bg": bg, "fg": fg, "card": card, "border": border,
                "primary": primary, "primary_fg": primary_fg,
                "muted_fg": muted_fg, "sidebar": sidebar,
                "destructive": destructive,
                "bg_a": bg_a, "card_a": card_a, "side_a": side_a,
                "radius": radius,
            }
            provider = getattr(self, "_css_provider", None)
            if provider is None:
                provider = Gtk.CssProvider()
                Gtk.StyleContext.add_provider_for_screen(
                    Gdk.Screen.get_default(), provider,
                    Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
                self._css_provider = provider
            try:
                provider.load_from_data(css.encode("utf-8"))
            except Exception as exc:      # never let styling break the app
                sys.stderr.write("mango-settings: stylesheet error: %s\n" % exc)

        def on_blur_toggled(self, switch, _pspec=None):
            self.write_ui_pref(blur="1" if switch.get_active() else "0")
            self.apply_blur()
    app = MangoApp(args)
    return app.run()



# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def cmd_check(config):
    """Headless validation: prints include problems; exit 1 on errors."""
    config.load()
    errors, warnings = config.summary()
    for issue in warnings + errors:
        print(issue)
    print("Config: %s — %d file(s) loaded, %d error(s), %d warning(s)"
          % (config.root_path, len(config.order), len(errors), len(warnings)))
    return 1 if errors else 0


def build_parser():
    parser = argparse.ArgumentParser(
        prog="mango-settings", description="Settings manager for MangoWM")
    parser.add_argument("--page", metavar="PAGE",
                        help="open a specific page (see --list-pages)")
    parser.add_argument("--config", metavar="FILE",
                        help="config file to edit (default: %s)" % DEFAULT_CONFIG_PATH)
    parser.add_argument("--check", action="store_true",
                        help="validate the config and exit (no GUI)")
    parser.add_argument("--list-pages", action="store_true",
                        help="list page names and exit")
    parser.add_argument("--version", action="store_true",
                        help="print version and exit")
    return parser


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.version:
        print("%s %s" % (APP_NAME, VERSION))
        return 0
    if args.list_pages:
        for key in PAGE_ORDER:
            print(PAGE_TITLES[key])
        return 0
    if args.page:
        resolved = resolve_page(args.page)
        if resolved is None:
            sys.stderr.write("Unknown page %r. Valid pages:\n%s\n" %
                             (args.page, "\n".join("  %s (key: %s)" % (PAGE_TITLES[k], k)
                                                    for k in PAGE_ORDER)))
            return 2
        args.page = resolved
    if args.check:
        return cmd_check(MangoConfig(root=args.config))
    return run_gui(args)


if __name__ == "__main__":
    sys.exit(main())
