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

VERSION = "2.1.0"
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


PAGE_ORDER = [
    "appearance", "animations", "behavior", "layouts", "keybindings",
    "workspaces", "windowrules", "monitors", "input", "startup",
    "theming", "background", "notifications", "files", "system",
]

PAGE_TITLES = {
    "appearance": "Appearance",
    "animations": "Animations",
    "behavior": "Window behavior",
    "layouts": "Layouts",
    "keybindings": "Keybindings",
    "workspaces": "Workspaces",
    "windowrules": "Window rules",
    "monitors": "Monitors",
    "input": "Input devices",
    "startup": "Startup apps",
    "theming": "Theming",
    "background": "Background",
    "notifications": "Notifications",
    "files": "Config files",
    "system": "System info",
}


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
SCHEMA_OPTIONS = {}
for _opts in SCHEMA.values():
    for _o in _opts:
        SCHEMA_OPTIONS[_o.key] = _o

MULTI_KEYS = {"exec", "exec_once"}          # may appear many times in config.conf
# Options handled on other pages / by other files — hidden from Appearance etc.
HIDDEN_OPTION_KEYS = {"exec", "exec_once"}


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
    if mmsg_available():
        for argv in (["mmsg", "version"], ["mmsg", "--version"]):
            rc, out = run_cmd(argv)
            if rc == 0 and out:
                return out.splitlines()[0]
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

    from gi.repository import Gtk, Gdk, Gio, GLib

    # ------------------------------------------------------------------ pages
    class PageBase(Gtk.Box):
        """Common scaffolding: titled, scrollable, reports changes upward."""

        page_key = None

        def __init__(self, app):
            super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=6)
            self.app = app
            self.set_border_width(12)
            head = Gtk.Label(xalign=0)
            head.set_markup("<b>%s</b>" % GLib.markup_escape_text(PAGE_TITLES[self.page_key]))
            head.get_style_context().add_class("large-title") if hasattr(head.get_style_context(), "add_class") else None
            self.pack_start(head, False, False, 6)
            self.content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
            scroll = Gtk.ScrolledWindow()
            scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
            scroll.add(self.content)
            self.pack_start(scroll, True, True, 0)
            self.changed = False
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
            self.content.pack_start(label, False, False, 2)

        def add_frame(self, title=None):
            frame = Gtk.Frame()
            frame.set_shadow_type(Gtk.ShadowType.NONE)
            box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
            frame.add(box)
            self.content.pack_start(frame, False, False, 2)
            return box

    class SchemaPage(PageBase):
        """Generated from the schema: one row per option definition."""

        def __init__(self, app, page_key):
            self.page_key = page_key
            super().__init__(app)

        def build(self):
            grid = None
            current_group = None
            self.widgets = {}

            def finish_frame():
                if grid is not None:
                    frame = self.add_frame()
                    frame.add(grid)

            row = 0
            for opt in SCHEMA[self.page_key]:
                if self.page_key != "appearance" and opt.key in HIDDEN_OPTION_KEYS:
                    continue
                # Start a new titled frame whenever the section changes.
                if opt.group != current_group:
                    finish_frame()
                    if opt.group:
                        head = Gtk.Label(xalign=0)
                        head.set_markup("<b>%s</b>" %
                                        GLib.markup_escape_text(opt.group))
                        self.content.pack_start(head, False, False, 2)
                    grid = Gtk.Grid(column_spacing=12, row_spacing=8)
                    grid.set_margin_top(2)
                    current_group = opt.group
                    row = 0
                grid.attach(Gtk.Label(label=opt.label, xalign=0), 0, row, 1, 1)
                widget = self._make_widget(opt)
                self.widgets[opt.key] = (opt, widget)
                grid.attach(widget, 1, row, 1, 1)
                if opt.desc:
                    note = Gtk.Label(xalign=0)
                    note.set_markup("<small>%s</small>" % GLib.markup_escape_text(opt.desc))
                    note.get_style_context().add_class("dim-label")
                    grid.attach(note, 2, row, 1, 1)
                row += 1
            finish_frame()
            if not self.widgets:
                self.add_note("Nothing to configure here.")
            self.show_all()

        def _current(self, opt):
            val = self.app.config.get(opt.key)
            if val is None:
                val = opt.default
            return val

        def _make_widget(self, opt):
            if opt.type == OptDef.BOOL:
                w = Gtk.Switch(halign=Gtk.Align.START)
                w.set_active((self._current(opt) or "0") == "1")
                w.connect("notify::active", self._on_bool, opt)
                return w
            if opt.type in (OptDef.INT, OptDef.FLOAT):
                adj = Gtk.Adjustment(
                    float(self._current(opt) or opt.default),
                    float(opt.min if opt.min is not None else 0),
                    float(opt.max if opt.max is not None else 100000),
                    1.0 if opt.type == OptDef.INT else 0.05,
                    10.0, 0)
                w = Gtk.SpinButton(adjustment=adj,
                                   digits=0 if opt.type == OptDef.INT else 2)
                w.connect("value-changed", self._on_number, opt)
                return w
            if opt.type == OptDef.CHOICE:
                w = Gtk.ComboBoxText()
                for c in opt.choices:
                    w.append_text(c)
                cur = self._current(opt) or opt.default
                if opt.choices and cur not in opt.choices:
                    cur = opt.default
                w.set_active(opt.choices.index(cur) if cur in opt.choices else 0)
                w.connect("changed", self._on_choice, opt)
                return w
            w = Gtk.Entry(width_chars=24, hexpand=True)
            w.set_text(self._current(opt) or "")
            w.connect("changed", self._on_text, opt)
            return w

        def _apply(self, opt, value):
            try:
                self.app.config.set(opt.key, value)
                self.mark_changed()
            except ConfigError as exc:
                self.app.error_dialog(str(exc))

        def _on_bool(self, sw, _pspec, opt):
            self._apply(opt, "1" if sw.get_active() else "0")

        def _on_number(self, spin, opt):
            if opt.type == OptDef.INT:
                self._apply(opt, str(int(spin.get_value())))
            else:
                self._apply(opt, ("%f" % spin.get_value()).rstrip("0").rstrip("."))

        def _on_choice(self, combo, opt):
            text = combo.get_active_text()
            if text:
                self._apply(opt, text)

        def _on_text(self, entry, opt):
            self._apply(opt, entry.get_text())

    # -------------------------------------------------------------- list pages
    class RuleListPage(PageBase):
        """Editor for a RuleFile: list of records with add/edit/delete."""

        record_name = None

        def __init__(self, app):
            super().__init__(app)

        def rules(self):
            return self.app.rule_file(self.rule_file_attr()).records(self.record_name)

        def build(self):
            self.listbox = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
            self.listbox.set_selection_mode(Gtk.SelectionMode.NONE)
            frame = self.add_frame()
            frame.add(self.listbox)
            # Newer PyGObject overrides no longer accept "layout" as a
            # constructor kwarg; set the layout style explicitly instead.
            btns = Gtk.ButtonBox(spacing=6)
            btns.set_layout(Gtk.ButtonBoxStyle.START)
            add = Gtk.Button(label="Add")
            add.connect("clicked", self.on_add)
            btns.add(add)
            self.content.pack_start(btns, False, False, 4)
            self.rebuild()

        def rule_file_attr(self):
            raise NotImplementedError

        def field_labels(self):
            raise NotImplementedError

        def edit_dialog(self, fields=None, on_ok=None):
            dlg = Gtk.Dialog(title=self.record_name.capitalize(),
                             transient_for=self.get_toplevel(), modal=True)
            dlg.add_buttons("_Cancel", Gtk.ResponseType.CANCEL, "_Save", Gtk.ResponseType.OK)
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
            conflicts = dict(RuleFile.bind_conflicts(records))
            for index, fields in records:
                row = Gtk.ListBoxRow()
                box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
                text = ", ".join(fields)[:80]
                label = Gtk.Label(label=text, xalign=0, hexpand=True)
                box.pack_start(label, True, True, 8)
                if index in conflicts:
                    warn = Gtk.Label()
                    warn.set_markup("<span foreground='orange'>⚠ %s</span>"
                                    % GLib.markup_escape_text(conflicts[index]))
                    box.pack_start(warn, False, False, 4)
                for lbl, handler, arg in (("Edit", self.on_edit, index),
                                          ("Delete", self.on_delete, index)):
                    b = Gtk.Button(label=lbl)
                    b.connect("clicked", handler, arg)
                    box.pack_start(b, False, False, 2)
                row.add(box)
                self.listbox.add(row)
            self.listbox.show_all()
            if not records:
                self.add_note("No %s entries yet — click Add." % self.record_name)

        def after_change(self):
            self.app.rule_file(self.rule_file_attr()).save()
            self.clear_dirty()
            self.rebuild()

        def on_add(self, _btn):
            self.edit_dialog(on_ok=self._added)

        def _added(self, values):
            self.app.rule_file(self.rule_file_attr()).add(self.record_name, values)
            self.after_change()

        def on_edit(self, _btn, index):
            fields = self.app.rule_file(self.rule_file_attr()).raw_lines[index][1][1]
            self.edit_dialog(fields=list(fields), on_ok=lambda v: self._edited(index, v))

        def _edited(self, index, values):
            self.app.rule_file(self.rule_file_attr()).update(index, values)
            self.after_change()

        def on_delete(self, _btn, index):
            self.app.rule_file(self.rule_file_attr()).remove(index)
            self.after_change()

    class KeybindsPage(RuleListPage):
        page_key = "keybindings"
        record_name = "bind"

        def rule_file_attr(self):
            return "bind"

        def field_labels(self):
            return ["Modifiers (SUPER, SHIFT…)", "Key", "Action", "Arguments"]

        def build(self):
            super().build()
            self.add_note("Conflicting binds (same modifiers and key) are flagged. "
                          "Ctrl+R sends the reload to MangoWM after saving.")

    class WindowRulesPage(RuleListPage):
        page_key = "windowrules"
        record_name = "window_rule"

        def rule_file_attr(self):
            return "rule"

        def field_labels(self):
            return ["Window class", "Title", "Tags", "Float (0/1)", "Monitor"]

        def build(self):
            super().build()
            btn_once = Gtk.Button(label="Add window_rule_once")
            btn_once.connect("clicked", self.on_add_once)
            self.content.pack_start(btn_once, False, False, 2)

        def on_add_once(self, _btn):
            self.edit_dialog(on_ok=self._added_once)

        def _added_once(self, values):
            self.app.rule_file("rule").add("window_rule_once", values)
            self.after_change()

    class MonitorsPage(RuleListPage):
        page_key = "monitors"
        record_name = "monitor"

        def rule_file_attr(self):
            return "monitor"

        def field_labels(self):
            return ["Name", "Enabled (0/1)", "X", "Y", "Width", "Height",
                    "Scale", "Refresh", "Transform"]

        def build(self):
            super().build()
            self.detect_btn = Gtk.Button(label="Add connected displays (mmsg)")
            self.detect_btn.connect("clicked", self.on_detect)
            self.content.pack_start(self.detect_btn, False, False, 2)
            if not mmsg_available():
                self.add_note("mmsg not found — 'Add connected displays' needs "
                              "MangoWM's mmsg. You can still add monitors manually.")

        def on_detect(self, _btn):
            monitors = connected_monitors()
            if not monitors:
                self.app.error_dialog("mmsg reported no connected displays "
                                      "(or mmsg is not installed).")
                return
            rf = self.app.rule_file("monitor")
            for name, _desc in monitors:
                if not any(rec[1] and rec[1][0] == name for rec in rf.records("monitor")):
                    rf.add("monitor", [name, "1", "0", "0", "-1", "-1", "1", "0", "normal"])
            self.after_change()

    class StartupPage(RuleListPage):
        page_key = "startup"
        record_name = "exec"

        def rule_file_attr(self):
            return "config"

        def rules(self):
            out = []
            for key in ("exec", "exec_once"):
                for value in self.app.config.get_all(key):
                    out.append((key, value))
            return out

        def field_labels(self):
            return ["Command", "Run once per session (0/1)"]

        def rebuild(self):
            for child in self.listbox.get_children():
                self.listbox.remove(child)
            entries = self.rules()
            for i, (key, value) in enumerate(entries):
                row = Gtk.ListBoxRow()
                box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
                label = Gtk.Label(label=value[:90], xalign=0, hexpand=True)
                box.pack_start(label, True, True, 8)
                tag = Gtk.Label()
                tag.set_markup("<small>%s</small>" % key)
                box.pack_start(tag, False, False, 4)
                b = Gtk.Button(label="Delete")
                b.connect("clicked", self.on_delete, i)
                box.pack_start(b, False, False, 2)
                row.add(box)
                self.listbox.add(row)
            self.listbox.show_all()
            if not entries:
                self.add_note("No startup commands yet.")

        def on_add(self, _btn):
            dlg = Gtk.Dialog(title="Add startup command", transient_for=self.get_toplevel(),
                             modal=True)
            dlg.add_buttons("_Cancel", Gtk.ResponseType.CANCEL, "_Add", Gtk.ResponseType.OK)
            box = dlg.get_content_area()
            box.set_spacing(8); box.set_border_width(12)
            entry = Gtk.Entry(width_chars=40)
            entry.set_placeholder_text("e.g. swaync  or  foot --server")
            once = Gtk.CheckButton(label="Run once per session (exec_once)")
            box.add(Gtk.Label(label="Command", xalign=0)); box.add(entry); box.add(once)
            dlg.show_all()
            if dlg.run() == Gtk.ResponseType.OK and entry.get_text().strip():
                key = "exec_once" if once.get_active() else "exec"
                self.app.config.add_value(key, entry.get_text().strip())
                self.app.config.save()
                self.clear_dirty()
                self.rebuild()
            dlg.destroy()

        def on_delete(self, _btn, index):
            key, _value = self.rules()[index]
            self.app.config.remove_value(key, index if key == "exec"
                                         else index - len(self.app.config.get_all("exec")))
            self.app.config.save()
            self.clear_dirty()
            self.rebuild()

    class WorkspacesPage(RuleListPage):
        page_key = "workspaces"
        record_name = "bind"

        def rule_file_attr(self):
            return "bind"

        def field_labels(self):
            return ["Modifiers", "Key", "Action", "Tag / target"]

        def rules(self):
            out = []
            for index, fields in self.app.rule_file("bind").records("bind"):
                if len(fields) >= 4 and fields[2] in ("view", "viewonmon", "tag", "togglegroup"):
                    out.append((index, fields))
            return out

        def rebuild(self):
            for child in self.listbox.get_children():
                self.listbox.remove(child)
            entries = self.rules()
            for index, fields in entries:
                row = Gtk.ListBoxRow()
                box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
                label = Gtk.Label(label=", ".join(fields)[:80], xalign=0, hexpand=True)
                box.pack_start(label, True, True, 8)
                b = Gtk.Button(label="Delete")
                b.connect("clicked", self.on_delete, index)
                box.pack_start(b, False, False, 2)
                row.add(box)
                self.listbox.add(row)
            self.listbox.show_all()
            self.add_note("These are the tag-switching binds in bind.conf "
                          "(actions: view, viewonmon, tag, togglegroup).")

        def _added(self, values):
            if len(values) < 4 or values[2] not in ("view", "viewonmon", "tag", "togglegroup"):
                self.app.error_dialog("Action must be one of: view, viewonmon, tag, togglegroup")
                return
            self.app.rule_file("bind").add("bind", values)
            self.after_change()

        def on_delete(self, _btn, index):
            self.app.rule_file("bind").remove(index)
            self.after_change()

    class ConfigFilesPage(PageBase):
        page_key = "files"

        def build(self):
            self.listbox = Gtk.ListBox()
            frame = self.add_frame()
            frame.add(self.listbox)
            self.rebuild()

        def rebuild(self):
            for child in self.listbox.get_children():
                self.listbox.remove(child)
            errors, warnings = self.app.config.summary()
            by_file = {}
            for issue in errors + warnings:
                by_file.setdefault(issue.path, []).append(issue)
            docs = [self.app.config.docs[r] for r in self.app.config.order]
            extra = [d for d in (self.app.bind_doc, self.app.rule_doc, self.app.monitor_doc)
                     if d is not None]
            for doc in list(dict.fromkeys(docs + [e.path for e in errors])) :
                if not hasattr(doc, "lines"):
                    continue
                problems = by_file.get(doc.path, [])
                row = Gtk.ListBoxRow(activatable=True)
                row.connect("activate", self.on_open, doc)
                box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
                name = Gtk.Label(label=doc.path.replace(os.path.expanduser("~"), "~"),
                                 xalign=0, hexpand=True)
                box.pack_start(name, True, True, 8)
                if problems:
                    errs = [p for p in problems if p.severity == Issue.ERROR]
                    color = "red" if errs else "orange"
                    text = "%d error%s" % (len(errs), "s" if len(errs) != 1 else "") if errs \
                        else "%d warning%s" % (len(problems), "s" if len(problems) != 1 else "")
                    warn = Gtk.Label()
                    warn.set_markup("<span foreground='%s'>%s</span>" % (color, text))
                    box.pack_start(warn, False, False, 4)
                box.pack_start(Gtk.Label(label="edit", ), False, False, 2)
                row.add(box)
                self.listbox.add(row)
            self.listbox.show_all()
            if warnings or errors:
                self.add_note("%d error(s), %d warning(s) across all files. Click a file "
                              "to view and edit it raw." % (len(errors), len(warnings)))

        def on_open(self, _row, doc):
            path = doc.path if hasattr(doc, "path") else doc
            try:
                with open(os.path.realpath(os.path.expanduser(path)), "rb") as fh:
                    data = fh.read()
                text = data.decode("utf-8", errors="surrogateescape")
                lossless = text.encode("utf-8", "surrogateescape") == data
            except OSError as exc:
                self.app.error_dialog("Cannot open %s: %s" % (path, exc.strerror))
                return
            dlg = Gtk.Dialog(title=os.path.basename(path),
                             transient_for=self.get_toplevel(), modal=True)
            dlg.add_buttons("_Cancel", Gtk.ResponseType.CANCEL, "_Save", Gtk.ResponseType.OK)
            dlg.set_default_size(700, 500)
            scroll = Gtk.ScrolledWindow()
            view = Gtk.TextView()
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
                    with open(os.path.realpath(os.path.expanduser(path)), "wb") as fh:
                        fh.write(new_text.encode("utf-8", "surrogateescape"))
                    self.app.reload_config()
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
        PANES = ["appearance", "animations", "behavior", "layouts", "keybindings",
                 "workspaces", "windowrules", "monitors", "input", "startup",
                 "theming", "background", "notifications", "files", "system"]

        def __init__(self, app):
            super().__init__(application=app.gtk_app,
                             title=APP_NAME, default_width=980, default_height=680)
            self.app = app
            self.paned = Gtk.Paned()
            self.add(self.paned)

            # No title bar: all window controls live in the sidebar instead.
            # The compositor keeps the window title (taskbar/mangobar) but the
            # app itself draws no header bar.
            # -- sidebar -------------------------------------------------------
            side = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
            side.set_border_width(8)

            self.search = Gtk.SearchEntry(placeholder_text="Search pages… (Ctrl+F)")
            self.search.connect("search-changed", self.on_search)
            side.pack_start(self.search, False, False, 2)

            theme_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
            theme_label = Gtk.Label(label="Theme", xalign=0)
            self.theme_combo = Gtk.ComboBoxText()
            for choice in ("system", "dark", "light"):
                self.theme_combo.append_text(choice)
            self.theme_combo.set_active(0)
            self.theme_combo.connect("changed", app.on_theme_changed)
            theme_box.add(theme_label); theme_box.add(self.theme_combo)
            side.pack_start(theme_box, False, False, 2)

            self.sidebar = Gtk.ListBox()
            self.sidebar.set_selection_mode(Gtk.SelectionMode.SINGLE)
            self.sidebar.connect("row-selected", self.on_row_selected)
            side.pack_start(self.sidebar, True, True, 0)

            btns = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
            save = Gtk.Button(label="_Save", use_underline=True)
            save.set_tooltip_text("Save all changed pages (Ctrl+S)")
            save.connect("clicked", lambda *_: app.action_save())
            btns.pack_start(save, False, False, 0)
            reload_btn = Gtk.Button(label="Reload Mango")
            reload_btn.set_tooltip_text("Run 'mmsg dispatch reload_config' (Ctrl+R)")
            reload_btn.connect("clicked", lambda *_: app.action_reload_mango())
            btns.pack_start(reload_btn, False, False, 0)
            spacer = Gtk.Box()               # pushes the close button to the right
            spacer.set_hexpand(True)
            btns.pack_start(spacer, True, True, 0)
            close_btn = Gtk.Button.new_from_icon_name(
                "window-close-symbolic", Gtk.IconSize.BUTTON)
            close_btn.set_tooltip_text("Quit (Ctrl+Q)")
            close_btn.connect("clicked", lambda *_: app.action_quit())
            btns.pack_end(close_btn, False, False, 0)
            side.pack_start(btns, False, False, 2)

            self.paned.add1(side)
            self.paned.set_position(220)

            # stack
            self.stack = Gtk.Stack()
            self.stack.set_transition_type(Gtk.StackTransitionType.NONE)
            self.paned.add2(self.stack)
            self.page_rows = {}
            for key in self.PANES:
                label = Gtk.Label(label=PAGE_TITLES[key], xalign=0.5)
                label.set_margin_top(6); label.set_margin_bottom(6)
                self.sidebar.add(label)
                row = self.sidebar.get_children()[-1]
                self.page_rows[key] = row
            self.sidebar.select_row(self.sidebar.get_children()[0])

        def on_row_selected(self, _box, row):
            if row is None:
                return
            children = list(self.sidebar.get_children())
            key = self.PANES[children.index(row)]
            child = self.stack.get_child_by_name(key)
            if child is not None:
                self.stack.remove(child)
            page = self.app.make_page(key)
            self.stack.add_named(page, key)
            self.stack.set_visible_child_name(key)

        def on_search(self, entry):
            needle = entry.get_text().lower().strip()
            for key, row in self.page_rows.items():
                row.set_visible(not needle or needle in PAGE_TITLES[key].lower())

        def current_key(self):
            children = list(self.sidebar.get_children())
            row = self.sidebar.get_selected_row()
            return self.PANES[children.index(row)] if row else None

        def goto(self, key):
            if key in self.page_rows:
                self.sidebar.select_row(self.page_rows[key])

    # ------------------------------------------------------------------ app
    class MangoApp(object):
        def __init__(self, args):
            self.args = args
            self.window = None
            self.config = None
            self._rule_files = {}
            self.settings_ini = os.path.expanduser(APP_SETTINGS_FILE)

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
            self.apply_theme(self.read_theme_pref())
            self.window.theme_combo.handler_block_by_func(self.on_theme_changed)
            self.window.theme_combo.set_active(
                ["system", "dark", "light"].index(self.read_theme_pref()))
            self.window.theme_combo.handler_unblock_by_func(self.on_theme_changed)
            if self.args.page:
                self.window.goto(self.args.page)
            if not self.config.exists:
                self.offer_default_config()

        def _install_accelerators(self):
            actions = {
                "save": ("save", ["<Ctrl>s"], self.action_save),
                "reload-mango": ("reload-mango", ["<Ctrl>r"], self.action_reload_mango),
                "quit-app": ("quit-app", ["<Ctrl>q"], self.action_quit),
                "find": ("find", ["<Ctrl>f"], self.action_find),
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
            if key in ("appearance", "animations", "behavior", "layouts", "input"):
                return SchemaPage(self, key)
            return {
                "keybindings": KeybindsPage, "workspaces": WorkspacesPage,
                "windowrules": WindowRulesPage, "monitors": MonitorsPage,
                "startup": StartupPage, "theming": ThemingPage,
                "background": BackgroundPage,
                "notifications": NotificationsPage, "files": ConfigFilesPage,
                "system": SystemInfoPage,
            }[key](self)

        def on_page_changed(self, page):
            pass  # pages self-save or save on Ctrl+S

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

        # -- actions ------------------------------------------------------------
        def action_save(self, *_a):
            errors_before = len([i for i in self.config.issues if i.severity == Issue.ERROR])
            try:
                saved = self.config.save()
                for rf in self._rule_files.values():
                    rf.save()
            except ConfigError as exc:
                self.error_dialog(str(exc))
                return
            for key in self.window.PANES:
                child = self.window.stack.get_child_by_name(key)
                if child is not None:
                    child.clear_dirty()
            self.error_dialog("Saved %d file(s)." % (len(saved) +
                             sum(1 for rf in self._rule_files.values() if rf.dirty)),
                              error=False)
            self.load_config()

        def action_reload_mango(self, *_a):
            try:
                self.config.save()
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
                                "_Save", Gtk.ResponseType.ACCEPT, "_Cancel", Gtk.ResponseType.CANCEL)
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
            try:
                import configparser
                os.makedirs(os.path.dirname(self.settings_ini), exist_ok=True)
                cp = configparser.ConfigParser()
                cp.read(self.settings_ini)
                if not cp.has_section("ui"):
                    cp.add_section("ui")
                cp.set("ui", "theme", theme)
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
        page = args.page.strip().lower()
        if page not in PAGE_ORDER:
            # Also accept the human-readable page titles, case-insensitively
            # (e.g. "Appearance", "Window behavior", "window-behavior").
            page = page.replace("-", " ").replace("_", " ")
            for key in PAGE_ORDER:
                if PAGE_TITLES[key].lower() == page or key == page:
                    break
            else:
                sys.stderr.write("Unknown page %r. Valid pages:\n%s\n" %
                                 (args.page, "\n".join("  " + t + " (key: %s)" % k
                                  for k, t in ((k, PAGE_TITLES[k]) for k in PAGE_ORDER))))
                return 2
        args.page = page
    if args.check:
        return cmd_check(MangoConfig(root=args.config))
    return run_gui(args)


if __name__ == "__main__":
    sys.exit(main())
