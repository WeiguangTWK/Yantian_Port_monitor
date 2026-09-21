"""绑定选择在导入原生扩展前完成；不依赖测试机安装两套 Qt。"""
import builtins
import pathlib
import runpy
import sys
import types
import unittest
from unittest import mock


MODULE = pathlib.Path(__file__).resolve().parent.parent / 'gui' / 'qt_compat.py'


def fake_binding(name):
    package = types.ModuleType(name)
    core = types.ModuleType(name + '.QtCore')
    core.qVersion = lambda: 'test-qt'
    core.QThread = type('QThread', (), {})
    core.Signal = core.Slot = lambda *args: None
    package.QtCore = core
    package.QtGui = types.ModuleType(name + '.QtGui')
    package.QtWidgets = types.ModuleType(name + '.QtWidgets')
    package.QtWidgets.QAbstractItemView = types.SimpleNamespace(NoEditTriggers=0)
    return package, core


class TestQtBindingSelection(unittest.TestCase):
    def run_binding(self, platform, forbidden):
        side2, core2 = fake_binding('PySide2')
        side6, core6 = fake_binding('PySide6')
        original_import = builtins.__import__

        def guarded_import(name, *args, **kwargs):
            if name.startswith(forbidden):
                raise AssertionError('不应导入 ' + forbidden)
            return original_import(name, *args, **kwargs)

        with mock.patch.object(sys, 'platform', platform), \
                mock.patch.dict(sys.modules, {'PySide2': side2, 'PySide2.QtCore': core2,
                                              'PySide6': side6, 'PySide6.QtCore': core6}), \
                mock.patch('builtins.__import__', side_effect=guarded_import):
            return runpy.run_path(str(MODULE))

    def test_linux_selects_pyside6_without_touching_pyside2(self):
        result = self.run_binding('linux', 'PySide2')
        self.assertEqual((result['BINDING'], result['QT_VERSION']), ('PySide6', 'test-qt'))

    def test_windows_keeps_pyside2_priority(self):
        result = self.run_binding('win32', 'PySide6')
        self.assertEqual((result['BINDING'], result['QT_VERSION']), ('PySide2', 'test-qt'))

