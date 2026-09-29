"""Local stock search server. Run: python server.py [--port 8765] [--no-open]."""
from __future__ import annotations

import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import re
import threading
import time
from urllib.parse import quote, unquote, urlsplit
import webbrowser

import ai_chat
import chart


ROOT = Path(__file__).resolve().parent


def saved_payload(path: Path) -> dict:
    content = path.read_text(encoding="utf-8")
    match = re.search(r'<script id="chart-data" type="application/json">(.*?)</script>', content, re.S)
    if not match:
        raise ValueError("圖表資料格式不正確，請重新搜尋此代碼。")
    return json.loads(match.group(1))


class StockServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address, directory=ROOT, period="2y", colors=None):
        self.directory = Path(directory).resolve()
        self.period = period
        self.colors = colors
        # yfinance uses shared logging state; serialize downloads and file writes.
        self.search_lock = threading.Lock()
        self.last_refresh: dict[str, float] = {}
        super().__init__(address, StockHandler)


class StockHandler(BaseHTTPRequestHandler):
    server: StockServer

    def log_message(self, *_):
        pass

    def respond(self, status, body, content_type="application/json; charset=utf-8"):
        if isinstance(body, dict):
            body = json.dumps(body, ensure_ascii=False).encode("utf-8")
        elif isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def trusted_request(self):
        # Docker may publish the container on a different host port.
        host = self.headers.get("Host", "")
        match = re.fullmatch(r"(127\.0\.0\.1|localhost):([1-9][0-9]{0,4})", host)
        if not match or int(match.group(2)) > 65535:
            return False
        origin = self.headers.get("Origin")
        return origin is None or origin == f"http://{host}"

    def do_GET(self):
        if not self.trusted_request():
            self.respond(403, {"error": "僅接受本機網頁請求。"})
            return
        path = unquote(urlsplit(self.path).path)
        if path == "/api/chat/status":
            self.respond(200, {"configured": bool(os.environ.get("OPENAI_API_KEY", "").strip()),
                               "model": os.environ.get("OPENAI_MODEL", "gpt-4.1-mini")})
            return
        if path == "/":
            pages = sorted(self.server.directory.glob("*_chart.html"))
            default = self.server.directory / "MU_chart.html"
            if default.exists() or pages:
                self.send_response(302)
                self.send_header("Location", "/" + quote((default if default.exists() else pages[0]).name))
                self.send_header("Content-Length", "0")
                self.end_headers()
            else:
                search = (ROOT / "search.html").read_text(encoding="utf-8")
                self.respond(200, '<!doctype html><html lang="zh-Hant"><meta charset="utf-8">'
                             '<meta name="viewport" content="width=device-width,initial-scale=1">'
                             '<title>股票搜尋</title><body style="margin:0;background:#0b0f16;color:#dce4f0">'
                             '<h1 style="font:24px system-ui;padding:20px 26px">台美股日K</h1>'
                             + search + '</body></html>', "text/html; charset=utf-8")
            return
        # Serve only generated charts, never Python source or the virtual environment.
        if not re.fullmatch(r"/[A-Z0-9][A-Z0-9.^=\-]{0,31}_chart\.html", path):
            self.respond(404, {"error": "找不到此頁面。"})
            return
        target = self.server.directory / path[1:]
        if not target.is_file() or target.resolve().parent != self.server.directory:
            self.respond(404, {"error": "圖表尚未產生，請先搜尋股票代碼。"})
            return
        try:
            with self.server.search_lock:
                payload = saved_payload(target)
            # Existing saved charts receive the latest UI without a new download.
            self.respond(200, chart.render_html(payload), "text/html; charset=utf-8")
        except (ValueError, KeyError, OSError) as exc:
            self.respond(500, {"error": str(exc)})

    def do_POST(self):
        if not self.trusted_request():
            self.respond(403, {"error": "僅接受本機網頁請求。"})
            return
        if self.path == "/api/chat":
            self.chat()
            return
        if self.path == "/api/refresh":
            self.refresh()
            return
        if self.path != "/api/search":
            self.respond(404, {"error": "找不到此功能。"})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if not 0 < length <= 2048:
                raise ValueError("搜尋內容格式不正確。")
            if self.headers.get_content_type() != "application/json":
                raise ValueError("請使用 JSON 搜尋請求。")
            request = json.loads(self.rfile.read(length))
            if not isinstance(request, dict) or not isinstance(request.get("symbol"), str):
                raise ValueError("請輸入股票代碼。")
            symbol = request["symbol"].strip().upper()
            chart.symbol_candidates(symbol)
        except (ValueError, UnicodeError) as exc:
            self.respond(400, {"error": str(exc)})
            return
        try:
            with self.server.search_lock:
                stock = chart.fetch_stock(symbol, self.server.period)
                payload = chart.build_payload(stock, self.server.colors)
                chart.write_html(payload, self.server.directory)
                self.server.last_refresh[stock.symbol] = time.monotonic()
            self.respond(200, {"symbol": stock.symbol, "name": stock.name,
                               "url": f"/{quote(stock.symbol, safe='')}_chart.html"})
        except Exception:
            self.respond(422, {"error": f"無法取得 {symbol} 的日K資料，請確認代碼或稍後重試。"})

    def chat(self):
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if not 0 < length <= 16000 or self.headers.get_content_type() != "application/json":
                raise ai_chat.ChatError("對話內容格式不正確。", 400)
            body = json.loads(self.rfile.read(length))
            if not isinstance(body, dict):
                raise ai_chat.ChatError("對話內容格式不正確。", 400)
            symbol = body.get("symbol")
            if not isinstance(symbol, str) or not re.fullmatch(r"[A-Z0-9][A-Z0-9.^=\-]{0,31}", symbol):
                raise ai_chat.ChatError("股票代碼格式不正確。", 400)
            messages = ai_chat.validate_messages(body.get("messages"))
            target = self.server.directory / f"{symbol}_chart.html"
            if not target.is_file():
                raise ai_chat.ChatError("找不到這檔股票的圖表，請重新搜尋。", 404)
            with self.server.search_lock:
                payload = saved_payload(target)
            answer = ai_chat.ask(payload, messages)
            self.respond(200, {"answer": answer})
        except ai_chat.ChatError as exc:
            self.respond(exc.status, {"error": str(exc)})
        except (ValueError, UnicodeError, json.JSONDecodeError):
            self.respond(400, {"error": "對話內容格式不正確。"})

    def refresh(self):
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if not 0 < length <= 512 or self.headers.get_content_type() != "application/json":
                raise ValueError("更新內容格式不正確。")
            body = json.loads(self.rfile.read(length))
            if not isinstance(body, dict):
                raise ValueError("更新內容格式不正確。")
            symbol, seen = body.get("symbol"), body.get("generated")
            if not isinstance(symbol, str) or not re.fullmatch(r"[A-Z0-9][A-Z0-9.^=\-]{0,31}", symbol):
                raise ValueError("股票代碼格式不正確。")
            if not isinstance(seen, str) or len(seen) > 80:
                raise ValueError("圖表版本格式不正確。")
        except (ValueError, UnicodeError):
            self.respond(400, {"error": "更新內容格式不正確。"})
            return
        target = self.server.directory / f"{symbol}_chart.html"
        if not target.is_file():
            self.respond(404, {"error": "找不到這檔股票的圖表，請重新搜尋。"})
            return
        try:
            with self.server.search_lock:
                old = saved_payload(target)
                if old["symbol"] != symbol:
                    raise ValueError("圖表代碼不符。")
                now = time.monotonic()
                if now - self.server.last_refresh.get(symbol, float("-inf")) >= 60:
                    stock = chart.fetch_stock(symbol, self.server.period)
                    fresh = chart.build_payload(stock, self.server.colors)
                    if {k: v for k, v in fresh.items() if k != "generated"} != {k: v for k, v in old.items() if k != "generated"}:
                        chart.write_html(fresh, self.server.directory)
                        old = fresh
                    self.server.last_refresh[symbol] = now
            self.respond(200, {"changed": old["generated"] != seen, "asof": old["asof"],
                               "generated": old["generated"], "provisional": old["provisional"]})
        except Exception:
            self.respond(502, {"error": "暫時無法更新行情，已保留目前圖表。"})


def main(argv=None):
    parser = argparse.ArgumentParser(description="台美股圖表與股票搜尋網站")
    parser.add_argument("--host", choices=("127.0.0.1", "0.0.0.0"), default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--period", default="2y")
    parser.add_argument("--colors", choices=("tw", "us"))
    parser.add_argument("--no-open", action="store_true")
    args = parser.parse_args(argv)
    try:
        directory = Path(os.environ.get("STOCK_CHART_DATA_DIR", ROOT)).resolve()
        directory.mkdir(parents=True, exist_ok=True)
        server = StockServer((args.host, args.port), directory=directory,
                             period=args.period, colors=args.colors)
    except OSError as exc:
        parser.exit(1, f"無法啟動本機服務：{exc}\n")
    url = f"http://127.0.0.1:{server.server_address[1]}"
    print(f"股票搜尋已啟動：{url}（Ctrl+C 結束）", flush=True)
    if not args.no_open:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
