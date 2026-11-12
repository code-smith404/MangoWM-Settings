# Mango Settings 🍊

A modern, polished settings manager for **MangoWM** — a tiling window manager.

GTK3 + Python (PyGObject). GNOME Settings-style: clean sidebar navigation, icons,
search, toggles, sliders, dropdowns, and a dark/light theme switcher.

## Pages

- **Appearance** — gaps, corner radius, opacity, shadows
- **Animations** — open/close/layer animations, durations
- **Window behavior** — smart gaps, sloppy focus, snapping, cursor warp
- **Layouts** — master factor, scroller proportions, dwindle options
- **Keybindings** — view/edit/add/delete `bind.conf` entries
- **Workspaces** — `tag.conf` editor
- **Monitors** — `monitor.conf` rule editor
- **Input devices** — key repeat, trackpad, pointer accel, cursor theme
- **Startup apps** — `autostart.sh` editor
- **Theming** — GTK theme / icon theme via `gsettings`
- **Notifications** — detect swaync, edit + reload
- **System info** — mango version, kernel, session, uptime, memory

All changes save to your `~/.config/mango/` config files automatically
(comments preserved). A "Reload mango" button in the headerbar applies them.

## Install

```bash
git clone https://github.com/YOUR_USERNAME/mango-settings.git
cd mango-settings
python3 mango-settings.py
```

Requires: `python3-gobject`, GTK3. Optional: a desktop-file entry is included —
copy `mango-settings.desktop` to `~/.local/share/applications/`.

## License

MIT
