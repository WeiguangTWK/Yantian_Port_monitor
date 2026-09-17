"""托盘生命周期的静态检查，不导入 GUI。"""

import ast
import pathlib
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent


class TestTrayLifecycle(unittest.TestCase):
    def test_hide_branch_does_not_stop_monitoring(self):
        tree = ast.parse((ROOT / 'gui/app.py').read_text('utf-8'))
        window = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == 'MainWindow')
        close = next(node for node in window.body if isinstance(node, ast.FunctionDef) and node.name == 'closeEvent')
        branch = close.body[0]
        self.assertIsInstance(branch, ast.If)
        calls = [node.func.attr for node in ast.walk(branch)
                 if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)]
        self.assertIn('hide', calls)
        self.assertIn('isSystemTrayAvailable', calls)
        self.assertNotIn('_stop_watch', calls)
        self.assertIsInstance(branch.body[-1], ast.Return)

    def test_explicit_exit_and_restore_are_available(self):
        source = (ROOT / 'gui/app.py').read_text('utf-8')
        self.assertIn("addAction('打开窗口', self._restore_window)", source)
        self.assertIn("addAction('退出程序', self._request_exit)", source)
        self.assertIn('setQuitOnLastWindowClosed(False)', source)
        self.assertIn('QApplication.instance().quit()', source)
