"""Linux 桌面通知的离线测试，不连接桌面会话。"""

import pathlib
import subprocess
import sys
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from ytmon.config import NotifyChannel, Settings
from ytmon.notify import Notifier, send_linux, test_message as make_test_message


class TestLinuxNotifications(unittest.TestCase):
    def test_notify_send_arguments(self):
        channel = NotifyChannel(kind='linux', hold_seconds=2.5)
        with patch('sys.platform', 'linux'), patch('shutil.which', return_value='/usr/bin/notify-send'), patch(
                'subprocess.run', return_value=Mock(returncode=0)) as run:
            send_linux(channel, make_test_message())
        args = run.call_args.args[0]
        self.assertEqual(args[:6], ['/usr/bin/notify-send', '-a', '盐田船期监控', '-t', '2500', '--'])
        self.assertIn('测试消息', args[6])
        self.assertEqual(run.call_args.kwargs['timeout'], 10)

    def test_wrong_platform_or_missing_command_reports_failure(self):
        channel = NotifyChannel(kind='linux')
        with patch('sys.platform', 'win32'):
            with self.assertRaisesRegex(RuntimeError, 'Linux'):
                send_linux(channel, make_test_message())
        with patch('sys.platform', 'linux'), patch('shutil.which', return_value=None):
            with self.assertRaisesRegex(RuntimeError, 'notify-send'):
                send_linux(channel, make_test_message())

    def test_send_failure_does_not_stop_other_channels(self):
        channels = [NotifyChannel(kind='linux'), NotifyChannel(kind='webhook', url='https://example.invalid/hook')]
        notifier = Notifier(channels, Settings(), transport=lambda url, payload: '')
        with patch('ytmon.notify.send_linux', side_effect=subprocess.TimeoutExpired('notify-send', 10)):
            results = notifier.send(make_test_message())
        self.assertFalse(results[0].ok)
        self.assertTrue(results[1].ok)

    def test_nonzero_exit_is_reported(self):
        with patch('sys.platform', 'linux'), patch('shutil.which', return_value='/usr/bin/notify-send'), patch(
                'subprocess.run', return_value=Mock(returncode=1, stderr='No session bus')):
            with self.assertRaisesRegex(RuntimeError, 'No session bus'):
                send_linux(NotifyChannel(kind='linux'), make_test_message())
