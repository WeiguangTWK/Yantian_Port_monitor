"""守住 `ytmon/paths.py` —— 冻结模式的路径基准。

为什么值得测：这个模块的错误**只在打包后、在别人机器上**才会暴露，
而且表现是"配置读不到 / 每次当首次运行"，属于最难从现场描述里反推的那类故障。
所以关键性质必须在离线测试里钉住。
"""

from __future__ import annotations

import os
import pathlib
import shutil
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from ytmon import paths                                          # noqa: E402


class TestSourceMode(unittest.TestCase):
    """源码运行时，一切必须保持**惰性** —— 不 chdir、不改行为。"""

    def test_not_frozen(self):
        self.assertFalse(paths.is_frozen())

    def test_app_base_dir_is_project_root(self):
        self.assertEqual(paths.app_base_dir(), ROOT)

    def test_anchor_does_not_change_cwd(self):
        before = os.getcwd()
        base = paths.anchor_to_app_dir()
        self.assertEqual(os.getcwd(), before, "源码运行时不该动 CWD")
        self.assertEqual(base, ROOT)

    def test_writable_warning_is_silent_when_not_frozen(self):
        self.assertEqual(paths.writable_warning("/definitely/not/here"), "")


class TestWritableProbe(unittest.TestCase):

    def test_true_for_a_real_writable_dir(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertTrue(paths.is_writable_dir(d))

    def test_false_for_missing_dir(self):
        self.assertFalse(paths.is_writable_dir(ROOT / "no-such-dir-ytmon"))

    def test_probe_file_is_cleaned_up(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertTrue(paths.is_writable_dir(d))
            leftovers = [p.name for p in pathlib.Path(d).iterdir()]
            self.assertEqual(leftovers, [], "探针文件必须自己删干净")


class TestFrozenMode(unittest.TestCase):
    """模拟冻结环境。必须把 sys 属性和 CWD 都还原，否则会污染同进程里的其他测试。"""

    def setUp(self):
        self._frozen = getattr(sys, "frozen", None)
        self._exe = sys.executable
        self._cwd = os.getcwd()
        self._tmp = tempfile.mkdtemp(prefix="ytmon-frozen-")
        sys.frozen = True                                          # type: ignore[attr-defined]
        sys.executable = os.path.join(self._tmp, "ytmon-gui.exe")
        self.addCleanup(self._restore)

    def _restore(self):
        if self._frozen is None:
            try:
                del sys.frozen                                     # type: ignore[attr-defined]
            except AttributeError:
                pass
        else:
            sys.frozen = self._frozen                              # type: ignore[attr-defined]
        sys.executable = self._exe
        os.chdir(self._cwd)
        shutil.rmtree(self._tmp, ignore_errors=True)

    def test_frozen_is_detected(self):
        self.assertTrue(paths.is_frozen())

    def test_app_base_dir_is_the_exe_directory(self):
        self.assertEqual(paths.app_base_dir(), pathlib.Path(self._tmp))

    def test_anchor_moves_cwd_to_the_exe_directory(self):
        base = paths.anchor_to_app_dir()
        self.assertEqual(base, pathlib.Path(self._tmp))
        self.assertEqual(pathlib.Path(os.getcwd()).resolve(), pathlib.Path(self._tmp).resolve(),
                         "冻结后 CWD 必须钉在 exe 目录，否则状态文件会散到别处")

    def test_warning_is_silent_when_writable(self):
        self.assertEqual(paths.writable_warning(), "")

    def test_warning_fires_when_not_writable(self):
        """指向一个不存在的目录 = 不可写，必须给出人能照做的提示。"""
        warn = paths.writable_warning(pathlib.Path(self._tmp) / "missing")
        self.assertTrue(warn)
        self.assertIn("不可写", warn)
        self.assertIn("Program Files", warn, "警告里要给出可照做的建议")


if __name__ == "__main__":
    unittest.main()
