"""Tests for the config engine. Run: python3 -m unittest discover tests"""
import importlib.util
import os
import stat
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location(
    "mango_settings", os.path.join(HERE, "..", "mango-settings.py"))
ms = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ms)


class ConfigTreeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = self.tmp.name
        self.root = os.path.join(self.dir, "config.conf")
        ms.HOME = self.dir
        ms._backed_up.clear()
        os.environ["XDG_CACHE_HOME"] = os.path.join(self.dir, "cache")

    def tearDown(self):
        self.tmp.cleanup()

    def write(self, name, text):
        path = os.path.join(self.dir, name)
        with open(path, "w") as fh:
            fh.write(text)
        return path

    def read(self, name):
        with open(os.path.join(self.dir, name)) as fh:
            return fh.read()

    def test_two_writers_do_not_clobber_each_other(self):
        # Regression: every page used to hold its own stale copy of config.conf.
        self.write("config.conf", "# mine\nborder_radius=6\nanimation_type_open=slide\n")
        a, b = ms.ConfigTree(self.root), ms.ConfigTree(self.root)
        a.set("border_radius", "12")
        b.set("animation_type_open", "zoom")
        self.assertEqual(self.read("config.conf"),
                         "# mine\nborder_radius=12\nanimation_type_open=zoom\n")

    def test_symlink_and_permissions_are_kept(self):
        real = self.write("real.conf", "border_radius=6\n")
        os.chmod(real, 0o640)
        os.symlink(real, self.root)
        ms.ConfigTree(self.root).set("border_radius", "8")
        self.assertTrue(os.path.islink(self.root))
        self.assertEqual(stat.S_IMODE(os.stat(real).st_mode), 0o640)
        self.assertEqual(self.read("real.conf"), "border_radius=8\n")

    def test_inline_comment_and_indent_preserved(self):
        self.write("config.conf", "  repeat_rate=25 # fast\n")
        ms.ConfigTree(self.root).set("repeat_rate", "40")
        self.assertEqual(self.read("config.conf"), "  repeat_rate=40 # fast\n")

    def test_hash_without_space_is_not_a_comment(self):
        self.assertEqual(ms.split_line("cursor_theme=Bibata#1"), ("cursor_theme", "Bibata#1", "", ""))
        self.assertEqual(ms.split_line('a="x # y" # c')[1], "x # y")

    def test_sources_followed_and_last_value_wins(self):
        self.write("theme.conf", "border_radius=10\n")
        self.write("config.conf", "border_radius=4\nsource=./theme.conf\n")
        tree = ms.ConfigTree(self.root)
        self.assertEqual(tree.get("border_radius"), "10")
        tree.set("border_radius", "20")  # edits the file that actually wins
        self.assertEqual(self.read("theme.conf"), "border_radius=20\n")
        self.assertEqual(self.read("config.conf"), "border_radius=4\nsource=./theme.conf\n")

    def test_source_cycles_do_not_hang(self):
        self.write("a.conf", "source=./config.conf\nx=1\n")
        self.write("config.conf", "source=./a.conf\n")
        self.assertEqual(ms.ConfigTree(self.root).get("x"), "1")

    def test_unset_removes_every_assignment(self):
        self.write("b.conf", "shadows=1\n")
        self.write("config.conf", "shadows=0\nsource=./b.conf\n")
        tree = ms.ConfigTree(self.root)
        tree.unset("shadows")
        self.assertIsNone(tree.get("shadows"))
        self.assertEqual(self.read("b.conf"), "\n")

    def test_new_key_appended_and_missing_dir_created(self):
        root = os.path.join(self.dir, "new", "config.conf")
        ms.ConfigTree(root).set("animations", "1")
        with open(root) as fh:
            self.assertEqual(fh.read(), "animations=1\n")

    def test_backup_written(self):
        self.write("config.conf", "a=1\n")
        ms.ConfigTree(self.root).set("a", "2")
        backups = os.path.join(self.dir, "cache", "mango-settings", "backups")
        files = [f for _d, _s, fs in os.walk(backups) for f in fs]
        self.assertEqual(len(files), 1)

    def test_resolve_source_like_mango(self):
        self.assertEqual(ms.resolve_source("./x.conf", self.root), os.path.join(self.dir, "x.conf"))
        self.assertEqual(ms.resolve_source("~/y.conf", self.root), os.path.join(self.dir, "y.conf"))
        self.assertEqual(ms.resolve_source("/etc/z.conf", self.root), "/etc/z.conf")


class HelperTests(unittest.TestCase):
    def test_format_number(self):
        self.assertEqual(ms.format_number(12.0, "int"), "12")
        self.assertEqual(ms.format_number(0.8500000000000001, "float"), "0.85")
        self.assertEqual(ms.format_number(1.0, "float"), "1.0")
        self.assertEqual(ms.format_number(-0.5, "float"), "-0.5")

    def test_parse_bool_matches_atoi(self):
        self.assertTrue(ms.parse_bool("1"))
        self.assertFalse(ms.parse_bool("0"))
        self.assertFalse(ms.parse_bool("true"))  # MangoWM's atoi("true") == 0

    def test_normalize_mods(self):
        self.assertEqual(ms.normalize_mods("shift+SUPER"), ms.normalize_mods("SUPER+SHIFT"))
        self.assertEqual(ms.normalize_mods("none"), "NONE")

    def test_schema_defaults_fit_ranges(self):
        for page, sections in ms.CONFIG_SCHEMA.items():
            for _t, _n, items in sections:
                for key, _label, kind, default, opts in items:
                    if kind in ("int", "float"):
                        self.assertTrue(opts[0] <= default <= opts[1], key)

    def test_detect_root_prefers_conf_then_toml(self):
        with tempfile.TemporaryDirectory() as d:
            ms.MANGO_DIR = d
            self.assertEqual(ms.detect_root_config()[1], "new")
            open(os.path.join(d, "config.toml"), "w").close()
            self.assertEqual(ms.detect_root_config()[1], "toml")
            open(os.path.join(d, "config.conf"), "w").close()
            self.assertEqual(ms.detect_root_config()[1], "conf")


if __name__ == "__main__":
    unittest.main()
