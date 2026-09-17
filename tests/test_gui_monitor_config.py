"""监听设置读写的离线测试，不导入 Qt。"""

import json
import pathlib
import sys
import tempfile
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from gui.monitor_config import MonitorConfig
from gui.target_config import TargetConfig


class TestMonitorConfig(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = pathlib.Path(self.directory.name) / 'watchlist.json'

    def test_defaults_and_first_save(self):
        store = MonitorConfig(self.path)
        values = store.values()
        self.assertEqual(values['watch_interval_seconds'], 600)
        self.assertFalse(values['close_to_tray'])
        values['watch_interval_seconds'] = 1800
        store.save_values(values)
        raw = json.loads(self.path.read_text('utf-8'))
        self.assertEqual(raw['targets'], [])
        self.assertEqual(raw['settings']['watch_interval_seconds'], 1800)

    def test_preserves_targets_notifications_and_unknown_fields(self):
        original = {'targets': ['MSC IRINA'], 'notify': [{'kind': 'windows'}],
                    'settings': {'future_option': 42, 'state_file': 'custom/state.json'},
                    '_说明': '保留'}
        self.path.write_text(json.dumps(original), 'utf-8')
        store = MonitorConfig(self.path)
        values = store.values()
        values['edge_path'] = '  C:/Browser/browser.exe  '
        store.save_values(values)
        raw = json.loads(self.path.read_text('utf-8'))
        for key in ('targets', 'notify', '_说明'):
            self.assertEqual(raw[key], original[key])
        self.assertEqual(raw['settings']['future_option'], 42)
        self.assertEqual(raw['settings']['state_file'], 'custom/state.json')
        self.assertEqual(raw['settings']['edge_path'], 'C:/Browser/browser.exe')

    def test_empty_browser_path_means_automatic(self):
        store = MonitorConfig(self.path)
        values = store.values()
        values['edge_path'] = '  '
        store.save_values(values)
        self.assertIsNone(store.values()['edge_path'])

    def test_invalid_values_do_not_write(self):
        store = MonitorConfig(self.path)
        for key, value in [('watch_interval_seconds', 0), ('watch_interval_seconds', 5.5),
                           ('max_pages', 0), ('retry_attempts', -1),
                           ('watch_jitter_seconds', float('nan')),
                           ('headless', 'true'), ('close_to_tray', 'true'), ('edge_path', 123)]:
            with self.subTest(key=key, value=value):
                values = store.values()
                values[key] = value
                with self.assertRaises(ValueError):
                    store.save_values(values)
        self.assertFalse(self.path.exists())

    def test_tray_option_roundtrip(self):
        from ytmon.config import AppConfig
        store = MonitorConfig(self.path)
        values = store.values()
        values['close_to_tray'] = True
        store.save_values(values)
        self.assertTrue(AppConfig.load(self.path).settings.close_to_tray)
        store.reload()
        self.assertTrue(store.values()['close_to_tray'])

    def test_external_change_requires_reload(self):
        store = MonitorConfig(self.path)
        values = store.values()
        self.path.write_text('{"targets": ["EVER GIVEN"]}', 'utf-8')
        original = self.path.read_bytes()
        with self.assertRaisesRegex(ValueError, '其他程序修改'):
            store.save_values(values)
        self.assertEqual(self.path.read_bytes(), original)

    def test_gui_pages_can_refresh_snapshots_without_losing_fields(self):
        settings = MonitorConfig(self.path)
        targets = TargetConfig(self.path)
        values = settings.values()
        values['watch_interval_seconds'] = 1800
        targets.put('ship', 'MSC IRINA')
        settings.reload()
        settings.save_values(values)
        targets.reload()
        targets.put('voyage', 'AB123')
        raw = json.loads(self.path.read_text('utf-8'))
        self.assertEqual(len(raw['targets']), 2)
        self.assertEqual(raw['settings']['watch_interval_seconds'], 1800)

    def test_failed_save_keeps_original_snapshot(self):
        from unittest.mock import patch
        store = MonitorConfig(self.path)
        values = store.values()
        with patch.object(pathlib.Path, 'replace', side_effect=OSError('不可写')):
            with self.assertRaises(OSError):
                store.save_values(values)
        self.assertIsNone(store.original)
        self.assertFalse(self.path.exists())
