"""守住 `tools/run_tests.py` 的两个机制：探测准不准、补丁真不真管用。

为什么值得测：这个脚本是"受限环境下还能不能拿到可信测试信号"的唯一入口，
它自己悄悄坏掉的话，我们会**重新**把沙箱假象当成代码回归（正是 E1 那个坑）。
"""

from __future__ import annotations

import os
import pathlib
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

import run_tests                                              # noqa: E402


class TestProbe(unittest.TestCase):

    def test_probe_returns_a_bool_and_a_reason(self):
        ok, why = run_tests._mkdtemp_is_writable()
        self.assertIsInstance(ok, bool)
        self.assertIsInstance(why, str)
        self.assertTrue(why)

    def test_probe_reports_the_truth(self):
        """探测说"能写"，就必须真的能写（不能只看建目录成功）。"""
        ok, why = run_tests._mkdtemp_is_writable()
        if not ok:
            self.skipTest("当前环境 mkdtemp 目录不可写（%s）—— 由绕行路径覆盖" % why)

        d = tempfile.mkdtemp(prefix="ytmon-verify-")
        try:
            target = os.path.join(d, "w.txt")
            with open(target, "w", encoding="utf-8") as fh:
                fh.write("x")
            self.assertTrue(os.path.exists(target))
        finally:
            import shutil
            shutil.rmtree(d, ignore_errors=True)


class TestPatch(unittest.TestCase):

    def setUp(self):
        self.original = tempfile.mkdtemp
        self.addCleanup(self._restore)

    def _restore(self):
        tempfile.mkdtemp = self.original

    def test_patched_mkdtemp_returns_a_writable_unique_dir(self):
        run_tests._patch_mkdtemp()

        d1 = tempfile.mkdtemp(prefix="ytmon-p1-")
        d2 = tempfile.mkdtemp(prefix="ytmon-p2-")
        self.assertNotEqual(d1, d2)
        for d in (d1, d2):
            self.assertTrue(os.path.isdir(d))
            with open(os.path.join(d, "f.txt"), "w", encoding="utf-8") as fh:   # 关键：必须写得进去
                fh.write("x")

    def test_temporary_directory_works_with_the_patch(self):
        """测试里绝大多数用例用的是 TemporaryDirectory，它内部也走 mkdtemp。"""
        run_tests._patch_mkdtemp()
        with tempfile.TemporaryDirectory(prefix="ytmon-td-") as d:
            with open(os.path.join(d, "f.txt"), "w", encoding="utf-8") as fh:
                fh.write("x")
            self.assertTrue(os.path.isdir(d))
        self.assertFalse(os.path.exists(d))

    def test_patch_does_not_touch_private_tempfile_api(self):
        """不能依赖 tempfile 的私有 API —— 它的形态跨版本变过（3.14 上踩过）。

        用 AST 判断**代码里有没有引用**，不能拿文本匹配：
        文档字符串里正解释着"为什么不用它"，文本匹配会自己把自己判失败。
        """
        import ast

        tree = ast.parse((ROOT / "tools" / "run_tests.py").read_text(encoding="utf-8"))
        referenced = {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)}
        self.assertNotIn("_get_candidate_names", referenced)


if __name__ == "__main__":
    unittest.main()
