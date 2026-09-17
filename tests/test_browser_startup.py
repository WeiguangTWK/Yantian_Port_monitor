"""浏览器数据目录的离线回归测试，不启动浏览器。"""

import os
import pathlib
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
from ytmon import cdp


class TestBrowserProfilePath(unittest.TestCase):
    def test_relative_profile_is_absolute_before_and_after_creation(self):
        previous_cwd = pathlib.Path.cwd()
        with tempfile.TemporaryDirectory() as directory:
            try:
                os.chdir(directory)
                profile = pathlib.Path('.browser_profile')
                expected = str(pathlib.Path.cwd() / profile)
                with patch.object(cdp, 'find_browser', return_value='msedge.exe'):
                    self.assertFalse(profile.exists())
                    first = cdp.Browser(profile_dir=str(profile), port=9411)
                    self.assertFalse(profile.exists())
                    self.assertTrue(pathlib.Path(first.profile_dir).is_absolute())
                    self.assertEqual(first.profile_dir, expected)
                    for flag in ('--headless=new', '--headless', None):
                        self.assertIn('--user-data-dir=' + expected, first._build_args(flag))

                    profile.mkdir()
                    second = cdp.Browser(profile_dir=str(profile), port=9411)
                    self.assertEqual(second.profile_dir, first.profile_dir)
            finally:
                os.chdir(previous_cwd)

    def test_absolute_missing_profile_is_preserved(self):
        with tempfile.TemporaryDirectory() as directory:
            profile = pathlib.Path(directory).absolute().resolve() / 'profile'
            with patch.object(cdp, 'find_browser', return_value='msedge.exe'):
                browser = cdp.Browser(profile_dir=str(profile), port=9411)
            self.assertEqual(browser.profile_dir, str(profile))
            self.assertFalse(profile.exists())


if __name__ == '__main__':
    unittest.main()
