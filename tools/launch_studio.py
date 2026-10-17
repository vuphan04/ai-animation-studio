"""Starts AI Animation Studio on this PC.

tools/Start-Studio.cmd opens the launcher window (`--window`): it runs the server in this process, shows the log as it
is written and stops the app when it closes. Without `--window` the server runs in the console, for development.
Each start also keeps the one double-click entry in the app folder: the Start-Studio shortcut with the capybara icon.
"""

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
PORT = 8000
URL = f"http://127.0.0.1:{PORT}/?v=" + BUILD_ID
HEALTH = f"http://127.0.0.1:{PORT}/api/health"
WINDOW_TITLE = "AI Animation Studio · Launcher"
sys.path.insert(0, str(ROOT / "services" / "api"))


def app_url(port: int = PORT) -> str:
    return f"http://127.0.0.1:{port}/?v=" + BUILD_ID


def running_studio(port: int = PORT) -> dict | None:
    try:
        with urllib.request.urlopen(HEALTH if port == PORT else f"http://127.0.0.1:{port}/api/health", timeout=1) as response:
            data = json.loads(response.read())
            # Builds before 2026.10.05.2 identified themselves with the adapter name; they are still recognised so the
            # launcher can say "an older version is running" instead of "the port is taken by another program".
            ours = isinstance(data, dict) and (data.get("app") == "ai-animation-studio" or data.get("adapter_kind") == "FAKE_CONTROL_FLOW")
            return data if response.status == 200 and ours else None
    except (OSError, ValueError, urllib.error.URLError):
        return None


def existing_studio(port: int = PORT) -> bool:
    return running_studio(port) is not None


def port_busy(port: int = PORT) -> bool:
    with socket.socket() as sock:
        # A listening port answers within a millisecond; on Windows a closed one is retried for seconds before it refuses.
        sock.settimeout(0.3)
        return sock.connect_ex(("127.0.0.1", port)) == 0


def open_when_ready(port: int = PORT) -> None:
    for _ in range(100):
        if existing_studio(port):
            webbrowser.open(app_url(port))
            return
        time.sleep(0.1)


def data_root() -> Path:
    return Path(os.environ.setdefault("STUDIO_DATA_ROOT", str(ROOT / "data")))


def ui_built() -> bool:
    return (ROOT / "apps" / "web" / "dist" / "index.html").is_file()


def ensure_shortcut(root: Path = ROOT) -> str:
    """Creates or repairs Start-Studio.lnk in the app folder; returns "created", "updated" or "unchanged".

    A shortcut stores absolute paths, so it cannot come with the repository: it is made on each PC at the first start
    and remade when the folder has moved.
    """
    import win32com.client

    link = root / "Start-Studio.lnk"
    wanted = {"TargetPath": str(root / "tools" / "Start-Studio.cmd"), "WorkingDirectory": str(root),
              "IconLocation": f"{root / 'assets' / 'launcher' / 'studio.ico'},0"}
    existed = link.is_file()
    shortcut = win32com.client.Dispatch("WScript.Shell").CreateShortcut(str(link))
    if existed and all(str(getattr(shortcut, name)).casefold() == value.casefold() for name, value in wanted.items()):
        return "unchanged"
    for name, value in wanted.items():
        setattr(shortcut, name, value)
    shortcut.Description = "AI Animation Studio"
    shortcut.Save()
    return "updated" if existed else "created"


def refresh_shortcut(log) -> None:
    try:
        result = ensure_shortcut()
    except Exception as exc:  # the app runs without it; tools\Start-Studio.cmd still opens it
        log.warning("Could not create the Start-Studio shortcut in %s: %s: %s", ROOT, type(exc).__name__, exc)
        return
    if result != "unchanged":
        log.info("%s the Start-Studio shortcut in %s", result.capitalize(), ROOT)


class ServerRunner:
    """The app's HTTP server on a background thread of this process, so the launcher window stays responsive.

    States: STOPPED, STARTING, RUNNING, STOPPING, FAILED. `problem` names why it is not running, for the window.
    """

    def __init__(self, port: int = PORT, *, app_factory=None):
        from studio import logs

        self.port = port
        self.log = logs.get("launcher")
        self.app_factory = app_factory
        self.state, self.problem = "STOPPED", None
        self._server = None
        self._thread: threading.Thread | None = None
        self._stopping = False
        self._began = time.monotonic()

    @property
    def url(self) -> str:
        return app_url(self.port)

    def preflight(self) -> str | None:
        if port_busy(self.port):
            running = running_studio(self.port)
            if running:
                return "OLD_BUILD" if running.get("build_id") != BUILD_ID else "ALREADY_RUNNING"
            return "PORT_BUSY"
        if self.app_factory is None and not ui_built():
            return "NO_UI"
        return None

    def start(self) -> bool:
        if self.state in ("STARTING", "RUNNING"):
            return True
        began = time.monotonic()
        problem = self.preflight()
        if problem:
            self.state, self.problem = "FAILED", problem
            self.log.error("Not started: %s (port %d)", problem.replace("_", " ").lower(), self.port)
            return False
        import uvicorn

        checked = time.monotonic()
        try:
            if self.app_factory:
                app = self.app_factory()
            else:
                from studio.standalone import create_standalone_app
                app = create_standalone_app()
        except Exception:
            self.state, self.problem = "FAILED", "START_ERROR"
            self.log.exception("The app could not be built")
            return False
        self.log.debug("Port check took %.2f s, opening the data folder and building the app %.2f s", checked - began, time.monotonic() - checked)
        self._began = began
        # The app's own logging is already set up; uvicorn must not replace it or print an access line per request.
        config = uvicorn.Config(app, host="127.0.0.1", port=self.port, log_config=None, access_log=False, log_level="info")
        self._server = uvicorn.Server(config)
        self._stopping = False
        self.state, self.problem = "STARTING", None
        self.log.info("Starting the server on port %d", self.port)
        self._thread = threading.Thread(target=self._serve, name="studio-http", daemon=True)
        self._thread.start()
        return True

    def _serve(self) -> None:
        failed = False
        try:
            self._server.run()
        except BaseException:  # uvicorn exits the thread with SystemExit when it cannot bind the port
            failed = True
            self.log.exception("The server stopped with an error")
        if self._stopping:
            self.state = "STOPPED"
        elif failed or not self._server.started:
            self.state, self.problem = "FAILED", "START_ERROR"
        else:
            self.state = "STOPPED"
            self.log.warning("The server stopped by itself")

    def poll(self) -> str:
        if self.state == "STARTING" and self._server is not None and self._server.started:
            self.state = "RUNNING"
            self.log.info("AI Animation Studio is running at %s (started in %.1f s)", self.url, time.monotonic() - self._began)
        return self.state

    def stop(self, timeout: float = 15.0) -> None:
        if self._thread is None or not self._thread.is_alive():
            if self.state != "FAILED":
                self.state = "STOPPED"
            return
        self._stopping = True
        self.state = "STOPPING"
        self.log.info("Stopping the server")
        self._server.should_exit = True
        self._thread.join(timeout)
        if self._thread.is_alive():
            self._server.force_exit = True
            self._thread.join(5)
        self.state = "STOPPED"
        self.log.info("The server has stopped")


def focus_window(title: str = WINDOW_TITLE) -> bool:
    """Brings an open launcher window forward; False when there is none (the app may run from a console)."""
    try:
        import win32con
        import win32gui
        handle = win32gui.FindWindow(None, title)
        if not handle:
            return False
        win32gui.ShowWindow(handle, win32con.SW_RESTORE)
        win32gui.SetForegroundWindow(handle)
        return True
    except Exception:
        return False


def notify(message: str, *, error: bool = True) -> None:
    """A message for the person who started the app: a dialog when there is no console, else standard error."""
    if sys.stderr is not None and sys.stdout is not None and not sys.executable.lower().endswith("pythonw.exe"):
        print(message, file=sys.stderr if error else sys.stdout)
        return
    try:
        import tkinter
        from tkinter import messagebox
        root = tkinter.Tk()
        root.withdraw()
        (messagebox.showerror if error else messagebox.showinfo)(WINDOW_TITLE, message, parent=root)
        root.destroy()
    except Exception:
        pass


def run_console(args) -> int:
    if port_busy(args.port):
        print(f"Port {args.port} is occupied by another application. Close it before starting Studio.", file=sys.stderr)
        return 1
    if not ui_built():
        print("UI build missing. Run the one-time setup and UI build in README.md.", file=sys.stderr)
        return 1
    try:
        import uvicorn
        from studio import logs
        from studio.standalone import create_standalone_app
    except ImportError as exc:
        print(f"Python dependencies missing: {exc}. Run the one-time setup in README.md.", file=sys.stderr)
        return 1
    root = data_root()
    system = logs.setup(root, logs.Settings(root).load(), console=True)
    log = logs.get("launcher")
    log.info("AI Animation Studio %s, Python %s, data folder %s", BUILD_ID, sys.version.split()[0], root)
    log.info("Log file: %s", system.file.path)
    refresh_shortcut(log)
    app = create_standalone_app()
    log.info("Opening AI Animation Studio at %s. Close this window or press Ctrl+C to stop the app.", app_url(args.port))
    if not args.no_browser:
        threading.Thread(target=open_when_ready, args=(args.port,), daemon=True).start()
    try:
        uvicorn.run(app, host="127.0.0.1", port=args.port, log_config=None, access_log=False, log_level="info")
    finally:
        log.info("Stopped")
        system.close()
    return 0


def run_window(args) -> int:
    from studio import logs

    root = data_root()
    store = logs.Settings(root)
    settings = store.load()
    system = logs.setup(root, settings)
    log = logs.get("launcher")
    # Under pythonw.exe there is no console; anything printed becomes a log line instead of being lost.
    if sys.stdout is None:
        sys.stdout = logs.StreamToLog(logs.get("stdout"), logs.logging.INFO)
    if sys.stderr is None:
        sys.stderr = logs.StreamToLog(logs.get("stderr"), logs.logging.WARNING)
    log.info("AI Animation Studio %s, Python %s, data folder %s", BUILD_ID, sys.version.split()[0], root)
    log.info("Log file: %s (%s, kept %d days)", system.file.path, settings["file_mode"].lower(), settings["keep_days"])
    refresh_shortcut(log)
    try:
        if __package__:
            from . import launcher_window
        else:
            import launcher_window
        runner = ServerRunner(args.port)
        launcher_window.run(runner=runner, system=system, store=store, build_id=BUILD_ID, open_browser=not args.no_browser, title=WINDOW_TITLE)
    except Exception:
        log.exception("The launcher window failed")
        notify("Launcher gặp lỗi và phải đóng. Xem file log trong thư mục data\\logs.\n"
               "The launcher hit an error and has to close. See the log file in data\\logs.")
        return 1
    finally:
        system.close()
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Start AI Animation Studio on this PC")
    parser.add_argument("--no-browser", action="store_true", help="Keep the server running without opening a browser")
    parser.add_argument("--window", action="store_true", help="Open the launcher window with the live log (what tools/Start-Studio.cmd uses)")
    parser.add_argument("--console", action="store_true", help="Run in this console without a window (the default)")
    # The API only accepts its own page from these two ports; 5173 is for checks next to a running copy.
    parser.add_argument("--port", type=int, choices=(PORT, 5173), default=PORT, help="Development and diagnostics only")
    args = parser.parse_args(argv)

    running = running_studio(args.port) if port_busy(args.port) else None
    if running:
        if running.get("build_id") != BUILD_ID:
            notify("Ứng dụng đang chạy bản cũ. Đóng cửa sổ launcher đang chạy rồi mở lại Start-Studio để cập nhật.")
            return 1
        if not (args.window and focus_window()) and sys.stdout is not None:
            print(f"AI Animation Studio is already running at {app_url(args.port)}")
        if not args.no_browser:
            webbrowser.open(app_url(args.port))
        return 0
    return run_window(args) if args.window and not args.console else run_console(args)


if __name__ == "__main__":
    raise SystemExit(main())
