#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""npm 源测速切换器 —— 程序入口。

直接运行
--------
    python npm_mirror_tool.py                # 打开界面
    python npm_mirror_tool.py --selftest     # 命令行测速，结果写到 JSON
    python npm_mirror_tool.py --version

打包 exe
--------
    python build.py
"""

from __future__ import annotations

import json
import os
import sys

from app import APP_NAME, APP_NAME_EN, APP_VERSION


def _enable_dpi_awareness() -> None:
    """让窗口在高分屏上不发虚；老系统上静默跳过。"""
    if os.name != "nt":
        return
    try:
        import ctypes

        # 2 = PER_MONITOR_AWARE_V2（Win10 1703+），失败再退回系统级
        try:
            ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
            return
        except Exception:  # noqa: BLE001
            pass
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except Exception:  # noqa: BLE001
            ctypes.windll.user32.SetProcessDPIAware()
    except Exception:  # noqa: BLE001
        pass


def _set_app_id() -> None:
    """独立的任务栏图标分组，避免和 python.exe 混在一起。"""
    if os.name != "nt":
        return
    try:
        import ctypes

        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("NANYAN01.QuickNpmOk")
    except Exception:  # noqa: BLE001
        pass


def run_gui() -> int:
    import tkinter as tk

    from app.ui import App, resource_path

    root = tk.Tk()
    icon = resource_path("app.ico")
    if os.path.exists(icon):
        try:
            root.iconbitmap(default=icon)
        except Exception:  # noqa: BLE001
            pass

    App(root)
    root.mainloop()
    return 0


def run_selftest(output: str | None = None) -> int:
    """不打开界面，跑一遍测速并把结果写成 JSON，便于 CI 冒烟测试。"""
    from app import core

    proxy = core.detect_proxy()
    results = core.run_benchmark(core.sources(), proxy=proxy)
    order = core.rank_results(results)
    rows = core.results_to_rows(results, core.sources())
    payload = {
        "version": APP_VERSION,
        "proxy": core.proxy_label(proxy),
        "fastest": core.sources()[order[0]]["name"] if order else None,
        "rows": rows,
    }
    text = json.dumps(payload, ensure_ascii=False, indent=2)
    if output:
        with open(output, "w", encoding="utf-8") as handle:
            handle.write(text + "\n")
        print(f"已写入 {output}（{sum(1 for r in rows if r['ok'] == '1')}/{len(rows)} 可用）")
    else:
        print(text)
    return 0 if order else 1


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)

    if "--version" in argv or "-V" in argv:
        print(f"{APP_NAME_EN} {APP_VERSION}")
        return 0

    if "--selftest" in argv:
        index = argv.index("--selftest")
        output = argv[index + 1] if len(argv) > index + 1 else None
        return run_selftest(output)

    if "--help" in argv or "-h" in argv:
        print(__doc__)
        return 0

    _enable_dpi_awareness()
    _set_app_id()
    try:
        return run_gui()
    except Exception as exc:  # noqa: BLE001
        if os.name == "nt":
            try:
                import ctypes

                ctypes.windll.user32.MessageBoxW(
                    None, f"{APP_NAME} 启动失败：\n{exc}", APP_NAME, 0x10
                )
            except Exception:  # noqa: BLE001
                pass
        raise


if __name__ == "__main__":
    sys.exit(main())
