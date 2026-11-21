"""Disguise in Task Manager (disguise.py): the renamed copy, its environment,
the cleanup of old copies, and the program folder the copy keeps using."""

import json
import os
import sys
import tempfile
import unittest
from unittest import mock

import disguise
import diag
from jigsaw_solver import cache_location


class NamesTest(unittest.TestCase):
    def test_names_fit_linux_process_name_and_avoid_system_names(self):
        system = {"svchost", "explorer", "notepad", "wordpad", "taskhost", "taskmgr",
                  "csrss", "lsass", "winlogon", "services", "dwm", "systemd"}
        for a in disguise.FIRST_WORDS:
            for b in disguise.SECOND_WORDS:
                stem = disguise.file_stem(f"{a} {b}")
                self.assertLessEqual(len(stem), 15, stem)
                self.assertTrue(stem.isalpha(), stem)
                self.assertNotIn(stem.lower(), system)

    def test_random_name_is_two_known_words(self):
        for _ in range(50):
            a, b = disguise.random_name().split(" ")
            self.assertIn(a, disguise.FIRST_WORDS)
            self.assertIn(b, disguise.SECOND_WORDS)


class EnvironmentTest(unittest.TestCase):
    def test_pyinstaller_and_bundle_paths_removed(self):
        bundle = os.path.join(tempfile.gettempdir(), "_MEI12345")
        env = {
            "_PYI_APPLICATION_HOME_DIR": bundle,
            "_PYI_ARCHIVE_FILE": "/x/app",
            "_PYI_PARENT_PROCESS_LEVEL": "1",
            "_MEIPASS2": bundle,
            "QT_PLUGIN_PATH": os.path.join(bundle, "PySide6", "plugins"),
            "PATH": os.pathsep.join([bundle, "/usr/bin", os.path.join(bundle, "sub")]),
            "HOME": "/home/u",
            disguise.ENV_HOME: "/opt/bot",
            disguise.ENV_NAME: "Pixel Studio",
            disguise.ENV_ORIGINAL: "/opt/bot/app",
        }
        out = disguise.clean_environment(env, bundle)
        for key in ("_PYI_APPLICATION_HOME_DIR", "_PYI_ARCHIVE_FILE", "_PYI_PARENT_PROCESS_LEVEL",
                    "_MEIPASS2", "QT_PLUGIN_PATH", disguise.ENV_HOME, disguise.ENV_NAME,
                    disguise.ENV_ORIGINAL):
            self.assertNotIn(key, out)
        self.assertEqual(out["PATH"], "/usr/bin")
        self.assertEqual(out["HOME"], "/home/u")
        self.assertEqual(out["PYINSTALLER_RESET_ENVIRONMENT"], "1")

    def test_linux_library_path_restored(self):
        out = disguise.clean_environment(
            {"LD_LIBRARY_PATH": "/tmp/_MEIabc", "LD_LIBRARY_PATH_ORIG": "/usr/local/lib"}, "/tmp/_MEIabc")
        self.assertEqual(out["LD_LIBRARY_PATH"], "/usr/local/lib")
        self.assertNotIn("LD_LIBRARY_PATH_ORIG", out)

    def test_similar_prefix_is_not_inside_bundle(self):
        out = disguise.clean_environment({"X": "/tmp/_MEIabc2/x"}, "/tmp/_MEIabc")
        self.assertEqual(out["X"], "/tmp/_MEIabc2/x")


class CopiesTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = self.tmp.name
        self.env = mock.patch.dict(os.environ, {
            "LOCALAPPDATA": os.path.join(root, "local"),
            "XDG_STATE_HOME": os.path.join(root, "state"),
            "XDG_CACHE_HOME": os.path.join(root, "cache"),
            "TMP": os.path.join(root, "temp"), "TEMP": os.path.join(root, "temp"),
            "TMPDIR": os.path.join(root, "temp"),
        })
        self.env.start()
        os.makedirs(os.path.join(root, "temp"), exist_ok=True)
        tempfile.tempdir = None
        self.source = os.path.join(root, "app.bin")
        with open(self.source, "wb") as f:
            f.write(b"\x7fELF fake program")

    def tearDown(self):
        self.env.stop()
        tempfile.tempdir = None
        self.tmp.cleanup()

    def test_copy_recorded_and_removed_by_next_cleanup(self):
        exe = disguise.make_copy(self.source, "Cedar Journal")
        self.assertTrue(os.path.isfile(exe))
        self.assertTrue(os.path.basename(exe).startswith("CedarJournal"))
        self.assertTrue(os.path.abspath(exe).startswith(os.path.abspath(disguise.copies_root())))
        with open(disguise.ledger_path(), encoding="utf-8") as f:
            self.assertEqual(json.load(f), [exe])
        with mock.patch.object(disguise, "_in_use_linux", return_value=False):
            self.assertEqual(disguise.cleanup_old_copies(), 0)
        self.assertFalse(os.path.exists(exe))
        self.assertFalse(os.path.exists(os.path.dirname(exe)))
        self.assertFalse(os.path.exists(disguise.ledger_path()))

    def test_running_copy_is_kept_for_later(self):
        exe = disguise.make_copy(self.source, "Nova Lab")
        with mock.patch.object(disguise, "_in_use_linux", return_value=True), \
                mock.patch.object(disguise.os, "remove", side_effect=PermissionError("in use")):
            self.assertEqual(disguise.cleanup_old_copies(), 1)
        self.assertTrue(os.path.exists(exe))
        with mock.patch.object(disguise, "_in_use_linux", return_value=False):
            self.assertEqual(disguise.cleanup_old_copies(), 0)

    def test_cleanup_never_removes_other_files(self):
        exe = disguise.make_copy(self.source, "Leaf Box")
        other = os.path.join(os.path.dirname(exe), "someone_elses.txt")
        with open(other, "w") as f:
            f.write("keep")
        with mock.patch.object(disguise, "_in_use_linux", return_value=False):
            disguise.cleanup_old_copies()
        self.assertTrue(os.path.exists(other))

    def test_enabled_reads_config_next_to_program(self):
        cfg_dir = os.path.join(self.tmp.name, "cfg")
        os.makedirs(cfg_dir)
        with mock.patch.dict(os.environ, {"FISHBOT_CONFIG_DIR": cfg_dir}):
            self.assertFalse(disguise.enabled_in_config())
            with open(os.path.join(cfg_dir, "bot_config.json"), "w") as f:
                json.dump({disguise.CONFIG_KEY: True}, f)
            self.assertTrue(disguise.enabled_in_config())
            with open(os.path.join(cfg_dir, "bot_config.json"), "w") as f:
                f.write("{broken")
            self.assertFalse(disguise.enabled_in_config())


class RelaunchTest(unittest.TestCase):
    def test_nothing_happens_from_source(self):
        with mock.patch.object(disguise.subprocess, "Popen") as popen:
            disguise.maybe_relaunch()
        popen.assert_not_called()

    def test_frozen_with_setting_starts_copy_and_exits(self):
        with mock.patch.object(sys, "frozen", True, create=True), \
                mock.patch.object(sys, "argv", ["app"]), \
                mock.patch.dict(os.environ, {}, clear=False), \
                mock.patch.object(disguise, "cleanup_old_copies"), \
                mock.patch.object(disguise, "enabled_in_config", return_value=True), \
                mock.patch.object(disguise, "make_copy", return_value="/c/PixelLab") as make, \
                mock.patch.object(disguise.subprocess, "Popen") as popen:
            os.environ.pop(disguise.ENV_NAME, None)
            with self.assertRaises(SystemExit):
                disguise.maybe_relaunch()
        make.assert_called_once()
        cmd = popen.call_args.args[0]
        env = popen.call_args.kwargs["env"]
        self.assertEqual(cmd, ["/c/PixelLab"])
        self.assertEqual(env[disguise.ENV_NAME], make.call_args.args[1])
        self.assertEqual(env[disguise.ENV_ORIGINAL], os.path.abspath(sys.executable))
        self.assertIn(disguise.ENV_HOME, env)

    def test_copy_itself_and_helpers_do_not_relaunch(self):
        for argv, env in ((["app"], {disguise.ENV_NAME: "Pixel Lab"}),
                          (["app", "-c", "from multiprocessing.resource_tracker import main"], {}),
                          (["app", "--multiprocessing-fork", "x"], {})):
            with mock.patch.object(sys, "frozen", True, create=True), \
                    mock.patch.object(sys, "argv", argv), \
                    mock.patch.dict(os.environ, env), \
                    mock.patch.object(disguise, "enabled_in_config", return_value=True), \
                    mock.patch.object(disguise.subprocess, "Popen") as popen:
                if not env:
                    os.environ.pop(disguise.ENV_NAME, None)
                disguise.maybe_relaunch()
            popen.assert_not_called()

    def test_failure_keeps_running_with_reason(self):
        with mock.patch.object(sys, "frozen", True, create=True), \
                mock.patch.object(sys, "argv", ["app"]), \
                mock.patch.object(disguise, "cleanup_old_copies"), \
                mock.patch.object(disguise, "enabled_in_config", return_value=True), \
                mock.patch.object(disguise, "make_copy", side_effect=OSError("disk full")), \
                mock.patch.object(disguise, "LAST_ERROR", None):
            os.environ.pop(disguise.ENV_NAME, None)
            disguise.maybe_relaunch()   # no SystemExit
            self.assertIn("disk full", disguise.LAST_ERROR)

    def test_restart_starts_the_real_program_without_disguise_vars(self):
        with mock.patch.object(sys, "frozen", True, create=True), \
                mock.patch.object(sys, "argv", ["/tmp/ab12/PixelLab"]), \
                mock.patch.dict(os.environ, {disguise.ENV_ORIGINAL: "/opt/bot/app",
                                             disguise.ENV_NAME: "Pixel Lab",
                                             disguise.ENV_HOME: "/opt/bot"}):
            cmd, env = disguise.restart_command()
        self.assertEqual(cmd, ["/opt/bot/app"])
        self.assertNotIn(disguise.ENV_NAME, env)
        self.assertNotIn(disguise.ENV_HOME, env)


class ProgramFolderTest(unittest.TestCase):
    def test_copy_keeps_the_real_program_folder(self):
        with tempfile.TemporaryDirectory() as home, \
                mock.patch.object(sys, "frozen", True, create=True), \
                mock.patch.dict(os.environ, {disguise.ENV_HOME: home}):
            self.assertEqual(diag.program_dir(), home)
            self.assertEqual(cache_location.program_cache_dir(),
                             os.path.join(home, "data", "cache", "jigsaw"))

    def test_missing_home_falls_back_to_the_executable(self):
        with mock.patch.object(sys, "frozen", True, create=True), \
                mock.patch.dict(os.environ, {disguise.ENV_HOME: "/does/not/exist"}):
            self.assertEqual(diag.program_dir(), os.path.dirname(os.path.abspath(sys.executable)))


if __name__ == "__main__":
    unittest.main()
