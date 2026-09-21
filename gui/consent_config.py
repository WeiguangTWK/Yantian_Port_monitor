"""保存站点条款确认，不覆盖其它配置或外部并发修改。"""

from datetime import datetime

from gui.target_config import TargetConfig


def record_site_terms_acceptance(path):
    store = TargetConfig(path)
    raw = dict(store.raw)
    settings = dict(raw.get('settings') or {})
    settings['site_terms_accepted'] = True
    settings['site_terms_accepted_at'] = datetime.now().astimezone().isoformat(timespec='seconds')
    raw['settings'] = settings
    store.save_raw(raw)
