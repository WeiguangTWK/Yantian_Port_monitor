"""GUI 目标配置操作的离线测试，不导入 Qt 或访问站点。"""

import json
import pathlib
import tempfile
import unittest
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from gui.target_config import TargetConfig


class TestTargetConfig(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = pathlib.Path(self.directory.name) / 'watchlist.json'

    def test_first_save_creates_configuration(self):
        store = TargetConfig(self.path)
        store.put('ship', '  MSC IRINA  ', '  我的船  ')
        saved = json.loads(self.path.read_text('utf-8'))
        self.assertEqual(saved['targets'][0]['value'], 'MSC IRINA')
        self.assertEqual(saved['targets'][0]['label'], '我的船')
        self.assertIn('settings', saved)

    def test_duplicate_including_disabled_is_rejected(self):
        store = TargetConfig(self.path)
        store.put('ship', 'MSC IRINA', enabled=False)
        original = self.path.read_bytes()
        with self.assertRaisesRegex(ValueError, '目标已存在'):
            store.put('ship', ' msc irina ')
        self.assertEqual(self.path.read_bytes(), original)
        self.assertEqual(len(store.targets), 1)

    def test_same_text_of_different_type_is_allowed(self):
        store = TargetConfig(self.path)
        store.put('ship', 'AB123')
        store.put('voyage', 'AB123')
        self.assertEqual(len(store.targets), 2)

    def test_edit_self_and_toggle(self):
        store = TargetConfig(self.path)
        store.put('ship', 'MSC IRINA')
        store.put('ship', 'MSC IRINA', '显示名称', False, 0)
        self.assertEqual(store.targets[0].label, '显示名称')
        self.assertFalse(store.targets[0].enabled)

    def test_edit_to_duplicate_is_rejected(self):
        store = TargetConfig(self.path)
        store.put('ship', 'MSC IRINA')
        store.put('ship', 'EVER GIVEN')
        with self.assertRaises(ValueError):
            store.put('ship', 'ever given', index=0)
        self.assertEqual(store.targets[0].value, 'MSC IRINA')

    def test_validation_does_not_write(self):
        store = TargetConfig(self.path)
        for kind, value in [('ship', ''), ('ship', 'A'), ('voyage', 'A'), ('invalid', 'AB')]:
            with self.subTest(kind=kind, value=value):
                with self.assertRaises(ValueError):
                    store.put(kind, value)
        self.assertFalse(self.path.exists())

    def test_delete_last_preserves_other_fields_and_history(self):
        raw = {'targets': ['MSC IRINA'], 'settings': {'future_option': 42},
               'notify': [{'kind': 'webhook', 'url': 'https://example.invalid'}],
               '_说明': '保留', 'custom': {'value': 1}}
        self.path.write_text(json.dumps(raw), 'utf-8')
        history = self.path.parent / 'history.json'
        history.write_text('history', 'utf-8')
        store = TargetConfig(self.path)
        store.delete(0)
        raw['targets'] = []
        self.assertEqual(json.loads(self.path.read_text('utf-8')), raw)
        self.assertEqual(history.read_text('utf-8'), 'history')

    def test_external_change_requires_reload(self):
        store = TargetConfig(self.path)
        store.put('ship', 'MSC IRINA')
        changed = {'targets': ['EVER GIVEN'], 'settings': {'max_pages': 2}}
        self.path.write_text(json.dumps(changed), 'utf-8')
        original = self.path.read_bytes()
        with self.assertRaisesRegex(ValueError, '其他程序修改'):
            store.delete(0)
        self.assertEqual(self.path.read_bytes(), original)
        store.reload()
        store.put('voyage', 'AB123')
        self.assertEqual(json.loads(self.path.read_text('utf-8'))['settings'], {'max_pages': 2})

    def test_save_failure_keeps_in_memory_targets(self):
        from unittest.mock import patch
        store = TargetConfig(self.path)
        store.put('ship', 'MSC IRINA')
        original = self.path.read_bytes()
        with patch.object(pathlib.Path, 'replace', side_effect=OSError('不可写')):
            with self.assertRaises(OSError):
                store.delete(0)
        self.assertEqual(store.targets[0].value, 'MSC IRINA')
        self.assertEqual(self.path.read_bytes(), original)
