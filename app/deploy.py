# -*- coding: utf-8 -*-
"""开发环境部署：Node.js / Python / Git 的版本目录、下载与静默安装。

设计取舍
--------
* Node.js、Git 用官方安装包（机器级，需要一次 UAC 授权）。
* Python 用官方安装器但按当前用户安装（InstallAllUsers=0），不弹 UAC。
* 下载优先走国内镜像（npmmirror），自动跳过 404 的镜像并回退官方源。
* Node.js 校验 SHASUMS256.txt；所有安装包都检查 PE 头（MZ）。

版本目录的来源
--------------
* Node.js：nodejs.org/dist/index.json（取每个主版本最新的 LTS）
* Python：endoflife.date 给出各分支最新补丁号，再向下探测“真正带 Windows 安装包”的版本
  （安全修复版只有源码包，没有 exe，不能直接装）
* Git：npmmirror 的 git-for-windows 目录清单（国内可访问，不受 GitHub API 限流影响）
"""

from __future__ import annotations

import ctypes
import hashlib
import json
import os
import platform
import re
import subprocess
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urljoin, urlsplit

from . import APP_VERSION
from .core import CREATE_NO_WINDOW, _connection, fetch_json, probe, tool_version

USER_AGENT = f"Quick-npm-ok/{APP_VERSION}"

NODE_INDEX = "https://nodejs.org/dist/index.json"
NODE_MIRROR = "https://registry.npmmirror.com/-/binary/node"
NODE_OFFICIAL = "https://nodejs.org/dist"

PYTHON_CYCLES = "https://endoflife.date/api/python.json"
PYTHON_MIRROR = "https://registry.npmmirror.com/-/binary/python"
PYTHON_HUAWEI = "https://repo.huaweicloud.com/python"
PYTHON_OFFICIAL = "https://www.python.org/ftp/python"

GIT_INDEX = "https://registry.npmmirror.com/-/binary/git-for-windows"
GIT_API = "https://api.github.com/repos/git-for-windows/git/releases?per_page=5"
GIT_FALLBACK = ["2.47.1.windows.1", "2.46.2.windows.1", "2.45.2.windows.1"]

MAX_PATCH_WALK = 24


def arch_suffix() -> str:
    machine = platform.machine().lower()
    return "arm64" if "arm" in machine else "x64"


def _version_key(text: str) -> tuple:
    return tuple(int(part) for part in re.findall(r"\d+", text))


# --------------------------------------------------------------------------- #
# 安装包文件名（各家的命名规则都不一样，集中在这里）
# --------------------------------------------------------------------------- #

def node_filename(version: str) -> str:
    return f"node-v{version}-{arch_suffix()}.msi"


def python_filename(version: str) -> str:
    return f"python-{version}-{'arm64' if arch_suffix() == 'arm64' else 'amd64'}.exe"


def git_base(version: str) -> str:
    return version.split(".windows")[0]


def git_filename(version: str) -> str:
    base = git_base(version)
    return f"Git-{base}-{'arm64' if arch_suffix() == 'arm64' else '64-bit'}.exe"


# --------------------------------------------------------------------------- #
# 版本目录
# --------------------------------------------------------------------------- #

def node_versions(proxy=None) -> list:
    data = fetch_json(NODE_INDEX, timeout=20, proxy=proxy)
    if not isinstance(data, list):
        return []
    seen = {}
    for item in data:
        if not isinstance(item, dict) or not item.get("lts"):
            continue
        version = str(item.get("version", "")).lstrip("v")
        if not version:
            continue
        major = version.split(".")[0]
        if major in seen:
            continue
        seen[major] = {
            "version": version,
            "lts": str(item.get("lts")),
            "date": item.get("date", ""),
        }
        if len(seen) >= 4:
            break
    return list(seen.values())


def _python_dir_files(version: str, proxy=None):
    """列出 npmmirror 上某个 Python 版本目录里的文件名；失败返回 None。"""
    data = fetch_json(f"{PYTHON_MIRROR}/{version}/", timeout=12, proxy=proxy,
                      limit=4_000_000)
    if not isinstance(data, list):
        return None
    names = set()
    for item in data:
        if isinstance(item, dict):
            name = str(item.get("name", ""))
            if name:
                names.add(name)
    return names


def has_python_installer(version: str, proxy=None) -> bool:
    names = _python_dir_files(version, proxy)
    if names:
        return python_filename(version) in names
    # 镜像查不到时退回官方地址探测
    return probe(f"{PYTHON_OFFICIAL}/{version}/{python_filename(version)}",
                 timeout=10, first_byte_only=True, proxy=proxy).ok


def _resolve_python(cycle: str, latest: str, proxy=None) -> dict | None:
    """从 latest 往下找第一个真正带 Windows 安装包的补丁版本。"""
    parts = latest.split(".")
    while len(parts) < 3:
        parts.append("0")
    major, minor, patch = (int(item) for item in parts[:3])
    for _ in range(MAX_PATCH_WALK):
        version = f"{major}.{minor}.{patch}"
        if has_python_installer(version, proxy):
            return {"version": version, "cycle": cycle, "lts": "", "eol": "", "note": ""}
        patch -= 1
        if patch < 0:
            return None
    return None


def python_versions(proxy=None) -> list:
    data = fetch_json(PYTHON_CYCLES, timeout=20, proxy=proxy)
    if not isinstance(data, list):
        return []

    candidates = []
    for item in data:
        if not isinstance(item, dict):
            continue
        cycle = str(item.get("cycle", ""))
        latest = item.get("latest")
        if not latest or not cycle.startswith("3."):
            continue
        try:
            if float(cycle) < 3.10:
                continue
        except ValueError:
            continue
        candidates.append((cycle, str(latest), item.get("eol", "")))
        if len(candidates) >= 4:
            break

    def resolve(entry):
        cycle, latest, eol = entry
        row = _resolve_python(cycle, latest, proxy)
        if row is None:
            return None
        row["eol"] = eol
        if row["version"] != latest:
            row["note"] = f"{latest} 无 Windows 安装包"
        return row

    if not candidates:
        return []
    with ThreadPoolExecutor(max_workers=len(candidates)) as pool:
        rows = [row for row in pool.map(resolve, candidates) if row]
    return rows


def git_versions(proxy=None) -> list:
    data = fetch_json(GIT_INDEX, timeout=20, proxy=proxy, limit=4_000_000)
    names = []
    if isinstance(data, list):
        for item in data:
            if isinstance(item, dict) and item.get("type") == "dir":
                name = str(item.get("name", "")).rstrip("/")
                if re.match(r"^v\d+\.\d+\.\d+\.windows\.\d+$", name):
                    names.append(name.lstrip("v"))
    if names:
        names.sort(key=_version_key)
        unique = []
        seen = set()
        for version in reversed(names):
            base = git_base(version)
            if base in seen:          # 同一内核版本只保留最新的 windows 修订
                continue
            seen.add(base)
            unique.append({"version": version, "lts": "", "date": ""})
            if len(unique) >= 4:
                break
        return unique

    data = fetch_json(GIT_API, timeout=20, proxy=proxy)
    rows = []
    if isinstance(data, list):
        for item in data:
            if not isinstance(item, dict):
                continue
            tag = str(item.get("tag_name", "")).lstrip("v")
            if tag:
                rows.append({"version": tag, "lts": "", "date": item.get("published_at", "")})
    if not rows:
        rows = [{"version": tag, "lts": "", "date": ""} for tag in GIT_FALLBACK]
    return rows[:4]


# --------------------------------------------------------------------------- #
# 下载
# --------------------------------------------------------------------------- #

def _pe_ok(path: str) -> bool:
    """检查是不是 Windows 可执行体。

    exe 以 "MZ" 开头，msi 是 OLE 复合文档（D0 CF 11 E0 …），两者都算正常。
    用来挡住「镜像返回一个 HTML 错误页却状态 200」这类情况。
    """
    try:
        with open(path, "rb") as handle:
            head = handle.read(8)
    except OSError:
        return False
    if head[:2] == b"MZ":
        return True
    return head == b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"


def sha256_file(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def download(url: str, dest: str, progress=None, cancel=None, proxy=None,
             timeout: float = 60) -> str:
    """流式下载到 dest，自动跟随跳转，进度 progress(pct, done, total)。"""
    current = url
    for _hop in range(6):
        parts = urlsplit(current)
        if parts.scheme not in ("http", "https") or not parts.hostname:
            raise ValueError("bad url")

        temp = dest + ".part"
        _, conn, path = _connection(current, timeout, proxy)
        headers = {
            "User-Agent": USER_AGENT,
            "Accept": "*/*",
            "Accept-Encoding": "identity",
            "Connection": "close",
        }
        if proxy and proxy.enabled and not proxy.bypass(parts.hostname):
            headers.update(proxy.header())

        total = 0
        done = 0
        try:
            conn.request("GET", path, headers=headers)
            resp = conn.getresponse()

            # 镜像常常 302 到 CDN，必须跟随，否则会把跳转页当成安装包
            if 300 <= resp.status < 400:
                location = resp.getheader("Location")
                resp.read()
                if not location:
                    raise OSError(f"HTTP {resp.status} 但没有跳转地址")
                current = urljoin(current, location)
                continue
            if resp.status >= 400:
                raise OSError(f"HTTP {resp.status}")
            try:
                total = int(resp.getheader("Content-Length") or 0)
            except ValueError:
                total = 0

            os.makedirs(os.path.dirname(dest) or ".", exist_ok=True)
            with open(temp, "wb") as handle:
                while True:
                    if cancel is not None and cancel.is_set():
                        raise InterruptedError("cancelled")
                    chunk = resp.read(1 << 16)
                    if not chunk:
                        break
                    handle.write(chunk)
                    done += len(chunk)
                    if progress:
                        pct = int(done * 100 / total) if total else 0
                        progress(pct, done, total)
        except Exception:
            try:
                os.remove(temp)
            except OSError:
                pass
            raise
        finally:
            conn.close()

        if total and done < total:
            try:
                os.remove(temp)
            except OSError:
                pass
            raise OSError("下载不完整")

        os.replace(temp, dest)
        return dest

    raise OSError("重定向次数过多")


def _fetch_text(url: str, proxy=None, limit: int = 2_000_000) -> str:
    current = url
    for _hop in range(4):
        parts = urlsplit(current)
        if parts.scheme not in ("http", "https") or not parts.hostname:
            return ""
        try:
            _, conn, path = _connection(current, 20, proxy)
            headers = {"User-Agent": USER_AGENT, "Accept-Encoding": "identity",
                       "Connection": "close"}
            if proxy and proxy.enabled and not proxy.bypass(parts.hostname):
                headers.update(proxy.header())
            try:
                conn.request("GET", path, headers=headers)
                resp = conn.getresponse()
                if 300 <= resp.status < 400:
                    location = resp.getheader("Location")
                    resp.read()
                    if not location:
                        return ""
                    current = urljoin(current, location)
                    continue
                if resp.status >= 400:
                    return ""
                return resp.read(limit).decode("utf-8", "replace")
            finally:
                conn.close()
        except Exception:  # noqa: BLE001
            return ""
    return ""


def expected_sha(base: str, filename: str, proxy=None) -> str:
    text = _fetch_text(base.rstrip("/") + "/SHASUMS256.txt", proxy=proxy)
    for line in text.splitlines():
        pieces = line.split()
        if len(pieces) >= 2 and pieces[-1].lstrip("*") == filename:
            return pieces[0].strip()
    return ""


# --------------------------------------------------------------------------- #
# 提权执行
# --------------------------------------------------------------------------- #

class _SHELLEXECUTEINFOW(ctypes.Structure):
    _fields_ = [
        ("cbSize", ctypes.c_ulong),
        ("fMask", ctypes.c_ulong),
        ("hwnd", ctypes.c_void_p),
        ("lpVerb", ctypes.c_wchar_p),
        ("lpFile", ctypes.c_wchar_p),
        ("lpParameters", ctypes.c_wchar_p),
        ("lpDirectory", ctypes.c_wchar_p),
        ("nShow", ctypes.c_int),
        ("hInstApp", ctypes.c_void_p),
        ("lpIDList", ctypes.c_void_p),
        ("lpClass", ctypes.c_wchar_p),
        ("hkeyClass", ctypes.c_void_p),
        ("dwHotKey", ctypes.c_ulong),
        ("hIcon", ctypes.c_void_p),
        ("hProcess", ctypes.c_void_p),
    ]


SEE_MASK_NOCLOSEPROCESS = 0x00000040


def run_elevated(file: str, params: str, timeout: float = 1800):
    """以管理员身份运行并等待，返回 (exit_code, error)。用户点“否”时 error=cancelled。"""
    if os.name != "nt":
        return -1, "仅支持 Windows"

    info = _SHELLEXECUTEINFOW()
    info.cbSize = ctypes.sizeof(_SHELLEXECUTEINFOW)
    info.fMask = SEE_MASK_NOCLOSEPROCESS
    info.lpVerb = "runas"
    info.lpFile = file
    info.lpParameters = params
    info.nShow = 1

    shell32 = ctypes.windll.shell32
    kernel32 = ctypes.windll.kernel32
    if not shell32.ShellExecuteExW(ctypes.byref(info)):
        code = kernel32.GetLastError()
        if code == 1223:
            return -1, "cancelled"
        return -1, f"ShellExecuteEx 失败（{code}）"
    if not info.hProcess:
        return 0, ""

    try:
        kernel32.WaitForSingleObject(ctypes.c_void_p(info.hProcess), int(timeout * 1000))
        exit_code = ctypes.c_ulong(0)
        kernel32.GetExitCodeProcess(ctypes.c_void_p(info.hProcess), ctypes.byref(exit_code))
        return int(exit_code.value), ""
    finally:
        kernel32.CloseHandle(ctypes.c_void_p(info.hProcess))


def run_installer(path: str, args, elevated: bool, timeout: float,
                  log=None, cancel=None):
    log = log or (lambda _text: None)
    if elevated:
        log("正在请求管理员权限…")
        return run_elevated(path, args, timeout=timeout)

    cmd = [path] + list(args)
    try:
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                text=True, encoding="utf-8", errors="replace",
                                creationflags=CREATE_NO_WINDOW)
    except OSError as exc:
        return -1, str(exc)

    while proc.poll() is None:
        if cancel is not None and cancel.is_set():
            proc.terminate()
            return -1, "cancelled"
        time.sleep(0.2)
    return proc.returncode, ""


# --------------------------------------------------------------------------- #
# 安装流程
# --------------------------------------------------------------------------- #

def _live_candidates(candidates, log, proxy):
    """先探测再下载，把 404 / 不可达的镜像提前剔除，日志更干净。"""
    live = []
    for label, url, base in candidates:
        result = probe(url, timeout=10, first_byte_only=True, proxy=proxy)
        if result.ok:
            live.append((label, url, base))
        else:
            reason = result.error or f"HTTP {result.status}"
            log(f"{label} 不可用（{reason}），换下一个源")
    return live


def _download_first(candidates, dest, log, progress, cancel, proxy, verify_sha=None):
    if cancel is not None and cancel.is_set():
        return False, "cancelled"

    live = _live_candidates(candidates, log, proxy)
    if not live:
        return False, "所有下载源都不可用（检查网络或代理）"

    last = ""
    for label, url, base in live:
        try:
            log(f"下载源：{label}")
            download(url, dest, progress=progress, cancel=cancel, proxy=proxy)
            if verify_sha:
                want = expected_sha(base, verify_sha, proxy=proxy)
                if want:
                    got = sha256_file(dest)
                    if got.lower() != want.lower():
                        last = "SHA256 校验失败"
                        log(f"{last}，换下一个源重试…")
                        continue
                    log("SHA256 校验通过")
            if not _pe_ok(dest):
                last = "安装包格式异常（不是有效的 exe/msi）"
                log(last)
                continue
            return True, ""
        except InterruptedError:
            return False, "cancelled"
        except Exception as exc:  # noqa: BLE001
            last = str(exc)
            log(f"{label} 下载失败：{exc}")
    return False, last or "下载失败"


def _temp_file(name: str) -> str:
    return os.path.join(tempfile.gettempdir(), name)


def _cleanup(path: str):
    try:
        os.remove(path)
    except OSError:
        pass


def _after_install(log, tool: str):
    from .core import refresh_path_from_registry
    refresh_path_from_registry()
    log(f"{tool} {tool_version(tool) or '?'} 已可用")


def install_node(version: str, log, progress, cancel=None, proxy=None):
    filename = node_filename(version)
    dest = _temp_file(filename)

    candidates = [
        ("npmmirror", f"{NODE_MIRROR}/v{version}/{filename}", f"{NODE_MIRROR}/v{version}"),
        ("nodejs.org", f"{NODE_OFFICIAL}/v{version}/{filename}", f"{NODE_OFFICIAL}/v{version}"),
    ]
    ok, error = _download_first(candidates, dest, log, progress, cancel, proxy, verify_sha=filename)
    if not ok:
        return False, "cancelled" if error == "cancelled" else error

    log("正在安装 Node.js（如弹出 UAC 请选择「是」）…")
    code, error = run_installer("msiexec.exe", f'/i "{dest}" /qn /norestart', True, 1800,
                                log=log, cancel=cancel)
    if error == "cancelled":
        return False, "cancelled"
    if error:
        return False, error
    if code not in (0, 3010):
        return False, f"安装器返回 {code}（可查看 %TEMP% 下的 MSI 日志）"

    _after_install(log, "npm")
    _cleanup(dest)
    return True, ""


def install_python(version: str, log, progress, cancel=None, proxy=None):
    filename = python_filename(version)
    dest = _temp_file(filename)

    candidates = [
        ("npmmirror", f"{PYTHON_MIRROR}/{version}/{filename}", ""),
        ("华为云", f"{PYTHON_HUAWEI}/{version}/{filename}", ""),
        ("python.org", f"{PYTHON_OFFICIAL}/{version}/{filename}", ""),
    ]
    ok, error = _download_first(candidates, dest, log, progress, cancel, proxy)
    if not ok:
        return False, "cancelled" if error == "cancelled" else error

    log("正在按当前用户安装 Python（无需管理员）…")
    args = ["/quiet", "InstallAllUsers=0", "PrependPath=1", "Include_pip=1",
            "Include_test=0", "Shortcuts=0", "Include_launcher=1"]
    code, error = run_installer(dest, args, False, 1800, log=log, cancel=cancel)
    if error == "cancelled":
        return False, "cancelled"
    if error:
        return False, error
    if code != 0:
        return False, f"安装器返回 {code}"

    _after_install(log, "python")
    _cleanup(dest)
    return True, ""


def install_git(version: str, log, progress, cancel=None, proxy=None):
    filename = git_filename(version)
    dest = _temp_file(filename)

    candidates = [
        ("npmmirror", f"{GIT_INDEX}/v{version}/{filename}", ""),
        ("github", f"https://github.com/git-for-windows/git/releases/download/v{version}/{filename}", ""),
    ]
    ok, error = _download_first(candidates, dest, log, progress, cancel, proxy)
    if not ok:
        return False, "cancelled" if error == "cancelled" else error

    log("正在安装 Git（如弹出 UAC 请选择「是」）…")
    args = ("/VERYSILENT /NORESTART /NOCANCEL /SP- /CLOSEAPPLICATIONS "
            "/RESTARTAPPLICATIONS")
    code, error = run_installer(dest, args, True, 1800, log=log, cancel=cancel)
    if error == "cancelled":
        return False, "cancelled"
    if error:
        return False, error
    if code not in (0, 3010):
        return False, f"安装器返回 {code}"

    _after_install(log, "git")
    _cleanup(dest)
    return True, ""


def installed_versions() -> dict:
    return {
        "node": tool_version("node"),
        "npm": tool_version("npm"),
        "python": tool_version("python"),
        "git": tool_version("git"),
    }


def summary() -> str:
    data = installed_versions()
    return json.dumps(data, ensure_ascii=False)
