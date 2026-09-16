"""守住 `tools/build_release.py` —— 现场包的不变量。

为什么值得测：这些不变量一旦破了，症状是**"包看着正常、到现场才发现不行"**：

* `.cmd` 里混进中文 → 中文 Windows 控制台按代码页读，可能整行乱码；
* 两个 exe 用了同一个 `_internal` 目录名 → 后打包的把先打包的运行时覆盖掉，
  在开发机上还能跑（因为各自单独测过），合并后才炸；
* 发布资产没被凭证扫描覆盖 → D1 那条泄露教训会重演。
"""

from __future__ import annotations

import pathlib
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

import build_release                                          # noqa: E402
import make_bundle                                            # noqa: E402
import build_gui                                              # noqa: E402


class TestTargetsAreMergeable(unittest.TestCase):
    """三个 exe 要并进同一个文件夹，所以运行时目录名必须互不相同。"""

    def test_exe_names_are_unique(self):
        names = [t["name"] for t in build_release.TARGETS]
        self.assertEqual(len(names), len(set(names)), names)

    def test_contents_directories_are_unique(self):
        dirs = [t["contents"] for t in build_release.TARGETS]
        self.assertEqual(len(dirs), len(set(dirs)),
                         "两个 exe 共用 _internal 目录会互相覆盖运行时：%s" % dirs)

    def test_contents_directory_is_actually_passed(self):
        staging = pathlib.Path(tempfile.gettempdir())
        for target in build_release.TARGETS:
            argv = build_release.pyinstaller_argv(target, staging, staging)
            self.assertIn("--contents-directory", argv)
            self.assertIn(target["contents"], argv)

    def test_every_target_builds_onedir(self):
        staging = pathlib.Path(tempfile.gettempdir())
        for target in build_release.TARGETS:
            argv = build_release.pyinstaller_argv(target, staging, staging)
            self.assertIn("--onedir", argv)
            self.assertNotIn("--onefile", argv)

    def test_entry_points_exist(self):
        for target in build_release.TARGETS:
            self.assertTrue((ROOT / target["entry"]).is_file(),
                            "入口不存在：%s" % target["entry"])

    def test_gui_uses_shared_build_command(self):
        target = next(t for t in build_release.TARGETS if t['entry'] == 'gui/app.py')
        staging = ROOT / '.toolchain/test-staging'
        expected = build_gui.build_command(target['name'], target['windowed'], staging / 'dist',
                                          contents_directory=target['contents'],
                                          workpath=staging / ('work-' + target['name']),
                                          specpath=staging / 'spec')
        self.assertEqual(build_release.pyinstaller_argv(target, staging, staging / 'spec'), expected)


class TestOfflineGuiDependencies(unittest.TestCase):
    def test_download_includes_both_requirement_files(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch('build_release.subprocess.run') as run:
                run.return_value.returncode = 0
                build_release.download_wheels(pathlib.Path(directory), 'https://example.invalid/simple')
        argv = run.call_args[0][0]
        self.assertIn(str(ROOT / 'requirements-win7.txt'), argv)
        self.assertIn(str(ROOT / 'requirements-win7-gui.txt'), argv)


class TestCmdAssets(unittest.TestCase):

    def cmd_assets(self):
        return [p for p in build_release.PACKAGING.glob("*.cmd")]

    def test_there_are_cmd_assets(self):
        self.assertTrue(self.cmd_assets())

    def test_cmd_assets_are_ascii_only(self):
        """`.cmd` 里绝不放中文。

        cmd 是按控制台代码页逐字节读批处理文件的，中文在没设对代码页时
        会整行乱码甚至被截断。所有中文解释都放在 .txt 里。
        """
        for p in self.cmd_assets():
            raw = p.read_bytes()
            bad = [(i, b) for i, b in enumerate(raw) if b > 127]
            self.assertEqual(bad, [], "%s 里有非 ASCII 字节（首个在偏移 %s）" % (p.name, bad[:1]))

    def test_text_assets_all_exist(self):
        for src_name, _rel, _bom in build_release.TEXT_ASSETS:
            self.assertTrue((build_release.PACKAGING / src_name).is_file(),
                            "缺少打包资产：%s" % src_name)

    def test_cmd_clears_pythonioencoding(self):
        """必须显式清空 PYTHONIOENCODING。

        实测：冻结的 exe **忽略**这个变量，而 `ytmon/console.py` 会因为"它存在"
        而放弃把重定向输出切成 UTF-8 —— 结果是日志落成 cp936。
        操作者环境里有没有这个变量，我们控制不了，所以 .cmd 里必须自己清掉。
        """
        for p in self.cmd_assets():
            text = p.read_text(encoding="utf-8")
            self.assertIn("set PYTHONIOENCODING=", text,
                          "%s 没清空 PYTHONIOENCODING，日志编码会随操作者环境变" % p.name)


class TestWindowsTextEncoding(unittest.TestCase):

    def setUp(self):
        self._tmp = pathlib.Path(tempfile.mkdtemp(prefix="ytmon-rel-"))
        self.addCleanup(lambda: __import__("shutil").rmtree(str(self._tmp), ignore_errors=True))
        self.src = self._tmp / "src.txt"
        self.src.write_text("第一行\n第二行\n", encoding="utf-8")

    def test_txt_gets_utf8_bom(self):
        """Win7 记事本对无 BOM 的 UTF-8 会猜错编码，中文直接乱码。"""
        dst = self._tmp / "out.txt"
        build_release.write_windows_text(self.src, dst, bom=True)
        raw = dst.read_bytes()
        self.assertTrue(raw.startswith(b"\xef\xbb\xbf"), "缺 UTF-8 BOM")
        self.assertIn("第一行".encode("utf-8"), raw)

    def test_crlf_line_endings(self):
        """cmd 对 LF-only 的 goto/label 处理不可靠。"""
        dst = self._tmp / "out.txt"
        build_release.write_windows_text(self.src, dst, bom=True)
        raw = dst.read_bytes()
        self.assertIn(b"\r\n", raw)
        self.assertNotIn(b"\n", raw.replace(b"\r\n", b""))

    def test_cmd_gets_no_bom(self):
        dst = self._tmp / "out.cmd"
        build_release.write_windows_text(self.src, dst, bom=False)
        self.assertFalse(dst.read_bytes().startswith(b"\xef\xbb\xbf"))


class TestAssetSecretScan(unittest.TestCase):
    """双向可靠：既不放过真密钥，也不误报占位符。

    只测"没误报"的扫描器等于没扫（dead-ends D1 的教训）。
    """

    def test_catches_a_real_looking_secret(self):
        # 形状像真 token，且**不含 FAKE 标记**（豁免只认 FAKE）
        leak = "loginVerifyCode=" + "A" * 60
        found = build_release.scan_assets([("现场必读.txt", leak.encode("utf-8"))])
        self.assertTrue(found, "真密钥形状没被抓到")

    def test_catches_a_secret_hiding_in_a_cmd(self):
        """.cmd 默认会被后缀过滤整份跳过 —— 这里确认补丁接上了。"""
        leak = ("@echo off\r\nset T=loginVerifyCode=" + "B" * 60 + "\r\n").encode("utf-8")
        found = build_release.scan_assets([("第1步-环境自检.cmd", leak)])
        self.assertTrue(found, ".cmd 里的密钥漏过了扫描")

    def test_placeholders_are_not_flagged(self):
        text = "loginVerifyCode=<你的令牌>\naccess_token=...\n"
        found = build_release.scan_assets([("现场必读.txt", text.encode("utf-8"))])
        self.assertEqual(found, [])


if __name__ == "__main__":
    unittest.main()
