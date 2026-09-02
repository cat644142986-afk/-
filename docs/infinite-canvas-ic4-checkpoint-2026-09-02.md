# 无限画布 IC4 业务桥检查点（2026-09-02）

## 结论

IC4 源码、隔离账本和后台浏览器门禁通过。素材、任务和结果已经接入独立无限画布；图片选中后按对象类型显示情境工具；现有 Fabric 精修可从空间画布进入，并把结果作为父节点右侧的新版本回填。父图、血缘箭头和子图使用 Excalidraw 公开绑定字段，移动任一图片时连线随动，不覆盖父节点。

这仍不是正式便携版发布。正式目录、正式快捷方式、正式用户账本和正式 artifact 均未修改；DWM、真实 DPI、系统级拖拽和 Tauri WebView 指针交互因用户正在工作而明确留到候选阶段复验。

## 实现范围

- 实现提交：`d3d0335d17722f9126fdc41e37942695dbbc13e1`；父提交：`739fcd4b2a11f78ca32a35d6b5df800f150fb6ea`。
- sidecar contract 继续为 `2026-09-02.4`；开发 schema 继续为 v8；没有建立第二套业务账本。
- 快捷处理结果、当前素材、素材管理器、历史结果和任务中心均可发送或拖入画布；回收站素材不显示“画布”按钮且不可拖入。
- scene `customData` 只保存 `asset_id / result_id / task_id / product_profile_version_id / lineage_parent_id`，不保存 URL、Base64、原图字节或机器路径。
- 素材和结果使用代理图；原件继续由素材账本和正式导出管线管理。程序化 `updateScene()` 会显式触发 scene 同步和代理图补载。
- 结果默认放到父图右侧并新增血缘箭头。父图和子图的 `boundElements`、箭头的 `startBinding / endBinding` 同时写入，全部使用 Excalidraw 公开数据合同。
- 程序化发送使用 `CaptureUpdateAction.IMMEDIATELY`，可立即撤销；重启或切换画布恢复使用 `CaptureUpdateAction.NEVER`，不会污染用户撤销历史。
- 选中源图只显示抠图、白底图、扩图、局部修改、生图、生视频和精细修改；结果增加对比与导出；任务只显示打开任务。未选中或不兼容对象不常驻禁用表单列。
- 抠图、白底图和生图只预填现有快捷处理，尚未点击生成前不会调用供应商；生视频在 IC5 前只显示明确的未接入说明。
- 双击图片或点击精细修改进入现有 Fabric ROI/Mask/扩图界面；compose 继续写不可变结果，回填空间画布时建立父子血缘并保留返回入口。

## 真实后台验收

验收只使用 `D:\ProductAtelier-IC4-Isolated-20260902-B` 隔离数据目录和 `127.0.0.1:64901` 隔离 API，没有读取正式用户账本、API Key 或调用付费生图。

- 新建画布 `spatial_763754b3e294ebee86e3226d447799d7`，先发送源素材，再发送离线结果；revision 3 为 2 张图片和 1 条绑定血缘箭头，`files={}`。
- 结果图向下移动 5 px 后，箭头从水平线变为随动斜线；父图、子图均保留箭头引用，箭头同时保留 start/end binding。
- 再次发送结果后撤销立即可用；发送、撤销、重做分别保存为 revision 5、6、7。恢复 scene 不进入撤销栈。
- 最终 revision 7 为 3 张代理图、2 条绑定箭头；3 个图片端点均有 `boundElements`，2 条箭头均有双端 binding，scene 仍无文件字节。
- 先前画布重启后保持 revision 5，不重复追加空版本；结果节点、父引用和视口均恢复。
- 后台浏览器控制台：0 error / 0 warning。

## 窗口与性能

| 窗口 | 横向溢出 | 纵向溢出 | 截图 |
| --- | ---: | ---: | --- |
| 1440×900 | 0 | 0 | `artifacts/excalidraw-spatial-ic4/viewport-1440x900.png` |
| 1280×720 | 0 | 0 | `artifacts/excalidraw-spatial-ic4/viewport-1280x720.png` |
| 960×600 | 0 | 0 | `artifacts/excalidraw-spatial-ic4/viewport-960x600.png` |

- 960×600 下对象检查器与 Excalidraw 顶部工具栏保持 8 px 间隔；页面横纵溢出均为 0。
- `failed-before-fix-viewport-960x600.png` 仅作为修复前失败证据，不代表最终状态。
- production `dist`：`10,604,026 bytes`（10.11 MiB）。
- 预计正式便携目录：368.67 MiB，低于 450 MiB 上限。
- 快捷处理初始 HTML `modulepreload` 为 0；Excalidraw 继续只在进入无限画布后懒加载。

## 工程门禁

- 前端：171/171。
- Python：352 项，351 通过、1 项平台预期跳过。
- 发布事务专项：31/31。
- Vite production build、画布 bundle 合同、Rust/Tauri custom-protocol、Python compileall 和 Git whitespace：通过。
- `package-lock.json` SHA-256：`A0758630BC38B94F76D71D0CB74D72AE5BC20829073C2A613F0F1651719F86BD`。
- 详细结构化证据：`artifacts/excalidraw-spatial-ic4/metrics.json`。

## 正式版保护与回滚

- 正式版仍为 Git `93539f0c9ec857d22d3751bb836ff722579cd8db`、contract `2026-09-02.3`、schema v7、`375,974,706 bytes`（358.56 MiB）。
- 正式 App、sidecar、manifest SHA-256 分别为 `F0D1A07313A47258CD17FA4143DF4F0069D6E075CAA9D22B00EB7220A15685A7`、`2AF3FC31A90961D77A41BE6CECAFD3559E0D0FFFB505FD9BBEA9EC7C61293894`、`287042C051B97CC9F60C42F670C470EBEC965042378B790E0F4F13FCC9718A38`。
- IC4 实施前回滚点为 `checkpoint-2026-09-02-infinite-canvas-ic3`；本检查点标签为 `checkpoint-2026-09-02-infinite-canvas-ic4`。

## 未关闭门禁与下一游标

- 尚未完成：Tauri 正式 WebView、DWM、真实 DPI、系统级跨面板拖拽、候选 EXE/sidecar、正式目录双 smoke 和 candidate-first 提升。
- 下一源码游标进入 IC5 视频第一阶段：复用现有任务队列和离线假处理器，完成视频参数、排队、进度、取消、失败解释、重试、结果回填、版本与导出；不接未经授权的付费 provider，不打包本地视频模型。
