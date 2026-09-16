"""环境自检 —— 尤其在 Win7 机器上先跑这个。

它按依赖顺序逐层验证，告诉你"到底卡在哪一步"，而不是笼统地失败：

    1. 项目      ytmon 包在不在（不在的话后面全是假象）
    2. 环境      Python 版本 / 系统 / 位数
    3. DNS       www.156yt.cn 能否解析
    4. TCP       443 端口能否连通
    5. TLS       握手协议版本 + 证书链（Win7 最常见的坑就是根证书缺失）
    6. 站点      HTTP 请求拿到的是 EdgeOne 挑战页还是真实页面
    7. 浏览器    机器上有没有可用的 Chromium 系浏览器（并真的启一次）
    8. 引导      能否用浏览器拿到 EO-Bot-Js-Token（这一步过了基本就通了）
    9. 查询      真的发一次查询（**唯一可信的验收**）

前 6 步只用标准库，所以在还没装任何依赖的 Win7 上也能跑。

用法：
    python tools/env_check.py
    python tools/env_check.py --token xxxx
    python tools/env_check.py --browser "C:\\Supermium\\supermium.exe"
"""

from __future__ import annotations

import argparse
import datetime as dt
import pathlib
import platform
import socket
import ssl
import sys
import urllib.error
import urllib.request

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))


def _bootstrap_path() -> pathlib.Path | None:
    """把项目根目录放进 sys.path，并返回它。

    不直接假定 `__file__/../..` —— 现实中很容易出偏差：
      * 解压时多套了一层目录（C:\\ytmon\\ytmon-20260914\\...）
      * 用户只把 tools/env_check.py 单独拷了出来
    所以这里向上逐级查找"含有 ytmon/__init__.py 的目录"，找到哪个算哪个。
    """
    here = pathlib.Path(__file__).resolve().parent
    for candidate in [here, *here.parents]:
        if (candidate / "ytmon" / "__init__.py").is_file():
            if str(candidate) not in sys.path:
                sys.path.insert(0, str(candidate))
            return candidate
    return None


PROJECT_ROOT = _bootstrap_path()

HOST = "www.156yt.cn"
TEST_URL = f"https://{HOST}/pqs_revision/pages/jsp/popuPublic.jsp?loginVerifyCode="
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/138.0.0.0 Safari/537.36")

WAF_MARKERS = ("Qua7lMrVs39mmYCjI2s", "_aMYJPelgGNdHBCHbZMSUTNCYVFeXkCWA")

# 与 ytmon.http_client 的默认值保持一致（有测试守着，防止哪天改一处忘另一处）。
# 第 8 步写、第 9 步读，两边必须用同一个路径，否则第 9 步会白起一次浏览器。
PROFILE_DIR = ".browser_profile"
COOKIE_CACHE = ".cache/cookies.json"

OK, WARN, FAIL, SKIP = "OK", "注意", "失败", "跳过"


class Report:
    def __init__(self) -> None:
        self.rows: list[tuple[str, str, str, str]] = []

    def add(self, step: str, status: str, detail: str, action: str = "") -> None:
        self.rows.append((step, status, detail, action))
        icon = {OK: "✓", WARN: "!", FAIL: "✗", SKIP: "-"}.get(status, "?")
        print(f"  [{icon}] {step}: {detail}", flush=True)
        if action and status in (WARN, FAIL):
            print(f"        → {action}", flush=True)

    @property
    def failed(self) -> list[str]:
        return [r[0] for r in self.rows if r[1] == FAIL]

    def summary(self) -> str:
        ok = sum(1 for r in self.rows if r[1] == OK)
        warn = sum(1 for r in self.rows if r[1] == WARN)
        fail = sum(1 for r in self.rows if r[1] == FAIL)
        return f"通过 {ok} / 注意 {warn} / 失败 {fail}"


# ------------------------------------------------------------------ 1 项目结构


def check_project(rep: Report) -> None:
    """最先检查：ytmon 包在不在。不在的话后面几步的失败都会是假象。"""
    if PROJECT_ROOT is not None:
        rep.add("项目", OK, f"ytmon 包位于 {PROJECT_ROOT}")
        return
    rep.add("项目", FAIL, "找不到 ytmon 包 —— 项目文件没复制全", "")
    print("        正确的目录结构应该是：")
    print("            <项目根>\\ytmon\\__init__.py")
    print("            <项目根>\\tools\\env_check.py")
    print("            <项目根>\\run_monitor.py")
    print("        最省事的做法：在开发机跑  python tools\\make_bundle.py")
    print("        把生成的 zip 整个解压过来，不要手工挑文件。")


# ------------------------------------------------------------------ 2 环境


def check_env(rep: Report) -> None:
    v = sys.version_info
    detail = (f"Python {platform.python_version()} · {platform.system()} "
              f"{platform.release()} ({platform.version()}) · {platform.machine()}")
    if v < (3, 8):
        rep.add("环境", FAIL, detail, "本项目需要 Python 3.8 或更高")
    elif v >= (3, 9):
        rep.add("环境", OK, detail + "  ← 非 Win7 路径")
    else:
        rep.add("环境", OK, detail + "  ← Win7 路径（3.8 是最后支持 Win7 的版本）")


# ------------------------------------------------------------------ 3-5 网络


def check_network(rep: Report) -> str | None:
    try:
        infos = socket.getaddrinfo(HOST, 443, proto=socket.IPPROTO_TCP)
        ips = sorted({i[4][0] for i in infos})
        rep.add("DNS", OK, f"{HOST} → {', '.join(ips[:3])}")
    except socket.gaierror as e:
        rep.add("DNS", FAIL, f"解析失败：{e}", "检查 DNS / 网络代理设置")
        return None

    try:
        with socket.create_connection((HOST, 443), timeout=15):
            rep.add("TCP", OK, "443 端口可连通")
    except OSError as e:
        rep.add("TCP", FAIL, f"连接失败：{e}", "检查防火墙 / 代理是否放行 443")
        return None

    # TLS —— Win7 最常见的坑
    try:
        ctx = ssl.create_default_context()
        with socket.create_connection((HOST, 443), timeout=15) as raw:
            with ctx.wrap_socket(raw, server_hostname=HOST) as s:
                proto = s.version()
                cipher = s.cipher()
                cert = s.getpeercert()
    except ssl.SSLCertVerificationError as e:
        rep.add("TLS", FAIL, f"证书校验失败：{e.verify_message if hasattr(e, 'verify_message') else e}",
                "Win7 常因缺少根证书而失败：安装系统更新，或手动导入对应根 CA")
        return None
    except Exception as e:                                   # noqa: BLE001
        rep.add("TLS", FAIL, f"握手失败：{type(e).__name__}: {e}",
                "Win7 需确保已启用 TLS 1.2 并安装了对应补丁")
        return None

    if proto in ("TLSv1", "TLSv1.1", "SSLv3"):
        rep.add("TLS", FAIL, f"协商到 {proto}（过旧）",
                "站点大概率会拒绝；Win7 需启用 TLS 1.2（注册表/补丁）")
    else:
        rep.add("TLS", OK, f"{proto} · {cipher[0] if cipher else '?'}")

    if cert:
        subject = dict(x[0] for x in cert.get("subject", []))
        issuer = dict(x[0] for x in cert.get("issuer", []))
        not_after = cert.get("notAfter", "?")
        cn = subject.get("commonName", "?")
        rep.add("证书", OK, f"CN={cn} 签发者={issuer.get('organizationName', '?')} 到期={not_after}")
    return proto


# ------------------------------------------------------------------ 6 站点


def check_site(rep: Report) -> bool:
    req = urllib.request.Request(TEST_URL, headers={
        "User-Agent": UA,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "zh-CN,zh;q=0.9",
    })
    try:
        with urllib.request.urlopen(req, timeout=25) as resp:
            body = resp.read().decode("utf-8", "replace")
            status = resp.status
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "replace")
        status = e.code
    except Exception as e:                                   # noqa: BLE001
        rep.add("站点", FAIL, f"请求失败：{type(e).__name__}: {e}", "回到上一步查网络/TLS")
        return False

    if any(m in body for m in WAF_MARKERS):
        rep.add("站点", WARN,
                f"HTTP {status}，返回的是 EdgeOne JS 挑战页（纯 HTTP 客户端被拦，属预期）",
                "这正是需要浏览器内核的原因 —— 见第 6、7 步")
        return True
    if "公众查询" in body or "popuPublic" in body or "集装箱" in body:
        rep.add("站点", OK, f"HTTP {status}，拿到真实页面（未触发挑战）")
        return True
    rep.add("站点", WARN, f"HTTP {status}，返回内容无法识别（长度 {len(body)}）",
            "可能是站点改版或代理插入了页面")
    return True


# ------------------------------------------------------------------ 7 浏览器


def find_browser_safe() -> tuple[str | None, str]:
    """浏览器探测必须不依赖第三方包 —— 裸 Win7 上还没装任何依赖时也要能跑。

    返回值第二项的约定：
        ""           成功
        "NOBROWSER"  路径通了，但机器上没有可用浏览器
        "LAYOUT:..." 连 ytmon 包都找不到（文件没复制全）
        "DEPS:..."   导入失败，通常是第三方依赖没装
    """
    try:
        from ytmon.browser_find import find_browser
    except ModuleNotFoundError as e:
        name = getattr(e, "name", "") or ""
        if name == "ytmon" or name.startswith("ytmon."):
            return None, "LAYOUT"
        return None, f"DEPS:{e}"
    except ImportError as e:
        return None, f"DEPS:{e}"

    try:
        return find_browser(), ""
    except FileNotFoundError:
        return None, "NOBROWSER"


def check_browser(rep: Report, explicit: str | None) -> str | None:
    if explicit:
        p = pathlib.Path(explicit)
        if not p.is_file():
            if p.is_dir():
                rep.add("浏览器", FAIL, f"给的是目录，不是 exe：{explicit}",
                        "请指向具体的可执行文件，例如 "
                        f"{p / 'supermium.exe'}")
            else:
                rep.add("浏览器", FAIL, f"指定路径不存在：{explicit}",
                        "路径要在**这台机器**上存在；注意别把开发机的路径抄过来")
            return None
    else:
        path, note = find_browser_safe()
        if path:
            explicit = path
        elif note == "LAYOUT":
            # 这正是 "No module named 'ytmon'" 的现场
            rep.add("浏览器", FAIL, "找不到 ytmon 包 —— 项目文件没复制全")
            print("        正确的目录结构应该是：")
            print("            <项目根>\\ytmon\\__init__.py")
            print("            <项目根>\\tools\\env_check.py")
            print("            <项目根>\\run_monitor.py")
            print("        最省事的做法：在开发机跑  python tools\\make_bundle.py")
            print("        把生成的 zip 整个解压过来，不要手工挑文件。")
            return None
        elif note.startswith("DEPS:"):
            rep.add("浏览器", FAIL, f"导入 ytmon.browser_find 失败：{note[5:]}",
                    "先装依赖：python -m pip install -r requirements-win7.txt")
            return None
        else:
            rep.add("浏览器", FAIL, "没有找到可用的 Chromium 系浏览器（但 ytmon 包是好的）",
                    "Win7 建议装 Supermium：https://github.com/win32ss/supermium/releases"
                    "（解压后把 settings.edge_path 指向 supermium.exe）")
            try:
                from ytmon.browser_find import describe_candidates
                print(f"        已扫描到：{describe_candidates()}")
            except Exception:                                # noqa: BLE001
                pass
            return None

    # **路径存在 != 能启动。** 用一次无头启动把它验掉再往下走。
    # 缺运行时 DLL 时 Windows 报的是"找不到文件"(WinError 2)，
    # 与"路径写错"完全同形，不主动验一次就会一路误导到很后面。
    try:
        from ytmon.browser_find import PROBE_FAIL, PROBE_OK, probe_browser
    except Exception:                                        # noqa: BLE001
        rep.add("浏览器", OK, f"使用指定路径：{explicit}")
        return explicit

    print("        （试启一次，确认真能跑起来…）", flush=True)
    result, detail = probe_browser(explicit)
    if result == PROBE_OK:
        rep.add("浏览器", OK, f"{detail}  ←  {explicit}")
        return explicit
    if result == PROBE_FAIL:
        rep.add("浏览器", FAIL, f"路径存在但**启动不了**：{explicit}", detail)
        return None

    # 探测不出来：如实说"无法判定"，但**不阻断**流程 ——
    # 把不确定当成失败，会误杀本来能用的浏览器。
    rep.add("浏览器", WARN, f"无法判定（先按可用继续）：{explicit}", detail)
    return explicit


# ------------------------------------------------------------------ 8 引导


def check_bootstrap(rep: Report, browser: str | None, token: str | None) -> bool:
    try:
        from ytmon.http_client import bootstrap_cookies
    except ImportError as e:
        rep.add("引导", SKIP, f"缺少依赖（{e}）", "装好 aiohttp/requests 后再跑一次")
        return False

    if not browser:
        rep.add("引导", SKIP, "没有可用浏览器，跳过", "先解决第 7 步")
        return False

    print("        （启动浏览器中，约 10-30 秒…）", flush=True)
    try:
        # 这里必须走 `bootstrap_cookies` 而不是直接 `_bootstrap_async`：
        # 前者会把 cookie **写进缓存**，第 9 步（查询）才能直接复用，
        # 不必再起一次浏览器。
        #
        # 之前用 `_bootstrap_async`（不写缓存）导致一个很隐蔽的 bug：
        # 第 9 步发现没缓存 → 回退到**自动探测**浏览器 → Win7 上 Supermium
        # 装在非标准目录 → `FileNotFoundError: 未找到任何 Chromium 系浏览器`。
        # 于是"第 7 步明明传了 --browser"，却在这一步报找不到浏览器。
        cookies = bootstrap_cookies(token or "", profile_dir=PROFILE_DIR,
                                    edge_path=browser, headless=True,
                                    cache_file=COOKIE_CACHE)
    except Exception as e:                                   # noqa: BLE001
        rep.add("引导", FAIL, f"{type(e).__name__}: {e}",
                "浏览器起来了但没过 EdgeOne 挑战；把 settings.headless 设为 false 再跑，观察窗口里发生了什么")
        return False

    rep.add("引导", OK, f"成功拿到 cookie：{', '.join(cookies)}"
                        f"（EO-Bot-Js-Token 长度 {len(cookies.get('EO-Bot-Js-Token', ''))}）")
    return True


# ------------------------------------------------------------ 9 实际查询


def check_query(rep: Report, token: str | None,
                browser: str | None = None) -> None:
    """最终验收：**真的查一次**。

    为什么不用 GET 判定：实测 `requests` 的 GET 会稳定落到「公共信息服务」首页，
    而同一个会话的 POST 查询却完全正常。所以页面对不对不是可靠判据 ——
    唯一可信的信号是查询本身。

    也不需要 token：实测公众查询不带 `loginVerifyCode` 参数都能查。

    `browser` 必须传进来：缓存失效时这里会**重新引导 cookie**，
    若不带上第 7 步确认过的那条路径，就会退化去自动探测，
    在 Win7（Supermium 装在非标准目录）上必然失败。
    """
    try:
        from ytmon.http_client import HttpClient
    except ImportError as e:
        rep.add("查询", SKIP, f"缺少依赖（{e}）", "装好 requests/aiohttp 后再跑一次")
        return

    try:
        client = HttpClient.create(token or "", prefer_cache=True,
                                   profile_dir=PROFILE_DIR,
                                   edge_path=browser,
                                   headless=True,
                                   cache_file=COOKIE_CACHE)
        st = client.verify_token(probe_ship="MSC")
    except FileNotFoundError as e:
        rep.add("查询", FAIL, f"{e}",
                "查询这步需要（重新）引导 cookie，但没找到浏览器。"
                "用 --browser 指定第 7 步里那个 exe，或在 watchlist.json 里设 settings.edge_path")
        return
    except Exception as e:                                   # noqa: BLE001
        rep.add("查询", FAIL, f"{type(e).__name__}: {e}",
                "若提示 EdgeOne 挑战，见上一步；否则把完整报错发我")
        return

    if st.ok:
        rep.add("查询", OK, st.describe())
    else:
        rep.add("查询", FAIL, st.describe(),
                "cookie 可能已失效；重跑一次通常会自动重新引导")


# ------------------------------------------------------------------ 主流程


def main() -> int:
    # 必须在**任何输出之前**放宽 stdout —— 中文 Windows 下把输出重定向到文件时，
    # `✓` 不在 GBK 里，会直接抛 UnicodeEncodeError 把自检打断在半路。
    # 这正是"把日志发给我看"最常见的用法，见 ytmon/console.py 的说明。
    try:
        from ytmon.console import ensure_safe_stdout
        ensure_safe_stdout()
    except Exception:                                        # noqa: BLE001
        # 自检工具本身的职责是"报告哪里坏了"，绝不能因为兜底代码而启动不了
        try:
            sys.stdout.reconfigure(errors="replace")         # type: ignore[union-attr]
            sys.stderr.reconfigure(errors="replace")         # type: ignore[union-attr]
        except Exception:                                    # noqa: BLE001
            pass

    ap = argparse.ArgumentParser(description="盐田船期监控 —— 环境自检")
    ap.add_argument("--token", default=None,
                    help="可选。实测公众查询不需要 token，一般不用填")
    ap.add_argument("--browser", default=None, help="显式指定浏览器 exe 路径")
    ap.add_argument("--config", default="watchlist.json")
    args = ap.parse_args()

    token = args.token
    if not token:
        try:
            from ytmon.config import AppConfig, resolve_token
            token = resolve_token(AppConfig.load(args.config), None)
        except Exception:                                    # noqa: BLE001
            token = None

    print("=" * 70)
    print(f"  盐田船期监控 · 环境自检   {dt.datetime.now():%Y-%m-%d %H:%M:%S}")
    print("=" * 70)

    rep = Report()
    check_project(rep)
    check_env(rep)
    proto = check_network(rep)
    if proto:
        check_site(rep)
    browser = check_browser(rep, args.browser)
    booted = check_bootstrap(rep, browser, token)
    if booted:
        # 必须把 browser 传下去：缓存失效时这一步会重新引导 cookie，
        # 漏传就会退化成自动探测浏览器（Win7 上必然失败）。
        check_query(rep, token, browser)

    print("-" * 70)
    print(f"  结果：{rep.summary()}")
    if rep.failed:
        print(f"  卡在：{'、'.join(rep.failed)}")
    else:
        print("  各层均通过 —— 这台机器可以运行船期监控。")
    print("-" * 70)
    print("""
  还可以做一步人工确认（最能说明问题）：
    在这台机器上用浏览器打开下面的地址，看是正常显示查询页，
    还是停在一个空白/转圈的页面上（那就是 EdgeOne 挑战没过）：
      https://www.156yt.cn/pqs_revision/pages/jsp/popuPublic.jsp?loginVerifyCode=
""")
    return 1 if rep.failed else 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        raise SystemExit(130)
