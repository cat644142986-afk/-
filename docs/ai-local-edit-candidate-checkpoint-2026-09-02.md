# AI 局部编辑候选生成检查点（2026-09-02）

## 结论

“描述修改 → 明确确认一次调用 → AI 生成候选 → 自动返回当前规格 → 严格 Mask/Outpaint 合成”已完成源码和隔离账本闭环。局部编辑不再只能从历史结果里手工找候选；用户可以在 Fabric 精修面板直接建立一个耐久候选任务，切换面板或重启后继续恢复进度。

这仍不是正式便携版发布，也不是对真实 provider 图像质量的结论。本轮只使用离线假 provider 验证合同、任务、尺寸、血缘和像素保护，没有读取 API Key、调用付费接口、修改正式账本或替换正式目录。

## 实现范围

- 实现提交：`dae910c8afec4c9ef4faa6ad94b4b1e6d719c727`；父提交/回滚点：`29b4f06f9338426c1ced6f0efbb76ce854970537`，标签 `checkpoint-2026-09-02-infinite-canvas-ic4`。
- sidecar contract 继续为 `2026-09-02.4`；开发 schema 继续为 v8；复用现有任务、trace、CanvasDocument、LocalEditSpec、结果资产和 composition，不建立第二套账本。
- 新命令 `command:local-edit-generate` 使用独立 `cloud-local-edit` 执行器，但仍属于 `single` 工作流，使任务可以绑定现有 Fabric 精确规格。
- 费用门禁强制 `provider_call_confirmed=true`、`max_attempts=1`、`automatic_paid_retry=false`、单来源和单候选；任何失败都不会自动追加付费重试。
- inpaint 只发送 ROI 周边上下文，候选恢复为原图尺寸；outpaint 按冻结输出尺寸和源图位置建立参考画布。provider 输出永远先成为候选，不直接改写画布。
- 最终写入继续走既有严格合成：inpaint 的 `outside_mask_changed_pixels == 0`；outpaint 的 `protected_changed_pixels == 0`。
- 结果图层可以继续编辑，但只对局部编辑命令开放 `result_*` 源；普通快捷生成仍只接受 `workspace_source`。
- 前端支持描述、一次调用确认、提交、进度、取消、失败解释和重启恢复；完成后自动刷新并选中新候选。幂等重放直接返回已完成任务时也执行同一收尾。

## 隔离验证

- 离线 API 闭环依次完成 28×20 inpaint 候选和严格合成，再把该结果作为 outpaint 输入生成 36×24 候选并严格合成。
- 两个候选任务各调用一次假 provider；候选尺寸分别为 28×20 和 36×24；全程网络请求为 0。
- inpaint 选区外像素变化为 0；outpaint 原 28×20 保护区域变化为 0；新区域只来自候选。
- 账本专项证明局部编辑可以使用 `result_main`，同一结果资产传给普通快捷生成会被拒绝。
- 取消、任务中断恢复、幂等请求、失败不发布结果和启动恢复继续由现有耐久任务全量回归覆盖。

## 工程门禁

- 前端：171/171。
- Python：354 项通过，1 项因当前 Windows 测试环境不允许创建测试符号链接而预期跳过。
- Vite production build：通过；production dist `10,613,142 bytes`（10.12 MiB）。
- 无限画布懒加载 bundle：通过；首屏 `modulepreload` 为 0；预计正式便携目录 368.68 MiB，低于 450 MiB。
- Rust/Tauri `custom-protocol` check、Python compileall、Git whitespace：通过。

## 正式版保护

- 正式便携版继续保持 Git `93539f0c9ec857d22d3751bb836ff722579cd8db`、contract `2026-09-02.3`、schema v7、358.56 MiB。
- 正式 App、sidecar 和 manifest 均未修改；正式快捷方式和用户 SQLite 未打开。
- 本检查点只能作为 IC5 和候选构建的源码回滚点；真实 provider 质量需另行授权预算后验证。

## 下一游标

进入 IC5 视频第一阶段：扩展现有任务/结果合同以支持视频，先用离线假处理器完成图生视频参数、排队、进度、取消、失败解释、重试、结果回填、封面、播放、血缘和原视频导出。2026-09-02 20:00 后并行执行真实 Tauri 窗口、DWM/DPI、系统级拖拽与安装包门禁；全部门禁通过前不提升正式便携版。
