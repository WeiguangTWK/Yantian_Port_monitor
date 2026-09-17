"""commit 版本的离线测试，不启动 GUI。"""

import pathlib
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
from ytmon import version


class TestRepoVersion(unittest.TestCase):
    def test_head_is_truncated_to_exactly_eight(self):
        with tempfile.TemporaryDirectory() as directory:
            (pathlib.Path(directory) / '.git').mkdir()
            with patch('ytmon.version.subprocess.run', return_value=Mock(
                    returncode=0, stdout=b'abcdef1234567890abcdef1234567890abcdef1234\n')):
                self.assertEqual(version.repo_commit(directory), 'abcdef12')

    def test_no_repository_does_not_use_parent_head(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch('ytmon.version.subprocess.run') as run:
                self.assertIsNone(version.repo_commit(directory))
                run.assert_not_called()

    def test_frozen_reads_stamp_without_git(self):
        with tempfile.TemporaryDirectory() as directory:
            (pathlib.Path(directory) / version.VERSION_FILE).write_text('abcdef12\n', 'ascii')
            with patch.object(sys, 'frozen', True, create=True), patch.object(sys, '_MEIPASS', directory, create=True):
                with patch('ytmon.version.subprocess.run') as run:
                    self.assertEqual(version.display_version(), 'abcdef12')
                    run.assert_not_called()
