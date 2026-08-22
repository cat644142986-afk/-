# 阶段 0：恢复与发布链加固记录

> 日期：2026-08-22<br>
> 分支：`codex/master-roadmap-phase-0-1`<br>
> 实施前提交：`eb76dedcb78aad7cd520176745989361c6052e65`<br>
> 状态：代码与独立 sidecar 运行验证已完成，完整回归和提交待阶段 1 一并收口。

## 1. 回滚锚点

- 本地标签：`baseline-2026-08-22-before-master-roadmap`
- 数据库备份：`D:\ProductAtelier-Backups\phase0-2026-08-22\atelier-pre-master-roadmap.sqlite3`
- 数据库备份 SHA-256：`853BA03FB446A0DBF511DDDCD74C7734E70221F5B340D6A9F59D0E0B10396738`
- 备份检查：schema v2、SQLite integrity `ok`、0 个外键异常、11 张业务表。
- 实施前便携版外壳 SHA-256：`A4FA568836B97334AC6E3B4360B04B84EECEEB8B1EB4EC7A6C2126B1AAC4A560`

用户配置、API Key 和图片内容没有写入 Git 或本文。

## 2. 已修复的部分迁移状态

历史实机曾出现“v2 表和索引已经存在，但 `ledger_meta.schema_version` 仍为 1”。旧逻辑
会再次执行 `CREATE TABLE asset_blobs` 并启动失败，只能人工修改 marker。

当前恢复规则：

1. schema marker 为 1 且完全没有 v2 对象：执行正常 v1 → v2 迁移。
2. marker 为 1 且存在 v2 对象：检查全部 v2 表、字段、索引、SQLite integrity 和外键。
3. v2 契约完整：先在线备份，再只修正 marker 为 2，并记录 `last_schema_repair`。
4. v2 契约不完整：保持数据库不变，抛出包含缺失对象的 `PartialSchemaError`，要求从自动备份恢复或显式修复。
5. marker 高于程序支持版本：继续拒绝打开，不做任何降级写入。

新增迁移测试覆盖完整旧 marker 自动恢复和真正部分结构拒绝写入。

## 3. sidecar 一致性契约

`/api/health` 现在返回：

- 产品版本；
- sidecar contract 版本；
- 是否为 PyInstaller 打包运行；
- 构建提交、源码指纹和构建时间；
- manifest 是否存在并与运行时代码契约一致；
- 账本 schema 与本次启动是否执行 marker 恢复。

当前 contract：`2026-08-22.1`。

`tools/Build-Sidecar.ps1` 会：

1. 在项目内 staging 目录构建，不先破坏当前发布包。
2. 对后端关键源码和 spec 计算 SHA-256，形成单一源码指纹。
3. 对生成的 `python-server.exe` 计算 SHA-256。
4. 写入 `sidecar-manifest.json`。
5. staging 成功后才替换 `src-tauri/bin/python-server`，按参数同步便携版。
6. 对所有递归删除目标先验证其绝对路径位于项目目录内。

`tools/Test-Portable.ps1` 会：

1. 验证便携版 exe 与 manifest 哈希。
2. 验证 manifest 记录的源码哈希与当前源码一致。
3. 用独立临时数据目录启动打包 sidecar。
4. 调用真实 `/api/health`，验证 contract、manifest 与 schema v2。
5. 停止测试进程并清理经过路径校验的临时目录。

本轮独立运行验证结果：PASS。源码指纹：
`BB20D111EC23ED4D9B4CCD77DA50C06D97BA7EA7A6FA0B3373094D60162E6B32`。

## 4. 构建性能修复

旧 `python-server.spec` 对 ONNX Runtime、Numba、llvmlite、SciPy 与 skimage 执行全量
`collect_submodules`，会把量化工具、Transformer 工具、CUDA 测试和大量开发模块一起
分析和打包。首次实测超过两分钟仍未完成。

现在改为显式运行模块，并排除测试/量化/CUDA/数据科学开发表面。完整 onedir sidecar
实测约 43–45 秒完成；独立健康验证约 3 秒。PyInstaller 的 `tbb12.dll` 可选线程池警告
仍会出现，但当前 CPU 运行路径和健康检查正常，后续抠图真实模型回归还需验证。

## 5. 一键开发链变化

`tools/dev.ps1` 默认顺序已改为：

1. 停止当前应用和 sidecar。
2. 从当前源码重建 sidecar 并同步便携版。
3. 构建前端。
4. 按需构建 Rust。
5. 部署外壳。
6. 验证便携版 sidecar 哈希和真实运行健康。
7. 启动应用并按需截图。

`-Quick` 只跳过 Rust，不会再静默沿用旧 sidecar；只有显式 `-SkipSidecar` 才跳过，
并且后续验证仍会拒绝与源码不一致的包。

## 6. 阶段 0 验收证据

- 原始基线：71 项后端测试通过，16 项前端测试通过，Vite production build 通过。
- 迁移专项：10 项通过，其中 2 项为新增部分迁移恢复测试。
- 健康 API：新增 contract/schema 测试通过。
- 打包 sidecar：PyInstaller onedir 构建通过。
- 便携 sidecar：静态哈希、源码指纹、独立运行 `/api/health` 全部通过。

阶段 1 完成后仍需运行全量回归、重新生成最终 manifest、构建 Rust 并验证桌面外壳。
