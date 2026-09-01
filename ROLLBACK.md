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

## 2026-08-28 语义选物确认源码检查点

- Git 标签：`checkpoint-2026-08-28-semantic-cutout-confirmation-source`
- 性质：源码与离线门禁检查点；不是新的正式 EXE、sidecar、NSIS 或桌面快捷方式发布。
- sidecar 源码合同：`2026-08-28.1`
- 数据库 schema：仍为 v3；复用既有 `mask_state`，没有迁移、删除或覆盖用户账本。
- 上一个已验证正式发布仍是 `checkpoint-2026-08-28-unified-status-recovery-portable`。

只回看本次源码时创建独立分支：

```powershell
git -C D:\ProductAtelier-Desktop switch -c restore/semantic-cutout-confirmation checkpoint-2026-08-28-semantic-cutout-confirmation-source
```

如果未来已经用新 sidecar 创建 `cutout_selection.strategy=semantic` 的排队或暂停任务，回退到旧 sidecar 前必须先让这些任务完成或明确取消，并保留账本副本。旧 sidecar 不认识确认摘要与区域合同，不能让它接管未完成的语义任务，否则可能按旧“全部前景”语义执行。当前本轮只在隔离数据目录验收，正式用户账本尚未写入这种任务。

## 2026-08-29 自动定位合同源码检查点

- Git 标签：`checkpoint-2026-08-29-semantic-grounding-contract-source`
- 性质：源码、固定程序合同和离线门禁检查点；不是新的正式 EXE、sidecar、NSIS、模型包或桌面快捷方式发布。
- sidecar 源码合同：`2026-08-29.1`
- 数据库 schema：仍为 v3；没有迁移、删除或覆盖用户账本。
- 模型边界：Git 不含权重；没有下载或运行真实 grounding 模型；正式 PyInstaller sidecar 继续排除 `torch / transformers / tokenizers`。
- 上一个已验证正式发布仍是 `checkpoint-2026-08-28-unified-status-recovery-portable`。

只回看本次源码时创建独立分支：

```powershell
git -C D:\ProductAtelier-Desktop switch -c restore/semantic-grounding-contract checkpoint-2026-08-29-semantic-grounding-contract-source
```

本次新增的 preview 候选字段对旧前端不是破坏性数据库变更，但旧 sidecar 不具备候选状态和晚到结果保护。回退前仍应完成或取消新版本创建的语义任务、保留账本副本，并确保 `PRODUCT_ATELIER_GROUNDING_MODEL_PATH` 不会误指向已移动或不完整的模型目录。模型权重在正式发布事务之外，不得通过覆盖正式便携目录的方式“回滚”。

## 2026-08-29 真实 Grounding DINO 基线源码检查点

- 性质：源码、许可照片小样、外置模型获取合同与开发机基线；不是新的正式 EXE、sidecar、NSIS、模型包或快捷方式发布。
- 模型权重固定在 Git 外的外置目录；删除或移动权重不会影响上一个正式便携版。需要停用时先清除当前开发终端的 `PRODUCT_ATELIER_GROUNDING_MODEL_PATH`，不修改正式目录。
- `docs/model-artifacts/grounding-dino-tiny.json` 是来源与哈希合同，`tools/bootstrap_semantic_grounding.py --verify` 只验证外置文件；不要把 receipt、Hub cache 或权重复制进仓库和发布目录。
- 本检查点新增的中文保护会把未翻译 CJK 返回 `query_translation_required`。回退到第二源码检查点会重新允许中文落为 `[UNK]` 后抓取显著主体，属于已知假语义风险，不应作为生产降级路线。
- 上一个已验证正式发布仍是 `checkpoint-2026-08-28-unified-status-recovery-portable`。真实模型门禁失败，因此不得运行 candidate-first 正式提升。

## 2026-08-30 “智能选物人工恢复、柔边与 Alpha 紧裁边”正式便携检查点

- 正式源码提交：`dddedf1ea7a123e02cc9205c40401afc93e5cbf6`
- 正式目录：`D:\ProductAtelier-Desktop\release\ProductAtelier-Portable`
- sidecar contract：`2026-08-30.4`；数据库 schema：v3
- 正式 EXE SHA-256：`AFDFA20FD4721D53749A3C9BF537C2806B4AA184EA8D3838C0412E9AD118CE88`
- sidecar SHA-256：`CA60D20BB78B7C3267A4D99A092C96B6803175E28746D8164FCD3FBF086B321E`
- manifest SHA-256：`FC876CCD07E39495BA6F8DB1AE0E0789495D2C675E6EE97E994803231095A5D7`
- 正式目录 tree SHA-256：`9FBD24CA83E521625FDC4E24B10AEFE2C3678EA91EC6E99460986AFF77B03816`
- 已 finalized 事务：`f64dd12fed5540b0b8489f1d439f3b4b`
- 上一正式目录完整备份：`D:\ProductAtelier-Backups\release-before-20260830-192339-dddedf1ea7a1`
- promotion evidence SHA-256：`CD1CE541F0895C055B0EF3533EEB48847BBBB35BCD8DB7F82F951D5E6864496A`
- NSIS 候选 SHA-256：`408C22F03B9C60947A1E01970CB1230BA6038594A24556BE8B76286A22EB7D80`（107,359,397 bytes，`NotSigned`）

该事务已经 finalize，活动 transaction 文件不存在。上一正式目录备份是 `.3` 中间包；更早的 `D:\ProductAtelier-Backups\release-before-20260830-172404-a8dbb550fbed` 仍保存 2026-08-28 的正式包。不要直接把任一备份覆盖到运行中的正式目录；如需降级，先退出正式 EXE 和对应 sidecar，保留当前正式目录与 `%APPDATA%\ProductAtelier` 的新副本，再从目标提交重新走 `tools/dev.ps1` 的 candidate-first 发布链。

本检查点没有数据库迁移。新 sidecar 创建的语义确认任务包含 `cutout_selection`、`mask_edits` 与确认摘要；回退到不认识这些字段的旧 sidecar 前，必须先让相关排队/暂停任务完成或明确取消，避免旧程序按“全部前景”语义接管。外置自动定位 runtime/模型不在正式目录事务内，当前正式包仍保持质量不达标时的人工确认回退。

NSIS 已完成隔离安装、安装态双 smoke 与静默卸载，但没有 Authenticode 签名，只能作为内部候选。对外分发前必须签名后重新记录安装器哈希；签名会改变文件字节，不能沿用上方 SHA-256。

## 2026-08-30 “R7 生图零成本基线”正式便携检查点

- 正式源码提交：`e776a96d36cc6b8536f37eecddf3b089b28be04d`
- 正式目录：`D:\ProductAtelier-Desktop\release\ProductAtelier-Portable`
- sidecar contract：`2026-08-30.5`；数据库 schema：v3
- source fingerprint：`2D5DA786D1457F0A307496A981665B49FB5186D25848EDBB73773035CA995819`
- 正式 EXE SHA-256：`7A184036AD70B866D5B3EB8CF9D9B03F19EDB41138158358A56BE2D97319554B`
- sidecar SHA-256：`F3899399AA3AFA65B301AFB31C2402337842F2804EDCB9673847E8E83A920C01`
- manifest SHA-256：`7B5980F0BFF4327C1B86CD411BC7C783F44603DA6AE971E0780586AB8B0E1EDE`
- 正式目录 tree SHA-256：`CE3F3148DCE2C4C40BD4FEDE4E971662D72D6BE7156BCD8E24A4508EF8F08E15`
- 已 finalized 事务：`5bdb52a1667c43b9a77f97a1fb10d22a`
- 上一 `.4` 正式目录完整备份：`D:\ProductAtelier-Backups\release-before-20260830-200109-e776a96d36cc`
- promotion evidence SHA-256：`E9162CF69E65B1D7D895055AC3169366D1BA7647330B186F785E619DD9CA513A`

活动 transaction 文件不存在，桌面快捷方式目标与工作目录已指向上述正式目录，健康接口确认 contract `.5`、manifest `ok`、schema v3。该版本没有数据库迁移；新增的是后续生图任务的 Prompt/知识/参考请求指纹、阶段耗时、失败边界与明确缺失的费用字段。回退到 `.4` 不需要迁移数据库，但 `.4` 不会生成新观测字段；回退前仍应先退出正式 EXE 和它启动的 sidecar，并为当前正式目录和 `%APPDATA%\ProductAtelier` 各保留一份新副本。

本轮没有重建 NSIS。此前 `408C22F...` 的未签名 NSIS 绑定 `.4`，不能用于恢复或安装 `.5`；当前 `.5` 的可恢复事实源是 Git 提交、正式便携目录、上方外部备份和 promotion evidence。

## 2026-08-31 “Prompt v2 默认关闭与盲评预算门禁”正式便携检查点

- 正式源码提交：`1b5947502af00ac57f273510bf4a26a72d7c577a`
- 正式目录：`D:\ProductAtelier-Desktop\release\ProductAtelier-Portable`
- sidecar contract：`2026-08-31.1`；generation trace contract：`generation-baseline-2026-08-31.1`；数据库 schema：v3
- source fingerprint：`AA08DDF0762CF3AF71F8AE687DAF34AC4133C1AC8D44CCD679B8EF28BFCFF34C`
- 正式 EXE SHA-256：`A2736D7FC053C5EAB20E04214499586C24A5E4458F60ADEFCD11DD0E8E30A5DC`
- sidecar SHA-256：`26CF20FF286EFEE74096BF18C8FF89A9904FF2FB369B2D099C5AA7F80DA53381`
- manifest SHA-256：`9C163ADD7C8A87657E6ADB538E3EC9F3FE455AC58B595953D1715833678D5223`
- 正式目录 tree SHA-256：`CF07D4317D6FEEC5EE936C851ABA0F5F5BE54A45F4405BC7E3A480CFAF8BEDBA`
- 已 finalized 事务：`b3c0c1da03bc4f4faf029ecaf844508a`
- 上一 `.5` 正式目录完整备份：`D:\ProductAtelier-Backups\release-before-20260831-001837-1b5947502af0`
- promotion evidence SHA-256：`BCF7D27AB8A3D64B5E776BB6FBC5929E69AC5AD4080CCC83FEDBE4D647EA9F7F`

活动 transaction 文件不存在。桌面快捷方式目标和工作目录均指向正式目录；正式应用与单一 sidecar 正在运行，动态端口 `64731` 的健康接口返回 `ok`、manifest `ok`、contract `2026-08-31.1`、schema v3。

本版没有数据库迁移。正常任务仍默认冻结 `prompt_v1`；`prompt_v2` 只有在显式环境门禁开启且任务参数明确选择时才会入队。回退到 `.5` 不需要转换账本，但 `.5` 不认识新 Prompt 模板快照与 v2 编译合同；回退前应先让本版创建的排队/运行任务完成或明确取消，退出正式 EXE 和对应 sidecar，并为当前正式目录及 `%APPDATA%\ProductAtelier` 另留新副本，再从目标 Git 提交重走完整 candidate-first 发布流程。

本轮没有调用付费 VLM/生图、没有处理用户图片，也没有重建 NSIS。现存未签名 NSIS 仍绑定 `.4`，不能用于安装或恢复本检查点。

## 2026-08-31 “R8 可逆知识建议治理”正式便携检查点

- 正式源码提交：`7f6e62b120d4c1527f975875c40ad53f131e9ed0`
- 正式目录：`D:\ProductAtelier-Desktop\release\ProductAtelier-Portable`
- sidecar contract：`2026-08-31.2`；generation trace contract 仍为 `generation-baseline-2026-08-31.1`；数据库 schema：v3
- source fingerprint：`2467D2843BEFDF869AEBBA69D3639DB667A0777ECCE3B8BFCA3A5CCB80EC63F0`
- 正式 EXE SHA-256：`FE3EE4CEA2D0F346D2635770FDFB65A1F6293EFCEB8562AD9B33F81A45D99191`
- sidecar SHA-256：`D2B385D5A75AB4710879DCBF8ABE56FFC2490D1541A347D3B74B6737DF55B7F0`
- manifest SHA-256：`86BCDC3AE9D110AE309C5EFB1756C56C41D94FF297A3DE5AF914F56C6FD5BA0B`
- 正式目录 tree SHA-256：`420D51D219249C8C2F68737745676431FAB5A02601933C8B21C96253379D0FD9`
- 已 finalized 事务：`f8b20f0f7aab4d9aa0aa338df042f06f`
- 上一 `.1` 正式目录完整备份：`D:\ProductAtelier-Backups\release-before-20260831-085138-7f6e62b120d4`
- promotion evidence SHA-256：`846B5FBEAC6073F4DEFB14270B7EDA0461C165368CC4CAD05ACB32AB78B7A90A`

活动 transaction 文件不存在。候选与正式目录双 smoke 均确认 contract `.2`、manifest `ok`、schema v3 和同一 Git/source fingerprint；桌面快捷方式的目标与工作目录均指向正式便携目录，正式应用已从该路径启动。

本版没有 schema 迁移，知识建议的 revision、操作历史、redo、稍后状态和人工编辑元数据保存在既有 `proposed_value_json._governance` 中。回退到 `.1` 不需要转换账本，但旧版不会显示或操作新的治理历史，也不会将 `disabled` 状态作为完整治理对象。降级前应先结束本版创建的排队/运行任务、退出正式 EXE 与对应 sidecar，并为 `%APPDATA%\ProductAtelier` 和当前正式目录各保留一份新副本，再从目标提交重新走完整 candidate-first 发布流程；不要直接覆盖运行中的正式目录。

本轮没有调用付费 VLM/生图、没有处理用户图片，也没有重建 NSIS。现存未签名 NSIS 仍绑定 `.4`，不能用于安装或恢复本检查点。

## 2026-08-31 R9 外观与无障碍首批正式便携检查点

- 正式 artifact 绑定提交：`652763764dcfeb559d99a96de8b866c829060472`
- 正式 EXE / sidecar / manifest SHA-256：`720551F55B02FB713F51EFB9988C842285D7FBD40677C901A35EFFFFADEFD438` / `31DBA2DDBB2840E69C4CB4E50936DCFD95D6F65CAD4A11343FA6EC513224E1EC` / `1A98EF7AD616A3AEC06A35B4339A91DDC4DDEFF436902CB1212A35165D9C2C80`
- 正式目录 tree SHA-256：`0C9931F810D178CDAEB0BB7233F5F1F9952A650A2A7F5CD4BFF58DB77634BA7F`
- finalized 事务：`44e9c55a76824b5197ec89ef74b1abf3`
- 上一 R8 正式目录备份：`D:\ProductAtelier-Backups\release-before-20260831-114143-652763764dcf`
- promotion evidence SHA-256：`524FAA6411CB7C1D04F6A0783F761500AF91B50649C84EE340E98CFFE0511EE2`

本检查点没有数据库迁移，新增外观偏好仅保存在本机 localStorage；回退到 R8 不需要转换 schema，但 R8 不显示主题跟随系统、舒适字号、高对比和减少动效设置。降级前先完成或取消排队/运行任务，退出正式 EXE 及其子 sidecar，为当前正式目录与 `%APPDATA%\ProductAtelier` 另留新副本，再从目标 Git 提交重走 `tools/dev.ps1`；不要覆盖运行中的正式目录。

发布后的证据工具修复提交 `1f7b33a` 不改变正式 artifact。它只将截图从模糊标题匹配改为正式进程 ID，并在抓取前恢复/置前窗口；若回退源码，建议保留该工具修复，否则同名 Photoshop 文档可能再次污染验收截图。当前活动 transaction 文件不存在，桌面快捷方式目标与工作目录均指向正式便携目录。

## 2026-08-31 R9 键盘、对比度与 50/200 素材正式便携检查点

- 正式 artifact 绑定提交：`107288d66675ff76276ec43b73a31c75ce865dfc`
- 正式 EXE / sidecar / manifest SHA-256：`E5A1600C0C813EADD383462B077C9FA1AB6CA34A1A65967D304AA36FC18FE8FB` / `89731E10F4127CA8655C0182A4A3089F465BDA7E9CD9E305C336FC126FA2E62A` / `784095094C8BA377B8EBA1F6F771411FF453472E73C7983544E77216C8A5A556`
- 正式目录 tree SHA-256：`EB44C2F9B20F5EBCAE7341EC67E9982F9BDEAA176C6821C5EF2D2F405846ED30`
- finalized 事务：`a7e29720dc2247729fdce0567e2a1fa8`
- 上一 R9 首批正式目录备份：`D:\ProductAtelier-Backups\release-before-20260831-170450-107288d66675`
- promotion evidence SHA-256：`D41E00A41F45C64A495FB31CB41F39E4A7BB6A6F45B9C719F33D79C841990473`

活动 transaction 文件不存在；候选与正式目录双 smoke 均确认 contract `2026-08-31.2`、schema v3、同一 Git/source fingerprint，桌面快捷方式已在 finalize 后更新。该版本没有数据库迁移；新增内容只影响前端呈现、键盘焦点与大列表渲染，回退到 R9 首批不需要转换账本，但会失去第二批对比度与页签键盘修复。

若需降级，先完成或取消排队/运行任务，退出正式 EXE 及其 sidecar，为当前正式目录和 `%APPDATA%\ProductAtelier` 另留新副本，再从目标提交重走 `tools/dev.ps1`；不要直接覆盖运行中的正式目录。本轮外部备份只含前一正式应用目录，不含用户账本、Key、素材或成品。

## 2026-09-01 R7 显式生成流程正式便携检查点

- 正式 artifact 绑定提交：`f0dc5440c060e9f5a0ee9e185bdfc49b61fb224e`
- sidecar contract：`2026-08-31.2`；generation trace contract：`generation-baseline-2026-08-31.2`；数据库 schema：v3
- source fingerprint：`1A90ABD1D6955DE5797C84D843D364CB4ACA7F799ED4CF1B51FEF3D8AC0B2398`
- 正式 EXE / sidecar / manifest SHA-256：`269775D9C8781686E2DE5C76D3F9F3519549375A102814C020F53A64210C7A6C` / `4B90F9F16B1555FF6350CCD7DAA59B321B6624851B0B8EE0D5E9970B07103474` / `5261B5E1511BA23777EE3C9B316EB01071C414930B7BA2CD5514A4C4C42388C7`
- 正式目录 tree SHA-256：`DB15888252479C0D2C139F6E2F96F10322B382AFB9E6DC1C77F661318E3F4812`
- finalized 事务：`2543dfcf698a4a91ba975620b31adea7`
- 上一 R9 正式目录完整备份：`D:\ProductAtelier-Backups\release-before-20260901-093526-f0dc5440c060`
- promotion evidence SHA-256：`5954B6CA864E8C40F31CF981621D3C901BD9CBC6DE0B99096F72A5A16C2B9966`

活动 transaction 文件不存在；隔离候选与正式目录双 smoke 均确认同一 Git、contract、schema 与 source fingerprint，桌面快捷方式目标/工作目录及正在运行的应用/sidecar 都指向正式便携目录。正式默认仍为 `prompt_v1 + legacy_double_pass`；用户可在单产品和多文件任务中显式选择 `single_pass`，来源会冻结为 `generation_strategy_source=user`。

本检查点没有数据库 schema 迁移，但旧正式版不会放行新建的用户单次任务。若需降级，先完成或取消使用 `single_pass` 的排队/运行任务，退出正式 EXE 及其 sidecar，并为当前正式目录和 `%APPDATA%\ProductAtelier` 另留新副本，再从目标提交重走 candidate-first 发布链；不要直接覆盖运行中的正式目录。本轮未重建 NSIS，也未在发布过程中调用付费 VLM/生图或处理用户图片。

## 2026-09-01 单产品识别异常恢复正式便携检查点

- 正式 artifact 绑定提交：`39ca4e59aeb1077c1367ecf2ed7496a4549bc1df`
- 正式 EXE / sidecar / manifest SHA-256：`E360A7E4329EBCA900D92B7F30FAEF08CBE1E0D4D7443B438D862CE35EFDC14B` / `079EC520515372D0C38D26D152AD72DEC95746E9DBB0E6D7CEC27D3672FCD896` / `064DC8C6C28DB0A2F4600531E07E01D20247F32F6EC53096FE79AF4740D5A095`
- source fingerprint：`21B510E6EF4213E6865721FD527E2B38A346FF9F11D66466784F6BAC56A3A430`
- 正式目录 tree SHA-256：`E0ED16332EDB63FAF2A25CFF2058A6CFDEF273CCA722325774DBEDE811CDEEE84`
- finalized 事务：`65fbdba328b642ee879021ce248cbae8`
- 上一正式目录完整备份：`D:\ProductAtelier-Backups\release-before-20260901-101926-39ca4e59aeb1`
- promotion evidence SHA-256：`262B0410833E3EB73806B8044E0C42E12261CBEF23E744B31EC5B2F3A0C615F4`

本检查点没有 schema 迁移。回退到 `f0dc544` 不需要转换账本，但旧版再次遇到非严格 VLM JSON 时会让单产品任务在生图前失败，并把失败终态显示为 100%。降级前先完成或取消排队/运行任务，退出正式 EXE 与对应 sidecar，为当前正式目录和 `%APPDATA%\ProductAtelier` 另留副本，再从目标提交重走 candidate-first 发布链；不要直接覆盖运行中的正式目录。回退不应删除现有失败任务与 trace，它们是诊断证据。本轮未重建 NSIS，也未调用付费 VLM/生图。

## 2026-09-01 G1B schema v4 画布合同源码检查点

- 性质：源码、迁移、API 与隔离 sidecar 候选检查点；在 Fabric 正式 UI、逐像素导出和 Windows WebView 门禁完成前，不提升正式便携目录、NSIS 或桌面快捷方式。
- sidecar 源码合同：`2026-09-01.1`；数据库 schema：v4。
- 上一个已验证正式 artifact 仍为 `911b352713e91f5da1caf8072b5618cba49af852`，正式用户账本仍为 schema v3。

schema v4 首次打开 v1、v2 或 v3 账本前，会用 SQLite online backup API 在原数据库旁生成带原 schema 版本的可查询备份，然后在单个 `BEGIN IMMEDIATE` 事务中升级。迁移失败会回滚本轮 DDL、数据和 schema marker；检测到不完整 v4 对象时拒绝启动，不猜测补齐。

v4 新增不可变画布版本、素材/结果来源引用，以及任务快照和 trace 的统一命令/画布版本绑定。旧 schema v3 sidecar 会把 v4 判断为未来版本并拒绝打开，不能直接用旧程序继续写 v4 账本。需要降级时：

1. 完成或取消排队/运行任务，退出目标桌面 EXE 和它启动的 sidecar，并核实进程路径；
2. 为当前 v4 数据库和正式应用目录另留完整副本；
3. 找到本次升级自动生成且经 `integrity_check`/外键检查验证的 `.backup-v3-*.sqlite3`；
4. 从目标 v3 Git 提交重走 candidate-first 发布链，并只把该 v3 备份恢复到隔离数据目录验证；
5. 验证通过后再按发布事务恢复正式数据，不得让 v3 程序覆盖或修改 v4 文件。

恢复 v3 备份会失去升级后新增的画布版本、画布操作绑定和 v4 期间写入的数据，因此这是有明确数据边界的降级，不是无损 schema down-migration。正常修复优先前滚到支持 v4 的版本。
