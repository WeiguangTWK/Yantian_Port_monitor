"""打包与"裸机器"可用性测试。

守住的是一条**很容易被无意破坏**的约定：
    `ytmon.browser_find` 必须能在没装任何第三方依赖的机器上导入。

Win7 机器上第一次自检时就是这种状态 —— 用户还没 pip install 任何东西，
但已经需要知道"这台机器有没有可用的浏览器"。
只要有人往 `ytmon/__init__.py` 里加一句 eager import，这个能力就没了。
"""

from __future__ import annotations

import pathlib
import subprocess
import sys
import textwrap
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

BARE_SCRIPT = textwrap.dedent("""
    import sys, importlib.abc
    BLOCKED = {"requests", "aiohttp", "urllib3", "certifi", "idna",
               "charset_normalizer", "PySide2", "PySide6", "qfluentwidgets"}

    class Blocker(importlib.abc.MetaPathFinder):
        def find_spec(self, fullname, path=None, target=None):
            if fullname.split(".")[0] in BLOCKED:
                raise ModuleNotFoundError(f"No module named {fullname.split('.')[0]!r}")
            return None

    sys.meta_path.insert(0, Blocker())
    sys.path.insert(0, sys.argv[1])

    # 1) 纯标准库子模块必须可用
    from ytmon.browser_find import find_browser, search_roots   # noqa: F401
    print("BROWSER_FIND_OK")

    # 2) 版本号必须可用（它在 __init__ 里，不触发子模块导入）
    import ytmon
    print("VERSION", ytmon.__version__)

    # 3) 重型子模块应当如实报缺依赖，而不是被 __init__ 提前拖垮
    try:
        from ytmon.http_client import HttpClient       # noqa: F401
        print("HTTP_UNEXPECTEDLY_OK")
    except ModuleNotFoundError:
        print("HTTP_NEEDS_DEPS_AS_EXPECTED")
""")


class TestBareMachine(unittest.TestCase):
    def test_browser_find_works_without_third_party(self):
        proc = subprocess.run([sys.executable, "-c", BARE_SCRIPT, str(ROOT)],
                              capture_output=True, text=True, timeout=120)
        out = proc.stdout
        self.assertIn("BROWSER_FIND_OK", out,
                      f"裸机器上无法导入 browser_find —— 有人加了 eager import？\n"
                      f"stdout={out}\nstderr={proc.stderr}")
        self.assertIn("HTTP_NEEDS_DEPS_AS_EXPECTED", out,
                      "http_client 在缺依赖时应当报错，而不是静默成功")
        self.assertNotIn("HTTP_UNEXPECTEDLY_OK", out)


class TestLazyExports(unittest.TestCase):
    def test_all_exports_resolve(self):
        """__all__ 里的每个名字都要真的能取到（防手写映射表打错字）。"""
        import ytmon
        missing = []
        for name in ytmon.__all__:
            try:
                getattr(ytmon, name)
            except Exception as e:                            # noqa: BLE001
                missing.append(f"{name}: {type(e).__name__}: {e}")
        self.assertEqual(missing, [], f"以下导出无法解析：{missing}")

    def test_unknown_attribute_raises_attributeerror(self):
        import ytmon
        with self.assertRaises(AttributeError):
            ytmon.NoSuchThing                        # noqa: B018

    def test_dir_includes_exports(self):
        import ytmon
        self.assertIn("MonitorService", dir(ytmon))


class TestTransferManifest(unittest.TestCase):
    """Win7 拷贝清单必须与磁盘实际情况一致。"""

    def test_required_paths_exist(self):
        required = [
            "ytmon/__init__.py",
            "ytmon/browser_find.py",
            "ytmon/cdp.py",
            "ytmon/http_client.py",
            "ytmon/service.py",
            "ytmon/notify.py",
            "ytmon/cli.py",
            "run_monitor.py",
            "tools/env_check.py",
            "watchlist.example.json",
        ]
        for rel in required:
            self.assertTrue((ROOT / rel).exists(), f"清单里缺文件：{rel}")


    def test_historical_and_personal_docs_are_not_delivered(self):
        from tools.make_bundle import iter_files
        paths = {path.relative_to(ROOT).as_posix() for path in iter_files(False)}
        for path in ("RECON-盐田船期.md", "docs/GUI-技术选型与基座.md",
                     "docs/GUI-Win7打包实测.md", "docs/OpenViking-Codex.md",
                     "tools/renew_token.py", "tools/watch_token.py", "ytmon/auth.py"):
            self.assertNotIn(path, paths)


class TestSecretScanner(unittest.TestCase):
    """凭证扫描器要**双向**可靠。

    只测"没误报"是不够的 —— 一个永远返回空列表的扫描器也能通过那种测试，
    而它恰恰是最危险的：让人以为"扫过了就安全"。
    所以这里同时断言**真密钥必须被抓到**。

    分两层测：
      * `match_secrets` —— 模式本身有没有效（不含任何豁免）
      * `scan_texts`    —— 文件过滤与假样本豁免
    """

    @classmethod
    def setUpClass(cls):
        sys.path.insert(0, str(ROOT / "tools"))
        import make_bundle
        cls.match = staticmethod(make_bundle.match_secrets)
        cls.scan = staticmethod(make_bundle.scan_texts)

    def hits(self, raw: bytes, name: str = "probe.json") -> bool:
        return bool(self.scan([(name, raw)]))

    def test_real_credentials_are_caught(self):
        # ⚠️ 这些字符串**故意拆开拼接**，不要在源码里合成一整条。
        # 否则本文件自己就含一条完整密钥形态，打包扫描器会（正确地）拦下来，
        # 搞得每次打包都被自己的测试样本卡住。
        cases = {
            "钉钉":
                "https://oapi.dingtalk.com/robot/send?access_token="
                + "abc123def456ghi789jkl012",
            "企业微信":
                "https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key="
                + "693a91f6-7xxx-4bc4-97a0-0ec2sifa5aaa",
            "飞书":
                "https://open.feishu.cn/open-apis/bot/v2/hook/"
                + "9c8b7a65-4321-0fed-cba9-876543210fed",
            "邮箱密码":
                '{"smtp_password": "' + "MyRealPassword123" + '"}',
            "船期 token":
                "loginVerifyCode=" + "A" * 60,
        }
        for label, text in cases.items():
            with self.subTest(label):
                self.assertTrue(self.match(text), f"{label} 的真实密钥没被抓到")

    def test_documentation_placeholders_do_not_trip_it(self):
        """文档里的占位符必须放行，否则打包会一直被自己的示例挡住。"""
        cases = [
            "https://oapi.dingtalk.com/robot/send?access_token=...",
            "https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=...",
            "https://open.feishu.cn/open-apis/bot/v2/hook/...",
            '{"smtp_password": "授权码"}',
            '{"smtp_password": "p"}',
        ]
        for text in cases:
            with self.subTest(text[:50]):
                self.assertEqual(self.match(text), [], f"占位符误报了：{text[:60]!r}")

    def test_fake_marker_exempts_only_that_match(self):
        """FAKE 豁免必须只作用于**那一处匹配**，不能把整个文件放行。"""
        # 同样拆开拼接，避免本文件源码出现完整密钥形态
        real = ("https://oapi.dingtalk.com/robot/send?access_token="
                + "R" * 28)
        raw = ('{"smtp_password": "FAKEFakePassword1", "url": "%s"}' % real).encode()
        hits = self.scan([("mixed.json", raw)])
        self.assertEqual(len(hits), 1, f"同一文件里的真密钥应仍被抓到：{hits}")
        self.assertIn("钉钉", hits[0])

    def test_fake_marker_does_exempt_its_own_match(self):
        raw = ('{"smtp_password": "' + "FAKEFakePassword1" + '"}').encode()
        self.assertTrue(self.match(raw.decode()), "模式本身应该命中")
        self.assertEqual(self.scan([("fake.json", raw)]), [], "标了 FAKE 就该放行")

    def test_scanner_skips_binary_and_unknown_suffixes(self):
        # 只扫文本文件；这不是漏洞（打包清单本身也只收这些后缀），
        # 但行为要固定下来，免得有人以为它什么都能扫。
        self.assertFalse(
            self.hits(("access_token=" + "abc123def456ghi789jkl012").encode(),
                      "x.exe"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
