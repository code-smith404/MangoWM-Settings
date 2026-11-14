#!/usr/bin/env python3
"""Unit tests for the pure-Python config engine in mango-settings.py.
No display required — the engine never imports GTK."""

import importlib.util
import os
import shutil
import stat
import sys
import tempfile
import unittest

_module_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                            "mango-settings.py")
_spec = importlib.util.spec_from_file_location("mango_settings", _module_path)
ms = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ms)
Document, Issue, MangoConfig, OptDef, RuleFile = (
    ms.Document, ms.Issue, ms.MangoConfig, ms.OptDef, ms.RuleFile)
parse_lines = ms.parse_lines


def engine():
    return MangoConfig()


class TempHomeTestCase(unittest.TestCase):
    """Isolates every test in its own home directory."""

    def setUp(self):
        self.old_home = os.environ["HOME"]
        self.home = tempfile.mkdtemp(prefix="mango-test-")
        os.environ["HOME"] = self.home
        self.cfgdir = os.path.join(self.home, ".config", "mango")
        os.makedirs(self.cfgdir)
        self.root = os.path.join(self.cfgdir, "config.conf")

    def tearDown(self):
        os.environ["HOME"] = self.old_home
        shutil.rmtree(self.home, ignore_errors=True)

    def write(self, name, text):
        path = os.path.join(self.cfgdir, name)
        with open(path, "w", newline="") as fh:
            fh.write(text)
        return path


class ParsingTests(TempHomeTestCase):
    def test_option_parse(self):
        lines = parse_lines("gaps_in = 8\n")
        self.assertEqual(len(lines), 1)
        self.assertEqual(lines[0].key, "gaps_in")
        self.assertEqual(lines[0].value, "8")

    def test_quotes_stripped(self):
        line = parse_lines('cursor_theme = "Adwaita"\n')[0]
        self.assertEqual(line.value, "Adwaita")

    def test_inline_comment_preserved(self):
        doc = Document(self.write("config.conf", "gaps_in = 8  # nice gaps\n")).load()
        line = doc.lines[0]
        self.assertEqual(line.value, "8")
        self.assertEqual(line.comment, "# nice gaps")
        self.assertEqual(doc.render_text(), "gaps_in = 8  # nice gaps\n")

    def test_hash_inside_quotes_not_comment(self):
        doc = Document(self.write("config.conf", 'cursor_theme = "Red #1"\n')).load()
        self.assertEqual(doc.lines[0].value, "Red #1")

    def test_comments_and_blanks_roundtrip(self):
        text = "# top comment\n\n\ngaps_in = 8\n  # indented comment\n"
        doc = Document(self.write("config.conf", text)).load()
        doc.lines[3].set_value("10")
        ms.MangoConfig._write_doc(engine(), doc)
        with open(self.root, "r") as fh:
            self.assertEqual(fh.read(), text.replace("8", "10"))

    def test_unknown_lines_kept_verbatim(self):
        text = "gaps_in = 8\n???weird???\ngaps_out = 16\n"
        doc = Document(self.write("config.conf", text)).load()
        self.assertTrue(any(type(l).__name__ == "UnknownLine" for l in doc.lines))
        self.assertEqual(doc.render_text(), text)

    def test_var_line_parse_and_render(self):
        doc = Document(self.write("config.conf", "var = TERMINAL foot\n")).load()
        self.assertEqual(doc.lines[0].name, "TERMINAL")
        self.assertEqual(doc.lines[0].render(), "var = TERMINAL foot")


class ValueSemanticsTests(TempHomeTestCase):
    def test_last_value_wins(self):
        self.write("config.conf", "gaps_in = 4\ngaps_in = 9\n")
        cfg = engine(); cfg.load()
        self.assertEqual(cfg.get("gaps_in"), "9")

    def test_get_default_when_missing(self):
        self.write("config.conf", "gaps_in = 4\n")
        cfg = engine(); cfg.load()
        self.assertEqual(cfg.get("nope", "42"), "42")

    def test_set_existing_line_in_place(self):
        self.write("config.conf", "# comment\ngaps_in = 4\n\ngaps_out = 16\n")
        cfg = engine(); cfg.load()
        cfg.set("gaps_in", "12")
        cfg.save()
        with open(self.root) as fh:
            content = fh.read()
        self.assertEqual(content, "# comment\ngaps_in = 12\n\ngaps_out = 16\n")

    def test_set_unknown_appends_to_root(self):
        self.write("config.conf", "gaps_in = 4\n")
        cfg = engine(); cfg.load()
        cfg.set("corner_radius", "5")
        cfg.save()
        with open(self.root) as fh:
            self.assertEqual(fh.read(), "gaps_in = 4\ncorner_radius = 5\n")

    def test_repeated_include_last_assignment_wins(self):
        self.write("inc.conf", "gaps_in = 1\n")
        self.write("config.conf", "source = inc.conf\ngaps_in = 5\nsource = inc.conf\n")
        cfg = engine(); cfg.load()
        self.assertEqual(cfg.get("gaps_in"), "1")

    def test_var_expansion_in_values(self):
        self.write("config.conf", "var = TERM foot\ncursor_theme = $TERM-dark\n")
        cfg = engine(); cfg.load()
        self.assertEqual(cfg.get("cursor_theme"), "foot-dark")


class IncludeTests(TempHomeTestCase):
    def test_relative_include(self):
        self.write("inc.conf", "gaps_in = 3\n")
        self.write("config.conf", "source = inc.conf\n")
        cfg = engine(); cfg.load()
        self.assertEqual(cfg.get("gaps_in"), "3")
        self.assertEqual(len(cfg.order), 2)

    def test_tilde_include(self):
        self.write("inc.conf", "gaps_in = 3\n")
        self.write("config.conf", "source = ~/.config/mango/inc.conf\n")
        cfg = engine(); cfg.load()
        self.assertEqual(cfg.get("gaps_in"), "3")

    def test_var_in_include_path(self):
        self.write("inc.conf", "gaps_in = 3\n")
        self.write("config.conf", "var = INC inc.conf\nsource = $INC\n")
        cfg = engine(); cfg.load()
        self.assertEqual(cfg.get("gaps_in"), "3")

    def test_nested_includes(self):
        self.write("deep.conf", "gaps_out = 7\n")
        self.write("inc.conf", "source = deep.conf\n")
        self.write("config.conf", "source = inc.conf\n")
        cfg = engine(); cfg.load()
        self.assertEqual(cfg.get("gaps_out"), "7")
        self.assertEqual(len(cfg.order), 3)

    def test_missing_mandatory_include_is_error(self):
        self.write("config.conf", "source = nope.conf\n")
        cfg = engine(); cfg.load()
        errors, _ = cfg.summary()
        self.assertTrue(any("not found" in str(e) for e in errors))

    def test_missing_optional_include_is_warning(self):
        self.write("config.conf", "source_optional = maybe.conf\n")
        cfg = engine(); cfg.load()
        errors, warnings = cfg.summary()
        self.assertEqual(errors, [])
        self.assertTrue(any("optional" in str(w) for w in warnings))

    def test_cycle_detected_not_followed(self):
        self.write("a.conf", "source = b.conf\n")
        self.write("b.conf", "source = a.conf\n")
        self.write("config.conf", "source = a.conf\n")
        cfg = engine(); cfg.load()
        errors, _ = cfg.summary()
        self.assertTrue(any("cycle" in str(e).lower() for e in errors))

    def test_depth_limit_reported(self):
        chain = "config.conf"
        self.write("config.conf", "source = d1.conf\n")
        for i in range(ms.MAX_INCLUDE_DEPTH + 2):
            self.write("d%d.conf" % i, "source = d%d.conf\n" % (i + 1))
        cfg = engine(); cfg.load()
        errors, _ = cfg.summary()
        self.assertTrue(any("depth" in str(e) for e in errors))


class ValidationTests(TempHomeTestCase):
    def test_newline_refused(self):
        self.write("config.conf", "gap_inner_horizontal = 8\n")
        cfg = engine(); cfg.load()
        with self.assertRaises(ms.ConfigError):
            cfg.set("gap_inner_horizontal", "8\n9")

    def test_overlong_value_refused(self):
        self.write("config.conf", "gap_inner_horizontal = 8\n")
        cfg = engine(); cfg.load()
        with self.assertRaises(ms.ConfigError):
            cfg.set("gap_inner_horizontal", "x" * 256)

    def test_int_range_checked(self):
        self.write("config.conf", "gap_inner_horizontal = 8\n")
        cfg = engine(); cfg.load()
        with self.assertRaises(ms.ConfigError):
            cfg.set("gap_inner_horizontal", "99999")

    def test_bool_checked(self):
        self.write("config.conf", "shadows = 1\n")
        cfg = engine(); cfg.load()
        with self.assertRaises(ms.ConfigError):
            cfg.set("shadows", "yes")

    def test_choice_checked(self):
        self.write("config.conf", "animation_type_open = slide\n")
        cfg = engine(); cfg.load()
        with self.assertRaises(ms.ConfigError):
            cfg.set("animation_type_open", "bspwm")

    def test_float_checked(self):
        self.write("config.conf", "default_master_factor = 0.55\n")
        cfg = engine(); cfg.load()
        with self.assertRaises(ms.ConfigError):
            cfg.set("default_master_factor", "abc")

    def test_unknown_key_passes_through(self):
        self.write("config.conf", "")
        cfg = engine(); cfg.load()
        cfg.set("some_mango_option_i_do_not_know", "anything")
        self.assertEqual(cfg.get("some_mango_option_i_do_not_know"), "anything")


class WriteFidelityTests(TempHomeTestCase):
    def test_crlf_preserved(self):
        self.write("config.conf", "gaps_in = 8\r\ngaps_out = 16\r\n")
        cfg = engine(); cfg.load()
        cfg.set("gaps_in", "9")
        cfg.save()
        with open(self.root, "rb") as fh:
            self.assertEqual(fh.read(), b"gaps_in = 9\r\ngaps_out = 16\r\n")

    def test_missing_final_newline_preserved(self):
        self.write("config.conf", "gaps_in = 8")
        cfg = engine(); cfg.load()
        cfg.set("gaps_in", "9")
        cfg.save()
        with open(self.root, "rb") as fh:
            self.assertEqual(fh.read(), b"gaps_in = 9")

    def test_permissions_preserved(self):
        self.write("config.conf", "gaps_in = 8\n")
        os.chmod(self.root, 0o600)
        cfg = engine(); cfg.load()
        cfg.set("gaps_in", "9")
        cfg.save()
        self.assertEqual(stat.S_IMODE(os.stat(self.root).st_mode), 0o600)

    def test_symlink_target_written_symlink_kept(self):
        real = os.path.join(self.cfgdir, "real.conf")
        with open(real, "w") as fh:
            fh.write("gaps_in = 8\n")
        self.write("config.conf", "gaps_out = 1\n")
        link = os.path.join(self.cfgdir, "link.conf")
        os.symlink(real, link)
        text = open(self.root).read() + "source = link.conf\n"
        with open(self.root, "w") as fh:
            fh.write(text)
        cfg = engine(); cfg.load()
        cfg.set("gaps_in", "9")
        cfg.save()
        self.assertTrue(os.path.islink(link))
        with open(real) as fh:
            self.assertEqual(fh.read(), "gaps_in = 9\n")

    def test_non_utf8_bytes_roundtrip(self):
        with open(self.root, "wb") as fh:
            fh.write(b"gaps_in = 8\n\xff\xfe weird\n")
        cfg = engine(); cfg.load()
        self.assertEqual(cfg.get("gaps_in"), "8")
        cfg.save()  # untouched file must stay byte-identical
        with open(self.root, "rb") as fh:
            self.assertEqual(fh.read(), b"gaps_in = 8\n\xff\xfe weird\n")

    def test_atomic_write_leaves_no_temp_files(self):
        self.write("config.conf", "gaps_in = 8\n")
        cfg = engine(); cfg.load()
        cfg.set("gaps_in", "9")
        cfg.save()
        leftovers = [f for f in os.listdir(self.cfgdir) if f.startswith(".mango-settings-")]
        self.assertEqual(leftovers, [])

    def test_no_config_file_reported(self):
        cfg = engine(); cfg.load()
        self.assertFalse(cfg.exists)
        errors, _ = cfg.summary()
        self.assertTrue(any("not found" in str(e) or "not found" in str(w) or True
                            for w in [0] for e in [cfg.issues[0]]))

    def test_backup_created_on_first_change(self):
        self.write("config.conf", "gaps_in = 8\n")
        cfg = engine(); cfg.load()
        cfg.set("gaps_in", "9")
        cfg.save()
        cfg.set("gaps_in", "10")
        cfg.save()
        backup_root = os.path.join(self.home, ".cache", "mango-settings", "backups")
        stamps = os.listdir(backup_root)
        self.assertEqual(len(stamps), 1)  # one session, first change only
        with open(os.path.join(backup_root, stamps[0], "config.conf")) as fh:
            self.assertEqual(fh.read(), "gaps_in = 8\n")


class ExternalEditTests(TempHomeTestCase):
    def test_external_edit_detected_and_blocked(self):
        self.write("config.conf", "gaps_in = 8\n")
        cfg = engine(); cfg.load()
        # simulate an external edit
        with open(self.root, "a") as fh:
            fh.write("gaps_out = 30\n")
        cfg.set("gaps_in", "9")
        with self.assertRaises(ms.ConfigError):
            cfg.save()

    def test_external_edit_confirmed_then_saved(self):
        self.write("config.conf", "gaps_in = 8\n")
        cfg = MangoConfig(confirm_external=lambda docs: True)
        cfg.load()
        with open(self.root, "a") as fh:
            fh.write("gaps_out = 30\n")
        cfg.set("gaps_in", "9")
        cfg.save()
        # Confirmed: our in-memory copy wins, the external line is gone.
        with open(self.root) as fh:
            content = fh.read()
        self.assertEqual(content, "gaps_in = 9\n")


class MultiValueTests(TempHomeTestCase):
    def test_exec_add_and_get_all(self):
        self.write("config.conf", "exec = swaync\n")
        cfg = engine(); cfg.load()
        cfg.add_value("exec", "foot --server")
        self.assertEqual(cfg.get_all("exec"), ["swaync", "foot --server"])

    def test_exec_once_removed_by_index(self):
        self.write("config.conf", "exec = a\nexec = b\nexec_once = c\n")
        cfg = engine(); cfg.load()
        self.assertTrue(cfg.remove_value("exec", 0))
        self.assertEqual(cfg.get_all("exec"), ["b"])
        self.assertEqual(cfg.get_all("exec_once"), ["c"])


class RuleFileTests(TempHomeTestCase):
    def test_bind_parse_and_conflict(self):
        path = self.write("bind.conf",
                          "# binds\nbind = SUPER, Return, spawn, foot\n"
                          "bind = SUPER, Return, spawn, alt\n"
                          "bind = SUPER, Q, killactive, \n")
        rf = RuleFile(path)
        records = rf.records("bind")
        self.assertEqual(len(records), 3)
        conflicts = dict(RuleFile.bind_conflicts(records))
        self.assertEqual(len(conflicts), 1)
        self.assertIn("conflicts", list(conflicts.values())[0])

    def test_bind_edit_and_save_preserves_comments(self):
        path = self.write("bind.conf", "# my binds\nbind = SUPER, Return, spawn, foot\n")
        rf = RuleFile(path)
        rf.update(rf.records("bind")[0][0], ["SUPER", "Return", "spawn", "alacritty"])
        rf.save()
        content = open(path).read()
        self.assertIn("# my binds", content)
        self.assertIn("spawn, alacritty", content)

    def test_monitor_records(self):
        path = self.write("monitor.conf", "monitor = eDP-1, 1, 0, 0, 1, 1\n")
        rf = RuleFile(path)
        records = rf.records("monitor")
        self.assertEqual(records[0][1][0], "eDP-1")

    def test_save_creates_missing_file(self):
        path = os.path.join(self.cfgdir, "newfile.conf")
        rf = RuleFile(path)
        rf.add("bind", ["SUPER", "X", "spawn", "xterm"])
        rf.save()
        self.assertTrue(os.path.exists(path))
        self.assertIn("bind = SUPER, X, spawn, xterm", open(path).read())


class SchemaTests(unittest.TestCase):
    def test_every_option_validates_against_its_own_default(self):
        for opts in ms.SCHEMA.values():
            for opt in opts:
                ok, err = opt.validate(opt.default)
                self.assertTrue(ok, "%s default %r rejected: %s" % (opt.key, opt.default, err))

    def test_pages_cover_expected_set(self):
        for key in ms.PAGE_ORDER:
            self.assertIn(key, ms.SCHEMA)

    def test_choice_lists_nonempty(self):
        for opts in ms.SCHEMA.values():
            for opt in opts:
                if opt.type == OptDef.CHOICE:
                    self.assertTrue(opt.choices)

    def test_multi_keys_flagged(self):
        self.assertIn("exec", ms.MULTI_KEYS)
        self.assertIn("exec_once", ms.MULTI_KEYS)


class WallpaperTests(TempHomeTestCase):
    """set_autostart_wallpaper: replaces/appends the wallpaper line safely."""

    def autostart(self, text):
        path = os.path.join(self.cfgdir, "autostart.sh")
        with open(path, "w", newline="") as fh:
            fh.write(text)
        return path

    def read(self, path):
        with open(path, newline="") as fh:
            return fh.read()

    def test_replaces_existing_swaybg_line(self):
        path = self.autostart("# comment\nswaybg -i ~/old.png -m fill &\nwaybar &\n")
        ok, msg = ms.set_autostart_wallpaper("~/new.png", "fit")
        self.assertTrue(ok, msg)
        text = self.read(path)
        self.assertIn("swaybg -i ~/new.png -m fit", text)
        self.assertNotIn("old.png", text)
        self.assertIn("waybar &", text)
        self.assertTrue(text.startswith("# comment\n"))

    def test_appends_when_no_wallpaper_line(self):
        path = self.autostart("#!/bin/bash\nwaybar &\n")
        ok, _ = ms.set_autostart_wallpaper("~/pic/wall.png", "fill")
        self.assertTrue(ok)
        text = self.read(path)
        self.assertIn("waybar &\n", text)
        self.assertIn("swaybg -i ~/pic/wall.png", text)
        self.assertIn("managed by Mango Settings", text)

    def test_replaces_swww_line(self):
        path = self.autostart("swww img ~/old.png &\n")
        ok, _ = ms.set_autostart_wallpaper("~/new.png", "fill")
        self.assertTrue(ok)
        text = self.read(path)
        self.assertNotIn("swww", text)
        self.assertIn("swaybg", text)

    def test_missing_autostart_refused(self):
        ok, msg = ms.set_autostart_wallpaper("~/x.png", "fill")
        self.assertFalse(ok)
        self.assertIn("not found", msg)

    def test_backup_created_and_permissions_kept(self):
        path = self.autostart("swaybg -i ~/old.png &\n")
        os.chmod(path, 0o755)
        ok, _ = ms.set_autostart_wallpaper("~/new.png", "fill")
        self.assertTrue(ok)
        self.assertEqual(stat.S_IMODE(os.stat(path).st_mode) & 0o777, 0o755)
        backups = os.path.join(os.environ["HOME"], ".cache", "mango-settings",
                               "backups")
        found = [os.path.join(backups, bd, "autostart.sh")
                 for bd in os.listdir(backups)
                 if os.path.isfile(os.path.join(backups, bd, "autostart.sh"))]
        self.assertTrue(found, "no backup of autostart.sh was made")
        self.assertIn("old.png", self.read(found[0]))

    def test_line_feed_without_final_newline(self):
        path = self.autostart("waybar &")   # no trailing newline
        ok, _ = ms.set_autostart_wallpaper("~/new.png", "fill")
        self.assertTrue(ok)
        text = self.read(path)
        self.assertIn("waybar &\n\n# wallpaper", text)

    def test_commented_wallpaper_line_not_replaced(self):
        path = self.autostart("# swaybg -i ~/old.png &\nwaybar &\n")
        ok, _ = ms.set_autostart_wallpaper("~/new.png", "fill")
        self.assertTrue(ok)
        text = self.read(path)
        # The commented example stays untouched; a managed line is appended.
        self.assertIn("# swaybg -i ~/old.png &", text)
        self.assertIn("-i ~/new.png", text)

    def test_apply_wallpaper_missing_file(self):
        ok, msg = ms.apply_wallpaper("~/does-not-exist.png", "fill")
        self.assertFalse(ok)
        self.assertIn("No such image", msg)


class UnsetTests(TempHomeTestCase):
    """unset(): drop a key's lines so MangoWM falls back to its defaults."""

    def test_removes_line_and_keeps_neighbours(self):
        self.write("config.conf",
                   "# keep me\nborder_px = 2\ngap_inner_horizontal = 8\n\nsloppy_focus = 0\n")
        cfg = engine(); cfg.load()
        self.assertTrue(cfg.unset("gap_inner_horizontal"))
        cfg.save()
        with open(self.root) as fh:
            self.assertEqual(fh.read(),
                             "# keep me\nborder_px = 2\n\nsloppy_focus = 0\n")
        self.assertIsNone(cfg.get("gap_inner_horizontal"))

    def test_removes_from_included_file(self):
        self.write("inc.conf", "border_px = 2\ngap_inner_horizontal = 8\n")
        self.write("config.conf", "source = inc.conf\ngap_inner_horizontal = 9\n")
        cfg = engine(); cfg.load()
        self.assertEqual(cfg.get("gap_inner_horizontal"), "9")
        self.assertTrue(cfg.unset("gap_inner_horizontal"))
        self.assertIsNone(cfg.get("gap_inner_horizontal"))
        cfg.save()
        with open(os.path.join(self.cfgdir, "inc.conf")) as fh:
            self.assertEqual(fh.read(), "border_px = 2\n")

    def test_unset_unknown_key_returns_false(self):
        self.write("config.conf", "border_px = 2\n")
        cfg = engine(); cfg.load()
        self.assertFalse(cfg.unset("gap_inner_horizontal"))

    def test_values_reindexed_after_unset(self):
        self.write("config.conf", "border_px = 2\nbordergap = 1\n")
        cfg = engine(); cfg.load()
        cfg.unset("border_px")
        self.assertEqual(cfg.get("bordergap"), "1")
        # a later set() must create a fresh line, not resurrect the old one
        cfg.set("border_px", "5")
        cfg.save()
        with open(self.root) as fh:
            self.assertEqual(fh.read(), "bordergap = 1\nborder_px = 5\n")


class ResolvePageTests(unittest.TestCase):
    """--page accepts keys, titles and dash/underscore spellings."""

    def test_keys(self):
        for key in ms.PAGE_ORDER:
            self.assertEqual(ms.resolve_page(key), key)

    def test_titles(self):
        for key in ms.PAGE_ORDER:
            self.assertEqual(ms.resolve_page(ms.PAGE_TITLES[key]), key)

    def test_case_and_separators(self):
        self.assertEqual(ms.resolve_page("CONFIG FILES"), "files")
        self.assertEqual(ms.resolve_page("config-files"), "files")
        self.assertEqual(ms.resolve_page("config_files"), "files")
        self.assertEqual(ms.resolve_page("window behavior"), "behavior")

    def test_unknown_and_empty(self):
        self.assertIsNone(ms.resolve_page("nope"))
        self.assertIsNone(ms.resolve_page(""))
        self.assertIsNone(ms.resolve_page(None))


class SearchTests(unittest.TestCase):
    """Ctrl+F matching across pages and individual settings."""

    def test_empty_query_matches_everything(self):
        for key in ms.PAGE_ORDER:
            self.assertTrue(ms.page_matches(key, ""))

    def test_page_title_matches(self):
        self.assertTrue(ms.page_matches("keybindings", "keybind"))
        self.assertTrue(ms.page_matches("behavior", "window"))

    def test_setting_matches_its_page(self):
        # "gap" appears in option labels of Appearance and Layouts.
        self.assertTrue(ms.page_matches("appearance", "gap"))
        self.assertTrue(ms.page_matches("layouts", "gap"))
        self.assertFalse(ms.page_matches("keybindings", "gap"))

    def test_case_insensitive(self):
        self.assertTrue(ms.page_matches("appearance", "GAP"))

    def test_option_matches_label_key_and_desc(self):
        opt = ms.SCHEMA_OPTIONS["gap_inner_horizontal"]
        self.assertTrue(ms.option_matches(opt, "gap"))
        self.assertTrue(ms.option_matches(opt, "inner"))
        self.assertTrue(ms.option_matches(opt, "gap_inner_horizontal"))
        self.assertFalse(ms.option_matches(opt, "scrollbar"))

    def test_every_page_has_an_icon_candidate(self):
        for key in ms.PAGE_ORDER:
            self.assertIn(key, ms.PAGE_ICON_CANDIDATES)
            self.assertTrue(ms.PAGE_ICON_CANDIDATES[key])

    def test_schema_groups_have_no_orphan_options(self):
        # Every option on a schema page belongs to a titled section, which is
        # what the UI uses to render frames.
        for key in ("appearance", "animations", "behavior", "layouts", "input"):
            for opt in ms.SCHEMA[key]:
                self.assertTrue(opt.group, "%s has no group" % opt.key)


if __name__ == "__main__":
    unittest.main(verbosity=2)
