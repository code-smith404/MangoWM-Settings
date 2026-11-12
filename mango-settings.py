#!/usr/bin/env python3
# Mango Settings — a settings manager for MangoWM
# Copyright: MIT. Requires: python3-gobject, GTK3.

import os
import sys
import shutil
import subprocess
import tempfile

import gi
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk, GLib, Pango, Gdk

MANGO_DIR = os.path.expanduser("~/.config/mango")

CONFIG_SCHEMA = {
    # key: (label, kind, extra)
    # kind: bool, int, float, string, dropdown(extra=list), percent(int 0-100 -> 0.0-1.0)
    "Appearance": [
        ("gap_inner_horizontal", "Inner gap (horizontal)", "int", (0, 200, 1)),
        ("gap_inner_vertical", "Inner gap (vertical)", "int", (0, 200, 1)),
        ("gap_outer_horizontal", "Outer gap (horizontal)", "int", (0, 200, 1)),
        ("gap_outer_vertical", "Outer gap (vertical)", "int", (0, 200, 1)),
        ("border_radius", "Corner radius", "int", (0, 60, 1)),
        ("focused_opacity", "Focused window opacity", "float", (0.1, 1.0)),
        ("unfocused_opacity", "Unfocused window opacity", "float", (0.1, 1.0)),
        ("shadows", "Shadows", "bool"),
        ("shadows_size", "Shadow size", "int", (0, 100, 1)),
        ("shadows_blur", "Shadow blur", "int", (0, 100, 1)),
    ],
    "Animations": [
        ("animations", "Animations", "bool"),
        ("layer_animations", "Layer-shell animations", "bool"),
        ("animation_type_open", "Open animation", "dropdown",
         ["slide", "zoom", "fade", "drop_above", "drop_below", "droptop"]),
        ("animation_type_close", "Close animation", "dropdown",
         ["slide", "zoom", "fade", "drop_above", "drop_below", "droptop"]),
        ("animation_fade_in", "Fade in", "bool"),
        ("animation_fade_out", "Fade out", "bool"),
        ("animation_duration_open", "Open duration (ms)", "int", (0, 3000, 10)),
        ("animation_duration_close", "Close duration (ms)", "int", (0, 3000, 10)),
        ("animation_duration_move", "Move duration (ms)", "int", (0, 3000, 10)),
        ("animation_duration_tag", "Tag-switch duration (ms)", "int", (0, 3000, 10)),
    ],
    "Window behavior": [
        ("smart_gaps", "Smart gaps (hide gaps with single window)", "bool"),
        ("sloppy_focus", "Focus follows mouse", "bool"),
        ("focus_on_activate", "Focus on activate", "bool"),
        ("warp_cursor", "Warp cursor to focused window", "bool"),
        ("focus_cross_monitor", "Focus crosses monitors", "bool"),
        ("drag_tile_to_tile", "Drag tiled windows to tile", "bool"),
        ("enable_floating_snap", "Floating windows snap", "bool"),
        ("snap_distance", "Snap distance", "int", (0, 200, 1)),
        ("new_is_master", "New window becomes master", "bool"),
        ("no_border_when_single", "No border with single window", "bool"),
    ],
    "Layouts": [
        ("default_master_factor", "Master width factor", "float", (0.2, 0.8)),
        ("default_master_count", "Master windows per column", "int", (1, 10, 1)),
        ("scroller_default_proportion", "Scroller default proportion", "float", (0.1, 1.0)),
        ("scroller_focus_center", "Center focused scroller column", "bool"),
        ("dwindle_smart_split", "Dwindle: smart split", "bool"),
        ("dwindle_preserve_split", "Dwindle: preserve split ratio", "bool"),
        ("circle_layout", "Circle layout list", "string"),
    ],
    "Input devices": [
        ("repeat_rate", "Key repeat rate", "int", (1, 100, 1)),
        ("repeat_delay", "Key repeat delay (ms)", "int", (100, 2000, 10)),
        ("numlock_on", "Numlock on at startup", "bool"),
        ("xkb_rules_layout", "Keyboard layouts", "string"),
        ("cursor_theme", "Cursor theme", "string"),
        ("cursor_size", "Cursor size", "int", (8, 128, 1)),
        ("disable_trackpad", "Disable trackpad", "bool"),
        ("tap_to_click", "Tap to click", "bool"),
        ("tap_and_drag", "Tap and drag", "bool"),
        ("drag_lock", "Drag lock", "bool"),
        ("trackpad_natural_scrolling", "Trackpad natural scrolling", "bool"),
        ("trackpad_disable_while_typing", "Disable while typing", "bool"),
        ("mouse_natural_scrolling", "Mouse natural scrolling", "bool"),
        ("mouse_accel_speed", "Pointer acceleration", "float", (-1.0, 1.0)),
    ],
}


def mango_reload():
    """Best-effort live reload of mango."""
    if shutil.which("mangoctl"):
        subprocess.Popen(["mangoctl", "reload_config"],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


class ConfFile:
    """Read/edit a `key=value` config file while preserving comments."""

    def __init__(self, path):
        self.path = path
        self.lines = []
        if os.path.exists(path):
            with open(path, encoding="utf-8") as fh:
                self.lines = fh.read().splitlines()
        else:
            self.lines = []

    def _index(self, key):
        for i, line in enumerate(self.lines):
            s = line.strip()
            if s and not s.startswith("#") and "=" in s:
                k = s.split("=", 1)[0].strip()
                if k == key:
                    return i
        return -1

    def get(self, key, default=None):
        i = self._index(key)
        if i < 0:
            return default
        return self.lines[i].split("=", 1)[1].strip()

    def set(self, key, value):
        v = str(value)
        i = self._index(key)
        if i >= 0:
            self.lines[i] = "%s=%s" % (key, v)
        else:
            self.lines.append("%s=%s" % (key, v))

    def save(self):
        tmp = tempfile.NamedTemporaryFile(
            "w", dir=os.path.dirname(self.path) or ".", delete=False,
            prefix=".tmp-", encoding="utf-8")
        tmp.write("\n".join(self.lines) + "\n")
        tmp.flush()
        os.fsync(tmp.fileno())
        tmp.close()
        os.replace(tmp.name, self.path)


def fmt_bool(v):
    return "1" if str(v).strip() in ("1", "true", "True", "on") else "0"


class SettingsPage(Gtk.Box):
    """Grid of toggles/sliders/spins bound to ~/.config/mango/config.conf."""

    def __init__(self, app, schema):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        self.app = app
        self.schema = schema
        self.widgets = {}
        self.build()

    def build(self):
        self.conf = ConfFile(os.path.join(MANGO_DIR, "config.conf"))
        frame = Gtk.Frame(margin=18, hexpand=True)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10,
                      margin=14)
        frame.add(box)
        self.pack_start(frame, False, False, 0)

        for key, label, kind, *extra in self.schema:
            row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=14)
            lab = Gtk.Label(label=label, xalign=0, hexpand=True)
            row.pack_start(lab, True, True, 0)

            if kind == "bool":
                w = Gtk.Switch(active=str(self.conf.get(key, "0")).strip()
                               in ("1", "true", "True", "on"))
                w.connect("notify::active", self.on_bool, key)
            elif kind == "int":
                lo, hi, step = extra[0]
                val = self.conf.get(key, str(lo))
                try:
                    val = int(float(val))
                except ValueError:
                    val = lo
                w = Gtk.SpinButton.new_with_range(lo, hi, step)
                w.set_value(val)
                w.set_digits(0)
                w.connect("value-changed", self.on_spin, key)
            elif kind == "float":
                lo, hi = extra[0]
                val = self.conf.get(key, str(lo))
                try:
                    val = float(val)
                except ValueError:
                    val = lo
                w = Gtk.SpinButton.new_with_range(lo, hi, 0.05)
                w.set_value(val)
                w.set_digits(2)
                w.connect("value-changed", self.on_spin, key)
            elif kind == "percent":
                pass
            elif kind == "dropdown":
                w = Gtk.ComboBoxText()
                for opt in extra[0]:
                    w.append_text(opt)
                cur = (self.conf.get(key, "") or "").strip()
                if cur in extra[0]:
                    w.set_active(extra[0].index(cur))
                w.connect("changed", self.on_dropdown, key)
            else:  # string
                w = Gtk.Entry(width_chars=22)
                w.set_text(str(self.conf.get(key, "") or ""))
                w.connect("changed", self.on_entry, key)
            self.widgets[key] = w
            row.pack_end(w, False, False, 0)
            box.pack_start(row, False, False, 0)

        self.pack_start(Gtk.Label(
            label="Changes are saved to ~/.config/mango/config.conf automatically.",
            xalign=0, margin_start=18), False, False, 0)

    def on_bool(self, sw, _pspec, key):
        self.conf.set(key, fmt_bool(sw.get_active()))
        self.save()

    def on_spin(self, btn, key):
        self.conf.set(key, btn.get_value())
        self.save()

    def on_dropdown(self, combo, key):
        self.conf.set(key, combo.get_active_text() or "")
        self.save()

    def on_entry(self, entry, key):
        self.conf.set(key, entry.get_text())
        self.save()

    def save(self):
        self.conf.save()
        self.app.toast("Saved — remember to reload mango (SUPER+R) if needed.")


class RawEditorPage(Gtk.Box):
    """Plain-text editor page for a config file."""

    def __init__(self, app, path, title_hint):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=10,
                         margin=18)
        self.app = app
        self.path = path
        self.pack_start(Gtk.Label(label=title_hint, xalign=0), False, False, 0)
        sc = Gtk.ScrolledWindow(shadow_type=Gtk.ShadowType.IN, vexpand=True,
                                hexpand=True)
        self.buf = Gtk.TextBuffer()
        if os.path.exists(path):
            with open(path, encoding="utf-8", errors="replace") as fh:
                self.buf.set_text(fh.read())
        tv = Gtk.TextView(buffer=self.buf, monospace=True, wrap_mode=Gtk.WrapMode.NONE)
        sc.add(tv)
        self.pack_start(sc, True, True, 0)
        btn = Gtk.Button(label="Save")
        btn.connect("clicked", self.on_save)
        self.pack_start(btn, False, False, 0)

    def on_save(self, _btn):
        start, end = self.buf.get_bounds()
        with open(self.path, "w", encoding="utf-8") as fh:
            fh.write(self.buf.get_text(start, end, False))
        self.app.toast("Saved %s" % self.path)


class KeybindsPage(Gtk.Box):
    def __init__(self, app):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=10,
                         margin=18)
        self.app = app
        self.path = os.path.join(MANGO_DIR, "bind.conf")
        self.pack_start(Gtk.Label(label="Keybindings (~/.config/mango/bind.conf)",
                                  xalign=0), False, False, 0)

        sc = Gtk.ScrolledWindow(shadow_type=Gtk.ShadowType.IN, vexpand=True)
        self.store = Gtk.ListStore(str, str, str)  # mods, key, command
        self.tree = Gtk.TreeView(model=self.store)
        for i, title in enumerate(("Modifiers", "Keys", "Action")):
            cell = Gtk.CellRendererText(ellipsize=Pango.EllipsizeMode.END)
            col = Gtk.TreeViewColumn(title, cell, text=i)
            col.set_resizable(True)
            self.tree.append_column(col)
        sc.add(self.tree)
        self.pack_start(sc, True, True, 0)

        btns = Gtk.Box(spacing=8)
        for label, cb in (("Add…", self.on_add), ("Delete", self.on_del),
                          ("Save", self.on_save)):
            b = Gtk.Button(label=label)
            b.connect("clicked", cb)
            btns.pack_start(b, False, False, 0)
        self.pack_start(btns, False, False, 0)
        self.load()

    def load(self):
        self.store.clear()
        if not os.path.exists(self.path):
            return
        with open(self.path, encoding="utf-8") as fh:
            for line in fh:
                s = line.strip()
                if not s.startswith("bind=") or "," not in s:
                    continue
                parts = s[len("bind="):].split(",", 2)
                if len(parts) == 3:
                    self.store.append([p.strip() for p in parts])

    def _write(self):
        existing = []
        if os.path.exists(self.path):
            with open(self.path, encoding="utf-8") as fh:
                existing = fh.read().splitlines()
        kept = [l for l in existing if not l.strip().startswith("bind=")]
        rows = ["bind=%s,%s,%s" % tuple(it) for it in
                (self.store[r] for r in range(len(self.store)))]
        # keep commented-out binds intact, keep original ordering
        out = []
        inserted = False
        for l in existing:
            if not inserted and (l.strip().startswith("bind=")):
                out.extend(rows)
                inserted = True
            if not l.strip().startswith("bind="):
                out.append(l)
        if not inserted:
            out.extend(rows)
        with open(self.path, "w", encoding="utf-8") as fh:
            fh.write("\n".join(out) + "\n")
        self.app.toast("Keybindings saved — reload mango to apply.")

    def on_save(self, _b):
        self._write()

    def on_del(self, _b):
        model, it = self.tree.get_selection().get_selected()
        if it:
            model.remove(it)
            self._write()

    def on_add(self, _b):
        dlg = Gtk.Dialog(title="Add keybinding", transient_for=self.get_toplevel(),
                         modal=True)
        dlg.add_buttons("Cancel", Gtk.ResponseType.CANCEL,
                        "Add", Gtk.ResponseType.OK)
        grid = Gtk.Grid(column_spacing=8, row_spacing=8, margin=10)
        e_mods = Gtk.Entry(width_chars=18)
        e_mods.set_text("SUPER+SHIFT")
        e_key = Gtk.Entry(width_chars=18)
        e_cmd = Gtk.Entry(width_chars=30)
        e_cmd.set_text("spawn,foot")
        for r, (name, w) in enumerate((("Modifiers", e_mods),
                                       ("Keys", e_key),
                                       ("Action", e_cmd))):
            grid.attach(Gtk.Label(label=name, xalign=0), 0, r, 1, 1)
            grid.attach(w, 1, r, 1, 1)
        box = dlg.get_content_area()
        box.add(grid)
        dlg.show_all()
        if dlg.run() == Gtk.ResponseType.OK:
            self.store.append([e_mods.get_text(), e_key.get_text(), e_cmd.get_text()])
            self._write()
        dlg.destroy()


class WorkspacesPage(Gtk.Box):
    def __init__(self, app):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=10,
                         margin=18)
        self.app = app
        self.path = os.path.join(MANGO_DIR, "tag.conf")
        self.pack_start(Gtk.Label(label="Workspaces / tags (tag.conf)",
                                  xalign=0), False, False, 0)
        sc = Gtk.ScrolledWindow(shadow_type=Gtk.ShadowType.IN, vexpand=True)
        self.buf = Gtk.TextBuffer()
        if os.path.exists(self.path):
            with open(self.path, encoding="utf-8") as fh:
                self.buf.set_text(fh.read())
        sc.add(Gtk.TextView(buffer=self.buf, monospace=True))
        self.pack_start(sc, True, True, 0)
        btn = Gtk.Button(label="Save")
        btn.connect("clicked", self.on_save)
        self.pack_start(btn, False, False, 0)

    def on_save(self, _b):
        start, end = self.buf.get_bounds()
        with open(self.path, "w", encoding="utf-8") as fh:
            fh.write(self.buf.get_text(start, end, False))
        self.app.toast("Tags saved — reload mango to apply.")


class StartupPage(RawEditorPage):
    def __init__(self, app):
        super().__init__(app, os.path.join(MANGO_DIR, "autostart.sh"),
                         "Startup apps — this script is executed by mango at login (autostart.sh).")


class MonitorsPage(RawEditorPage):
    def __init__(self, app):
        super().__init__(app, os.path.join(MANGO_DIR, "monitor.conf"),
                         "Monitors — one monitor_rule line per display: name, width, height, refresh, x, y, scale, vrr, rr.")


class ThemingPage(Gtk.Box):
    def __init__(self, app):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=12,
                         margin=18)
        self.app = app
        self.pack_start(Gtk.Label(label="GTK theming (applies to GTK apps)",
                                  xalign=0), False, False, 0)
        grid = Gtk.Grid(column_spacing=10, row_spacing=10)

        self.theme = Gtk.ComboBoxText()
        for t in sorted(_safe_listdir("/usr/share/themes")):
            self.theme.append_text(t)
        self.icons = Gtk.ComboBoxText()
        for t in sorted(_safe_listdir("/usr/share/icons")):
            self.icons.append_text(t)
        cur_theme = _gsettings_get("gtk-theme")
        cur_icons = _gsettings_get("icon-theme")
        if cur_theme:
            _combo_set(self.theme, cur_theme)
        if cur_icons:
            _combo_set(self.icons, cur_icons)
        self.theme.connect("changed", self.apply)
        self.icons.connect("changed", self.apply)

        grid.attach(Gtk.Label(label="Theme", xalign=0), 0, 0, 1, 1)
        grid.attach(self.theme, 1, 0, 1, 1)
        grid.attach(Gtk.Label(label="Icons", xalign=0), 0, 1, 1, 1)
        grid.attach(self.icons, 1, 1, 1, 1)
        self.pack_start(grid, False, False, 0)

        self.pack_start(Gtk.Label(
            label="Rofi themes live in ~/.config/rofi and ~/.config/mango/rofi.\n"
                  "Widget theme / wallpapers can be changed with swaybg in autostart.sh.",
            xalign=0), False, False, 0)

    def apply(self, _combo):
        theme = self.theme.get_active_text()
        icons = self.icons.get_active_text()
        if theme:
            _gsettings_set("gtk-theme", theme)
        if icons:
            _gsettings_set("icon-theme", icons)
        self.app.toast("Theming applied.")


class NotificationsPage(Gtk.Box):
    def __init__(self, app):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=10,
                         margin=18)
        path = os.path.expanduser("~/.config/swaync/config.json")
        self.pack_start(Gtk.Label(
            label="Notifications (swaync)", xalign=0), False, False, 0)
        if os.path.exists(path):
            self.pack_start(Gtk.Label(label="Config: %s" % path, xalign=0),
                            False, False, 0)
            btn = Gtk.Button(label="Edit config")
            btn.connect("clicked", lambda *_: app.open_file(path))
            self.pack_start(btn, False, False, 0)
            btn2 = Gtk.Button(label="Reload swaync")
            btn2.connect("clicked", lambda *_: subprocess.Popen(
                ["swaync-client", "-rs"], stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL))
            self.pack_start(btn2, False, False, 0)
        else:
            self.pack_start(Gtk.Label(
                label="swaync is not configured.\nInstall swaync and add it to autostart.sh.",
                xalign=0), False, False, 0)


class SystemInfoPage(Gtk.Box):
    def __init__(self, app):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=10,
                         margin=18)
        self.pack_start(Gtk.Label(label="System", xalign=0), False, False, 0)

        welcome = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        welcome.get_style_context().add_class("welcome")
        wlab = Gtk.Label(xalign=0, wrap=True)
        wlab.set_markup("<big><b>🍊 Welcome to Mango Settings</b></big>\n"
                        "<small>Manage your MangoWM tiling setup — appearance, "
                        "keybindings, workspaces and more. Changes are saved to "
                        "your config files automatically.</small>")
        welcome.pack_start(wlab, False, False, 0)
        self.pack_start(welcome, False, False, 0)

        def info_row(name, cmd):
            try:
                val = subprocess.check_output(cmd, shell=True,
                                              text=True).strip()
            except Exception:
                val = "—"
            lab = Gtk.Label(xalign=0)
            lab.set_markup("<b>%s</b>\t%s" % (GLib.markup_escape_text(name),
                                              GLib.markup_escape_text(val)))
            lab.set_selectable(True)
            return lab

        for name, cmd in (
                ("MangoWM", "mango -v 2>/dev/null || echo unknown"),
                ("Kernel", "uname -r"),
                ("Session", "echo $XDG_SESSION_TYPE ($WAYLAND_DISPLAY)"),
                ("Uptime", "uptime -p 2>/dev/null || echo —"),
                ("Memory", "free -h | awk '/Mem:/{print $3 \"/\" $2}'"),
        ):
            self.pack_start(info_row(name, cmd), False, False, 4)


def _safe_listdir(path):
    try:
        return [d for d in os.listdir(path)
                if os.path.isdir(os.path.join(path, d)) and not d.startswith(".")]
    except OSError:
        return []


def _gsettings_get(key):
    try:
        return subprocess.check_output(
            ["gsettings", "get", "org.gnome.desktop.interface", key],
            text=True).strip().strip("'\"")
    except Exception:
        return ""


def _gsettings_set(key, value):
    subprocess.Popen(["gsettings", "set", "org.gnome.desktop.interface",
                      key, value], stdout=subprocess.DEVNULL,
                     stderr=subprocess.DEVNULL)


def _combo_set(combo, value):
    for i in range(len(combo.get_model())):
        if combo.get_model()[i][0] == value:
            combo.set_active(i)
            return


APP_CSS = b"""
/* Mango Settings stylesheet */
\n/* brand */
headerbar {
  background: alpha(@theme_selected_bg_color, 0.9);
  color: white;
}
headerbar button {
  background: alpha(white, 0.18);
  color: white;
  border-radius: 6px;
  padding: 6px 12px;
}
headerbar button:hover {
  background: alpha(white, 0.30);
}

/* sidebar */
.sidebar {
  background: alpha(@theme_unfocused_bg_color, 0.6);
  border-right: 1px solid alpha(@theme_fg_color, 0.12);
}
.sidebar listbox row {
  border-radius: 8px;
  margin: 2px 8px;
  padding: 4px 6px;
}
.sidebar listbox row:hover {
  background: alpha(@theme_fg_color, 0.06);
}
.sidebar listbox row:selected {
  background: alpha(@theme_selected_bg_color, 0.85);
  color: @theme_selected_fg_color;
}
.sidebar listbox row:selected image {
  color: @theme_selected_fg_color;
}

/* pages */
stack > box, stack > scrolledwindow {
  padding: 14px;
}
stack frame > border {
  border-radius: 10px;
  border-color: alpha(@theme_fg_color, 0.10);
}
.dim-label {
  opacity: 0.55;
}

/* title cards */
.page-title {
  font-weight: 700;
  font-size: 15px;
  padding: 4px 0 10px 0;
}

/* welcome card */
.welcome {
  background: alpha(@theme_selected_bg_color, 0.08);
  border-radius: 12px;
  padding: 16px;
  margin: 10px;
}

/* toast */
.toast {
  background: alpha(@theme_fg_color, 0.85);
  color: @theme_bg_color;
  border-radius: 999px;
  padding: 8px 18px;
}
"""


class App(Gtk.Window):
    PAGES = [
        ("Appearance", "document-open-recent", None),
        ("Animations", "media-playback-start", None),
        ("Window behavior", "preferences-desktop", None),
        ("Layouts", "view-grid", None),
        ("Keybindings", "input-keyboard", "keybinds"),
        ("Workspaces", "user-desktop", "workspaces"),
        ("Monitors", "video-display", "monitors"),
        ("Input devices", "input-tablet", None),
        ("Startup apps", "system-run", "startup"),
        ("Theming", "applications-graphics", "theming"),
        ("Notifications", "preferences-system-notifications", "notifications"),
        ("System info", "dialog-information", "sysinfo"),
    ]

    def __init__(self):
        super().__init__(title="Mango Settings")
        self.set_default_size(960, 640)
        self.search, self.stack, self.btn_map = None, None, {}
        provider = Gtk.CssProvider()
        provider.load_from_data(APP_CSS)
        Gtk.StyleContext.add_provider_for_screen(
            Gdk.Screen.get_default(), provider,
            Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        self._build_header()
        self._build_body()
        self.connect("destroy", Gtk.main_quit)
        self.show_all()

    def _build_header(self):
        hb = Gtk.HeaderBar(show_close_button=True)
        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        icon = Gtk.Image.new_from_icon_name("weather-clear-night", Gtk.IconSize.BUTTON)
        title = Gtk.Label()
        title.set_markup("<b>Mango Settings</b> <small>— MangoWM</small>")
        box.pack_start(icon, False, False, 0)
        box.pack_start(title, False, False, 0)
        hb.set_custom_title(box)
        self.set_titlebar(hb)
        reload_btn = Gtk.Button(label=" ⟳ Reload mango")
        reload_btn.get_style_context().add_class("suggested-action")
        reload_btn.connect("clicked", lambda *_: (
            mango_reload(), self.toast("Reload signal sent to mango")))
        hb.pack_end(reload_btn)

        # theme switcher: follow system / light / dark
        self.theme_btn = Gtk.MenuButton(label="◐ Theme")
        pop = Gtk.Popover()
        pbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4, margin=8)
        for label, val in (("Follow system", "system"), ("Light", "light"),
                           ("Dark", "dark")):
            b = Gtk.ModelButton(text=label)
            b.connect("clicked", self.on_theme, val)
            pbox.pack_start(b, False, False, 0)
        pbox.show_all()
        pop.add(pbox)
        self.theme_btn.set_popover(pop)
        hb.pack_end(self.theme_btn)
        self.apply_theme("system")

    def apply_theme(self, mode):
        self.theme_mode = mode
        settings = Gtk.Settings.get_default()
        if mode == "dark":
            settings.set_property("gtk-application-prefer-dark-theme", True)
            settings.set_property("gtk-theme-name", "Adwaita-dark")
        elif mode == "light":
            settings.set_property("gtk-application-prefer-dark-theme", False)
            settings.set_property("gtk-theme-name", "Adwaita")
        else:  # follow system
            prefer_dark = False
            try:
                out = subprocess.check_output(
                    ["gsettings", "get", "org.gnome.desktop.interface",
                     "gtk-theme"], text=True).strip().strip("'")
                prefer_dark = "dark" in out.lower()
            except Exception:
                pass
            settings.set_property("gtk-application-prefer-dark-theme", prefer_dark)
            settings.set_property("gtk-theme-name", out if out else "Adwaita")

    def on_theme(self, _btn, mode):
        self.apply_theme(mode)
        self.toast("Theme: %s" % mode)
        if mode == "system":
            # keep following system changes while open
            if getattr(self, "_gsettings_watch", None):
                GLib.source_remove(self._gsettings_watch)
            try:
                self._gsettings_watch = GLib.timeout_add_seconds(
                    3, lambda: (self.apply_theme("system"), True)[1])
            except Exception:
                pass

    def _build_body(self):
        paned = Gtk.Paned(orientation=Gtk.Orientation.HORIZONTAL, position=220)

        sidebar = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6,
                          margin=10)
        sidebar.get_style_context().add_class("sidebar")
        self.search = Gtk.SearchEntry()
        self.search.connect("search-changed", self.on_search)
        sidebar.pack_start(self.search, False, False, 0)

        # scrollable listbox-based sidebar (icon + label rows)
        self.btn_map = {}
        self.row_map = {}
        self.stack = Gtk.Stack()
        self.stack.set_transition_type(Gtk.StackTransitionType.SLIDE_LEFT_RIGHT)
        lb = Gtk.ListBox(selection_mode=Gtk.SelectionMode.SINGLE)
        lb.set_activate_on_single_click(True)
        lb.connect("row-selected", self.on_row_selected)
        for name, icon, special in self.PAGES:
            page = self._make_page(name, special)
            self.stack.add_titled(page, name, name)
            row = Gtk.ListBoxRow()
            hbox = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8,
                           margin=6)
            ic = Gtk.Image.new_from_icon_name(icon, Gtk.IconSize.BUTTON)
            lab = Gtk.Label(label=name, xalign=0, hexpand=True)
            hbox.pack_start(ic, False, False, 0)
            hbox.pack_start(lab, True, True, 0)
            row.add(hbox)
            lb.add(row)
            self.btn_map[name] = row
            self.row_map[name] = special
        lb_sc = Gtk.ScrolledWindow(vexpand=True)
        lb_sc.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        lb_sc.add(lb)
        sidebar.pack_start(lb_sc, True, True, 0)

        hint = Gtk.Label(label="Changes apply per config file; reload mango to apply.",
                         xalign=0, margin=6, wrap=True)
        hint.get_style_context().add_class("dim-label")
        sidebar.pack_start(hint, False, False, 0)

        paned.add1(sidebar)
        paned.add2(self.stack)
        paned.set_position(220)
        self.add(paned)

    def on_row_selected(self, _lb, row):
        if row is None:
            return
        name = row.get_child().get_children()[1].get_text()
        self.stack.set_visible_child_name(name)

    def _make_page(self, name, special):
        if special is None and name in CONFIG_SCHEMA:
            return SettingsPage(self, CONFIG_SCHEMA[name])
        if special == "keybinds":
            return KeybindsPage(self)
        if special == "workspaces":
            return WorkspacesPage(self)
        if special == "monitors":
            return MonitorsPage(self)
        if special == "startup":
            return StartupPage(self)
        if special == "theming":
            return ThemingPage(self)
        if special == "notifications":
            return NotificationsPage(self)
        if special == "sysinfo":
            return SystemInfoPage(self)
        return Gtk.Box()

    def on_search(self, entry):
        q = entry.get_text().lower()
        for name, btn in self.btn_map.items():
            btn.set_visible(q in name.lower())

    def toast(self, msg):
        if getattr(self, "_toast", None):
            self._toast.destroy()
        self._toast = Gtk.Label(label=msg, halign=Gtk.Align.CENTER)
        overlay = self.get_child()
        ov = Gtk.Overlay()
        # simplest: show as transient window title bar message via header subtitle
        self.get_titlebar().set_subtitle(msg)
        GLib.timeout_add(3500, lambda: (
            self.get_titlebar().set_subtitle("Tiling window manager"), False))


def main():
    GLib.set_application_name("Mango Settings")
    GLib.set_prgname("mango-settings")
    App()
    Gtk.main()


if __name__ == "__main__":
    main()
