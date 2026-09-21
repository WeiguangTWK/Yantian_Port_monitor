"""Linux 冻结构建的离线检查；Windows 开发机无需安装 PySide6。"""

import pathlib
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from tools import build_linux_gui
from ytmon import paths


class TestLinuxBuild(unittest.TestCase):
    def test_command_uses_linux_pyside6_onedir(self):
        cmd = build_linux_gui.build_command(
            ROOT / '.toolchain' / 'gui-version.txt', ROOT / 'dist' / 'linux')
        joined = ' '.join(cmd)
        self.assertIn('--onedir', cmd)
        self.assertIn('--console', cmd)
        self.assertNotIn('--windowed', cmd)
        self.assertIn('--copy-metadata PySide6', joined)
        self.assertIn('--copy-metadata PySide6-Fluent-Widgets', joined)
        self.assertIn('--hidden-import PySide6.QtSvg', joined)
        self.assertIn('--exclude-module PySide2', joined)
        self.assertIn(str(ROOT / 'gui' / 'app.py'), cmd)

    def test_output_verifier_checks_required_resources(self):
        with tempfile.TemporaryDirectory() as directory:
            output = pathlib.Path(directory)
            with self.assertRaisesRegex(RuntimeError, 'gui-version.txt'):
                build_linux_gui.verify_output(output)
            runtime = output / build_linux_gui.CONTENTS
            for relative in ('gui-version.txt', 'LICENSE',
                             'licenses/LGPL-3.0.txt'):
                path = runtime / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.touch()
            for name in build_linux_gui.ASSETS:
                path = runtime / 'gui' / 'assets' / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.touch()
            for name in ('PySide6-6.11.1.dist-info',
                         'PySide6_Fluent_Widgets-1.11.3.dist-info'):
                (runtime / name).mkdir()
            for name in ('libqsvgicon.so', 'libqjpeg.so', 'libqxcb.so'):
                (runtime / name).touch()
            build_linux_gui.verify_output(output)


class TestSystemProgramEnv(unittest.TestCase):
    def test_frozen_linux_restores_original_ld_path(self):
        with patch.object(paths.sys, 'frozen', True, create=True), patch.object(
                paths.sys, 'platform', 'linux'), patch.dict(
                paths.os.environ, {'LD_LIBRARY_PATH': '/bundle',
                                   'LD_LIBRARY_PATH_ORIG': '/system'}, clear=True):
            self.assertEqual(paths.system_program_env()['LD_LIBRARY_PATH'], '/system')

    def test_frozen_linux_removes_bundle_ld_path_without_original(self):
        with patch.object(paths.sys, 'frozen', True, create=True), patch.object(
                paths.sys, 'platform', 'linux'), patch.dict(
                paths.os.environ, {'LD_LIBRARY_PATH': '/bundle'}, clear=True):
            self.assertNotIn('LD_LIBRARY_PATH', paths.system_program_env())

    def test_source_and_windows_keep_environment(self):
        for frozen, platform in ((False, 'linux'), (True, 'win32')):
            with self.subTest(frozen=frozen, platform=platform), patch.object(
                    paths.sys, 'frozen', frozen, create=True), patch.object(
                    paths.sys, 'platform', platform), patch.dict(
                    paths.os.environ, {'LD_LIBRARY_PATH': '/current'}, clear=True):
                self.assertEqual(paths.system_program_env()['LD_LIBRARY_PATH'], '/current')


if __name__ == '__main__':
    unittest.main()
