# Product Atelier：macOS 零文件接续入口

## 一条命令完成初始化

在苹果电脑打开“终端”，完整粘贴下面这一行并回车：

```bash
/bin/bash -c "$(curl -fsSL 'https://raw.githubusercontent.com/cat644142986-afk/-/refs/heads/codex/master-roadmap-phase-0-1/tools/bootstrap-macos.sh')"
```

这条命令不依赖 Windows 电脑的任何本地文件。它会：

1. 检查或安装 Apple Command Line Tools、Homebrew、Node.js 20+、Python 3.12 和 Rust。
2. 从 GitHub 克隆 `codex/master-roadmap-phase-0-1` 到 `~/ProductAtelier-Desktop`。
3. 建立仓库内 `.venv`，通过 `package-lock.json` 安装确定版本的前端依赖。
4. 执行前端测试、Python 测试、Vite production build 和 macOS Rust check。
5. 把实际分支、提交和验证时间写入不进入 Git 的 `.macos-bootstrap-state`，方便接手者核对环境。

脚本可以安全重跑：已有仓库只做 fast-forward 更新；发现未提交修改、错误仓库或同名非仓库目录时会停止，不会覆盖文件。首次安装 Apple 工具时需要在系统弹窗中确认；安装 Homebrew 时可能需要输入当前 Mac 的管理员密码。

## Git 中已经包含的接续材料

- 唯一总计划：`docs/product-atelier-master-execution-plan-2026-08-22.md`
- 当前状态与唯一游标：`docs/handoff-2026-08-28.md`
- schema v3 契约：`docs/ledger-schema-v3.md`
- 回滚原则：`ROLLBACK.md`
- 前端、Python、Rust、测试、锁文件以及本脚本

Git 不包含且不应传输：API Key、`.env`、SQLite 账本、用户图片、运行日志、Windows EXE、sidecar 和本机备份。新 Mac 第一次打开软件后在设置页自行录入 API Key；接续开发和全部离线测试不需要 Key，也不会产生生图费用。

## 在 Mac 上打开开发版

初始化全绿后执行：

```bash
cd ~/ProductAtelier-Desktop
source .venv/bin/activate
npm run tauri dev
```

如果只改前端，也可执行 `npm run dev`；如果只做离线门禁，执行：

```bash
cd ~/ProductAtelier-Desktop
source .venv/bin/activate
npm run test:frontend
python -m unittest discover -s tests -p 'test_*.py'
npm run build
cargo check --manifest-path src-tauri/Cargo.toml --features custom-protocol
```

## Mac 可以继续什么，不能批准什么

Mac 可以直接继续：前端、FastAPI/SQLite、跨平台 Rust 源码、契约、测试、无障碍、状态恢复、Phase 7/8/9 中不依赖 Windows 壳的工作。提交仍推送到 `codex/master-roadmap-phase-0-1`，每次开始前先 `git pull --ff-only`。

Mac 不能替代以下 Windows 正式发布证据：

- `tools/dev.ps1`、`Build-Sidecar.ps1`、`Test-Portable.ps1`、`Test-Portable-App.ps1`
- PyInstaller Windows sidecar、`.exe`、NSIS、Windows 桌面快捷方式
- 100–200% Windows DPI、DWM 圆角以及 Windows 覆盖升级验证

因此当前“统一状态与冲突恢复”可以在 Mac 上继续检查或修正源码，但不能仅凭 Mac 测试标记为“已提升正式便携版”。该正式提升仍要回到 Windows 完成。Mac 若要立即推进新功能，应选择总计划中不改变发布基线的源码任务，独立提交并在交接说明中标记“待 Windows 正式门禁”。

## 给另一台电脑上的 Codex 的开工指令

初始化完成后，可以把下面一段原样发给 Codex：

```text
请在 ~/ProductAtelier-Desktop 工作。先完整阅读 docs/product-atelier-master-execution-plan-2026-08-22.md、docs/handoff-2026-08-28.md、docs/macos-zero-state-handoff-2026-08-28.md、docs/ledger-schema-v3.md 和 ROLLBACK.md，再核对当前分支、HEAD、git status 与 .macos-bootstrap-state。这个 Mac 没有 Windows 发布环境：可以继续跨平台源码和离线门禁，但不得把 macOS 验收写成 Windows 正式便携版验收，不得提交 Key、用户数据或构建产物，不调用付费生图。先报告可在 Mac 执行的当前计划游标和平台阻断，再从总计划继续。
```
