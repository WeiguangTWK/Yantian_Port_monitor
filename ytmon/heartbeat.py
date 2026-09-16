"""心跳与停摆检测 —— "谁来监控监控者"。

## 为什么必须有这个

告警工具的经典空洞：**如果监控本身死了，你会收到一片安静，
而安静和"船期没变化"长得一模一样。**

具体的死法有很多种，而且都不产生任何输出：

  * Win7 那台机器关机了 / 断电了
  * 计划任务被禁用、被安全软件拦了
  * Python 启动就报错（比如哪天有人动了依赖）
  * 站点改版导致每轮都失败，而失败恰好没被报出来

## 两种机制，能力**不一样**（这点必须说清楚）

**1. 本地停摆检测（`stale_after_hours`）**
   每轮把"上次成功的时刻"写进状态文件；下次运行时若发现间隔超阈值就告警。
   它能回答："这中间断过 4 个小时。"
   **它回答不了**："监控再也没跑过" —— 因为进程没起来就没有"下次运行"。

**2. 外部 ping（`heartbeat_url`）**
   每轮往一个外部地址发一次请求（如 healthchecks.io 之类的 dead-man 服务），
   那个服务负责"到点没收到就报警"。
   **这是唯一能发现"再也没跑过"的办法**，因为判定必须发生在进程之外。

所以：想真正兜住"监控死了"，得配 `heartbeat_url`。
只配本地检测，能兜住"重启后补报中断"，兜不住"永远不回来了"。

这里不引入任何新依赖：ping 用 requests，时间用标准库。
"""

from __future__ import annotations

import datetime as dt
import json
import pathlib
from dataclasses import dataclass

DEFAULT_HEARTBEAT_STATE = "state/heartbeat.json"
STATE_VERSION = 1


@dataclass
class HeartbeatState:
    """跨轮次记住"上次成功是什么时候"。"""

    path: pathlib.Path
    last_success: str = ""          # ISO 时间戳
    last_heartbeat: str = ""
    consecutive_failures: int = 0

    @classmethod
    def load(cls, path: str | pathlib.Path) -> "HeartbeatState":
        p = pathlib.Path(path)
        data: dict = {}
        try:
            raw = json.loads(p.read_text("utf-8"))
            if isinstance(raw, dict):
                data = raw
        except (OSError, ValueError):
            data = {}               # 读不出来就当没有，绝不因此中断监控
        return cls(
            path=p,
            last_success=str(data.get("last_success") or ""),
            last_heartbeat=str(data.get("last_heartbeat") or ""),
            consecutive_failures=int(data.get("consecutive_failures") or 0),
        )

    def save(self) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            payload = {
                "version": STATE_VERSION,
                "last_success": self.last_success,
                "last_heartbeat": self.last_heartbeat,
                "consecutive_failures": self.consecutive_failures,
            }
            tmp = self.path.with_suffix(self.path.suffix + ".tmp")
            tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), "utf-8")
            tmp.replace(self.path)
        except OSError:
            pass                    # 写不下就算了 —— 心跳是附属品，不能拖垮监控

    # ---------------------------------------------------------- 时间

    def hours_since_success(self, now: dt.datetime | None = None) -> float | None:
        """距上次成功过了多少小时。从没成功过返回 None。"""
        if not self.last_success:
            return None
        try:
            then = dt.datetime.fromisoformat(self.last_success)
        except ValueError:
            return None
        now = now or dt.datetime.now()
        return (now - then).total_seconds() / 3600.0

    def hours_since_heartbeat(self, now: dt.datetime | None = None) -> float | None:
        if not self.last_heartbeat:
            return None
        try:
            then = dt.datetime.fromisoformat(self.last_heartbeat)
        except ValueError:
            return None
        now = now or dt.datetime.now()
        return (now - then).total_seconds() / 3600.0

    # ---------------------------------------------------------- 判定

    def note_success(self, now: dt.datetime | None = None) -> None:
        self.last_success = (now or dt.datetime.now()).isoformat(timespec="seconds")
        self.consecutive_failures = 0
        self.save()

    def note_failure(self) -> int:
        self.consecutive_failures += 1
        self.save()
        return self.consecutive_failures

    def stale_reason(self, stale_after_hours: float,
                     now: dt.datetime | None = None) -> str:
        """超过了阈值就返回一句可读的原因，否则返回空串。"""
        if stale_after_hours <= 0:
            return ""
        gap = self.hours_since_success(now)
        if gap is None or gap <= stale_after_hours:
            return ""
        return (f"距上次成功查询已过 {gap:.1f} 小时"
                f"（阈值 {stale_after_hours:.1f} 小时）—— 监控中途停摆过")

    def heartbeat_due(self, heartbeat_hours: float,
                      now: dt.datetime | None = None) -> bool:
        if heartbeat_hours <= 0:
            return False
        gap = self.hours_since_heartbeat(now)
        return gap is None or gap >= heartbeat_hours

    def mark_heartbeat(self, now: dt.datetime | None = None) -> None:
        self.last_heartbeat = (now or dt.datetime.now()).isoformat(timespec="seconds")
        self.save()


def ping(url: str, timeout: float = 15.0) -> tuple[bool, str]:
    """往外部 dead-man 服务发一次请求。

    失败**绝不能**影响监控 —— 它只是个旁路信号。
    """
    if not url:
        return False, "未配置"
    try:
        import requests
        r = requests.get(url, timeout=timeout,
                         headers={"User-Agent": "ytmon/0.2"})
        if 200 <= r.status_code < 300:
            return True, f"HTTP {r.status_code}"
        return False, f"HTTP {r.status_code}"
    except Exception as e:                                  # noqa: BLE001
        return False, f"{type(e).__name__}: {e}"
