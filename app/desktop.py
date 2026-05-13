from __future__ import annotations

import argparse
import os
import signal
import sys
import threading
import webbrowser
from pathlib import Path

from app.pybridge.server import BridgeServer


def default_static_root() -> Path | None:
    root = Path(__file__).resolve().parents[1]
    candidates = [
        root / "llm_wiki" / "dist",
        root / "dist",
    ]
    for candidate in candidates:
        if (candidate / "index.html").exists():
            return candidate
    return None


def main() -> None:
    parser = argparse.ArgumentParser(description="Run LLM Wiki with the Python desktop bridge.")
    parser.add_argument("--host", default=os.environ.get("LLMWIKI_HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("LLMWIKI_PORT", "0")))
    parser.add_argument("--static-root", type=Path, default=default_static_root())
    parser.add_argument("--browser", action="store_true", help="Open in the system browser instead of pywebview.")
    args = parser.parse_args()

    server = BridgeServer(static_root=args.static_root, host=args.host, port=args.port)
    url = server.start()
    stop_event = threading.Event()

    def shutdown(*_: object) -> None:
        server.stop()
        stop_event.set()

    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)

    if args.browser:
        print(url)
        webbrowser.open(url)
        stop_event.wait()
        return

    try:
        import webview
    except ImportError:
        print(f"pywebview is not installed; opening browser fallback at {url}", file=sys.stderr)
        webbrowser.open(url)
        stop_event.wait()
        return

    window = webview.create_window("LLM Wiki", url, width=1280, height=820, min_size=(980, 640))
    try:
        webview.start()
    finally:
        server.stop()


if __name__ == "__main__":
    main()
