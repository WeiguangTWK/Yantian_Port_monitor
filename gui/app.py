"""GUI 骨架（PySide6 或 PySide2 + Fluent-Widgets）。

⚠️ 状态：**尚未运行验证**。依赖装好后需要跑一遍再修。

绑定选择由 `qt_compat` 自动决定：
    Win10/11 → PySide6（Qt 6）
    Win7     → PySide2（Qt 5.15，因为 Qt 6 不支持 Win7）
界面代码只写一套。

为什么先写它：GUI 最容易写错的不是界面，而是
「不要在 worker 线程里碰控件」。这里把正确模式固化下来 ——
`MonitorService` 在 QThread 里跑，事件通过 signal 回到 UI 线程。

运行：
    Win10/11:  pip install PySide6-Fluent-Widgets
    Win7:      pip install -r requirements-win7-gui.txt
    python gui/app.py
"""

from __future__ import annotations

import pathlib
import sys
import traceback

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

try:
    from qt_compat import (BINDING, NO_EDIT_TRIGGERS, QT_VERSION, QThread,
                           QtWidgets, Signal, exec_app)
except ImportError as e:                                      # pragma: no cover
    print(f"缺少 Qt 绑定：{e}", file=sys.stderr)
    raise SystemExit(2)

try:
    from qfluentwidgets import (BodyLabel, CardWidget, FluentWindow,
                                InfoBar, InfoBarPosition, NavigationItemPosition,
                                PrimaryPushButton, ProgressBar, PushButton,
                                SubtitleLabel, TextEdit)
except ImportError as e:                                      # pragma: no cover
    print("缺少 qfluentwidgets。请安装：\n"
          "    Win10/11:  pip install PySide6-Fluent-Widgets\n"
          "    Win7:      pip install -r requirements-win7-gui.txt\n"
          f"（原始错误：{e}）", file=sys.stderr)
    raise SystemExit(2)

# 从兼容层统一取控件类（两套绑定通用）
QWidget = QtWidgets.QWidget
QVBoxLayout = QtWidgets.QVBoxLayout
QHBoxLayout = QtWidgets.QHBoxLayout
QTableWidget = QtWidgets.QTableWidget
QTableWidgetItem = QtWidgets.QTableWidgetItem
QApplication = QtWidgets.QApplication

from ytmon import AppConfig, MonitorService                        # noqa: E402
from ytmon.errors import TokenError                                # noqa: E402
from ytmon.service import (STATUS_CHANGED, STATUS_ERROR,           # noqa: E402
                           STATUS_FIRST, STATUS_MISSING, STATUS_SAME)

CONFIG_PATH = "watchlist.json"

STATUS_TEXT = {
    STATUS_FIRST: "首次",
    STATUS_CHANGED: "⚠ 变更",
    STATUS_SAME: "无变化",
    STATUS_MISSING: "未查到",
    STATUS_ERROR: "错误",
}

TABLE_COLUMNS = ["目标", "类型", "码头航次", "船名", "闸口",
                 "ETB", "ETD", "船代", "上次核对"]


# ---------------------------------------------------------------- worker 线程


class MonitorWorker(QThread):
    """在后台线程里跑 MonitorService，用 signal 把结果送回 UI 线程。

    ⚠️ 绝不要在这个类里直接改控件 —— 只能 emit。
    """

    event = Signal(str, dict)          # 进度事件（on_event 回调转出来）
    finished_ok = Signal(object)       # CycleReport
    failed = Signal(str)

    def __init__(self, cfg: AppConfig, action: str = "cycle", parent=None):
        super().__init__(parent)
        self.cfg = cfg
        self.action = action

    def run(self) -> None:                                     # noqa: D102
        try:
            svc = MonitorService(self.cfg, on_event=self._on_event,
                                 dump_dir="snapshots")
            if self.action == "token":
                self.finished_ok.emit(svc.check_token())
            elif self.action == "renew":
                self.finished_ok.emit(svc.renew_token())
            else:
                self.finished_ok.emit(svc.run_cycle())
        except TokenError as e:
            self.failed.emit(f"token 失效：{e}")
        except Exception as e:                                 # noqa: BLE001
            self.failed.emit(f"{type(e).__name__}: {e}\n"
                             f"{traceback.format_exc()[-600:]}")

    def _on_event(self, kind: str, payload: dict) -> None:
        # payload 里可能有 TargetOutcome 等对象，Qt signal 用 object 传即可
        self.event.emit(kind, payload)


# ---------------------------------------------------------------- 界面


class MonitorPage(QWidget):
    """监控面板：目标表格 + 操作按钮 + 进度 + 日志。"""

    def __init__(self, cfg: AppConfig, parent=None):
        super().__init__(parent)
        self.cfg = cfg
        self.worker: MonitorWorker | None = None

        root = QVBoxLayout(self)
        root.setContentsMargins(24, 24, 24, 24)
        root.setSpacing(12)

        root.addWidget(SubtitleLabel("船期监控", self))
        self.hint = BodyLabel("盯住特定船 / 码头航次，ETB·ETD 一变就告警。", self)
        root.addWidget(self.hint)

        # --- 按钮行 ---
        bar = QHBoxLayout()
        self.btn_run = PrimaryPushButton("立即检查", self)
        self.btn_token = PushButton("Token 体检", self)
        self.btn_renew = PushButton("续期 Token", self)
        for b in (self.btn_run, self.btn_token, self.btn_renew):
            bar.addWidget(b)
        bar.addStretch(1)
        root.addLayout(bar)

        self.btn_run.clicked.connect(lambda: self._start("cycle"))
        self.btn_token.clicked.connect(lambda: self._start("token"))
        self.btn_renew.clicked.connect(lambda: self._start("renew"))

        self.progress = ProgressBar(self)
        self.progress.setValue(0)
        root.addWidget(self.progress)

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
            values = [row["label"], {"ship": "船名", "voyage": "航次"}.get(row["type"], row["type"]),
                      row["voyage_code"], row["ship_name"], row["gate"],
                      row["etb"], row["etd"], row["agent"], row["last_seen"]]
            for c, v in enumerate(values):
                self.table.setItem(r, c, QTableWidgetItem(str(v)))
        self.table.resizeColumnsToContents()

    # ---------------------------------------------------------- 运行

    def _start(self, action: str) -> None:
        if self.worker and self.worker.isRunning():
            self.append_log("[skip] 上一轮还没跑完")
            return

        try:
            self.cfg = AppConfig.load(CONFIG_PATH)             # 每轮重载配置
        except FileNotFoundError:
            self._toast("错误", f"找不到 {CONFIG_PATH}", error=True)
            return

        errs = self.cfg.validate()
        if errs:
            self._toast("配置有问题", "；".join(errs[:3]), error=True)
            return

        self.progress.setValue(0)
        self.append_log(f"—— 开始 {action} ——")
        for b in (self.btn_run, self.btn_token, self.btn_renew):
            b.setEnabled(False)

        self.worker = MonitorWorker(self.cfg, action, self)
        self.worker.event.connect(self._on_event)
        self.worker.finished_ok.connect(self._on_done)
        self.worker.failed.connect(self._on_failed)
        self.worker.finished.connect(self._on_thread_finished)
        self.worker.start()

    def _on_event(self, kind: str, payload: dict) -> None:
        """在 UI 线程里执行（signal 跨线程是队列投递，安全）。"""
        if kind == "cycle_start":
            total = payload.get("total", 0)
            self.progress.setMaximum(max(1, total))
            self.progress.setValue(0)
            self.append_log(f"本轮 {total} 个目标，起始日 {payload.get('etb_time')}")
        elif kind == "target_start":
            t = payload.get("target")
            self.append_log(f"[{payload.get('index')}/{payload.get('total')}] 查询 {t.display()} …")
        elif kind == "target_done":
            o = payload["outcome"]
            self.append_log(f"    {STATUS_TEXT.get(o.status, o.status)}：{o.label}"
                            + (f"  {o.error}" if o.error else ""))
            self.progress.setValue(payload.get("index", 0))
            if o.changed:
                for v in o.voyages:
                    for c in o.changes.get(v.voyage_code, []):
                        self.append_log(f"      · {c.describe()}")
        elif kind == "token_status":
            st = payload["status"]
            self.append_log(f"token 体检：{st.describe()}")
        elif kind == "log":
            self.append_log(f"    {payload.get('message')}")

    def _on_done(self, result) -> None:
        # 三种 action 返回不同类型，分别处理
        if hasattr(result, "summary_line"):                    # CycleReport
            self.append_log(f"—— 本轮结束：{result.summary_line()} ——")
            if result.has_changes:
                self._toast("发现船期变更", result.summary_line())
            self.refresh_table()
        elif hasattr(result, "reason"):                        # TokenStatus
            st = result
            self._toast("Token 体检", st.describe(), error=not st.ok)
        else:                                                  # RenewResult
            ok = getattr(result, "ok", False)
            self._toast("续期结果", getattr(result, "detail", str(result)), error=not ok)
            if ok:
                self.cfg.save()

    def _on_failed(self, message: str) -> None:
        self.append_log(f"[错误] {message}")
        self._toast("执行失败", message[:200], error=True)

    def _on_thread_finished(self) -> None:
        for b in (self.btn_run, self.btn_token, self.btn_renew):
            b.setEnabled(True)

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
        self.monitor_page = MonitorPage(cfg, self)
        self.monitor_page.setObjectName("monitorPage")

        self.addSubInterface(self.monitor_page, None, "监控面板")
        self.navigationInterface.addItem(
            routeKey="placeholder", icon=None, text="目标管理 / 设置（待实现）",
            onClick=lambda: None, position=NavigationItemPosition.BOTTOM)

        self.resize(1080, 720)
        self.setWindowTitle(f"盐田船期监控  ·  {BINDING} / Qt {QT_VERSION}")


def main() -> int:
    try:
        cfg = AppConfig.load(CONFIG_PATH)
    except FileNotFoundError:
        print(f"找不到 {CONFIG_PATH}，请先复制 watchlist.example.json", file=sys.stderr)
        return 1

    app = QApplication(sys.argv)
    win = MainWindow(cfg)
    win.show()
    return exec_app(app)


if __name__ == "__main__":
    raise SystemExit(main())
