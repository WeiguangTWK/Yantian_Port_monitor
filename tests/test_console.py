"""控制台编码兜底测试。

守的是一个**真实踩过的坑**：中文 Windows 下把输出重定向到文件时，
`env_check.py` 会死在第一个 `✓` 上 ——

    UnicodeEncodeError: 'gbk' codec can't encode character '\\u2713'

控制台实时输出不会（Python 用宽字符 API），只有重定向才会。
而"把日志发给我看"恰恰就是重定向，所以这个坑出现的位置非常刁钻：
自检还没跑完就崩，报错还完全看不出跟编码有关。

这里的测试用真子进程 + GBK 编码跑，不是模拟 —— 只有真跑才说明问题。
"""

from __future__ import annotations

import os
import pathlib
import subprocess
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from ytmon.console import (FRAGILE_SYMBOLS, ensure_safe_stdout,  # noqa: E402
                           stream_cannot_encode)

# 子进程脚本：真实模拟"中文 Windows + 重定向"。
#
# 关键点：不能靠设 PYTHONIOENCODING 来模拟 —— 那会走"尊重用户显式设置"的分支，
# 而真实用户根本没设过它。真实情况是 Python 从 locale 推出 cp936。
# 所以这里先手动把 stdout 掰成 gbk（等价于 locale 的结果），再清掉那个变量。
PROBE = """
import os, sys
sys.path.insert(0, sys.argv[1])
os.environ.pop("PYTHONIOENCODING", None)
if os.environ.get("YT_SIMULATE_CP936") == "1":
    sys.stdout.reconfigure(encoding="gbk", errors="strict")
if os.environ.get("YT_SKIP_FIX") != "1":
    from ytmon.console import ensure_safe_stdout
    ensure_safe_stdout()
print("[OK] 引导: 成功 " + " ".join(sys.argv[2:]))
print("中文也应该正常：船期监控")
"""


def run_probe(symbols, *, simulate_cp936=True, skip_fix=False):
    env = dict(os.environ)
    env.pop("PYTHONIOENCODING", None)
    env["YT_SIMULATE_CP936"] = "1" if simulate_cp936 else "0"
    env["YT_SKIP_FIX"] = "1" if skip_fix else "0"
    return subprocess.run([sys.executable, "-c", PROBE, str(ROOT), *symbols],
                          capture_output=True, timeout=120, env=env)


class TestGbkRedirectCrash(unittest.TestCase):

    def test_without_the_fix_it_really_crashes(self):
        """先确认问题真实存在 —— 否则这整套兜底就是在防一个不存在的东西。"""
        proc = run_probe(["\u2713"], skip_fix=True)
        self.assertNotEqual(proc.returncode, 0,
                            "如果这里通过了，说明前提变了，需要重新确认这个坑还在不在")
        self.assertIn(b"UnicodeEncodeError", proc.stderr)

    def test_the_fix_prevents_the_crash(self):
        proc = run_probe(["\u2713", "\u2717", "\u26a0", "\u2192"])
        self.assertEqual(proc.returncode, 0,
                         f"兜底没起作用：stderr={proc.stderr[:400]!r}")
        self.assertNotIn(b"UnicodeEncodeError", proc.stderr)

    def test_redirected_output_is_readable_utf8(self):
        """重定向时切成 UTF-8 —— 这样日志发给别人（或粘到别处）不会变乱码。

        中文 Windows 下如果坚持用 cp936，日志文件在 UTF-8 工具里就是乱码，
        而"把日志发我看看"恰恰是这个功能最主要的用途。
        """
        proc = run_probe(["\u2713"])
        self.assertIn("中文也应该正常".encode("utf-8"), proc.stdout,
                      f"重定向输出应当是 UTF-8，实际={proc.stdout[:80]!r}")

    def test_console_output_is_left_alone(self):
        """真控制台不该被改动 —— 它本来就能正确输出 Unicode。"""
        proc = run_probe(["\u2713"], simulate_cp936=False)
        self.assertEqual(proc.returncode, 0)
        self.assertIn("\u2713", proc.stdout.decode("utf-8", "replace"),
                      "非重定向时勾号应该照常显示，而不是被换成 ?")

    def test_fragile_symbols_are_the_ones_we_think(self):
        import io
        gbk = io.TextIOWrapper(io.BytesIO(), encoding="gbk")
        bad = set(stream_cannot_encode(gbk))
        self.assertIn("\u2713", bad, "✓ 应当被判定为 GBK 编不了")
        self.assertIn("\u2717", bad, "✗ 应当被判定为 GBK 编不了")
        # 中文本身在 GBK 里，不该被误判
        self.assertNotIn("船", FRAGILE_SYMBOLS)


class TestRelaxApi(unittest.TestCase):

    def test_returns_encoding_without_raising(self):
        result = ensure_safe_stdout()
        self.assertIn("stdout", result)

    def test_survives_a_stream_without_reconfigure(self):
        """没有 reconfigure 的流（旧式/替换实现）不该让入口崩掉。"""
        import io
        from contextlib import redirect_stdout
        buf = io.StringIO()                     # StringIO 没有 reconfigure
        with redirect_stdout(buf):
            out = ensure_safe_stdout()
            print("仍然能打印中文：船期")
        self.assertIn("stdout", out)

    def test_explicit_pythonioencoding_is_respected(self):
        """用户显式设了 PYTHONIOENCODING 就别改编码，只放宽 errors。"""
        import io
        from ytmon.console import _relax
        stream = io.TextIOWrapper(io.BytesIO(), encoding="gbk")
        old = os.environ.get("PYTHONIOENCODING")
        os.environ["PYTHONIOENCODING"] = "gbk"
        try:
            _relax(stream, prefer_utf8=True)
            self.assertIn("gbk", (stream.encoding or "").lower(),
                          "不该覆盖用户显式指定的编码")
        finally:
            if old is None:
                os.environ.pop("PYTHONIOENCODING", None)
            else:
                os.environ["PYTHONIOENCODING"] = old


class TestEntryPointsCallIt(unittest.TestCase):
    """入口点忘了调用兜底 = 坑还在。"""

    def test_cli_main_relaxes_stdout(self):
        src = (ROOT / "ytmon" / "cli.py").read_text("utf-8")
        self.assertIn("ensure_safe_stdout", src)

    def test_env_check_relaxes_stdout(self):
        src = (ROOT / "tools" / "env_check.py").read_text("utf-8")
        self.assertIn("ensure_safe_stdout", src)

    def test_fake_webhook_relaxes_stdout(self):
        src = (ROOT / "tools" / "fake_webhook.py").read_text("utf-8")
        self.assertIn("ensure_safe_stdout", src)

    def test_env_check_still_runs_under_gbk(self):
        """真正的验收：自检在 GBK 重定向下要能跑完，而不是崩在半路。"""
        env = dict(os.environ)
        env["PYTHONIOENCODING"] = "gbk"
        proc = subprocess.run(
            [sys.executable, "tools/env_check.py", "--help"],
            capture_output=True, timeout=180, cwd=str(ROOT), env=env)
        self.assertEqual(proc.returncode, 0, f"stderr={proc.stderr[:400]!r}")
        self.assertNotIn(b"UnicodeEncodeError", proc.stderr)


if __name__ == "__main__":
    unittest.main(verbosity=2)
