#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""一键打包：生成图标 → PyInstaller 单文件 exe。

用法
----
    python build.py            # 打包，结束后暂停（双击 build.bat 时用）
    python build.py --no-pause # 打包，不暂停（脚本/CI 用）
"""

import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
APP_NAME = "npm源测速"


def force_utf8_console():
    """CI（英文代码页）下 print 中文会 UnicodeEncodeError，这里统一成 UTF-8。"""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:  # noqa: BLE001
            pass


def run(cmd):
    print(">", " ".join(cmd), flush=True)
    subprocess.run(cmd, cwd=ROOT, check=True)


def main():
    force_utf8_console()
    no_pause = "--no-pause" in sys.argv

    try:
        print("[1/3] 生成图标…", flush=True)
        run([sys.executable, "make_icon.py"])

        print("[2/3] 打包 exe…", flush=True)
        run([
            sys.executable, "-m", "PyInstaller",
            "--noconfirm", "--clean", "--onefile", "--windowed",
            "--name", APP_NAME,
            "--icon", "app.ico",
            "--add-data", "app.ico;.",
            "--add-data", "app.png;.",
            "--hidden-import", "app.core",
            "--hidden-import", "app.deploy",
            "--hidden-import", "app.i18n",
            "--hidden-import", "app.tray",
            "--hidden-import", "app.ui",
            "--hidden-import", "app.updater",
            "npm_mirror_tool.py",
        ])

        exe = os.path.join(ROOT, "dist", APP_NAME + ".exe")
        if not os.path.exists(exe):
            raise SystemExit("未找到输出文件：" + exe)

        size = os.path.getsize(exe) / 1024 / 1024
        print(f"[3/3] 完成：dist\\{APP_NAME}.exe（{size:.1f} MB）", flush=True)
        code = 0
    except subprocess.CalledProcessError as exc:
        print(f"\n构建失败：命令返回 {exc.returncode}，请查看上方输出。", flush=True)
        code = exc.returncode or 1
    except SystemExit as exc:
        print(f"\n构建失败：{exc}", flush=True)
        code = 1

    if not no_pause:
        try:
            input("\n按回车退出…")
        except EOFError:
            pass
    return code


if __name__ == "__main__":
    sys.exit(main())
