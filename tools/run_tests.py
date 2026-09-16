"""跑测试套件，并在受限环境（沙箱）下自动绕开 `tempfile.mkdtemp()` 的坑。

## 为什么需要这个

正常情况下直接 `python -m unittest discover -s tests` 就行。但在 DSH 沙箱
（只有工作区可写）**加上 Python 3.13+/3.14** 时，`tempfile.mkdtemp()` 建出来的目录不可写：

    tempfile.mkdtemp() 建目录  → 往里写文件 → Permission denied
    同一位置用 os.makedirs 建  → 写入正常

⚠️ 这**不是**"沙箱一律如此"：**Python 3.8.10 下同一台机器上完全正常**。
是 3.13+ 起 `tempfile` 建目录的方式变了才撞上沙箱（实测对照见 OV `dead-ends.md` E1）。

后果是套件会稳定报出**一批假失败**（写盘再重读的用例全部失败、
清理阶段 chmod 被拒变成 ERROR），看起来像代码回归。

这个脚本的做法是**先探测再决定**：

1. 真去 mkdtemp 一个目录、写一个文件、删掉；
2. 写得进 → 什么都不做，直接跑测试（正常环境行为完全不变）；
3. 写不进 → 只在本进程内把 `tempfile.mkdtemp` 换成"用 `os.makedirs` 建目录"的版本，
   **并打印一行提示**，然后跑测试。

这样"绕过了"这件事永远是显式的，不会静默改变测试语义。

用法：
    python tools/run_tests.py            # 跑全部
    python tools/run_tests.py -v         # 其余参数原样转给 unittest
    .toolchain\\py38\\python.exe tools\\run_tests.py    # 用交付运行时（3.8）跑
"""

from __future__ import annotations

import os
import pathlib
import secrets
import shutil
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
TESTS = ROOT / "tests"


def _mkdtemp_is_writable() -> "tuple[bool, str]":
    """真建一个目录、写一个文件、删掉。返回 (能不能写, 说明)。"""
    try:
        d = tempfile.mkdtemp(prefix="ytmon-runtests-probe-")
    except OSError as e:                                        # noqa: PERF203
        return False, "建目录就失败：%s" % e

    try:
        with open(os.path.join(d, "probe.txt"), "w") as fh:
            fh.write("x")
        return True, "正常"
    except OSError as e:
        return False, "目录建得出来但写不进：%s" % e
    finally:
        shutil.rmtree(d, ignore_errors=True)


def _patch_mkdtemp() -> None:
    """把 mkdtemp 换成用 os.makedirs 建目录（不带 mkdtemp 的 0o700）。

    只动"怎么建目录"，其余语义（唯一名、返回路径、重名重试）保持一致。

    ⚠️ 这里刻意**不用** `tempfile._get_candidate_names` —— 它是私有 API，
    而且形态跨版本变过（3.14 里是函数，`next()` 它会报
    `TypeError: 'function' object is not an iterator`，本项目踩过）。
    用 `secrets` 自己生成唯一名，与解释器版本无关。
    """

    def mkdtemp(suffix="", prefix="temp", dir=None):           # noqa: A002
        base = dir or tempfile.gettempdir()
        for _ in range(1000):
            name = "%s%s%s" % (prefix, secrets.token_hex(8), suffix)
            path = os.path.join(base, name)
            try:
                os.makedirs(path)
                return path
            except FileExistsError:
                continue
        raise FileExistsError("建不出唯一临时目录：%s" % base)

    tempfile.mkdtemp = mkdtemp                                  # type: ignore[assignment]


def main(argv: "list[str]") -> int:
    ok, why = _mkdtemp_is_writable()
    if ok:
        print("[run_tests] tempfile.mkdtemp 可用（%s），不启用绕行。" % why)
    else:
        print("[run_tests] 检测到受限环境：%s" % why)
        print("[run_tests] 已在本进程内改用 os.makedirs 建临时目录（绕过沙箱限制）。")
        print("[run_tests] 注意：这是**环境绕行**，不是代码修好了 —— "
              "真正的代码回归仍会让用例失败。\n")
        _patch_mkdtemp()

    sys.path.insert(0, str(ROOT))
    loader = unittest.TestLoader()
    # 不加 top_level_dir：tests/ 没有 __init__.py，传了它反而会要求 tests 是个包，
    # 直接报 "Start directory is not importable"（本项目踩过两次）。
    suite = loader.discover(str(TESTS))

    runner_args = {"verbosity": 2 if "-v" in argv else 1}
    result = unittest.TextTestRunner(**runner_args).run(suite)
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
