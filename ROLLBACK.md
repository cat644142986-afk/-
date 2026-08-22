# Product Atelier 回滚说明

## 2026-08-22 主路线实施前基线

- Git 标签：`baseline-2026-08-22-before-master-roadmap`
- 标签提交：`eb76dedcb78aad7cd520176745989361c6052e65`
- 实施分支：`codex/master-roadmap-phase-0-1`
- 用户账本备份：`D:\ProductAtelier-Backups\phase0-2026-08-22\atelier-pre-master-roadmap.sqlite3`
- 账本备份 SHA-256：`853BA03FB446A0DBF511DDDCD74C7734E70221F5B340D6A9F59D0E0B10396738`
- 备份时账本状态：schema v2、`integrity_check=ok`、0 个外键异常。

恢复用户账本前必须先退出 `Product Atelier.exe` 与 `python-server.exe`，保留当前
`%APPDATA%\ProductAtelier\atelier.sqlite3` 的第二份副本，再用上方备份替换。不要在
应用运行中直接复制 WAL 数据库文件。

从主路线实施前源码创建独立恢复分支：

```powershell
git -C D:\ProductAtelier-Desktop switch -c restore/before-master-roadmap baseline-2026-08-22-before-master-roadmap
```

这份标签早于阶段0的迁移修复与 sidecar 指纹工具；恢复后如需构建旧版，应保留当时
的发布包，不要把新版数据库交给不支持其 schema 的旧程序写入。

## 2026-08-22 schema v3 实施前基线

- Git 标签：`baseline-2026-08-22-before-schema-v3`
- 标签提交：`01b533619b3539faa9e2a423c3a448c5a76c96d3`
- 标签包含：Phase 0 恢复/发布链加固与 Phase 1 可交互原型。
- 用户正式账本尚未由开发中的 schema v3 sidecar 打开；当前可恢复副本仍是上方
  `phase0-2026-08-22` 中通过校验的 schema v2 数据库。

恢复 schema v3 之前的源码：

```powershell
git -C D:\ProductAtelier-Desktop switch -c restore/before-schema-v3 baseline-2026-08-22-before-schema-v3
```

schema v3 第一次打开旧账本时会在原数据库旁创建
`atelier.sqlite3.backup-v2-<UTC>-<随机值>.sqlite3`。若升级后需要恢复旧程序，必须先
退出桌面应用和 sidecar，再保留当前 v3 文件的副本，并恢复该自动生成的 v2 备份；
不要让只支持 schema v2 的旧程序写入 v3 数据库。

已使用实施前用户备份的独立副本完成真实迁移演练：

- 演练目录：`D:\ProductAtelier-Backups\phase2-v3-rehearsal-2026-08-22`
- 升级副本：`atelier-v2-copy.sqlite3`，升级后 schema v3、`integrity_check=ok`、0 个外键异常、3 个素材域、4 个工作流草稿。
- 自动生成的 v2 回滚文件：`atelier-v2-copy.sqlite3.backup-v2-20260822T091154958219Z-c85ea672.sqlite3`
- 自动回滚文件 SHA-256：`853BA03FB446A0DBF511DDDCD74C7734E70221F5B340D6A9F59D0E0B10396738`，与实施前原备份完全一致。

## 当前可靠基线

- Git 标签：`baseline-2026-08-21-pre-workspace-refactor`
- 用途：记录工作台/批处理/知识与记忆闭环重构前的当前可运行版本。
- 离线源码包：`D:\ProductAtelier-Backups\ProductAtelier-baseline-2026-08-21-pre-workspace-refactor.zip`
- 校验清单：与离线源码包同目录的 `.sha256.txt` 文件。

## 安全恢复方式

不要直接覆盖当前开发分支。先从基线创建独立恢复分支：

```powershell
git -C D:\ProductAtelier-Desktop switch -c restore/baseline-2026-08-21 baseline-2026-08-21-pre-workspace-refactor
```

恢复后执行验证：

```powershell
cd D:\ProductAtelier-Desktop
npm run build
python -m py_compile python\server.py python\atelier_ledger.py python\knowledge_engine.py python\memory_engine.py
python tools\test_memory_smoke.py
cargo check --manifest-path src-tauri\Cargo.toml
```

## 基线范围

基线包含当前前端、Tauri 桌面壳、Python 服务、知识编译、成长记忆、创作账本和验证脚本。

以下内容有意排除：API Key、本机配置、SQLite 运行数据、生成结果、模型缓存、构建产物、历史截图、旧备份目录以及未被当前入口引用的 v35-v133 试验 UI 层。它们不属于可复现源码，并可能包含隐私信息或造成样式冲突。
