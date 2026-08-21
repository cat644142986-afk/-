# Product Atelier 回滚说明

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
