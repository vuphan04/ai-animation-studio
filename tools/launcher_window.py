"""The launcher window: the server's state and controls, and the app's log as it is written.

What the operator sets here (language, filters, log file settings, window size) is saved in <data>/launcher.json.
"""

from __future__ import annotations

import ctypes
import os
import re
import subprocess
import tkinter as tk
import webbrowser
from datetime import datetime
from pathlib import Path
from tkinter import filedialog, font as tkfont, messagebox, ttk

from studio import logs

ROOT = Path(__file__).resolve().parents[1]
ICON = ROOT / "assets" / "launcher" / "studio.ico"
POLL_MS = 150
MAX_ROWS = 5000
LEVEL_COLORS = {"DEBUG": ("#6b7280", ""), "WARNING": ("#92400e", "#fef3c7"), "ERROR": ("#991b1b", "#fee2e2")}
STATE_COLORS = {"STARTING": "#d97706", "RUNNING": "#16a34a", "STOPPING": "#d97706", "STOPPED": "#6b7280", "FAILED": "#dc2626"}
SIZE = re.compile(r"^\d{3,5}x\d{3,5}$")

TEXT = {
    "vi": {
        "state_STARTING": "Đang khởi động…", "state_RUNNING": "Đang chạy tại {0}", "state_STOPPING": "Đang dừng…",
        "state_STOPPED": "Đã dừng", "state_FAILED": "Không khởi động được",
        "problem_PORT_BUSY": "Cổng {0} đang bị chương trình khác dùng. Đóng chương trình đó rồi bấm Khởi động.",
        "problem_OLD_BUILD": "Một bản cũ của app đang chạy ở cổng {0}. Đóng cửa sổ launcher cũ rồi bấm Khởi động.",
        "problem_ALREADY_RUNNING": "App đã chạy ở cổng {0} từ một cửa sổ khác.",
        "problem_NO_UI": "Thiếu bản build giao diện. Đóng cửa sổ này rồi mở lại Start-Studio để chuẩn bị lại.",
        "problem_START_ERROR": "Máy chủ gặp lỗi khi chạy; xem các dòng ERROR bên dưới hoặc bấm Export Logs.",
        "build": "Bản {0}", "open_app": "Mở app", "start": "Khởi động", "stop": "Dừng", "restart": "Khởi động lại",
        "search": "Tìm", "module": "Module", "all_modules": "Tất cả", "levels": "Mức",
        "errors_only": "Chỉ hiện lỗi", "autoscroll": "Tự cuộn", "clear": "Xóa màn hình", "export": "Export Logs…",
        "col_time": "Thời gian", "col_level": "Mức", "col_module": "Module", "col_message": "Nội dung",
        "counts": "Đang hiện {0} / {1} dòng · {2} lỗi · {3} cảnh báo", "log_file": "File log: {0}",
        "open_folder": "Mở thư mục log", "settings": "Cài đặt log…",
        "detail_hint": "Chọn một dòng để xem đầy đủ, kể cả traceback. Ctrl+C sao chép các dòng đã chọn; chuột phải để lọc theo module.",
        "confirm_close_title": "Đóng launcher", "confirm_close": "Đóng cửa sổ này sẽ dừng AI Animation Studio. Đóng?",
        "export_title": "Xuất log", "export_done": "Đã xuất log ra:\n{0}\n\nMở thư mục chứa file?", "export_failed": "Không xuất được log: {0}",
        "settings_title": "Cài đặt log", "file_mode": "Cách tạo file log",
        "mode_DAY": "Một file mỗi ngày ({0})", "mode_SESSION": "Một file mỗi lần chạy ({0})",
        "keep_days": "Tự xóa file log cũ hơn (ngày)", "file_debug": "Ghi cả dòng DEBUG (mỗi request thành công) vào file",
        "folder": "Thư mục: {0}", "save": "Lưu", "cancel": "Hủy", "keep_days_invalid": "Nhập số ngày từ 1 đến 365.",
        "saved": "Đã lưu cài đặt log.", "saved_pruned": "Đã lưu cài đặt log; đã xóa {0} file log cũ.",
        "copy": "Sao chép", "only_module": "Chỉ xem module {0}", "all_modules_menu": "Xem mọi module",
        "cleared": "Đã xóa màn hình; log vẫn được ghi vào file.", "open_failed": "Không mở được: {0}",
    },
    "en": {
        "state_STARTING": "Starting…", "state_RUNNING": "Running at {0}", "state_STOPPING": "Stopping…",
        "state_STOPPED": "Stopped", "state_FAILED": "Could not start",
        "problem_PORT_BUSY": "Port {0} is used by another program. Close it, then click Start.",
        "problem_OLD_BUILD": "An older version of the app is running on port {0}. Close its launcher window, then click Start.",
        "problem_ALREADY_RUNNING": "The app is already running on port {0} from another window.",
        "problem_NO_UI": "The UI build is missing. Close this window and open Start-Studio again to prepare it.",
        "problem_START_ERROR": "The server hit an error; see the ERROR lines below or click Export Logs.",
        "build": "Build {0}", "open_app": "Open app", "start": "Start", "stop": "Stop", "restart": "Restart",
        "search": "Search", "module": "Module", "all_modules": "All", "levels": "Levels",
        "errors_only": "Errors only", "autoscroll": "Auto-scroll", "clear": "Clear view", "export": "Export Logs…",
        "col_time": "Time", "col_level": "Level", "col_module": "Module", "col_message": "Message",
        "counts": "Showing {0} of {1} lines · errors: {2} · warnings: {3}", "log_file": "Log file: {0}",
        "open_folder": "Open log folder", "settings": "Log settings…",
        "detail_hint": "Select a line to see it in full, traceback included. Ctrl+C copies the selected lines; right-click to filter by module.",
        "confirm_close_title": "Close the launcher", "confirm_close": "Closing this window stops AI Animation Studio. Close it?",
        "export_title": "Export logs", "export_done": "Logs exported to:\n{0}\n\nOpen the folder that contains it?", "export_failed": "Could not export the logs: {0}",
        "settings_title": "Log settings", "file_mode": "Log files",
        "mode_DAY": "One file per day ({0})", "mode_SESSION": "One file per run ({0})",
        "keep_days": "Delete log files older than (days)", "file_debug": "Also write DEBUG lines (every successful request) to the file",
        "folder": "Folder: {0}", "save": "Save", "cancel": "Cancel", "keep_days_invalid": "Enter a number of days from 1 to 365.",
        "saved": "Log settings saved.", "saved_pruned": "Log settings saved; {0} old log files deleted.",
        "copy": "Copy", "only_module": "Show only module {0}", "all_modules_menu": "Show every module",
        "cleared": "View cleared; the log is still written to the file.", "open_failed": "Could not open: {0}",
    },
}


def prepare_process() -> None:
    """Sharp text on high-DPI screens, and the app's own taskbar icon instead of Python's."""
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        pass
    try:
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("AIAnimationStudio.Launcher")
    except Exception:
        pass


def run(*, runner, system, store, build_id: str, open_browser: bool, title: str) -> None:
    prepare_process()
    root = tk.Tk()
    LauncherWindow(root, runner=runner, system=system, store=store, build_id=build_id, open_browser=open_browser, title=title).start()
    root.mainloop()


class LauncherWindow:
    def __init__(self, root: tk.Tk, *, runner, system, store, build_id: str, open_browser: bool = True,
                 title: str = "AI Animation Studio · Launcher", browser=webbrowser.open, folder_opener=None,
                 save_dialog=None, ask=None, tell=None):
        self.root, self.runner, self.system, self.store, self.build_id = root, runner, system, store, build_id
        self.settings = store.load()
        self.lang = self.settings["language"]
        self.open_browser, self.browser = open_browser, browser
        self.folder_opener = folder_opener or os.startfile
        self.save_dialog = save_dialog or filedialog.asksaveasfilename
        self.ask = ask or messagebox.askyesno
        self.tell = tell or messagebox.showerror
        self.log = logs.get("launcher")
        self.rows: dict[str, logs.Entry] = {}
        self.modules: set[str] = set()
        self.counts = {"ERROR": 0, "WARNING": 0}
        self.hidden_before = 0
        self._opened = False
        self._shown_state = None
        self._after = None
        self._search_after = None
        self._notice_after = None
        self._labels: list[tuple] = []
        self.search = tk.StringVar(root)
        self.module = tk.StringVar(root)
        self.level_vars = {level: tk.BooleanVar(root, value=level in self.settings["levels"]) for level in logs.LEVELS}
        self.errors_only = tk.BooleanVar(root, value=self.settings["errors_only"])
        self.autoscroll = tk.BooleanVar(root, value=self.settings["autoscroll"])
        self.language = tk.StringVar(root, value=self.lang)
        root.title(title)
        if ICON.is_file():
            try:
                root.iconbitmap(default=str(ICON))
            except tk.TclError:
                pass
        # Tk counts in screen pixels once the process is DPI aware, so the first size follows the display scaling.
        scale = max(1.0, root.winfo_fpixels("1i") / 96)
        width, height = min(int(1180 * scale), int(root.winfo_screenwidth() * 0.92)), min(int(720 * scale), int(root.winfo_screenheight() * 0.85))
        root.geometry(self.settings["geometry"] if SIZE.match(self.settings["geometry"]) else f"{width}x{height}")
        root.minsize(min(int(860 * scale), width), min(int(480 * scale), height))
        root.report_callback_exception = lambda kind, value, trace: self.log.error("Launcher window error", exc_info=(kind, value, trace))
        root.protocol("WM_DELETE_WINDOW", self.close)
        self._build()
        self.relabel()

    # ---- text --------------------------------------------------------------------------------------------------

    def t(self, key: str, *args) -> str:
        text = TEXT[self.lang].get(key, key)
        return text.format(*args) if args else text

    def _text(self, widget, key: str, option: str = "text"):
        self._labels.append((widget, key, option))
        return widget

    def relabel(self) -> None:
        for widget, key, option in self._labels:
            widget.configure(**{option: self.t(key)})
        for column in ("time", "level", "module", "message"):
            self.tree.heading(column, text=self.t("col_" + column))
        self.build_label.configure(text=self.t("build", self.build_id))
        self._update_modules()
        self._show_state(self.runner.state, force=True)
        self._update_counts()
        self._update_file_label()
        self._show_detail()

    def _language_changed(self) -> None:
        old_all = self.t("all_modules")
        self.lang = self.language.get()
        if self.module.get() in ("", old_all):
            self.module.set(self.t("all_modules"))
        self.settings = self.store.save(language=self.lang)
        self.relabel()

    # ---- layout ------------------------------------------------------------------------------------------------

    def _build(self) -> None:
        root = self.root
        style = ttk.Style(root)
        mono = tkfont.Font(root, family="Consolas", size=10)
        bold = tkfont.nametofont("TkDefaultFont").copy()
        bold.configure(weight="bold", size=bold.cget("size") + 2)
        style.configure("Log.Treeview", font=mono, rowheight=mono.metrics("linespace") + 6)
        style.configure("Status.TLabel", font=bold)
        style.configure("Muted.TLabel", foreground="#6b7280")
        style.configure("Problem.TLabel", foreground="#b91c1c")

        top = self.top = ttk.Frame(root, padding=(12, 10, 12, 4))
        top.pack(fill="x")
        self.dot = tk.Canvas(top, width=14, height=14, highlightthickness=0, bd=0, background=style.lookup("TFrame", "background") or None)
        self.dot_item = self.dot.create_oval(2, 2, 13, 13, fill=STATE_COLORS["STOPPED"], outline="")
        self.dot.pack(side="left")
        self.status = ttk.Label(top, style="Status.TLabel")
        self.status.pack(side="left", padx=(8, 12))
        self.build_label = ttk.Label(top, style="Muted.TLabel")
        self.build_label.pack(side="left")
        languages = ttk.Frame(top)
        languages.pack(side="right", padx=(12, 0))
        for code in ("vi", "en"):
            ttk.Radiobutton(languages, text=code.upper(), value=code, variable=self.language, command=self._language_changed, style="Toolbutton").pack(side="left")
        self.restart_button = self._text(ttk.Button(top, command=self.restart), "restart")
        self.restart_button.pack(side="right", padx=(6, 0))
        self.toggle_button = ttk.Button(top, command=self.toggle)
        self.toggle_button.pack(side="right", padx=(6, 0))
        self.open_button = self._text(ttk.Button(top, command=self.open_app), "open_app")
        self.open_button.pack(side="right")
        # Shown under the status only while the server is not running for a known reason.
        self.problem = ttk.Label(root, style="Problem.TLabel", padding=(34, 0, 12, 4), wraplength=1000, justify="left")

        bar = self.bar = ttk.Frame(root, padding=(12, 4, 12, 6))
        bar.pack(fill="x")
        self._text(ttk.Label(bar), "search").pack(side="left")
        search = ttk.Entry(bar, textvariable=self.search, width=28)
        search.pack(side="left", padx=(6, 12))
        self.search.trace_add("write", lambda *_: self._search_changed())
        self._text(ttk.Label(bar), "module").pack(side="left")
        self.module_box = ttk.Combobox(bar, textvariable=self.module, state="readonly", width=18)
        self.module_box.pack(side="left", padx=(6, 12))
        self.module_box.bind("<<ComboboxSelected>>", lambda _event: self.refresh())
        self._text(ttk.Label(bar), "levels").pack(side="left", padx=(0, 4))
        self.level_boxes = {}
        for level in logs.LEVELS:
            box = ttk.Checkbutton(bar, text=level, variable=self.level_vars[level], command=self._filters_changed)
            box.pack(side="left", padx=(0, 4))
            self.level_boxes[level] = box
        ttk.Separator(bar, orient="vertical").pack(side="left", fill="y", padx=8)
        self._text(ttk.Checkbutton(bar, variable=self.errors_only, command=self._filters_changed), "errors_only").pack(side="left")
        self._text(ttk.Checkbutton(bar, variable=self.autoscroll, command=self._autoscroll_changed), "autoscroll").pack(side="left", padx=(8, 0))

        body = ttk.PanedWindow(root, orient="vertical")
        body.pack(fill="both", expand=True, padx=12)
        table = ttk.Frame(body)
        self.tree = ttk.Treeview(table, columns=("time", "level", "module", "message"), show="headings", style="Log.Treeview", selectmode="extended")
        pad = mono.measure("  ") + 12
        for column, sample, stretch in (("time", "00:00:00.000", False), ("level", "WARNING", False), ("module", "claude_code.x", False), ("message", "x" * 60, True)):
            width = mono.measure(sample) + pad
            self.tree.column(column, width=width, minwidth=min(width, mono.measure("0000") + pad), stretch=stretch, anchor="w")
        for level, (foreground, background) in LEVEL_COLORS.items():
            self.tree.tag_configure(level, foreground=foreground, **({"background": background} if background else {}))
        scroll = ttk.Scrollbar(table, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=scroll.set)
        self.tree.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")
        self.tree.bind("<<TreeviewSelect>>", lambda _event: self._show_detail())
        self.tree.bind("<Control-c>", lambda _event: self.copy_selection())
        self.tree.bind("<Button-3>", self._context_menu)
        details = ttk.Frame(body)
        self.detail = tk.Text(details, height=5, wrap="word", font=mono, borderwidth=1, relief="solid", padx=8, pady=6)
        detail_scroll = ttk.Scrollbar(details, orient="vertical", command=self.detail.yview)
        self.detail.configure(yscrollcommand=detail_scroll.set, state="disabled")
        self.detail.pack(side="left", fill="both", expand=True)
        detail_scroll.pack(side="right", fill="y")
        body.add(table, weight=4)
        body.add(details, weight=1)

        bottom = ttk.Frame(root, padding=(12, 6, 12, 10))
        bottom.pack(fill="x")
        self.counts_label = ttk.Label(bottom)
        self.counts_label.pack(side="left")
        self.notice = ttk.Label(bottom, foreground="#15803d")
        self.notice.pack(side="left", padx=(16, 0))
        self._text(ttk.Button(bottom, command=self.open_settings), "settings").pack(side="right")
        self._text(ttk.Button(bottom, command=self.open_folder), "open_folder").pack(side="right", padx=(0, 6))
        self._text(ttk.Button(bottom, command=self.export), "export").pack(side="right", padx=(0, 6))
        self._text(ttk.Button(bottom, command=self.clear), "clear").pack(side="right", padx=(0, 6))
        self.file_label = ttk.Label(bottom, style="Muted.TLabel")
        self.file_label.pack(side="right", padx=(0, 12))
        self.menu = tk.Menu(root, tearoff=False)

    # ---- server ------------------------------------------------------------------------------------------------

    def start(self) -> None:
        self._show_state("STARTING", force=True)
        self.root.update_idletasks()
        self.runner.start()
        self._tick()

    def _tick(self) -> None:
        try:
            entries = self.system.memory.drain()
            if entries:
                self._append(entries)
            state = self.runner.poll()
            self._show_state(state)
            if state == "RUNNING" and self.open_browser and not self._opened:
                self._opened = True
                self.browser(self.runner.url)
        finally:
            if self._after:  # keep a single polling loop even when called while one is pending
                self.root.after_cancel(self._after)
            self._after = self.root.after(POLL_MS, self._tick)

    def _show_state(self, state: str, force: bool = False) -> None:
        key = (state, self.runner.problem, self.lang)
        if key == self._shown_state and not force:
            return
        self._shown_state = key
        self.status.configure(text=self.t("state_" + state, self.runner.url.split("/?")[0]))
        self.dot.itemconfigure(self.dot_item, fill=STATE_COLORS.get(state, STATE_COLORS["STOPPED"]))
        problem = self.t("problem_" + self.runner.problem, self.runner.port) if state == "FAILED" and self.runner.problem else ""
        self.problem.configure(text=problem)
        if problem:
            self.problem.pack(fill="x", after=self.top)
        else:
            self.problem.pack_forget()
        active = state in ("STARTING", "RUNNING")
        self.toggle_button.configure(text=self.t("stop" if active else "start"), state="disabled" if state == "STOPPING" else "normal")
        self.open_button.configure(state="normal" if state == "RUNNING" else "disabled")
        self.restart_button.configure(state="normal" if state == "RUNNING" else "disabled")

    def open_app(self) -> None:
        self.browser(self.runner.url)

    def toggle(self) -> None:
        if self.runner.state in ("STARTING", "RUNNING"):
            self._show_state("STOPPING", force=True)
            self.root.update_idletasks()
            self.runner.stop()
        else:
            self._show_state("STARTING", force=True)
            self.root.update_idletasks()
            self.runner.start()
        self._show_state(self.runner.state, force=True)

    def restart(self) -> None:
        self._show_state("STOPPING", force=True)
        self.root.update_idletasks()
        self.runner.stop()
        self.runner.start()
        self._show_state(self.runner.state, force=True)

    def close(self) -> None:
        if self.runner.state in ("STARTING", "RUNNING") and not self.ask(self.t("confirm_close_title"), self.t("confirm_close"), parent=self.root):
            return
        size = f"{self.root.winfo_width()}x{self.root.winfo_height()}"
        if SIZE.match(size):
            self.settings = self.store.save(geometry=size)
        self.cancel_timers()
        self._show_state("STOPPING", force=True)
        self.root.update_idletasks()
        self.runner.stop()
        self.root.destroy()

    def cancel_timers(self) -> None:
        for name in ("_after", "_search_after", "_notice_after"):
            timer = getattr(self, name)
            if timer:
                try:
                    self.root.after_cancel(timer)
                except tk.TclError:
                    pass
                setattr(self, name, None)

    # ---- log view ----------------------------------------------------------------------------------------------

    def _levels(self) -> set[str]:
        return {level for level, var in self.level_vars.items() if var.get()}

    def _module_filter(self) -> str | None:
        value = self.module.get()
        return None if value in ("", self.t("all_modules")) else value

    def matches(self, entry: logs.Entry) -> bool:
        return entry.seq > self.hidden_before and logs.matches(entry, levels=self._levels(), errors_only=self.errors_only.get(),
                                                               module=self._module_filter(), text=self.search.get())

    def visible_entries(self) -> list[logs.Entry]:
        return [entry for entry in self.system.memory.entries() if self.matches(entry)]

    def _insert(self, entry: logs.Entry) -> None:
        iid = str(entry.seq)
        self.rows[iid] = entry
        self.tree.insert("", "end", iid=iid, values=(entry.stamp(False), entry.level, entry.module, entry.summary()), tags=(entry.level,))

    def _trim(self) -> None:
        children = self.tree.get_children()
        if len(children) > MAX_ROWS:
            extra = children[:len(children) - MAX_ROWS]
            self.tree.delete(*extra)
            for iid in extra:
                self.rows.pop(iid, None)

    def _append(self, entries: list[logs.Entry]) -> None:
        added, new_module = False, False
        for entry in entries:
            if entry.level in self.counts:
                self.counts[entry.level] += 1
            if entry.module not in self.modules:
                self.modules.add(entry.module)
                new_module = True
            if self.matches(entry):
                self._insert(entry)
                added = True
        self._trim()
        if new_module:
            self._update_modules()
        if added and self.autoscroll.get():
            children = self.tree.get_children()
            if children:
                self.tree.see(children[-1])
        self._update_counts()

    def refresh(self) -> None:
        self.tree.delete(*self.tree.get_children())
        self.rows.clear()
        for entry in self.visible_entries()[-MAX_ROWS:]:
            self._insert(entry)
        children = self.tree.get_children()
        if children and self.autoscroll.get():
            self.tree.see(children[-1])
        self._update_counts()
        self._show_detail()

    def _search_changed(self) -> None:
        if self._search_after:
            self.root.after_cancel(self._search_after)
        self._search_after = self.root.after(250, self.refresh)

    def _filters_changed(self) -> None:
        only = self.errors_only.get()
        for box in self.level_boxes.values():
            box.state(["disabled"] if only else ["!disabled"])
        self.settings = self.store.save(levels=[level for level in logs.LEVELS if self.level_vars[level].get()], errors_only=only)
        self.refresh()

    def _autoscroll_changed(self) -> None:
        self.settings = self.store.save(autoscroll=self.autoscroll.get())
        if self.autoscroll.get():
            children = self.tree.get_children()
            if children:
                self.tree.see(children[-1])

    def _update_modules(self) -> None:
        self.module_box.configure(values=[self.t("all_modules"), *sorted(self.modules)])
        if not self.module.get():
            self.module.set(self.t("all_modules"))
        for box in self.level_boxes.values():
            box.state(["disabled"] if self.errors_only.get() else ["!disabled"])

    def _update_counts(self) -> None:
        total = sum(1 for entry in self.system.memory.entries() if entry.seq > self.hidden_before)
        self.counts_label.configure(text=self.t("counts", len(self.tree.get_children()), total, self.counts["ERROR"], self.counts["WARNING"]))

    def _update_file_label(self) -> None:
        self.file_label.configure(text=self.t("log_file", self.system.file.path.name))

    def selected(self) -> list[logs.Entry]:
        return [self.rows[iid] for iid in self.tree.selection() if iid in self.rows]

    def _show_detail(self) -> None:
        chosen = self.selected()[:50]
        text = "\n".join(entry.line() for entry in chosen) if chosen else self.t("detail_hint")
        self.detail.configure(state="normal")
        self.detail.delete("1.0", "end")
        self.detail.insert("1.0", text)
        self.detail.configure(state="disabled")

    def copy_selection(self) -> str:
        text = "\n".join(entry.line() for entry in self.selected())
        if text:
            self.root.clipboard_clear()
            self.root.clipboard_append(text)
        return text

    def _context_menu(self, event) -> None:
        row = self.tree.identify_row(event.y)
        if row and row not in self.tree.selection():
            self.tree.selection_set(row)
        entry = self.rows.get(row)
        self.menu.delete(0, "end")
        self.menu.add_command(label=self.t("copy"), command=self.copy_selection)
        if entry:
            self.menu.add_command(label=self.t("only_module", entry.module), command=lambda: self.filter_module(entry.module))
        self.menu.add_command(label=self.t("all_modules_menu"), command=lambda: self.filter_module(None))
        self.menu.tk_popup(event.x_root, event.y_root)

    def filter_module(self, module: str | None) -> None:
        self.module.set(module or self.t("all_modules"))
        self.refresh()

    def clear(self) -> None:
        entries = self.system.memory.entries()
        self.hidden_before = entries[-1].seq if entries else self.hidden_before
        self.counts = {"ERROR": 0, "WARNING": 0}
        self.refresh()
        self.flash(self.t("cleared"))

    def flash(self, text: str) -> None:
        self.notice.configure(text=text)
        if self._notice_after:
            self.root.after_cancel(self._notice_after)
        self._notice_after = self.root.after(8000, lambda: self.notice.configure(text=""))

    # ---- export, folder and settings -------------------------------------------------------------------------

    def export(self) -> Path | None:
        home = Path.home()
        start = next((folder for folder in (home / "Downloads", home / "Desktop") if folder.is_dir()), home)
        chosen = self.save_dialog(parent=self.root, title=self.t("export_title"), initialdir=str(start), defaultextension=".zip",
                                  initialfile=datetime.now().strftime("studio-logs-%Y-%m-%d_%H-%M.zip"), filetypes=[("Zip", "*.zip")])
        if not chosen:
            return None
        target = Path(chosen)
        try:
            info = logs.system_info(self.system.folder.parent, self.build_id, {
                "launcher": {"state": self.runner.state, "problem": self.runner.problem, "port": self.runner.port},
                "log_settings": {key: self.settings[key] for key in ("file_mode", "keep_days", "file_debug")},
                "view_filter": {"levels": sorted(self._levels()), "errors_only": self.errors_only.get(), "module": self._module_filter(),
                                "search": self.search.get()}})
            logs.export(target, folder=self.system.folder, session=self.system.memory.entries(), view=self.visible_entries(), info=info)
        except Exception as exc:
            self.log.exception("Log export to %s failed", target)
            self.tell(self.t("export_title"), self.t("export_failed", exc), parent=self.root)
            return None
        self.log.info("Exported logs to %s", target)
        if self.ask(self.t("export_title"), self.t("export_done", target), parent=self.root):
            subprocess.Popen(f'explorer /select,"{target}"')
        return target

    def open_folder(self) -> None:
        try:
            self.folder_opener(str(self.system.folder))
        except OSError as exc:
            self.tell(self.t("open_folder"), self.t("open_failed", exc), parent=self.root)

    def apply_settings(self, mode: str, keep_days, file_debug: bool) -> tuple[bool, str]:
        """Checks and saves the log file settings; they take effect at once. Returns (saved, message)."""
        try:
            days = int(str(keep_days).strip())
        except ValueError:
            days = 0
        if not logs.KEEP_DAYS[0] <= days <= logs.KEEP_DAYS[1] or mode not in logs.FILE_MODES:
            return False, self.t("keep_days_invalid")
        self.settings = self.store.save(file_mode=mode, keep_days=days, file_debug=bool(file_debug))
        removed = self.system.apply(self.settings)
        self.log.info("Log settings: %s files, kept %d days, DEBUG in file %s", mode.lower(), days, "on" if file_debug else "off")
        self._update_file_label()
        return True, self.t("saved_pruned", len(removed)) if removed else self.t("saved")

    def open_settings(self) -> tk.Toplevel:
        dialog = tk.Toplevel(self.root)
        dialog.title(self.t("settings_title"))
        dialog.transient(self.root)
        dialog.resizable(False, False)
        mode = tk.StringVar(dialog, value=self.settings["file_mode"])
        keep = tk.StringVar(dialog, value=str(self.settings["keep_days"]))
        debug = tk.BooleanVar(dialog, value=self.settings["file_debug"])
        frame = ttk.Frame(dialog, padding=16)
        frame.pack(fill="both", expand=True)
        ttk.Label(frame, text=self.t("file_mode"), style="Status.TLabel").grid(row=0, column=0, columnspan=2, sticky="w")
        ttk.Radiobutton(frame, text=self.t("mode_DAY", datetime.now().strftime("studio-%Y-%m-%d.log")), value="DAY", variable=mode).grid(row=1, column=0, columnspan=2, sticky="w", pady=(6, 0))
        ttk.Radiobutton(frame, text=self.t("mode_SESSION", self.system.file.started.strftime("studio-%Y-%m-%d_%H-%M-%S.log")), value="SESSION",
                        variable=mode).grid(row=2, column=0, columnspan=2, sticky="w")
        ttk.Label(frame, text=self.t("keep_days")).grid(row=3, column=0, sticky="w", pady=(14, 0))
        ttk.Spinbox(frame, from_=logs.KEEP_DAYS[0], to=logs.KEEP_DAYS[1], textvariable=keep, width=6).grid(row=3, column=1, sticky="w", padx=(8, 0), pady=(14, 0))
        ttk.Checkbutton(frame, text=self.t("file_debug"), variable=debug).grid(row=4, column=0, columnspan=2, sticky="w", pady=(10, 0))
        ttk.Label(frame, text=self.t("folder", self.system.folder), style="Muted.TLabel").grid(row=5, column=0, columnspan=2, sticky="w", pady=(10, 0))
        error = ttk.Label(frame, style="Problem.TLabel")
        error.grid(row=6, column=0, columnspan=2, sticky="w", pady=(6, 0))
        buttons = ttk.Frame(frame)
        buttons.grid(row=7, column=0, columnspan=2, sticky="e", pady=(12, 0))

        def save() -> None:
            ok, message = self.apply_settings(mode.get(), keep.get(), debug.get())
            if not ok:
                error.configure(text=message)
                return
            dialog.destroy()
            self.flash(message)

        ttk.Button(buttons, text=self.t("save"), command=save).pack(side="right")
        ttk.Button(buttons, text=self.t("cancel"), command=dialog.destroy).pack(side="right", padx=(0, 6))
        dialog.bind("<Return>", lambda _event: save())
        dialog.bind("<Escape>", lambda _event: dialog.destroy())
        dialog.grab_set()
        self.settings_dialog = dialog
        return dialog
