"""控制台输出兜底 —— 让中文 Windows（GBK/cp936）下不会因为一个符号崩掉。

## 为什么需要这个

实测（Windows，`PYTHONIOENCODING=gbk`）：

    >>> print("[✓] 引导: 成功")
    UnicodeEncodeError: 'gbk' codec can't encode character '\\u2713'

`✓` `✗` 这些符号**不在 GBK 里**。而 Python 在 Windows 上分两种情况：

  * **真控制台**：用宽字符 API（`WriteConsoleW`）写出，不受代码页影响 → 没问题
  * **重定向到文件/管道**：按 `locale.getpreferredencoding()`（中文 Windows 是 cp936）
    编码，且默认 `errors='strict'` → **直接抛异常，整个程序中断**

第二种情况非常常见 —— 用户为了把输出发给我看，最自然的动作就是

    python tools\\env_check.py > log.txt

结果自检还没跑完就崩在一个装饰符号上，而且报错信息（`UnicodeEncodeError`）
完全看不出真正原因。**这是本工具最容易被踩到、又最不像自己问题的坑。**

## 做法

* 重定向且当前不是 UTF-8 时 → 切到 UTF-8（内容是完整可读的，粘到哪都对）
* 其余情况 → 只把 `errors` 放宽成 `replace`，保证**永不因编码中断**
* 控制台本来就是好的，不折腾

`PYTHONIOENCODING` 由用户显式设置时，不覆盖编码选择，只放宽 errors ——
但**只在解释器真的认这个变量时**才这么算：冻结出来的 exe 会忽略它，
详见 `_pythonioencoding_is_honoured()`。
"""

from __future__ import annotations

import os
import sys
from typing import Any, Optional

# 这些符号在 GBK 里不存在，是崩掉输出的元凶
FRAGILE_SYMBOLS = ("✓", "✗", "⚠", "→", "←", "·", "—")


def _encoding_of(stream: Any) -> str:
    return (getattr(stream, "encoding", None) or "").lower().replace("-", "")


def _is_utf8(stream: Any) -> bool:
    return _encoding_of(stream) in ("utf8", "utf8mb4", "cp65001")


def _is_tty(stream: Any) -> bool:
    isatty = getattr(stream, "isatty", None)
    try:
        return bool(isatty()) if callable(isatty) else False
    except (ValueError, OSError):
        return False


def _pythonioencoding_is_honoured() -> bool:
    """`PYTHONIOENCODING` 在当前解释器里是否真的生效。

    ⚠️ **冻结出来的 exe（PyInstaller）完全忽略这个变量。** 实测（同一个入口，
    重定向到文件后看落盘字节）：

        | PYTHONIOENCODING | 冻结 exe | 非冻结 py38（对照） |
        |---|---|---|
        | 未设     | utf-8 | utf-8 |
        | utf-8    | **gbk** | utf-8 |
        | gbk      | gbk   | gbk   |
        | latin-1  | **gbk** | 生效（输出被 latin-1 搅乱） |

    为什么这条必须单独判：不判的话会出现**最坏的组合** ——
    用户设了 `PYTHONIOENCODING=utf-8` 以为万事大吉，实际拿到 cp936；
    而本模块又因为"这个变量存在"而放弃把编码切成 UTF-8。
    两头都指望对方，结果谁都没管。
    """
    return not getattr(sys, "frozen", False)


def _user_chose_encoding() -> bool:
    """用户是不是**真的**指定了输出编码（变量存在 **且** 解释器认它）。"""
    if not os.environ.get("PYTHONIOENCODING"):
        return False
    return _pythonioencoding_is_honoured()


def _relax(stream: Any, *, prefer_utf8: bool) -> Optional[str]:
    """把流的编码/错误策略放宽。返回生效后的编码；动不了就返回 None。"""
    reconfigure = getattr(stream, "reconfigure", None)
    if not callable(reconfigure):
        return None                       # Python 3.6 及更早的替代流，够不着就算了

    kwargs: dict = {"errors": "replace"}
    # 用户显式指定了 PYTHONIOENCODING（且解释器认它）就尊重它，只放宽 errors
    if prefer_utf8 and not _is_utf8(stream) and not _user_chose_encoding():
        kwargs["encoding"] = "utf-8"

    try:
        reconfigure(**kwargs)
    except (ValueError, OSError, LookupError):
        # encoding 换不了（比如流已经被读过）时，至少把 errors 放宽
        try:
            reconfigure(errors="replace")
        except (ValueError, OSError, LookupError):
            return None
    return _encoding_of(stream)


def ensure_safe_stdout() -> dict:
    """入口处调用一次。保证中文和符号不会把输出打断。

    返回实际生效的编码，便于调用方在必要时提示用户。
    """
    out = {}
    for name, stream in (("stdout", sys.stdout), ("stderr", sys.stderr)):
        if stream is None:
            continue
        # 真控制台本来就能正确输出 Unicode，不必动它
        actual = _relax(stream, prefer_utf8=not _is_tty(stream))
        out[name] = actual if actual is not None else _encoding_of(stream)
    return out


def stream_cannot_encode(stream: Any = None) -> list:
    """返回该流编码不了的脆弱符号（自检用，也可用于降级渲染）。"""
    stream = stream if stream is not None else sys.stdout
    enc = _encoding_of(stream) or "ascii"
    bad = []
    for ch in FRAGILE_SYMBOLS:
        try:
            ch.encode(enc)
        except (UnicodeEncodeError, LookupError):
            bad.append(ch)
    return bad
