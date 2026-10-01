from __future__ import annotations

import argparse
import json
import os
import socket
import sys
import threading
import time
import urllib.error
import urllib.request
import webbrowser
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BUILD_ID = json.loads((ROOT / "resources" / "app-build.json").read_text(encoding="utf-8"))["build_id"]
URL = "http://127.0.0.1:8000/?v=" + BUILD_ID
HEALTH = "http://127.0.0.1:8000/api/health"


def running_studio() -> dict | None:
    try:
        with urllib.request.urlopen(HEALTH, timeout=1) as response:
            data = json.loads(response.read())
            return data if response.status == 200 and isinstance(data, dict) and data.get("adapter_kind") == "FAKE_CONTROL_FLOW" else None
    except (OSError, ValueError, urllib.error.URLError):
        return None


def existing_studio() -> bool:
    return running_studio() is not None


def port_busy() -> bool:
    with socket.socket() as sock:
        return sock.connect_ex(("127.0.0.1", 8000)) == 0


def open_when_ready() -> None:
    for _ in range(100):
        if existing_studio():
            webbrowser.open(URL)
            return
        time.sleep(0.1)


def main() -> int:
    parser = argparse.ArgumentParser(description="Start AI Animation Studio on this PC")
    parser.add_argument("--no-browser", action="store_true", help="Keep the server running without opening a browser")
    args = parser.parse_args()

    running = running_studio()
    if running:
        if running.get("build_id") != BUILD_ID:
            print("Ứng dụng đang chạy bản cũ. Đóng cửa sổ launcher đang chạy rồi mở lại Start-Studio.cmd để cập nhật.", file=sys.stderr)
            return 1
        print(f"AI Animation Studio is already running at {URL}")
        if not args.no_browser:
            webbrowser.open(URL)
        return 0
    if port_busy():
        print("Port 8000 is occupied by another application. Close it before starting Studio.", file=sys.stderr)
        return 1

    dist = ROOT / "apps" / "web" / "dist" / "index.html"
    if not dist.is_file():
        print("UI build missing. Run the one-time setup and UI build in README.md.", file=sys.stderr)
        return 1
    try:
        import uvicorn
        sys.path.insert(0, str(ROOT / "services" / "api"))
        from studio.standalone import create_standalone_app
    except ImportError as exc:
        print(f"Python dependencies missing: {exc}. Run the one-time setup in README.md.", file=sys.stderr)
        return 1

    os.environ.setdefault("STUDIO_DATA_ROOT", str(ROOT / "data"))
    app = create_standalone_app()
    print(f"Opening AI Animation Studio at {URL}")
    print("Close this window or press Ctrl+C to stop the app.")
    if not args.no_browser:
        threading.Thread(target=open_when_ready, daemon=True).start()
    uvicorn.run(app, host="127.0.0.1", port=8000, log_level="info")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
