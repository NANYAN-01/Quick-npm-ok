# -*- coding: utf-8 -*-
"""检查各弹窗在不同 DPI 缩放下是否被裁切。

用法：python tools/check_dialogs.py            # 100% / 150% / 200% 各跑一遍
      python tools/check_dialogs.py 1.5        # 只跑指定缩放
"""

from __future__ import annotations

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ.setdefault("NPM_MIRROR_TOOL_CONFIG_DIR", os.path.join(ROOT, "out", "cfg-check"))

import tkinter as tk  # noqa: E402

from app import core  # noqa: E402
from app.ui import App  # noqa: E402

def open_upgrade(app, eco):
    if app.ecosystem != eco:
        app.select_ecosystem(eco)
    app.open_upgrade()


OPENERS = (
    ("sources", lambda a: a.open_sources()),
    ("scopes", lambda a: a.open_scopes()),
    ("project", lambda a: a.open_project()),
    ("history", lambda a: a.open_history()),
    ("settings", lambda a: a.open_settings()),
    ("about", lambda a: a.open_about()),
    ("env", lambda a: a.open_env()),
    ("upgrade-npm", lambda a: open_upgrade(a, "npm")),
    ("upgrade-pip", lambda a: open_upgrade(a, "pip")),
)


def measure(dialog):
    dialog.update_idletasks()
    dialog.update()
    squashed = []
    overflow = []
    bottom = dialog.winfo_rooty() + dialog.winfo_height()
    right = dialog.winfo_rootx() + dialog.winfo_width()
    stack = list(dialog.winfo_children())
    while stack:
        widget = stack.pop()
        stack.extend(widget.winfo_children())
        if not widget.winfo_ismapped():
            continue
        if widget.winfo_rooty() + widget.winfo_height() > bottom + 1:
            overflow.append(widget.winfo_class())
        elif widget.winfo_rootx() + widget.winfo_width() > right + 1:
            overflow.append(widget.winfo_class())
        elif widget.winfo_height() + 2 < widget.winfo_reqheight():
            squashed.append(f"{widget.winfo_class()}:{widget.winfo_height()}<{widget.winfo_reqheight()}")
    return dialog.winfo_width(), dialog.winfo_height(), overflow, squashed


def main_overflow(app):
    """主窗口里的控件有没有被挤到右边界外（顶部那一排最容易出问题）。"""
    root = app.root
    root.update()
    limit = root.winfo_rootx() + root.winfo_width()
    bad = []
    stack = [root]
    while stack:
        widget = stack.pop()
        stack.extend(widget.winfo_children())
        if not widget.winfo_ismapped():
            continue
        if widget.winfo_rootx() + widget.winfo_width() > limit + 1:
            bad.append(widget.winfo_class())
    return root.winfo_width(), bad


def run(scale: float):
    root = tk.Tk()
    app = App(root, scale=scale)
    root.update()
    print(f"\n=== 缩放 {scale:.0%} ===")
    left, top, right, bottom = app.work_area()
    print(f"工作区 {right - left}x{bottom - top}")
    failures = 0
    for eco in core.ECOSYSTEMS:
        if eco != app.ecosystem:
            app.select_ecosystem(eco)
            root.update()
        width, bad = main_overflow(app)
        ok = not bad
        print(f"  {'ok  ' if ok else 'FAIL'} 主窗口[{eco:3s}] {width}px"
              f"{'' if ok else ' 越界控件: ' + str(sorted(set(bad))[:6])}")
        if not ok:
            failures += 1
    for name, opener in OPENERS:
        try:
            opener(app)
            dialog = list(app.dialogs.values())[-1]
            width, height, overflow, squashed = measure(dialog)
            ok = not overflow
            problem = "" if ok else f" 越界控件: {sorted(set(overflow))[:6]}"
            if squashed:
                problem += f" 被压扁: {squashed[:3]}"
            if width > right - left or height > bottom - top:
                ok = False
                problem += " 超出工作区"
            print(f"  {'ok  ' if ok else 'FAIL'} {name:9s} {width}x{height}{problem}")
            if not ok:
                failures += 1
            dialog.grab_release()
            dialog.destroy()
            root.update()
        except Exception as exc:  # noqa: BLE001
            failures += 1
            print(f"  ERR  {name}: {exc}")
    app.tray and app.tray.stop()
    root.destroy()
    return failures


def main():
    scales = [float(arg) for arg in sys.argv[1:]] or [1.0, 1.5, 2.0]
    total = sum(run(scale) for scale in scales)
    print(f"\n结果：{'全部通过' if total == 0 else f'{total} 个问题'}")
    return 0 if total == 0 else 1


if __name__ == "__main__":
    code = main()
    # 关窗时后台的版本查询线程可能还在跑，正常退出会在解释器析构阶段刷
    # "cannot schedule new futures after interpreter shutdown"，这里直接硬退出。
    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(code)
