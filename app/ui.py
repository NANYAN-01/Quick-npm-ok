# -*- coding: utf-8 -*-
"""界面层：主窗口与各个对话框（tkinter）。"""

from __future__ import annotations

import os
import queue
import re
import sys
import threading
import time
import tkinter as tk
from tkinter import filedialog, font as tkfont, messagebox, ttk

from . import (APP_NAME, APP_VERSION, GITHUB_URL, TAGLINE, TAGLINE_EN)
from . import core, deploy, updater
from .i18n import t as _t
from .tray import Tray

BG = "#f7f9fc"
CARD = "#ffffff"
ACCENT = "#1677ff"
ACCENT_DARK = "#0b5ed7"
TEXT = "#111827"
TEXT_MUTED = "#6b7280"
TEXT_FAINT = "#9ca3af"
BORDER = "#e5e7eb"
WARN_BG = "#fff7e6"
WARN_BORDER = "#ffe0a3"
OK_GREEN = "#16a34a"


def tr(key: str, **kwargs) -> str:
    return _t(key, core.language(), **kwargs)


def resource_path(name: str) -> str:
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    return os.path.join(base, name)


def normalize(url: str) -> str:
    return (url or "").strip().lower().rstrip("/")


def format_ago(stamp: float) -> str:
    if not stamp:
        return ""
    delta = max(0, int(time.time() - stamp))
    if delta < 60:
        return f"{delta}s"
    if delta < 3600:
        return f"{delta // 60}m"
    if delta < 86400:
        return f"{delta // 3600}h"
    return f"{delta // 86400}d"


# --------------------------------------------------------------------------- #
# 主窗口
# --------------------------------------------------------------------------- #

class App:
    def __init__(self, root: tk.Tk, scale: float | None = None):
        self.root = root
        self.q: queue.Queue = queue.Queue()
        self.ecosystem = core.ecosystem()
        self.sources = core.sources(self.ecosystem)
        self.data = [self._empty_data() for _ in self.sources]
        self.manager = core.managers(self.ecosystem)[0]
        self.current = ""
        self.current_index = None
        self.loading_registry = False
        self.fastest_index = None
        self.testing = False
        self.switching = False
        self.steps = 0
        self.total_steps = max(1, len(self.sources) * (core.RUNS + 2))
        self.started_at = 0.0
        self.last_run = 0.0
        self.tool_ver = ""
        self.cancel_event = threading.Event()
        self.dialogs = {}
        self.update_info = None
        self.tray = None
        self._closing = False
        self.scale = max(1.0, min(2.5, float(scale))) if scale else self._dpi_scale()
        self._apply_tk_scaling()

        self._setup_style()
        self._build_ui()
        self._refresh_manager_buttons()
        self._refresh_current()
        self._load_tool_version()
        self._load_cache()
        self._start_tray()

        self.root.protocol("WM_DELETE_WINDOW", self.on_close)
        self.root.bind("<F5>", lambda _e: self.start_test())
        self.root.after(80, self._poll)
        self.root.after(400, lambda: self._check_updates(manual=False))

        if not self.tool_ready():
            self.status.configure(text=tr("main.npm_missing" if self.ecosystem == "npm"
                                          else "main.pip_missing"))

    @staticmethod
    def _empty_data():
        return {"ok": False, "lat": None, "speed": None, "version": None,
                "error": "", "score": 0.0, "rank": None}

    # ---------------- 样式 / 布局 ---------------- #

    def _setup_style(self):
        style = ttk.Style(self.root)
        for theme in ("vista", "winnative", "clam"):
            if theme in style.theme_names():
                style.theme_use(theme)
                break

        family = "Microsoft YaHei UI"
        available = set(tkfont.families(self.root))
        if family not in available:
            family = "Microsoft YaHei" if "Microsoft YaHei" in available else "Segoe UI"
        self.font_family = family

        tkfont.nametofont("TkDefaultFont").configure(family=family, size=10)
        tkfont.nametofont("TkTextFont").configure(family=family, size=10)
        tkfont.nametofont("TkHeadingFont").configure(family=family, size=10, weight="bold")

        style.configure(".", background=BG)
        style.configure("TFrame", background=BG)
        style.configure("Card.TFrame", background=CARD)
        style.configure("TLabel", background=BG)
        style.configure("Muted.TLabel", foreground=TEXT_MUTED, background=BG)
        style.configure("Faint.TLabel", foreground=TEXT_FAINT, background=BG)
        style.configure("Card.TLabel", background=CARD)
        style.configure("CardMuted.TLabel", foreground=TEXT_MUTED, background=CARD)
        style.configure("CardFaint.TLabel", foreground=TEXT_FAINT, background=CARD)
        style.configure("Current.TLabel", foreground=ACCENT_DARK, font=(family, 10, "bold"))
        style.configure("Title.TLabel", font=(family, 13, "bold"))
        style.configure("TButton", padding=(self.px(10), self.px(4)))
        style.configure("Treeview", font=(family, 10), rowheight=self.px(30),
                        background=CARD, fieldbackground=CARD, borderwidth=0)
        style.configure("Treeview.Heading", font=(family, 9, "bold"),
                        padding=(0, self.px(6)))
        style.map("Treeview", background=[("selected", "#dbeafe")],
                  foreground=[("selected", "#111827")])
        style.configure("TCheckbutton", background=BG)
        style.configure("TRadiobutton", background=BG)

    def _build_ui(self):
        self.root.title(f"{tr('main.title')} v{APP_VERSION}")
        self.root.configure(bg=BG)
        # minsize 不能超过可用工作区，否则高缩放 + 小屏时窗口会被顶出屏幕
        left, top, right, bottom = self.work_area()
        self.root.minsize(
            min(self.px(900), max(self.px(480), right - left - self.px(24))),
            min(self.px(560), max(self.px(360), bottom - top - self.px(48))),
        )
        self.root.columnconfigure(0, weight=1)
        self.root.rowconfigure(4, weight=1)

        # --- 顶部：生态 + 包管理器 + 版本 + 操作 ---
        top = ttk.Frame(self.root, padding=(18, 12, 18, 2))
        top.grid(row=0, column=0, sticky="ew")
        top.columnconfigure(3, weight=1)

        self.eco_buttons = {}
        eco_box = ttk.Frame(top)
        eco_box.grid(row=0, column=0, sticky="w")
        for position, name in enumerate(core.ECOSYSTEMS):
            button = tk.Button(
                eco_box, text=tr(f"main.eco_{name}"),
                command=lambda n=name: self.select_ecosystem(n),
                relief="flat", bd=0, padx=12, pady=4, cursor="hand2",
                font=(self.font_family, 9, "bold"),
                disabledforeground="#a3aab8",
            )
            button.pack(side="left", padx=(0 if position == 0 else 4, 0))
            self.eco_buttons[name] = button

        ttk.Separator(top, orient="vertical").grid(row=0, column=1, sticky="ns", padx=(12, 0))
        ttk.Label(top, text=tr("main.manager"), style="Muted.TLabel").grid(
            row=0, column=2, sticky="w", padx=(12, 0))

        self.manager_segment = ttk.Frame(top)
        self.manager_segment.grid(row=0, column=3, sticky="w", padx=(10, 0))
        self._build_manager_buttons()

        right = ttk.Frame(top)
        right.grid(row=0, column=4, sticky="e")
        self.version_label = ttk.Label(right, text="…", style="Muted.TLabel")
        self.version_label.pack(side="left")
        self.btn_upgrade = ttk.Button(right, text=tr("main.upgrade"), command=self.open_upgrade)
        self.btn_upgrade.pack(side="left", padx=(10, 0))
        self.btn_env = ttk.Button(right, text=tr("main.env"), command=self.open_env)
        self.btn_env.pack(side="left", padx=(6, 0))
        self.btn_more = ttk.Menubutton(right, text=tr("main.more"))
        self.btn_more.pack(side="left", padx=(6, 0))
        self._build_more_menu()
        self._sync_upgrade_button()

        # --- 缺 npm 提示条 ---
        self.banner = tk.Frame(self.root, bg=WARN_BG, highlightbackground=WARN_BORDER,
                               highlightthickness=1)
        self.banner.grid(row=1, column=0, sticky="ew", padx=18, pady=(8, 0))
        self.banner.columnconfigure(1, weight=1)
        tk.Label(self.banner, text="!", bg=WARN_BG, fg="#b45309",
                 font=(self.font_family, 11, "bold")).grid(row=0, column=0, padx=(12, 6), pady=8)
        inner = tk.Frame(self.banner, bg=WARN_BG)
        inner.grid(row=0, column=1, sticky="w")
        self.banner_title = tk.Label(inner, text="", bg=WARN_BG, fg="#92400e",
                                     font=(self.font_family, 10, "bold"))
        self.banner_title.pack(anchor="w")
        self.banner_body = tk.Label(inner, text="", bg=WARN_BG, fg="#92400e",
                                    font=(self.font_family, 9))
        self.banner_body.pack(anchor="w")
        self.banner_action = tk.Button(self.banner, text="", command=self._banner_action,
                                       bg="#f59e0b", fg="white", activebackground="#d97706",
                                       activeforeground="white", relief="flat", bd=0,
                                       padx=12, pady=4, cursor="hand2",
                                       font=(self.font_family, 9, "bold"))
        self.banner_action.grid(row=0, column=2, padx=12)
        self._sync_banner_text()
        self.banner.grid_remove()

        # --- 当前源 + 范围 ---
        line = ttk.Frame(self.root, padding=(18, 8, 18, 4))
        line.grid(row=2, column=0, sticky="ew")
        line.columnconfigure(1, weight=1)
        ttk.Label(line, text=tr("main.current"), style="Muted.TLabel").grid(row=0, column=0, sticky="w")
        self.current_label = ttk.Label(line, text=tr("common.loading"), style="Current.TLabel")
        self.current_label.grid(row=0, column=1, sticky="w", padx=(10, 0))

        scope_box = ttk.Frame(line)
        scope_box.grid(row=0, column=2, sticky="e")
        ttk.Label(scope_box, text=tr("main.scope"), style="Muted.TLabel").pack(side="left")
        self.scope_var = tk.StringVar()
        self.scope_combo = ttk.Combobox(scope_box, textvariable=self.scope_var, width=8,
                                        state="readonly", values=[tr("main.scope_user"),
                                                                  tr("main.scope_project")])
        self.scope_combo.pack(side="left", padx=(6, 0))
        self.scope_combo.bind("<<ComboboxSelected>>", self._on_scope_change)
        self._sync_scope_combo()

        # --- 标题 ---
        head = ttk.Frame(self.root, padding=(18, 4, 18, 6))
        head.grid(row=3, column=0, sticky="ew")
        head.columnconfigure(1, weight=1)
        ttk.Label(head, text=tr("main.heading"), style="Title.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Label(
            head,
            text=tr("main.legend", lat=int(core.LATENCY_WEIGHT * 100),
                    spd=int(core.SPEED_WEIGHT * 100), runs=core.RUNS, pkg=self.probe_label()),
            style="Muted.TLabel",
        ).grid(row=0, column=1, sticky="w", padx=(12, 0), pady=(4, 0))

        # --- 表格 ---
        table = ttk.Frame(self.root, padding=(18, 0, 18, 0))
        table.grid(row=4, column=0, sticky="nsew")
        table.columnconfigure(0, weight=1)
        table.rowconfigure(0, weight=1)

        columns = ("rank", "name", "url", "latency", "speed", "sync", "status")
        self.tree = ttk.Treeview(table, columns=columns, show="headings", selectmode="browse")
        headings = {
            "rank": tr("col.rank"), "name": tr("col.name"), "url": tr("col.url"),
            "latency": tr("col.latency"), "speed": tr("col.speed"),
            "sync": tr("col.sync"), "status": tr("col.status"),
        }
        for key in columns:
            self.tree.heading(key, text=headings[key], anchor="center")
        self.tree.column("rank", width=self.px(54), minwidth=self.px(48), anchor="center", stretch=False)
        self.tree.column("name", width=self.px(124), minwidth=self.px(100), anchor="w", stretch=False)
        self.tree.column("url", width=self.px(320), minwidth=self.px(180), anchor="w")
        self.tree.column("latency", width=self.px(128), minwidth=self.px(110), anchor="center", stretch=False)
        self.tree.column("speed", width=self.px(96), minwidth=self.px(86), anchor="center", stretch=False)
        self.tree.column("sync", width=self.px(88), minwidth=self.px(80), anchor="center", stretch=False)
        self.tree.column("status", width=self.px(98), minwidth=self.px(90), anchor="center", stretch=False)

        self.tree.tag_configure("current", background="#eaf3ff")
        self.tree.tag_configure("best", background="#f0fbf3")
        self.tree.grid(row=0, column=0, sticky="nsew")

        scroll = ttk.Scrollbar(table, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=scroll.set)
        scroll.grid(row=0, column=1, sticky="ns")

        self.tree.bind("<<TreeviewSelect>>", self._on_select)
        self.tree.bind("<Double-1>", self._on_double_click)

        # --- 底部操作 ---
        footer = ttk.Frame(self.root, padding=(18, 10, 18, 2))
        footer.grid(row=5, column=0, sticky="ew")
        footer.columnconfigure(0, weight=1)

        buttons = ttk.Frame(footer)
        buttons.grid(row=0, column=0, sticky="w")

        self.btn_test = tk.Button(
            buttons, text=tr("main.start"), command=self.start_test,
            bg=ACCENT, fg="white", activebackground=ACCENT_DARK, activeforeground="white",
            relief="flat", bd=0, padx=20, pady=7, cursor="hand2",
            font=(self.font_family, 10, "bold"), disabledforeground="#dbeafe",
        )
        self.btn_test.pack(side="left")
        self.btn_test.bind("<Enter>", lambda e: self.btn_test.config(bg=ACCENT_DARK))
        self.btn_test.bind("<Leave>", lambda e: self.btn_test.config(bg=ACCENT))

        self.btn_fastest = ttk.Button(buttons, text=tr("main.fastest"), command=self.use_fastest)
        self.btn_fastest.pack(side="left", padx=(10, 0))
        self.btn_fastest.state(["disabled"])

        self.btn_switch = ttk.Button(buttons, text=tr("main.switch"), command=self.switch_selected)
        self.btn_switch.pack(side="left", padx=(10, 0))
        self.btn_switch.state(["disabled"])

        self.btn_sources = ttk.Button(buttons, text=tr("main.sources"), command=self.open_sources)
        self.btn_sources.pack(side="left", padx=(10, 0))

        self.progress = tk.Canvas(footer, width=self.px(180), height=self.px(8), bg=BG,
                                  highlightthickness=0)
        bar_pad, bar_width = self.px(4), self.px(180)
        self._bar_pad = bar_pad
        self._bar_span = bar_width - 2 * bar_pad
        line_width = max(2, self.px(5))
        self.progress.create_line(bar_pad, bar_pad, bar_width - bar_pad, bar_pad,
                                  width=line_width, capstyle="round", fill="#e2e8f0")
        self.progress_fill = self.progress.create_line(bar_pad, bar_pad, bar_pad, bar_pad,
                                                       width=line_width, capstyle="round", fill=ACCENT)
        self.progress.grid(row=0, column=1, sticky="e", padx=(12, 0))
        self.progress.grid_remove()

        # --- 状态 ---
        self.status = ttk.Label(footer, text=tr("main.select_hint"), style="Muted.TLabel")
        self.status.grid(row=1, column=0, columnspan=2, sticky="w", pady=(8, 0))

        # --- 底部链接 ---
        links = tk.Frame(self.root, bg=BG)
        links.grid(row=6, column=0, pady=(2, 10))
        self._link(links, tr("main.about"), self.open_about).pack(side="left")
        tk.Label(links, text="·", bg=BG, fg=TEXT_FAINT, font=(self.font_family, 9)).pack(side="left", padx=6)
        self._link(links, "GitHub", self.open_github).pack(side="left")
        tk.Label(links, text="·", bg=BG, fg=TEXT_FAINT, font=(self.font_family, 9)).pack(side="left", padx=6)
        tk.Label(links, text=f"v{APP_VERSION}", bg=BG, fg=TEXT_FAINT,
                 font=(self.font_family, 9)).pack(side="left")
        self.update_link = self._link(links, "")

        self._rebuild_rows()
        self._center_window(980, 640)

    def _link(self, parent, text, command=None):
        label = tk.Label(parent, text=text, bg=BG, fg=TEXT_FAINT, cursor="hand2",
                         font=(self.font_family, 9))
        if command is not None:
            label.bind("<Button-1>", lambda _e: command())
            label.bind("<Enter>", lambda _e: label.config(fg=ACCENT))
            label.bind("<Leave>", lambda _e: label.config(fg=TEXT_FAINT))
        return label

    def _build_more_menu(self):
        menu = tk.Menu(self.root, tearoff=0, font=(self.font_family, 10))
        menu.add_command(label=tr("scope.title"), command=self.open_scopes)
        menu.add_command(label=tr("project.title"), command=self.open_project)
        menu.add_separator()
        menu.add_command(label=tr("settings.export"), command=self.export_csv)
        menu.add_command(label=tr("settings.history"), command=self.open_history)
        menu.add_separator()
        menu.add_command(label=tr("menu.check_update"), command=lambda: self._check_updates(True))
        menu.add_command(label=tr("settings.title"), command=self.open_settings)
        menu.add_command(label=tr("main.about"), command=self.open_about)
        menu.add_separator()
        menu.add_command(label=tr("tray.quit"), command=self.quit_app)
        self.btn_more["menu"] = menu

    def _dpi_scale(self) -> float:
        """当前显示器的缩放倍数（96 DPI = 1.0）。

        Tk 的 `winfo fpixels 1i` 在 Windows 上恒为 96（它问的是系统 DPI，
        不是显示器 DPI），所以真正的高分屏缩放下这里会一直返回 1.0。
        需要直接问 Windows 要窗口所在显示器的 DPI。
        """
        dpi = 0
        if os.name == "nt":
            try:
                import ctypes

                get_dpi = ctypes.windll.user32.GetDpiForWindow
                get_dpi.argtypes = [ctypes.c_void_p]
                get_dpi.restype = ctypes.c_uint
                dpi = int(get_dpi(ctypes.c_void_p(self.root.winfo_id())))
            except Exception:  # noqa: BLE001
                dpi = 0
        if dpi <= 0:
            try:
                dpi = int(round(self.root.winfo_fpixels("1i")))
            except Exception:  # noqa: BLE001
                dpi = 96
        return max(1.0, min(2.5, dpi / 96.0))

    def _apply_tk_scaling(self) -> None:
        """把 Tk 的“每磅多少像素”对齐到实际 DPI，字体才会跟着一起放大。"""
        try:
            self.root.tk.call("tk", "scaling", 96.0 * self.scale / 72.0)
        except Exception:  # noqa: BLE001
            pass

    def px(self, value: float) -> int:
        """把按 96 DPI 设计的像素值换算成当前缩放下的像素。"""
        return max(1, int(round(value * self.scale)))

    def work_area(self) -> tuple:
        """主窗口所在显示器的可用区域（去掉任务栏）。拿不到就退回整屏。"""
        if os.name == "nt":
            try:
                import ctypes
                from ctypes import wintypes

                class MONITORINFO(ctypes.Structure):
                    _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", wintypes.RECT),
                                ("rcWork", wintypes.RECT), ("dwFlags", wintypes.DWORD)]

                user32 = ctypes.windll.user32
                hwnd = wintypes.HWND(self.root.winfo_id())
                monitor = user32.MonitorFromWindow(hwnd, 2)  # MONITOR_DEFAULTTONEAREST
                info = MONITORINFO()
                info.cbSize = ctypes.sizeof(MONITORINFO)
                if monitor and user32.GetMonitorInfoW(monitor, ctypes.byref(info)):
                    rect = info.rcWork
                    return rect.left, rect.top, rect.right, rect.bottom
            except Exception:  # noqa: BLE001
                pass
        return 0, 0, self.root.winfo_screenwidth(), self.root.winfo_screenheight()

    def place_dialog(self, dialog, min_width: int = 0, min_height: int = 0):
        """按内容决定尺寸，再交给 Tk 自带的 tk::PlaceWindow 居中。

        弹窗尺寸只要等于 `winfo_reqwidth/reqheight`，`tk::PlaceWindow` 就能算准中心，
        所以这里不手动算 x/y；只有内容比可用区域还大时才缩到可用区域（允许拉伸），
        最后再兜底把窗口挪回工作区内，避免压到任务栏或被屏幕边缘裁掉。
        """
        dialog.update_idletasks()
        width = max(dialog.winfo_reqwidth(), self.px(min_width))
        height = max(dialog.winfo_reqheight(), self.px(min_height))

        left, top, right, bottom = self.work_area()
        avail_w = max(self.px(320), right - left - self.px(24))
        avail_h = max(self.px(240), bottom - top - self.px(48))
        if width > avail_w or height > avail_h:
            width, height = min(width, avail_w), min(height, avail_h)
            dialog.resizable(True, True)

        if width != dialog.winfo_reqwidth() or height != dialog.winfo_reqheight():
            dialog.geometry(f"{width}x{height}")
            dialog.update_idletasks()

        try:
            dialog.tk.call("tk::PlaceWindow", dialog._w, "widget", self.root._w)
        except tk.TclError:
            dialog.geometry(f"+{self.px(40)}+{self.px(40)}")

        self._clamp_to_work_area(dialog, left, top, right, bottom)

    def _clamp_to_work_area(self, dialog, left: int, top: int, right: int, bottom: int):
        """把窗口矩形挪进工作区；只挪刚好超出的那段，不影响正常居中。"""
        dialog.update_idletasks()
        x = dialog.winfo_rootx()
        y = dialog.winfo_rooty()
        width = dialog.winfo_width()
        height = dialog.winfo_height()

        dx = 0
        dy = 0
        if x < left:
            dx = left - x
        elif x + width > right:
            dx = right - (x + width)
        if y < top:
            dy = top - y
        elif y + height > bottom:
            dy = bottom - (y + height)
        if not dx and not dy:
            return

        match = re.match(r"(\d+)x(\d+)([+-]\d+)([+-]\d+)", dialog.wm_geometry())
        if not match:
            return
        frame_x, frame_y = int(match.group(3)), int(match.group(4))
        dialog.geometry(f"+{frame_x + dx}+{frame_y + dy}")

    def _center_window(self, width: int, height: int):
        self.root.update_idletasks()
        left, top, right, bottom = self.work_area()
        width = min(self.px(width), max(self.px(480), right - left - self.px(24)))
        height = min(self.px(height), max(self.px(320), bottom - top - self.px(48)))
        x = left + max(0, (right - left - width) // 2)
        y = top + max(0, (bottom - top - height) // 3)
        self.root.geometry(f"{width}x{height}+{x}+{y}")

    # ---------------- 托盘 ---------------- #

    def _start_tray(self):
        if not core.get("tray"):
            return
        icon = resource_path("app.ico")
        if not os.path.exists(icon):
            return
        try:
            tray = Tray(icon, f"{tr('main.title')} v{APP_VERSION}")
            if tray.start():
                self.tray = tray
        except Exception:  # noqa: BLE001
            self.tray = None

    def _popup_tray_menu(self, x: int, y: int):
        """托盘右键菜单用 Tk 自己画，系统菜单在部分机器上会渲染成空白框。"""
        menu = getattr(self, "_tray_menu", None)
        if menu is None:
            menu = tk.Menu(
                self.root, tearoff=0, font=(self.font_family, 10),
                bg=CARD, fg=TEXT, bd=0, relief="solid",
                activebackground=ACCENT, activeforeground="#ffffff",
            )
            menu.add_command(label=tr("tray.show"), command=self.show_window)
            menu.add_command(label=tr("tray.benchmark"), command=self._tray_benchmark)
            menu.add_separator()
            menu.add_command(label=tr("tray.quit"), command=self.quit_app)
            self._tray_menu = menu
        try:
            menu.tk_popup(x, y)
        finally:
            menu.grab_release()

    def _tray_benchmark(self):
        self.show_window()
        self.start_test()

    def _drain_tray(self):
        if not self.tray:
            return
        for action in self.tray.pop_events():
            if isinstance(action, tuple) and action[0] == "menu":
                self._popup_tray_menu(action[1], action[2])
            elif action == "show":
                self.show_window()
            elif action == "benchmark":
                self._tray_benchmark()
            elif action == "quit":
                self.quit_app()

    def show_window(self):
        self.root.deiconify()
        self.root.lift()
        self.root.focus_force()

    def on_close(self):
        if self.tray is not None and not self._closing:
            self.root.withdraw()
            self.status.configure(text=tr("tray.hint"))
            return
        self._closing = True
        if self.tray:
            self.tray.stop()
        self.root.destroy()

    def quit_app(self):
        self._closing = True
        if self.tray:
            self.tray.stop()
        self.root.destroy()

    # ---------------- 包管理器 / 当前源 ---------------- #

    def _busy(self):
        return self.testing or self.switching

    def probe_label(self) -> str:
        return "pip wheel" if self.ecosystem == "pip" else core.SPEED_PKG

    def tool_name(self) -> str:
        return "pip" if self.ecosystem == "pip" else "npm"

    def tool_ready(self) -> bool:
        if self.ecosystem == "pip":
            return core.tool_exists("pip") or core.tool_exists("pip3")
        return core.tool_exists("npm")

    def _build_manager_buttons(self):
        for child in self.manager_segment.winfo_children():
            child.destroy()
        self.manager_buttons = {}
        for position, name in enumerate(core.managers(self.ecosystem)):
            button = tk.Button(
                self.manager_segment, text=name, command=lambda n=name: self.select_manager(n),
                relief="flat", bd=0, padx=14, pady=4, cursor="hand2",
                font=(self.font_family, 9, "bold"),
                disabledforeground="#a3aab8",
            )
            button.pack(side="left", padx=(0 if position == 0 else 5, 0))
            self.manager_buttons[name] = button

    def _refresh_manager_buttons(self):
        busy = self._busy()
        for name, button in self.eco_buttons.items():
            active = name == self.ecosystem
            button.config(
                state="disabled" if busy else "normal",
                bg="#9ec5ff" if busy and active else (ACCENT if active else "#eaeef4"),
                fg="#f5f9ff" if busy and active else ("white" if active else "#374151"),
                activebackground=ACCENT_DARK if active else "#dfe6ef",
                activeforeground="white" if active else "#111827",
                cursor="arrow" if busy else "hand2",
            )
        for name, button in self.manager_buttons.items():
            installed = core.tool_exists(name) or (name == "pip" and core.tool_exists("pip3"))
            if not installed:
                button.config(state="disabled", bg="#f1f3f5", fg="#a3aab8", cursor="arrow")
            elif name == self.manager:
                button.config(
                    state="disabled" if busy else "normal",
                    bg="#9ec5ff" if busy else ACCENT,
                    fg="#f5f9ff" if busy else "white",
                    activebackground=ACCENT_DARK, activeforeground="white",
                    cursor="hand2",
                )
            else:
                button.config(
                    state="disabled" if busy else "normal",
                    bg="#eaeef4", fg="#8b93a3" if busy else "#374151",
                    activebackground="#dfe6ef", activeforeground="#111827",
                    cursor="hand2",
                )

    def _sync_banner_text(self):
        if self.ecosystem == "pip":
            title, body, action = ("main.banner_pip_title", "main.banner_pip_body",
                                   "main.banner_pip_action")
        else:
            title, body, action = "main.banner_title", "main.banner_body", "main.banner_action"
        self.banner_title.configure(text=tr(title))
        self.banner_body.configure(text=tr(body))
        self.banner_action.configure(text=tr(action))

    def _banner_action(self):
        self.open_env("python" if self.ecosystem == "pip" else "node")

    def _sync_upgrade_button(self):
        self.btn_upgrade.configure(
            text=tr("main.upgrade_pip") if self.ecosystem == "pip" else tr("main.upgrade"))
        if not self.btn_upgrade.winfo_manager():
            self.btn_upgrade.pack(side="left", padx=(10, 0), before=self.btn_env)

    def select_ecosystem(self, name: str):
        if self._busy() or name == self.ecosystem or name not in core.ECOSYSTEMS:
            return
        core.set_ecosystem(name)
        self.ecosystem = name
        self.sources = core.sources(name)
        self.manager = core.managers(name)[0]
        self.data = [self._empty_data() for _ in self.sources]
        self.current = ""
        self.current_index = None
        self.fastest_index = None
        self.last_run = 0.0
        self.tool_ver = ""
        self.btn_fastest.config(text=tr("main.fastest"))
        self.total_steps = max(1, len(self.sources) * (core.RUNS + 2))
        self._build_manager_buttons()
        self._refresh_manager_buttons()
        self._sync_scope_combo()
        self._sync_banner_text()
        self._sync_upgrade_button()
        self._rebuild_rows()
        self._refresh_current()
        self._load_tool_version()
        self._load_cache()
        self.status.configure(text=tr("main.ecosystem_switched", eco=tr(f"main.eco_{name}")))

    def select_manager(self, name: str):
        if self._busy() or name == self.manager:
            return
        if not core.tool_exists(name):
            return
        self.manager = name
        self._refresh_manager_buttons()
        self.current = ""
        self.current_index = None
        self.loading_registry = True
        self.current_label.configure(text=tr("common.loading"))
        self._clear_current_tags()
        threading.Thread(target=self._read_registry_worker,
                         args=(name, self.ecosystem), daemon=True).start()

    def _read_registry_worker(self, manager: str, eco: str):
        value = core.get_registry(manager, core.scope(eco))
        self.q.put(("registry", eco, manager, value))

    def _load_tool_version(self):
        eco, tool = self.ecosystem, self.tool_name()

        def worker():
            self.q.put(("tool_version", eco, core.tool_version(tool)))

        threading.Thread(target=worker, daemon=True).start()

    def _refresh_current(self):
        if not self.current:
            self.loading_registry = True
            self.current_label.configure(text=tr("common.loading"))
            threading.Thread(target=self._read_registry_worker,
                             args=(self.manager, self.ecosystem), daemon=True).start()
        self._apply_current()

    def _apply_current(self):
        target = normalize(self.current)
        self.current_index = None
        for index, source in enumerate(self.sources):
            if normalize(source["url"]) == target:
                self.current_index = index
                break

        if self.current_index is not None:
            name = self.sources[self.current_index]["name"]
            self.current_label.configure(text=f"{name}    {self.current}")
        elif self.current:
            self.current_label.configure(text=f"{self.current}")
        elif self.loading_registry:
            self.current_label.configure(text=tr("common.loading"))
        else:
            self.current_label.configure(text=tr("main.no_registry"))

        self._clear_current_tags()
        if self.current_index is not None and self.tree.exists(str(self.current_index)):
            self.tree.item(str(self.current_index), tags=self._tags_for(self.current_index))
        self._update_actions()

    def _clear_current_tags(self):
        for index in range(len(self.sources)):
            if self.tree.exists(str(index)):
                self.tree.item(str(index), tags=self._tags_for(index))

    def _tags_for(self, index: int):
        tags = []
        if self.data[index].get("rank") == 1:
            tags.append("best")
        if index == self.current_index:
            tags.append("current")
        return tuple(tags)

    def _sync_scope_combo(self):
        if self.ecosystem == "pip":
            self._scope_labels = [tr("main.scope_user"), tr("main.scope_venv")]
        else:
            self._scope_labels = [tr("main.scope_user"), tr("main.scope_project")]
        self.scope_combo.configure(values=self._scope_labels)
        scope = core.scope(self.ecosystem)
        use_project = scope.get("mode") == "project" and scope.get("dir")
        self.scope_var.set(self._scope_labels[1] if use_project else self._scope_labels[0])

    def _on_scope_change(self, _event=None):
        labels = getattr(self, "_scope_labels", [])
        want_project = len(labels) > 1 and self.scope_var.get() == labels[1]
        value = {"mode": "user", "dir": ""}
        if want_project:
            pip_mode = self.ecosystem == "pip"
            title = tr("main.scope_venv_pick") if pip_mode else tr("project.pick")
            directory = filedialog.askdirectory(title=title)
            if not directory:
                self._sync_scope_combo()
                return
            if pip_mode and not core.looks_like_venv(directory):
                if not messagebox.askyesno(tr("main.scope_not_venv_title"),
                                           tr("main.scope_not_venv_body"),
                                           parent=self.root):
                    self._sync_scope_combo()
                    return
            value = {"mode": "project", "dir": directory}
        core.set_scope(value, self.ecosystem)
        self.current = ""
        self._refresh_current()
        self.status.configure(text=value.get("dir") or tr("main.scope_user"))

    # ---------------- 表格 ---------------- #

    def _rebuild_rows(self):
        for item in self.tree.get_children():
            self.tree.delete(item)
        self.data = [self._empty_data() for _ in self.sources]
        for index, source in enumerate(self.sources):
            self.tree.insert(
                "", "end", iid=str(index),
                values=(tr("cell.dash"), source["name"], source["url"],
                        tr("cell.dash"), tr("cell.dash"), "", ""),
                tags=self._tags_for(index),
            )
        self._update_actions()

    def _render_row(self, index: int):
        item = str(index)
        if not self.tree.exists(item):
            return
        data = self.data[index]
        source = self.sources[index]

        latency = (f'{data["lat"]["min"]:.0f} / {data["lat"]["median"]:.0f}'
                   if data["lat"] else tr("cell.dash"))
        speed = f'{data["speed"]:.1f}' if data["speed"] is not None else tr("cell.dash")

        sync = ""
        if data.get("version"):
            newest = core.newest_version([row.get("version") for row in self.data])
            if newest and core.parse_version(data["version"]) < core.parse_version(newest):
                sync = f'⚠ {data["version"]}'
            else:
                sync = f'✓ {data["version"]}'

        if data["ok"]:
            status = f'⚠ {data["error"]}' if data["error"] else "✓"
        elif data["error"]:
            status = f'✗ {data["error"]}'
        else:
            status = ""

        self.tree.item(
            item,
            values=(core.rank_text(data.get("rank")), source["name"], source["url"],
                    latency, speed, sync, status),
            tags=self._tags_for(index),
        )

    def _render_all(self):
        for index in range(len(self.sources)):
            self._render_row(index)

    # ---------------- 操作状态 ---------------- #

    def _selected_index(self):
        selection = self.tree.selection()
        if not selection:
            return None
        try:
            return int(selection[0])
        except ValueError:
            return None

    def _update_actions(self):
        idle = not self._busy()
        self.btn_switch.state(["!disabled"] if idle and self.tree.selection() else ["disabled"])
        self.btn_fastest.state(
            ["!disabled"] if idle and self.fastest_index is not None else ["disabled"]
        )
        self.btn_sources.state(["!disabled"] if idle else ["disabled"])
        self.btn_upgrade.state(
            ["!disabled"] if idle and self.tool_ready() else ["disabled"]
        )
        self.scope_combo.state(["!disabled", "readonly"] if idle else ["disabled"])

    def _set_busy(self, busy: bool, switching: bool = False):
        self.testing = busy and not switching
        self.switching = busy and switching
        self.btn_test.config(state="disabled" if busy else "normal")
        self._refresh_manager_buttons()
        self._update_actions()

    def _on_select(self, _event=None):
        self._update_actions()
        index = self._selected_index()
        if index is None or self.testing:
            return
        data = self.data[index]
        source = self.sources[index]
        if data.get("rank"):
            text = tr("main.row_info_rank", name=source["name"], url=source["url"],
                      rank=data["rank"], score=int(data["score"] * 100))
        else:
            text = tr("main.row_info", name=source["name"], url=source["url"])
        self.status.configure(text=text)

    def _on_double_click(self, event):
        row = self.tree.identify_row(event.y)
        if not row or self._busy():
            return
        self.tree.selection_set(row)
        self.switch_selected()

    # ---------------- 测速 ---------------- #

    def start_test(self):
        if self._busy():
            return
        if not self.sources:
            return

        self.data = [self._empty_data() for _ in self.sources]
        self.fastest_index = None
        self.btn_fastest.config(text=tr("main.fastest"))
        self._render_all()
        self.tree.selection_remove(self.tree.selection())
        self.steps = 0
        self.total_steps = max(1, len(self.sources) * (core.RUNS + 2))
        self._progress_set(0)
        self.progress.grid()
        self.status.configure(text=tr("main.testing"))
        self.started_at = time.time()
        self.cancel_event = threading.Event()
        self._set_busy(True)
        threading.Thread(target=self._bench_worker, daemon=True).start()

    def _bench_worker(self):
        try:
            proxy = core.detect_proxy()
            results = core.run_ecosystem_benchmark(
                self.ecosystem, self.sources, proxy=proxy,
                emit=lambda *args: self.q.put(args),
                cancel=self.cancel_event,
            )
            self.q.put(("done", results))
        except Exception as exc:  # noqa: BLE001
            self.q.put(("error", str(exc)))

    def _poll(self):
        try:
            while True:
                message = self.q.get_nowait()
                kind, *rest = message

                if kind == "latency":
                    index, stats, _error = rest
                    self.data[index]["lat"] = stats
                    self.data[index]["ok"] = stats is not None
                    self._render_row(index)
                elif kind == "speed":
                    index, speed, _error = rest
                    self.data[index]["speed"] = speed
                    self._render_row(index)
                elif kind == "fresh":
                    index, version, _error = rest
                    self.data[index]["version"] = version
                    self._render_row(index)
                elif kind == "step":
                    self.steps += 1
                    self._progress_set(self.steps / max(1, self.total_steps))
                    self.status.configure(text=tr("main.testing_n", done=min(self.steps, self.total_steps),
                                                  total=self.total_steps))
                elif kind == "done":
                    self._on_done(rest[0])
                elif kind == "error":
                    self._set_busy(False)
                    self.progress.grid_remove()
                    self.status.configure(text=tr("main.failed"))
                    messagebox.showerror(APP_NAME, f"{tr('main.failed_msg')}\n{rest[0]}")
                elif kind == "registry":
                    eco, manager, value = rest
                    if eco == self.ecosystem and manager == self.manager:
                        self.loading_registry = False
                        self.current = value
                        self._apply_current()
                elif kind == "tool_version":
                    eco, value = rest
                    if eco == self.ecosystem:
                        self.tool_ver = value
                        self._apply_tool_version()
                elif kind == "switched":
                    self._on_switched(*rest)
                elif kind == "update":
                    self._on_update_checked(rest[0], rest[1])
                elif kind == "update_downloaded":
                    self._on_update_downloaded(rest[0], rest[1])
                elif kind == "update_progress":
                    self.status.configure(text=tr("update.downloading", pct=rest[0]))
        except queue.Empty:
            pass

        self._drain_tray()
        self.root.after(80, self._poll)

    def _apply_tool_version(self):
        tool = self.tool_name()
        ready = self.tool_ready()
        if self.tool_ver:
            self.version_label.configure(text=f"{tool} v{self.tool_ver}")
        elif ready:
            self.version_label.configure(text=f"{tool} …")
        else:
            self.version_label.configure(
                text=tr("env.node_missing") if tool == "npm" else tr("main.banner_pip_title")
            )

        if ready:
            self.banner.grid_remove()
        else:
            self._sync_banner_text()
            self.banner.grid()

    def _reorder_by_rank(self):
        """按综合得分重排表格：可用的在前（按名次），不可用的排到末尾。"""
        order = core.rank_results(self.data)
        self.fastest_index = order[0] if order else None
        for position, index in enumerate(order):
            if self.tree.exists(str(index)):
                self.tree.move(str(index), "", position)
        failed = [i for i, item in enumerate(self.data) if not item["ok"]]
        for offset, index in enumerate(failed):
            if self.tree.exists(str(index)):
                self.tree.move(str(index), "", len(order) + offset)
        self._render_all()
        self._update_actions()

    def _on_done(self, results):
        self.data = results
        self._render_all()
        self._set_busy(False)
        self.progress.grid_remove()

        self._reorder_by_rank()

        self.last_run = time.time()
        self._save_cache()

        elapsed = time.time() - self.started_at
        ok_count = sum(1 for item in self.data if item["ok"])
        if self.fastest_index is not None:
            fastest = self.sources[self.fastest_index]
            self.btn_fastest.config(text=tr("main.fastest_named", name=fastest["name"]))
            self.status.configure(text=tr("main.done", ok=ok_count, total=len(self.data),
                                          name=fastest["name"], secs=f"{elapsed:.1f}"))
        else:
            self.btn_fastest.config(text=tr("main.fastest"))
            self.status.configure(text=tr("main.done_none", secs=f"{elapsed:.1f}"))

        core.append_history({
            "time": time.strftime("%Y-%m-%d %H:%M:%S"),
            "ok": ok_count,
            "total": len(self.data),
            "fastest": self.sources[self.fastest_index]["name"] if self.fastest_index is not None else "",
            "elapsed": round(elapsed, 1),
        })

    def _progress_set(self, ratio: float):
        ratio = max(0.0, min(1.0, ratio))
        pad = getattr(self, "_bar_pad", 4)
        span = getattr(self, "_bar_span", 172)
        self.progress.coords(self.progress_fill, pad, pad, pad + span * ratio, pad)

    # ---------------- 结果缓存 ---------------- #

    def _cache_path(self) -> str:
        return core.data_path("last-pip.json" if self.ecosystem == "pip" else "last.json")

    def _save_cache(self):
        payload = {"ecosystem": self.ecosystem, "time": self.last_run,
                   "sources": self.sources, "results": self.data}
        try:
            import json
            with open(self._cache_path(), "w", encoding="utf-8") as handle:
                json.dump(payload, handle, ensure_ascii=False)
        except OSError:
            pass

    def _load_cache(self):
        try:
            import json
            with open(self._cache_path(), encoding="utf-8") as handle:
                payload = json.load(handle)
        except (OSError, ValueError):
            return
        if not isinstance(payload, dict):
            return
        if payload.get("ecosystem", "npm") != self.ecosystem:
            return
        if normalize(json.dumps(payload.get("sources"), sort_keys=True)) != normalize(
                json.dumps(self.sources, sort_keys=True)):
            return
        results = payload.get("results")
        if not isinstance(results, list) or len(results) != len(self.sources):
            return
        self.data = results
        self.last_run = float(payload.get("time") or 0)
        self._reorder_by_rank()
        if self.fastest_index is not None:
            self.btn_fastest.config(text=tr("main.fastest_named",
                                            name=self.sources[self.fastest_index]["name"]))
            self.btn_fastest.state(["!disabled"])
        if self.last_run:
            self.status.configure(text=tr("main.cached", when=format_ago(self.last_run)))

    # ---------------- 切换源 ---------------- #

    def switch_selected(self):
        index = self._selected_index()
        if index is None:
            return
        self._switch_to(index)

    def use_fastest(self):
        if self.fastest_index is not None:
            self._switch_to(self.fastest_index)

    def _switch_to(self, index: int):
        if self._busy():
            return
        source = self.sources[index]
        manager = self.manager
        eco = self.ecosystem
        scope = core.scope(eco)
        self._set_busy(True, switching=True)
        self.status.configure(text=tr("main.switching", manager=manager, name=source["name"]))

        def worker():
            ok, error, previous = core.set_registry(manager, source["url"], scope)
            if not ok and previous:
                core.rollback_registry(manager, previous, scope)
            self.q.put(("switched", manager, source["name"], source["url"],
                        ok, error, previous, eco))

        threading.Thread(target=worker, daemon=True).start()

    def _on_switched(self, manager, name, url, ok, error, previous="", eco=None):
        self._set_busy(False)
        if not ok:
            self.status.configure(text=tr("main.switch_failed"))
            messagebox.showerror(APP_NAME, f"{manager}: {error}")
            return
        self.current = url
        self._apply_current()
        self.status.configure(text=tr("main.switched", manager=manager, name=name))
        messagebox.showinfo(APP_NAME, f"{tr('main.switched', manager=manager, name=name)}\n{url}")
        if (eco or self.ecosystem) == "pip":
            self._warn_pip_overrides()

    def _warn_pip_overrides(self):
        codes = core.pip_warnings(core.scope("pip"))
        messages = []
        if "env_index_url" in codes:
            messages.append(tr("main.pip_env_locked"))
        if "env_extra_index" in codes or "extra_index_url" in codes:
            messages.append(tr("main.pip_extra_index"))
        if messages:
            messagebox.showwarning(APP_NAME, "\n\n".join(messages))

    # ---------------- 源管理 ---------------- #

    def open_sources(self):
        if self._busy():
            return
        dialog = self.dialogs.get("sources")
        if dialog is not None and dialog.winfo_exists():
            dialog.lift()
            dialog.focus_force()
            return
        self.dialogs["sources"] = SourceDialog(self)

    def update_sources(self, items, eco=None):
        eco = eco or self.ecosystem
        ok, error = core.set_sources(items, eco)
        if not ok:
            messagebox.showerror(APP_NAME, f"{tr('sources.save_failed')}\n{error}")
        if eco != self.ecosystem:
            return
        self.sources = core.sources(eco)
        self.fastest_index = None
        self.btn_fastest.config(text=tr("main.fastest"))
        self._rebuild_rows()
        self.current = ""
        self._refresh_current()
        self.status.configure(text=tr("main.sources_updated"))

    # ---------------- 升级 npm ---------------- #

    def open_upgrade(self):
        if self._busy():
            return
        dialog = self.dialogs.get("upgrade")
        if dialog is not None and dialog.winfo_exists():
            dialog.lift()
            dialog.focus_force()
            return
        self.dialogs["upgrade"] = UpgradeDialog(self)

    # ---------------- 环境部署 ---------------- #

    def open_env(self, focus: str | None = None):
        dialog = self.dialogs.get("env")
        if dialog is not None and dialog.winfo_exists():
            dialog.lift()
            dialog.focus_force()
            if focus:
                dialog.highlight(focus)
            return
        self.dialogs["env"] = EnvDialog(self, focus)

    def refresh_tool_state(self):
        self._refresh_manager_buttons()
        self._load_tool_version()
        self.current = ""
        self._refresh_current()

    # ---------------- 其他对话框 ---------------- #

    def open_about(self):
        dialog = self.dialogs.get("about")
        if dialog is not None and dialog.winfo_exists():
            dialog.lift()
            return
        self.dialogs["about"] = AboutDialog(self)

    def open_settings(self):
        dialog = self.dialogs.get("settings")
        if dialog is not None and dialog.winfo_exists():
            dialog.lift()
            return
        self.dialogs["settings"] = SettingsDialog(self)

    def open_scopes(self):
        dialog = self.dialogs.get("scopes")
        if dialog is not None and dialog.winfo_exists():
            dialog.lift()
            return
        self.dialogs["scopes"] = ScopedDialog(self)

    def open_project(self):
        dialog = self.dialogs.get("project")
        if dialog is not None and dialog.winfo_exists():
            dialog.lift()
            return
        self.dialogs["project"] = ProjectDialog(self)

    def open_history(self):
        dialog = self.dialogs.get("history")
        if dialog is not None and dialog.winfo_exists():
            dialog.lift()
            return
        self.dialogs["history"] = HistoryDialog(self)

    def open_github(self):
        import webbrowser
        webbrowser.open(GITHUB_URL)

    # ---------------- 导出 ---------------- #

    def export_csv(self):
        if not any(item.get("lat") for item in self.data):
            messagebox.showinfo(APP_NAME, tr("export.empty"))
            return
        path = filedialog.asksaveasfilename(
            title=tr("export.title"), defaultextension=".csv",
            filetypes=[("CSV", "*.csv")],
            initialfile=time.strftime("npm-mirror-%Y%m%d-%H%M.csv"),
        )
        if not path:
            return
        rows = core.results_to_rows(self.data, self.sources)
        count = core.export_csv(path, rows)
        messagebox.showinfo(APP_NAME, tr("export.done", n=count, path=path))

    # ---------------- 更新 ---------------- #

    def _check_updates(self, manual: bool):
        if not core.get("update_check") and not manual:
            return

        def worker():
            info = updater.check(core.detect_proxy())
            self.q.put(("update", info, manual))

        threading.Thread(target=worker, daemon=True).start()

    def _on_update_checked(self, info, manual):
        if not info:
            if manual:
                messagebox.showinfo(APP_NAME, tr("update.latest"))
            return
        self.update_info = info
        self.update_link.configure(text=tr("main.update_available", version=info["version"]))
        self.update_link.pack(side="left", padx=(10, 0))
        self.update_link.bind("<Button-1>", lambda _e: self._ask_update())
        if manual:
            self._ask_update()

    def _ask_update(self):
        info = self.update_info
        if not info:
            return
        if not messagebox.askyesno(APP_NAME, tr("update.body", version=info["version"],
                                                current=APP_VERSION)):
            return

        dest = os.path.join(os.environ.get("TEMP", "."), "quick-npm-ok-new.exe")

        def worker():
            try:
                def progress(pct, _done, _total):
                    self.q.put(("update_progress", pct))
                updater.download(info, dest, progress=progress, proxy=core.detect_proxy())
                self.q.put(("update_downloaded", dest, info))
            except Exception as exc:  # noqa: BLE001
                self.q.put(("error", tr("update.failed_dl", error=exc)))

        threading.Thread(target=worker, daemon=True).start()
        self.status.configure(text=tr("update.downloading", pct=0))

    def _on_update_downloaded(self, path, info):
        target = sys.executable
        try:
            script = updater.apply_update(path, target)
        except Exception as exc:  # noqa: BLE001
            messagebox.showerror(APP_NAME, tr("update.failed_dl", error=exc))
            return
        if messagebox.askyesno(APP_NAME, tr("update.ready")):
            updater.launch_update(script)
            self.quit_app()


# --------------------------------------------------------------------------- #
# 管理源
# --------------------------------------------------------------------------- #

class SourceDialog(tk.Toplevel):
    def __init__(self, app: App):
        super().__init__(app.root)
        self.app = app
        self.ecosystem = app.ecosystem
        self.sources = [dict(item) for item in app.sources]
        eco_label = tr(f"main.eco_{self.ecosystem}")
        title = tr("sources.title_eco", eco=eco_label)
        self.title(title)
        self.configure(bg=BG)
        self.transient(app.root)
        self.grab_set()
        self.bind("<Escape>", lambda _e: self.destroy())
        self.resizable(False, False)

        wrapper = ttk.Frame(self, padding=(16, 14))
        wrapper.pack(fill="both", expand=True)

        ttk.Label(wrapper, text=title, style="Title.TLabel").pack(anchor="w")
        ttk.Label(wrapper, text=tr("sources.subtitle"), style="Muted.TLabel").pack(anchor="w", pady=(2, 8))

        # 先摆底部按钮和输入行，再让表格吃掉剩余空间：窗口被压缩时按钮不会被挤没
        buttons = ttk.Frame(wrapper)
        buttons.pack(side="bottom", fill="x", pady=(12, 0))
        ttk.Button(buttons, text=tr("sources.add"), command=self._add).pack(side="left")
        ttk.Button(buttons, text=tr("sources.update"), command=self._update).pack(side="left", padx=(8, 0))
        ttk.Button(buttons, text=tr("common.delete"), command=self._delete).pack(side="left", padx=(8, 0))
        ttk.Button(buttons, text=tr("sources.restore"), command=self._restore).pack(side="left", padx=(8, 0))
        ttk.Button(buttons, text=tr("common.close"), command=self.destroy).pack(side="right")

        form = ttk.Frame(wrapper)
        form.pack(side="bottom", fill="x", pady=(12, 0))
        form.columnconfigure(1, weight=1)

        ttk.Label(form, text=tr("sources.name"), style="Muted.TLabel").grid(row=0, column=0, sticky="w")
        self.name_var = tk.StringVar()
        ttk.Entry(form, textvariable=self.name_var).grid(row=0, column=1, sticky="ew", padx=(8, 0))

        ttk.Label(form, text=tr("sources.url"), style="Muted.TLabel").grid(
            row=1, column=0, sticky="w", pady=(6, 0))
        self.url_var = tk.StringVar()
        ttk.Entry(form, textvariable=self.url_var).grid(
            row=1, column=1, sticky="ew", padx=(8, 0), pady=(6, 0))

        table = ttk.Frame(wrapper)
        table.pack(fill="both", expand=True)

        self.tree = ttk.Treeview(table, columns=("name", "url"), show="headings", height=8)
        self.tree.heading("name", text=tr("sources.name"), anchor="center")
        self.tree.heading("url", text=tr("sources.url"), anchor="center")
        self.tree.column("name", width=self.app.px(150), anchor="w", stretch=False)
        self.tree.column("url", width=self.app.px(430), anchor="w")
        self.tree.pack(side="left", fill="both", expand=True)

        scroll = ttk.Scrollbar(table, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=scroll.set)
        scroll.pack(side="right", fill="y")

        self.tree.bind("<<TreeviewSelect>>", self._on_select)
        self._fill()

        app.place_dialog(self, min_width=600, min_height=440)

    def _fill(self):
        for item in self.tree.get_children():
            self.tree.delete(item)
        for index, source in enumerate(self.sources):
            self.tree.insert("", "end", iid=str(index), values=(source["name"], source["url"]))

    def _on_select(self, _event=None):
        selection = self.tree.selection()
        if not selection:
            return
        source = self.sources[int(selection[0])]
        self.name_var.set(source["name"])
        self.url_var.set(source["url"])

    def _clean_input(self):
        name = self.name_var.get().strip()
        url = self.url_var.get().strip().rstrip("/")
        if not name:
            messagebox.showerror(tr("common.error"), tr("sources.bad_name"), parent=self)
            return None
        if not url.lower().startswith(("http://", "https://")):
            messagebox.showerror(tr("common.error"), tr("sources.bad_url"), parent=self)
            return None
        return name, url

    def _duplicate(self, url, ignore_index=None):
        for index, source in enumerate(self.sources):
            if index != ignore_index and normalize(source["url"]) == normalize(url):
                return True
        return False

    def _commit(self, message: str):
        self._fill()
        self.app.update_sources(self.sources, self.ecosystem)
        self.app.status.configure(text=message)

    def _add(self):
        cleaned = self._clean_input()
        if not cleaned:
            return
        name, url = cleaned
        if self._duplicate(url):
            messagebox.showerror(tr("common.error"), tr("sources.duplicate"), parent=self)
            return
        self.sources.append({"name": name, "url": url})
        self._commit(tr("sources.added", name=name))

    def _update(self):
        selection = self.tree.selection()
        if not selection:
            messagebox.showinfo(tr("common.tip"), tr("sources.pick_first"), parent=self)
            return
        index = int(selection[0])
        cleaned = self._clean_input()
        if not cleaned:
            return
        name, url = cleaned
        if self._duplicate(url, ignore_index=index):
            messagebox.showerror(tr("common.error"), tr("sources.duplicate"), parent=self)
            return
        self.sources[index] = {"name": name, "url": url}
        self._commit(tr("sources.updated", name=name))

    def _delete(self):
        selection = self.tree.selection()
        if not selection:
            messagebox.showinfo(tr("common.tip"), tr("sources.pick_delete"), parent=self)
            return
        index = int(selection[0])
        name = self.sources[index]["name"]
        if not messagebox.askyesno(tr("sources.delete_title"), tr("sources.delete_body", name=name), parent=self):
            return
        del self.sources[index]
        self.name_var.set("")
        self.url_var.set("")
        self._commit(tr("sources.deleted", name=name))

    def _restore(self):
        if not messagebox.askyesno(tr("sources.restore_title"), tr("sources.restore_body"), parent=self):
            return
        self.sources = [dict(item) for item in core.default_sources(self.ecosystem)]
        self._commit(tr("sources.restored"))


# --------------------------------------------------------------------------- #
# 升级 npm
# --------------------------------------------------------------------------- #

class UpgradeDialog(tk.Toplevel):
    def __init__(self, app: App):
        super().__init__(app.root)
        self.app = app
        self.q: queue.Queue = queue.Queue()
        self.process = None
        self.running = False
        self.eco = app.ecosystem
        self.is_pip = self.eco == "pip"
        title = tr("up.title_pip") if self.is_pip else tr("up.title")

        self.title(title)
        self.configure(bg=BG)
        self.transient(app.root)
        self.resizable(False, False)
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self.bind("<Escape>", lambda _e: self._on_close())

        wrapper = ttk.Frame(self, padding=(16, 14))
        wrapper.pack(fill="both", expand=True)

        ttk.Label(wrapper, text=title, style="Title.TLabel").pack(anchor="w")
        ttk.Label(wrapper,
                  text=tr("up.subtitle_pip") if self.is_pip else tr("up.subtitle"),
                  style="Muted.TLabel").pack(anchor="w", pady=(2, 10))

        info = ttk.Frame(wrapper)
        info.pack(fill="x")
        info.columnconfigure(1, weight=1)

        ttk.Label(info, text=tr("up.current"), style="Muted.TLabel").grid(row=0, column=0, sticky="w")
        self.current_label = ttk.Label(info, text=tr("up.checking"), style="Current.TLabel")
        self.current_label.grid(row=0, column=1, sticky="w", padx=(10, 0))

        ttk.Label(info, text=tr("up.latest"), style="Muted.TLabel").grid(row=1, column=0, sticky="w", pady=(4, 0))
        self.latest_label = ttk.Label(info, text=tr("up.checking"), style="Current.TLabel")
        self.latest_label.grid(row=1, column=1, sticky="w", padx=(10, 0), pady=(4, 0))

        self.pip_path = ""
        if self.is_pip:
            self.pip_path = core.find_pip(core.scope("pip"))
            ttk.Label(info, text=tr("up.target"), style="Muted.TLabel").grid(
                row=2, column=0, sticky="nw", pady=(4, 0))
            self.target_label = ttk.Label(
                info, text=self.pip_path or tr("up.pip_missing"), style="Muted.TLabel",
                wraplength=app.px(460), justify="left")
            self.target_label.grid(row=2, column=1, sticky="w", padx=(10, 0), pady=(4, 0))

        actions = ttk.Frame(wrapper)
        actions.pack(fill="x", pady=(12, 8))

        self.btn_install = tk.Button(
            actions, text=tr("up.install"), command=self._install,
            bg=ACCENT, fg="white", activebackground=ACCENT_DARK, activeforeground="white",
            relief="flat", bd=0, padx=18, pady=6, cursor="hand2",
            font=(app.font_family, 10, "bold"), disabledforeground="#dbeafe",
        )
        self.btn_install.pack(side="left")

        self.btn_elevate = ttk.Button(actions, text=tr("up.elevate"), command=self._elevate)
        self.btn_elevate.pack(side="left", padx=(8, 0))

        self.btn_recheck = ttk.Button(actions, text=tr("up.recheck"), command=self._refresh_versions)
        self.btn_recheck.pack(side="left", padx=(8, 0))

        ttk.Button(actions, text=tr("common.close"), command=self._on_close).pack(side="right")

        log_frame = ttk.Frame(wrapper)
        log_frame.pack(fill="both", expand=True)

        self.log = tk.Text(log_frame, height=9, wrap="none", bg="#fbfcfe", fg="#334155",
                           relief="solid", bd=1, highlightthickness=0,
                           insertbackground="#334155", padx=10, pady=8,
                           font=("Consolas", 9), state="disabled")
        self.log.pack(side="left", fill="both", expand=True)

        scroll = ttk.Scrollbar(log_frame, orient="vertical", command=self.log.yview)
        self.log.configure(yscrollcommand=scroll.set)
        scroll.pack(side="right", fill="y")

        self.status = ttk.Label(wrapper, text="", style="Muted.TLabel")
        self.status.pack(anchor="w", pady=(8, 0))

        self._refresh_versions()
        self._append(tr("up.waiting"))

        app.place_dialog(self, min_width=600, min_height=360)
        self._poll_id = self.after(100, self._poll)

    def _version_tool(self) -> str:
        if not self.is_pip:
            return "npm"
        return "pip" if core.tool_exists("pip") else "pip3"

    def _refresh_versions(self):
        self.current_label.configure(text=tr("up.checking"))
        self.latest_label.configure(text=tr("up.checking"))
        tool = self._version_tool()
        is_pip = self.is_pip
        bases = [item["url"] for item in self.app.sources]

        def worker():
            current = core.tool_version(tool)
            latest = core.pip_latest_version(bases=bases) if is_pip else core.latest_npm_version()
            self.q.put(("versions", current, latest))

        threading.Thread(target=worker, daemon=True).start()

    def _apply_versions(self, current, latest):
        self.current_label.configure(text=f"v{current}" if current else tr("common.not_installed"))
        if latest:
            if current and latest == current:
                self.latest_label.configure(text=tr("up.newest", version=latest))
            else:
                self.latest_label.configure(text=f"v{latest}")
        else:
            self.latest_label.configure(text=tr("up.failed"))
        self.app.tool_ver = current
        self.app._apply_tool_version()

    def _install(self):
        if self.running:
            return
        if self.is_pip:
            shown, argv = core.pip_upgrade_argv(core.scope("pip"))
            if not argv:
                messagebox.showerror(APP_NAME, tr("up.pip_missing"), parent=self)
                return
        else:
            if not core.tool_exists("npm"):
                messagebox.showerror(APP_NAME, tr("main.npm_missing"), parent=self)
                return
            shown, argv = "npm install -g npm@latest", None
        if not messagebox.askyesno(
                tr("up.title_pip") if self.is_pip else tr("up.title"),
                tr("up.confirm", cmd=shown), parent=self):
            return

        self.running = True
        self.btn_install.config(state="disabled", bg="#9ec5ff")
        self.btn_elevate.state(["disabled"])
        self.btn_recheck.state(["disabled"])
        self.status.configure(text=tr("up.running"))
        self._append("")
        self._append("$ " + shown)

        def worker():
            try:
                if argv:
                    proc = core.run_command(argv, timeout=900)
                else:
                    proc = core.run_tool("npm", ["install", "-g", "npm@latest"], timeout=600)
                text = (proc.stdout or "").strip() or (proc.stderr or "").strip()
                self.q.put(("install", proc.returncode, text))
            except Exception as exc:  # noqa: BLE001
                self.q.put(("install", -1, str(exc)))

        threading.Thread(target=worker, daemon=True).start()

    def _elevate(self):
        if self.running:
            return
        if os.name != "nt":
            return
        if self.is_pip:
            shown, argv = core.pip_upgrade_argv(core.scope("pip"))
            if not argv:
                messagebox.showerror(APP_NAME, tr("up.pip_missing"), parent=self)
                return
        else:
            shown = "npm install -g npm@latest"

        self._append("")
        self._append(tr("up.elevated_log"))
        self.status.configure(text=tr("up.elevated_log"))

        def worker():
            try:
                deploy.run_elevated("cmd.exe", f"/c {shown} & echo. & pause")
            except Exception:  # noqa: BLE001
                pass
            self.q.put(("elevated_done", 0, ""))

        threading.Thread(target=worker, daemon=True).start()

    def _finish(self, code):
        self.running = False
        self.btn_install.config(state="normal", bg=ACCENT)
        self.btn_elevate.state(["!disabled"])
        self.btn_recheck.state(["!disabled"])
        if code == 0:
            self.status.configure(text=tr("up.done"))
            self._append("")
            self._append(tr("up.done_log"))
            self._refresh_versions()
            self.app._load_tool_version()
        else:
            self.status.configure(text=tr("up.fail"))
            self._append("")
            self._append(tr("up.fail_hint"))

    def _append(self, text: str):
        self.log.configure(state="normal")
        self.log.insert("end", text + "\n")
        self.log.see("end")
        self.log.configure(state="disabled")

    def _poll(self):
        self._poll_id = None
        try:
            while True:
                message = self.q.get_nowait()
                kind, *rest = message
                if kind == "versions":
                    self._apply_versions(rest[0], rest[1])
                elif kind == "install":
                    self._append(rest[1] or "")
                    self._finish(rest[0])
                elif kind == "elevated_done":
                    self._refresh_versions()
        except queue.Empty:
            pass
        if self.winfo_exists():
            self._poll_id = self.after(100, self._poll)

    def destroy(self):
        if getattr(self, "_poll_id", None):
            try:
                self.after_cancel(self._poll_id)
            except Exception:  # noqa: BLE001
                pass
            self._poll_id = None
        self.app.dialogs.pop("upgrade", None)
        super().destroy()

    def _on_close(self):
        if self.running:
            messagebox.showinfo(APP_NAME, tr("up.busy_close"), parent=self)
            return
        self.app.dialogs.pop("upgrade", None)
        self.destroy()


# --------------------------------------------------------------------------- #
# 环境部署
# --------------------------------------------------------------------------- #

class EnvDialog(tk.Toplevel):
    SECTIONS = (
        ("node", "env.node", "env.uac_hint"),
        ("python", "env.python", "env.peruser_hint"),
        ("git", "env.git", "env.uac_hint"),
    )

    def __init__(self, app: App, focus: str | None = None):
        super().__init__(app.root)
        self.app = app
        self.q: queue.Queue = queue.Queue()
        self.catalogs = {}
        self.installing = False
        self.cancel_event = threading.Event()
        self.focus_section = focus

        self.title(tr("env.title"))
        self.configure(bg=BG)
        self.transient(app.root)
        self.grab_set()
        self.bind("<Escape>", lambda _e: self.destroy())
        self.resizable(False, False)

        wrapper = ttk.Frame(self, padding=(16, 14))
        wrapper.pack(fill="both", expand=True)

        ttk.Label(wrapper, text=tr("env.title"), style="Title.TLabel").pack(anchor="w")
        ttk.Label(wrapper, text=tr("env.subtitle"), style="Muted.TLabel").pack(anchor="w", pady=(2, 10))

        self.status_labels = {}
        self.combos = {}
        self.buttons = {}

        for key, label_key, hint_key in self.SECTIONS:
            card = ttk.Frame(wrapper, style="Card.TFrame", padding=(12, 10))
            card.pack(fill="x", pady=(0, 8))
            card.columnconfigure(2, weight=1)

            ttk.Label(card, text=tr(label_key), style="Card.TLabel",
                      font=(app.font_family, 10, "bold")).grid(row=0, column=0, sticky="w")
            status = tk.Label(card, text=tr("env.detecting"), bg=CARD, fg=TEXT_MUTED,
                              font=(app.font_family, 9))
            status.grid(row=0, column=3, sticky="e")
            self.status_labels[key] = status

            ttk.Label(card, text=tr("env.version"), style="CardMuted.TLabel").grid(
                row=1, column=0, sticky="w", pady=(6, 0))
            combo = ttk.Combobox(card, width=30, state="readonly")
            combo.grid(row=1, column=1, sticky="w", padx=(8, 0), pady=(6, 0))
            combo.set(tr("common.loading"))
            self.combos[key] = combo

            button = ttk.Button(card, text=tr("env.install"),
                                command=lambda k=key: self._install(k))
            button.grid(row=1, column=2, sticky="w", padx=(10, 0), pady=(6, 0))
            self.buttons[key] = button

            ttk.Label(card, text=tr(hint_key), style="CardFaint.TLabel").grid(
                row=2, column=0, columnspan=4, sticky="w", pady=(4, 0))

        actions = ttk.Frame(wrapper)
        self.actions = actions
        self.progress = ttk.Progressbar(actions, mode="determinate", maximum=100)
        self.progress.pack(side="left", fill="x", expand=True)
        self.btn_cancel = ttk.Button(actions, text=tr("common.cancel"), command=self._cancel)
        self.btn_cancel.pack(side="left", padx=(8, 0))
        self.btn_cancel.state(["disabled"])

        self.status = ttk.Label(wrapper, text="", style="Muted.TLabel")

        log_frame = ttk.Frame(wrapper)
        self.log_frame = log_frame

        self.log = tk.Text(log_frame, height=9, wrap="none", bg="#fbfcfe", fg="#334155",
                           relief="solid", bd=1, highlightthickness=0,
                           insertbackground="#334155", padx=10, pady=8,
                           font=("Consolas", 9), state="disabled")
        self.log.pack(side="left", fill="both", expand=True)

        scroll = ttk.Scrollbar(log_frame, orient="vertical", command=self.log.yview)
        self.log.configure(yscrollcommand=scroll.set)
        scroll.pack(side="right", fill="y")

        self.hint = ttk.Label(wrapper, text="", style="Faint.TLabel")

        # 先占住底部，日志区再吃剩余高度：被压缩时抓进度条和按钮还在
        self.hint.pack(side="bottom", anchor="w", pady=(6, 0))
        actions.pack(side="bottom", fill="x", pady=(0, 6))
        self.status.pack(side="bottom", anchor="w", pady=(0, 6))
        log_frame.pack(fill="both", expand=True)

        app.place_dialog(self, min_width=600, min_height=560)

        self._refresh_status()
        self._load_catalogs()
        self._poll_id = self.after(100, self._poll)

        if focus:
            self.after(200, lambda: self.highlight(focus))

    def highlight(self, key: str):
        if key in self.combos:
            self.combos[key].focus_set()

    # ---------------- 目录 / 状态 ---------------- #

    def _load_catalogs(self):
        def worker():
            proxy = core.detect_proxy()
            self.q.put(("catalog", "node", deploy.node_versions(proxy)))
            self.q.put(("catalog", "python", deploy.python_versions(proxy)))
            self.q.put(("catalog", "git", deploy.git_versions(proxy)))
        threading.Thread(target=worker, daemon=True).start()

    def _apply_catalog(self, key, rows):
        self.catalogs[key] = rows
        combo = self.combos[key]
        if not rows:
            combo["values"] = []
            combo.set(tr("env.no_versions"))
            return
        labels = []
        for index, row in enumerate(rows):
            label = f"v{row['version']}"
            if row.get("lts"):
                label += f"  {row['lts']}"
            if index == 0:
                label += f"  ({tr('env.recommended')})"
            elif row.get("note"):
                label += f"  · {row['note']}"
            labels.append(label)
        combo["values"] = labels
        combo.current(0)

    def _refresh_status(self):
        def worker():
            self.q.put(("status", deploy.installed_versions()))
        threading.Thread(target=worker, daemon=True).start()

    def _apply_status(self, data):
        mapping = {"node": ("node", "npm"), "python": ("python",), "git": ("git",)}
        for key, names in mapping.items():
            parts = [data.get(name) for name in names if data.get(name)]
            label = self.status_labels[key]
            if parts:
                label.configure(text=f"✓ {', '.join(parts)}", fg=OK_GREEN)
                self.buttons[key].configure(text=tr("env.reinstall"))
            else:
                label.configure(text=tr("common.not_installed"), fg=TEXT_MUTED)
                self.buttons[key].configure(text=tr("env.install"))
        self.app.refresh_tool_state()

    # ---------------- 安装 ---------------- #

    def _install(self, key):
        if self.installing:
            return
        rows = self.catalogs.get(key) or []
        index = self.combos[key].current()
        if not rows or index < 0 or index >= len(rows):
            messagebox.showinfo(APP_NAME, tr("env.need_select"), parent=self)
            return
        row = rows[index]
        version = row["version"]

        if key == "node":
            hint = "Node.js 与 npm 会安装到系统，需要一次管理员授权。"
            title = f"Node.js v{version}"
        elif key == "python":
            hint = "Python 会安装到当前用户目录，不影响其他用户。"
            title = f"Python {version}"
        else:
            hint = "Git 会安装到系统，需要一次管理员授权。"
            title = f"Git {version}"

        if not messagebox.askyesno(tr("env.title"), f"{title}\n\n{hint}\n\n继续吗？", parent=self):
            return

        self.installing = True
        self.cancel_event = threading.Event()
        self.progress["value"] = 0
        self.actions.pack(fill="x", pady=(2, 6), before=self.log_frame)
        self.btn_cancel.state(["!disabled"])
        for button in self.buttons.values():
            button.state(["disabled"])
        self.status.configure(text=tr("env.install_running"))
        self._append("")
        self._append(f"=== {title} ===")

        def log(text):
            self.q.put(("log", text))

        def progress(pct, done, total):
            self.q.put(("progress", pct, done, total))

        def worker():
            try:
                if key == "node":
                    ok, error = deploy.install_node(version, log, progress,
                                                    self.cancel_event, core.detect_proxy())
                elif key == "python":
                    ok, error = deploy.install_python(version, log, progress,
                                                      self.cancel_event, core.detect_proxy())
                else:
                    ok, error = deploy.install_git(version, log, progress,
                                                   self.cancel_event, core.detect_proxy())
                self.q.put(("installed", ok, error))
            except Exception as exc:  # noqa: BLE001
                self.q.put(("installed", False, str(exc)))

        threading.Thread(target=worker, daemon=True).start()

    def _cancel(self):
        self.cancel_event.set()
        self._append(tr("env.cancelled"))

    def _finish_install(self, ok, error):
        self.installing = False
        self.btn_cancel.state(["disabled"])
        for button in self.buttons.values():
            button.state(["!disabled"])
        if ok:
            self.progress["value"] = 100
            self.status.configure(text=tr("env.done", name=""))
            self._append(tr("env.restart_hint"))
            self.after(1200, self.actions.pack_forget)
            self._refresh_status()
        else:
            self.actions.pack_forget()
            if error == "cancelled":
                self.status.configure(text=tr("env.cancelled"))
            else:
                self.status.configure(text=tr("env.failed"))
                self._append(f"✗ {error}")
                messagebox.showerror(APP_NAME, f"{tr('env.failed')}\n{error}", parent=self)

    def _append(self, text: str):
        self.log.configure(state="normal")
        self.log.insert("end", text + "\n")
        self.log.see("end")
        self.log.configure(state="disabled")

    def _poll(self):
        self._poll_id = None
        try:
            while True:
                kind, *rest = self.q.get_nowait()
                if kind == "catalog":
                    self._apply_catalog(rest[0], rest[1])
                elif kind == "status":
                    self._apply_status(rest[0])
                elif kind == "log":
                    self._append(rest[0])
                elif kind == "progress":
                    pct = rest[0]
                    self.progress["value"] = pct
                    self.status.configure(text=tr("env.downloading", name="", pct=pct))
                elif kind == "installed":
                    self._finish_install(rest[0], rest[1])
        except queue.Empty:
            pass
        if self.winfo_exists():
            self._poll_id = self.after(100, self._poll)

    def destroy(self):
        if getattr(self, "_poll_id", None):
            try:
                self.after_cancel(self._poll_id)
            except Exception:  # noqa: BLE001
                pass
            self._poll_id = None
        self.app.dialogs.pop("env", None)
        super().destroy()


# --------------------------------------------------------------------------- #
# 关于
# --------------------------------------------------------------------------- #

class AboutDialog(tk.Toplevel):
    def __init__(self, app: App):
        super().__init__(app.root)
        self.app = app
        self.title(tr("about.title"))
        self.configure(bg=CARD)
        self.transient(app.root)
        self.resizable(False, False)
        self.grab_set()
        self.bind("<Escape>", lambda _e: self.destroy())

        wrapper = tk.Frame(self, bg=CARD, padx=26, pady=20)
        wrapper.pack(fill="both", expand=True)

        icon = resource_path("app.png")
        try:
            image = tk.PhotoImage(file=icon)
            factor = max(1, image.width() // 72)
            self._image = image.subsample(factor, factor) if factor > 1 else image
            tk.Label(wrapper, image=self._image, bg=CARD).pack()
        except Exception:  # noqa: BLE001
            pass

        tk.Label(wrapper, text=tr("main.title"), bg=CARD, fg=TEXT,
                 font=(app.font_family, 14, "bold")).pack(pady=(10, 2))
        tk.Label(wrapper, text=TAGLINE if core.language() == "zh" else TAGLINE_EN,
                 bg=CARD, fg="#4b5563", font=(app.font_family, 10)).pack()
        tk.Label(wrapper, text=tr("about.version", version=APP_VERSION), bg=CARD, fg=TEXT_FAINT,
                 font=(app.font_family, 9)).pack(pady=(6, 10))

        tk.Label(wrapper, text=tr("about.desc"), bg=CARD, fg=TEXT_MUTED,
                 font=(app.font_family, 9)).pack()
        tk.Label(wrapper, text=tr("about.desc2"), bg=CARD, fg=TEXT_MUTED,
                 font=(app.font_family, 9)).pack(pady=(2, 12))

        link = tk.Label(wrapper, text=GITHUB_URL, bg=CARD, fg=ACCENT, cursor="hand2",
                        font=(app.font_family, 9))
        link.pack()
        link.bind("<Button-1>", lambda _e: app.open_github())

        tk.Label(wrapper, text=tr("about.license", author="NANYAN-01"), bg=CARD, fg=TEXT_FAINT,
                 font=(app.font_family, 8)).pack(pady=(10, 0))
        tk.Label(wrapper, text=tr("about.built_with"), bg=CARD, fg=TEXT_FAINT,
                 font=(app.font_family, 8)).pack()

        ttk.Button(wrapper, text=tr("common.close"), command=self.destroy).pack(pady=(14, 0))

        app.place_dialog(self, min_width=340)

    def destroy(self):
        self.app.dialogs.pop("about", None)
        super().destroy()


# --------------------------------------------------------------------------- #
# 设置
# --------------------------------------------------------------------------- #

class SettingsDialog(tk.Toplevel):
    LANGS = (("zh", "简体中文"), ("en", "English"))

    def __init__(self, app: App):
        super().__init__(app.root)
        self.app = app
        self.title(tr("settings.title"))
        self.configure(bg=BG)
        self.transient(app.root)
        self.resizable(False, False)
        self.grab_set()
        self.bind("<Escape>", lambda _e: self.destroy())

        wrapper = ttk.Frame(self, padding=(18, 16))
        wrapper.pack(fill="both", expand=True)

        ttk.Label(wrapper, text=tr("settings.title"), style="Title.TLabel").grid(
            row=0, column=0, columnspan=2, sticky="w", pady=(0, 10))
        wrapper.columnconfigure(1, weight=1)

        ttk.Label(wrapper, text=tr("settings.language"), style="Muted.TLabel").grid(
            row=1, column=0, sticky="w", pady=4)
        self.lang_var = tk.StringVar(value=dict(self.LANGS)[core.language()])
        lang = ttk.Combobox(wrapper, textvariable=self.lang_var, state="readonly", width=18,
                            values=[label for _, label in self.LANGS])
        lang.grid(row=1, column=1, sticky="w", padx=(10, 0))

        ttk.Label(wrapper, text=tr("settings.proxy"), style="Muted.TLabel").grid(
            row=2, column=0, sticky="w", pady=4)
        proxy = core.get("proxy") or {}
        self.proxy_mode = tk.StringVar(value=proxy.get("mode", "auto"))
        mode_box = ttk.Frame(wrapper)
        mode_box.grid(row=2, column=1, sticky="w", padx=(10, 0))
        for value, label in (("auto", tr("settings.proxy_auto")),
                             ("manual", tr("settings.proxy_manual")),
                             ("off", tr("settings.proxy_off"))):
            ttk.Radiobutton(mode_box, text=label, value=value, variable=self.proxy_mode).pack(
                side="left", padx=(0, 10))

        ttk.Label(wrapper, text=tr("settings.proxy_url"), style="Muted.TLabel").grid(
            row=3, column=0, sticky="w", pady=4)
        self.proxy_url = tk.StringVar(value=proxy.get("url", ""))
        ttk.Entry(wrapper, textvariable=self.proxy_url, width=28).grid(
            row=3, column=1, sticky="w", padx=(10, 0))

        self.update_var = tk.BooleanVar(value=bool(core.get("update_check")))
        ttk.Checkbutton(wrapper, text=tr("settings.update_check"), variable=self.update_var).grid(
            row=4, column=0, columnspan=2, sticky="w", pady=(8, 0))

        self.autostart_var = tk.BooleanVar(value=bool(core.get("autostart")))
        ttk.Checkbutton(wrapper, text=tr("settings.autostart"), variable=self.autostart_var).grid(
            row=5, column=0, columnspan=2, sticky="w")

        buttons = ttk.Frame(wrapper)
        buttons.grid(row=6, column=0, columnspan=2, sticky="e", pady=(14, 0))
        ttk.Button(buttons, text=tr("common.cancel"), command=self.destroy).pack(side="right")
        ttk.Button(buttons, text=tr("common.save"), command=self._save).pack(side="right", padx=(0, 8))

        app.place_dialog(self, min_width=420)

    def _save(self):
        old_lang = core.language()
        chosen = {label: code for code, label in self.LANGS}.get(self.lang_var.get(), "zh")
        core.set_value("language", chosen)
        core.set_value("proxy", {"mode": self.proxy_mode.get(), "url": self.proxy_url.get().strip()})
        core.set_value("update_check", bool(self.update_var.get()))
        core.set_value("autostart", bool(self.autostart_var.get()))
        set_autostart(bool(self.autostart_var.get()))
        messagebox.showinfo(APP_NAME, tr("settings.saved"), parent=self)
        if chosen != old_lang:
            messagebox.showinfo(
                APP_NAME,
                "界面语言会在下次启动时生效。\nThe new language applies next time you start the app.",
                parent=self,
            )
        self.destroy()

    def destroy(self):
        self.app.dialogs.pop("settings", None)
        super().destroy()


def set_autostart(enabled: bool):
    if os.name != "nt":
        return
    try:
        import winreg
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                             r"Software\Microsoft\Windows\CurrentVersion\Run",
                             0, winreg.KEY_SET_VALUE)
        try:
            if enabled:
                winreg.SetValueEx(key, "Quick-npm-ok", 0, winreg.REG_SZ, f'"{sys.executable}"')
            else:
                try:
                    winreg.DeleteValue(key, "Quick-npm-ok")
                except OSError:
                    pass
        finally:
            winreg.CloseKey(key)
    except OSError:
        pass


# --------------------------------------------------------------------------- #
# 作用域源
# --------------------------------------------------------------------------- #

class ScopedDialog(tk.Toplevel):
    def __init__(self, app: App):
        super().__init__(app.root)
        self.app = app
        self.title(tr("scope.title"))
        self.configure(bg=BG)
        self.transient(app.root)
        self.resizable(False, False)
        self.grab_set()
        self.bind("<Escape>", lambda _e: self.destroy())

        wrapper = ttk.Frame(self, padding=(16, 14))
        wrapper.pack(fill="both", expand=True)

        ttk.Label(wrapper, text=tr("scope.title"), style="Title.TLabel").pack(anchor="w")
        ttk.Label(wrapper, text=tr("scope.subtitle"), style="Muted.TLabel").pack(anchor="w", pady=(2, 8))

        self.tree = ttk.Treeview(wrapper, columns=("scope", "url"), show="headings", height=6)
        self.tree.heading("scope", text="Scope", anchor="center")
        self.tree.heading("url", text=tr("sources.url"), anchor="center")
        self.tree.column("scope", width=self.app.px(130), anchor="w", stretch=False)
        self.tree.column("url", width=self.app.px(420), anchor="w")
        self.tree.pack(fill="both", expand=True)

        form = ttk.Frame(wrapper)
        form.pack(fill="x", pady=(10, 0))
        self.scope_var = tk.StringVar()
        self.url_var = tk.StringVar()
        ttk.Entry(form, textvariable=self.scope_var, width=16).pack(side="left")
        ttk.Entry(form, textvariable=self.url_var).pack(side="left", fill="x", expand=True, padx=(8, 0))

        buttons = ttk.Frame(wrapper)
        buttons.pack(fill="x", pady=(10, 0))
        ttk.Button(buttons, text=tr("scope.add"), command=self._add).pack(side="left")
        ttk.Button(buttons, text=tr("common.delete"), command=self._delete).pack(side="left", padx=(8, 0))
        ttk.Button(buttons, text=tr("common.close"), command=self.destroy).pack(side="right")

        self._fill()

        app.place_dialog(self, min_width=560, min_height=360)

    def _fill(self):
        for item in self.tree.get_children():
            self.tree.delete(item)
        for index, row in enumerate(core.list_scoped(core.scope("npm"))):
            self.tree.insert("", "end", iid=str(index), values=(row["scope"], row["url"]))

    def _add(self):
        scope = self.scope_var.get().strip()
        url = self.url_var.get().strip().rstrip("/")
        if not scope.startswith("@") or ":" in scope or " " in scope:
            messagebox.showerror(APP_NAME, tr("scope.bad"), parent=self)
            return
        if not url.lower().startswith(("http://", "https://")):
            messagebox.showerror(APP_NAME, tr("sources.bad_url"), parent=self)
            return
        ok, error = core.set_scoped(scope, url, core.scope("npm"))
        if not ok:
            messagebox.showerror(APP_NAME, error, parent=self)
            return
        self._fill()
        self.app.status.configure(text=tr("scope.saved"))

    def _delete(self):
        selection = self.tree.selection()
        if not selection:
            return
        row = core.list_scoped(core.scope("npm"))[int(selection[0])]
        core.remove_scoped(row["scope"], core.scope("npm"))
        self._fill()

    def destroy(self):
        self.app.dialogs.pop("scopes", None)
        super().destroy()


# --------------------------------------------------------------------------- #
# 项目级 .npmrc
# --------------------------------------------------------------------------- #

class ProjectDialog(tk.Toplevel):
    def __init__(self, app: App):
        super().__init__(app.root)
        self.app = app
        self.directory = ""
        self.title(tr("project.title"))
        self.configure(bg=BG)
        self.transient(app.root)
        self.resizable(False, False)
        self.grab_set()
        self.bind("<Escape>", lambda _e: self.destroy())

        wrapper = ttk.Frame(self, padding=(16, 14))
        wrapper.pack(fill="both", expand=True)

        ttk.Label(wrapper, text=tr("project.title"), style="Title.TLabel").pack(anchor="w")
        ttk.Label(wrapper, text=tr("project.subtitle"), style="Muted.TLabel").pack(anchor="w", pady=(2, 8))

        row = ttk.Frame(wrapper)
        row.pack(fill="x", pady=(4, 0))
        self.path_label = ttk.Label(row, text=tr("project.none"), style="Muted.TLabel")
        self.path_label.pack(side="left")
        ttk.Button(row, text=tr("project.pick"), command=self._pick).pack(side="right")

        row2 = ttk.Frame(wrapper)
        row2.pack(fill="x", pady=(12, 0))
        ttk.Label(row2, text=tr("sources.url"), style="Muted.TLabel").pack(side="left")
        self.url_var = tk.StringVar()
        self.combo = ttk.Combobox(row2, textvariable=self.url_var, width=48,
                                  values=[f'{item["name"]}  {item["url"]}' for item in core.sources()])
        self.combo.pack(side="left", padx=(8, 0))
        if core.sources():
            self.combo.current(core_current_index(core.sources(), app.current))

        buttons = ttk.Frame(wrapper)
        buttons.pack(fill="x", pady=(14, 0))
        ttk.Button(buttons, text=tr("project.write"), command=self._write).pack(side="left")
        ttk.Button(buttons, text=tr("common.close"), command=self.destroy).pack(side="right")

        app.place_dialog(self, min_width=460, min_height=200)

    def _pick(self):
        directory = filedialog.askdirectory(title=tr("project.pick"), parent=self)
        if not directory:
            return
        self.directory = directory
        self.path_label.configure(text=directory)
        existing = core.read_npmrc(os.path.join(directory, ".npmrc")).get("registry")
        if existing:
            self.app.status.configure(text=existing)

    def _write(self):
        if not self.directory:
            messagebox.showinfo(APP_NAME, tr("project.none"), parent=self)
            return
        index = self.combo.current()
        items = core.sources()
        if index < 0 or index >= len(items):
            messagebox.showinfo(APP_NAME, tr("env.need_select"), parent=self)
            return
        try:
            path = core.write_project_npmrc(self.directory, items[index]["url"])
        except Exception as exc:  # noqa: BLE001
            messagebox.showerror(APP_NAME, tr("project.failed", error=exc), parent=self)
            return
        messagebox.showinfo(APP_NAME, tr("project.done", path=path), parent=self)
        self.destroy()

    def destroy(self):
        self.app.dialogs.pop("project", None)
        super().destroy()


def core_current_index(items, current):
    target = normalize(current)
    for index, item in enumerate(items):
        if normalize(item["url"]) == target:
            return index
    return 0


# --------------------------------------------------------------------------- #
# 历史记录
# --------------------------------------------------------------------------- #

class HistoryDialog(tk.Toplevel):
    def __init__(self, app: App):
        super().__init__(app.root)
        self.app = app
        self.title(tr("history.title"))
        self.configure(bg=BG)
        self.transient(app.root)
        self.resizable(True, True)
        self.bind("<Escape>", lambda _e: self.destroy())

        wrapper = ttk.Frame(self, padding=(16, 14))
        wrapper.pack(fill="both", expand=True)

        ttk.Label(wrapper, text=tr("history.title"), style="Title.TLabel").pack(anchor="w", pady=(0, 8))

        table = ttk.Frame(wrapper)
        table.pack(fill="both", expand=True)
        self.tree = ttk.Treeview(table, columns=("time", "fastest", "ok"),
                                 show="headings", height=12)
        self.tree.heading("time", text=tr("history.col_time"), anchor="center")
        self.tree.heading("fastest", text=tr("history.col_fastest"), anchor="center")
        self.tree.heading("ok", text=tr("history.col_ok"), anchor="center")
        self.tree.column("time", width=self.app.px(180), anchor="w", stretch=False)
        self.tree.column("fastest", width=self.app.px(180), anchor="w")
        self.tree.column("ok", width=self.app.px(90), anchor="center", stretch=False)
        self.tree.pack(side="left", fill="both", expand=True)

        scroll = ttk.Scrollbar(table, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=scroll.set)
        scroll.pack(side="right", fill="y")

        self.empty = ttk.Label(wrapper, text="", style="Muted.TLabel")
        self.empty.pack(anchor="w", pady=(8, 0))

        buttons = ttk.Frame(wrapper)
        buttons.pack(fill="x", pady=(10, 0))
        ttk.Button(buttons, text=tr("settings.export"), command=app.export_csv).pack(side="left")
        ttk.Button(buttons, text=tr("common.close"), command=self.destroy).pack(side="right")

        rows = core.load_history()
        for index, row in enumerate(rows):
            self.tree.insert("", "end", iid=str(index), values=(
                row.get("time", ""), row.get("fastest", ""),
                f'{row.get("ok", 0)}/{row.get("total", 0)}',
            ))
        if not rows:
            self.empty.configure(text=tr("history.empty"))

        app.place_dialog(self, min_width=480, min_height=420)

    def destroy(self):
        self.app.dialogs.pop("history", None)
        super().destroy()
