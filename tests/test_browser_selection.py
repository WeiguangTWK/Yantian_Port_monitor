"""浏览器路径选择的离线测试，不启动浏览器。"""

import pathlib
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
from ytmon import browser_find


class TestBrowserSelection(unittest.TestCase):
    def test_os_priority_overrides_other_brand_version(self):
        edge = 'C:/Microsoft/EdgeCore/999.0/msedge.exe'
        supermium = 'C:/Program Files/Supermium/chrome.exe'
        self.assertEqual(browser_find.select_browser([edge, supermium], True), supermium)
        self.assertEqual(browser_find.select_browser([edge, supermium], False), edge)

    def test_other_preferred_brand_and_legacy_fallback(self):
        edge = 'C:/Edge/Application/msedge.exe'
        chromium = 'C:/Chromium/Application/chrome.exe'
        self.assertEqual(browser_find.select_browser([edge], True), edge)
        self.assertEqual(browser_find.select_browser([chromium], False), chromium)

    def test_version_comparison_within_brand(self):
        paths = ['C:/EdgeCore/109.0/msedge.exe', 'C:/EdgeCore/153.0/msedge.exe']
        self.assertEqual(browser_find.select_browser(paths, False), paths[1])

    def test_supermium_chrome_executable_is_in_candidates(self):
        self.assertIn((r'C:\Program Files\Supermium', 'chrome.exe'), browser_find.CANDIDATES)
        self.assertEqual(browser_find.browser_brand(r'C:\Program Files\Supermium\chrome.exe'), 'Supermium')

    def test_machine_install_roots_use_environment(self):
        with patch.dict('os.environ', {'ProgramW6432': r'D:\Apps', 'ProgramFiles': r'D:\Apps',
                                      'ProgramFiles(x86)': r'D:\Apps32'}, clear=True):
            with patch.object(browser_find, '_expand', return_value=[]) as expand:
                browser_find.search_roots()
        roots = [call.args[0] for call in expand.call_args_list]
        self.assertIn(str(pathlib.Path(r'D:\Apps') / r'Supermium'), roots)
