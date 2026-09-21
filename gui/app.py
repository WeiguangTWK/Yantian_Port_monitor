"""船期监控面板。查询在 QThread 中执行，通过信号更新界面。"""

from __future__ import annotations

import pathlib
import datetime as dt
import math
import sys
import traceback
import copy
from collections import deque

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

# 先把控制台编码兜底装上：下面任何一句报错都可能带中文，
# 而中文 Windows 下**重定向输出**时会因 GBK 编码不了而崩在半路（见 ytmon/console.py）。
# cli.py 早就有这一句，GUI 入口之前漏了。
from ytmon.console import ensure_safe_stdout                 # noqa: E402

ensure_safe_stdout()

try:
    from qt_compat import (BINDING, NO_EDIT_TRIGGERS, QT_VERSION, QThread,
                           QtCore, QtGui, QtWidgets, Signal, exec_app)
except ImportError as e:                                      # pragma: no cover
    print(f"缺少 Qt 绑定：{e}", file=sys.stderr)
    raise SystemExit(2)

try:
    from qfluentwidgets import (BodyLabel, CardWidget, FluentWindow,
                                InfoBar, InfoBarPosition,
                                PrimaryPushButton, ProgressBar,
                                PushButton, SubtitleLabel, TextEdit)
except ImportError as e:                                      # pragma: no cover
    print(f"界面组件加载失败：{e}\n"
          "源码运行请检查对应平台的 Fluent Widgets 依赖；"
          "冻结程序请检查组件是否完整。", file=sys.stderr)
    raise SystemExit(2)

# 从兼容层统一取控件类（两套绑定通用）
QWidget = QtWidgets.QWidget
QVBoxLayout = QtWidgets.QVBoxLayout
QHBoxLayout = QtWidgets.QHBoxLayout
QTableWidget = QtWidgets.QTableWidget
QTableWidgetItem = QtWidgets.QTableWidgetItem
QApplication = QtWidgets.QApplication

from ytmon import AppConfig, MonitorService                        # noqa: E402
from targets import TargetsPage                                   # noqa: E402
from monitor_settings import MonitorSettingsPage                  # noqa: E402
from notifications import NotificationsPage                       # noqa: E402
from about import AboutPage                                       # noqa: E402
from gui.tray_icon import TrayIconTheme                           # noqa: E402
from gui.notification_transport import dispatch_windows           # noqa: E402
from gui.persistent_notifications import PersistentNotifications   # noqa: E402
from gui.monitor_runtime import Countdown, row_status             # noqa: E402
from ytmon.notify import Notifier                                 # noqa: E402
from ytmon.store import target_key                                # noqa: E402
from ytmon.fatal import (format_exception, report_fatal,           # noqa: E402
                         stderr_is_lost)
from ytmon.paths import (anchor_to_app_dir, app_base_dir,          # noqa: E402
                         writable_warning)
from ytmon.service import (STATUS_CHANGED, STATUS_ERROR,           # noqa: E402
                           STATUS_FIRST, STATUS_MISSING, STATUS_SAME)

# 配置用**绝对路径**：打包成 exe 之后 CWD 不可信
# （双击、快捷方式、计划任务各自的"当前目录"都不一样）。
CONFIG_PATH = str(app_base_dir() / "watchlist.json")

# 图标属于程序资源，冻结后从解包目录读取，不依赖当前工作目录。
ASSET_DIR = (pathlib.Path(sys._MEIPASS) / "gui" / "assets"
             if getattr(sys, "frozen", False)
             else pathlib.Path(__file__).resolve().parent / "assets")

STATUS_TEXT = {
    STATUS_FIRST: "首次",
    STATUS_CHANGED: "⚠ 变更",
    STATUS_SAME: "无变化",
    STATUS_MISSING: "未查到",
    STATUS_ERROR: "错误",
}

TABLE_COLUMNS = ["目标", "类型", "码头航次", "船名", "闸口",
                 "ETB", "ETD", "船代", "状态"]


# ---------------------------------------------------------------- worker 线程


class MonitorWorker(QThread):
    """在后台线程里跑 MonitorService，用 signal 把结果送回 UI 线程。

    ⚠️ 绝不要在这个类里直接改控件 —— 只能 emit。
    """

    event = Signal(str, dict)          # 进度事件（on_event 回调转出来）
    finished_ok = Signal(object)       # CycleReport
    failed = Signal(str)

    def __init__(self, cfg: AppConfig, parent=None):
        super().__init__(parent)
        self.cfg = cfg

    def run(self) -> None:                                     # noqa: D102
        try:
            svc = MonitorService(self.cfg, on_event=self._on_event,
                                 dump_dir="snapshots")
            report = svc.run_cycle()
            self.finished_ok.emit(report)
        except Exception as e:                                 # noqa: BLE001
            self.failed.emit(f"{type(e).__name__}: {e}\n"
                             f"{traceback.format_exc()[-600:]}")

    def _on_event(self, kind: str, payload: dict) -> None:
        # payload 里可能有 TargetOutcome 等对象，Qt signal 用 object 传即可
        self.event.emit(kind, payload)


# ---------------------------------------------------------------- 界面


class NotificationWorker(QThread):
    """只发送通知；由主页按 FIFO 启动，避免冷却状态的并发读写。"""

    event = Signal(str, dict)
    persistent_notification = Signal(str, str)

    def __init__(self, cfg, report, parent=None):
        super().__init__(parent)
        self.cfg = cfg
        self.report = report

    def run(self):
        try:
            Notifier(self.cfg.enabled_channels, self.cfg.settings,
                     on_event=self.event.emit,
                     windows_transport=lambda channel, message: dispatch_windows(
                         channel, message, self.persistent_notification.emit)).notify_cycle(self.report)
        except Exception as error:
            self.event.emit('log', {'message': f'通知处理失败：{error}', 'level': 'error'})


class MonitorPage(QWidget):
    """监控面板：目标表格 + 操作按钮 + 进度 + 日志。"""

    busy_changed = Signal(bool)
    notification_queue_changed = Signal(bool)
    persistent_notification = Signal(str, str)

    def __init__(self, cfg: AppConfig, parent=None):
        super().__init__(parent)
        self.cfg = cfg
        self.worker: MonitorWorker | None = None
        self.notification_worker = None
        self.notification_jobs = deque()
        self.querying = False
        self.notification_busy = False
        self.monitoring = False
        self.countdown = Countdown()
        self.target_states = {}
        self.last_checked = {}
        self.latest_outcomes = {}

        root = QVBoxLayout(self)
        root.setContentsMargins(24, 24, 24, 24)
        root.setSpacing(12)

        root.addWidget(SubtitleLabel("船期监控", self))
        self.hint = BodyLabel("按船名或码头航次查看查询结果和船期变更。", self)
        root.addWidget(self.hint)

        # --- 按钮行 ---
        bar = QHBoxLayout()
        self.btn_run = PrimaryPushButton("立即检查", self)
        bar.addWidget(self.btn_run)
        self.btn_watch = PushButton("开始监听", self)
        self.btn_watch.clicked.connect(self._toggle_watch)
        bar.addWidget(self.btn_watch)
        bar.addStretch(1)
        root.addLayout(bar)

        self.btn_run.clicked.connect(self._start)

        self.progress = ProgressBar(self)
        self.progress.setMaximum(1000)
        self.progress.setValue(0)
        root.addWidget(self.progress)
        self.countdown_text = BodyLabel("监听未启动", self)
        root.addWidget(self.countdown_text)
        self.timer = QtCore.QTimer(self)
        self.timer.setInterval(250)
        self.timer.timeout.connect(self._tick)
        self.timer.start()

        # --- 表格 ---
        card = CardWidget(self)
        card_layout = QVBoxLayout(card)
        self.table = QTableWidget(0, len(TABLE_COLUMNS), card)
        self.table.setHorizontalHeaderLabels(TABLE_COLUMNS)
        self.table.setEditTriggers(NO_EDIT_TRIGGERS)
        self.table.setAlternatingRowColors(True)
        self.table.horizontalHeader().setStretchLastSection(True)
        card_layout.addWidget(self.table)
        root.addWidget(card, 2)

        # --- 日志 ---
        root.addWidget(BodyLabel("运行日志", self))
        self.log = TextEdit(self)
        self.log.setReadOnly(True)
        self.log.document().setMaximumBlockCount(1000)
        root.addWidget(self.log, 1)

        self.refresh_table()

    # ---------------------------------------------------------- 表格

    def refresh_table(self) -> None:
        """从 service.snapshot() 拉当前状态填表（只读）。"""
        try:
            data = MonitorService(self.cfg).snapshot()["targets"]
        except Exception as e:                                 # noqa: BLE001
            self.append_log(f"[warn] 读取状态失败：{e}")
            data = []

        self.table.setRowCount(len(data))
        for r, row in enumerate(data):
            key = target_key(row['type'], row['value'])
            outcome = self.latest_outcomes.get(key)
            if outcome and outcome.voyages:
                voyage = outcome.voyages[0]
                row.update(voyage_code=voyage.voyage_code, ship_name=voyage.ship_name,
                           gate=voyage.gate, etb=voyage.etb_raw, etd=voyage.etd_raw, agent=voyage.agent)
            status = row_status(row['enabled'], self.target_states.get(key, ''),
                                self.last_checked.get(key, row['last_seen']))
            values = [row["label"], {"ship": "船名", "voyage": "航次"}.get(row["type"], row["type"]),
                      row["voyage_code"], row["ship_name"], row["gate"],
                      row["etb"], row["etd"], row["agent"], status]
            for c, v in enumerate(values):
                self.table.setItem(r, c, QTableWidgetItem(str(v)))
        self.table.resizeColumnsToContents()

    # ---------------------------------------------------------- 运行

    def _start(self) -> None:
        if self.querying:
            self.append_log("[skip] 上一轮还没跑完")
            return
        if self.notification_busy:
            return

        try:
            self.cfg = AppConfig.load(CONFIG_PATH)             # 每轮重载配置
        except (OSError, ValueError, TypeError, AttributeError) as error:
            self._configuration_failed(str(error))
            return

        try:
            errs = self.cfg.validate()
            if not self.cfg.enabled_targets:
                errs.append('请先新增并启用至少一个目标。')
            if self.monitoring:
                Countdown.validate(self.cfg.settings.watch_interval_seconds, self.cfg.settings.watch_jitter_seconds)
        except (ValueError, TypeError, OverflowError) as error:
            errs = [str(error)]
        if errs:
            self._configuration_failed("；".join(errs[:3]))
            return

        self.countdown.clear()
        self.progress.setValue(0)
        self.querying = True
        self.countdown_text.setText('正在查询，完成后开始下一轮倒计时' if self.monitoring else '正在查询')
        for target in self.cfg.enabled_targets:
            self.target_states[target_key(target.type, target.value)] = 'updating'
        self.refresh_table()
        self.append_log("开始检查船期")
        self.btn_run.setEnabled(False)

        self.worker = MonitorWorker(self.cfg, self)
        self.worker.event.connect(self._on_event)
        self.worker.finished_ok.connect(self._on_done)
        self.worker.failed.connect(self._on_failed)
        self.worker.finished.connect(self._on_thread_finished)
        self.busy_changed.emit(True)
        self.worker.start()

    def _on_event(self, kind: str, payload: dict) -> None:
        """在 UI 线程里执行（signal 跨线程是队列投递，安全）。"""
        if kind == "cycle_start":
            total = payload.get("total", 0)
            self.append_log(f"本轮 {total} 个目标，起始日 {payload.get('etb_time')}")
        elif kind == "target_start":
            t = payload.get("target")
            self.append_log(f"[{payload.get('index')}/{payload.get('total')}] 查询 {t.display()} …")
        elif kind == "target_done":
            o = payload["outcome"]
            self.append_log(f"    {STATUS_TEXT.get(o.status, o.status)}：{o.label}"
                            + (f"  {o.error}" if o.error else ""))
            key = target_key(o.target.type, o.target.value)
            self.target_states[key] = o.status
            self.latest_outcomes[key] = o
            if o.status not in (STATUS_ERROR, STATUS_MISSING):
                self.last_checked[key] = dt.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            self.refresh_table()
            if o.changed:
                for v in o.voyages:
                    for c in o.changes.get(v.voyage_code, []):
                        self.append_log(f"      · {c.describe()}")
        elif kind == "log":
            self.append_log(f"    {payload.get('message')}")
        elif kind == "alert_sent":
            self.append_log(f"[通知] {'已发送：' + str(payload.get('channel')) if payload.get('ok') else payload.get('message')}")

    def _on_done(self, result) -> None:
        cycle_cfg = self.worker.cfg
        if cycle_cfg.enabled_channels:
            # 使用本轮配置快照；后续编辑配置不能改写已排队的告警。
            self.notification_jobs.append((copy.deepcopy(cycle_cfg), copy.deepcopy(result)))
            self._start_notification_job()
        # 早停时没有结果的目标不能继续显示“正在更新”。
        for target in self.cfg.enabled_targets:
            key = target_key(target.type, target.value)
            if self.target_states.get(key) == 'updating':
                self.target_states[key] = STATUS_ERROR
        self.append_log(f"检查完成：{result.summary_line()}")
        if result.has_changes:
            self._toast("船期变更", result.summary_line())
        self.refresh_table()

    @property
    def notifications_pending(self):
        return self.notification_worker is not None or bool(self.notification_jobs)

    def _start_notification_job(self):
        if self.notification_worker is not None or not self.notification_jobs:
            return
        cfg, report = self.notification_jobs.popleft()
        self.notification_worker = NotificationWorker(cfg, report, self)
        self.notification_worker.event.connect(self._on_event)
        self.notification_worker.persistent_notification.connect(self.persistent_notification)
        self.notification_worker.finished.connect(self._notification_job_finished)
        self.notification_queue_changed.emit(True)
        self.notification_worker.start()

    def _notification_job_finished(self):
        worker = self.notification_worker
        self.notification_worker = None
        worker.deleteLater()
        self._start_notification_job()
        if not self.notifications_pending:
            self.notification_queue_changed.emit(False)

    def _on_failed(self, message: str) -> None:
        for target in self.cfg.enabled_targets:
            key = target_key(target.type, target.value)
            if self.target_states.get(key) == 'updating':
                self.target_states[key] = STATUS_ERROR
        self.refresh_table()
        self.append_log(f"[错误] {message}")
        self._toast("执行失败", message[:200], error=True)

    def _on_thread_finished(self) -> None:
        worker = self.worker
        self.worker = None
        if worker is not None:
            worker.deleteLater()
        self.querying = False
        self.btn_run.setEnabled(True)
        self.busy_changed.emit(False)
        if self.monitoring:
            self._schedule_next()
        else:
            self._tick()

    def _configuration_failed(self, message):
        self._stop_watch()
        self.append_log(f'[配置错误] {message}')
        self._toast('配置有问题', message, error=True)

    def _toggle_watch(self):
        if self.monitoring:
            self._stop_watch()
            return
        if self.notification_busy:
            return
        self.monitoring = True
        self.btn_watch.setText('停止监听')
        self.append_log('监听已启动')
        if not self.querying:
            self._start()

    def _stop_watch(self):
        was_monitoring = self.monitoring
        self.monitoring = False
        self.countdown.clear()
        self.btn_watch.setText('开始监听')
        if was_monitoring:
            self.append_log('监听已停止；当前查询不会被强制中断')
        self._tick()

    def _schedule_next(self):
        try:
            self.countdown.reset(self.cfg.settings.watch_interval_seconds,
                                 self.cfg.settings.watch_jitter_seconds)
        except (ValueError, TypeError, OverflowError) as error:
            self._configuration_failed(str(error))
            return
        self._tick()

    def configuration_changed(self):
        keys = {target_key(target.type, target.value) for target in self.cfg.targets}
        for mapping in (self.target_states, self.last_checked, self.latest_outcomes):
            for key in list(mapping):
                if key not in keys:
                    del mapping[key]
        self.refresh_table()
        if self.monitoring and not self.querying:
            if not self.cfg.enabled_targets:
                self._stop_watch()
            else:
                self._schedule_next()

    def _tick(self):
        if self.querying:
            self.progress.setValue(0)
            self.countdown_text.setText('正在查询；完成后继续监听' if self.monitoring else '正在查询；自动监听已停止')
            return
        if self.notification_busy:
            self.progress.setValue(self.countdown.bar_value)
            self.countdown_text.setText('测试推送中，自动查询暂缓')
            return
        if not self.monitoring:
            self.progress.setValue(0)
            self.countdown_text.setText('监听未启动')
            return
        self.progress.setValue(self.countdown.bar_value)
        seconds = math.ceil(self.countdown.remaining)
        minutes, seconds = divmod(seconds, 60)
        self.countdown_text.setText(f'距离下一次查询：{minutes:02d}:{seconds:02d}')
        if self.countdown.due and QApplication.activeModalWidget() is None:
            self._start()

    def set_notification_busy(self, busy):
        self.notification_busy = busy
        self.btn_run.setEnabled(not busy and not self.querying)
        self._tick()

    # ---------------------------------------------------------- 小工具

    def append_log(self, line: str) -> None:
        self.log.append(line)

    def _toast(self, title: str, content: str, error: bool = False) -> None:
        InfoBar.error(title, content, duration=6000, position=InfoBarPosition.TOP_RIGHT,
                      parent=self) if error else \
            InfoBar.success(title, content, duration=4000, position=InfoBarPosition.TOP_RIGHT,
                            parent=self)


class MainWindow(FluentWindow):
    def __init__(self, cfg: AppConfig):
        super().__init__()
        self.exit_requested = False
        QApplication.instance().setQuitOnLastWindowClosed(False)
        self.tray = QtWidgets.QSystemTrayIcon(QtGui.QIcon(str(ASSET_DIR / "ship.svg")), self)
        self.tray_theme = TrayIconTheme(self.tray, QtGui.QIcon(str(ASSET_DIR / "ship.svg")), self)
        self.tray.setToolTip('盐田船期监控')
        self.tray_menu = QtWidgets.QMenu(self)
        self.tray_menu.addAction('打开窗口', self._restore_window)
        self.tray_menu.addSeparator()
        self.tray_menu.addAction('退出程序', self._request_exit)
        self.tray.setContextMenu(self.tray_menu)
        self.tray.activated.connect(self._tray_activated)
        self.persistent_notifications = PersistentNotifications(self)
        self.monitor_page = MonitorPage(cfg, self)
        self.monitor_page.persistent_notification.connect(self.persistent_notifications.show_message)
        self.monitor_page.setObjectName("monitorPage")

        self.addSubInterface(self.monitor_page, QtGui.QIcon(str(ASSET_DIR / "home.svg")), "主页")
        self.targets_page = TargetsPage(CONFIG_PATH, self)
        self.targets_page.setObjectName("targetsPage")
        self.addSubInterface(self.targets_page, QtGui.QIcon(str(ASSET_DIR / "ship.svg")), "目标管理")
        self.monitor_page.busy_changed.connect(self.targets_page.set_busy)
        self.targets_page.changed.connect(self._reload_config)
        self.settings_page = MonitorSettingsPage(CONFIG_PATH, self)
        self.settings_page.setObjectName("monitorSettingsPage")
        self.addSubInterface(self.settings_page, QtGui.QIcon(str(ASSET_DIR / "monitor.svg")), "监听设置")
        self.monitor_page.busy_changed.connect(self.settings_page.set_busy)
        self.settings_page.changed.connect(self._settings_saved)
        self.targets_page.changed.connect(self.settings_page.refresh_snapshot)
        self.notifications_page = NotificationsPage(CONFIG_PATH, self)
        self.notifications_page.persistent_notification.connect(self.persistent_notifications.show_message)
        self.notifications_page.setObjectName("notificationsPage")
        self.addSubInterface(self.notifications_page, QtGui.QIcon(str(ASSET_DIR / "notify.svg")), "通知与测试")
        self.monitor_page.busy_changed.connect(self.notifications_page.set_busy)
        self.notifications_page.testing_changed.connect(self._notification_testing)
        self.monitor_page.busy_changed.connect(self._try_pending_exit)
        self.monitor_page.notification_queue_changed.connect(self._try_pending_exit)
        self.notifications_page.testing_changed.connect(self._try_pending_exit)
        self.notifications_page.changed.connect(self._notifications_saved)
        self.targets_page.changed.connect(self.notifications_page.refresh_snapshot)
        self.settings_page.changed.connect(self.notifications_page.refresh_snapshot)
        self.about_page = AboutPage(self)
        self.about_page.setObjectName("aboutPage")
        self.addSubInterface(self.about_page, QtGui.QIcon(str(ASSET_DIR / "about.svg")), "关于")

        self.resize(1080, 720)
        self.setWindowTitle(f"盐田船期监控  ·  {BINDING} / Qt {QT_VERSION}")
        self._sync_tray()

    def _sync_tray(self):
        enabled = self.monitor_page.cfg.settings.close_to_tray is True
        self.tray.setVisible(enabled and self.tray.isSystemTrayAvailable())
        self.tray_theme.set_enabled(enabled)

    def _restore_window(self):
        self.showNormal()
        self.raise_()
        self.activateWindow()

    def _tray_activated(self, reason):
        if reason in (QtWidgets.QSystemTrayIcon.Trigger, QtWidgets.QSystemTrayIcon.DoubleClick):
            self._restore_window()

    def _request_exit(self):
        self.exit_requested = True
        self.close()

    def _try_pending_exit(self, busy=False):
        if (self.exit_requested and not self.monitor_page.querying
                and not self.monitor_page.notifications_pending and not self.notifications_page.testing):
            # 等 finished 的清理槽完成后，再关窗口和 Qt 事件循环。
            QtCore.QTimer.singleShot(0, self.close)

    def _notification_testing(self, busy):
        self.targets_page.set_busy(busy)
        self.settings_page.set_busy(busy)
        self.monitor_page.set_notification_busy(busy)

    def _notifications_saved(self):
        self.settings_page.refresh_snapshot()
        try:
            self.targets_page.store.reload()
            self.targets_page.refresh()
        except (OSError, ValueError) as error:
            self.targets_page.message.setText(str(error))
        self._reload_config()

    def _settings_saved(self) -> None:
        try:
            self.targets_page.store.reload()
            self.targets_page.refresh()
        except (OSError, ValueError) as error:
            self.targets_page.message.setText(str(error))
        self._reload_config()

    def _reload_config(self) -> None:
        try:
            self.monitor_page.cfg = AppConfig.load(CONFIG_PATH)
        except (OSError, ValueError) as error:
            self.monitor_page.append_log(f"配置读取失败：{error}")
            return
        self.monitor_page.configuration_changed()
        self._sync_tray()

    def closeEvent(self, event):
        if not self.exit_requested and self.monitor_page.cfg.settings.close_to_tray is True:
            if not self.tray.isSystemTrayAvailable():
                event.ignore()
                self.monitor_page._toast('系统托盘不可用', '窗口保持打开；请在监听设置中关闭托盘驻留后退出。', error=True)
                return
            self.tray.show()
            self.tray_theme.set_enabled(True)
            event.ignore()
            self.hide()
            self.monitor_page.append_log('窗口已隐藏至系统托盘，当前监听状态保持不变')
            return
        self.monitor_page._stop_watch()
        if (self.monitor_page.querying or self.monitor_page.notifications_pending
                or self.notifications_page.testing):
            self.exit_requested = True
            self.setEnabled(False)
            event.ignore()
            self._restore_window()
            self.monitor_page.append_log('正在退出：已停止自动监听，等待当前后台任务结束')
            return
        self.monitor_page.timer.stop()
        self.tray_theme.timer.stop()
        self.persistent_notifications.close_all()
        self.tray.hide()
        super().closeEvent(event)
        if event.isAccepted():
            QApplication.instance().quit()


def main() -> int:
    # 冻结时把 CWD 钉到 exe 所在目录：`state/`、`.cache/`、`.browser_profile/`
    # 都是相对 CWD 解析的，不钉的话它们会散到"启动时所在目录"
    # （被计划任务拉起时常常是 C:\Windows\System32）。
    # 源码运行时这个调用什么都不做。
    base = anchor_to_app_dir()

    # ⚠️ windowed 产物**没有控制台** —— `print(..., file=sys.stderr)` 等于没说。
    # 这类启动期失败必须走"落盘 + 弹窗"，否则现场看到的就是"双击没反应"。
    # 实测教训见 ytmon/fatal.py 的模块文档。
    warn = writable_warning(base)
    if warn:
        report_fatal("程序所在目录不可写", warn, dialog=stderr_is_lost())

    try:
        cfg = AppConfig.load(CONFIG_PATH)
    except FileNotFoundError:
        cfg = AppConfig(path=pathlib.Path(CONFIG_PATH))

    app = QApplication(sys.argv)
    win = MainWindow(cfg)
    win.show()
    return exec_app(app)


if __name__ == "__main__":
    # 兜住一切启动失败：windowed 产物里连未捕获的异常都是**什么都不显示**。
    try:
        raise SystemExit(main())
    except SystemExit:
        raise
    except BaseException as e:                                # noqa: BLE001
        report_fatal("程序启动失败", format_exception(e),
                     dialog=stderr_is_lost())
        raise SystemExit(1)
