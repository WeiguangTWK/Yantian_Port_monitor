"""监控状态存储与变化比对。

状态文件是一个 JSON，按目标（船名或码头航次）保存"上次看到的样子"，
以便下次运行时比对 ETB/ETD 是否变动。
"""

from __future__ import annotations

import datetime as dt
import json
import pathlib
from dataclasses import dataclass

from .parse import Voyage, parse_dt

STATE_VERSION = 1
HISTORY_LIMIT = 200

# 参与比对的字段（页面列名 -> 中文标签）
WATCHED_FIELDS = {
    "voyage_code": "码头航次",
    "ship_name": "船名",
    "gate": "闸口",
    "etb_raw": "ETB",
    "etd_raw": "ETD",
    "agent": "船代",
}

# 时间类字段，变化时附带 delta
TIME_FIELDS = {"etb_raw", "etd_raw"}


@dataclass
class FieldChange:
    field: str
    label: str
    old: str
    new: str
    delta_hours: float | None = None

    def describe(self) -> str:
        text = f"{self.label} {self.old or '—'} → {self.new or '—'}"
        if self.delta_hours is not None:
            h = self.delta_hours
            sign = "+" if h >= 0 else "−"
            text += f"  ({sign}{abs(h):.1f} 小时)"
        return text


def compare(prev: dict | None, new: Voyage) -> list[FieldChange]:
    """比对上一次记录与本次记录，返回变化列表。"""
    if not prev:
        return []
    cur = new.to_dict()
    changes: list[FieldChange] = []
    for field, label in WATCHED_FIELDS.items():
        old_v = (prev.get(field) or "").strip()
        new_v = (cur.get(field) or "").strip()
        if old_v == new_v:
            continue
        delta = None
        if field in TIME_FIELDS:
            d_old, d_new = parse_dt(old_v), parse_dt(new_v)
            if d_old and d_new:
                # 站点会在"仅日期"和"日期+00:00"之间来回变，指的是同一时刻，
                # 这种精度差异不该当成船期变动来告警。
                if d_old == d_new:
                    continue
                delta = (d_new - d_old).total_seconds() / 3600.0
        changes.append(FieldChange(field=field, label=label, old=old_v, new=new_v,
                                   delta_hours=delta))
    return changes


def target_key(ttype: str, value: str) -> str:
    return f"{ttype}:{value.strip().upper()}"


class StateStore:
    def __init__(self, path: str | pathlib.Path):
        self.path = pathlib.Path(path)
        self.data: dict = {"version": STATE_VERSION, "targets": {}}

    def load(self) -> "StateStore":
        if self.path.exists():
            try:
                self.data = json.loads(self.path.read_text("utf-8"))
            except (json.JSONDecodeError, OSError):
                # 状态坏了不能静默当作"首次运行"——那会吞掉一次告警，
                # 但也绝不能中断监控，所以备份后重来。
                backup = self.path.with_suffix(self.path.suffix + ".broken")
                try:
                    self.path.replace(backup)
                except OSError:
                    pass
        self.data.setdefault("version", STATE_VERSION)
        self.data.setdefault("targets", {})
        return self

    def get(self, key: str) -> dict | None:
        return self.data["targets"].get(key)

    def record(self, key: str, ttype: str, value: str, voyage: Voyage) -> None:
        now = dt.datetime.now().isoformat(timespec="seconds")
        entry = self.data["targets"].setdefault(key, {
            "type": ttype, "value": value, "history": [],
        })
        entry["label"] = value
        entry["last_seen"] = now
        entry["record"] = voyage.to_dict()
        entry["history"].append({
            "ts": now,
            "voyage_code": voyage.voyage_code,
            "etb_raw": voyage.etb_raw,
            "etd_raw": voyage.etd_raw,
        })
        if len(entry["history"]) > HISTORY_LIMIT:
            entry["history"] = entry["history"][-HISTORY_LIMIT:]

    def mark_missing(self, key: str, note: str) -> None:
        entry = self.data["targets"].get(key)
        if entry:
            entry["last_missing"] = dt.datetime.now().isoformat(timespec="seconds")
            entry["last_missing_note"] = note

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        tmp.write_text(json.dumps(self.data, ensure_ascii=False, indent=2), "utf-8")
        tmp.replace(self.path)
