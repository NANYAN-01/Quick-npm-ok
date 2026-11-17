# -*- coding: utf-8 -*-
"""界面冒烟测试：构造主窗口、逐个打开对话框、检查关键控件，然后退出。

用法：python tools/smoke_ui.py
不会联网（更新检查与托盘关闭），不会安装任何东西。
"""

from __future__ import annotations

import os
import sys
import tempfile
import traceback

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

CONFIG_DIR = os.path.join(tempfile.gettempdir(), "quick-npm-ok-smoke")
os.makedirs(CONFIG_DIR, exist_ok=True)
with open(os.path.join(CONFIG_DIR, "config.json"), "w", encoding="utf-8") as handle:
    handle.write('{"tray": false, "update_check": false, "language": "zh"}')
os.environ["NPM_MIRROR_TOOL_CONFIG_DIR"] = CONFIG_DIR

import tkinter as tk  # noqa: E402

from app import APP_VERSION  # noqa: E402
from app import core, ui  # noqa: E402

failures = []


def check(name, condition, detail=""):
    if condition:
        print(f"  ok   {name}")
    else:
        print(f"  FAIL {name} {detail}")
        failures.append(name)


def main():
    root = tk.Tk()
    app = ui.App(root)
    root.update()

    print("[主窗口]")
    check("标题含版本号", APP_VERSION in root.title(), root.title())
    check("包管理器按钮 3 个", len(app.manager_buttons) == 3)
    check("表格行数等于源数量", len(app.tree.get_children()) == len(app.sources),
          f"{len(app.tree.get_children())} vs {len(app.sources)}")
    check("底部链接存在", app.update_link.winfo_exists() == 1)
    check("当前源已读取", app.current_label.cget("text") != "", app.current_label.cget("text"))

    print("[pip 生态]")
    app.select_ecosystem("pip")
    root.update()
    check("pip 管理器按钮 1 个", len(app.manager_buttons) == 1, str(sorted(app.manager_buttons)))
    check("当前管理器为 pip", app.manager == "pip", app.manager)
    check("pip 源数量正确", len(app.sources) == len(core.default_sources("pip")),
          f"{len(app.sources)} vs {len(core.default_sources('pip'))}")
    check("表格已切到 pip 源", len(app.tree.get_children()) == len(app.sources))
    check("范围含虚拟环境", "虚拟环境" in app._scope_labels, str(app._scope_labels))
    check("pip 模式下隐藏升级按钮", not app.btn_upgrade.winfo_manager())
    app.open_sources()
    pip_sources = app.dialogs.get("sources")
    check("pip 管理源打开", pip_sources is not None and pip_sources.winfo_exists())
    if pip_sources is not None:
        check("pip 源列表已填充", len(pip_sources.tree.get_children()) == len(app.sources))
        root.update()
        pip_sources.destroy()
    app.select_ecosystem("npm")
    root.update()
    check("切回 npm 后按钮 3 个", len(app.manager_buttons) == 3, str(sorted(app.manager_buttons)))
    check("切回 npm 后显示升级按钮", bool(app.btn_upgrade.winfo_manager()))

    print("[部署对话框]")
    app.open_env()
    env = app.dialogs.get("env")
    check("环境对话框打开", env is not None and env.winfo_exists())
    if env is not None:
        check("三行工具", len(env.buttons) == 3, str(sorted(env.buttons)))
        root.update()
        env.destroy()

    print("[管理源]")
    app.open_sources()
    sources = app.dialogs.get("sources")
    check("管理源打开", sources is not None and sources.winfo_exists())
    if sources is not None:
        check("源列表已填充", len(sources.tree.get_children()) == len(app.sources))
        root.update()
        sources.destroy()

    print("[关于 / 设置 / 历史 / 作用域 / 项目]")
    for opener, key in ((app.open_about, "about"), (app.open_settings, "settings"),
                        (app.open_history, "history"), (app.open_scopes, "scopes"),
                        (app.open_project, "project"), (app.open_upgrade, "upgrade")):
        opener()
        dialog = app.dialogs.get(key)
        check(f"{key} 打开", dialog is not None and dialog.winfo_exists())
        root.update()
        if dialog is not None and dialog.winfo_exists():
            dialog.destroy()
        root.update()

    print("[切换语言渲染]")
    from app.i18n import keys, t
    missing = [k for k in keys() if not t(k, "zh") or not t(k, "en")]
    check("中英文案都不为空", not missing, str(missing[:5]))
    check("英文标题可用", t("main.title", "en") == "npm / pip Mirror Speed Test")

    print("[关闭]")
    app._closing = True
    if app.tray:
        app.tray.stop()
    root.destroy()
    return 1 if failures else 0


if __name__ == "__main__":
    try:
        code = main()
    except Exception:  # noqa: BLE001
        traceback.print_exc()
        code = 1
    print("\n结果：" + ("全部通过" if code == 0 else f"{len(failures)} 项失败"))
    sys.exit(code)
