# 盐田码头船期监控（ytmon）

盯住**特定船**（如 `MSC IRINA`）或**码头航次**（如 `GJ634W`），定时查询盐田港公共船期，
比对 **ETB（预计停靠）/ ETD（预计离港）** 的变化并告警 —— 目的是在船期变动造成问题**之前**发现它。

---

## 快速开始

```powershell
# 1) 配置监控目标（token 不用填，见"令牌：其实不需要"）
Copy-Item watchlist.example.json watchlist.json
notepad watchlist.json

# 2) 跑一轮
python run_monitor.py

# 3) 持续监控，每 10 分钟一轮
python run_monitor.py --watch 600

# 4) 配了告警通道的话，先验一下通不通
python run_monitor.py --test-alert
```

输出示例：

```
—— 船期监控 2026-09-14 15:11:40 | 目标 2 个 ——
[变更] MSC IRINA
        航次 GJ634W | MSC IRINA | 闸口 B | ETB 2026-09-21 | ETD 2026-09-22 | 船代 外运
        · ETB 2026-09-20 → 2026-09-21  (+24.0 小时)
[无变化] KN637A：航次 KN637A | ETB 2026-09-14 21:00 | ETD 2026-09-16 04:00
—— 本轮结束：变更 1 / 首次 0 / 无变化 1 / 未查到 0 ——
```

**退出码**：`0` 无变更 · `10` 有变更 · `1` 错误 · `2` token 失效 · `3` 查询失败
（`10` 是刻意设计的：计划任务/监控系统可据此立刻触发告警）

---

## 工作原理

站点前面有**腾讯 EdgeOne 的 JS 挑战**，纯 HTTP 客户端（`requests`/`curl`）会被降级成一个混淆 JS 页面。

实测发现：**只要拿到 `EO-Bot-Js-Token` 这一个 cookie，纯 HTTP 就能完成查询**。
所以采用混合架构：

```
[浏览器] --一次--> EO-Bot-Js-Token --> [纯 HTTP] --每分钟级--> 查询/体检
  ↑ 只在 cookie 失效时重新拉一次（约 10 秒）
```

只用 `aiohttp` + `requests`（均已装）+ 标准库，**不需要 `pip install`，也不需要下载浏览器**。

| 操作 | 浏览器方案 | 本方案 |
|---|---|---|
| 每轮监控（2 个目标） | ~22 秒 | **~1.8 秒** |
| token 体检 | — | **~0.4 秒** |

### 抓到的接口（2026-09-14 实测）

```
POST https://www.156yt.cn/pqs_revision/pages/jsp/voyQuery.jsp?modify=query
  pageCurrent=1
  etb_time=20260907      查询起始日 YYYYMMDD（返回其后约 30 天窗口）
  ship_name=MSC IRINA    船名，子串匹配，≥2 字符
  voyage_code=           码头航次，非空时 ≥2 字符（与船名不可同时为空）
  Submit1=查询
```

返回 `text/html;charset=gb2312`，6 列：码头航次 / 船名 / 闸口 / 预计停靠（ETB）/ 预计离港（ETD）/ 船代，
每页 50 条。

> 详细侦察过程与页面证据见 `RECON-盐田船期.md`。

---

## 目录结构

```
ytmon/
  cdp.py          异步 CDP 原语（驱动本机 Edge 过 EdgeOne 挑战）
  http_client.py  同步 HTTP：cookie 引导 + token 体检 + 查询   ← 日常走这条
  auth.py         登录与 token 自动续期（两个会话，见下）
  parse.py        结果页解析（GB18030、日期双格式、分页元数据）
  matching.py     船名/航次匹配规则（区分精确/模糊命中）
  store.py        状态存储与变化比对
  notify.py       告警出口（Windows 通知/webhook/钉钉/企微/飞书/邮件，零新依赖）
  winnotify.py    Windows 原生通知（Shell_NotifyIcon，纯 ctypes）
  heartbeat.py    心跳与停摆检测（谁来监控监控者）
  config.py       配置模型（dataclass，GUI 可直接绑定）
  console.py      控制台编码兜底（中文 Windows 重定向不再崩在 ✓ 上）
  service.py      ★ UI 无关的编排层 —— GUI 基座
  cli.py          命令行呈现层
  errors.py       统一异常
  browser_find.py 浏览器定位（纯标准库，裸机器可用）
tools/
  env_check.py    环境自检（逐层定位卡在哪一步）
  fake_webhook.py 本机告警接收端（不用真机器人就能验证告警链路）
  watch_token.py  token 寿命观测器
  renew_token.py  手动触发续期
  make_bundle.py  打包给 Win7 的 zip（含凭证扫描）
gui/
  app.py          GUI 骨架（Win10/11 用 PySide6；Win7 用 PySide2）
  qt_compat.py    Qt 绑定兼容层（同一套界面代码跑两种绑定）
tests/            237 项离线测试（不联网；有一条会真起一次无头浏览器）
docs/
  GUI-技术选型与基座.md
  Win7-实机验证指南.md
```

---

## Windows 中文环境的一个坑（已修）

中文 Windows（代码页 936）下，**把输出重定向到文件**会走一条和平时不同的路径：

```
python tools\env_check.py > log.txt
```

* 输出到**真控制台**：Python 用宽字符 API 写，`✓ ✗` 正常显示
* 输出被**重定向**：Python 按 locale 的 `cp936` 编码，而 `✓ ✗` 不在 GBK 里
  → `UnicodeEncodeError`，**自检直接崩在半路**

这个坑特别刁钻：报错完全看不出跟编码有关，而且"存成文件发我看看"恰恰就是重定向。
现在 `ytmon/console.py` 会在检测到重定向时切到 UTF-8，日志可直接打开、可直接贴。
`tests/test_console.py` 用**真子进程**守着它（先断言不修会崩，再断言修了不崩）。

---

## 令牌：其实不需要

**这是本项目最重要的一个实测结论。**

`loginVerifyCode` 对「船期公众查询」**完全不必要**。干净对照实验（每个条件都用全新浏览器档案）：

| 条件 | 引导 | 查询 |
|---|---|---|
| 真实 token | ✅ | ✅ 真实数据 |
| 伪造 token | ✅ | ✅ 真实数据 |
| 空 token | ✅ | ✅ 真实数据 |
| **完全不带该参数** | ✅ | ✅ 真实数据 |

**真正的门槛只有一个：用浏览器过一次 EdgeOne 的 JS 挑战**（拿到 `EO-Bot-Js-Token`），
而这一步是程序自动完成的。所以：

- ✅ **不需要账号，不需要 token，不需要保管任何凭证**
- ✅ 没有"token 过期"这个问题
- ✅ `watchlist.json` 的 `token` 字段留空即可

配置里仍保留 `token` 字段，以及 `--token` / `YT_TOKEN`，只是为了兼容
"确实想指定某个 token"的场景；填了也不影响什么。

> 获取/使用 token 的原始说明、以及"为什么最初会误判成必须登录"，
> 见 `RECON-盐田船期.md` 第 2.4 节。

⚠️ **但仍要守规矩**：公众查询是公开服务，**限流是真实存在的**（实测触发过 HTTP 567）。
请保持低频率、单并发。若要高频或需要更多字段，正路是申请官方的**船期信息订阅接口**。

### 自愈（默认开启）

既然"token 过期"探测不出来（伪造 token 一样能拿到查询页），设计上就不依赖它，
改成**只在真实查询失败时自愈**：

```
查询失败
  ├─ 第 1 级：重新引导 cookie（解决 WAF/会话问题）→ 重试
  └─ 第 2 级：仍失败 → 续期 token（需配置凭证）→ 重试
```

**预检查只在中止于"必然失败"的两类问题上生效**（WAF / 网络不通）。
原因是实测发现 `requests` 的 GET 会稳定落到首页，而同一会话的 POST 查询却完全正常 ——
**页面对不对不是可靠判据，唯一可信的信号是查询本身。**

```powershell
# 凭证存进 Windows 凭据管理器（推荐，密码不落明文）
$env:YT_USER = "你的用户名"; $env:YT_PASS = "你的密码"
python tools\renew_token.py --save

# 之后正常跑就行，续期是自动的
python run_monitor.py
python run_monitor.py --no-auto-renew    # 需要时可关掉自愈
```

**两级自愈的顺序是刻意的**：能靠重引导 cookie 解决，就不浪费一次账号密码校验
（也避免频繁登录触发风控）。这条判断有测试守着（`tests/test_service.py`）。

续期得到的新 token：
- 配置里**原本就有** token → 写回配置文件
- 原本没有（走 `YT_TOKEN` 环境变量）→ 只留在内存，**不会**偷偷把凭证落进文件

⚠️ **续期为什么是"两个会话"**（实测踩过的坑）：
已登录的会话访问 `voyQuery.jsp` 反而会被弹回首页，而新 token 在**干净会话**里才有效。
所以 `auth.py` 用会话 A 登录拿 token，查询仍由只带 cookie 的会话 B 负责。

### 观测 token 寿命

```powershell
python tools/watch_token.py --interval 60
```

每分钟体检一次，写入 `state/token_watch.jsonl`，确认失效后输出结论到
`state/token_watch_summary.json`。

设计上刻意区分了三件事，避免误判：
- **cookie 过期导致的 WAF 拦截 ≠ token 过期** —— 遇到 WAF 会先重新引导 cookie 再复检
- **网络错误绝不记为过期**
- 单次异常不算数，需连续确认 + 复查

---

## 配置（watchlist.json）

```jsonc
{
  "token": "",                       // 留空，用环境变量 YT_TOKEN

  "targets": [
    { "type": "ship",   "value": "MSC IRINA", "label": "MSC IRINA" },
    { "type": "voyage", "value": "GJ634W",    "label": "GJ634W" }
  ],

  "settings": {
    "etb_back_days": 7,              // 查询起始日 = 今天 - N 天
    "query_interval_seconds": 3.0,
    "max_pages": 5,
    "state_file": "state/voyage_state.json",
    "profile_dir": ".browser_profile",
    "edge_path": null,               // null = 自动找最新的 Edge
    "headless": true,
    "cookie_cache": ".cache/cookies.json",

    "alert_on": ["changed", "missing", "error"],   // 哪些情况告警
    "alert_cooldown_seconds": 3600,  // 同一问题多久内不重复发
    "alert_error_after": 2,          // 查询失败连续几轮才告警
    "alert_state_file": "state/alert_state.json",
    "alert_max_per_cycle": 5,

    "precheck": false,               // 默认关（恒报 expired 且每轮多打一次站点）
    "retry_attempts": 2,             // 被限流时退避重试几次（不重引导浏览器）
    "retry_backoff_seconds": 20.0,   // 退避基数，指数增长 + 抖动
    "watch_jitter_seconds": 30.0,    // 每轮额外随机等待上限

    "stale_after_hours": 6.0,        // 距上次成功多久算停摆（0=关）
    "heartbeat_hours": 24.0,         // 多久发一条"我还活着"（0=关）
    "heartbeat_url": "",             // 外部 dead-man ping，唯一能发现"再也没跑过"
    "heartbeat_state_file": "state/heartbeat.json"
  },

  "notify": []                       // 告警通道，见下节
}
```

- `type: ship` → 按**船名**查（精确匹配优先，否则退回子串匹配并**明确警告**）
- `type: voyage` → 按**码头航次**查（精确匹配）

因为站点是子串匹配，`EVER` 会连 `LEVERKUSEN EXPRESS` 一起返回 —— 所以**船名请写完整**。

---

## 告警通道

`notify` 是一个数组，可以同时配多个通道。**全部零新依赖**（标准库 + `requests`
+ `ctypes`）—— 这一点是刻意的，因为目标环境要跑在 Win7 + Python 3.8 上。

```jsonc
"notify": [
  { "kind": "windows", "label": "系统通知" },
  { "kind": "dingtalk", "url": "https://oapi.dingtalk.com/robot/send?access_token=...",
    "secret": "SEC..." },
  { "kind": "wecom",  "url": "https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=..." },
  { "kind": "feishu", "url": "https://open.feishu.cn/open-apis/bot/v2/hook/...",
    "secret": "..." },
  { "kind": "webhook", "url": "https://你的服务/ytmon" },
  { "kind": "email", "smtp_host": "smtp.example.com", "smtp_port": 465,
    "smtp_user": "user@example.com", "smtp_password": "授权码",
    "mail_from": "user@example.com", "mail_to": ["ops@example.com"],
    "use_ssl": true }
]
```

- `dingtalk` / `feishu` 的 `secret` 是**加签密钥**，没有就留空
- `webhook` 发的是结构化 JSON：`{title, text, source, sent_at}`
- 填好后**先跑 `python run_monitor.py --test-alert`**，逐条验证通不通
  （内网代理、机器人被踢、SMTP 端口被封，这些比监控逻辑本身更容易出问题）

### Windows 系统通知（`kind: "windows"`）

用 `Shell_NotifyIcon` 弹系统气泡，纯 `ctypes` 实现，零依赖。
选它而不是 Win10 那套 Toast API，是因为 **Toast 在 Win7 上根本不存在** ——
`Shell_NotifyIcon` 是唯一跨版本的（Win7 → 经典气泡，Win10/11 → 自动转成系统通知）。

**但它有两个绕不过去的硬限制**（Windows 的设计，不是实现问题）：

1. **必须有一个已登录的交互式桌面会话。** 计划任务勾了"不管用户是否登录"，
   进程会落在会话 0，`Shell_NotifyIcon` 直接失败。
2. **没人看着屏幕就等于没通知。** 机器开着但没人在，气泡弹了也没人看见。

所以它适合**托盘常驻程序**（也就是 GUI 那条路线），不适合无人值守的后台任务。
无人值守场景请**再加一个邮件或 webhook 通道兜底** —— 否则你会以为告警配好了，
直到真出事那天才发现从来没收到过。

失败时会**如实报错并说明原因**，不会静默假装成功。`hold_seconds` 控制气泡停留时长（默认 5 秒）。

### 三条防骚扰设计

| 机制 | 为什么 |
|---|---|
| **首次不发告警** | 第一次看到只是建立基线，不是"出事了"，不该半夜叫人 |
| **冷却去重** | 船一离港，`missing` 会**每轮都出现**。靠冷却压住；但内容一变（ETB 又改了）会**立刻放行** —— 冷却不会吞掉真实变化 |
| **连续失败阈值** | 实测站点会偶发降级/限流（HTTP 567、会话被弹回）。一次失败就叫人等于狼来了，用不了多久告警本身就会被无视 |

订阅失败有一条重要行为：**所有通道都发失败时不记账**，下一轮会重试 ——
否则一次网络抖动就把这条告警永久吞掉了。

`watchlist.json` 在 `make_bundle.py` 的排除名单里，**打包时被整个剔除** ——
这是真正兜住凭证外泄的措施，机器人 token 不会进交付包。

> ⚠️ 注意：**本目录当前不是 git 仓库**（`.gitignore` 已写好但尚未生效）。
> 不要依赖它来防凭证外泄 —— 真正起作用的是打包脚本的排除名单，
> 加上打包后的凭证扫描（双向：既不误报占位符，也必须抓到真密钥）。
> 以后如果 `git init`，`.gitignore` 会自动开始起作用。

---

## 心跳：谁来监控监控者

告警工具最经典的空洞：**如果监控本身死了，你会收到一片安静，
而安静和"船期没变化"长得一模一样。**

死法很多，而且都不产生任何输出：机器关机、计划任务被禁用、Python 起不来、
站点改版导致每轮都失败。

配置里有两个旋钮，但**它们的能力不一样**，这点必须分清楚：

| 旋钮 | 能发现 | 发现不了 |
|---|---|---|
| `stale_after_hours` | "这中间断过 5 小时"（机器重启后立刻知道） | "再也没跑过" —— 进程没起来就没有"下次运行" |
| `heartbeat_url` | **"再也没跑过"** —— 判定发生在进程之外 | — |

所以：**想真正兜住"监控死了"，必须配 `heartbeat_url`**
（一个 healthchecks.io 之类的 dead-man 服务，到点没收到 ping 就报警）。
只配 `stale_after_hours` 只能做到"重启后补报中断"。

`heartbeat_hours` 是定期"我还活着"的消息。示例配置里两个都打开了
（代码默认是关的，免得脚本和测试被意外打扰）。

外部 ping 每轮都发，代表**"进程跑起来了"**，和"查询成功"是两件事 ——
站点挂了但进程活着，照样 ping；查询失败由告警通道负责报。

---

## 命令行

| 参数 | 说明 |
|---|---|
| `--config FILE` | 配置文件，默认 `watchlist.json` |
| `--token TOKEN` | 令牌（一般不用；优先级 `--token` > `YT_TOKEN` > 配置文件） |
| `--etb-time YYYYMMDD` | 覆盖查询起始日 |
| `--watch 秒` | 循环监控；不填只跑一轮 |
| `--quiet` | 只输出变更与异常（适合挂计划任务） |
| `--verbose` | 连调试信息一起输出（例如那条恒定无信息的预检查提示） |
| `--no-notify` | 本轮不发告警（即使配了通道） |
| `--test-alert` | 给每个告警通道发一条测试消息然后退出 |
| `--dump-html 目录` | 存每次查询的原始 HTML（复核/排障） |
| `--show-browser` | 引导 cookie 时显示浏览器窗口 |

---

## 挂到 Windows 计划任务

```powershell
# token 一般不需要设；只在确实要指定时才设
# [Environment]::SetEnvironmentVariable("YT_TOKEN", "你的令牌", "User")

$action  = New-ScheduledTaskAction -Execute "python" `
           -Argument "run_monitor.py --quiet" `
           -WorkingDirectory "C:\Users\weiguangtwk\Documents\source\Port_Auto_Fetch"
$trigger = New-ScheduledTaskTrigger -Once -At (Get-Date) `
           -RepetitionInterval (New-TimeSpan -Minutes 30)
Register-ScheduledTask -TaskName "盐田船期监控" -Action $action -Trigger $trigger
```

> **频率别调太密。** 实测站点会限流（HTTP 567）并偶发把会话弹回首页。
> 30 分钟一轮足够发现船期变动，对站点也客气。

---

## GUI

图形界面还在准备阶段。**基座已就绪**：`MonitorService` 与任何 GUI 库解耦，
GUI 只是它的一个消费者。选型与骨架见 **`docs/GUI-技术选型与基座.md`**。

```powershell
pip install -r requirements-gui.txt
python gui/app.py
```

---

## 测试

```powershell
python tools/run_tests.py -v
```

**250 项离线测试**（含真实浏览器探测），不需要网络。

> **为什么用 `tools/run_tests.py`，而不是直接 `python -m unittest discover`：**
> Python 3.13+ 的 `tempfile` 在"只有工作区可写"的环境（沙箱等）里建出的临时目录**写不进去**，
> 会让"写盘再重读"型用例集体假失败、清理阶段报满 ERROR —— 看起来像代码回归，其实不是。
> 这个脚本**先探测再决定**：正常环境下什么都不做，受限环境才在自己进程内绕开，**并打印提示**。
> 判别方法：真回归的断言失败长在 `tests/*.py` 自己的断言行上；
> 环境假象长在 `tempfile.py` / `shutil.py` 的清理路径上。
> **不要为了消掉这些假失败去改业务代码。**

**已在 Python 3.8.10（Win7 交付运行时）上验证**：250 项中 249 项通过。
唯一失败的是 `test_real_browser_is_recognised` —— 沙箱拒绝 Edge 的 `OpenProcess`，
属环境限制，与探测逻辑无关。

其中几类是**回归守卫**：

- `TestQueryPageContract` —— 站点改了表单字段或结果页表头就立刻失败
- `TestCheckToken` —— 守住 "WAF 拦截 / token 过期 / 网络错误" 三者的区分
- `tests/test_notify.py` —— 守住告警的防骚扰三条：首次不报、冷却去重、连续失败阈值；
  以及 **Windows 通知失败必须如实报错**（不能静默假装成功）
- `tests/test_console.py` —— 用真子进程守住 GBK 重定向不再崩（先验证不修会崩）
- `tests/test_env_check.py` —— 守住"`--browser` 必须一路传到查询那步"
  （把自动探测打成返回空来复现 Win7 的处境）
- `tests/test_heartbeat.py` —— 守住"本地停摆检测"和"外部 ping"**能力不同**这件事
- `TestRateLimitBackoff` —— 守住"**限流绝不触发浏览器重引导**"
  （两者的正确反应是相反的：重引导只会雪上加霜）
- `TestSecretScanner` —— 打包扫描器要**双向**可靠：既不许误报占位符，也必须抓到真密钥

---

## 已知限制

1. **站点只发布约 1 个月的船期**，更远返回 0 条 → **无法长期回溯历史**。
2. **依赖页面结构**：站点改版会导致解析失效 —— 靠回归测试尽早发现。
3. **限流是最大的运行风险**。站点会返回非标准状态码 `567`，并偶发把会话弹回首页。
   现在的处理是分层的：识别出 `567` → **退避重试**（不重引导浏览器）；
   一个目标撞上致命错误 → **跳过本轮剩余目标**；每个目标失败 → 连续 2 轮才告警。
   但**频率本身仍要克制**：30 分钟一轮足够。探得越急越容易把自己推进降级窗口。
4. **"未查到"不等于正常**：船已离港或超出窗口都会是"未查到"，需人工判断。
5. **cookie 过期会自动重新引导**，代价是那一次多花约 10 秒。
6. **告警通道本身没有重试队列**：发失败只在下一轮重试一次，没有指数退避。
   对"船期变动"这种低频事件够用；要保证不丢，应改用可靠队列。
7. **Windows 通知需要有人登录、且是交互式会话**（见上文）。无人值守场景
   必须再加一个邮件或 webhook 通道，否则会误以为自己配好了告警。
8. **本地停摆检测报不了"监控再也没跑过"** —— 那需要配 `heartbeat_url` 外部 ping。

---

## 待办

- [x] 混合 HTTP 架构（浏览器只引导 cookie）—— 性能提升 10 倍
- [x] 去掉 token/账号依赖（实测证明公众查询不需要）
- [x] **告警通道**（webhook / 钉钉 / 企微 / 飞书 / 邮件 / **Windows 系统通知**，零新依赖，含防骚扰）
- [x] 自愈（cookie 重引导 → 可选 token 续期），且**无凭证时不再做无谓尝试**
- [x] **抗抖动**：限流识别 + 退避重试、首个目标失败即停、循环抖动
- [x] **心跳与停摆检测**（`heartbeat.py`）
- [ ] token 有效期观测（`tools/watch_token.py`）—— 结论已不重要，token 本就不需要
- [ ] 历史入库（SQLite），支持"这条船改了几次期"的复盘
- [ ] GUI 实现（选型已定；**先验证 PySide2 能否在 Win7 上跑**，再决定要不要写整套界面）
- [ ] 并行推进官方**船期信息订阅接口**（`/clearance2/clearanceValueAdd.action`，可申请开通）


