"""守住 `tools/build_gui.py` 的两件要紧事。

为什么值得测：这个问题**只在打包后、在别人机器上**才暴露，
而且症状（`No module named 'win32com'`）会被 `gui/app.py` 误报成"没装 qfluentwidgets"，
把排查方向整个带偏 —— 真踩过一次。所以"构建命令里必须有那几个隐藏导入"
这件事必须由测试钉住，不能靠记忆。
"""

from __future__ import annotations

import pathlib
import os
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

import build_gui                                              # noqa: E402


class TestBuildCommand(unittest.TestCase):

    def setUp(self):
        self.cmd = build_gui.build_command("demo", True, ROOT / "dist")
        self.joined = " ".join(self.cmd)

    def test_uses_onedir_not_onefile(self):
        """Qt 运行时 100 MB+；onefile 每次启动都要解压，Win7 上更慢、更易被杀软误报。"""
        self.assertIn("--onedir", self.joined)
        self.assertNotIn("--onefile", self.joined)

    def test_collects_the_hidden_imports_freezing_needs(self):
        """漏收会报 No module named 'win32com'。

        原因：qfluentwidgets 的依赖 qframelesswindow 用了
        `from win32comext.shell import shellcon`，PyInstaller 静态分析抓不到。
        """
        for needle in ("--collect-all qfluentwidgets",
                       "--collect-submodules win32comext",
                       "--hidden-import win32con",
                       "--hidden-import pythoncom",
                       "--hidden-import pywintypes"):
            self.assertIn(needle, self.joined)

    def test_windowed_is_default_and_console_is_available(self):
        self.assertIn("--windowed", self.joined)
        console = " ".join(build_gui.build_command("demo", False, ROOT / "dist"))
        self.assertIn("--console", console)
        self.assertNotIn("--windowed", console)

    def test_entry_point_is_the_gui(self):
        self.assertTrue(self.cmd[-1].endswith("app.py"))

    def test_navigation_assets_are_included(self):
        self.assertIn("--add-data", self.cmd)
        self.assertIn(str(ROOT / "gui" / "assets") + os.pathsep + "gui/assets", self.cmd)
        for name in ("home.svg", "ship.svg"):
            self.assertTrue((ROOT / "gui" / "assets" / name).is_file())

    def test_paths_include_project_root_and_gui_dir(self):
        """app.py 在运行期往 sys.path 插目录，分析期得靠 --paths 才找得到 ytmon / qt_compat。"""
        self.assertIn(str(ROOT), self.cmd)
        self.assertIn(str(ROOT / "gui"), self.cmd)


class TestTemplateShipping(unittest.TestCase):
    """产物目录必须自带配置模板。

    冻结后配置基准是 exe 所在目录，模板不在旁边的话，
    第一次运行只会看到"找不到配置文件" —— 而 windowed 产物连这句话都看不见。
    """

    def test_template_is_copied_into_the_output_dir(self):
        with tempfile.TemporaryDirectory() as d:
            dst = build_gui.copy_template_into(pathlib.Path(d))
            self.assertTrue(dst)
            self.assertTrue(pathlib.Path(dst).is_file())
            self.assertEqual(pathlib.Path(dst).name, "watchlist.example.json")

    def test_does_not_create_a_real_config(self):
        """构建脚本不该凭空造出 watchlist.json —— 那里面会有告警通道密钥。"""
        with tempfile.TemporaryDirectory() as d:
            build_gui.copy_template_into(pathlib.Path(d))
            self.assertFalse((pathlib.Path(d) / "watchlist.json").exists())

    def test_missing_output_dir_is_not_fatal(self):
        self.assertEqual(build_gui.copy_template_into(ROOT / "no-such-dir-ytmon"), "")


class TestEnvironmentGuard(unittest.TestCase):

    def test_wrong_python_version_is_rejected(self):
        """用 3.14 编出来的 exe 里是 python314.dll —— 在开发机上跑得好好的，到 Win7 才炸。"""
        problems = build_gui.check_environment(version_info=(3, 14, 0))
        self.assertTrue(any("不是 3.8" in p for p in problems),
                        "非 3.8 解释器必须被拦住，否则会静默产出 Win7 跑不了的二进制")
        self.assertTrue(any("--force" in p for p in problems),
                        "拒绝的同时要给出路，否则用户只会绕过检查")

    def test_python_38_passes_the_version_rule(self):
        problems = build_gui.check_environment(version_info=(3, 8, 10))
        self.assertEqual([p for p in problems if "不是 3.8" in p], [])


if __name__ == "__main__":
    unittest.main()
