"""Tests for the config engine. Run: python3 -m unittest discover tests"""
import importlib.util
import io
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


class IncludeTests(unittest.TestCase):
    """Include handling, checked against MangoWM's src/config/load.c."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = self.tmp.name
        self.root = os.path.join(self.dir, "config.conf")
        self._home = ms.HOME
        ms.HOME = self.dir
        ms._backed_up.clear()
        os.environ["XDG_CACHE_HOME"] = os.path.join(self.dir, "cache")

    def tearDown(self):
        ms.HOME = self._home
        self.tmp.cleanup()

    def write(self, name, text, mode="w"):
        path = os.path.join(self.dir, name)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, mode) as fh:
            fh.write(text)
        return path

    def read(self, name, mode="r"):
        with open(os.path.join(self.dir, name), mode) as fh:
            return fh.read()

    def tree(self):
        return ms.ConfigTree(self.root)

    def kinds(self, tree):
        return [p.kind for p in tree.problems]

    # -- parse order -------------------------------------------------------
    def test_same_file_included_twice_is_parsed_twice(self):
        # Regression: a global "seen" set skipped the second include, so the
        # value assigned between the two includes looked like the winner.
        self.write("colors.conf", "border_radius=10\n")
        self.write("config.conf", "source=./colors.conf\nborder_radius=4\nsource=./colors.conf\n")
        tree = self.tree()
        self.assertEqual(tree.get("border_radius"), "10")
        entries, files = tree.scan()
        self.assertEqual([e.value for e in entries if e.key == "border_radius"], ["10", "4", "10"])
        self.assertEqual(files, [self.root, os.path.join(self.dir, "colors.conf")])
        self.assertEqual(tree.problems, [])

    def test_assignment_after_last_include_wins(self):
        self.write("colors.conf", "border_radius=10\n")
        self.write("config.conf", "source=./colors.conf\nborder_radius=4\n"
                                  "source=./colors.conf\nborder_radius=7\n")
        tree = self.tree()
        self.assertEqual(tree.get("border_radius"), "7")
        tree.set("border_radius", "9")
        self.assertEqual(self.read("config.conf").splitlines()[-1], "border_radius=9")
        self.assertEqual(self.read("colors.conf"), "border_radius=10\n")

    def test_set_in_twice_included_file_writes_one_line(self):
        self.write("colors.conf", "border_radius=10\n")
        self.write("config.conf", "source=./colors.conf\nsource=./colors.conf\n")
        self.tree().set("border_radius", "12")
        self.assertEqual(self.read("colors.conf"), "border_radius=12\n")

    def test_unset_in_twice_included_file_deletes_only_that_line(self):
        # Regression: the duplicated (file, line) pair was deleted twice,
        # removing an unrelated line as well.
        self.write("colors.conf", "border_radius=10\nshadows=1\n")
        self.write("config.conf", "source=./colors.conf\nsource=./colors.conf\n")
        self.tree().unset("border_radius")
        self.assertEqual(self.read("colors.conf"), "shadows=1\n")

    def test_deeply_nested_includes(self):
        for i in range(20):
            self.write("n%d.conf" % i, "source=./n%d.conf\nlevel=%d\n" % (i + 1, i))
        self.write("n20.conf", "deepest=1\n")
        self.write("config.conf", "source=./n0.conf\n")
        tree = self.tree()
        self.assertEqual(tree.get("deepest"), "1")
        self.assertEqual(tree.get("level"), "0")  # n0's line comes after its include
        self.assertEqual(tree.problems, [])

    def test_depth_limit(self):
        n = ms.MAX_INCLUDE_DEPTH + 5
        for i in range(n):
            self.write("d%d.conf" % i, "source=./d%d.conf\n" % (i + 1))
        self.write("d%d.conf" % n, "too_deep=1\n")
        self.write("config.conf", "source=./d0.conf\n")
        tree = self.tree()
        self.assertIsNone(tree.get("too_deep"))
        self.assertEqual(self.kinds(tree), ["depth"])

    # -- cycles ------------------------------------------------------------
    def test_direct_cycle_detected(self):
        self.write("config.conf", "a=1\nsource=./config.conf\nb=2\n")
        tree = self.tree()
        self.assertEqual(tree.values(), {"a": "1", "b": "2"})
        self.assertEqual(self.kinds(tree), ["cycle"])
        self.assertEqual(tree.problems[0].origin, (self.root, 1))

    def test_indirect_cycle_detected(self):
        self.write("a.conf", "source=./b.conf\nx=1\n")
        self.write("b.conf", "source=./a.conf\ny=2\n")
        self.write("config.conf", "source=./a.conf\n")
        tree = self.tree()
        self.assertEqual(tree.values(), {"x": "1", "y": "2"})
        self.assertEqual(self.kinds(tree), ["cycle"])

    def test_cycle_through_symlink_detected(self):
        self.write("a.conf", "source=./link.conf\nx=1\n")
        os.symlink(os.path.join(self.dir, "a.conf"), os.path.join(self.dir, "link.conf"))
        self.write("config.conf", "source=./a.conf\n")
        tree = self.tree()
        self.assertEqual(tree.get("x"), "1")
        self.assertEqual(self.kinds(tree), ["cycle"])

    def test_check_command_reports_cycle(self):
        self.write("config.conf", "source=./config.conf\n")
        out = io.StringIO()
        self.assertEqual(ms.check_config(self.root, out), 1)
        self.assertIn("includes itself", out.getvalue())
        self.write("config.conf", "a=1\n")
        self.assertEqual(ms.check_config(self.root, io.StringIO()), 0)

    # -- missing files -----------------------------------------------------
    def test_missing_optional_include_is_silent(self):
        self.write("config.conf", "source_optional=./nope.conf\na=1\n")
        tree = self.tree()
        self.assertEqual(tree.get("a"), "1")
        self.assertEqual(tree.problems, [])

    def test_missing_mandatory_include_reported_and_parsing_continues(self):
        self.write("config.conf", "source=./nope.conf\na=1\n")
        tree = self.tree()
        self.assertEqual(tree.get("a"), "1")  # MangoWM keeps parsing the parent
        self.assertEqual(self.kinds(tree), ["missing"])
        self.assertEqual(tree.problems[0].path, os.path.join(self.dir, "nope.conf"))

    def test_source_keys_match_by_prefix_like_strncmp(self):
        self.write("x.conf", "x=1\n")
        self.write("config.conf", "source_optional_extra=./gone.conf\nsourcefile=./x.conf\n")
        tree = self.tree()
        self.assertEqual(tree.get("x"), "1")
        self.assertEqual(tree.problems, [])  # the first one is optional
        self.assertNotIn("sourcefile", tree.values())

    def test_unreadable_include_reported(self):
        if os.geteuid() == 0:
            self.skipTest("root can read everything")
        path = self.write("secret.conf", "a=1\n")
        os.chmod(path, 0)
        self.write("config.conf", "source=./secret.conf\n")
        tree = self.tree()
        self.assertEqual(tree.values(), {})
        self.assertEqual(self.kinds(tree), ["unreadable"])

    def test_toml_include_not_parsed_as_conf(self):
        self.write("extra.toml", "border_radius = 9\n")
        self.write("noext", "# comment\n[global]\nborder_radius = 9\n")
        self.write("config.conf", "source=./extra.toml\nsource=./noext\n")
        tree = self.tree()
        self.assertIsNone(tree.get("border_radius"))
        self.assertEqual(self.kinds(tree), ["toml", "toml"])

    # -- paths -------------------------------------------------------------
    def test_dot_slash_is_relative_to_root_dir_not_including_file(self):
        # MangoWM resolves ./x against the root config's folder, even from a
        # file in a subdirectory.
        self.write("sub/inc.conf", "source=./b.conf\n")
        self.write("b.conf", "where=root-dir\n")
        self.write("sub/b.conf", "where=sub-dir\n")
        self.write("config.conf", "source=./sub/inc.conf\n")
        self.assertEqual(self.tree().get("where"), "root-dir")

    def test_absolute_and_home_paths(self):
        other = tempfile.TemporaryDirectory()
        self.addCleanup(other.cleanup)
        abs_path = os.path.join(other.name, "abs.conf")
        with open(abs_path, "w") as fh:
            fh.write("abs=1\n")
        self.write("home.conf", "home=1\n")
        self.write("config.conf", "source=%s\nsource=~/home.conf\n" % abs_path)
        tree = self.tree()
        self.assertEqual((tree.get("abs"), tree.get("home")), ("1", "1"))

    def test_plain_relative_path_is_relative_to_cwd(self):
        self.write("cwd.conf", "cwd=1\n")
        self.write("config.conf", "source=cwd.conf\n")
        old = os.getcwd()
        self.addCleanup(os.chdir, old)
        os.chdir(self.dir)
        self.assertEqual(self.tree().get("cwd"), "1")

    def test_symlinked_root_resolves_includes_next_to_link(self):
        # MangoWM doesn't resolve the root symlink: ./x is next to the link.
        dots = os.path.join(self.dir, "dotfiles")
        self.write("dotfiles/config.conf", "source=./bind.conf\n")
        self.write("dotfiles/bind.conf", "where=dotfiles\n")
        self.write("cfg/bind.conf", "where=link-dir\n")
        root = os.path.join(self.dir, "cfg", "config.conf")
        os.symlink(os.path.join(dots, "config.conf"), root)
        self.assertEqual(ms.ConfigTree(root).get("where"), "link-dir")

    def test_symlinked_include_is_edited_through_the_link(self):
        self.write("dotfiles/bind.conf", "repeat_rate=25\n")
        os.symlink(os.path.join(self.dir, "dotfiles", "bind.conf"),
                   os.path.join(self.dir, "bind.conf"))
        self.write("config.conf", "source=./bind.conf\n")
        self.tree().set("repeat_rate", "40")
        self.assertTrue(os.path.islink(os.path.join(self.dir, "bind.conf")))
        self.assertEqual(self.read("dotfiles/bind.conf"), "repeat_rate=40\n")

    def test_variables_expand_in_values_and_source_paths(self):
        self.write("themes/dark.conf", "border_radius=$r\n")
        self.write("config.conf", "var=theme,dark\nvar=r,8\nsource=./themes/${theme}.conf\n"
                                  "focus_color=$unknown\n")
        tree = self.tree()
        self.assertEqual(tree.get("border_radius"), "8")
        self.assertEqual(tree.get("focus_color"), "$unknown")  # unknown names stay literal
        raw = [e.raw for e in tree.scan()[0] if e.key == "border_radius"]
        self.assertEqual(raw, ["$r"])

    def test_variables_must_be_defined_before_use(self):
        self.write("config.conf", "a=$x\nvar=x,1\nb=$x\n")
        tree = self.tree()
        self.assertEqual((tree.get("a"), tree.get("b")), ("$x", "1"))

    # -- line parsing ------------------------------------------------------
    def test_duplicate_keys_last_assignment_wins(self):
        self.write("config.conf", "gap=1\ngap=2\ngap=3\n")
        tree = self.tree()
        self.assertEqual(tree.get("gap"), "3")
        tree.set("gap", "4")
        self.assertEqual(self.read("config.conf"), "gap=1\ngap=2\ngap=4\n")

    def test_lines_mango_rejects_are_ignored(self):
        self.assertIsNone(ms.split_line("key="))           # sscanf needs a value
        self.assertIsNone(ms.split_line("=value"))
        self.assertIsNone(ms.split_line("  # comment=1"))
        self.assertIsNone(ms.split_line("k=" + "x" * 256))  # value over 255 bytes
        self.assertEqual(ms.split_line("key= # c")[:2], ("key", ""))
        self.assertEqual(ms.split_line('k=" padded "')[1], "padded")  # strip_quotes trims

    def test_comments_and_whitespace_preserved(self):
        text = ("# header\n\n  # indented comment\nborder_radius = 6   # round\n"
                "\tshadows=1\n\n# footer\n")
        self.write("config.conf", text)
        tree = self.tree()
        tree.set("border_radius", "8")
        tree.set("shadows", "0")
        self.assertEqual(self.read("config.conf"),
                         text.replace("= 6", "=8").replace("border_radius ", "border_radius")
                             .replace("shadows=1", "shadows=0"))

    def test_quoted_value_keeps_its_quotes(self):
        self.write("config.conf", 'cursor_theme="Bibata Modern"\n')
        self.tree().set("cursor_theme", "Adwaita")
        self.assertEqual(self.read("config.conf"), 'cursor_theme="Adwaita"\n')

    def test_value_with_comment_marker_is_quoted(self):
        tree = self.tree()
        tree.set("exec", "notify-send a #b")
        self.assertEqual(self.read("config.conf"), 'exec="notify-send a #b"\n')
        self.assertEqual(tree.get("exec"), "notify-send a #b")

    def test_unstorable_values_rejected(self):
        tree = self.tree()
        self.assertRaises(ValueError, tree.set, "a", "x\ny")
        self.assertRaises(ValueError, tree.set, "a", "y" * 300)
        self.assertFalse(os.path.exists(self.root))

    def test_crlf_and_missing_final_newline_preserved(self):
        self.write("config.conf", "a=1\r\nb=2", mode="w")
        with open(self.root, "wb") as fh:
            fh.write(b"a=1\r\nb=2")
        self.tree().set("a", "5")
        self.assertEqual(self.read("config.conf", "rb"), b"a=5\r\nb=2")

    def test_non_utf8_bytes_survive_a_write(self):
        with open(self.root, "wb") as fh:
            fh.write(b"# caf\xe9 latin-1\na=1\n")
        self.tree().set("a", "2")
        self.assertEqual(self.read("config.conf", "rb"), b"# caf\xe9 latin-1\na=2\n")

    def test_form_feed_does_not_split_lines(self):
        with open(self.root, "wb") as fh:
            fh.write(b"exec=printf '\x0c'\na=1\n")
        self.tree().set("a", "2")
        self.assertEqual(self.read("config.conf", "rb"), b"exec=printf '\x0c'\na=2\n")

    # -- external edits and write safety -----------------------------------
    def test_external_edit_between_operations_is_kept(self):
        self.write("config.conf", "a=1\nb=2\n")
        tree = self.tree()
        tree.set("a", "3")
        self.write("config.conf", "# added in an editor\na=3\nb=2\nc=9\n")
        tree.set("b", "4")
        self.assertEqual(self.read("config.conf"), "# added in an editor\na=3\nb=4\nc=9\n")

    def test_line_moved_after_scan_is_rescanned(self):
        self.write("config.conf", "a=1\nb=2\n")
        tree = self.tree()
        real_scan = tree.scan
        calls = []

        def racing_scan():
            result = real_scan()
            if not calls:  # an editor inserts a line right after our first scan
                self.write("config.conf", "new=0\na=1\nb=2\n")
            calls.append(1)
            return result
        tree.scan = racing_scan
        tree.set("b", "5")
        self.assertEqual(self.read("config.conf"), "new=0\na=1\nb=5\n")

    def test_unwritable_include_falls_back_to_root(self):
        if os.geteuid() == 0:
            self.skipTest("root can write everything")
        sys_conf = self.write("system.conf", "border_radius=3\n")
        os.chmod(sys_conf, 0o444)
        self.write("config.conf", "source=./system.conf\n")
        tree = self.tree()
        self.assertEqual(tree.set("border_radius", "11"), self.root)
        self.assertEqual(self.read("system.conf"), "border_radius=3\n")
        self.assertEqual(tree.get("border_radius"), "11")

    def test_unwritable_root_raises_permission_error(self):
        if os.geteuid() == 0:
            self.skipTest("root can write everything")
        self.write("config.conf", "a=1\n")
        os.chmod(self.root, 0o444)
        with self.assertRaises(PermissionError) as cm:
            self.tree().set("a", "2")
        self.assertIn("permission denied", ms.describe_error(cm.exception))
        self.assertEqual(self.read("config.conf"), "a=1\n")

    def test_failed_backup_blocks_the_write(self):
        self.write("config.conf", "a=1\n")
        blocker = os.path.join(self.dir, "cache")
        with open(blocker, "w") as fh:  # a file where the backup folder should be
            fh.write("x")
        with self.assertRaises(ms.ConfigError):
            self.tree().set("a", "2")
        self.assertEqual(self.read("config.conf"), "a=1\n")

    def test_backup_keeps_the_original_once_per_session(self):
        self.write("config.conf", "a=1\n")
        tree = self.tree()
        tree.set("a", "2")
        tree.set("a", "3")
        backups = os.path.join(self.dir, "cache", "mango-settings", "backups", ms.SESSION_STAMP)
        files = os.listdir(backups)
        self.assertEqual(len(files), 1)
        with open(os.path.join(backups, files[0])) as fh:
            self.assertEqual(fh.read(), "a=1\n")

    def test_interrupted_write_leaves_old_file_and_no_temp_files(self):
        self.write("config.conf", "a=1\n")
        real_replace = ms.os.replace

        def boom(*_a):
            raise KeyboardInterrupt
        ms.os.replace = boom
        try:
            with self.assertRaises(KeyboardInterrupt):
                self.tree().set("a", "2")
        finally:
            ms.os.replace = real_replace
        self.assertEqual(self.read("config.conf"), "a=1\n")
        self.assertEqual([f for f in os.listdir(self.dir) if f.endswith(".tmp")], [])

    def test_new_file_respects_umask(self):
        old = os.umask(0o077)
        try:
            self.tree().set("a", "1")
        finally:
            os.umask(old)
        self.assertEqual(stat.S_IMODE(os.stat(self.root).st_mode), 0o600)

    def test_add_source_uses_relative_path(self):
        self.write("config.conf", "a=1\n")
        inc = self.write("extra.conf", "b=2\n")
        tree = self.tree()
        tree.add_source(inc)
        self.assertEqual(self.read("config.conf"), "a=1\nsource=./extra.conf\n")
        self.assertTrue(tree.is_sourced(inc))
        self.assertEqual(tree.get("b"), "2")

    def test_detect_root_explicit(self):
        path = self.write("my.cfg", "[global]\n")
        self.assertEqual(ms.detect_root_config(path), (path, "toml"))
        self.assertEqual(ms.detect_root_config(os.path.join(self.dir, "none.conf"))[1], "new")


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

    def test_expand_vars(self):
        v = {"a": "1", "b_2": "x"}
        self.assertEqual(ms.expand_vars("$a-${b_2}-$c-${}-$-$1", v), "1-x-$c-${}-$-$1")

    def test_describe_error(self):
        err = OSError(ms.errno.EROFS, "Read-only file system", "/nix/store/x")
        self.assertIn("read-only", ms.describe_error(err))
        self.assertEqual(ms.describe_error(ValueError("bad")), "bad")

    def test_page_names_without_gtk(self):
        self.assertIn("Keybindings", ms.PAGE_NAMES)

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
