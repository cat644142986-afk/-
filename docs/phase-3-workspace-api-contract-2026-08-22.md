# Phase 3：工作区与可解释执行 API 契约

> 日期：2026-08-22<br>
> 分支：`codex/master-roadmap-phase-0-1`<br>
> sidecar contract：`2026-08-22.3`<br>
> ledger schema：`3`

## 目标

前端不再用一个全局素材数组或 `localStorage` 猜测任务现场。四种工作流通过明确 API
读取持久草稿、所属素材域、活动任务、最近结果和评审；任务执行过程同时留下可解释 trace。

## 工作区映射

| 工作流 | 素材域 | 草稿隔离 |
|---|---|---|
| `single` | `product` | `draft_single` |
| `multi-file` | `product` | `draft_multi_file` |
| `group-split` | `group` | `draft_group_split` |
| `cutout-batch` | `cutout` | `draft_cutout_batch` |

单产品与多文件复用同一产品素材库，但选择、描述、参数、活动任务、当前结果、对比和 UI
位置互不覆盖。合照与抠图不与产品素材串台。

## API

### 素材域与回收站

- `GET /api/collections/{collection}/assets?limit=&offset=`：分页查询当前素材域。
- `POST /api/collections/{collection}/assets/{asset_id}`：加入或恢复成员关系。
- `DELETE /api/collections/{collection}/assets/{asset_id}`：软删除，不删除文件与血缘。
- `PUT /api/collections/{collection}/order`：提交完整活动素材顺序。
- `GET /api/trash?collection=`：按素材域查询回收站。
- `GET /api/assets/{asset_id}/references`：返回所有清理阻断项和最早可清理时间。
- `DELETE /api/trash/assets/{asset_id}?confirm_asset_id={asset_id}`：受控永久清理。

永久清理默认等待 30 天，可通过本地部署变量
`PRODUCT_ATELIER_TRASH_RETENTION_DAYS` 调整。它必须同时满足：所有成员关系均非活动状态，
且没有草稿、任务、快照、子结果、生成结果、反馈、评审、知识证据或执行 trace 引用。
数据库元数据先提交删除，再清理无人引用的内容寻址文件；文件清理失败会作为 orphan 警告返回，
不会恢复已经确认删除的元数据。

### 工作流现场

- `GET /api/workspaces/{mode}`：一次读取草稿、所属素材、活动/最近任务、最近结果与评审。
- `PUT /api/workspaces/{mode}/draft`：用 `expected_revision` 原子替换草稿。

过期 revision 返回 `409 DRAFT_REVISION_CONFLICT` 并附当前草稿。任务提交后使用不可变
`job_snapshot`，后续编辑草稿不会改变正在运行或可重试任务。

### 可解释执行与反馈

- `POST /api/jobs/{job_id}/traces`：记录用户输入、实际 Prompt、采用/忽略知识、模型、参数、
  输出或失败阶段。
- `GET /api/jobs/{job_id}/traces`：按执行顺序读取 trace。
- `POST /api/jobs/{job_id}/reviews`：记录采用/调整/放弃、原因、备注和独立学习动作。
- `GET /api/jobs/{job_id}/reviews`：读取任务结果评审。

trace 与 review 的 `client_request_id` 映射到确定性主键。完全相同的重连请求返回同一记录；
同一键携带不同内容返回 `409 IDEMPOTENCY_CONFLICT`。所有 job/item/generation/result 关系均做
血缘校验，反馈不能误写到其他任务。

## 已验证场景

1. 同一物理图片按 SHA-256 去重，并可同时属于不同素材域。
2. 单产品与多文件读取相同产品素材，但保存任一草稿不会改变另一草稿。
3. 跨素材域选择、过期草稿 revision、重复素材排序均返回结构化错误。
4. 软删除、撤销、回收站、保留期阻断、任务引用阻断与永久文件清理均有 API 测试。
5. trace/review 重放不产生重复数据，篡改同一 idempotency key 会被拒绝。
6. 20 张批量导入、50/200 条分页性能、CORS、路径白名单和服务重启持久性均有测试保护。

本阶段验收结果：87 项 Python 测试、16 项前端状态测试、Vite 生产构建与 Rust
`cargo check` 全部通过；测试使用临时账本和 mock 模型，不调用真实生图接口。

## Phase 4 前端接入边界

Phase 4 只允许把以上 API 接入新的单一状态树，不在旧 `app.js` 上继续叠加全局变量或版本补丁
CSS。网络态、草稿持久态和临时交互态必须分层；切换模式只切换当前视图，不能清空其他模式
的素材、草稿、任务或预览。
