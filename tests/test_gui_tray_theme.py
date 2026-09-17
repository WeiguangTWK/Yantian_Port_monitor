"""系统主题检测的离线测试，不导入 Qt 或读取真实注册表。"""

import pathlib
import sys
import unittest
from unittest.mock import MagicMock, Mock, patch

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
from gui import tray_theme


class TestTrayTheme(unittest.TestCase):
    def test_win7_does_not_read_registry(self):
        registry = MagicMock()
        with patch.object(sys, 'platform', 'win32'), patch.object(sys, 'getwindowsversion',
                return_value=Mock(major=6, minor=1), create=True), patch.dict(sys.modules, {'winreg': registry}):
            self.assertIsNone(tray_theme.taskbar_dark())
        registry.OpenKey.assert_not_called()

    def test_reads_system_not_application_theme(self):
        for value, expected in ((0, True), (1, False)):
            registry = MagicMock()
            registry.REG_DWORD = 4
            registry.QueryValueEx.return_value = (value, 4)
            with patch.object(tray_theme, 'supports_system_theme', return_value=True), patch.dict(sys.modules, {'winreg': registry}):
                self.assertIs(tray_theme.taskbar_dark(), expected)
            self.assertEqual(registry.QueryValueEx.call_args.args[1], 'SystemUsesLightTheme')

    def test_unreadable_registry_falls_back(self):
        registry = MagicMock()
        registry.OpenKey.side_effect = OSError('missing')
        with patch.object(tray_theme, 'supports_system_theme', return_value=True), patch.dict(sys.modules, {'winreg': registry}):
            self.assertIsNone(tray_theme.taskbar_dark())

    def test_non_windows_is_unknown(self):
        with patch.object(sys, 'platform', 'linux'):
            self.assertIsNone(tray_theme.taskbar_dark())
