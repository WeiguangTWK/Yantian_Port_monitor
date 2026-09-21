"""通知配置与脱敏的离线测试，不导入 Qt 或发送消息。"""

import json
import pathlib
import sys
import tempfile
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from gui.notify_config import (NotificationConfig, available_channel_kinds,
                               redact, validate_channel)
from ytmon.config import AppConfig, NotifyChannel


class TestNotificationConfig(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = pathlib.Path(self.directory.name) / 'watchlist.json'
        self.store = NotificationConfig(self.path)

    def test_add_edit_disable_and_delete(self):
        self.store.put_channel(NotifyChannel(kind='windows', label='系统通知'))
        channel = self.store.channels[0]
        channel.enabled = False
        self.store.put_channel(channel, 0)
        self.assertFalse(self.store.channels[0].enabled)
        self.store.delete_channel(0)
        self.assertEqual(self.store.channels, [])

    def test_all_supported_channels(self):
        channels = [NotifyChannel(kind='windows'), NotifyChannel(kind='linux'),
                    NotifyChannel(kind='email', smtp_host='smtp.example.invalid', mail_to=['ops@example.invalid'])]
        channels += [NotifyChannel(kind=kind, url='https://example.invalid/hook') for kind in ('dingtalk', 'wecom', 'feishu', 'webhook')]
        for channel in channels:
            self.store.put_channel(channel)
        self.assertEqual(len(self.store.channels), 7)

    def test_new_channel_options_follow_platform(self):
        self.assertIn('windows', available_channel_kinds('win32'))
        self.assertNotIn('linux', available_channel_kinds('win32'))
        self.assertIn('linux', available_channel_kinds('linux'))
        self.assertNotIn('windows', available_channel_kinds('linux'))
        self.assertNotIn('windows', available_channel_kinds('darwin'))
        self.assertNotIn('linux', available_channel_kinds('darwin'))
        self.assertIn('email', available_channel_kinds('linux'))

    def test_linux_channel_roundtrip(self):
        self.store.put_channel(NotifyChannel(kind='linux', hold_seconds=8))
        config = AppConfig.load(self.path)
        config.save()
        self.store.reload()
        self.assertEqual(self.store.channels[0].hold_seconds, 8)

    def test_persistent_roundtrip_preserves_duration(self):
        self.store.put_channel(NotifyChannel(kind='windows', persistent=True, hold_seconds=12))
        config = AppConfig.load(self.path)
        self.assertTrue(config.notify[0].persistent)
        config.save()
        self.store.reload()
        channel = self.store.channels[0]
        self.assertTrue(channel.persistent)
        self.assertEqual(channel.hold_seconds, 12)
        channel.persistent = False
        self.store.put_channel(channel, 0)
        self.assertFalse(self.store.channels[0].persistent)
        self.assertEqual(self.store.channels[0].hold_seconds, 12)

    def test_persistent_validation(self):
        self.assertFalse(NotifyChannel(kind='windows').persistent)
        for channel in (NotifyChannel(kind='windows', persistent='true'),
                        NotifyChannel(kind='webhook', url='https://example.invalid/hook', persistent=True)):
            with self.assertRaises(ValueError):
                validate_channel(channel)

    def test_preserves_unrelated_and_channel_comment_fields(self):
        raw = {'targets': ['MSC IRINA'], 'settings': {'max_pages': 2, 'future': 42},
               '_说明': '保留', 'notify': [{'kind': 'windows', '_说明': '渠道说明'}]}
        self.path.write_text(json.dumps(raw), 'utf-8')
        self.store.reload()
        self.store.put_channel(NotifyChannel(kind='windows', label='新名称'), 0)
        self.store.save_policy(self.store.policy())
        saved = json.loads(self.path.read_text('utf-8'))
        self.assertEqual(saved['targets'], raw['targets'])
        self.assertEqual(saved['_说明'], '保留')
        self.assertEqual(saved['notify'][0]['_说明'], '渠道说明')
        self.assertEqual(saved['settings']['future'], 42)

    def test_policy_validation_and_save(self):
        policy = self.store.policy()
        self.assertNotIn('first', policy['alert_on'])
        policy['alert_on'] = ['changed', 'error']
        policy['alert_error_after'] = 3
        self.store.save_policy(policy)
        self.assertEqual(self.store.policy()['alert_error_after'], 3)
        policy['alert_on'] = []
        original = self.path.read_bytes()
        with self.assertRaises(ValueError):
            self.store.save_policy(policy)
        self.assertEqual(self.path.read_bytes(), original)

    def test_invalid_channel_does_not_write(self):
        for channel in [NotifyChannel(kind='webhook', url='file:///local'),
                        NotifyChannel(kind='email', smtp_host='smtp.example.invalid', smtp_port=0, mail_to=['ops@example.invalid']),
                        NotifyChannel(kind='email', smtp_host='smtp.example.invalid', mail_to=['invalid']),
                        NotifyChannel(kind='windows', hold_seconds=float('nan'))]:
            with self.subTest(kind=channel.kind):
                with self.assertRaises(ValueError):
                    self.store.put_channel(channel)
        self.assertFalse(self.path.exists())

    def test_external_change_is_not_overwritten(self):
        self.path.write_text('{"targets": ["MSC IRINA"]}', 'utf-8')
        with self.assertRaisesRegex(ValueError, '其他程序修改'):
            self.store.put_channel(NotifyChannel(kind='windows'))
        self.assertEqual(json.loads(self.path.read_text('utf-8'))['targets'], ['MSC IRINA'])


class TestRedaction(unittest.TestCase):
    def test_exception_url_and_credentials_are_masked(self):
        channel = NotifyChannel(kind='dingtalk', url='https://example.invalid/hook?access_token=FAKE_TOKEN', secret='FAKE_SECRET', smtp_password='FAKE_PASSWORD')
        text = redact('失败：%s FAKE_TOKEN FAKE_SECRET FAKE_PASSWORD' % channel.url, [channel])
        for value in ('FAKE_TOKEN', 'FAKE_SECRET', 'FAKE_PASSWORD', channel.url):
            self.assertNotIn(value, text)

    def test_feishu_hook_fragment_is_masked(self):
        channel = NotifyChannel(kind='feishu', url='https://example.invalid/hook/FAKE_UUID')
        self.assertNotIn('FAKE_UUID', redact('请求失败 /hook/FAKE_UUID', [channel]))
