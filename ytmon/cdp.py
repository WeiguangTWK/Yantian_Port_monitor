"""最小 CDP（Chrome DevTools Protocol）客户端。

为什么需要浏览器：站点前面有腾讯 EdgeOne 的 JS 挑战，纯 HTTP 客户端会被降级。
真实浏览器内核能自动通过，因此用 CDP 驱动本机浏览器。

依赖 aiohttp 和本机兼容的 Chromium 系浏览器，不自动下载浏览器。
"""

from __future__ import annotations

import asyncio
import pathlib
import subprocess
import time

import aiohttp

from .browser_find import find_browser

# 兼容旧名字（本模块曾有 find_edge / find_browser 两套）
find_edge = find_browser


def find_free_port(start: int = 9411, tries: int = 40) -> int:
    """挑一个没被占用的本地端口。"""
    import socket

    for port in range(start, start + tries):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            if s.connect_ex(("127.0.0.1", port)) != 0:
                return port
    raise RuntimeError("找不到空闲端口")


# ------------------------------------------------------------------- CDP 客户端


class CDPError(RuntimeError):
    pass


class CDP:
    """一个页面 target 上的 CDP 会话。"""

    def __init__(self, ws: aiohttp.ClientWebSocketResponse):
        self.ws = ws
        self._id = 0
        self._pending_events: list[dict] = []

    async def send(self, method: str, **params) -> dict:
        self._id += 1
        mid = self._id
        await self.ws.send_json({"id": mid, "method": method, "params": params})
        while True:
            msg = await self.ws.receive_json()
            if msg.get("id") == mid:
                if "error" in msg:
                    raise CDPError(f"{method} 失败：{msg['error']}")
                return msg.get("result", {})
            self._pending_events.append(msg)

    async def evaluate(self, expression: str, *, await_promise: bool = False):
        res = await self.send(
            "Runtime.evaluate",
            expression=expression,
            returnByValue=True,
            awaitPromise=await_promise,
        )
        if "exceptionDetails" in res:
            raise CDPError(f"页面内 JS 抛错：{res['exceptionDetails'].get('text')}")
        return res.get("result", {}).get("value")

    # -------------------------------------------------------------- 导航

    async def goto(self, url: str, timeout: float = 60.0) -> str:
        """导航并在页面稳定后返回最终 URL。

        页面稳定 = readyState 为 complete 且 location.href 连续两次未变。
        EdgeOne 挑战会自行 reload，这个循环天然能等到挑战结束。
        """
        await self.send("Page.navigate", url=url)
        deadline = time.monotonic() + timeout
        last: tuple | None = None
        stable = 0
        while time.monotonic() < deadline:
            await asyncio.sleep(0.6)
            try:
                href = await self.evaluate("location.href")
                state = await self.evaluate("document.readyState")
            except (CDPError, aiohttp.ClientError):
                continue
            cur = (href, state)
            if cur == last and state == "complete":
                stable += 1
                if stable >= 3:
                    return href or url
            else:
                stable = 0
                last = cur
        raise TimeoutError(f"页面在 {timeout:.0f}s 内未稳定：{url}")


class Browser:
    """启动/关闭一个隔离的无头 Chromium 系浏览器，并连上它的第一个页面 target。

    `edge_path` 参数名沿用旧称，实际接受任何 Chromium 系浏览器的路径
    （Edge / Chrome / Chromium / Supermium）。
    """

    def __init__(self, edge_path: str | None = None, profile_dir: str = ".browser_profile",
                 port: int | None = None, headless: bool = True):
        self.browser_path = find_browser(edge_path)
        self.profile_dir = str(pathlib.Path(profile_dir).resolve())
        self.port = port or find_free_port()
        self.headless = headless
        self.proc: subprocess.Popen | None = None
        self._session: aiohttp.ClientSession | None = None
        self._ws: aiohttp.ClientWebSocketResponse | None = None
        self._log_file = None
        self.browser_log_path: str | None = None
        self.cdp: CDP | None = None

    @property
    def edge_path(self) -> str:
        """旧属性名，保留兼容。"""
        return self.browser_path

    # ------------------------------------------------------------ 浏览器输出

    def _open_browser_log(self) -> None:
        """给浏览器进程开一个日志文件。

        **不要用 DEVNULL。** 浏览器起不来时，真正的原因（缺 DLL、
        参数不认识、用户数据目录不可写……）就写在它自己的 stderr 里，
        扔掉之后用户只能看到一句毫无信息量的
        `RuntimeError: 浏览器起不来：浏览器的调试端口未就绪`。
        """
        if self._log_file is not None:
            return
        import tempfile

        fd = tempfile.NamedTemporaryFile(
            mode="w+b", prefix="ytmon-browser-", suffix=".log", delete=False)
        self._log_file = fd
        self.browser_log_path = fd.name

    def _browser_log_tail(self, max_lines: int = 12) -> str:
        """读浏览器输出的末尾几行（出错时用来解释原因）。"""
        if self._log_file is None or not self.browser_log_path:
            return ""
        try:
            self._log_file.flush()
            raw = pathlib.Path(self.browser_log_path).read_bytes()
        except OSError:
            return ""
        text = raw.decode("utf-8", "replace").strip()
        if not text:
            return ""
        lines = [ln for ln in text.splitlines() if ln.strip()]
        tail = lines[-max_lines:]
        return "\n".join(f"        | {ln}" for ln in tail)

    def _close_browser_log(self) -> None:
        if self._log_file is not None:
            try:
                self._log_file.close()
            except OSError:
                pass
        self._log_file = None

    def _build_args(self, headless_flag: str | None) -> list[str]:
        args = [
            self.browser_path,
            f"--remote-debugging-port={self.port}",
            f"--user-data-dir={self.profile_dir}",
            "--no-first-run",
            "--no-default-browser-check",
            "--disable-gpu",
            "--disable-extensions",
            "--disable-background-networking",
            "--disable-sync",
            "--disable-features=Translate,MediaRouter",
            # Win7 上没有现代 GPU 栈，关掉相关特性可避免启动即崩
            "--disable-software-rasterizer",
        ]
        if headless_flag:
            args.append(headless_flag)
        args.append("about:blank")
        return args

    async def __aenter__(self) -> "Browser":
        pathlib.Path(self.profile_dir).mkdir(parents=True, exist_ok=True)
        self._open_browser_log()

        # `--headless=new` 是 Chromium 112+ 才有的；Win7 上 Edge 最高 109，
        # 只认旧的 `--headless`。所以按新→旧顺序试，哪个能起来用哪个。
        flags: list[str | None] = ["--headless=new", "--headless"] if self.headless else [None]
        last_err: Exception | None = None

        for idx, flag in enumerate(flags):
            if idx:
                # 上一次尝试可能留下了占用端口的残留进程，换一个端口重试
                self.port = find_free_port(self.port + 1)
            self.proc = subprocess.Popen(
                self._build_args(flag),
                stdout=self._log_file, stderr=subprocess.STDOUT)
            self._session = aiohttp.ClientSession()
            try:
                ws_url = await self._wait_for_target(timeout=30)
                self._ws = await self._session.ws_connect(ws_url, max_msg_size=0, heartbeat=30)
            except (TimeoutError, aiohttp.ClientError) as e:
                last_err = e
                await self._teardown()
                continue
            self.cdp = CDP(self._ws)
            await self.cdp.send("Page.enable")
            await self.cdp.send("Runtime.enable")
            return self

        tail = self._browser_log_tail()
        log_path = self.browser_log_path
        # __aenter__ 抛异常时 __aexit__ 不会被调用，这里得自己收尾。
        # 注意 delete=False，文件保留下来供用户查看。
        self._close_browser_log()

        hint = (f"\n        浏览器自己的输出（末尾）：\n{tail}" if tail
                else "\n        （浏览器没有输出任何东西 —— 通常是它根本没起来）")
        log_hint = f"\n        完整输出在：{log_path}" if log_path else ""
        raise RuntimeError(
            f"浏览器起不来（{pathlib.Path(self.browser_path).name}）：{last_err}\n"
            f"        已尝试的无头参数：{[f for f in flags if f]}\n"
            f"        可试：去掉 --headless（配置 settings.headless=false）观察报错"
            f"{hint}{log_hint}"
        )

    async def _teardown(self) -> None:
        """清掉当前这次尝试留下的进程与连接，便于重试。"""
        try:
            if self._ws and not self._ws.closed:
                await self._ws.close()
        except Exception:                                    # noqa: BLE001
            pass
        self._ws = None
        if self._session:
            try:
                await self._session.close()
            except Exception:                                # noqa: BLE001
                pass
        self._session = None
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.proc.kill()
        self.proc = None

    async def _wait_for_target(self, timeout: float = 40.0) -> str:
        assert self._session
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                async with self._session.get(
                    f"http://127.0.0.1:{self.port}/json/list"
                ) as r:
                    targets = await r.json()
                page = next((t for t in targets if t.get("type") == "page"), None)
                if page:
                    return page["webSocketDebuggerUrl"]
            except (aiohttp.ClientError, asyncio.TimeoutError, ValueError):
                pass
            await asyncio.sleep(0.4)
        raise TimeoutError("浏览器的调试端口未就绪")

    async def __aexit__(self, *exc):
        await self._teardown()
        self._close_browser_log()
