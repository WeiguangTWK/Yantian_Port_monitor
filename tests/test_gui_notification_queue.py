"""通知队列的离线检查；提取队列方法配合假 worker，不加载 Qt。"""

import ast
from collections import deque
import pathlib
import unittest
from unittest.mock import Mock

ROOT = pathlib.Path(__file__).resolve().parent.parent


class FakeWorker:
    def __init__(self, cfg, report, parent):
        self.cfg, self.report = cfg, report
        self.event = Mock()
        self.persistent_notification = Mock()
        self.finished = Mock()
        self.start = Mock()
        self.deleteLater = Mock()


def queue_harness():
    tree = ast.parse((ROOT / 'gui/app.py').read_text('utf-8'))
    page = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == 'MonitorPage')
    names = {'notifications_pending', '_start_notification_job', '_notification_job_finished'}
    methods = [n for n in page.body if isinstance(n, ast.FunctionDef) and n.name in names]
    namespace = {'NotificationWorker': FakeWorker}
    module = ast.Module(body=methods, type_ignores=[])
    exec(compile(module, 'gui/app.py queue methods', 'exec'), namespace)
    harness = type('QueueHarness', (), {name: namespace[name] for name in names})()
    harness.notification_worker = None
    harness.notification_jobs = deque()
    harness.notification_queue_changed = Mock()
    harness._on_event = Mock()
    harness.persistent_notification = Mock()
    return harness


class TestNotificationQueue(unittest.TestCase):
    def test_fifo_keeps_later_cycles_while_first_notification_is_running(self):
        page = queue_harness()
        page.notification_jobs.append(('cfg1', 'report1'))
        page._start_notification_job()
        first = page.notification_worker
        page.notification_jobs.extend([('cfg2', 'report2'), ('cfg3', 'report3')])
        page._start_notification_job()
        self.assertIs(page.notification_worker, first)
        self.assertEqual(len(page.notification_jobs), 2)
        self.assertTrue(page.notifications_pending)
        page._notification_job_finished()
        first.deleteLater.assert_called_once()
        self.assertEqual(page.notification_worker.report, 'report2')
        page._notification_job_finished()
        self.assertEqual(page.notification_worker.report, 'report3')
        page._notification_job_finished()
        self.assertFalse(page.notifications_pending)
        page.notification_queue_changed.assert_called_with(False)

    def test_scanner_and_countdown_do_not_wait_for_notification_queue(self):
        tree = ast.parse((ROOT / 'gui/app.py').read_text('utf-8'))
        scanner = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == 'MonitorWorker')
        self.assertNotIn('Notifier', {n.id for n in ast.walk(scanner) if isinstance(n, ast.Name)})
        page = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == 'MonitorPage')
        for name in ('_start', '_tick', '_on_thread_finished'):
            method = next(n for n in page.body if isinstance(n, ast.FunctionDef) and n.name == name)
            attrs = {n.attr for n in ast.walk(method) if isinstance(n, ast.Attribute)}
            self.assertNotIn('notifications_pending', attrs)
            self.assertNotIn('wait', attrs)
        cleanup = next(n for n in page.body if isinstance(n, ast.FunctionDef) and n.name == '_on_thread_finished')
        self.assertIn('_schedule_next', {n.attr for n in ast.walk(cleanup) if isinstance(n, ast.Attribute)})
