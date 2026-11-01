# Product Atelier — 新电脑开发交接

交接日期：2026-11-01。目的是保存当前开发状态并跨电脑续接，不是发布或验收通过声明。

## 1. 先认准分支与状态

| 项目 | 当前事实 |
| --- | --- |
| GitHub | https://github.com/cat644142986-afk/-.git |
| 开发分支 | `codex/excalidraw-infinite-canvas` |
| 固定交接标记 | `handoff/canvas-smart-workbench-2026-11-01`（标记包含本文的提交） |
| 本次保存前 HEAD | `aafb778c2363a41a1aaa72dd71e6299341a3c927`，其后的未提交实现全部纳入本交接 |
| 当前实现 | React Flow 节点式多媒体 Canvas replacement；独立 packaged prototype；完整 Gate **OPEN** |
| 数据库 schema | 10；NodeScene v2 是 scene codec 版本，不是数据库 schema 迁移 |
| 正式桌面版 | `4a0eb09275395951a3cd862850d128e5b48a3774` / contract `2026-09-23.1` / schema 10；本轮未替换 |
| 付费调用 / Promote | 本轮均为 0；不应自行追加 |
| Gitee 镜像 | 旧远端授权返回 403 / OAuth token expired；本次使用 GitHub，不依赖该镜像 |

不要从 `main` 或旧的 346e7261 / e4ff6840 等阶段重新开始。根 README 的商拍定位、schema 4、红绿灯等内容是历史介绍，不能覆盖本交接及用户后续要求。

## 2. 用户真正要的产品与当前工作

PA 是设计师个人工作台。当前优先任务是把节点式智能画布做到成熟、顺手、可连续使用；用户明确拒绝廉价缩减版和逐项指认 bug 的交付方式。对标的是 hero8152/Infinite-Canvas **smart-canvas** 的完整交互，包括软件 UI、节点/端口/连线、悬停删除、Prompt、图片编辑、多媒体、生成和工作流。视频属于目标范围，不能因早期“只做图片”的计划而删掉。

现有正式业务底座继续复用；新画布不是另一个 Provider/Task/Asset 系统。Home 仅暂停，未来仍是工作台总入口；不得永久删除未来 Home / Knowledge / Toolbox / Projects 的产品位置。

当前实施依据：[完整方案 v2 与逐按钮来源清单](docs/canvas-smart-workbench-implementation-plan-v2-2026-09-30.md)。先读该文第 1–10 节，附录按具体任务查，不必一次读取全部历史文档。旧 Gold Master v1 与旧 acceptance-v1 的视频/循环排除条款已被用户后续要求取代。正式验收前需据完整范围校正验收条款，不能用旧小范围 Gate 宣称完整交付。

上游审计 commit：`67f49e4b9a0ac090bad1f5bd62ae385f8fb5394e`，仓库 https://github.com/hero8152/Infinite-Canvas.git 。上游受限 source/CSS/assets 不包含在本仓库；需要复核实现时单独检出该 commit 只读研究，许可合适才移植。PA 使用 React Flow / React Aria / Fabric / fflate / TinyTag 等成熟组件及已有 Retake / BeatDesign 适配。见 [第三方说明](prototypes/canvas-replacement-v1/THIRD_PARTY_NOTICES.md) 和随仓库许可证。

## 3. 已保存的实现，避免重复开发

| 能力 | 代码入口与当前范围 |
| --- | --- |
| 节点与持久化 | `prototypes/canvas-replacement-v1/src/canvas-model.js`、`node-scene-codec.js`、`node-canvas-adapter.js`；`python/node_scene_contract.py`、`atelier_ledger.py`、`spatial_canvas_contract.py`。复用 Canvas ledger，scene 只存元数据和 asset ID，localStorage 只存小指针；旧恢复数据不删除。 |
| 节点交互 | `canvas-nodes.jsx`、`reference-edge.jsx`、`reference-model.js`、`overlay-layout.js`。React Flow 公开节点、端口、Bezier 连线、selection、resize 能力；输入与流程线语义分离。 |
| 创作状态 | `node-composer.jsx`、`creative-input.js`、`node-execution-adapter.js`。精确 Asset/Result 输入、每节点 draft、模型准入/Router、Governor、Task、Result/lineage；普通 deselect、对象切换及取消预览保留 draft。 |
| Prompt/模板 | `prompt-templates.js`、`prompt-template-panel.jsx`。Prompt 节点、个人模板 CRUD、分隔拆分预览；模板不是自动注入的 Skill。 |
| 设计执行 | `python/server.py` 节点执行适配。Canvas design prompt 不再自动注入旧商拍白底意图；Canvas design 路径保留 Provider 原始 PNG，避免默认增强/抠图/JPEG 二次处理；经典路径保留。 |
| 精细编辑 | `canvas-edit-dialog.jsx`、`asset-pixel-source.js`、`fabric-pixel-geometry.js`；`python/canvas_local_derivative.py`。Fabric 本地裁剪、mask、画笔、文字、透明扩边、grid；冻结精确输入/hash，输出已有 Asset/Result 和 lineage，不创建付费 Task。 |
| 视频/音频 | `media-frame.js`、`python/asset_store.py`、现有 video Task。视频/音频持久导入、原生播放器、首/当前/尾帧本地输出。视频生成目前只有 **offline-preview-v1 fixture**，不是根据输入进行真实 AI 视频合成。 |
| 多 Task/恢复 | `node-execution-adapter.js`、`workbench-dialog.jsx`、`prototype.jsx`。Task 投影、日志、恢复已有 Result；恢复不新建 attempt，删除场景视图不删除历史产物。 |
| 工作流迁移 | `workflow-transfer.js`、`workflow-transfer-panel.jsx`，Tauri `save_workflow_bundle`。有界 JSON/ZIP、精确子图、hash/安全路径检查；导入重绑定新 ID，以 workspace_source 入库，不伪造历史 Task，不自动执行。 |

主入口 `prototypes/canvas-replacement-v1/src/prototype.jsx` 较大，请先按符号搜索相关片段。正式旧画布在 `src/`，是底座/适配来源，不是新 UI 视觉母版。

## 4. 已有验证与明确边界

交接时在当前源码上实际完成：

- prototype 定向 Node 测试 **45/45**。
- `tests.test_node_canvas_api`、`test_node_scene_contract`、`test_canvas_local_derivative`、`test_node_audio_api`、`test_node_video_api`：**96/96**。故障注入用例输出 synthetic publish/cleanup fault 是预期日志，最终 unittest 为 OK。
- `git diff --check` 通过。
- 最近 r7 packaged 构建日志已确认成功；本次只交接，不重复 build/package，不作新的完整运行验收。

历史 packaged 证据和边界：[可随 Git 搬迁的 r6/r7 证据](docs/evidence/canvas-smart-workbench-r7/README.md)。证据目录原名日期为 20260930，不能把它们说成本次重新现场验证。

已现场覆盖：Fabric 精确图片加载和局部编辑；透明扩边参数直接应用且像素验证；video 首尾帧工具；尾帧再作为图片参考；offline 视频 Task；已删除 Result 视图不会重启自动复活；日志恢复已有 Result 并选中；原生 ZIP 导出/导入；音频导入与播放控件切换。真实 Provider 调用 0。

**没有完成完整产品验收，也没有交用户认可。** 特别是 r7 恢复 Result、音频导入、ZIP 导入后的整组再次重启，以及下列完整范围仍待完成。不要把上一版 Mature Canvas Baseline 和局部通过记录当成新 replacement 全部通过。

## 5. 从这里继续，不要重开项目

先核对源码与以下缺口；现存代码覆盖了的部分用证据更新，不能仅因文档写未完成就再写一遍。

1. **从零创作执行**：当前 generate-single 命令最小来源数仍为 1；空图片节点还不等于可执行 text-to-image。需复用现有执行链补正确输入合同/adapter，不能用商拍路径或假按钮冒充。
2. **完整多媒体/流程**：LLM 辅助缺准入文本 adapter/Command；loop/clip codec 允许不代表 UI/runtime 完成；有界循环、停止、预算、恢复、片段时间轴仍缺。视频当前只离线 fixture，真实 adapter/admission/成本证明独立处理，任何付费先申请。
3. **现有体验缺口**：Skill/Profile 的节点选择接线、对比预览、完整 resize/grid join/软边/椭圆/裁剪预设等依完整按钮清单补齐；不扩另一套数据系统。资产目录与媒体失败恢复边界也要逐项覆盖。
4. **已知细节**：workflow export 的 `catch(reason) { setError(reason.message) }` 会丢失 Tauri 字符串拒绝（如用户取消保存）；文件选择后 popover 焦点/Escape 需再核对；Task 回填在视口外时已有日志定位入口，不能随意自动抢视口。
5. **完整 delta 验收**：连贯导入→连接→Prompt/参数→Governor 取消/确认（offline）→多结果→再参考→Fabric→导出→关闭重启；错误/取消路径；100/125/150% DPI 和小窗口；30/100 混合对象基础性能。先复用已经保存的有效证据，再补缺口。
6. **正式发布前另外处理**：`tools/Build-Sidecar.ps1` manifest source inventory 尚未列出本轮新 `node_scene_contract.py` / `canvas_local_derivative.py`；PyInstaller spec 已包含它们，但发布身份覆盖须单独补齐并验证。当前不是 release-ready/Validated 候选。

用户已要求不要按微小阶段反复停下让他找问题；内部可分批验证和提交，做到完整可用 packaged checkpoint 再交审核。禁止自动 Promote。本交接不会继续这些实现。

## 6. 新电脑环境和最短启动路径（Windows）

可检出任意本地目录；下面命令在仓库根目录执行。已使用的环境：Windows、Node 24.19.0、Python 3.12.10、Rust 1.97.1；prototype 声明 Node >=20。锁文件要保留，先 `npm ci`，不要顺手升级依赖。此组合是旧机实测，未宣称在新机已验证。

需要 Git、Node/npm、Python 3.12 x64、Rust/rustup、Windows WebView2 Runtime。当前 Rust 是 Windows GNU 工具链；`.cargo/config.toml` 的 linker/ar 写为 `C:\mingw64\bin`。新电脑安装相同 MinGW-w64 或将路径调整到真实安装处，不要误把缺工具当产品 bug。macOS 上可做部分前端/离线开发，但当前 Credential Manager、打包及桌面验收是 Windows 实现；不可声称原样跨平台完成。

```powershell
git clone --branch codex/excalidraw-infinite-canvas https://github.com/cat644142986-afk/-.git ProductAtelier-Desktop
Set-Location ProductAtelier-Desktop
git fetch origin --tags
git rev-parse HEAD
git rev-parse 'handoff/canvas-smart-workbench-2026-11-01^{commit}'
# 首次交接两者应一致；以后分支可能前进，保留工作，不盲目 reset。
npm.cmd ci
npm.cmd ci --prefix prototypes/canvas-replacement-v1
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r python/requirements-test.txt -r python/requirements-build.txt
# 当前 PowerShell 会话使用该 venv（Build-Sidecar.ps1 调用 python）。
$env:PATH = (Join-Path $PWD '.venv\Scripts') + ';' + $env:PATH
```

定向检查（新机安装验证可以重跑一次；之后按改动做 delta，不跑无关全仓库审计）：

```powershell
npm.cmd --prefix prototypes/canvas-replacement-v1 test
python -m unittest tests.test_node_canvas_api tests.test_node_scene_contract tests.test_canvas_local_derivative tests.test_node_audio_api tests.test_node_video_api -q
```

仅静态前端预览：`npm.cmd --prefix prototypes/canvas-replacement-v1 run dev`，默认 127.0.0.1:4187。普通浏览器缺 Tauri/sidecar，不代表媒体/持久化/原生保存已验证，正式接续应使用隔离 packaged app。

构建隔离候选（不使用会进入正式发布流程的 `tools/dev.ps1` 或 portable Promote 脚本）：

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File tools/Build-Sidecar.ps1
if ($LASTEXITCODE -ne 0) { throw 'Sidecar build failed' }
npx.cmd --no-install tauri build --no-bundle --features custom-protocol --config src-tauri/tauri.canvas-foundation.conf.json
if ($LASTEXITCODE -ne 0) { throw 'Prototype build failed' }
```

默认 EXE 为 `src-tauri/target/release/product-atelier.exe`；设置了 `CARGO_TARGET_DIR` 时按构建输出定位，不硬编码旧电脑目录。创建新的 `build/` 候选目录，复制 EXE 与完整 `src-tauri/bin/python-server` 目录到同一级，不能只复制 sidecar exe。构建需要可下载 npm/pip/Cargo 依赖的网络；本地抠图权重等不在 Git。

隔离启动示例（先把新候选 EXE 的路径填入 `$paCandidateExe`）：

```powershell
$paTestRoot = Join-Path $env:TEMP ('ProductAtelier-app-test-' + [guid]::NewGuid().ToString('N'))
python tools/seed_node_canvas_acceptance.py --root $paTestRoot
if ($LASTEXITCODE -ne 0) { throw 'Fixture seed failed' }
$env:PRODUCT_ATELIER_CANDIDATE_ISOLATION = '1'
$env:PRODUCT_ATELIER_DATA_DIR = $paTestRoot
$env:PRODUCT_ATELIER_WEBVIEW_DATA_DIR = Join-Path $paTestRoot 'webview2-user-data'
$env:PRODUCT_ATELIER_LEGACY_CONFIG = Join-Path $paTestRoot 'no-legacy-config.json'
$env:PRODUCT_ATELIER_KNOWLEDGE_BASE = Join-Path $paTestRoot 'no-knowledge-vault'
$env:PRODUCT_ATELIER_CREDENTIAL_NAMESPACE = 'ProductAtelier-Handoff-Isolated'
# $paCandidateExe = 完整的新候选 EXE 路径
Start-Process -FilePath $paCandidateExe -WorkingDirectory (Split-Path -Parent $paCandidateExe) -WindowStyle Hidden
```

上述环境只放在当前测试终端；结束该终端，不写机器级变量。seed 创建人工设计图、透明图、静音 WAV 和仓库自带 offline WebM，不读真实密钥，不创建付费 Task。新机没 Catalog 时模型列表可能为空；应通过现有 ProviderConnection 用户授权后只读同步，不造假 eligible，也不把缺账户状态误判成画布功能失败。完成离线工作不需要 API Key。

## 7. Git 包含与不包含什么

**包含**：全部当前源码、Git 历史、lockfiles、测试/人工 fixtures、审计/产品计划/历史 evidence 文档、许可证、本次精选非敏感 packaged 截图及 workflow ZIP、本交接及项目规则。

**不包含**：API Key / Windows Credential Manager、SQLite 用户账本、私人图片/视频/音频、个人知识库、WebView 用户资料、`node_modules` / `.venv`、模型权重、正式安装包/rollback 和 `build/`。不要把这些强行 `git add -f`；新电脑需要重新安装依赖、构建，以及通过现有连接 UI 重新授权。DPAPI/凭据不能靠复制旧系统文件安全地迁到另一台电脑。

如果还要迁移旧个人作品和正式运行现场，在旧机卸载/清盘之前另外保留安全的离线备份：

- `%APPDATA%\ProductAtelier` 默认正式数据根，以及用户另选的成品输出路径；先关闭对应正式应用和 sidecar 再完整复制数据库及其资产，或用项目已有一致性备份工具。不要只复制 SQLite 主文件而遗漏 WAL/关联媒体。
- 旧机 `D:\ProductAtelier-Desktop\release` 的正式包和永久 rollback；新机路径不同，旧 `.lnk` 不能原样当新快捷方式。
- `D:\知识库` 和用户自有素材库单独搬迁，本轮没有读取或上传它们。
- 仅供继续本次现场复验的隔离根：`%LOCALAPPDATA%\Temp\ProductAtelier-app-test-c85bb0be23484434be768ea66949eb31`。本轮未上传其数据库。新机可用 seed 重建人工现场，不能声称恢复了旧 durable Task 身份。
- 旧 localStorage 救援备份：旧仓库 `build/canvas-smart-v2-legacy-recovery-20260930/Local Storage/leveldb`。约 52.8 MB，未放 Git；不要因新 scene 已持久化就删掉它。历史未成功落盘的对象不保证可恢复。

此列表是迁移边界，**不是这些私人文件已备份的声明**。开发源码和接续上下文已纳入 Git；私人运行数据另行迁移。

## 8. 新 Codex 的第一步

读 `AGENTS.md`、本文、v2 方案主体和证据 README；检查 branch / HEAD / status，确认没有用户新改动。不要遍历旧聊天，不要重做开源审计、重写路线或把技术原型说成用户已验收。用现有代码核对第 5 节，从实际未完成处继续。每项结果区分实现、自动化、packaged 现场、用户审核及正式发布状态。
