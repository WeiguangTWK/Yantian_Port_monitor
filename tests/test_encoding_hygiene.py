"""守住"文本读写必须显式指定编码"这条纪律。

## 为什么值得单独一个测试文件

Windows 上 `locale.getpreferredencoding()` 是 **cp936**（中文）/ cp1252（英文），
而项目里所有文本文件都是 **UTF-8**。于是任何一处漏写 `encoding=` 的文本读写，
都会在中文 Windows 上变成一个跟"编码"看起来毫不相干的故障：

* 读配置 -> `UnicodeDecodeError: 'gbk' codec can't decode byte ...`
* 写状态 -> 中文被写成乱码，或者直接抛异常中断整轮监控

而开发机（以及所有 CI）很可能正好是 UTF-8 环境，**根本看不到**。
所以这条只能靠静态检查守住 —— 这里用 AST 查，不靠人记得。

> 真事：本项目的 `requirements-win7.txt` 就栽在这上面 —— pip 用
> `locale.getpreferredencoding()` 解码 requirements 文件，
> 文件是 UTF-8 带中文注释且无 BOM -> 中文 Windows 上
> `pip install -r requirements-win7.txt` 直接报 `UnicodeDecodeError`。
> 而 Win7 指南让现场执行的就是这条命令。
"""

from __future__ import annotations

import ast
import pathlib
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

SCAN_DIRS = ("ytmon", "tools", "gui", "tests")
SCAN_FILES = ("run_monitor.py",)

# 位置参数里的 encoding 下标：
#   open(file, mode, buffering, encoding, ...) -> 3
#   Path.read_text(encoding, errors)           -> 0
#   Path.write_text(data, encoding, errors)    -> 1
#
# ⚠️ 一定要算上位置参数。第一版审计只看关键字参数，把
# `p.read_text("utf-8")` 当成"漏了编码"，一次报出 42 个假阳性 ——
# 差点据此去改一堆无辜代码。
POSITIONAL_ENCODING = {"open": 3, "read_text": 0, "write_text": 1}


def _arg(node: ast.Call, idx: int):
    return node.args[idx] if len(node.args) > idx else None


def _kw(node: ast.Call, name: str):
    for k in node.keywords:
        if k.arg == name:
            return k.value
    return None


def _is_binary(node: ast.Call) -> bool:
    mode = _kw(node, "mode") or _arg(node, 1)
    return isinstance(mode, ast.Constant) and "b" in (mode.value or "")


def scan_file(path: pathlib.Path) -> "list[tuple[int, str]]":
    """返回 [(行号, 说明)]，空列表 = 这个文件干净。"""
    found = []
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        name = getattr(func, "id", None) or getattr(func, "attr", None)
        if name not in POSITIONAL_ENCODING:
            continue
        if name == "open" and _is_binary(node):
            continue
        given = _kw(node, "encoding") or _arg(node, POSITIONAL_ENCODING[name])
        if given is None:
            found.append((node.lineno, "%s() 没给 encoding" % name))
        elif not (isinstance(given, ast.Constant) and given.value):
            found.append((node.lineno, "%s() 的 encoding 不是非空常量" % name))
    return found


class TestTextIODeclaresEncoding(unittest.TestCase):

    def test_scanned_files_exist(self):
        """先确认扫描面确实有东西 —— 空扫描集的"全绿"是假绿。"""
        files = self._py_files()
        self.assertGreater(len(files), 20, "扫描到的文件太少，规则形同虚设")

    def _py_files(self):
        out = []
        for d in SCAN_DIRS:
            out += sorted((ROOT / d).rglob("*.py"))
        out += [ROOT / f for f in SCAN_FILES if (ROOT / f).is_file()]
        return [p for p in out if "__pycache__" not in str(p)]

    def test_no_text_io_without_explicit_encoding(self):
        offenders = []
        for p in self._py_files():
            for line, what in scan_file(p):
                offenders.append("%s:%d %s" % (p.relative_to(ROOT), line, what))
        self.assertEqual(
            offenders, [],
            "这些文本读写漏了 encoding，中文 Windows 上会崩或写出乱码：\n  "
            + "\n  ".join(offenders))


class TestRequirementsAreReadableUnderAnyLocale(unittest.TestCase):
    """pip 用 `locale.getpreferredencoding()` 解码 requirements 文件。

    没有 BOM 时，中文 Windows 会按 cp936 解，UTF-8 的中文字节必然解不开
    （pip 的 `auto_decode` 只有在有 BOM 或纯 ASCII 时才安全）。
    所以规则是：**要么纯 ASCII，要么带 UTF-8 BOM**，没有第三种选择。
    """

    def requirements_files(self):
        return sorted(ROOT.glob("requirements*.txt"))

    def test_there_are_requirements_files(self):
        self.assertTrue(self.requirements_files())

    def test_each_is_pure_ascii_or_has_a_utf8_bom(self):
        bad = []
        for p in self.requirements_files():
            data = p.read_bytes()
            if data.startswith(b"\xef\xbb\xbf"):
                continue
            non_ascii = [b for b in data if b > 127]
            if non_ascii:
                bad.append("%s（%d 个非 ASCII 字节，且无 BOM）" % (p.name, len(non_ascii)))
        self.assertEqual(
            bad, [],
            "这些 requirements 文件在没有 BOM 时含非 ASCII，"
            "pip 在中文 Windows 上会报 UnicodeDecodeError：\n  " + "\n  ".join(bad))


if __name__ == "__main__":
    unittest.main()
