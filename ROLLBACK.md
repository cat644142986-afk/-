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

## 2026-08-24 自定义交付目录检查点

- Git 标签：`checkpoint-2026-08-24-custom-output-root`
- 前一检查点：`checkpoint-2026-08-24-knowledge-path`（提交 `eff925a`）
- 数据库 schema：仍为 v3，没有数据库迁移。
- 新增配置字段：`output_root`、`known_output_roots`；旧版本会忽略这些 JSON 字段，不会自动删除外部成品。
- sidecar contract：`2026-08-24.1`。

从本检查点创建恢复分支：

```powershell
git -C D:\ProductAtelier-Desktop switch -c restore/custom-output-root checkpoint-2026-08-24-custom-output-root
```

若要回到功能实施前源码，应另建分支，不要覆盖当前分支：

```powershell
git -C D:\ProductAtelier-Desktop switch -c restore/before-custom-output-root checkpoint-2026-08-24-knowledge-path
```

回退前必须先退出 `Product Atelier.exe` 与 `python-server.exe`。本功能不移动账本、缓存或学习证据，因此不需要恢复数据库；外部交付目录中的成品也不会被代码回退删除。

注意：功能实施前的 sidecar 只允许从内部 `output` 根读取结果。若账本已经登记外部目录成品，旧版界面可能无法预览这些结果，尽管文件仍安全存在。因此已使用自定义交付目录的数据环境优先回滚到本检查点；确需运行旧版时，先保留 `%APPDATA%\ProductAtelier` 和所有外部交付目录的完整副本，并准备结果路径兼容迁移，不要直接覆盖账本路径。

## 2026-08-24 体验外壳 2.0 与动态知识原型

- 实施前标签：`baseline-2026-08-24-before-experience-shell2-prototype`
- 标签提交：`236525b89fb5ebfd761b90a19cc9e1c08c50bc9e`
- 原型检查点标签：`checkpoint-2026-08-24-experience-shell2-prototype`
- 原型目录：`prototypes/experience-shell2/`
- 交互与验收说明：`docs/experience-shell2-knowledge-motion-prototype-2026-08-24.md`
- 用户账本在线备份：`D:\ProductAtelier-Backups\experience-shell2-2026-08-24\atelier-before-experience-shell2.sqlite3`
- 备份 SHA-256：`F2F78087B14EEACA59578C55B8822F5CF122225AFA02BFAA2B18D758ECF07A02`
- 备份检查：`integrity_check=ok`、0 个外键异常；不包含 API Key。

本检查点只新增独立静态原型、测试与文档，没有修改生产 `src/`、Python sidecar、Rust 壳或 schema。回看原型前状态可创建独立恢复分支：

```powershell
git -C D:\ProductAtelier-Desktop switch -c restore/before-experience-shell2-prototype baseline-2026-08-24-before-experience-shell2-prototype
```

原型不会写入用户账本，通常无需恢复数据库。只有用户正式账本本身另有损坏时才使用上方备份；恢复前仍必须退出桌面程序和 sidecar，并先保留当前数据库副本。

## 2026-08-24 生产体验外壳实施前基线

- 实施前标签：`baseline-2026-08-24-before-production-experience-shell`
- 标签提交：`949d34f`
- 生产检查点标签：`checkpoint-2026-08-24-production-experience-shell`
- 实现与验收说明：`docs/phase-5-production-experience-shell-checkpoint-2026-08-24.md`
- 数据库 schema：仍为 v3；本检查点没有数据库迁移。

回看生产外壳实施前状态时创建独立恢复分支：

```powershell
git -C D:\ProductAtelier-Desktop switch -c restore/before-production-experience-shell baseline-2026-08-24-before-production-experience-shell
```

回到本次生产检查点时创建独立恢复分支：

```powershell
git -C D:\ProductAtelier-Desktop switch -c restore/production-experience-shell checkpoint-2026-08-24-production-experience-shell
```

回退前先退出 `Product Atelier.exe` 与 `python-server.exe`。本次变更不迁移或删除用户账本、素材、结果与外部交付目录，因此通常只需切换源码；仍应先保留 `%APPDATA%\ProductAtelier` 和外部成品目录的副本。回到实施前版本后会恢复旧生产 UI，但既有 schema v3 数据和任务记录保持可读。

## 2026-08-28 便携版提升事务恢复

`tools/dev.ps1` 现在只在隔离候选包通过两道 smoke 后触发正式提升。事务日志固定为
`build\portable-promotion-transaction.json`，外部完整备份在
`D:\ProductAtelier-Backups\release-before-<时间>-<Git短哈希>`，最终证据同时记录在备份旁与
`build\last-portable-promotion.json`。不要手工删除 transaction、`previous` 或 `recovery` 目录。

若脚本中断且 transaction 仍存在，先退出该正式目录下的桌面应用与 sidecar，再只读查看身份与阶段：

```powershell
$tx = Get-Content -Raw build\portable-promotion-transaction.json | ConvertFrom-Json
$tx | Select-Object phase, git_commit, transaction_id, portable_dir, backup_dir
```

- `phase` 为 `prepared / backed_up / candidate_copied / previous_moved / promoted`时，如果正式 smoke 未通过，使用该日志中的精确身份回滚：

  ```powershell
  python tools\portable_release.py rollback --project-root $PWD.Path --transaction build\portable-promotion-transaction.json --reason "resume interrupted release" --git-commit $tx.git_commit --transaction-id $tx.transaction_id
  ```

- `phase` 为 `finalizing / finalized`时已跨过最终确认点，不得反向猜测回滚；重跑可幂等的 finalize：

  ```powershell
  python tools\portable_release.py finalize --project-root $PWD.Path --transaction build\portable-promotion-transaction.json --git-commit $tx.git_commit --transaction-id $tx.transaction_id
  ```

恢复器只会移动固定项目路径与该 transaction ID 派生的目录；如果 formal、previous、backup 或 recovery 出现未知文件树哈希，它会保留现场并拒绝删除。此时不要强制清理，应保留 transaction、`release\` 和对应备份后再审计。这套目录事务不操作 `%APPDATA%\ProductAtelier` 的账本、Key、素材或生成结果。

## 2026-08-28 “统一状态与冲突恢复”正式便携检查点

- 正式源码提交：`9eb71a8c50e6f916954d1871c40ba39a6312ae3b`
- Git 标签：`checkpoint-2026-08-28-unified-status-recovery-portable`
- 正式目录：`D:\ProductAtelier-Desktop\release\ProductAtelier-Portable`
- 正式 EXE SHA-256：`FFCE84EC4A0B84798371ACDA1C147B88CAF01ACC1A5EDEFCCD0B2536F4FFF9D6`
- sidecar SHA-256：`6E57B211524539E4734D53DB5866174C44648280154FA989FA103E3DE4B360DA`
- manifest SHA-256：`32AF9473E6586691D8BD7554C77254BAA652C5FC0B0A68BC3C7F2A8EF99AF248`
- 正式目录 tree SHA-256：`F4181478B368D466819432B94F6C893CBB995632A869812A43C115C9653A3BC4`
- 已 finalized 事务：`aca596a42c4849afadaed4dca8704bac`
- 上一正式目录完整备份：`D:\ProductAtelier-Backups\release-before-20260828-175633-9eb71a8c50e6`
- promotion evidence SHA-256：`35FDD4AC7510BB38C3FA27BC1234B6C1C83AB9D9A353F76020C4B123EC68C3A8`

该事务已完成 finalize，活动 transaction 文件已经清除；不要尝试伪造 transaction ID 调用 rollback。若只需回看本检查点源码，创建独立分支：

```powershell
git -C D:\ProductAtelier-Desktop switch -c restore/unified-status-recovery checkpoint-2026-08-28-unified-status-recovery-portable
```

若未来正式包需要降级，应先退出正式 EXE 与其 `python-server.exe`，保留当前正式目录和 `%APPDATA%\ProductAtelier` 的新副本，再从目标 Git 检查点重新构建候选并走完整 `tools/dev.ps1` 发布事务；不要把上方备份直接覆盖到运行中的正式目录。上方备份保存的是前一正式包，用户账本、Key、素材与成品不在该目录事务内。

同提交 NSIS 安装候选 SHA-256 为 `5750C444E6F4FC85BCCA2FA5C393BC2DA02E25B5E4E3A04DE664CD78B8647EEF`，已通过隔离安装 smoke，但尚未 Authenticode 签名；对外分发前应先签名并重新记录哈希，不能把未签名文件当最终公开发布物。
