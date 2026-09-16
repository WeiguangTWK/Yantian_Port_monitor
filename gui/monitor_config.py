"""监听设置读写；仅更新页面支持的字段。"""

from __future__ import annotations

import math

from gui.target_config import TargetConfig
from ytmon.config import Settings


# 字段 -> (下限, 上限, 是否整数)
NUMERIC_FIELDS = {
    'watch_interval_seconds': (5, 2147483647, True),
    'watch_jitter_seconds': (0, 86400, False),
    'etb_back_days': (0, 3650, True),
    'max_pages': (1, 10000, True),
    'retry_attempts': (0, 100, True),
    'retry_backoff_seconds': (0, 86400, False),
}
EDITABLE_FIELDS = frozenset(NUMERIC_FIELDS) | {'edge_path', 'headless'}


class MonitorConfig(TargetConfig):
    def values(self):
        settings = self.raw.get('settings') or {}
        defaults = Settings()
        values = {key: settings.get(key, getattr(defaults, key)) for key in EDITABLE_FIELDS}
        self.validate_values(values)
        return values

    @staticmethod
    def validate_values(values):
        if set(values) != EDITABLE_FIELDS:
            raise ValueError('监听设置字段不完整或包含未知字段。')
        for key, (minimum, maximum, integer) in NUMERIC_FIELDS.items():
            value = values[key]
            expected = (int,) if integer else (int, float)
            if isinstance(value, bool) or not isinstance(value, expected) or not math.isfinite(value):
                raise ValueError('%s 必须为%s。' % (key, '整数' if integer else '数值'))
            if not minimum <= value <= maximum:
                raise ValueError('%s 必须在 %s 到 %s 之间。' % (key, minimum, maximum))
        if not isinstance(values['headless'], bool):
            raise ValueError('headless 必须为布尔值。')
        path = values['edge_path']
        if path is not None and not isinstance(path, str):
            raise ValueError('浏览器路径必须为字符串或 null。')

    def save_values(self, values):
        self.validate_values(values)
        values = dict(values)
        path = values['edge_path']
        values['edge_path'] = (path.strip() or None) if path else None
        raw = dict(self.raw)
        raw['settings'] = dict(raw.get('settings') or {})
        raw['settings'].update(values)
        self.save_raw(raw)
