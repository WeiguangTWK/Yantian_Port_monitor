"""自检流程的回归测试。

守的是一个**真实踩过的坑**，而且它非常隐蔽：

    第 7 步用 --browser 指定了 Supermium，第 8 步（引导）也用的是它，
    但第 9 步（查询）**没有把这条路径传下去** —— 于是查询那步在需要
    重新引导时退化成"自动探测浏览器"，在 Win7 上（Supermium 装在
    非标准目录）必然失败，报出：

        [✗] 查询: FileNotFoundError: 未找到任何 Chromium 系浏览器

    用户看到的是"第 7 步明明通过了，查询却说找不到浏览器"，
    完全对不上。根因有两层，缺一不可：
      a) check_query 没接 browser 参数
      b) 第 8 步直接调 _bootstrap_async，**没写 cookie 缓存**，
         逼着第 9 步必须重新引导

    开发机上 Edge 能被自动探测到，所以这个 bug 一直没暴露。

这里的测试用**假浏览器脚本**跑真实流程，不联网、不装东西。
"""

from __future__ import annotations

import contextlib
import io
import os
import pathlib
import stat
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

import env_check                                              # noqa: E402
from ytmon.browser_find import (PROBE_FAIL, PROBE_OK,         # noqa: E402
                                PROBE_UNKNOWN, find_browser, probe_browser)


class QuietReportTest(unittest.TestCase):
    """`Report.add` 会往 stdout 打印，测试里把它收掉，免得刷屏。"""

    def setUp(self):
        self._buf = io.StringIO()
        self._ctx = contextlib.redirect_stdout(self._buf)
        self._ctx.__enter__()

    def tearDown(self):
        self._ctx.__exit__(None, None, None)

    @property
    def printed(self) -> str:
        return self._buf.getvalue()


def write_fake_exe(path: pathlib.Path, body: str) -> pathlib.Path:
    """造一个可执行的假文件，用来验证"路径存在 != 能启动"这类判断。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, "utf-8")
    if os.name != "nt":
        path.chmod(path.stat().st_mode | stat.S_IEXEC)
    return path


class TestProbeBrowser(unittest.TestCase):
    """路径存在 != 能启动。这组测试就是把这句口号变成断言。"""

    def setUp(self):
        self.dir = pathlib.Path(tempfile.mkdtemp(prefix="ytmon-probe-"))

    def test_missing_file(self):
        result, detail = probe_browser(str(self.dir / "nope.exe"))
        self.assertEqual(result, PROBE_FAIL)
        self.assertIn("不存在", detail)

    def test_directory_is_not_a_browser(self):
        result, detail = probe_browser(str(self.dir))
        self.assertEqual(result, PROBE_FAIL)
        self.assertIn("目录", detail)

    def test_real_browser_is_recognised(self):
        """真的启一个浏览器 —— 这是唯一能证明探测方式正确的方法。

        这条同时守住"不能用 --version"这个教训：本机实测
        `msedge.exe --version` 会挂死，而 `--headless --dump-dom about:blank`
        3.7 秒就返回。如果有人把命令改回 --version，这条会超时失败。
        """
        from ytmon.browser_find import search_roots
        found = search_roots()
        if not found:
            self.skipTest("这台机器上没有可自动探测到的浏览器")
        exe = max(found, key=lambda p: __import__(
            "ytmon.browser_find", fromlist=["version_key"]).version_key(p))
        result, detail = probe_browser(exe, timeout=60)
        self.assertEqual(result, PROBE_OK,
                         f"探测真实浏览器失败：{detail}\n（若超时，说明探测命令不可靠）")

    def test_file_that_is_not_an_executable(self):
        junk = self.dir / "not-an-exe.exe"
        junk.write_text("这不是可执行程序", "utf-8")
        result, _ = probe_browser(str(junk))
        self.assertNotEqual(result, PROBE_OK,
                            "不该把随便一个文件判成可用浏览器")

    def test_missing_dll_hint_is_actionable(self):
        """WinError 2 那种错义必须被翻译成人能懂的话。

        这里不真去造一个缺 DLL 的 exe（不现实），而是直接验证
        probe_browser 在拿到 FileNotFoundError 时的措辞分支。
        用 mock 精确卡住这个分支，比造场景可靠。
        """
        from unittest.mock import patch
        exe = write_fake_exe(self.dir / "supermium.exe", "stub")

        def raiser(*a, **kw):
            raise FileNotFoundError(
                2, "系统找不到指定的文件。", str(exe), 2, None)

        with patch("subprocess.run", side_effect=raiser):
            result, detail = probe_browser(str(exe))

        self.assertEqual(result, PROBE_FAIL)
        self.assertIn("WinError 2", detail)
        # 必须明确告诉用户"这不是路径问题"，否则他会一直去折腾路径
        self.assertIn("运行时库", detail)
        self.assertIn("VC++", detail)

    def test_timeout_is_unknown_not_failure(self):
        """挂住 != 坏了。把不确定当失败会误杀能用的浏览器。"""
        from unittest.mock import patch
        from subprocess import TimeoutExpired
        exe = write_fake_exe(self.dir / "supermium.exe", "stub")

        with patch("subprocess.run", side_effect=TimeoutExpired("x", 1)):
            result, detail = probe_browser(str(exe))

        self.assertEqual(result, PROBE_UNKNOWN)
        self.assertIn("无法判定", detail)

    def test_probe_does_not_touch_the_users_profile(self):
        """必须带 --user-data-dir 指向临时目录，否则会污染用户真实配置。"""
        from unittest.mock import patch
        exe = write_fake_exe(self.dir / "supermium.exe", "stub")
        seen: dict = {}

        class Done:
            returncode = 0
            stdout = b"<html></html>"
            stderr = b""

        def fake_run(args, **kw):
            seen["args"] = args
            return Done()

        with patch("subprocess.run", side_effect=fake_run):
            result, _ = probe_browser(str(exe))

        self.assertEqual(result, PROBE_OK)
        joined = " ".join(seen["args"])
        self.assertIn("--user-data-dir=", joined,
                      "缺 --user-data-dir 会去动用户真实的浏览器配置")
        self.assertIn("--headless", joined)


class TestFindBrowserExplicit(unittest.TestCase):

    def setUp(self):
        self.dir = pathlib.Path(tempfile.mkdtemp(prefix="ytmon-find-"))

    def test_existing_file_is_returned(self):
        exe = write_fake_exe(self.dir / "supermium.exe", "stub")
        self.assertEqual(find_browser(str(exe)), str(exe))

    def test_directory_is_rejected_with_a_useful_message(self):
        """用户很可能把目录当成 exe 传进来 —— 要说清楚，并给出该填什么。"""
        d = self.dir / "Supermium"
        d.mkdir()
        with self.assertRaises(FileNotFoundError) as ctx:
            find_browser(str(d))
        msg = str(ctx.exception)
        self.assertIn("目录", msg)
        self.assertIn("supermium.exe", msg)

    def test_missing_path(self):
        with self.assertRaises(FileNotFoundError):
            find_browser(str(self.dir / "nope.exe"))


class TestEnvCheckConstants(unittest.TestCase):
    """第 8 步写、第 9 步读，两边必须指向同一个缓存文件。"""

    def test_cookie_cache_matches_library_default(self):
        from ytmon.http_client import DEFAULT_COOKIE_CACHE
        self.assertEqual(env_check.COOKIE_CACHE, DEFAULT_COOKIE_CACHE,
                         "env_check 的缓存路径和库里不一致，第 9 步会读不到")

    def test_profile_dir_matches_library_default(self):
        from ytmon.http_client import bootstrap_cookies
        import inspect
        default = inspect.signature(bootstrap_cookies).parameters["profile_dir"].default
        self.assertEqual(env_check.PROFILE_DIR, default)


class TestQueryGetsTheBrowserPath(QuietReportTest):
    """核心回归：第 9 步必须拿到第 7 步确认过的浏览器路径。"""

    def setUp(self):
        super().setUp()
        self.rep = env_check.Report()

    def _run_check_query(self, browser):
        import ytmon.http_client as real
        from unittest.mock import patch

        captured: dict = {}

        class FakeClient:
            def verify_token(self, **kw):
                return real.TokenStatus(True, "ok", "探针", 200, 1)

        class FakeHttpClient:
            @classmethod
            def create(cls, token="", **kwargs):
                captured.update(kwargs)
                return FakeClient()

        with patch.object(real, "HttpClient", FakeHttpClient):
            env_check.check_query(self.rep, "", browser)
        return captured

    def test_check_query_forwards_edge_path(self):
        captured = self._run_check_query(r"C:\Supermium\supermium.exe")
        self.assertEqual(captured.get("edge_path"), r"C:\Supermium\supermium.exe",
                         "第 9 步没把 --browser 传下去 —— 这正是那个 bug")
        self.assertEqual(captured.get("profile_dir"), env_check.PROFILE_DIR)
        self.assertEqual(captured.get("cache_file"), env_check.COOKIE_CACHE)

    def test_missing_browser_error_explains_the_fix(self):
        """找不到浏览器时的提示必须指向 --browser，而不是一句干巴巴的报错。"""
        import ytmon.http_client as real
        from unittest.mock import patch

        class FakeHttpClient:
            @classmethod
            def create(cls, *a, **kw):
                raise FileNotFoundError("未找到任何 Chromium 系浏览器")

        with patch.object(real, "HttpClient", FakeHttpClient):
            env_check.check_query(self.rep, "", None)

        row = self.rep.rows[-1]
        self.assertEqual(row[1], env_check.FAIL)
        self.assertIn("--browser", row[3])


class TestBootstrapCachesCookies(QuietReportTest):
    """第 8 步必须把 cookie 写进缓存，否则第 9 步会白起一次浏览器。"""

    def test_bootstrap_calls_bootstrap_cookies_not_raw_async(self):
        import ytmon.http_client as real
        from unittest.mock import patch

        called: dict = {}

        def fake_bootstrap(token="", **kw):
            called["token"] = token
            called.update(kw)
            return {"JSESSIONID": "x", "EO-Bot-Js-Token": "y"}

        rep = env_check.Report()
        with patch.object(real, "bootstrap_cookies", fake_bootstrap):
            ok = env_check.check_bootstrap(rep, r"C:\Supermium\supermium.exe", "")

        self.assertTrue(ok)
        self.assertEqual(called.get("edge_path"), r"C:\Supermium\supermium.exe",
                         "第 8 步没把浏览器路径带下去")
        self.assertEqual(called.get("cache_file"), env_check.COOKIE_CACHE,
                         "必须指定缓存文件，否则第 9 步读不到")

    def test_no_browser_skips_instead_of_crashing(self):
        rep = env_check.Report()
        self.assertFalse(env_check.check_bootstrap(rep, None, ""))
        self.assertEqual(rep.rows[-1][1], env_check.SKIP)


class TestCheckQueryIsWired(unittest.TestCase):
    """main() 里必须把 browser 传进 check_query —— 光改函数签名没用。"""

    def test_main_forwards_browser(self):
        src = (ROOT / "tools" / "env_check.py").read_text("utf-8")
        self.assertIn("check_query(rep, token, browser)", src,
                      "main 里没把 browser 传给 check_query，bug 会原样复现")


if __name__ == "__main__":
    unittest.main(verbosity=2)
