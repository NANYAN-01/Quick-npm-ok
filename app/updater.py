# -*- coding: utf-8 -*-
"""自动更新：检查 GitHub Releases，下载新 exe，退出后替换并重启。"""

from __future__ import annotations

import os
import subprocess
import tempfile

from . import APP_VERSION, GITHUB_REPO
from .core import fetch_json, parse_version

API = f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest"
ASSET_NAME = "Quick-npm-ok-{version}.exe"


def check(proxy=None):
    """返回 {'version', 'url', 'size', 'notes'} 或 None。"""
    data = fetch_json(API, timeout=15, proxy=proxy)
    if not isinstance(data, dict):
        return None
    tag = str(data.get("tag_name", "")).lstrip("v")
    if not tag or parse_version(tag) <= parse_version(APP_VERSION):
        return None

    assets = data.get("assets") or []
    url = ""
    size = 0
    for asset in assets:
        name = str(asset.get("name", ""))
        if name.lower().endswith(".exe"):
            url = asset.get("browser_download_url", "")
            size = int(asset.get("size", 0) or 0)
            break
    if not url:
        return None
    return {"version": tag, "url": url, "size": size, "notes": data.get("body", "")}


def download(update, dest: str, progress=None, cancel=None, proxy=None):
    from .deploy import download as _download
    return _download(update["url"], dest, progress=progress, cancel=cancel, proxy=proxy)


def apply_update(new_exe: str, target_exe: str) -> str:
    """写一个替换脚本，返回脚本路径。调用方应在退出前执行它。"""
    staging = os.path.join(os.path.dirname(target_exe), "_update_download.exe")
    try:
        os.replace(new_exe, staging)
    except OSError:
        staging = new_exe

    script = os.path.join(tempfile.gettempdir(), "quick-npm-ok-update.bat")
    lines = [
        "@echo off",
        "ping -n 3 127.0.0.1 >nul",
        ":wait",
        f'tasklist /fi "IMAGENAME eq {os.path.basename(target_exe)}" | find /i "{os.path.basename(target_exe)}" >nul',
        "if not errorlevel 1 (ping -n 2 127.0.0.1 >nul & goto wait)",
        f'move /y "{staging}" "{target_exe}" >nul',
        f'start "" "{target_exe}"',
        'del "%~f0"',
    ]
    with open(script, "w", encoding="gbk", errors="replace", newline="\r\n") as handle:
        handle.write("\n".join(lines) + "\n")
    return script


def launch_update(script: str):
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    subprocess.Popen(["cmd", "/c", script], creationflags=flags, close_fds=True)
