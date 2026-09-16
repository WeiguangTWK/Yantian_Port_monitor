"""通知渠道与触发策略配置，仅改动相关 JSON 字段。"""

from __future__ import annotations

from dataclasses import fields
import math
from urllib.parse import parse_qs, urlsplit

from gui.target_config import TargetConfig
from ytmon.config import NotifyChannel, Settings, VALID_ALERT_ON, URL_CHANNELS

CHANNEL_FIELDS = frozenset(field.name for field in fields(NotifyChannel) if field.name != 'unknown_keys')
POLICY_RANGES = {'alert_error_after': (1, 10000), 'alert_cooldown_seconds': (0, 2147483647),
                 'alert_max_per_cycle': (1, 10000)}


def validate_channel(channel):
    for key in ('kind', 'label', 'url', 'secret', 'smtp_host', 'smtp_user', 'smtp_password', 'mail_from'):
        if not isinstance(getattr(channel, key), str):
            raise ValueError('%s 必须为字符串。' % key)
    if any(not isinstance(getattr(channel, key), bool) for key in ('enabled', 'use_ssl', 'persistent')):
        raise ValueError('启停、SSL 和保持显示选项必须为布尔值。')
    if not isinstance(channel.mail_to, list) or any(not isinstance(address, str) for address in channel.mail_to):
        raise ValueError('收件人必须为邮箱地址列表。')
    if isinstance(channel.smtp_port, bool) or not isinstance(channel.smtp_port, int):
        raise ValueError('SMTP 端口必须为整数。')
    if isinstance(channel.hold_seconds, bool) or not isinstance(channel.hold_seconds, (int, float)) or not math.isfinite(channel.hold_seconds) or not 0 <= channel.hold_seconds <= 300:
        raise ValueError('通知停留时间必须在 0 到 300 秒之间。')
    errors = channel.validate()
    if channel.kind in URL_CHANNELS:
        try:
            parsed = urlsplit(channel.url)
            valid = parsed.scheme in ('http', 'https') and bool(parsed.hostname)
        except ValueError:
            valid = False
        if not valid:
            errors.append('通知地址必须是有效的 HTTP / HTTPS URL。')
    if channel.kind == 'email':
        if not 1 <= channel.smtp_port <= 65535:
            errors.append('SMTP 端口必须在 1 到 65535 之间。')
        if any('@' not in address or not address.strip() for address in channel.mail_to):
            errors.append('请填写有效的收件人邮箱，多个地址以逗号分隔。')
    if errors:
        raise ValueError('；'.join(errors))


def redact(message, channels):
    """请求异常可能包含完整机器人地址，推送结果显示前进行遮罩。"""
    values = set()
    for channel in channels:
        values.update(value for value in (channel.url, channel.secret, channel.smtp_password) if value)
        if channel.url:
            try:
                parsed = urlsplit(channel.url)
                for key, items in parse_qs(parsed.query).items():
                    if any(marker in key.lower() for marker in ('token', 'key', 'secret', 'sign', 'password')):
                        values.update(value for value in items if value)
                if channel.kind == 'feishu':
                    values.add(parsed.path.rstrip('/').split('/')[-1])
            except ValueError:
                pass
    message = str(message)
    for value in sorted(values, key=len, reverse=True):
        if value:
            message = message.replace(value, '***')
    return message


class NotificationConfig(TargetConfig):
    @property
    def channels(self):
        channels = []
        for item in self.raw.get('notify') or []:
            if isinstance(item, str):
                channels.append(NotifyChannel(kind='webhook', url=item))
            elif isinstance(item, dict):
                channels.append(NotifyChannel(**{key: value for key, value in item.items() if key in CHANNEL_FIELDS}))
            else:
                raise ValueError('通知配置中存在无法识别的渠道。')
        return channels

    def policy(self):
        settings = self.raw.get('settings') or {}
        defaults = Settings()
        policy = {key: settings.get(key, getattr(defaults, key)) for key in POLICY_RANGES}
        policy['alert_on'] = list(settings.get('alert_on', defaults.alert_on))
        self.validate_policy(policy)
        return policy

    @staticmethod
    def validate_policy(policy):
        if set(policy) != set(POLICY_RANGES) | {'alert_on'}:
            raise ValueError('通知策略字段不完整或包含未知字段。')
        for key, (minimum, maximum) in POLICY_RANGES.items():
            value = policy[key]
            if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
                raise ValueError('%s 必须为 %s 到 %s 之间的整数。' % (key, minimum, maximum))
        states = policy['alert_on']
        if not isinstance(states, list) or not states or any(state not in VALID_ALERT_ON for state in states):
            raise ValueError('请至少选择一种有效触发状态；暂停通知可停用所有渠道。')

    def save_policy(self, policy):
        self.validate_policy(policy)
        raw = dict(self.raw)
        raw['settings'] = dict(raw.get('settings') or {})
        raw['settings'].update(policy)
        self.save_raw(raw)

    def put_channel(self, channel, index=None):
        validate_channel(channel)
        items = list(self.raw.get('notify') or [])
        existing = items[index] if index is not None else {}
        item = dict(existing) if isinstance(existing, dict) else {}
        item.update({key: getattr(channel, key) for key in CHANNEL_FIELDS})
        if index is None:
            items.append(item)
        else:
            items[index] = item
        raw = dict(self.raw)
        raw['notify'] = items
        self.save_raw(raw)

    def delete_channel(self, index):
        items = list(self.raw.get('notify') or [])
        del items[index]
        raw = dict(self.raw)
        raw['notify'] = items
        self.save_raw(raw)
