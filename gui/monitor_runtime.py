"""与 Qt 无关的倒计时和目标状态呈现。"""

from __future__ import annotations

import math
import random
import time


class Countdown:
    def __init__(self, clock=time.monotonic, uniform=random.uniform):
        self.clock = clock
        self.uniform = uniform
        self.deadline = None
        self.duration = 0

    def reset(self, interval, jitter=0):
        self.validate(interval, jitter)
        interval = float(interval)
        jitter = float(jitter)
        self.duration = interval + self.uniform(0, jitter)
        if not math.isfinite(self.duration):
            raise ValueError('监听等待时间过大。')
        self.deadline = self.clock() + self.duration

    @staticmethod
    def validate(interval, jitter=0):
        if isinstance(interval, bool) or isinstance(jitter, bool):
            raise ValueError('监听间隔和随机等待必须为数值。')
        interval, jitter = float(interval), float(jitter)
        if not math.isfinite(interval) or interval < 5 or not math.isfinite(jitter) or jitter < 0:
            raise ValueError('监听间隔至少 5 秒，随机等待必须为非负有限数值。')

    def clear(self):
        self.deadline = None

    @property
    def remaining(self):
        return max(0, self.deadline - self.clock()) if self.deadline is not None else 0

    @property
    def due(self):
        return self.deadline is not None and self.remaining <= 0

    @property
    def bar_value(self):
        return round(1000 * min(1, self.remaining / self.duration)) if self.deadline is not None else 0


def row_status(enabled, status='', last_seen=''):
    if not enabled:
        return '已停用'
    if status == 'updating':
        return '正在更新'
    if status == 'error':
        return '查询失败'
    if status == 'missing':
        return '不存在'
    return '上次核对: %s' % (last_seen or '尚未查询')
