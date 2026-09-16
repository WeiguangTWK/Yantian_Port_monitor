"""本机告警接收端 —— 用来在**不需要真机器人**的情况下验证告警链路。

用途：先在 Win7 上把"能不能发出去"这件事单独确认掉，再去折腾钉钉/企微的密钥。
只要监控机能往本机端口发 HTTP，就说明 requests、网络、payload 都没问题，
剩下的就只是机器人那边的配置。

用法：

    # 终端 1：起接收端
    python tools\\fake_webhook.py

    # 终端 2：把 watchlist.json 的 notify 指过来
    #   { "kind": "webhook", "url": "http://127.0.0.1:8799/hook" }
    python run_monitor.py --test-alert

接收端会把收到的每个请求的路径和正文打印出来，并回一个 200。

**只监听 127.0.0.1**，不对局域网开放 —— 它没有任何鉴权，
不该被别的机器碰到。要换端口用 `--port`。
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

DEFAULT_PORT = 8799
MAX_BODY = 1 << 20          # 1 MB 上限，防止有人往这里灌数据


def _out(line: str = "") -> None:
    """立刻输出。

    不加 flush 的话，stdout 被重定向到管道/文件时 Python 会缓冲，
    看起来就像"什么都没收到" —— 而接收端的作用恰恰是让人**当场**看到。
    """
    print(line, flush=True)


class Handler(BaseHTTPRequestHandler):
    server_version = "ytmon-fake-webhook/1.0"

    def do_POST(self):                                   # noqa: N802
        try:
            n = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            n = 0
        n = min(n, MAX_BODY)
        raw = self.rfile.read(n) if n else b""

        stamp = dt.datetime.now().strftime("%H:%M:%S")
        _out(f"\n[{stamp}] ← POST {self.path}  ({n} 字节)")
        if self.headers.get("Content-Type"):
            _out(f"           Content-Type: {self.headers['Content-Type']}")

        text = raw.decode("utf-8", "replace")
        try:
            pretty = json.dumps(json.loads(text), ensure_ascii=False, indent=2)
        except ValueError:
            pretty = text
        for line in pretty.splitlines():
            _out(f"           {line}")

        # 各家机器人的成功应答格式不同，这里回一个最通用的
        body = json.dumps({"errcode": 0, "errmsg": "ok"}).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):                                    # noqa: N802
        # 方便用浏览器确认"服务活着"
        body = ("ytmon 假 webhook 正在运行。\n"
                "把 watchlist.json 的 notify 指向 "
                f"http://127.0.0.1:{self.server.server_address[1]}/hook 即可。\n"
                ).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)
        _out(f"[{dt.datetime.now():%H:%M:%S}] ← GET {self.path}（有人在探活）")

    def log_message(self, fmt, *args):
        pass                                             # 用我们自己的格式打印


def main() -> int:
    # 中文 Windows 重定向到文件时，箭头等符号不在 GBK 里会抛异常
    try:
        import pathlib
        import sys
        sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
        from ytmon.console import ensure_safe_stdout
        ensure_safe_stdout()
    except Exception:                                        # noqa: BLE001
        pass

    ap = argparse.ArgumentParser(description="本机告警接收端（只监听 127.0.0.1）")
    ap.add_argument("--port", type=int, default=DEFAULT_PORT)
    ap.add_argument("--host", default="127.0.0.1",
                    help="默认只监听本机；除非你清楚风险，不要改成 0.0.0.0")
    args = ap.parse_args()

    srv = ThreadingHTTPServer((args.host, args.port), Handler)
    url = f"http://{args.host}:{args.port}/hook"
    _out(f"假 webhook 已启动：{url}")
    _out(f"  GET  {url.replace('/hook', '/')}  可确认服务是否活着")
    _out('\n把 watchlist.json 里配成：')
    _out(f'  "notify": [{{ "kind": "webhook", "url": "{url}" }}]')
    _out("\n然后另开一个窗口跑：  python run_monitor.py --test-alert")
    _out("\nCtrl+C 退出。\n")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        _out("\n已停止。")
    finally:
        srv.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
