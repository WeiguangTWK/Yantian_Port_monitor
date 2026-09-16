"""目标配置编辑；保留其他 JSON 字段，拒绝覆盖外部修改。"""

from __future__ import annotations

import json
import pathlib

from ytmon.config import AppConfig, Target


class TargetConfig:
    def __init__(self, path):
        self.path = pathlib.Path(path)
        self.reload()

    def reload(self):
        self.original = self.path.read_bytes() if self.path.exists() else None
        if self.original is None:
            self.raw = AppConfig().to_dict()
            self.targets = []
        else:
            self.raw = json.loads(self.original.decode('utf-8-sig'))
            self.targets = []
            for item in self.raw.get('targets', []):
                if isinstance(item, str):
                    self.targets.append(Target('ship', item, item))
                else:
                    self.targets.append(Target(item.get('type', 'ship'),
                                               item.get('value', ''), item.get('label', ''),
                                               bool(item.get('enabled', True))))

    def save(self, targets):
        errors = AppConfig(targets=targets).validate_targets()
        if errors:
            raise ValueError('；'.join(errors))
        current = self.path.read_bytes() if self.path.exists() else None
        if current != self.original:
            raise ValueError('配置已被其他程序修改，请重新加载后再编辑。')
        raw = dict(self.raw)
        raw['targets'] = [dict(type=t.type, value=t.value, label=t.label,
                               enabled=t.enabled) for t in targets]
        self.save_raw(raw)
        self.targets = list(targets)

    def save_raw(self, raw):
        current = self.path.read_bytes() if self.path.exists() else None
        if current != self.original:
            raise ValueError('配置已被其他程序修改，请重新加载后再编辑。')
        payload = json.dumps(raw, ensure_ascii=False, indent=2).encode('utf-8')
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(self.path.suffix + '.tmp')
        temporary.write_bytes(payload)
        temporary.replace(self.path)
        self.raw, self.original = raw, payload

    def put(self, kind, value, label='', enabled=True, index=None):
        target = Target(kind, value.strip(), label.strip(), enabled)
        errors = target.validate()
        if errors:
            raise ValueError('；'.join(errors))
        for i, existing in enumerate(self.targets):
            if i != index and (existing.type, existing.value.strip().upper()) == (target.type, target.value.upper()):
                raise ValueError('目标已存在：%s%s' % (existing.display(), '' if existing.enabled else '（已停用）'))
        targets = list(self.targets)
        if index is None:
            targets.append(target)
        else:
            targets[index] = target
        self.save(targets)

    def delete(self, index):
        targets = list(self.targets)
        del targets[index]
        self.save(targets)
