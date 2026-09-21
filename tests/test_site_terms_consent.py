"""站点条款确认的离线测试，不访问网站。"""

import contextlib
import io
import json
import pathlib
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from gui.consent_config import record_site_terms_acceptance
from ytmon.cli import EXIT_ERROR, main
from ytmon.config import AppConfig, Settings, Target
from tools import env_check


class TestSiteTermsConsent(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.path = pathlib.Path(directory.name) / 'watchlist.json'

    def test_missing_file_is_created_only_after_acceptance(self):
        self.assertFalse(self.path.exists())
        record_site_terms_acceptance(self.path)
        cfg = AppConfig.load(self.path)
        self.assertIs(cfg.settings.site_terms_accepted, True)
        self.assertTrue(cfg.settings.site_terms_accepted_at)

    def test_preserves_other_config_and_roundtrips(self):
        original = {'targets': [{'type': 'ship', 'value': 'TEST'}],
                    'settings': {'watch_interval_seconds': 900, '_说明': '保留'},
                    'notify': [{'kind': 'windows', 'enabled': False}], '_备注': '保留'}
        self.path.write_text(json.dumps(original), encoding='utf-8')
        record_site_terms_acceptance(self.path)
        saved = json.loads(self.path.read_text('utf-8'))
        self.assertEqual(saved['targets'], original['targets'])
        self.assertEqual(saved['notify'], original['notify'])
        self.assertEqual(saved['_备注'], '保留')
        self.assertEqual(saved['settings']['_说明'], '保留')
        cfg = AppConfig.load(self.path)
        cfg.save()
        self.assertIs(AppConfig.load(self.path).settings.site_terms_accepted, True)

    def test_only_literal_true_passes_cli_query_gate(self):
        for value in (None, False, 'true', 1):
            with self.subTest(value=value):
                settings = Settings()
                settings.site_terms_accepted = value
                AppConfig(targets=[Target('ship', 'TEST')], settings=settings).save(self.path)
                output = io.StringIO()
                with patch('ytmon.cli.run_once') as run_once, contextlib.redirect_stderr(output):
                    code = main(['--config', str(self.path)])
                self.assertEqual(code, EXIT_ERROR)
                run_once.assert_not_called()
                self.assertIn('先启动 GUI', output.getvalue())

    def test_cli_query_runs_after_acceptance(self):
        AppConfig(targets=[Target('ship', 'TEST')],
                  settings=Settings(site_terms_accepted=True)).save(self.path)
        with patch('ytmon.cli.run_once', return_value=0) as run_once:
            self.assertEqual(main(['--config', str(self.path)]), 0)
        run_once.assert_called_once()

    def test_offline_alert_test_is_not_gated(self):
        AppConfig().save(self.path)
        with patch('ytmon.cli.run_test_alert', return_value=0) as test_alert:
            self.assertEqual(main(['--config', str(self.path), '--test-alert']), 0)
        test_alert.assert_called_once()

    def test_env_check_does_not_probe_site_without_acceptance(self):
        AppConfig().save(self.path)
        with patch.object(sys, 'argv', ['env_check.py', '--config', str(self.path)]), patch.object(
                env_check, 'check_network') as network, patch.object(
                env_check, 'check_site') as site, patch.object(
                env_check, 'check_bootstrap') as bootstrap, contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(env_check.main(), 1)
        network.assert_not_called()
        site.assert_not_called()
        bootstrap.assert_not_called()
