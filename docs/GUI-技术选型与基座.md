# GUI 技术选型与基座

> 目标：把现在的命令行监控程序图形化，外观接近 Windows 11 / WinUI。
> 本文记录选型依据、已做好的基座、以及**两处需要决策的问题**。

---

## ★ 决策已定（2026-09-16）

| 问题 | 结论 |
|---|---|
| 许可证路线 | **走 A：Fluent-Widgets**（GPLv3）。内部自用；**若把打包出的 exe 发到公司外部，须先过合规。** |
| Qt 绑定 | **收敛到 PySide2 / Qt 5.15**。开发与交付同一个 Python 3.8 环境；PySide6 / Qt 6 降为遗留分支（Qt 6 不支持 Win7）。 |
| 打包 | 需要 **PyInstaller 出 Windows 二进制**。已实测可行（onedir，约 125 MB）；构建脚本 `tools/build_gui.py`。 |

> 下文 §一 ~ §五 是**定案前的调研记录**，保留原样以便回溯；
> 其中"待你确认"的三问已在上面回答。
>
> **实测记录**（跑出来的，不是推的）：见 `GUI-Win7打包实测.md`。
> 仍未完成的一步是 **Win7 真机验证** —— 冻结产物能否启动、能否过 EdgeOne 挑战。

---

## 零、Win7 兼容性（2026-09-14 追加，已确认必须支持）

**前提**：监控服务与 GUI **都要在 Win7 上跑**，且现场确实有在用的 Win7 机器。

### 0.1 先否掉一个方案：pywinstyles 在 Win7 上帮不上忙

查证了 `pywinstyles`（[PyPI](https://pypi.org/project/pywinstyles/) / [GitHub](https://github.com/Akascape/py-window-styles)）：

> "Customize window styles in **windows 11** … **Windows 10 is also supported (only themes).**"

- 它的样式列表里确实有一个叫 **`win7`** 的条目，但那是**在 Win11 上把窗口伪装成 Win7 Aero 外观**，
  不是"能在 Windows 7 上运行"。
- 机制是 `DwmSetWindowAttribute` + Win10/11 专有的 `DWMWA_*` 属性
  （沉浸式暗色标题栏、Mica、圆角偏好），**Win7 上这些属性根本不存在**。
- 另外它**不是一个 GUI 框架**，而是叠加在 Tkinter / CustomTkinter / PySide / wx 之上的**外观增强层**，
  所以它回答不了"哪个工具包能在 Win7 上跑"。

**但值得留着**：CC0 许可、要求 Python ≥3.8、支持 PySide。
将来若上 Win10/11，它能给 PySide 窗口加 Mica / 暗色标题栏，成本极低。

### 0.2 三个阻断与对应的 Win7 解法

| # | 阻断 | Win7 上的解法 | 状态 |
|---|---|---|---|
| 1 | **Qt 6 不支持 Win7**<br>[Qt 6.11 官方文档](https://doc.qt.io/qt-6/supported-platforms.html) 的 Windows 只有 `Windows 10 (1809+)` / `Windows 11` | **PySide2 5.15 + PySide2-Fluent-Widgets**<br>同作者、同 API、Qt 5.15 支持 Win7，外观基本一致 | 同 API，`gui/app.py` 基本只改 import |
| 2 | **Python 3.9+ 不支持 Win7**<br>Win7 上最后一个 Python 是 **3.8**（2024-10 已 EOL） | **Python 3.8.10**（可用便携版，免安装） | ✅ **移植成本≈0**（见 0.3） |
| 3 | **Edge 最高只能到 109**（2023-01，已 EOL）<br>而 EdgeOne 挑战绕行依赖拉起浏览器 | **Supermium**——面向 Windows XP/2003 及以上、内核跟进到 **Chromium 138** 的分支，Chromium 系所以 **CDP 可用** | ⚠️ **必须实机验证** |

> Supermium 参考：[GitHub](https://github.com/supercoeus/supermium)（"Chromium fork for Windows XP/2003 and up"）、
> [SourceForge](https://sourceforge.net/app/supermium/)（"Modern Chromium Built for Legacy Windows"）、
> 近期版本 `138.0.7204.300 R9`。

### 0.3 Python 3.8 移植成本：几乎为零（已实测核对）

对全部 16 个 `.py` 文件做了检查：

- ✅ 每个模块都已 `from __future__ import annotations` → 所有 `X | Y` 注解在 3.8 上只是字符串，不参与求值
- ✅ 未使用任何 3.9+ / 3.10+ 语法或 API
  （无 `removeprefix`/`removesuffix`、无 `match` 语句、无 `functools.cache`、
  无 `zoneinfo`/`tomllib`、无运行时 `isinstance(a | b)`、无 `TypeAlias`）
- ⚠️ 需要下调版本的只有依赖：`aiohttp` 需 ≤3.9.x（3.10+ 要求 Python ≥3.9）

**结论：业务代码不用改，只需固定 Python 3.8 解释器与依赖版本。**

### 0.4 唯一的决定性未知：Win7 上能否过 EdgeOne 挑战

**这一项我无法在当前这台机器上代你验证，必须在真机测。**

```
在 Win7 机器上：
  1. 装 Supermium（或已有的 Edge 109）
  2. 打开  https://www.156yt.cn/pqs_revision/pages/jsp/popuPublic.jsp?loginVerifyCode=
  3. 看是否能正常显示查询页，还是卡在 EdgeOne 的 JS 挑战页
```

- **能过** → 整套方案在 Win7 上成立，按 0.2 的解法推进
- **不能过** → Win7 上无解，只剩两条路：
  服务跑在现代机器（Win7 只做展示），或改走官方**船期信息订阅接口**

### 0.5 两个必须一起权衡的风险

1. **Python 3.8 已 EOL**（2024-10），不再有安全更新。在公司机器上长期运行 EOL 解释器
   通常需要走一次合规确认。
2. **Qt 5.15 也已进入维护尾声**。PySide2 与 PySide2-Fluent-Widgets 能用，但都是"稳定但不再前进"的技术栈。

---

## 一、结论（2026-09-14 初版，未计入 Win7）

**推荐：PySide6 + PySide6-Fluent-Widgets（qfluentwidgets）**

| 项 | 结论 |
|---|---|
| 外观 | 就是照着 WinUI / Fluent Design 做的，参考 [Microsoft/WinUI-Gallery](https://github.com/microsoft/WinUI-Gallery) |
| 版本 | `PySide6-Fluent-Widgets` **1.11.3**（2026-08-01 发布，活跃维护） |
| 本机可行性 | ✅ **已验证可解析**：`py3-none-any` 纯 Python 包 + PySide6 用 `cp310-abi3` 稳定 ABI wheel，**Python 3.14 装得上** |
| 依赖体积 | PySide6 约 100–200 MB（Qt 运行时） |
| 组件丰富度 | 导航视图、卡片、表格、进度环、信息栏、对话框、深浅色主题、Mica/Acrylic 材质 |
| ⚠️ 许可证 | **GPLv3**（非商业免费；商业用途需购买授权） |

详细安装解析（本机实测，dry-run 未安装）：

```
Would install PySide6-6.11.2 PySide6-Fluent-Widgets-1.11.3
              PySide6_Addons-6.11.2 PySide6_Essentials-6.11.2
              PySideSix-Frameless-Window-0.8.2 darkdetect-0.8.0 shiboken6-6.11.2
```

安装命令：

```powershell
pip install PySide6-Fluent-Widgets
# 需要 Mica/Acrylic 效果就装 full 版：
pip install "PySide6-Fluent-Widgets[full]"
```

> ⚠️ 官方警告：`PyQt-Fluent-Widgets` / `PyQt6-…` / `PySide2-…` / `PySide6-…`
> **四种包的模块名都是 `qfluentwidgets`，不能同时安装**。本项目只用 PySide6 版。

---

## 二、⚠️ 需要你决策：许可证

`PySide6-Fluent-Widgets` 采用**双许可**：

- 非商业用途 → GPLv3
- 商业用途 → 需购买[商业授权](https://qfluentwidgets.com/price)

GPLv3 的义务主要在**分发**时触发。**公司内部自用、不对外分发**通常不构成分发，
但这一点取决于公司合规口径，我不能替你判断。

底层的 PySide6（Qt for Python）是 **LGPL**，本身对商业内部使用友好；
问题只出在 `qfluentwidgets` 这一层。

### 三条路

| | 方案 | 外观 | 许可 | 代价 |
|---|---|---|---|---|
| **A** | PySide6 + **Fluent-Widgets** | ★★★★★ 最接近 WinUI | GPLv3 / 需购买 | 需确认合规 |
| **B** | PySide6 + **自写 QSS 主题** | ★★★☆☆ 能做到"像"，但不是原生控件 | LGPL | 多花 2–4 天写样式 |
| **C** | **CustomTkinter**（MIT） | ★★★☆☆ 现代但非 WinUI | MIT 最宽松 | 组件少、无 Mica、表格能力弱 |

**我的建议**：先按 A 做原型（外观差距是肉眼可见的），
同时去问一句公司合规；如果要规避 GPL，B 是最接近的替代——
因为**基座已经让 UI 层可替换**：`MonitorService` 完全不依赖任何 GUI 库，
换 UI 工具包不影响业务逻辑。

> 另有一个值得知道的选项：本机**已装 WebView2 152**。
> 若愿意用 Web 技术做界面（本地 FastAPI + WebView2 外壳），
> 外观可以任意定制且无 GPL 问题，但就偏离了你"Python 仿 WinUI 模块"的偏好。

---

## 三、已做好的基座（本轮完成）

关键原则：**业务逻辑与 UI 完全解耦**。GUI 只是 `MonitorService` 的一个消费者。

```
ytmon/
  cdp.py          异步 CDP 原语（驱动 Edge 过 EdgeOne 挑战）
  http_client.py  同步 HTTP：cookie 引导 + token 体检 + 查询   ← 日常走这条
  auth.py         登录与 token 自动续期
  parse.py        结果页解析
  matching.py     船名/航次匹配规则
  store.py        状态存储与变化比对
  config.py       配置模型（dataclass，GUI 可直接绑定）
  service.py      ★ UI 无关的编排层 —— GUI 基座
  cli.py          命令行呈现层（service 的一个消费者）
  errors.py       统一异常
gui/
  app.py          GUI 骨架（待安装依赖后运行）
tools/
  watch_token.py  token 寿命观测器
  renew_token.py  手动/自动续期
```

### 3.1 `MonitorService` 是 GUI 的接口面

```python
from ytmon import AppConfig, MonitorService

cfg = AppConfig.load("watchlist.json")

def on_event(kind, payload):
    # 进度回调：GUI 在这里更新进度条 / 日志面板
    #   cycle_start / token_status / target_start / target_done / cycle_done / log
    ...

svc = MonitorService(cfg, on_event=on_event, dump_dir="snapshots")

status = svc.check_token()      # token 体检（约 0.4 秒）
report = svc.run_cycle()        # 跑一轮，返回结构化结果，不打印任何东西
report.has_changes              # 是否有变更
report.alerted                  # 需要提醒的目标（变更 + 未查到 + 错误）
report.summary_line()           # "变更 1 / 首次 0 / 无变化 1 / 未查到 0"

svc.snapshot()                  # 给表格用的当前状态（list[dict]）
svc.renew_token()               # 用账号密码续期 token
```

**全部是同步方法**：GUI 请在 worker 线程里调用（内部有网络请求，首次还会拉一次浏览器）。
不要直接在 UI 线程调用，否则界面会卡。

### 3.2 配置模型可直接双向绑定

```python
cfg.targets          # list[Target]  —— type/value/label/enabled
cfg.settings         # Settings      —— 带默认值，GUI 表单直接绑
cfg.validate()       # list[str]     —— 保存前校验，返回人类可读的错误
cfg.save()           # 写回 JSON
```

### 3.3 性能：GUI 响应不用担心

本轮把日常抓取从"每次拉起浏览器"改成"浏览器只引导一次 cookie + 纯 HTTP 查询"：

| 操作 | 之前（浏览器） | 现在（HTTP） |
|---|---|---|
| 每轮监控（2 个目标） | ~22 秒 | **~1.8 秒** |
| token 体检 | — | **~0.4 秒** |
| 首次 cookie 引导 | — | ~10 秒（之后走缓存） |

---

## 四、GUI 骨架说明

`gui/app.py` 已按下面的结构写好（**未安装依赖前无法运行**，逻辑未经运行验证）：

- **左侧导航**：监控面板 / 目标管理 / 日志 / 设置
- **监控面板**：目标表格（船名、航次、ETB、ETD、闸口、船代、上次核对时间）
  + 「立即检查」按钮 + 进度条 + 告警高亮
- **线程模型**：`QThread` worker 调 `MonitorService`，
  通过 Qt signal 把 `on_event` 的事件转回 UI 线程（**不要跨线程碰控件**）
- **设置页**：绑定 `Settings` dataclass，保存前调 `cfg.validate()`

---

## 五、待你确认

1. **许可证**：走 A（接受 GPLv3 / 买授权）、B（自写主题，多花几天）还是 C（CustomTkinter）？
2. 是否现在安装 PySide6（约 100–200 MB）并把 `gui/app.py` 跑起来验证？
3. 界面语言：中文（当前 CLI 全中文）？
