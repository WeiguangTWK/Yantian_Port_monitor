"""配置模型（供 CLI 与将来的 GUI 共用）。

GUI 会直接绑定这些 dataclass，所以：
  * 字段带默认值，缺字段的旧配置也能读
  * `to_dict()` 保证写回的 JSON 结构稳定
  * 校验集中在 `validate()`，GUI 可以在保存前调用
"""

from __future__ import annotations

import json
import pathlib
from dataclasses import asdict, dataclass, field, fields


VALID_TYPES = ("ship", "voyage")
DEFAULT_CONFIG_PATH = "watchlist.json"

# 告警通道类型。除 windows 外全部只用标准库 + requests 实现，
# 因此在 Win7 + Python 3.8 上一样能跑（不需要任何新依赖）。
# windows = 系统原生通知（托盘气泡），也是纯 ctypes，零依赖。
VALID_CHANNELS = ("webhook", "dingtalk", "wecom", "feishu", "email", "windows")
# 需要 url 的通道（email 走 SMTP、windows 走系统托盘，都不用 url）
URL_CHANNELS = ("webhook", "dingtalk", "wecom", "feishu")
# 可以作为告警触发条件的状态
VALID_ALERT_ON = ("changed", "missing", "error", "first")
DEFAULT_ALERT_ON = ("changed", "missing", "error")


@dataclass
class Target:
    type: str = "ship"                  # ship | voyage
    value: str = ""
    label: str = ""
    enabled: bool = True

    def display(self) -> str:
        return self.label or self.value or "(未命名)"

    def validate(self) -> list[str]:
        errs = []
        if self.type not in VALID_TYPES:
            errs.append(f"类型必须是 {VALID_TYPES} 之一，收到 {self.type!r}")
        if not self.value.strip():
            errs.append("监控目标不能为空")
        elif self.type == "ship" and len(self.value.strip()) < 2:
            errs.append("船名至少 2 个字符（站点限制）")
        elif self.type == "voyage" and len(self.value.strip()) < 2:
            errs.append("码头航次至少 2 个字符（站点限制）")
        return errs


@dataclass
class Settings:
    etb_back_days: int = 7
    query_interval_seconds: float = 3.0
    max_pages: int = 5
    state_file: str = "state/voyage_state.json"
    profile_dir: str = ".browser_profile"
    edge_path: str | None = None
    headless: bool = True
    close_to_tray: bool = False             # GUI 关闭窗口后驻留托盘
    cookie_cache: str = ".cache/cookies.json"
    watch_interval_seconds: int = 600        # 循环监控间隔，GUI 用

    # ---- 抗抖动 ----
    # 被限流（HTTP 567/429/503）时的退避重试次数。**不会**触发浏览器重引导。
    retry_attempts: int = 2
    retry_backoff_seconds: float = 20.0
    # 循环监控每轮额外随机等待的上限（秒）。避免计划任务/循环卡在同一相位上
    # 集体打到站点 —— 那比固定间隔更像攻击。
    watch_jitter_seconds: float = 30.0

    # ---- 心跳 / 停摆检测 ----
    # 每隔这么多小时发一条"我还活着"。0 = 关闭。
    # 为什么需要：如果监控本身死了（机器关机、任务被禁用、站点改版），
    # 你会收到**一片安静** —— 而安静和"船期没变化"长得一模一样。
    heartbeat_hours: float = 0.0
    # 距上次成功超过这么多小时就告警（机器重新开机后能立刻发现中间断过）。
    stale_after_hours: float = 0.0
    # 每轮 ping 一下这个 URL（外部 dead-man 服务）。
    # 只有它能发现"监控**再也没跑过**"—— 本地检测在进程根本没起来时是无声的。
    heartbeat_url: str = ""
    heartbeat_state_file: str = "state/heartbeat.json"

    # 配置文件里出现了但本类不认识的键（拼写错误）。不写回文件，只在校验时报出来。
    # 静默忽略拼错的键是很危险的：用户以为配上了，其实没生效，
    # 而且要等到"告警怎么没来"才发现。
    unknown_keys: list[str] = field(default_factory=list, compare=False, repr=False)

    # ---- 告警策略 ----
    # 只在"真的该叫人"的时候发消息；无变化/首次默认静默。
    alert_on: list[str] = field(default_factory=lambda: list(DEFAULT_ALERT_ON))
    # 同一个目标的同一种问题，冷却期内不重复发（否则船一离港会每轮刷屏）
    alert_cooldown_seconds: int = 3600
    alert_state_file: str = "state/alert_state.json"
    # 每轮最多发几条告警，防止首次运行时一次性轰炸
    alert_max_per_cycle: int = 5
    # 查询失败要连续几轮才告警。设成 1 = 第一次失败就叫。
    # 默认 2：实测站点会偶发降级/限流（HTTP 567、会话被弹回），
    # 一次失败就叫人等于狼来了，用不了多久告警就会被无视。
    alert_error_after: int = 2

    def validate(self) -> list[str]:
        # 注意：unknown_keys（拼写错误）不在这里报 ——
        # 那是"配置完整性"问题，统一由 AppConfig.validate_unknown_keys() 负责。
        # 两边都报会得到两条一模一样的错误（真出现过）。
        errs = []
        if not isinstance(self.close_to_tray, bool):
            errs.append("close_to_tray 必须为布尔值")
        if self.etb_back_days < 0:
            errs.append("etb_back_days 不能为负")
        if self.query_interval_seconds < 0:
            errs.append("查询间隔不能为负")
        if self.max_pages < 1:
            errs.append("max_pages 至少为 1")
        if self.alert_cooldown_seconds < 0:
            errs.append("alert_cooldown_seconds 不能为负")
        if self.alert_max_per_cycle < 1:
            errs.append("alert_max_per_cycle 至少为 1")
        if self.alert_error_after < 1:
            errs.append("alert_error_after 至少为 1")
        if self.retry_attempts < 0:
            errs.append("retry_attempts 不能为负")
        if self.retry_backoff_seconds < 0:
            errs.append("retry_backoff_seconds 不能为负")
        if self.watch_jitter_seconds < 0:
            errs.append("watch_jitter_seconds 不能为负")
        if self.heartbeat_hours < 0 or self.stale_after_hours < 0:
            errs.append("heartbeat_hours / stale_after_hours 不能为负")
        if self.heartbeat_url and not self.heartbeat_url.startswith(("http://", "https://")):
            errs.append("heartbeat_url 必须是 http:// 或 https:// 开头")
        for name in self.alert_on:
            if name not in VALID_ALERT_ON:
                errs.append(f"alert_on 里有无法识别的状态 {name!r}，"
                            f"可选：{VALID_ALERT_ON}")
        return errs


@dataclass
class NotifyChannel:
    """一个告警出口。

    `kind` 决定怎么发，其余字段按需填。凭证（webhook 的 access_token、
    邮箱密码）都写在这个文件里 —— `watchlist.json` 已被 .gitignore 忽略，
    打包脚本也会把它整个排除，所以不会外泄。
    """

    kind: str = "webhook"
    enabled: bool = True
    url: str = ""
    secret: str = ""                     # dingtalk / feishu 加签密钥
    label: str = ""                      # 出错时用来指认是哪个通道

    # email 专用
    smtp_host: str = ""
    smtp_port: int = 465
    smtp_user: str = ""
    smtp_password: str = ""
    mail_from: str = ""
    mail_to: list[str] = field(default_factory=list)
    use_ssl: bool = True

    # windows 通道专用：托盘图标的消息处理时长；实际气泡时长由系统决定。
    hold_seconds: float = 5.0
    # GUI 使用独立浮窗，直到手动关闭或程序退出。
    persistent: bool = False

    # 配置文件里出现了但本类不认识的键。**不写回文件**，只在校验时报出来。
    #
    # 为什么需要：JSON 里拼错一个键名（`rtery_attempts`、`kinds`）本来会被
    # 静默忽略，用户以为配上了、其实没生效 —— 而且往往要等到"告警没来"
    # 才发现。这类问题在配置项变多之后特别容易发生，所以必须主动报出来。
    unknown_keys: list[str] = field(default_factory=list, compare=False, repr=False)

    def display(self) -> str:
        return self.label or f"{self.kind} 通道"

    def validate(self) -> list[str]:
        # unknown_keys 不在这里报，理由同 Settings.validate()。
        errs = []
        if self.kind not in VALID_CHANNELS:
            errs.append(f"通道类型必须是 {VALID_CHANNELS} 之一，收到 {self.kind!r}")
            return errs
        if self.kind in URL_CHANNELS and not self.url.strip():
            errs.append(f"{self.kind} 通道缺少 url")
        if self.kind == "windows" and self.url.strip():
            # 只有 windows 是"有 url 一定是搞错了"的通道，明确拦下来，
            # 免得用户以为那个 url 起了作用。
            errs.append("windows 通道不用 url（它走系统托盘），填了会被忽略；"
                        "要发到别处请另加一个通道")
        if self.kind == "email":
            if not self.smtp_host.strip():
                errs.append("email 通道缺少 smtp_host")
            if not self.mail_to:
                errs.append("email 通道缺少 mail_to")
            if self.smtp_port <= 0:
                errs.append("email 通道的 smtp_port 不合法")
        if self.kind == "windows" and self.hold_seconds < 0:
            errs.append("windows 通道的 hold_seconds 不能为负")
        if not isinstance(self.persistent, bool):
            errs.append("persistent 必须为布尔值")
        elif self.persistent and self.kind != "windows":
            errs.append("persistent 仅适用于 windows 通道")
        return errs


# 注意：这里必须用 dataclasses.fields()，不能用 hasattr(NotifyChannel, name)。
# 带 default_factory 的字段（比如 mail_to）在类上**没有**类属性，
# hasattr 会返回 False，于是 JSON 里的值被静默丢弃 —— 这个坑真踩过一次。
#
# 内部记账字段（unknown_keys）必须排除：它不该能从 JSON 里设，
# 也不该算作"认识的字段"，否则拼错的名字会被误认为合法。
_INTERNAL_FIELDS = frozenset({"unknown_keys"})
_CHANNEL_FIELDS = frozenset(f.name for f in fields(NotifyChannel)
                            if f.name not in _INTERNAL_FIELDS)


def _channel_to_dict(c: NotifyChannel) -> dict:
    """序列化通道，省掉空字段，让写回的 JSON 干净可读。"""
    out: dict = {"kind": c.kind}
    if not c.enabled:
        out["enabled"] = False
    if c.label:
        out["label"] = c.label
    if c.url:
        out["url"] = c.url
    if c.secret:
        out["secret"] = c.secret
    for k in ("smtp_host", "smtp_user", "smtp_password", "mail_from"):
        v = getattr(c, k)
        if v:
            out[k] = v
    if c.kind == "windows" and c.hold_seconds != 5.0:
        out["hold_seconds"] = c.hold_seconds
    if c.kind == "windows" and c.persistent:
        out["persistent"] = True
    if c.kind == "email":
        out["smtp_port"] = c.smtp_port
        out["use_ssl"] = c.use_ssl
        if c.mail_to:
            out["mail_to"] = list(c.mail_to)
    return out


@dataclass
class AppConfig:
    targets: list[Target] = field(default_factory=list)
    settings: Settings = field(default_factory=Settings)
    notify: list[NotifyChannel] = field(default_factory=list)
    path: pathlib.Path | None = None

    # ---------------------------------------------------------- 读写

    @classmethod
    def load(cls, path: str | pathlib.Path = DEFAULT_CONFIG_PATH) -> "AppConfig":
        p = pathlib.Path(path)
        if not p.exists():
            raise FileNotFoundError(f"找不到配置文件 {p}")
        raw = json.loads(p.read_text("utf-8"))

        targets = []
        for t in raw.get("targets", []):
            if isinstance(t, str):                       # 容忍 ["MSC IRINA"] 这种简写
                targets.append(Target("ship", t, t))
            else:
                targets.append(Target(
                    type=t.get("type", "ship"),
                    value=t.get("value", ""),
                    label=t.get("label", ""),
                    enabled=bool(t.get("enabled", True)),
                ))

        s = Settings()
        unknown_settings = []
        for k, v in (raw.get("settings") or {}).items():
            if k == "precheck":
                continue  # 旧配置兼容；GET 预检查已移除。
            if hasattr(s, k):
                setattr(s, k, v)
            elif not k.startswith("_"):
                # 以 _ 开头的是注释键，不当成拼写错误
                unknown_settings.append(k)
        s.unknown_keys = unknown_settings

        notify: list[NotifyChannel] = []
        for c in (raw.get("notify") or []):
            if isinstance(c, str):                       # 容忍 "https://..." 简写
                notify.append(NotifyChannel(kind="webhook", url=c))
                continue
            if not isinstance(c, dict):
                continue
            kwargs = {k: v for k, v in c.items() if k in _CHANNEL_FIELDS}
            ch = NotifyChannel(**kwargs)
            ch.unknown_keys = [k for k in c
                               if k not in _CHANNEL_FIELDS and not k.startswith("_")]
            notify.append(ch)

        return cls(targets=targets,
                   settings=s, notify=notify, path=p)

    def to_dict(self) -> dict:
        # 保持与手写配置相近的可读性。
        # 去掉内部记账字段（unknown_keys），它不该被写回文件。
        settings_d = {k: v for k, v in asdict(self.settings).items()
                      if k not in _INTERNAL_FIELDS}
        out = {"targets": [], "settings": settings_d}
        for t in self.targets:
            item = {"type": t.type, "value": t.value}
            if t.label:
                item["label"] = t.label
            if not t.enabled:
                item["enabled"] = False
            out["targets"].append(item)
        if self.notify:
            out["notify"] = [_channel_to_dict(c) for c in self.notify]
        return out

    def save(self, path: str | pathlib.Path | None = None) -> pathlib.Path:
        p = pathlib.Path(path) if path else self.path
        if p is None:
            raise ValueError("没有指定配置文件路径")
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(p.suffix + ".tmp")
        tmp.write_text(json.dumps(self.to_dict(), ensure_ascii=False, indent=2), "utf-8")
        tmp.replace(p)
        self.path = p
        return p

    # ---------------------------------------------------------- 校验

    def validate(self) -> list[str]:
        errs = (self.validate_unknown_keys()
                + self.settings.validate()
                + self.validate_targets()
                + self.validate_channels())
        return errs

    def validate_unknown_keys(self) -> list[str]:
        """只查"配置文件里有不认识的键"。

        单独拆出来，是因为 `--test-alert` 需要在**还没配好监控目标**时
        也能先把拼写错误报出来 —— 之前那版只校验通道，导致 settings 里的
        拼写错误在自检时被静默跳过，用户永远发现不了。
        """
        errs: list[str] = []
        if self.settings.unknown_keys:
            errs.append(f"settings 里有无法识别的设置项："
                        f"{', '.join(self.settings.unknown_keys)}"
                        f"（可能是拼写错误，这些项不会生效）")
        for i, c in enumerate(self.notify, 1):
            if c.unknown_keys:
                errs.append(f"第 {i} 个告警通道有无法识别的字段："
                            f"{', '.join(c.unknown_keys)}"
                            f"（可能是拼写错误，这些字段不会生效）")
        return errs

    def validate_targets(self) -> list[str]:
        errs: list[str] = []
        for i, t in enumerate(self.targets, 1):
            for e in t.validate():
                errs.append(f"第 {i} 个目标：{e}")
        seen = {}
        for t in self.targets:
            key = (t.type, t.value.strip().upper())
            if key in seen:
                errs.append(f"重复的监控目标：{t.value}")
            seen[key] = True
        return errs

    def validate_channels(self) -> list[str]:
        """只校验告警通道。

        `--test-alert` 用它：还没填监控目标时也该能先测通道通不通。
        """
        errs = []
        for i, c in enumerate(self.notify, 1):
            for e in c.validate():
                errs.append(f"第 {i} 个告警通道：{e}")
        return errs

    @property
    def enabled_targets(self) -> list[Target]:
        return [t for t in self.targets if t.enabled and t.value.strip()]

    @property
    def enabled_channels(self) -> list[NotifyChannel]:
        return [c for c in self.notify if c.enabled]
