# npm 源测速切换器 2.0

[![Release](https://img.shields.io/github/v/release/NANYAN-01/Quick-npm-ok?label=release)](https://github.com/NANYAN-01/Quick-npm-ok/releases/latest)
[![Python](https://img.shields.io/badge/python-3.9%2B-blue)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/license-MIT-green)](LICENSE)

一个 Windows 小工具：**测所有 npm 镜像源的延迟与下载速度、综合排名、一键切换**，并支持 **npm / pnpm / yarn** 和 **一键升级 npm**。
单文件 exe（约 11 MB），无需安装 Python 运行环境；切换源和升级需要本机已安装 Node.js。

## 下载与运行

到 [Releases](https://github.com/NANYAN-01/Quick-npm-ok/releases/latest) 下载最新的 `Quick-npm-ok-v2.0.0.exe`，双击即可运行。
首次运行若被 SmartScreen 拦截，点「更多信息 → 仍要运行」。

也可以从源码运行或自行打包，见下文。

## 界面

```
┌──────────────────────────────────────────────────────────────┐
│ 包管理器  [npm]  [pnpm]  [yarn]           npm v11.20.0 [升级 npm] │
│ 当前源  腾讯云  https://mirrors.cloud.tencent.com/npm/          │
│ 镜像源测速   排名 = 延迟 70% + 下载 30% ...                      │
├──────────────────────────────────────────────────────────────┤
│ 排名  源名称      地址               延迟 ms   下载 MB/s  状态    │
│ ①    华为云      repo.huaweicloud…   29 / 41   21.2      ✓      │
│ ②    淘宝        registry.npmmirror… 119 / 139 8.0       ✓      │
│ ...                                                          │
├──────────────────────────────────────────────────────────────┤
│ [开始测速] [使用最快源·华为云] [切换到选中源] [管理源]            │
│ 测速完成：6/6 个源可用，最快 华为云 · 24.3s                       │
└──────────────────────────────────────────────────────────────┘
```

### 功能

| 功能 | 说明 |
|---|---|
| 一键测速 | 延迟测 3 次取最快/中位（含 DNS+TCP+TLS）；下载测固定 1 MB tarball |
| 综合排名 | 延迟 70% + 下载 30% 归一化打分，① ② ③ … 排序，第一名绿色高亮 |
| 一键切换 | 写入对应包管理器的用户级配置；双击表格行也可以 |
| 使用最快源 | 测速后按钮直接给出第一名，点一下切换 |
| 多包管理器 | 自动检测 npm / pnpm / yarn，未安装的置灰 |
| 管理源 | 应用内添加 / 修改 / 删除 / 恢复默认，立即生效并持久化 |
| 升级 npm | 显示当前 / 最新版本，一键 `npm install -g npm@latest`，带实时日志；权限不足可"以管理员身份升级" |

### 排名算法

对可用的源：

```
延迟得分 = 最小中位延迟 / 本源中位延迟
下载得分 = 本源下载速度 / 最大下载速度
综合分   = 0.7 × 延迟得分 + 0.3 × 下载得分       // 0 ~ 1
```

按综合分从高到低排名，失败或无响应的源排在最后。选中某行时，底部会显示该源的排名和综合分。

### 包管理器说明

- **npm**：`npm config set registry <url>`，写入 `%USERPROFILE%\.npmrc`
- **pnpm**：`pnpm config set registry <url>`（与 npm 共用 `.npmrc`，所以设置后会同时影响 npm）
- **yarn**：自动区分经典版（`yarn config set registry`）和 Berry（`npmRegistryServer`）

### 升级 npm

- 只升级到 **latest**，不会降级；升级前会二次确认
- 依赖当前 registry 下载，若镜像同步滞后可能拿不到最新版
- Windows 上 Node 装在 `C:\Program Files` 时通常需要管理员权限；失败日志出现 `EPERM/EACCES` 时点「以管理员身份升级」，完成后点「重新检测版本」
- 升级过程不会关闭工具，日志实时输出

## 源码运行

```bash
python npm_mirror_tool.py             # 打开界面
python npm_mirror_tool.py --selftest  # 无界面自检，结果写入 %TEMP%\npm-mirror-selftest.json
```

仅依赖 Python 标准库（tkinter）。

## 重新打包 exe

需要 Python 3.9+ 和 PyInstaller：

```bash
pip install pyinstaller
build.bat
```

输出：`dist\npm源测速.exe`。

## 配置文件

自定义源列表保存在：

```
%APPDATA%\npm-mirror-tool\sources.json
```

格式：

```json
{
  "version": 1,
  "sources": [
    { "name": "华为云", "url": "https://repo.huaweicloud.com/repository/npm/" }
  ]
}
```

删除该文件即恢复内置 6 个源。可用环境变量 `NPM_MIRROR_TOOL_CONFIG_DIR` 覆盖配置目录（便于测试）。

## 命令行版

`npm-mirror-bench.mjs`（Node 18+）适合脚本/CI：

```bash
node npm-mirror-bench.mjs --runs 3
node npm-mirror-bench.mjs --json
```

## 常见问题

- **SmartScreen 提示"未知发布者"**：exe 未签名，点「更多信息 → 仍要运行」。
- **杀毒软件误报**：PyInstaller 单文件包偶尔被误报；可用 `--onedir` 打包或直接跑源码。
- **切换失败**：确认对应包管理器命令可用；写入的是用户级配置，不需要管理员。
- **pnpm 切换后 npm 也变了**：正常的，两者共用 `.npmrc`。
- **发布包**：`npm publish` 前记得切回官方源。

## GitHub 上的类似项目

| 项目 | 说明 |
|---|---|
| [Pana/nrm](https://github.com/Pana/nrm) | 最知名的 npm 源管理器（CLI），`nrm test` 可测速 |
| [YunYouJun/nnrm](https://github.com/YunYouJun/nnrm) | nrm 重写版，依赖更少 |
| [modern-magic/grm](https://github.com/modern-magic/grm) | Go 写的极简源管理器，单二进制 |
| [coderhsh/nrm-desktop](https://github.com/coderhsh/nrm-desktop) | 跨平台桌面版源管理器 |
| [fa1seut0pia/mirror-speed-test](https://github.com/fa1seut0pia/mirror-speed-test) | 中文镜像源测速工具（Web） |
| [Weidows/mirrorhub](https://github.com/Weidows/mirrorhub) | 多包管理器镜像测速与切换 |
| [open-services/public-registry-benchmarks](https://github.com/open-services/public-registry-benchmarks) | 公共 npm registry 基准数据 |

## 文件说明

| 文件 | 用途 |
|---|---|
| `npm_mirror_tool.py` | GUI 主程序（v2.0 源码） |
| `make_icon.py` | 生成 `app.ico` / `app.png`（纯标准库） |
| `build.bat` | 一键打包 exe |
| `dist\npm源测速.exe` | 打包后的程序（本地产物，未纳入 git；发布版见 Releases） |
| `npm-mirror-bench.mjs` | 命令行测速脚本 |
| `npm源测速.bat` | 命令行脚本的 Windows 启动器 |
