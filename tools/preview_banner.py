# -*- coding: utf-8 -*-
"""预览「未安装 npm」提示条：仅在测试里假装 npm 不存在，不改动系统。

用法：python tools/preview_banner.py
窗口会显示 20 秒，方便截图后自动关闭。
"""

from __future__ import annotations

import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

CONFIG_DIR = os.path.join(tempfile.gettempdir(), "qno-banner")
os.makedirs(CONFIG_DIR, exist_ok=True)
with open(os.path.join(CONFIG_DIR, "config.json"), "w", encoding="utf-8") as handle:
    handle.write('{"tray": false, "update_check": false}')
os.environ["NPM_MIRROR_TOOL_CONFIG_DIR"] = CONFIG_DIR

import tkinter as tk  # noqa: E402

from app import core, ui  # noqa: E402


def main():
    real = core.tool_exists
    real_version = core.tool_version

    def fake(name):
        if name in ("npm", "pnpm", "yarn"):
            return False
        return real(name)

    core.tool_exists = fake
    core.get_registry = lambda *_args, **_kwargs: ""
    core.tool_version = lambda name: "" if name in ("npm", "pnpm", "yarn", "node") else real_version(name)

    root = tk.Tk()
    app = ui.App(root)
    app.banner.grid()
    app.status.configure(text=ui.tr("main.npm_missing"))
    root.after(20000, root.destroy)
    root.mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
