"""浮窗分发的离线测试，不导入 Qt 或调用系统通知。"""

import pathlib
import sys
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from gui.notification_transport import dispatch_windows
from ytmon.config import NotifyChannel
from ytmon.notify import send_windows, test_message as make_test_message


class TestNotificationTransport(unittest.TestCase):
    def test_persistent_only_emits_message(self):
        message = make_test_message()
        emit = Mock()
        with patch('gui.notification_transport.send_windows') as native:
            dispatch_windows(NotifyChannel(kind='windows', persistent=True), message, emit)
        native.assert_not_called()
        emit.assert_called_once_with(message.title, message.text)

    def test_default_uses_native(self):
        channel = NotifyChannel(kind='windows', hold_seconds=12)
        message = make_test_message()
        emit = Mock()
        with patch('gui.notification_transport.send_windows') as native:
            dispatch_windows(channel, message, emit)
        native.assert_called_once_with(channel, message)
        emit.assert_not_called()

    def test_cli_rejects_persistent_before_native_import(self):
        with self.assertRaisesRegex(RuntimeError, '仅支持 GUI'):
            send_windows(NotifyChannel(kind='windows', persistent=True), make_test_message())
