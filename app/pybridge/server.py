from __future__ import annotations

import json
import mimetypes
import socket
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse

from app.config import load_config
from app.pybridge.commands import BridgeContext, CommandDispatcher
from app.pybridge.events import EventHub
from app.pybridge.store import JsonAppStore


class BridgeServer:
    def __init__(self, static_root: Path | None = None, host: str = "127.0.0.1", port: int = 0) -> None:
        config = load_config()
        self.events = EventHub()
        self.dispatcher = CommandDispatcher(
            BridgeContext(
                config=config,
                events=self.events,
                app_store=JsonAppStore(config.root),
            )
        )
        self.static_root = static_root
        self.host = host
        self.port = port
        self.httpd: ThreadingHTTPServer | None = None
        self.thread: threading.Thread | None = None

    @property
    def url(self) -> str:
        if self.httpd is None:
            raise RuntimeError("Bridge server has not started")
        host, port = self.httpd.server_address[:2]
        return f"http://{host}:{port}"

    def start(self) -> str:
        handler = self._make_handler()
        self.httpd = ThreadingHTTPServer((self.host, self.port), handler)
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()
        return self.url

    def stop(self) -> None:
        if self.httpd is not None:
            self.httpd.shutdown()
            self.httpd.server_close()
        if self.thread is not None:
            self.thread.join(timeout=2)

    def _make_handler(self) -> type[BaseHTTPRequestHandler]:
        bridge = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, format: str, *args: Any) -> None:
                return

            def do_OPTIONS(self) -> None:
                self.send_response(204)
                self._cors()
                self.end_headers()

            def do_POST(self) -> None:
                if self.path != "/api/invoke":
                    self._json({"error": "Not found"}, status=404)
                    return
                try:
                    length = int(self.headers.get("Content-Length", "0"))
                    payload = json.loads(self.rfile.read(length).decode("utf-8") or "{}")
                    command = str(payload.get("command") or "")
                    args = payload.get("args") or {}
                    if not isinstance(args, dict):
                        raise ValueError("args must be an object")
                    result = bridge.dispatcher.invoke(command, args)
                    self._json({"ok": True, "result": result})
                except Exception as exc:
                    self._json({"ok": False, "error": str(exc)}, status=500)

            def do_GET(self) -> None:
                parsed = urlparse(self.path)
                if parsed.path == "/api/events":
                    self._events()
                    return
                if parsed.path == "/api/file":
                    self._file(parse_qs(parsed.query).get("path", [""])[0])
                    return
                self._static(parsed.path)

            def _events(self) -> None:
                subscriber = bridge.events.subscribe()
                self.send_response(200)
                self._cors()
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Cache-Control", "no-cache")
                self.send_header("Connection", "keep-alive")
                self.end_headers()
                try:
                    while True:
                        message = subscriber.get()
                        self.wfile.write(message.to_sse())
                        self.wfile.flush()
                except (BrokenPipeError, ConnectionError):
                    pass
                finally:
                    bridge.events.unsubscribe(subscriber)

            def _file(self, raw_path: str) -> None:
                path = Path(unquote(raw_path))
                if not path.exists() or not path.is_file():
                    self.send_error(404)
                    return
                content_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
                data = path.read_bytes()
                self.send_response(200)
                self._cors()
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def _static(self, request_path: str) -> None:
                if bridge.static_root is None:
                    self._html("LLM Wiki Python bridge is running. Build the React UI to serve the app from here.")
                    return
                rel = request_path.lstrip("/") or "index.html"
                path = (bridge.static_root / rel).resolve()
                root = bridge.static_root.resolve()
                if root not in path.parents and path != root:
                    self.send_error(403)
                    return
                if not path.exists() or path.is_dir():
                    path = bridge.static_root / "index.html"
                if not path.exists():
                    self.send_error(404)
                    return
                data = path.read_bytes()
                content_type = mimetypes.guess_type(path.name)[0] or "text/html"
                self.send_response(200)
                self._cors()
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def _json(self, payload: dict[str, Any], status: int = 200) -> None:
                data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
                self.send_response(status)
                self._cors()
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def _html(self, body: str) -> None:
                data = f"<!doctype html><meta charset='utf-8'><title>LLM Wiki</title><body>{body}</body>".encode("utf-8")
                self.send_response(200)
                self._cors()
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def _cors(self) -> None:
                self.send_header("Access-Control-Allow-Origin", "*")
                self.send_header("Access-Control-Allow-Headers", "content-type")
                self.send_header("Access-Control-Allow-Methods", "GET,POST,OPTIONS")

        return Handler


def find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])
