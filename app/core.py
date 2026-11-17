# -*- coding: utf-8 -*-
"""核心逻辑：配置、代理、测速、排名、包管理器操作。

只依赖 Python 标准库，界面层不直接碰这些细节。
"""

from __future__ import annotations

import base64
import csv
import datetime as _dt
import http.client
import json
import os
import re
import shutil
import socket
import ssl
import subprocess
import threading
import time
from statistics import median
from urllib.parse import urljoin, urlsplit

from . import APP_VERSION

# --------------------------------------------------------------------------- #
# 常量
# --------------------------------------------------------------------------- #

MANAGERS = ("npm", "pnpm", "yarn")
PIP_MANAGERS = ("pip",)

ECOSYSTEMS = ("npm", "pip")
ECOSYSTEM_MANAGERS = {"npm": MANAGERS, "pip": PIP_MANAGERS}

DEFAULT_SOURCES = [
    {"name": "华为云", "url": "https://repo.huaweicloud.com/repository/npm/"},
    {"name": "淘宝 npmmirror", "url": "https://registry.npmmirror.com"},
    {"name": "腾讯云", "url": "https://mirrors.cloud.tencent.com/npm/"},
    {"name": "CERNET", "url": "https://mirrors.cernet.edu.cn/npm/"},
    {"name": "npm 官方", "url": "https://registry.npmjs.org"},
    {"name": "Yarn 官方", "url": "https://registry.yarnpkg.com"},
]

OFFICIAL_REGISTRY = "https://registry.npmjs.org/"

DEFAULT_PIP_SOURCES = [
    {"name": "清华 TUNA", "url": "https://pypi.tuna.tsinghua.edu.cn/simple"},
    {"name": "阿里云", "url": "https://mirrors.aliyun.com/pypi/simple"},
    {"name": "腾讯云", "url": "https://mirrors.cloud.tencent.com/pypi/simple"},
    {"name": "华为云", "url": "https://repo.huaweicloud.com/repository/pypi/simple"},
    {"name": "中科大", "url": "https://mirrors.ustc.edu.cn/pypi/simple"},
    {"name": "南京大学", "url": "https://mirror.nju.edu.cn/pypi/web/simple"},
    {"name": "上海交大", "url": "https://mirror.sjtu.edu.cn/pypi/web/simple"},
    {"name": "PyPI 官方", "url": "https://pypi.org/simple"},
]

OFFICIAL_PIP_INDEX = "https://pypi.org/simple/"
PIP_PROBE_PKG = "pip"
PIP_INI_NAME = "pip.ini" if os.name == "nt" else "pip.conf"

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

CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

DEFAULTS = {
    "language": "zh",
    "ecosystem": "npm",
    "proxy": {"mode": "auto", "url": ""},
    "scope": {"mode": "user", "dir": ""},
    "pip_scope": {"mode": "user", "dir": ""},
    "tray": True,
    "autostart": False,
    "update_check": True,
    "sources": [],
    "pip_sources": [],
}

# --------------------------------------------------------------------------- #
# 配置
# --------------------------------------------------------------------------- #

_config = None


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


def data_path(name: str) -> str:
    return os.path.join(config_dir(), name)


CONFIG_FILE = "config.json"
HISTORY_FILE = "history.jsonl"
LEGACY_SOURCES = "sources.json"


def _load_legacy_sources():
    try:
        with open(data_path(LEGACY_SOURCES), encoding="utf-8") as handle:
            payload = json.load(handle)
    except (OSError, ValueError):
        return None
    items = payload.get("sources", []) if isinstance(payload, dict) else payload
    cleaned = []
    for item in items:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name", "")).strip()
        url = str(item.get("url", "")).strip()
        if name and url.lower().startswith(("http://", "https://")):
            cleaned.append({"name": name, "url": url})
    return cleaned or None


def config() -> dict:
    global _config
    if _config is not None:
        return _config

    data = {}
    try:
        with open(data_path(CONFIG_FILE), encoding="utf-8") as handle:
            loaded = json.load(handle)
        if isinstance(loaded, dict):
            data = loaded
    except (OSError, ValueError):
        pass

    merged = dict(DEFAULTS)
    for key, value in data.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = {**merged[key], **value}
        else:
            merged[key] = value

    if not merged.get("sources"):
        merged["sources"] = _load_legacy_sources() or [dict(item) for item in DEFAULT_SOURCES]

    merged["sources"] = _clean_sources(merged["sources"], DEFAULT_SOURCES)
    merged["pip_sources"] = _clean_sources(merged["pip_sources"], DEFAULT_PIP_SOURCES)
    if merged.get("ecosystem") not in ECOSYSTEMS:
        merged["ecosystem"] = "npm"

    _config = merged
    return _config


def _clean_sources(items, fallback) -> list:
    cleaned = []
    for item in items or []:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name", "")).strip()
        url = str(item.get("url", "")).strip().rstrip("/")
        if name and url.lower().startswith(("http://", "https://")):
            cleaned.append({"name": name, "url": url})
    return cleaned or [dict(item) for item in fallback]


def save_config() -> tuple[bool, str]:
    try:
        with open(data_path(CONFIG_FILE), "w", encoding="utf-8") as handle:
            json.dump(config(), handle, ensure_ascii=False, indent=2)
        return True, ""
    except OSError as exc:
        return False, str(exc)


def get(key, default=None):
    return config().get(key, DEFAULTS.get(key, default))


def set_value(key, value) -> tuple[bool, str]:
    config()[key] = value
    return save_config()


def sources(eco=None) -> list:
    eco = eco if eco in ECOSYSTEMS else ecosystem()
    return config()["pip_sources" if eco == "pip" else "sources"]


def set_sources(items, eco=None) -> tuple[bool, str]:
    eco = eco if eco in ECOSYSTEMS else ecosystem()
    cleaned = []
    for item in items:
        name = str(item.get("name", "")).strip()
        url = str(item.get("url", "")).strip().rstrip("/")
        if name and url.lower().startswith(("http://", "https://")):
            cleaned.append({"name": name, "url": url})
    config()["pip_sources" if eco == "pip" else "sources"] = cleaned
    return save_config()


def default_sources(eco=None) -> list:
    eco = eco if eco in ECOSYSTEMS else ecosystem()
    base = DEFAULT_PIP_SOURCES if eco == "pip" else DEFAULT_SOURCES
    return [dict(item) for item in base]


def managers(eco=None) -> tuple:
    eco = eco if eco in ECOSYSTEMS else ecosystem()
    return ECOSYSTEM_MANAGERS.get(eco, MANAGERS)


def ecosystem() -> str:
    value = get("ecosystem")
    return value if value in ECOSYSTEMS else "npm"


def set_ecosystem(name: str) -> tuple[bool, str]:
    if name not in ECOSYSTEMS:
        return False, "unknown ecosystem"
    config()["ecosystem"] = name
    return save_config()


def scope(eco=None) -> dict:
    """当前生态的作用域设置（npm: user/project；pip: user/venv）。"""
    eco = eco if eco in ECOSYSTEMS else ecosystem()
    key = "pip_scope" if eco == "pip" else "scope"
    value = dict(config().get(key) or {})
    if value.get("mode") == "project" and value.get("dir"):
        return {"mode": "project", "dir": str(value["dir"])}
    return {"mode": "user", "dir": ""}


def set_scope(value: dict, eco=None) -> tuple[bool, str]:
    eco = eco if eco in ECOSYSTEMS else ecosystem()
    key = "pip_scope" if eco == "pip" else "scope"
    config()[key] = dict(value)
    return save_config()


def language() -> str:
    return "en" if get("language") == "en" else "zh"


# --------------------------------------------------------------------------- #
# 历史记录 / 导出
# --------------------------------------------------------------------------- #

def append_history(record: dict):
    record = dict(record)
    record.setdefault("time", _dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    try:
        with open(data_path(HISTORY_FILE), "a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
    except OSError:
        pass


def load_history(limit: int = 200) -> list:
    rows = []
    try:
        with open(data_path(HISTORY_FILE), encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    rows.append(json.loads(line))
                except ValueError:
                    continue
    except OSError:
        return []
    return rows[-limit:][::-1]


def export_csv(path: str, rows: list) -> int:
    """把测速结果写为 CSV，返回写入行数。"""
    fields = ["time", "name", "url", "latency_min_ms", "latency_median_ms",
              "download_mbps", "version", "rank", "score", "ok", "error"]
    with open(path, "w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow({key: row.get(key, "") for key in fields})
    return len(rows)


def results_to_rows(results, src_list, stamp=None) -> list:
    stamp = stamp or _dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    rows = []
    for source, item in zip(src_list, results):
        lat = item.get("lat") or {}
        rows.append({
            "time": stamp,
            "name": source["name"],
            "url": source["url"],
            "latency_min_ms": round(lat.get("min", 0), 1) if lat else "",
            "latency_median_ms": round(lat.get("median", 0), 1) if lat else "",
            "download_mbps": round(item["speed"], 2) if item.get("speed") is not None else "",
            "version": item.get("version", ""),
            "rank": item.get("rank") or "",
            "score": round(item.get("score", 0), 3),
            "ok": "1" if item.get("ok") else "0",
            "error": item.get("error", ""),
        })
    return rows


# --------------------------------------------------------------------------- #
# 代理
# --------------------------------------------------------------------------- #

class Proxy:
    __slots__ = ("host", "port", "bypass_list", "auth")

    def __init__(self, host, port, bypass_list=None, auth=None):
        self.host = host
        self.port = int(port or 0)
        self.bypass_list = [item.strip().lower() for item in (bypass_list or []) if item.strip()]
        self.auth = auth

    @property
    def enabled(self) -> bool:
        return bool(self.host and self.port)

    def bypass(self, host: str) -> bool:
        host = (host or "").lower()
        for entry in self.bypass_list:
            if entry == "<local>":
                if "." not in host:
                    return True
                continue
            entry = entry.split(":")[0]
            if entry.startswith("*"):
                if host.endswith(entry[1:]):
                    return True
            elif entry.endswith("*"):
                if host.startswith(entry[:-1]):
                    return True
            elif host == entry or host.endswith("." + entry):
                return True
        return False

    def header(self) -> dict:
        if not self.auth:
            return {}
        raw = ("%s:%s" % self.auth).encode("utf-8")
        return {"Proxy-Authorization": "Basic " + base64.b64encode(raw).decode("ascii")}


def parse_proxy_url(value: str, bypass_list=None):
    value = (value or "").strip()
    if not value:
        return None
    if "://" not in value:
        value = "http://" + value
    parts = urlsplit(value)
    if not parts.hostname:
        return None
    auth = (parts.username, parts.password) if parts.username else None
    return Proxy(parts.hostname, parts.port or 8080, bypass_list, auth)


def _windows_proxy():
    try:
        import winreg
    except ImportError:
        return None
    try:
        key = winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Internet Settings",
        )
        try:
            enabled = winreg.QueryValueEx(key, "ProxyEnable")[0]
        except OSError:
            enabled = 0
        if not enabled:
            return None
        try:
            server = winreg.QueryValueEx(key, "ProxyServer")[0]
        except OSError:
            return None
        try:
            override = winreg.QueryValueEx(key, "ProxyOverride")[0]
        except OSError:
            override = ""
        bypass = [item for item in str(override).split(";") if item]
        server = str(server)
        if "=" not in server:
            return parse_proxy_url(server, bypass)
        entries = {}
        for chunk in server.split(";"):
            if "=" in chunk:
                scheme, value = chunk.split("=", 1)
                entries[scheme.strip().lower()] = value.strip()
        return parse_proxy_url(entries.get("https") or entries.get("http") or "", bypass)
    except OSError:
        return None


def detect_proxy() -> Proxy | None:
    settings = get("proxy") or {}
    mode = settings.get("mode", "auto")
    if mode == "off":
        return None
    if mode == "manual":
        return parse_proxy_url(settings.get("url", ""))

    for name in ("HTTPS_PROXY", "https_proxy", "ALL_PROXY", "all_proxy",
                 "HTTP_PROXY", "http_proxy"):
        value = os.environ.get(name)
        if value:
            proxy = parse_proxy_url(value, os.environ.get("NO_PROXY", "").split(","))
            if proxy:
                return proxy

    if os.name == "nt":
        return _windows_proxy()
    return None


def proxy_label(proxy) -> str:
    if not proxy or not proxy.enabled:
        return ""
    return f"{proxy.host}:{proxy.port}"


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


def _connection(current: str, timeout: float, proxy):
    parts = urlsplit(current)
    host = parts.hostname
    port = parts.port
    path = parts.path or "/"
    if parts.query:
        path += "?" + parts.query
    use_proxy = bool(proxy and proxy.enabled and not proxy.bypass(host))

    if parts.scheme == "https":
        if use_proxy:
            conn = http.client.HTTPSConnection(proxy.host, proxy.port, timeout=timeout)
            conn.set_tunnel(host, port or 443)
        else:
            conn = http.client.HTTPSConnection(host, port, timeout=timeout)
    else:
        if use_proxy:
            conn = http.client.HTTPConnection(proxy.host, proxy.port, timeout=timeout)
            path = current
        else:
            conn = http.client.HTTPConnection(host, port, timeout=timeout)
    return parts, conn, path


def probe(url: str, timeout: float = TIMEOUT, first_byte_only: bool = False,
          proxy=None) -> ProbeResult:
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

            parts, conn, path = _connection(current, timeout, proxy)
            headers = dict(HEADERS)
            if proxy and proxy.enabled and not proxy.bypass(parts.hostname):
                headers.update(proxy.header())
            try:
                conn.request("GET", path, headers=headers)
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


def fetch_json(url: str, timeout: float = 10.0, proxy=None, limit: int = 2_000_000):
    """取一个小 JSON；失败返回 None。"""
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        return None
    try:
        parts, conn, path = _connection(url, timeout, proxy)
        headers = dict(HEADERS)
        headers["Accept"] = "application/json, */*"
        if proxy and proxy.enabled and not proxy.bypass(parts.hostname):
            headers.update(proxy.header())
        try:
            conn.request("GET", path, headers=headers)
            resp = conn.getresponse()
            if resp.status >= 400:
                resp.read()
                return None
            raw = resp.read(limit)
        finally:
            conn.close()
        return json.loads(raw.decode("utf-8", "replace"))
    except Exception:  # noqa: BLE001
        return None


def fetch_text(url: str, timeout: float = 12.0, proxy=None, limit: int = 8_000_000):
    """取一段文本，返回 (text, ttfb_seconds)；失败返回 (None, None)。

    会跟随重定向：部分镜像的 /simple 入口是 301（如中科大）。
    """
    current = url
    t0 = time.perf_counter()
    ttfb = None
    for _ in range(6):
        parts = urlsplit(current)
        if parts.scheme not in ("http", "https") or not parts.hostname:
            return None, None
        try:
            parts, conn, path = _connection(current, timeout, proxy)
            headers = dict(HEADERS)
            headers["Accept"] = "text/html, application/vnd.pypi.simple.v1+html, */*"
            if proxy and proxy.enabled and not proxy.bypass(parts.hostname):
                headers.update(proxy.header())
            chunks = []
            size = 0
            try:
                conn.request("GET", path, headers=headers)
                resp = conn.getresponse()
                status = resp.status
                location = resp.getheader("Location")
                if 300 <= status < 400 and location:
                    resp.read()
                    current = urljoin(current, location)
                    continue
                if status >= 400:
                    resp.read()
                    return None, None
                while True:
                    chunk = resp.read(65536)
                    if not chunk:
                        break
                    if ttfb is None:
                        ttfb = time.perf_counter() - t0
                    chunks.append(chunk)
                    size += len(chunk)
                    if size > limit:
                        break
            finally:
                conn.close()
            return b"".join(chunks).decode("utf-8", "replace"), (ttfb or time.perf_counter() - t0)
        except Exception:  # noqa: BLE001
            return None, None
    return None, None


# --------------------------------------------------------------------------- #
# 测速 / 排名
# --------------------------------------------------------------------------- #

def _empty_result():
    return {"ok": False, "lat": None, "speed": None, "version": None,
            "error": "", "score": 0.0, "rank": None}


def run_benchmark(src_list, proxy=None, runs: int = RUNS, timeout: float = TIMEOUT,
                  speed_timeout: float = SPEED_TIMEOUT, emit=None, cancel=None):
    """测速：并行测延迟，串行测下载与版本，emit(kind, *args) 汇报进度。"""
    emit = emit or (lambda *args: None)
    cancel = cancel or threading.Event()
    pairs = [(item["name"], item["url"]) for item in src_list]
    count = len(pairs)
    results = [_empty_result() for _ in range(count)]
    total_steps = count * (runs + 2)

    def latency(index: int):
        _, base = pairs[index]
        url = join_url(base, LATENCY_PKG)
        values = []
        error = ""
        for _ in range(runs):
            if cancel.is_set():
                return
            r = probe(url, timeout=timeout, first_byte_only=True, proxy=proxy)
            if r.ok and r.ttfb is not None:
                values.append(r.ttfb * 1000)
            else:
                error = r.error or "连接失败"
            emit("step", total_steps)
        if values:
            results[index]["ok"] = True
            results[index]["lat"] = {"min": min(values), "median": median(values)}
            emit("latency", index, results[index]["lat"], "")
        else:
            results[index]["error"] = error
            emit("latency", index, None, error)

    from concurrent.futures import ThreadPoolExecutor

    with ThreadPoolExecutor(max_workers=min(8, max(1, count))) as pool:
        list(pool.map(latency, range(count)))

    for index in range(count):
        if cancel.is_set():
            break
        if not results[index]["ok"]:
            continue
        _, base = pairs[index]

        r = probe(join_url(base, f"{SPEED_PKG}/-/{SPEED_FILE}"), timeout=speed_timeout, proxy=proxy)
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
        emit("step", total_steps)

        version = _registry_npm_version(base, proxy)
        results[index]["version"] = version
        emit("fresh", index, version, "")
        emit("step", total_steps)

    order = rank_results(results)
    emit("rank", order)
    return results


_HREF_RE = re.compile(r"""href\s*=\s*["']([^"']+)["']""", re.I)


def pip_index_url(base: str, pkg: str = PIP_PROBE_PKG) -> str:
    """PyPI simple 索引里某个包的页面地址（必须以 / 结尾，相对链接才能解析）。"""
    return join_url(base, pkg + "/")


def parse_pip_simple(html: str, page_url: str):
    """从 PEP 503 页面解析出 (最新版本, 该版本 wheel 的绝对地址)。

    PyPI 没有 npm 那种 dist-tags 接口，版本只能从 wheel 文件名里取；
    下载测速也依赖页面里给出的真实文件地址。
    """
    versions, wheels = [], []
    for href in _HREF_RE.findall(html or ""):
        target = href.split("#", 1)[0].strip()
        if not target.lower().endswith(".whl"):
            continue
        filename = target.rsplit("/", 1)[-1]
        parts = filename.split("-")
        if len(parts) < 2 or not parts[1]:
            continue
        version = parts[1]
        versions.append(version)
        wheels.append((version, urljoin(page_url, target)))
    if not versions:
        return None, None
    newest = newest_version(versions)
    for version, url in wheels:
        if version == newest:
            return newest, url
    return newest, wheels[0][1]


def run_pip_benchmark(src_list, proxy=None, runs: int = RUNS, timeout: float = TIMEOUT,
                      speed_timeout: float = SPEED_TIMEOUT, emit=None, cancel=None):
    """pip 源测速：并行测 simple 页延迟，串行解析版本并下载 wheel。"""
    emit = emit or (lambda *args: None)
    cancel = cancel or threading.Event()
    pairs = [(item["name"], item["url"]) for item in src_list]
    count = len(pairs)
    results = [_empty_result() for _ in range(count)]
    total_steps = count * (runs + 2)

    def latency(index: int):
        _, base = pairs[index]
        url = pip_index_url(base)
        values = []
        error = ""
        for _ in range(runs):
            if cancel.is_set():
                return
            r = probe(url, timeout=timeout, first_byte_only=True, proxy=proxy)
            if r.ok and r.ttfb is not None:
                values.append(r.ttfb * 1000)
            else:
                error = r.error or "连接失败"
            emit("step", total_steps)
        if values:
            results[index]["ok"] = True
            results[index]["lat"] = {"min": min(values), "median": median(values)}
            emit("latency", index, results[index]["lat"], "")
        else:
            results[index]["error"] = error
            emit("latency", index, None, error)

    from concurrent.futures import ThreadPoolExecutor

    with ThreadPoolExecutor(max_workers=min(8, max(1, count))) as pool:
        list(pool.map(latency, range(count)))

    for index in range(count):
        if cancel.is_set():
            break
        if not results[index]["ok"]:
            continue
        _, base = pairs[index]
        page = pip_index_url(base)

        html, _ttfb = fetch_text(page, timeout=timeout, proxy=proxy)
        version, wheel = parse_pip_simple(html, page) if html else (None, None)
        results[index]["version"] = version
        emit("fresh", index, version, "")
        emit("step", total_steps)

        if not wheel:
            results[index]["error"] = "索引页解析失败"
            emit("speed", index, None, "索引页解析失败")
            emit("step", total_steps)
            continue

        r = probe(wheel, timeout=speed_timeout, proxy=proxy)
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
        emit("step", total_steps)

    order = rank_results(results)
    emit("rank", order)
    return results


def run_ecosystem_benchmark(eco: str, src_list, **kwargs):
    """按生态分派到对应的测速实现。"""
    if eco == "pip":
        return run_pip_benchmark(src_list, **kwargs)
    return run_benchmark(src_list, **kwargs)


# --------------------------------------------------------------------------- #
# pip 版本与升级
# --------------------------------------------------------------------------- #

PIP_JSON_URL = "https://pypi.org/pypi/{pkg}/json"


def pip_latest_version(proxy=None, bases=None) -> str:
    """最新 pip 版本：先问各镜像的 simple 页（国内快），再回退到 PyPI JSON。

    顺序是有意的：国内直连 pypi.org 经常十几秒才响应，先问镜像能让
    升级弹窗立刻出结果；拿不到再兜底官方 JSON。
    """
    seen = set()
    for base in list(bases or []) + [OFFICIAL_PIP_INDEX]:
        key = (base or "").strip().rstrip("/").lower()
        if not key or key in seen:
            continue
        seen.add(key)
        page = pip_index_url(base)
        html, _ttfb = fetch_text(page, timeout=8, proxy=proxy)
        if not html:
            continue
        version, _wheel = parse_pip_simple(html, page)
        if version:
            return version

    data = fetch_json(PIP_JSON_URL.format(pkg=PIP_PROBE_PKG), timeout=10, proxy=proxy)
    if isinstance(data, dict):
        info = data.get("info")
        if isinstance(info, dict):
            version = info.get("version")
            if isinstance(version, str) and version.strip():
                return version.strip()
    return ""


def find_pip(scope=None) -> str:
    """定位要升级的那个 pip：虚拟环境优先，其次是 PATH 里的 pip / pip3。"""
    scope = scope or {}
    if scope.get("mode") == "project" and scope.get("dir"):
        for parts in (("Scripts", "pip.exe"), ("bin", "pip")):
            path = os.path.join(scope["dir"], *parts)
            if os.path.exists(path):
                return path
    for name in ("pip", "pip3"):
        found = shutil.which(name)
        if found:
            return found
    return ""


def pip_python(pip_path: str, scope=None) -> str:
    """pip 对应的 python 解释器。

    优先用 `python -m pip` 升级：Windows 上 pip 不能覆盖正在运行的 pip.exe，
    走模块方式可以绕开这个文件占用问题。
    """
    scope = scope or {}
    candidates = []
    if scope.get("mode") == "project" and scope.get("dir"):
        candidates += [os.path.join(scope["dir"], "Scripts", "python.exe"),
                       os.path.join(scope["dir"], "bin", "python")]
    if pip_path:
        folder = os.path.dirname(pip_path)
        # python.org 的布局：<prefix>\Scripts\pip.exe 与 <prefix>\python.exe
        candidates += [os.path.join(folder, "python.exe"),
                       os.path.join(folder, os.pardir, "python.exe")]
    for path in candidates:
        if path and os.path.exists(path):
            return os.path.normpath(path)
    return ""


def pip_upgrade_argv(scope=None):
    """返回 (显示用命令, argv 列表)；找不到 pip 时返回 ("", None)。"""
    pip_path = find_pip(scope)
    if not pip_path:
        return "", None
    python = pip_python(pip_path, scope)
    if python:
        argv = [python, "-m", "pip", "install", "--upgrade", "pip"]
    else:
        argv = [pip_path, "install", "--upgrade", "pip"]
    return subprocess.list2cmdline(argv), argv


def run_command(argv, timeout: float = 30, cwd=None):
    """按给定 argv 直接执行（路径已完整，不需要 shell 补全）。"""
    cmd = list(argv)
    if os.name == "nt" and cmd and cmd[0].lower().endswith((".cmd", ".bat")):
        cmd = ["cmd", "/c"] + cmd
    flags = CREATE_NO_WINDOW if os.name == "nt" else 0
    return subprocess.run(
        cmd, capture_output=True, text=True, timeout=timeout, creationflags=flags,
        encoding="utf-8", errors="replace", cwd=cwd, stdin=subprocess.DEVNULL,
    )


def _registry_npm_version(base: str, proxy=None):
    data = fetch_json(join_url(base, "-/package/npm/dist-tags"), timeout=8, proxy=proxy)
    if isinstance(data, dict):
        latest = data.get("latest")
        if isinstance(latest, str) and latest:
            return latest.strip()
    data = fetch_json(join_url(base, "npm/latest"), timeout=12, proxy=proxy)
    if isinstance(data, dict):
        version = data.get("version")
        if isinstance(version, str) and version:
            return version.strip()
    return None


def parse_version(value):
    if not value:
        return ()
    numbers = re.findall(r"\d+", str(value))
    return tuple(int(item) for item in numbers[:3])


def newest_version(versions):
    valid = [item for item in versions if item]
    if not valid:
        return None
    return max(valid, key=parse_version)


def rank_results(results):
    """延迟 70% + 下载 30% 归一化打分，返回按名次排列的下标。"""
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
# 包管理器
# --------------------------------------------------------------------------- #

def tool_exists(name: str) -> bool:
    return shutil.which(name) is not None


def run_tool(name: str, args, timeout: float = 30, cwd=None):
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
        encoding="utf-8", errors="replace", cwd=cwd,
        # 没有 stdin 时有些工具会等待输入（例如 python -v），一律给空设备
        stdin=subprocess.DEVNULL,
    )


# 各工具查询版本用的参数（python 的 -v 是“详细导入日志”，不是版本）
VERSION_ARGS = {
    "npm": ["-v"], "pnpm": ["-v"], "node": ["-v"],
    "yarn": ["--version"], "python": ["--version"], "python3": ["--version"],
    "pip": ["--version"], "pip3": ["--version"], "git": ["--version"],
}


def tool_version(name: str) -> str:
    try:
        proc = run_tool(name, VERSION_ARGS.get(name, ["-v"]), timeout=15)
        text = (proc.stdout or "").strip() or (proc.stderr or "").strip()
        first = text.splitlines()[0] if text else ""
        lower = first.lower()
        if lower.startswith("git version"):
            first = first[len("git version"):].strip()
        elif lower.startswith("python "):
            first = first[len("python "):].strip()
        elif lower.startswith("pip "):
            first = first[len("pip "):].strip()
            # "25.2 from C:\...\site-packages\pip (python 3.12)" → 只留版本号
            first = first.split(" from ", 1)[0].strip()
        return first
    except Exception:  # noqa: BLE001
        return ""


def refresh_path_from_registry():
    """安装完 Node/Git 后，把注册表里的 PATH 合并进当前进程。"""
    if os.name != "nt":
        return
    try:
        import winreg
    except ImportError:
        return

    values = []
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as key:
            values.append(winreg.QueryValueEx(key, "Path")[0])
    except OSError:
        pass
    try:
        with winreg.OpenKey(
            winreg.HKEY_LOCAL_MACHINE,
            r"SYSTEM\CurrentControlSet\Control\Session Manager\Environment",
        ) as key:
            values.append(winreg.QueryValueEx(key, "Path")[0])
    except OSError:
        pass

    existing = [item for item in os.environ.get("PATH", "").split(os.pathsep) if item]
    seen = {item.lower() for item in existing}
    for value in values:
        for item in str(value).split(os.pathsep):
            item = item.strip()
            if item and item.lower() not in seen:
                existing.append(item)
                seen.add(item.lower())
    os.environ["PATH"] = os.pathsep.join(existing)


def _scope_args(scope) -> tuple[list, str | None]:
    scope = scope or {}
    if scope.get("mode") == "project" and scope.get("dir"):
        return ["--location=project"], scope["dir"]
    return [], None


def _project_dir(scope) -> str:
    scope = scope or {}
    if scope.get("mode") == "project" and scope.get("dir"):
        return scope["dir"]
    return ""


def _yarnrc_line(key: str, url: str, separator: str) -> str:
    if separator == ":":
        return f'{key}: "{url}"\n'
    return f'{key} "{url}"\n'


def _yarnrc_set(directory: str, filename: str, key: str, url: str, separator: str) -> str:
    """按行改写 .yarnrc / .yarnrc.yml 的顶层键，保留其余内容。"""
    path = os.path.join(directory, filename)
    try:
        with open(path, encoding="utf-8", errors="replace") as handle:
            lines = list(handle)
    except OSError:
        lines = []
    if lines and not lines[-1].endswith("\n"):
        lines[-1] += "\n"

    new_line = _yarnrc_line(key, url, separator)
    out, replaced = [], False
    for line in lines:
        if not replaced and line.startswith(f"{key}{separator}"):
            out.append(new_line)
            replaced = True
        else:
            out.append(line)
    if not replaced:
        out.append(new_line)
    with open(path, "w", encoding="utf-8", newline="\n") as handle:
        handle.writelines(out)
    return path


def write_project_yarnrc(directory: str, url: str, berry: bool) -> str:
    """yarn 的 `config set` 只写全局配置，项目级必须自己落到 .yarnrc / .yarnrc.yml。"""
    if berry:
        return _yarnrc_set(directory, ".yarnrc.yml", "npmRegistryServer", url, ":")
    return _yarnrc_set(directory, ".yarnrc", "registry", url, " ")


def yarn_major() -> int:
    try:
        proc = run_tool("yarn", ["--version"], timeout=20)
        text = (proc.stdout or "").strip()
        return int(text.split(".")[0]) if text else 0
    except Exception:  # noqa: BLE001
        return 0


def get_registry(manager: str, scope=None) -> str:
    if manager == "pip":
        return effective_pip_index(scope)
    extra, cwd = _scope_args(scope)
    try:
        if manager == "yarn":
            proc = run_tool("yarn", ["config", "get", "registry"], timeout=20, cwd=cwd)
            value = (proc.stdout or "").strip()
            if proc.returncode == 0 and value.lower().startswith("http"):
                return value
            proc = run_tool("yarn", ["config", "get", "npmRegistryServer"], timeout=20, cwd=cwd)
            value = (proc.stdout or "").strip()
            if proc.returncode == 0 and value.lower().startswith("http"):
                return value
            return ""
        proc = run_tool(manager, ["config", "get", "registry"] + extra, timeout=20, cwd=cwd)
        value = (proc.stdout or "").strip()
        return value if proc.returncode == 0 else ""
    except Exception:  # noqa: BLE001
        return ""


def _set_yarn_project_registry(directory: str, url: str):
    scope = {"mode": "project", "dir": directory}
    previous = get_registry("yarn", scope)
    try:
        write_project_yarnrc(directory, url, yarn_major() >= 2)
    except OSError as exc:
        return False, str(exc), previous
    return True, "", previous


def set_registry(manager: str, url: str, scope=None):
    """切换源。返回 (ok, error, previous)。previous 用于失败回滚。"""
    if manager == "pip":
        return set_pip_index(url, scope)
    project = _project_dir(scope)
    if manager == "yarn" and project:
        return _set_yarn_project_registry(project, url)

    previous = get_registry(manager, scope)
    extra, cwd = _scope_args(scope)
    try:
        if manager == "yarn":
            if yarn_major() >= 2:
                attempts = [["config", "set", "npmRegistryServer", url]]
            else:
                attempts = [["config", "set", "registry", url]]
            last = ""
            for args in attempts:
                proc = run_tool("yarn", args, timeout=60, cwd=cwd)
                if proc.returncode == 0:
                    return True, "", previous
                last = (proc.stderr or proc.stdout or "yarn 执行失败").strip()
            return False, last, previous

        proc = run_tool(manager, ["config", "set", "registry", url] + extra, timeout=60, cwd=cwd)
        if proc.returncode == 0:
            return True, "", previous
        return False, (proc.stderr or proc.stdout or f"{manager} 执行失败").strip(), previous
    except FileNotFoundError:
        return False, f"未找到 {manager}，请先安装", previous
    except subprocess.TimeoutExpired:
        return False, "执行超时", previous
    except Exception as exc:  # noqa: BLE001
        return False, str(exc), previous


def rollback_registry(manager: str, previous: str, scope=None) -> bool:
    if not previous:
        return False
    ok, _, _ = set_registry(manager, previous, scope)
    return ok


def npmrc_path(scope=None) -> str:
    scope = scope or {}
    if scope.get("mode") == "project" and scope.get("dir"):
        return os.path.join(scope["dir"], ".npmrc")
    return os.path.join(os.path.expanduser("~"), ".npmrc")


def read_npmrc(path: str) -> dict:
    data = {}
    try:
        with open(path, encoding="utf-8", errors="replace") as handle:
            for line in handle:
                line = line.strip()
                if not line or line.startswith(("#", ";")):
                    continue
                if "=" in line:
                    key, value = line.split("=", 1)
                    data[key.strip()] = value.strip()
    except OSError:
        pass
    return data


def list_scoped(scope=None) -> list:
    """从 .npmrc 里读出 @scope:registry 行。"""
    data = read_npmrc(npmrc_path(scope))
    rows = []
    for key, value in data.items():
        if key.startswith("@") and key.endswith(":registry"):
            rows.append({"scope": key[:-len(":registry")], "url": value})
    return sorted(rows, key=lambda item: item["scope"])


def set_scoped(scope_name: str, url: str, scope=None):
    extra, cwd = _scope_args(scope)
    try:
        proc = run_tool("npm", ["config", "set", f"{scope_name}:registry", url] + extra,
                        timeout=60, cwd=cwd)
        if proc.returncode == 0:
            return True, ""
        return False, (proc.stderr or proc.stdout or "npm 执行失败").strip()
    except Exception as exc:  # noqa: BLE001
        return False, str(exc)


def remove_scoped(scope_name: str, scope=None):
    extra, cwd = _scope_args(scope)
    try:
        proc = run_tool("npm", ["config", "delete", f"{scope_name}:registry"] + extra,
                        timeout=60, cwd=cwd)
        return proc.returncode == 0, (proc.stderr or "").strip()
    except Exception as exc:  # noqa: BLE001
        return False, str(exc)


def write_project_npmrc(directory: str, url: str) -> str:
    """在项目目录写入/合并 .npmrc 的 registry 行。"""
    path = os.path.join(directory, ".npmrc")
    lines = []
    replaced = False
    try:
        with open(path, encoding="utf-8", errors="replace") as handle:
            for line in handle:
                if line.strip().startswith("registry=") and not replaced:
                    lines.append(f"registry={url}\n")
                    replaced = True
                else:
                    lines.append(line)
    except OSError:
        pass
    if not replaced:
        lines.append(f"registry={url}\n")
    with open(path, "w", encoding="utf-8", newline="\n") as handle:
        handle.writelines(lines)
    return path


# --------------------------------------------------------------------------- #
# pip 配置（pip.ini）
# --------------------------------------------------------------------------- #

def pip_user_ini() -> str:
    if os.name == "nt":
        base = os.environ.get("APPDATA") or os.path.expanduser("~")
        return os.path.join(base, "pip", PIP_INI_NAME)
    return os.path.join(os.path.expanduser("~"), ".config", "pip", PIP_INI_NAME)


def pip_scope_ini(scope=None) -> str:
    """用户级写用户 pip.ini；venv 级写 <venv>\\pip.ini（pip 的 site 配置）。"""
    scope = scope or {}
    if scope.get("mode") == "project" and scope.get("dir"):
        return os.path.join(scope["dir"], PIP_INI_NAME)
    return pip_user_ini()


def looks_like_venv(directory: str) -> bool:
    if not directory:
        return False
    markers = ("pyvenv.cfg", os.path.join("Scripts", "python.exe"),
               os.path.join("bin", "python"))
    return any(os.path.exists(os.path.join(directory, marker)) for marker in markers)


def _read_ini_text(path: str) -> str:
    try:
        with open(path, "rb") as handle:
            raw = handle.read()
    except OSError:
        return ""
    for encoding in ("utf-8-sig", "gbk"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", "replace")


_SECTION_RE = re.compile(r"^\s*\[([^\]]+)\]\s*$")


def _ini_sections(lines):
    """[(小写节名, 节头行号, 节结束行号)]。"""
    sections = []
    current = None
    for index, line in enumerate(lines):
        match = _SECTION_RE.match(line)
        if match:
            if current is not None:
                sections.append((current[0], current[1], index))
            current = (match.group(1).strip().lower(), index)
    if current is not None:
        sections.append((current[0], current[1], len(lines)))
    return sections


def _ini_value(text: str, section: str, key: str) -> str:
    lines = text.splitlines()
    pattern = re.compile(r"^\s*" + re.escape(key) + r"\s*[=:]\s*(.*)$", re.I)
    for name, start, end in _ini_sections(lines):
        if name != section.lower():
            continue
        for line in lines[start + 1:end]:
            if line.lstrip().startswith(("#", ";")):
                continue
            match = pattern.match(line)
            if match:
                return match.group(1).strip()
    return ""


def _ini_set(text: str, section: str, key: str, value: str) -> str:
    """按行改写 ini，保留注释与其他键，不整份重写。"""
    lines = text.splitlines()
    new_line = f"{key} = {value}"
    pattern = re.compile(r"^\s*" + re.escape(key) + r"\s*[=:]", re.I)
    for name, start, end in _ini_sections(lines):
        if name != section.lower():
            continue
        for index in range(start + 1, end):
            if lines[index].lstrip().startswith(("#", ";")):
                continue
            if pattern.match(lines[index]):
                lines[index] = new_line
                return "\n".join(lines) + "\n"
        insert_at = start + 1
        for index in range(start + 1, end):
            if lines[index].strip():
                insert_at = index + 1
        lines.insert(insert_at, new_line)
        return "\n".join(lines) + "\n"
    if lines and lines[-1].strip():
        lines.append("")
    lines.append(f"[{section}]")
    lines.append(new_line)
    return "\n".join(lines) + "\n"


def _write_text_atomic(path: str, text: str):
    directory = os.path.dirname(path)
    if directory:
        os.makedirs(directory, exist_ok=True)
    temp = path + ".tmp"
    with open(temp, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(text)
    os.replace(temp, path)


def read_pip_index(path: str) -> str:
    return _ini_value(_read_ini_text(path), "global", "index-url")


def effective_pip_index(scope=None) -> str:
    """pip 实际生效的 index-url：环境变量 > venv 配置 > 用户配置 > 官方源。

    pip 没配 index-url 时用的是 PyPI 官方，这里跟着返回官方源，
    界面上就能正确高亮「PyPI 官方」而不是显示「未检测到」。
    """
    url = (os.environ.get("PIP_INDEX_URL") or "").strip()
    if url:
        return url
    scope = scope or {}
    if scope.get("mode") == "project" and scope.get("dir"):
        value = read_pip_index(pip_scope_ini(scope))
        if value:
            return value
    return read_pip_index(pip_user_ini()) or OFFICIAL_PIP_INDEX


def set_pip_index(url: str, scope=None):
    """写入 pip.ini 的 [global] index-url。返回 (ok, error, previous)。"""
    path = pip_scope_ini(scope)
    text = _read_ini_text(path)
    previous = _ini_value(text, "global", "index-url")
    try:
        _write_text_atomic(path, _ini_set(text, "global", "index-url", url))
        return True, "", previous
    except OSError as exc:
        return False, str(exc), previous


def pip_warnings(scope=None) -> list:
    """可能让切换“看起来没生效”的情况，返回代码列表供界面翻译。"""
    codes = []
    if (os.environ.get("PIP_INDEX_URL") or "").strip():
        codes.append("env_index_url")
    if (os.environ.get("PIP_EXTRA_INDEX_URL") or "").strip():
        codes.append("env_extra_index")
    if _ini_value(_read_ini_text(pip_scope_ini(scope)), "global", "extra-index-url").strip():
        codes.append("extra_index_url")
    return codes


def latest_npm_version(proxy=None) -> str:
    try:
        proc = run_tool("npm", ["view", "npm", "version"], timeout=40)
        text = (proc.stdout or "").strip()
        return text.splitlines()[-1] if text else ""
    except Exception:  # noqa: BLE001
        return ""
