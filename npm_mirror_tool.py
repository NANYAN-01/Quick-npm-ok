#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""npm 源测速切换器 2.0

功能
----
* 多包管理器：npm / pnpm / yarn（自动检测是否安装）
* 一键测速 + 综合排名（延迟 70% + 下载 30%）
* 一键切换源，支持"使用最快源"
* 应用内增删改镜像源，配置持久化到 %APPDATA%\\npm-mirror-tool\\sources.json
* 一键升级 npm 到最新版（支持管理员权限重试）
* 双击表格行直接切换

依赖：仅 Python 标准库（tkinter）

直接运行
--------
    python npm_mirror_tool.py
    python npm_mirror_tool.py --selftest [输出.json]

打包 exe
--------
    build.bat
"""

from __future__ import annotations

import http.client
import json
import os
import queue
import shutil
import socket
import ssl
import subprocess
import sys
import tempfile
import threading
import time
import tkinter as tk
from concurrent.futures import ThreadPoolExecutor
from statistics import median
from tkinter import font as tkfont
from tkinter import messagebox, ttk
from urllib.parse import urljoin, urlsplit

APP_NAME = "npm 源测速切换器"
APP_VERSION = "2.0"

MANAGERS = ("npm", "pnpm", "yarn")

DEFAULT_SOURCES = [
    {"name": "华为云", "url": "https://repo.huaweicloud.com/repository/npm/"},
    {"name": "淘宝 npmmirror", "url": "https://registry.npmmirror.com"},
    {"name": "腾讯云", "url": "https://mirrors.cloud.tencent.com/npm/"},
    {"name": "CERNET", "url": "https://mirrors.cernet.edu.cn/npm/"},
    {"name": "npm 官方", "url": "https://registry.npmjs.org"},
    {"name": "Yarn 官方", "url": "https://registry.yarnpkg.com"},
]

OFFICIAL_REGISTRY = "https://registry.npmjs.org/"

LATENCY_PKG = "react"
SPEED_PKG = "webpack"
SPEED_FILE = "webpack-5.97.1.tgz"

RUNS = 3
TIMEOUT = 8.0
SPEED_TIMEOUT = 20.0

LATENCY_WEIGHT = 0.7
SPEED_WEIGHT = 0.3

HEADERS = {
    "Accept": "application/vnd.npm.install-v1+json, application/json, */*",
    "Accept-Encoding": "identity",
    "User-Agent": f"npm-mirror-tool/{APP_VERSION}",
    "Connection": "close",
}

BG = "#f7f9fc"
CARD = "#ffffff"
ACCENT = "#1677ff"
ACCENT_DARK = "#0b5ed7"
TEXT_MUTED = "#6b7280"
BORDER = "#e5e7eb"

CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


# --------------------------------------------------------------------------- #
# 源配置持久化
# --------------------------------------------------------------------------- #

def config_dir() -> str:
    override = os.environ.get("NPM_MIRROR_TOOL_CONFIG_DIR")
    if override:
        base = override
    elif os.name == "nt":
        base = os.path.join(os.environ.get("APPDATA") or os.path.expanduser("~"), "npm-mirror-tool")
    else:
        base = os.path.join(os.path.expanduser("~"), ".config", "npm-mirror-tool")
    try:
        os.makedirs(base, exist_ok=True)
    except OSError:
        pass
    return base


def sources_path() -> str:
    return os.path.join(config_dir(), "sources.json")


def load_sources():
    try:
        with open(sources_path(), encoding="utf-8") as handle:
            payload = json.load(handle)
        items = payload.get("sources", []) if isinstance(payload, dict) else payload
        cleaned = []
        for item in items:
            if not isinstance(item, dict):
                continue
            name = str(item.get("name", "")).strip()
            url = str(item.get("url", "")).strip()
            if name and url.lower().startswith(("http://", "https://")):
                cleaned.append({"name": name, "url": url})
        if cleaned:
            return cleaned
    except (OSError, ValueError):
        pass
    return [dict(item) for item in DEFAULT_SOURCES]


def save_sources(sources):
    try:
        with open(sources_path(), "w", encoding="utf-8") as handle:
            json.dump({"version": 1, "sources": sources}, handle, ensure_ascii=False, indent=2)
        return True, ""
    except OSError as exc:
        return False, str(exc)


# --------------------------------------------------------------------------- #
# 命令执行（npm / pnpm / yarn）
# --------------------------------------------------------------------------- #

def tool_exists(name: str) -> bool:
    return shutil.which(name) is not None


def run_tool(name: str, args, timeout: float = 30):
    path = shutil.which(name)
    if not path:
        raise FileNotFoundError(name)
    if os.name == "nt":
        cmd = ["cmd", "/c", name] + list(args)
        flags = CREATE_NO_WINDOW
    else:
        cmd = [path] + list(args)
        flags = 0
    return subprocess.run(
        cmd, capture_output=True, text=True, timeout=timeout, creationflags=flags,
        encoding="utf-8", errors="replace",
    )


def tool_version(name: str) -> str:
    try:
        proc = run_tool(name, ["-v"], timeout=15)
        text = (proc.stdout or "").strip()
        return text.splitlines()[0] if text else ""
    except Exception:  # noqa: BLE001
        return ""


def get_registry(manager: str) -> str:
    try:
        if manager == "yarn":
            # Yarn 经典版写 .yarnrc，Berry 写 .yarnrc.yml
            proc = run_tool("yarn", ["config", "get", "registry"], timeout=20)
            value = (proc.stdout or "").strip()
            if proc.returncode == 0 and value.lower().startswith("http"):
                return value
            proc = run_tool("yarn", ["config", "get", "npmRegistryServer"], timeout=20)
            value = (proc.stdout or "").strip()
            if proc.returncode == 0 and value.lower().startswith("http"):
                return value
            return ""
        proc = run_tool(manager, ["config", "get", "registry"], timeout=20)
        value = (proc.stdout or "").strip()
        return value if proc.returncode == 0 else ""
    except Exception:  # noqa: BLE001
        return ""


def set_registry(manager: str, url: str):
    try:
        if manager == "yarn":
            proc = run_tool("yarn", ["config", "set", "registry", url], timeout=60)
            if proc.returncode == 0:
                return True, ""
            proc = run_tool("yarn", ["config", "set", "npmRegistryServer", url], timeout=60)
            if proc.returncode == 0:
                return True, ""
            return False, (proc.stderr or proc.stdout or "yarn 执行失败").strip()

        proc = run_tool(manager, ["config", "set", "registry", url], timeout=60)
        if proc.returncode == 0:
            return True, ""
        return False, (proc.stderr or proc.stdout or f"{manager} 执行失败").strip()
    except FileNotFoundError:
        return False, f"未找到 {manager}，请先安装"
    except subprocess.TimeoutExpired:
        return False, "执行超时"
    except Exception as exc:  # noqa: BLE001
        return False, str(exc)


def latest_npm_version() -> str:
    try:
        proc = run_tool("npm", ["view", "npm", "version"], timeout=30)
        text = (proc.stdout or "").strip()
        return text.splitlines()[-1] if text else ""
    except Exception:  # noqa: BLE001
        return ""


# --------------------------------------------------------------------------- #
# 网络探测
# --------------------------------------------------------------------------- #

class ProbeResult:
    __slots__ = ("ok", "status", "ttfb", "total", "size", "error")

    def __init__(self, ok, status=0, ttfb=None, total=0.0, size=0, error=""):
        self.ok = ok
        self.status = status
        self.ttfb = ttfb
        self.total = total
        self.size = size
        self.error = error


def describe_error(exc: BaseException) -> str:
    if isinstance(exc, (TimeoutError, socket.timeout)):
        return "超时"
    if isinstance(exc, socket.gaierror):
        return "DNS 解析失败"
    if isinstance(exc, ssl.SSLError):
        return "TLS 握手失败"
    if isinstance(exc, http.client.RemoteDisconnected):
        return "连接被断开"
    if isinstance(exc, ConnectionResetError):
        return "连接被重置"
    if isinstance(exc, OSError):
        return "连接失败"
    return type(exc).__name__


def join_url(base: str, path: str) -> str:
    return base.rstrip("/") + "/" + path.lstrip("/")


def probe(url: str, timeout: float = TIMEOUT, first_byte_only: bool = False) -> ProbeResult:
    t0 = time.perf_counter()
    ttfb = None
    size = 0
    status = 0
    redirects = 5
    current = url

    try:
        while True:
            parts = urlsplit(current)
            if parts.scheme not in ("http", "https") or not parts.hostname:
                return ProbeResult(False, status, ttfb, time.perf_counter() - t0, size, "地址无效")

            conn_cls = (
                http.client.HTTPSConnection if parts.scheme == "https" else http.client.HTTPConnection
            )
            conn = conn_cls(parts.hostname, parts.port, timeout=timeout)
            try:
                path = parts.path or "/"
                if parts.query:
                    path += "?" + parts.query
                conn.request("GET", path, headers=HEADERS)
                resp = conn.getresponse()
                status = resp.status

                location = resp.getheader("Location")
                if 300 <= status < 400 and location and redirects > 0:
                    redirects -= 1
                    resp.read()
                    current = urljoin(current, location)
                    continue

                if status >= 400:
                    resp.read()
                    return ProbeResult(False, status, ttfb, time.perf_counter() - t0, size, f"HTTP {status}")

                while True:
                    chunk = resp.read(65536)
                    if not chunk:
                        break
                    if ttfb is None:
                        ttfb = time.perf_counter() - t0
                        if first_byte_only:
                            return ProbeResult(True, status, ttfb, time.perf_counter() - t0, len(chunk))
                    size += len(chunk)

                return ProbeResult(True, status, ttfb, time.perf_counter() - t0, size)
            finally:
                conn.close()
    except Exception as exc:  # noqa: BLE001
        return ProbeResult(False, status, ttfb, time.perf_counter() - t0, size, describe_error(exc))


def run_benchmark(sources, runs: int = RUNS, timeout: float = TIMEOUT,
                  speed_timeout: float = SPEED_TIMEOUT, emit=None):
    """测速：并行测延迟，串行测下载。emit(kind, *args) 用于进度回调。"""
    emit = emit or (lambda *args: None)
    pairs = [(item["name"], item["url"]) for item in sources]
    count = len(pairs)
    results = [
        {"ok": False, "lat": None, "speed": None, "error": "", "score": 0.0, "rank": None}
        for _ in range(count)
    ]

    def latency(index: int):
        _, base = pairs[index]
        url = join_url(base, LATENCY_PKG)
        values = []
        error = ""
        for _ in range(runs):
            r = probe(url, timeout=timeout, first_byte_only=True)
            if r.ok and r.ttfb is not None:
                values.append(r.ttfb * 1000)
            else:
                error = r.error or "连接失败"
            emit("step", 1)

        if values:
            results[index]["ok"] = True
            results[index]["lat"] = {"min": min(values), "median": median(values)}
            emit("latency", index, results[index]["lat"], "")
        else:
            results[index]["error"] = error
            emit("latency", index, None, error)

    with ThreadPoolExecutor(max_workers=min(8, count)) as pool:
        list(pool.map(latency, range(count)))

    for index in range(count):
        if not results[index]["ok"]:
            continue
        _, base = pairs[index]
        r = probe(join_url(base, f"{SPEED_PKG}/-/{SPEED_FILE}"), timeout=speed_timeout)
        if r.ok and r.size > 0 and r.total > 0:
            start = r.ttfb if r.ttfb is not None else 0.0
            elapsed = r.total - start
            if elapsed < 0.02:
                elapsed = r.total
            results[index]["speed"] = r.size / 1e6 / max(elapsed, 1e-6)
            emit("speed", index, results[index]["speed"], "")
        else:
            results[index]["error"] = r.error or "下载失败"
            emit("speed", index, None, r.error or "下载失败")
        emit("step", 1)

    return results


def rank_results(results):
    """按 延迟 70% + 下载 30% 计算综合分并排名，返回按名次排列的下标。"""
    valid = [i for i, item in enumerate(results) if item["ok"] and item["lat"]]
    for item in results:
        item["score"] = 0.0
        item["rank"] = None
    if not valid:
        return []

    best_latency = min(results[i]["lat"]["median"] for i in valid)
    best_speed = max((results[i]["speed"] or 0.0) for i in valid)

    for i in valid:
        latency = results[i]["lat"]["median"]
        speed = results[i]["speed"] or 0.0
        latency_score = best_latency / latency if latency > 0 else 0.0
        speed_score = speed / best_speed if best_speed > 0 else 0.0
        results[i]["score"] = LATENCY_WEIGHT * latency_score + SPEED_WEIGHT * speed_score

    order = sorted(valid, key=lambda i: results[i]["score"], reverse=True)
    for position, i in enumerate(order, start=1):
        results[i]["rank"] = position
    return order


def rank_text(rank) -> str:
    if not rank:
        return "—"
    if rank <= 10:
        return chr(0x2460 + rank - 1)
    return str(rank)


# --------------------------------------------------------------------------- #
# 图形界面
# --------------------------------------------------------------------------- #

class App:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.q: queue.Queue = queue.Queue()
        self.sources = load_sources()
        self.data = [self._empty_data() for _ in self.sources]
        self.manager = "npm"
        self.current = ""
        self.current_index = None
        self.loading_registry = False
        self.fastest_index = None
        self.testing = False
        self.switching = False
        self.steps = 0
        self.total_steps = len(self.sources) * (RUNS + 1)
        self.started_at = 0.0
        self.npm_version = ""
        self.source_dialog = None
        self.upgrade_dialog = None

        self._setup_style()
        self._build_ui()
        self._refresh_manager_buttons()
        self._refresh_current()
        self._load_npm_version()
        self.root.after(80, self._poll)

        if not tool_exists("npm"):
            self.status.configure(text="未检测到 npm，请先安装 Node.js（测速仍可使用）")

    @staticmethod
    def _empty_data():
        return {"ok": False, "lat": None, "speed": None, "error": "", "score": 0.0, "rank": None}

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
        style.configure("TLabel", background=BG)
        style.configure("Muted.TLabel", foreground=TEXT_MUTED, background=BG)
        style.configure("Current.TLabel", foreground=ACCENT_DARK, font=(family, 10, "bold"))
        style.configure("Title.TLabel", font=(family, 13, "bold"))
        style.configure("Treeview", font=(family, 10), rowheight=30,
                        background=CARD, fieldbackground=CARD, borderwidth=0)
        style.configure("Treeview.Heading", font=(family, 9, "bold"), padding=(0, 6))
        style.map("Treeview", background=[("selected", "#dbeafe")],
                  foreground=[("selected", "#111827")])

    def _build_ui(self):
        self.root.title(f"{APP_NAME} v{APP_VERSION}")
        self.root.configure(bg=BG)
        self.root.minsize(880, 540)
        self.root.columnconfigure(0, weight=1)
        self.root.rowconfigure(3, weight=1)

        # --- 第一行：包管理器 + npm 版本 ---
        top = ttk.Frame(self.root, padding=(18, 14, 18, 2))
        top.grid(row=0, column=0, sticky="ew")
        top.columnconfigure(2, weight=1)

        ttk.Label(top, text="包管理器", style="Muted.TLabel").grid(row=0, column=0, sticky="w")

        self.manager_buttons = {}
        segment = ttk.Frame(top)
        segment.grid(row=0, column=1, sticky="w", padx=(10, 0))
        for position, name in enumerate(MANAGERS):
            button = tk.Button(
                segment, text=name, command=lambda n=name: self.select_manager(n),
                relief="flat", bd=0, padx=14, pady=4, cursor="hand2",
                font=(self.font_family, 9, "bold"),
                disabledforeground="#a3aab8",
            )
            button.pack(side="left", padx=(0 if position == 0 else 5, 0))
            self.manager_buttons[name] = button

        right = ttk.Frame(top)
        right.grid(row=0, column=3, sticky="e")
        self.npm_version_label = ttk.Label(right, text="npm …", style="Muted.TLabel")
        self.npm_version_label.pack(side="left")
        self.btn_upgrade = ttk.Button(right, text="升级 npm", command=self.open_upgrade)
        self.btn_upgrade.pack(side="left", padx=(10, 0))
        if not tool_exists("npm"):
            self.btn_upgrade.state(["disabled"])

        # --- 第二行：当前源 ---
        line = ttk.Frame(self.root, padding=(18, 8, 18, 4))
        line.grid(row=1, column=0, sticky="ew")
        line.columnconfigure(1, weight=1)
        ttk.Label(line, text="当前源", style="Muted.TLabel").grid(row=0, column=0, sticky="w")
        self.current_label = ttk.Label(line, text="读取中…", style="Current.TLabel")
        self.current_label.grid(row=0, column=1, sticky="w", padx=(10, 0))

        # --- 第三行：标题 + 排名说明 ---
        head = ttk.Frame(self.root, padding=(18, 4, 18, 6))
        head.grid(row=2, column=0, sticky="ew")
        head.columnconfigure(1, weight=1)
        ttk.Label(head, text="镜像源测速", style="Title.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Label(
            head,
            text=f"排名 = 延迟 {int(LATENCY_WEIGHT * 100)}% + 下载 {int(SPEED_WEIGHT * 100)}% · "
                 f"延迟测 {RUNS} 次取最优 · 下载 {SPEED_PKG}",
            style="Muted.TLabel",
        ).grid(row=0, column=1, sticky="w", padx=(12, 0), pady=(4, 0))

        # --- 表格 ---
        table = ttk.Frame(self.root, padding=(18, 0, 18, 0))
        table.grid(row=3, column=0, sticky="nsew")
        table.columnconfigure(0, weight=1)
        table.rowconfigure(0, weight=1)

        columns = ("rank", "name", "url", "latency", "speed", "status")
        self.tree = ttk.Treeview(table, columns=columns, show="headings", selectmode="browse")
        headings = {
            "rank": "排名",
            "name": "源名称",
            "url": "地址",
            "latency": "延迟 ms（快/中位）",
            "speed": "下载 MB/s",
            "status": "状态",
        }
        for key in columns:
            self.tree.heading(key, text=headings[key], anchor="center")
        self.tree.column("rank", width=58, minwidth=50, anchor="center", stretch=False)
        self.tree.column("name", width=130, minwidth=110, anchor="w", stretch=False)
        self.tree.column("url", width=300, minwidth=200, anchor="w")
        self.tree.column("latency", width=140, minwidth=120, anchor="center", stretch=False)
        self.tree.column("speed", width=110, minwidth=95, anchor="center", stretch=False)
        self.tree.column("status", width=120, minwidth=100, anchor="center", stretch=False)

        self.tree.tag_configure("current", background="#eaf3ff")
        self.tree.tag_configure("best", background="#f0fbf3")
        self.tree.grid(row=0, column=0, sticky="nsew")

        scroll = ttk.Scrollbar(table, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=scroll.set)
        scroll.grid(row=0, column=1, sticky="ns")

        self.tree.bind("<<TreeviewSelect>>", self._on_select)
        self.tree.bind("<Double-1>", self._on_double_click)

        # --- 底部操作区 ---
        footer = ttk.Frame(self.root, padding=(18, 12, 18, 14))
        footer.grid(row=4, column=0, sticky="ew")
        footer.columnconfigure(0, weight=1)

        buttons = ttk.Frame(footer)
        buttons.grid(row=0, column=0, sticky="w")

        self.btn_test = tk.Button(
            buttons, text="开始测速", command=self.start_test,
            bg=ACCENT, fg="white", activebackground=ACCENT_DARK, activeforeground="white",
            relief="flat", bd=0, padx=20, pady=7, cursor="hand2",
            font=(self.font_family, 10, "bold"), disabledforeground="#dbeafe",
        )
        self.btn_test.pack(side="left")
        self.btn_test.bind("<Enter>", lambda e: self.btn_test.config(bg=ACCENT_DARK))
        self.btn_test.bind("<Leave>", lambda e: self.btn_test.config(bg=ACCENT))

        self.btn_fastest = ttk.Button(buttons, text="使用最快源", command=self.use_fastest)
        self.btn_fastest.pack(side="left", padx=(10, 0))
        self.btn_fastest.state(["disabled"])

        self.btn_switch = ttk.Button(buttons, text="切换到选中源", command=self.switch_selected)
        self.btn_switch.pack(side="left", padx=(10, 0))
        self.btn_switch.state(["disabled"])

        self.btn_sources = ttk.Button(buttons, text="管理源", command=self.open_sources)
        self.btn_sources.pack(side="left", padx=(10, 0))

        self.progress = tk.Canvas(footer, width=180, height=8, bg=BG, highlightthickness=0)
        self.progress.create_line(4, 4, 176, 4, width=5, capstyle="round", fill="#e2e8f0")
        self.progress_fill = self.progress.create_line(4, 4, 4, 4, width=5, capstyle="round", fill=ACCENT)
        self.progress.grid(row=0, column=1, sticky="e", padx=(12, 0))
        self.progress.grid_remove()

        self.status = ttk.Label(
            footer, text="选择一行后点击「切换」，或直接双击该行", style="Muted.TLabel"
        )
        self.status.grid(row=1, column=0, columnspan=2, sticky="w", pady=(10, 0))

        self._rebuild_rows()
        self._center_window(940, 600)

    def _center_window(self, width: int, height: int):
        self.root.update_idletasks()
        screen_w = self.root.winfo_screenwidth()
        screen_h = self.root.winfo_screenheight()
        x = max(0, (screen_w - width) // 2)
        y = max(0, (screen_h - height) // 3)
        self.root.geometry(f"{width}x{height}+{x}+{y}")

    # ---------------- 包管理器 ---------------- #

    def _refresh_manager_buttons(self):
        busy = self.testing or self.switching
        for name, button in self.manager_buttons.items():
            installed = tool_exists(name)
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

    def select_manager(self, name: str):
        if self.testing or self.switching or name == self.manager:
            return
        if not tool_exists(name):
            return
        self.manager = name
        self._refresh_manager_buttons()
        self.current = ""
        self.current_index = None
        self.loading_registry = True
        self.current_label.configure(text="读取中…")
        self._clear_current_tags()
        threading.Thread(target=self._read_registry_worker, args=(name,), daemon=True).start()

    def _read_registry_worker(self, manager: str):
        self.q.put(("registry", manager, get_registry(manager)))

    def _load_npm_version(self):
        def worker():
            self.q.put(("npm_version", tool_version("npm")))

        threading.Thread(target=worker, daemon=True).start()

    # ---------------- 当前源 ---------------- #

    def _refresh_current(self):
        if not self.current:
            self.loading_registry = True
            self.current_label.configure(text="读取中…")
            threading.Thread(target=self._read_registry_worker, args=(self.manager,), daemon=True).start()
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
            self.current_label.configure(text=f"{self.current}（自定义）")
        elif self.loading_registry:
            self.current_label.configure(text="读取中…")
        else:
            self.current_label.configure(text="未读取到配置")

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

    # ---------------- 表格 ---------------- #

    def _rebuild_rows(self):
        for item in self.tree.get_children():
            self.tree.delete(item)
        self.data = [self._empty_data() for _ in self.sources]
        for index, source in enumerate(self.sources):
            self.tree.insert(
                "", "end", iid=str(index),
                values=("—", source["name"], source["url"], "—", "—", ""),
                tags=self._tags_for(index),
            )
        self._update_actions()

    def _render_row(self, index: int):
        item = str(index)
        if not self.tree.exists(item):
            return
        data = self.data[index]
        source = self.sources[index]

        latency = f'{data["lat"]["min"]:.0f} / {data["lat"]["median"]:.0f}' if data["lat"] else "—"
        speed = f'{data["speed"]:.1f}' if data["speed"] is not None else "—"

        if data["ok"]:
            status = "✓"
        elif data["error"]:
            status = f'✗ {data["error"]}'
        else:
            status = ""

        self.tree.item(
            item,
            values=(rank_text(data.get("rank")), source["name"], source["url"], latency, speed, status),
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
        idle = not self.testing and not self.switching
        self.btn_switch.state(["!disabled"] if idle and self.tree.selection() else ["disabled"])
        self.btn_fastest.state(
            ["!disabled"] if idle and self.fastest_index is not None else ["disabled"]
        )
        self.btn_sources.state(["!disabled"] if idle else ["disabled"])

    def _set_busy(self, busy: bool, switching: bool = False):
        self.testing = busy and not switching
        self.switching = busy and switching
        self.btn_test.config(state="disabled" if busy else "normal")
        if not tool_exists("npm"):
            self.btn_upgrade.state(["disabled"])
        else:
            self.btn_upgrade.state(["disabled"] if busy else ["!disabled"])
        self._refresh_manager_buttons()
        self._update_actions()

    def _on_select(self, _event=None):
        self._update_actions()
        index = self._selected_index()
        if index is None or self.testing:
            return
        data = self.data[index]
        source = self.sources[index]
        extra = ""
        if data.get("rank"):
            extra = f" · 排名 {data['rank']} · 综合 {data['score'] * 100:.0f} 分"
        self.status.configure(text=f'{source["name"]}  {source["url"]}{extra}')

    def _on_double_click(self, event):
        row = self.tree.identify_row(event.y)
        if not row or self.testing or self.switching:
            return
        self.tree.selection_set(row)
        self.switch_selected()

    # ---------------- 测速 ---------------- #

    def start_test(self):
        if self.testing or self.switching:
            return

        self.data = [self._empty_data() for _ in self.sources]
        self.fastest_index = None
        self.btn_fastest.config(text="使用最快源")
        self._render_all()
        self.tree.selection_remove(self.tree.selection())
        self.steps = 0
        self.total_steps = len(self.sources) * (RUNS + 1)
        self._progress_set(0)
        self.progress.grid()
        self.status.configure(text="正在测速…")
        self.started_at = time.time()
        self._set_busy(True)
        threading.Thread(target=self._bench_worker, daemon=True).start()

    def _bench_worker(self):
        try:
            results = run_benchmark(self.sources, emit=lambda *args: self.q.put(args))
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

                elif kind == "step":
                    self.steps += 1
                    self._progress_set(self.steps / max(1, self.total_steps))
                    self.status.configure(
                        text=f'正在测速… {min(self.steps, self.total_steps)}/{self.total_steps}'
                    )

                elif kind == "done":
                    self._on_done(rest[0])

                elif kind == "error":
                    self._finish_busy()
                    self.progress.grid_remove()
                    self.status.configure(text="测速失败")
                    messagebox.showerror(APP_NAME, f"测速过程中出错：\n{rest[0]}")

                elif kind == "registry":
                    manager, value = rest
                    if manager == self.manager:
                        self.loading_registry = False
                        self.current = value
                        self._apply_current()

                elif kind == "npm_version":
                    self.npm_version = rest[0]
                    self._apply_npm_version()

                elif kind == "switched":
                    manager, name, url, ok, error = rest
                    self._on_switched(manager, name, url, ok, error)
        except queue.Empty:
            pass

        self.root.after(80, self._poll)

    def _apply_npm_version(self):
        if self.npm_version:
            self.npm_version_label.configure(text=f"npm v{self.npm_version}")
        else:
            self.npm_version_label.configure(text="npm 未检测到")

    def _on_done(self, results):
        self.data = results
        self._render_all()
        self._finish_busy()
        self.progress.grid_remove()

        order = rank_results(self.data)
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

        elapsed = time.time() - self.started_at
        ok_count = sum(1 for item in self.data if item["ok"])
        if self.fastest_index is not None:
            fastest = self.sources[self.fastest_index]
            self.btn_fastest.config(text=f"使用最快源 · {fastest['name']}")
            self.status.configure(
                text=f'测速完成：{ok_count}/{len(self.data)} 个源可用，最快 {fastest["name"]} · {elapsed:.1f}s'
            )
        else:
            self.btn_fastest.config(text="使用最快源")
            self.status.configure(text=f"测速完成：没有可用源 · {elapsed:.1f}s")

    def _finish_busy(self):
        self._set_busy(False)

    def _progress_set(self, ratio: float):
        ratio = max(0.0, min(1.0, ratio))
        self.progress.coords(self.progress_fill, 4, 4, 4 + 172 * ratio, 4)

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
        if self.testing or self.switching:
            return
        source = self.sources[index]
        manager = self.manager
        self._set_busy(True, switching=True)
        self.status.configure(text=f'正在将 {manager} 切换到 {source["name"]}…')

        def worker():
            ok, error = set_registry(manager, source["url"])
            self.q.put(("switched", manager, source["name"], source["url"], ok, error))

        threading.Thread(target=worker, daemon=True).start()

    def _on_switched(self, manager, name, url, ok, error):
        self._set_busy(False)
        if not ok:
            self.status.configure(text="切换失败")
            messagebox.showerror(APP_NAME, f"{manager} 切换失败：\n{error}")
            return

        self.current = url
        self._apply_current()
        self.status.configure(text=f"已将 {manager} 切换到 {name}")
        messagebox.showinfo(
            APP_NAME,
            f"已将 {manager} 切换到 {name}\n{url}\n\n配置已写入用户级配置文件。",
        )

    # ---------------- 源管理 ---------------- #

    def open_sources(self):
        if self.testing or self.switching:
            return
        if self.source_dialog is not None and self.source_dialog.winfo_exists():
            self.source_dialog.lift()
            self.source_dialog.focus_force()
            return
        self.source_dialog = SourceDialog(self)

    def update_sources(self, sources):
        self.sources = [dict(item) for item in sources]
        ok, error = save_sources(self.sources)
        if not ok:
            messagebox.showerror(APP_NAME, f"保存源配置失败：\n{error}\n\n本次修改仅在当前运行期间有效。")
        self.fastest_index = None
        self.btn_fastest.config(text="使用最快源")
        self._rebuild_rows()
        self.current = ""
        self._refresh_current()
        self.status.configure(text="源列表已更新，重新测速即可查看排名")

    # ---------------- 升级 npm ---------------- #

    def open_upgrade(self):
        if self.testing or self.switching:
            return
        if self.upgrade_dialog is not None and self.upgrade_dialog.winfo_exists():
            self.upgrade_dialog.lift()
            self.upgrade_dialog.focus_force()
            return
        self.upgrade_dialog = UpgradeDialog(self)


# --------------------------------------------------------------------------- #
# 源管理窗口
# --------------------------------------------------------------------------- #

class SourceDialog(tk.Toplevel):
    def __init__(self, app: App):
        super().__init__(app.root)
        self.app = app
        self.sources = [dict(item) for item in app.sources]
        self.title("管理镜像源")
        self.configure(bg=BG)
        self.transient(app.root)
        self.grab_set()
        self.resizable(False, False)

        wrapper = ttk.Frame(self, padding=(16, 14))
        wrapper.pack(fill="both", expand=True)

        ttk.Label(wrapper, text="镜像源列表", style="Title.TLabel").pack(anchor="w")
        ttk.Label(
            wrapper, text="双击可在下方修改；所有修改立即保存", style="Muted.TLabel"
        ).pack(anchor="w", pady=(2, 8))

        table = ttk.Frame(wrapper)
        table.pack(fill="both", expand=True)

        self.tree = ttk.Treeview(table, columns=("name", "url"), show="headings", height=8)
        self.tree.heading("name", text="名称", anchor="center")
        self.tree.heading("url", text="地址", anchor="center")
        self.tree.column("name", width=150, anchor="w", stretch=False)
        self.tree.column("url", width=430, anchor="w")
        self.tree.pack(side="left", fill="both", expand=True)

        scroll = ttk.Scrollbar(table, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=scroll.set)
        scroll.pack(side="right", fill="y")

        form = ttk.Frame(wrapper)
        form.pack(fill="x", pady=(12, 0))
        form.columnconfigure(1, weight=1)

        ttk.Label(form, text="名称", style="Muted.TLabel").grid(row=0, column=0, sticky="w")
        self.name_var = tk.StringVar()
        ttk.Entry(form, textvariable=self.name_var).grid(row=0, column=1, sticky="ew", padx=(8, 0))

        ttk.Label(form, text="地址", style="Muted.TLabel").grid(
            row=1, column=0, sticky="w", pady=(6, 0)
        )
        self.url_var = tk.StringVar()
        ttk.Entry(form, textvariable=self.url_var).grid(
            row=1, column=1, sticky="ew", padx=(8, 0), pady=(6, 0)
        )

        buttons = ttk.Frame(wrapper)
        buttons.pack(fill="x", pady=(12, 0))
        ttk.Button(buttons, text="添加", command=self._add).pack(side="left")
        ttk.Button(buttons, text="保存修改", command=self._update).pack(side="left", padx=(8, 0))
        ttk.Button(buttons, text="删除", command=self._delete).pack(side="left", padx=(8, 0))
        ttk.Button(buttons, text="恢复默认", command=self._restore).pack(side="left", padx=(8, 0))
        ttk.Button(buttons, text="关闭", command=self.destroy).pack(side="right")

        self.tree.bind("<<TreeviewSelect>>", self._on_select)
        self.tree.bind("<Double-1>", lambda _e: self._on_select())
        self._fill()

        self.update_idletasks()
        width, height = 660, 480
        x = app.root.winfo_rootx() + (app.root.winfo_width() - width) // 2
        y = app.root.winfo_rooty() + (app.root.winfo_height() - height) // 2
        self.geometry(f"{width}x{height}+{max(0, x)}+{max(0, y)}")

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
            messagebox.showerror("输入有误", "名称不能为空", parent=self)
            return None
        if not url.lower().startswith(("http://", "https://")):
            messagebox.showerror("输入有误", "地址必须以 http:// 或 https:// 开头", parent=self)
            return None
        if not urlsplit(url).hostname:
            messagebox.showerror("输入有误", "地址格式不正确", parent=self)
            return None
        return name, url

    def _duplicate(self, url, ignore_index=None):
        for index, source in enumerate(self.sources):
            if index != ignore_index and normalize(source["url"]) == normalize(url):
                return True
        return False

    def _commit(self, message: str):
        self._fill()
        self.app.update_sources(self.sources)
        self.app.status.configure(text=message)

    def _add(self):
        cleaned = self._clean_input()
        if not cleaned:
            return
        name, url = cleaned
        if self._duplicate(url):
            messagebox.showerror("输入有误", "该地址已存在", parent=self)
            return
        self.sources.append({"name": name, "url": url})
        self._commit(f"已添加源：{name}")

    def _update(self):
        selection = self.tree.selection()
        if not selection:
            messagebox.showinfo("提示", "请先在列表中选择要修改的源", parent=self)
            return
        index = int(selection[0])
        cleaned = self._clean_input()
        if not cleaned:
            return
        name, url = cleaned
        if self._duplicate(url, ignore_index=index):
            messagebox.showerror("输入有误", "该地址已存在", parent=self)
            return
        self.sources[index] = {"name": name, "url": url}
        self._commit(f"已修改源：{name}")

    def _delete(self):
        selection = self.tree.selection()
        if not selection:
            messagebox.showinfo("提示", "请先在列表中选择要删除的源", parent=self)
            return
        index = int(selection[0])
        name = self.sources[index]["name"]
        if not messagebox.askyesno("删除确认", f"确定删除「{name}」吗？", parent=self):
            return
        del self.sources[index]
        self.name_var.set("")
        self.url_var.set("")
        self._commit(f"已删除源：{name}")

    def _restore(self):
        if not messagebox.askyesno(
            "恢复默认", "将丢弃所有自定义源并恢复内置列表，确定继续吗？", parent=self
        ):
            return
        self.sources = [dict(item) for item in DEFAULT_SOURCES]
        self._commit("已恢复默认源列表")


# --------------------------------------------------------------------------- #
# 升级 npm 窗口
# --------------------------------------------------------------------------- #

class UpgradeDialog(tk.Toplevel):
    def __init__(self, app: App):
        super().__init__(app.root)
        self.app = app
        self.q: queue.Queue = queue.Queue()
        self.process = None
        self.running = False
        self.latest = ""

        self.title("升级 npm")
        self.configure(bg=BG)
        self.transient(app.root)
        self.resizable(False, False)
        self.protocol("WM_DELETE_WINDOW", self._on_close)

        wrapper = ttk.Frame(self, padding=(16, 14))
        wrapper.pack(fill="both", expand=True)

        ttk.Label(wrapper, text="升级 npm", style="Title.TLabel").pack(anchor="w")
        ttk.Label(
            wrapper,
            text="从当前 registry 下载最新版 npm 并全局安装；失败时可用管理员权限重试",
            style="Muted.TLabel",
        ).pack(anchor="w", pady=(2, 10))

        info = ttk.Frame(wrapper)
        info.pack(fill="x")
        info.columnconfigure(1, weight=1)

        ttk.Label(info, text="当前版本", style="Muted.TLabel").grid(row=0, column=0, sticky="w")
        self.current_label = ttk.Label(info, text="检测中…", style="Current.TLabel")
        self.current_label.grid(row=0, column=1, sticky="w", padx=(10, 0))

        ttk.Label(info, text="最新版本", style="Muted.TLabel").grid(
            row=1, column=0, sticky="w", pady=(4, 0)
        )
        self.latest_label = ttk.Label(info, text="查询中…", style="Current.TLabel")
        self.latest_label.grid(row=1, column=1, sticky="w", padx=(10, 0), pady=(4, 0))

        actions = ttk.Frame(wrapper)
        actions.pack(fill="x", pady=(12, 8))

        self.btn_install = tk.Button(
            actions, text="升级到最新版", command=self._install,
            bg=ACCENT, fg="white", activebackground=ACCENT_DARK, activeforeground="white",
            relief="flat", bd=0, padx=18, pady=6, cursor="hand2",
            font=(app.font_family, 10, "bold"), disabledforeground="#dbeafe",
        )
        self.btn_install.pack(side="left")

        self.btn_elevate = ttk.Button(actions, text="以管理员身份升级", command=self._elevate)
        self.btn_elevate.pack(side="left", padx=(8, 0))

        self.btn_recheck = ttk.Button(actions, text="重新检测版本", command=self._refresh_versions)
        self.btn_recheck.pack(side="left", padx=(8, 0))

        ttk.Button(actions, text="关闭", command=self._on_close).pack(side="right")

        log_frame = ttk.Frame(wrapper)
        log_frame.pack(fill="both", expand=True)

        self.log = tk.Text(
            log_frame, height=13, wrap="none", bg="#0f172a", fg="#e2e8f0",
            insertbackground="#e2e8f0", relief="flat", padx=10, pady=8,
            font=("Consolas", 9), state="disabled",
        )
        self.log.pack(side="left", fill="both", expand=True)

        scroll = ttk.Scrollbar(log_frame, orient="vertical", command=self.log.yview)
        self.log.configure(yscrollcommand=scroll.set)
        scroll.pack(side="right", fill="y")

        self.status = ttk.Label(wrapper, text="", style="Muted.TLabel")
        self.status.pack(anchor="w", pady=(8, 0))

        self._refresh_versions()
        self._append("等待操作…")

        self.update_idletasks()
        width, height = 620, 500
        x = app.root.winfo_rootx() + (app.root.winfo_width() - width) // 2
        y = app.root.winfo_rooty() + (app.root.winfo_height() - height) // 2
        self.geometry(f"{width}x{height}+{max(0, x)}+{max(0, y)}")

        self.after(100, self._poll)

    # ---------------- 版本查询 ---------------- #

    def _refresh_versions(self):
        self.current_label.configure(text="检测中…")
        self.latest_label.configure(text="查询中…")

        def worker():
            current = tool_version("npm")
            latest = latest_npm_version()
            self.q.put(("versions", current, latest))

        threading.Thread(target=worker, daemon=True).start()

    def _apply_versions(self, current, latest):
        if current:
            self.current_label.configure(text=f"v{current}")
        else:
            self.current_label.configure(text="未检测到 npm")

        if latest:
            self.latest = latest
            if current and latest == current:
                self.latest_label.configure(text=f"v{latest}（已是最新）")
            else:
                self.latest_label.configure(text=f"v{latest}")
        else:
            self.latest_label.configure(text="查询失败（检查网络或 registry）")

        self.app.npm_version = current
        self.app._apply_npm_version()

    # ---------------- 安装 ---------------- #

    def _install(self):
        if self.running:
            return
        if not tool_exists("npm"):
            messagebox.showerror(APP_NAME, "未检测到 npm，请先安装 Node.js", parent=self)
            return
        if not messagebox.askyesno(
            "升级 npm", "将执行：\nnpm install -g npm@latest\n\n继续吗？", parent=self
        ):
            return

        self.running = True
        self.btn_install.config(state="disabled", bg="#9ec5ff")
        self.btn_elevate.state(["disabled"])
        self.btn_recheck.state(["disabled"])
        self.status.configure(text="正在升级，请稍候…")
        self._append("")
        self._append("$ npm install -g npm@latest")

        if os.name == "nt":
            cmd = ["cmd", "/c", "npm", "install", "-g", "npm@latest"]
            flags = CREATE_NO_WINDOW
        else:
            cmd = ["npm", "install", "-g", "npm@latest"]
            flags = 0

        try:
            self.process = subprocess.Popen(
                cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, encoding="utf-8", errors="replace", bufsize=1,
                creationflags=flags,
            )
        except Exception as exc:  # noqa: BLE001
            self._finish(1, f"启动失败：{exc}")
            return

        threading.Thread(target=self._reader, daemon=True).start()

    def _reader(self):
        try:
            for line in self.process.stdout:
                self.q.put(("log", line.rstrip()))
            code = self.process.wait()
        except Exception as exc:  # noqa: BLE001
            self.q.put(("log", f"读取输出失败：{exc}"))
            code = 1
        self.q.put(("installed", code))

    def _finish(self, code, note=""):
        self.running = False
        self.btn_install.config(state="normal", bg=ACCENT)
        self.btn_elevate.state(["!disabled"])
        self.btn_recheck.state(["!disabled"])

        if code == 0:
            self.status.configure(text="升级完成")
            self._append("")
            self._append("✓ 升级完成，正在重新检测版本…")
            self._refresh_versions()
            self.app._load_npm_version()
        else:
            self.status.configure(text="升级失败")
            self._append("")
            if note:
                self._append(note)
            self._append("✗ 升级失败。若提示权限不足（EPERM/EACCES），请点击「以管理员身份升级」。")

    def _elevate(self):
        if self.running:
            return
        if os.name != "nt":
            messagebox.showinfo(APP_NAME, "该功能仅支持 Windows", parent=self)
            return
        try:
            import ctypes

            params = (
                '/c npm install -g npm@latest & echo. & '
                'echo 安装结束，请回到工具点「重新检测版本」。 & pause'
            )
            result = ctypes.windll.shell32.ShellExecuteW(None, "runas", "cmd.exe", params, None, 1)
            if result <= 32:
                raise OSError(f"ShellExecuteW 返回 {result}")
        except Exception as exc:  # noqa: BLE001
            messagebox.showerror(APP_NAME, f"无法启动管理员进程：\n{exc}", parent=self)
            return

        self._append("")
        self._append("已打开管理员命令行，请在其中完成安装，然后点击「重新检测版本」。")
        self.status.configure(text="请在管理员窗口中完成升级")

    # ---------------- 轮询 ---------------- #

    def _append(self, text: str):
        self.log.configure(state="normal")
        self.log.insert("end", text + "\n")
        self.log.see("end")
        self.log.configure(state="disabled")

    def _poll(self):
        try:
            while True:
                message = self.q.get_nowait()
                kind, *rest = message
                if kind == "log":
                    self._append(rest[0])
                elif kind == "installed":
                    self._finish(rest[0])
                elif kind == "versions":
                    self._apply_versions(rest[0], rest[1])
        except queue.Empty:
            pass
        if self.winfo_exists():
            self.after(100, self._poll)

    def _on_close(self):
        if self.running:
            messagebox.showinfo(APP_NAME, "升级正在进行，请等待完成后再关闭。", parent=self)
            return
        self.destroy()
        self.app.upgrade_dialog = None


# --------------------------------------------------------------------------- #
# 自检（无界面）
# --------------------------------------------------------------------------- #

def selftest(argv):
    out_path = None
    if "--selftest" in argv:
        pos = argv.index("--selftest")
        if pos + 1 < len(argv) and not argv[pos + 1].startswith("--"):
            out_path = argv[pos + 1]
    if not out_path:
        out_path = os.path.join(tempfile.gettempdir(), "npm-mirror-selftest.json")

    sources = load_sources()
    started = time.time()
    results = run_benchmark(sources)
    elapsed = time.time() - started
    order = rank_results(results)

    payload = {
        "version": APP_VERSION,
        "elapsed_seconds": round(elapsed, 1),
        "registry": {name: get_registry(name) for name in MANAGERS if tool_exists(name)},
        "sources_file": sources_path(),
        "results": [
            {
                "rank": item["rank"],
                "score": round(item["score"], 3),
                "name": source["name"],
                "url": source["url"],
                "ok": item["ok"],
                "latency_ms": None if item["lat"] is None else {
                    "min": round(item["lat"]["min"], 1),
                    "median": round(item["lat"]["median"], 1),
                },
                "download_mbps": None if item["speed"] is None else round(item["speed"], 2),
                "error": item["error"],
            }
            for source, item in zip(sources, results)
        ],
    }

    with open(out_path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)

    print(f"自检完成，用时 {elapsed:.1f}s -> {out_path}")
    for entry in payload["results"]:
        lat = entry["latency_ms"]
        latency = f'{lat["min"]:.0f}/{lat["median"]:.0f}ms' if lat else "失败"
        speed = f'{entry["download_mbps"]:.1f}MB/s' if entry["download_mbps"] else "—"
        rank = rank_text(entry["rank"])
        print(f'  {rank}  {entry["name"]:<14} {latency:<14} {speed:<12} {entry["error"]}')


def resource_path(name: str) -> str:
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, name)


def enable_dpi_awareness():
    if os.name != "nt":
        return
    try:
        import ctypes

        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:  # noqa: BLE001
        pass


def normalize(url: str) -> str:
    return (url or "").strip().lower().rstrip("/")


def main():
    if "--selftest" in sys.argv:
        selftest(sys.argv)
        return

    enable_dpi_awareness()
    root = tk.Tk()
    try:
        root.iconbitmap(resource_path("app.ico"))
    except Exception:  # noqa: BLE001
        pass

    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
