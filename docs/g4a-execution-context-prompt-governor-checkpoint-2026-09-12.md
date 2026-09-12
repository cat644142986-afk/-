# G4A 执行上下文与 Prompt Governor 检查点（2026-09-12）

## 结论

G4A 的最小可验证闭环已完成：提交前预览、不可变任务快照和实际 Prompt trace 现在共享同一个 `execution-context-v1` 身份哈希。服务端不会在提交后静默替换上下文；若素材、Product Profile 或已批准知识在预览后发生变化，提交以 `EXECUTION_CONTEXT_STALE` 拒绝并要求重新预览。

本检查点建立在源码基线 `e041a8c` 之上，不改变正式产品身份 `ba4671b`，也不触发正式打包、发布或真实付费 Provider 调用。

## 已形成的闭环

1. 固定优先级：User Intent → Canvas Context → Product Profile → Approved Knowledge → Skill → Case → Provider Adapter。
2. Skill / Case 在 v1 合同中保留位置但明确为 `reserved-not-active`，没有提前建设 Skill 市场或 Agent 系统。
3. 预览上下文随任务提交；服务端以当前权威状态重算并校验，同一内容从 `preview` 提升为 `job-snapshot` 时哈希不变。
4. 任务执行只读取冻结的 `prompt_inputs`，不因稍后修改知识库、记忆状态或 Product Profile 而漂移。
5. Prompt trace、prompt snapshot 和事件证据记录同一个执行上下文哈希，可从结果反查本次真正使用的意图、规则、来源和 Provider 路由。
6. 复用现有 Prompt 路由的规则预算：先选出实际会进入当前 Prompt 的最小规则集合，再冻结；没有新增 Prompt 模板，没有改变 `prompt_v1` / `prompt_v3` 的既有路由与输出行为。
7. 调整任务将本次调整说明作为新的 User Intent 冻结，同时继续引用原任务锁定的 Product Profile 版本。
8. 快速工作台的 Intelligence 抽屉可以看到五层执行上下文和短哈希；历史任务优先从任务快照恢复，旧任务继续兼容既有 trace 证据。
9. Canvas command、批量抠图和离线图生视频也获得任务级上下文；非 Prompt Provider 合同不会收到执行上下文元数据。

## 合同与实现入口

- JSON Schema：`docs/contracts/execution-context-v1.schema.json`
- 确定性构建、哈希和校验：`python/execution_context.py`
- 知识选择与冻结执行：`python/knowledge_engine.py`
- 预览、提交校验、任务快照和 trace 绑定：`python/server.py`
- 提交与可见检查：`src/js/app.js`、`src/index.html`
- 历史任务恢复：`src/js/workspace-state.js`

## 验证证据

- Python 全量：`558` 项通过，`4` 项按既有条件跳过。
- 前端全量：`277` 项通过，`0` 失败。
- Vite production build：通过；仅保留现有依赖的 chunk / `use client` 警告。
- 针对性合同覆盖：Schema、确定性哈希、防篡改、预览到快照同哈希、陈旧预览 409、Product Profile 固定版本、Approved Memory 冻结、调整任务意图、历史恢复、视频 Provider 参数隔离。

## 明确不在本检查点内

- 不新增 `Prompt v4`，不切换默认 Prompt。
- 不接入或执行 Skill / Case；这里只冻结未来可扩展槽位。
- 不建设首页、Toolbox、Skill 市场或新 Agent 系统。
- 不实现 Obsidian Knowledge Home 或 wikilink/frontmatter Graph Projection。
- 不推进 P2 公开签名分发，也不替换 `ba4671b` 正式包。

## 后续边界

下一步若继续 G4A，只应在真实使用证据支持时调整 Provider / 场景预算或增加更细的排除解释；不得借此新增 Prompt 模板。Skill、Case 与 Obsidian 只读投影应在其各自阶段接入本合同，不回头再建第二套上下文系统。
