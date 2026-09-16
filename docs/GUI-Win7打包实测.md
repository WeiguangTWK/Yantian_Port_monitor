> 历史记录：部分接口与功能描述已过时。当前用法以 README.md 和现行源码为准。

# GUI 工具链与 Win7 打包 · 实测记录

> 2026-09-16 在开发机（Win11 + Python 3.14）上**跑出来**的结论，不是查文档推的。
> 唯一还没做的一步是 **Win7 真机验证** —— 见文末「还差什么」。
>
> 配套阅读：`GUI-技术选型与基座.md`（选型依据）、`Win7-实机验证指南.md`（现场检查表）。

---

## 1. 环境矩阵：两条绑定腿，两个环境（硬性）

| 场景 | Python | Qt 绑定 | Fluent-Widgets |
|---|---|---|---|
| 本机开发 / 预览（Win11） | 3.14 | PySide6 | PySide6-Fluent-Widgets 1.11.3 |
| **Win7 交付构建** | **3.8.10** | **PySide2 5.15.2.1** | **PySide2-Fluent-Widgets 1.11.3** |

为什么必须分两个环境：

- 四种 Fluent 包（PyQt / PyQt6 / PySide2 / PySide6）的**模块名都是 `qfluentwidgets`**，不能共存。
- `PySide2 5.15.2.1` 的 `requires_python` 是 **`<3.11`** → **装不进开发机的 Python 3.14**。
  所以 Win7 那条腿只能在 3.8 环境里跑、在 3.8 环境里验证。

已核实的两条事实（此前不确定）：

- `PySide2-Fluent-Widgets` **确实有 1.11.3**，与 PySide6 版**同版本号**。
- `PySide2 5.15.2.1` 提供 `cp35.cp36.cp37.cp38.cp39.cp310-none-win_amd64` wheel
  → **Win7 64 位可用**（win32 wheel 也在，32 位机器同样有路）。

### 1.1 开发机上的便携工具链

Win7 构建用仓库内的便携 Python，不碰系统 Python、不需要管理员权限：

```
.toolchain/                      ← 已写进 .gitignore，可随时整个删除重建
  python-3.8.10-amd64.exe        ← 官方安装包
  py38/                          ← 静默装到这里的 Python 3.8.10
  tmp/                           ← 构建期 TMP/TEMP（沙箱要求）
  pyi-config/                    ← PYINSTALLER_CONFIG_DIR（沙箱要求，见 §5）
```

搭建方式：

```powershell
# 1) 下载（注意：PowerShell 的 Invoke-WebRequest / curl.exe 在本机会因 Schannel 失败，
#    必须用 Python 下载 —— 详见 OV dead-ends.md A7）
python -c "import urllib.request;urllib.request.urlretrieve('https://www.python.org/ftp/python/3.8.10/python-3.8.10-amd64.exe','.toolchain/python-3.8.10-amd64.exe')"

# 2) 装到仓库内（此步需要一次性提权到 danger-full-access：安装程序要写注册表）
#    参数：/quiet InstallAllUsers=0 TargetDir=.toolchain\py38 PrependPath=0 Include_launcher=0 ...

# 3) 装依赖（走国内镜像，PyPI 直连太慢）
.toolchain\py38\python.exe -m pip install -i https://pypi.tuna.tsinghua.edu.cn/simple `
  "PySide2==5.15.2.1" "PySide2-Fluent-Widgets==1.11.3" `
  "requests>=2.28,<2.33" "aiohttp>=3.8,<3.10" "async-timeout>=4.0,<5.0"
```

---

## 2. 实测：`gui/app.py` 在 PySide2 上**零改动**跑通

离屏冒烟（`QT_QPA_PLATFORM=offscreen`）：

```
BINDING    = PySide2
QT_VERSION = 5.15.2
targets    = ['MSC IRINA', 'KN637A']
window     = 盐田船期监控  ·  PySide2 / Qt 5.15.2
table rows = 2   columns = 9
SMOKE PASS
```

真实桌面冒烟（真开窗口、真跑事件循环、2 秒后自动关）：

```
platform   = (qwindows 平台插件正常加载)
shown      = True
title      = 盐田船期监控  ·  PySide2 / Qt 5.15.2
size       = 1080x720
event loop rc = 0
ONSCREEN PASS
```

**结论：`qt_compat.py` 的自动分叉有效，界面代码确实只写了一套。**

> ⚠️ offscreen 插件会刷屏 `QFontDatabase: Cannot find font directory .../PySide2/lib/fonts`，
> **真桌面上一句都没有** → 那是 offscreen 特有噪音（Windows 上走系统字体引擎，不走 fontconfig）。
> **不要据此去"修字体"。**

---

## 3. 新增传递依赖（选型文档里没提，必须知道）

`PySide2-Fluent-Widgets` 1.11.3 的依赖链：

```
PySide2-Fluent-Widgets
  └─ PySide2-Frameless-Window → pywin32        ← 硬运行时依赖（Windows）
  └─ darkdetect
  └─ PySide2
```

- `PySide2-Frameless-Window` 的元数据写着 `Requires-Dist: pywin32 ; platform_system == "Windows"`。
- `qfluentwidgets` 自己 `import win32con`；
  其依赖 `qframelesswindow` 用 **`from win32comext.shell import shellcon`** ← 冻结时最容易漏的就是它。

代价：GUI 侧不再是"零新依赖"（运行本体仍然只有 requests + aiohttp）。
不过 `pywin32` 的 Win7 可用性已实测过关，见 §4。

---

## 4. Win7 可用性：读 PE 头实测，不靠文档措辞

**方法**：直接读二进制的 PE 可选头与导入表。
子系统版本是 Windows 加载器的硬判据 —— Win7 = `6.1`；**要求 Win8 的二进制会写 `6.2`，要求 Win10 的写 `10.0`**。

| 二进制 | PE 子系统版本 |
|---|---|
| `python38.dll` / `python.exe` | **6.0** |
| PySide2 5.15.2.1（`Qt5Core/Qt5Gui/Qt5Widgets.dll`、`qwindows.dll`） | **6.0** |
| pywin32 311（`win32gui.pyd`、`pythoncom38.dll`、`pywintypes38.dll`） | **6.0** |
| PyInstaller 4.10 bootloader | 5.2 |
| **PyInstaller 6.22.3 bootloader** | **6.0** |

→ **`pywin32 311` 在 Win7 上没有子系统级障碍**（此前它是整条链上最大的未知）。

### 4.1 PyInstaller 版本选择：不必吊死在 4.10

两个候选的导入表对比结果：

- 两个版本的 bootloader **都只导入 `KERNEL32` / `ADVAPI32` / `USER32`**；
- **都不导入任何 `api-ms-win-*` API set**（那才是典型的 Win8+ 门槛）；
- 都不需要 Win7 的 **KB2533623**（即不调用 `SetDefaultDllDirectories` / `AddDllDirectory`）；
- 6.22.3 相对 4.10 新增要求 21 个 `kernel32` 函数，其中只有
  **`K32EnumProcessModules` / `K32GetModuleFileNameExW`** 是新增的 ——
  而它们**正是 Windows 7 引入的**（其余全部 Vista 及更早）。

另外：`pyinstaller 6.22.3` 的 `Requires-Python: <3.16,>=3.8` → **支持 Python 3.8**。

**结论**：官方文档里 "PyInstaller runs in Windows 8 and newer" 指的是**构建主机**（我们在 Win10/11 上构建，满足），
**不是产物**。产物这一侧，6.22.3 的 bootloader 只要 Win7 级别的 API。

> ⚠️ 但**产物能否在 Win7 跑，仍必须真机验证**。上面的 API 分析是强证据，不是实测。

---

## 5. 冻结（PyInstaller）：命令与两个必须踩到的坑

### 5.1 必须补的隐藏导入

不补会报 **`No module named 'win32com'`**，而且会被 `gui/app.py` **误报**成"缺少 qfluentwidgets"（见 §6）：

```
--collect-all qfluentwidgets
--collect-submodules win32comext
--hidden-import win32con --hidden-import pythoncom --hidden-import pywintypes
```

完整可复现命令（onedir，PyInstaller 6.22.3）：

```powershell
$env:PYINSTALLER_CONFIG_DIR = "$PWD\.toolchain\pyi-config"     # ← 坑 1，见 §5.2
.toolchain\py38\python.exe -m PyInstaller --noconfirm --clean --onedir --windowed `
  --name ytmon-gui --paths "$PWD" --paths "$PWD\gui" `
  --collect-all qfluentwidgets --collect-submodules win32comext `
  --hidden-import win32con --hidden-import pythoncom --hidden-import pywintypes `
  --workpath "$PWD\.toolchain\pyi-build" --distpath "$PWD\.toolchain\pyi-dist" `
  --specpath "$PWD\.toolchain" "$PWD\gui\app.py"
```

实测结果：产物 **约 125 MB / 343 个文件**；启动后进程存活、事件循环正常、**stderr 为空**。

### 5.2 DSH 沙箱下的两个坑

1. **`PYINSTALLER_CONFIG_DIR` 必须指进工作区。**
   否则 PyInstaller 去建 `%LOCALAPPDATA%\pyinstaller`，被沙箱拒 →
   **构建在最后一步 `COLLECT` 倒下**（而前面 `Building EXE ... completed successfully` 明明已经打完），
   报 `PermissionError [WinError 5]`。**极易误判成"打包失败"。**
2. 跑构建前把 `TMP` / `TEMP` 指进工作区（`.toolchain\tmp`），
   否则 pip 与安装程序会因沙箱禁止写 `%TEMP%\dsh-*` 而失败。

### 5.3 用 `--onedir`，不要 `--onefile`

Qt 运行时 100 MB+。onefile 每次启动都要把这一坨解压到临时目录，
在 Win7 上更慢、更容易被杀软误报，而且 onefile 的临时目录行为在 Win7 上变量更多。

---

## 6. 已发现、待修的 GUI 缺陷

### 6.1 误导性报错（冻结后立刻会撞到）

`gui/app.py` 捕获 `ImportError` 后打印：

```
缺少 qfluentwidgets。请安装：
    Win7:      pip install -r requirements-win7-gui.txt
（原始错误：No module named 'win32com'）
```

真实原因是**冻结包里缺 `win32com` 子模块**，与"没装 qfluentwidgets"毫无关系。
报错把人引向"去装包"，方向完全错了 —— 典型的 `dead-ends.md` 式陷阱。
**原始错误必须放在最显眼位置**，而不是附在最后一行。

### 6.2 没接 `ytmon/console.py`

`cli.py` 装了控制台编码兜底，**GUI 入口没装**。
中文 Windows 下把输出重定向时，`⚠` `·` 这类不在 GBK 里的符号仍有
`UnicodeEncodeError` 中断风险（见 `dead-ends.md` B7）。

### 6.3 冻结后的路径基准

`gui/app.py` 的 `CONFIG_PATH = "watchlist.json"` 是**相对 CWD** 解析的，
`state/` / `.cache/` / `.browser_profile/` 同理；而 `sys.path` 还用 `__file__/..` 拼。
冻结后 `__file__` 指向解包目录，双击 / 快捷方式 / 计划任务的 CWD 又各不相同。

→ 需要一层统一解析：**冻结时以 exe 所在目录为基准**（`Path(sys.executable).parent`），
否则"配置读不到 / 状态每次都当首次"会以最难查的方式出现。

### 6.4 浏览器不进包

Win7 上仍然是 **Supermium**（或已有的 Edge 109），不会被打进 exe。
`settings.edge_path` 必须在现场配，`browser_find.py` 的自动探测要能在冻结环境下工作。

---

## 7. 还差什么（无法在开发机上完成）

| 待验证 | 为什么必须在真机做 |
|---|---|
| **冻结产物能否在 Win7 启动** | API/子系统分析是强证据，但不等于实测；这是唯一能定案的一步 |
| Win7 上能否**过 EdgeOne 挑战** | 决定整套方案在 Win7 是否成立（选型文档 §0.4 早已列为决定性未知） |
| Win7 是否需要 UCRT / VC++ 运行库 | 缺 `api-ms-win-crt-*.dll` 时报 `WinError 2`，报错极具误导性（见 `dead-ends.md` B2） |
| Win7 上窗口观感与中文字体 | 本机只验证了渲染成功，没看外观 |

现场检查表见 `Win7-实机验证指南.md`。
