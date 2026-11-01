# PA 智能画布完整落地方案 v2

日期：2026-09-30

状态：实施中，完整 Gate OPEN。本文最初为源码拆解方案，随后已有实现与局部 packaged 证据；第 2 节是审查开始时的历史事实，不代表当前实现仍为 localStorage-only。2026-11-01 新电脑交接状态、已完成与未完成项见 [START_HERE.md](../START_HERE.md)。正式发布树和桌面入口未被本轮 prototype 修改。

## 1. 目标与范围校正

目标不是给 aafb778 的图片底座继续加按钮，而是交付一个节点式、多媒体、可连续使用的设计工作现场。对标对象为 hero8152/Infinite-Canvas 的 smart-canvas，不是另一个 workflow Canvas。

已确认的行为和呈现为母体；改善模型选择、参数兼容、软件焦点、持久化、错误恢复和桌面适配，不削减成熟交互来追求所谓设计感。用户图片、文字和素材原始视觉不被软件 UI 自动改写。

旧 Gold Master v1 第 2 节把视频、循环等直接排除，与用户最新要求冲突；此排除不再作为实施依据。旧的逐个技术阶段暂停方式也不再适用。内部可以分批提交和回归，但第一次交用户验收必须是完整、连续的 packaged 工作流。

正式稳定版仍不被本方案修改。替换候选在现有 prototype 分支中实现；完整验收、数据兼容、用户产品审核和发布流程通过前不 merge、不 Promote。

## 2. 已核对的真实事实

- PA：codex/excalidraw-infinite-canvas / aafb778c2363a41a1aaa72dd71e6299341a3c927；开始本次审查时工作区干净。
- 目标：67f49e4b9a0ac090bad1f5bd62ae385f8fb5394e；只读源码，无上游修改。
- 静态 smart-canvas.html：107 个 button 声明、14 个 input、5 个 select。
- smart-canvas.js：机械定位 134 个动态模板 button 声明。map/条件分支会展开更多实际控件，241 不是运行时按钮数量。
- 附录逐项保留源码定位。声明定位不是“每个按钮已经理解并通过验收”；动态生成、媒体类型、条件开关、异步失败路径必须继续沿事件和运行状态核对。
- 当前 prototype 只有 image/group 节点；readAsDataURL + 整场景 localStorage 确实会触发截图中的容量问题。失败消息还使用成功勾号，状态表达也必须修。
- PA 已有 MP4/WebM 导入、持久化视频资产、image-to-video Task 与离线输出。video_contract 明确只接受 offline-preview-v1，不能声称已有真实视频生成。
- Provider Catalog 已取 image/video/audio 目录；当前 Capability Overlay 的 task kinds 仍是四类图像任务。目录存在不代表视频、音频、LLM 已被准入。
- 现有 SpatialScene 是 elements/app_state/files 契约，禁止嵌入媒体。不能直接把 React Flow nodes/edges 塞进旧接口，也不能建立第二套长期 Canvas store。

## 3. 每个交互的完成定义

每一个源码按钮、手势、状态分支都建立以下记录：

来源位置 → 出现条件 → 输入状态 → 事件/焦点规则 → 实际数据变化 → 取消/失败恢复 → PA 接入点 → 自动化证据 → packaged 证据。

状态至少覆盖 idle / hover / keyboard-focus / selected / dragging / editing / loading / disabled / failed / restored，以及单选、多选、媒体类型差异。无需为每个按钮重复所有组合，但不存在“只看见按钮就算完成”。

所有条目必须落到以下四类之一：

1. 合适且许可允许的成熟组件直接复用；
2. PA 已有能力通过 adapter 接入；
3. 真正缺失的能力，扩展已有 PA 合同与实现；
4. 上游占位或特定平台插件，明确记录真实限制，不冒充已交付，不默默删除。

## 4. 完整行为与接入图

| 区域 | 必须完整交付的行为 | 上游定位 | PA 接入 / 实现归属 |
| --- | --- | --- | --- |
| Canvas 外框 | 安静大画布、返回/名称、收起的工具入口、Light/Dark、Windows 正常窗口行为 | smart-canvas.html；theme；shell 事件 | 候选独立入口；PA 现有导航/主题，正式 App Shell 不另行重设计 |
| 创建 | 空白处双击/快捷菜单；导入媒体、Prompt、分组、循环、视频工作节点；从零创作 | createMenu / createNodeFromMenu | React Flow 坐标 API + 节点定义；不是中心 onboarding 卡片 |
| 媒体节点 | 原比例、软件边框、名称/尺寸、独立 resize、导入替换、缺失文件可恢复 | nodeBodyHtml / singleMediaHtml / render | Asset/Result identity 投影；媒体不嵌入 scene |
| Hover/selection | 悬停删除、hover 端口、selected 工具条；键盘等价入口；多选抑制单节点条；拖动时动作隐藏 | CSS floating-node-actions / mini-x；syncSelectionUi；node-delete handler | public Handle/NodeToolbar/NodeResizeControl + 节点 chrome |
| 删除 | 删除场景实例及失效输入连接，可撤销；不删除底层 Asset/历史 Result；执行中删除视图不抹掉 Task | deleteNode / deleteNodeFromButton | 现有场景变更和 Task 生命周期分离 |
| 连线 | 输入/流程语义明确；拖动预览；端点重接；空白落点创建；选中/断开；非法组合解释；Undo | renderConnections / handlePortDrop / inputNodesFor / workflowInputNodesFor | public BaseEdge/getBezierPath；PA InputBinding adapter。线不是装饰，也不等于自动执行 |
| 多选与分组 | 框选/增选、移动/复制、成组/解组、整理排列、组内预览、拼接、批量导出；位置与来源不漂移 | smartGroupBodyHtml / smartGroupToolbarHtml / arrangeSmartIdsByConnections | React Flow group；现有 Asset/Result/export；整理只改场景几何 |
| Prompt 节点 | 输入/换行、输入框 resize、模板库、分隔符、拆分预览、上游文本/图片；编辑态不被 Canvas 快捷键劫持 | createPromptNode / promptNodeBodyHtml | 节点 draft 元数据；编译入口只进现有 Governor |
| Prompt 模板 | 搜索/分类、应用、创建、重命名、修改、删除、取消；区分共享模板与个人草稿 | promptTemplatePanel / promptPresetPanel | 复用现有设计方法/知识底座的合适边界；模板是明确用户文本，不是全篇自动注入 Skill |
| LLM 辅助 | 明确启用、输入指令/上游引用、模型/系统设置、运行状态、生成文本可编辑/可保留 | runPromptLLMNode / prompt-node-llm | 缺少准入执行时扩展现有 Command/Task adapter；不能新建 Agent/Memory；每次付费仍需确认 |
| Composer | 节点就近出现；图片/视频模式；引用 tray、加入/移除、媒体角色、@引用；Prompt、参数、运行组成一套表面 | updateComposer / positionComposerForNode / renderInputThumbsRow / renderMentionPicker | 复用 BeatDesign 输入隔离、Retake 参考选择/托盘已有实现；PA Draft adapter，不新写一套无关联面板 |
| 状态连续 | 普通 deselect/空白点击/对象切换保留 draft；每节点恢复 Prompt/ref/model/params；预览取消和失败不清空 | savePromptDraftForCurrent / loadPromptDraft | Draft 与 selection 解耦；稳定 React key/DOM，输入中不重挂载 composer、不重复解码全部缩略图 |
| 模型选择 | 可搜索、键盘完整、列表限高、当前选择清晰；推荐有依据；按 task/参数过滤；缺目录有有效恢复；不能静默换模型 | renderModelControl / renderDynamicParams | React Aria ComboBox/ListBox/Popover + PA Catalog/Identity/Overlay/Admission/Router。既有证据不能外推到新任务 |
| 参数选择 | 自动/预设/自定义；比例与分辨率联动；实际像素；质量、数量；视频时长/比例/分辨率；值保持与校验 | renderSizePickerControl / renderQualityControl / renderCountVisualControl / video controls | 现有参数契约；没有能力证据的选项不能假装可提交。切模型/参数使 preview stale |
| 执行确认 | 核对精确输入、当前 Prompt、约束、设计方法、模型和参数；取消不创建 Task；提交冻结 identity/context/evidence | buildPromptRequest / snapshotRunMeta | existing openCanvasAiPreview → execution-context/Governor → Command/Task |
| 运行/输出 | 来源不覆盖；新输出分支/多个方案；运行计时、进度、失败原因；Result 可继续参考；Task 恢复不创建新 attempt | runGeneration / createPendingOutputFromSource / resumeSmartPendingNode | existing Task/attempt/receipt/Result/lineage/backfill；不显示“生成单产品图”通用占位，不凭空编百分比 |
| 图片预览 | 上/下一张、对比源图、原图下载、整组下载、缩放、返回对象；透明通道正确显示 | openImagePreview / refreshComparePanel / downloadPreviewGroup | PA Asset/Result 读取；原图与缩略图分开；棋盘格只是软件预览 |
| 图片编辑 | crop/outpaint/mask/brush/resize/grid 的完整工具和取消/应用；原位返回 | openImageEditor / applyImageEdit | 现有 Fabric 公开能力与 PA local-edit adapter，不复制一个第二像素编辑器 |
| 裁剪 | 自由/原图/预设比例；八方向控制、移动范围；实际像素映射；取消不改变源 | applyImageCrop / cropBox | Fabric/既有 ROI 能力；本地编辑产出派生 Asset/Result，保留源 |
| 扩图 | 拖四边/角、预设/自定义尺寸、原图锚定、显示输出大小、准备扩图输入，再确认生成 | applyImageOutpaint / applyOutpaintSizeToSmartParams | existing outpaint geometry/Task 参数；不能把普通扩大图片当 AI 扩图 |
| 遮罩/画笔/文字 | 画笔半径/颜色/软化、矩形/椭圆/标记/文字、撤销/重做/清空、取消/应用 | mask/brush controls / drawing/text methods | Fabric/local-edit；用户明确编辑才改变作品；只改数字时不默认整图重绘 |
| 缩放/宫格 | 目标像素、比例保持；split/join；预设/自定义分割线、线的撤销、间隔、拼接布局、输出尺寸 | applyImageResize/GridSplit/GridJoin | 复用已有导出/图像处理；缺少的确定性切分/拼接补到本地处理 adapter，不能误用付费合照拆分 |
| 视频/音频素材 | 导入、封面/时长、播放暂停、预览、拖为引用；首尾/当前帧导出；音频不当图片加载 | mediaKind / exportVideoFrame / video preview | 已有视频 AssetStore；音频持久化缺口必须补现有 Asset contract，不建立新素材库 |
| 视频生成 | 同一 Composer；参考/首尾帧、时长、比例、分辨率、实际模型；Task 输出视频和封面，回到来源节点 | runApiVideoGeneration | 复用 PA offline-video Task/Result 先验证；真实视频 Provider 需要独立 adapter/admission/evidence，不继承图像 canary |
| 循环/工作流 | Prompt 分段、批次/轮次、串行/并行、起点、运行/停止；显式图连接；每步状态与分支 | smartLoopBodyHtml / smartCascadeGraphForTail / runSmartCascade | 有界执行计划编译为现有 Tasks；付费总次数/预算先确认，停止调度不能伪称远端已取消 |
| 视频片段时间轴 | 片段增删/选择、素材轨道、引用、播放头/预览、片段 Prompt/参数、片段结果和导出 | smartMinimaxBodyHtml / smartMinimax timeline methods | 媒体编排层对接现有 Task，不绑定外部 MiniMax/ComfyUI 后端；真实生成兼容性另有证据 Gate |
| 资产库 | 显隐、库/文件夹、搜索/hover 预览、拖入拖出、加入/移除、命名取消/确认 | renderAssetLibrary / assetPanel | PA 唯一 Asset store，不迁入外部 Board/asset backend |
| 日志/快捷键 | 可读真实执行信息、失败、参数和结果预览；快捷键与当前编辑状态一致 | renderSmartCanvasLog / smartShortcutModal | 现有 Task/trace/receipt 是事实源；不做另一套生成日志数据库 |
| 工作流导入导出 | 导出选中子图 JSON/含资源包，追加导入；ID/引用重新绑定；缺资源/不兼容报告；不带密钥 | exportSelectedSmartWorkflow / importSmartWorkflowFile | 节点 scene adapter + Asset exporter；不能绕过 Governor 执行导入内容 |
| 特殊预览 | 360 全景、对比、多媒体特殊预览的可用条件和返回 | panorama/compare methods | 需要时用成熟 permissive viewer；本地预览与生成能力分开验证 |
| 保存/恢复 | debounce 只保存元数据、冲突恢复、媒体不存在可恢复、切 Canvas、关闭重启、运行恢复 | loadCanvas / saveCanvas / canvasForStorage | existing PA Canvas service + replacement scene codec，禁止整个图片写 localStorage |

## 5. 重要细节不是用户逐项点名后才补

### Hover 删除的完整行为

- idle 不出现；hover 或 keyboard focus-within 出现；selected 仍有明确删除入口。
- 点击删除只影响该对象，不开始拖动、不顺带切换选区或清掉其他节点 draft。
- dragging/resize/connecting 时不触发误删；命中区域可点，tooltip/ARIA 可识别。
- 图内媒体项删除与整节点删除区分；只删一个方案不能删掉整组或原始资产。
- Undo 恢复节点、连接、位置和 draft；历史 Task/Result 及媒体文件保留。
- 键盘 Delete 不捕获文本编辑；焦点移到合理目标。需要确认的是不可逆资产删除，不是每次删场景实例都弹确认。
- 运行中移除节点不取消已提交 Task、也不删除 receipt；结果仍可从 Task/恢复入口找回。

### Composer 与参数浮层

- 对象锚定与屏幕可达同时满足：优先节点下方，碰边翻转/偏移，必要时暂时固定可操作区域。
- 面板高度有上限，内部能滚动；小窗口不遮死来源对象和确认按钮。
- 页面缩放、Canvas zoom、Windows DPI 分开处理；控件不随 artwork 缩放到不可点。
- 输入中不重挂载；IME composition、选中文字、粘贴、Tab、Escape 不被 Canvas 接管。
- Escape 分层关闭最上层浮层，恢复触发焦点，不丢草稿、不自动提交。
- 参数刷新保留展开态与滚动位置；一个参数变化不能把整个表单清空。
- ratio/resolution 值与真 capability 对齐；保留不兼容草稿并解释，不静默改成另一套值。

### 生成语义与质量边界

- 当前对象编辑、按参考创作、无图片生成是执行语义，不是三套割裂 UI。
- 连线输入、手动引用、当前源、首尾帧等角色明确；精确 Asset/Result 绑定。
- 小改数字不默认走产品主体重绘/商业白底流水线。局部编辑需要 ROI/mask 与对应 capability，不能把未验证模式伪装可用。
- 不追加无关历史；Skill/Knowledge/Profile 由现有 Governor 相关性筛选和冲突处理。
- 结果与来源并存，派生结果可继续迭代；不保证生成模型像素级复刻，需明确不可控质量与耗时边界。
- 付费执行显式确认；不因为连线、预览、hover、切选区或恢复而执行；每个 attempt 默认不自动付费重试。

## 6. 复用边界与具体组件范围

保留 React Flow 已验证的公共 primitives，不继续把当前两种节点和 copy/delete toolbar 当产品母版。

拟拆分以下 adapter/components（名称为方案，不宣称代码已存在）：

- NodeSceneCodec：在 PA Canvas 服务里版本化表示节点、连接、视图、draft；旧 scene 只读兼容和可逆迁移。
- MediaNode / PromptNode / GroupNode / LoopNode / ClipNode：各自投影媒体或创作输入，不另建 selection/store。
- NodeChrome / NodeActionToolbar / InputEdge：hover/focus/selected/drag 状态统一，业务动作只调用现有 handlers。
- NodeComposer / ReferenceTray：已有 BeatDesign/Retake 行为复用，节点 draft 独立于 selection。
- ModelPicker / OutputParameterPopover：React Aria 公共焦点/关闭/翻转能力 + 现有 admission。
- CanvasEditDialog：Fabric、ROI/mask、确定性图像处理和 return adapter。
- ExecutionProjection：已有 Task/Result/receipt/lineage 的视图和恢复，不拥有 Provider。
- WorkflowPlanAdapter / TransferAdapter：引用图、执行预算、子图导入导出转换；不导入外部执行系统。

来源合规的组件移植保留 commit/LICENSE/NOTICE。目标自定义许可下的实现只作为行为规格，不复制受限源码；这只改变复用方式，不允许降低行为完整度。

上游自动选第一个模型、潜在静默参数纠正、未完成工作流资产占位，不作为 PA 必须照搬的成熟答案。采用 PA 已确认的 no-fallback、真实状态和可靠持久化标准。

## 7. 连续实施顺序，不作为逐阶段用户暂停点

1. 先保护当前原型内存中未保存素材：导出恢复点/导入现有隔离 Asset pipeline；新存储读回通过后才迁移旧 localStorage，不先清掉旧副本。
2. 实现 versioned node scene codec 与原始媒体引用。当前契约不能表达的字段明确扩展并做兼容测试，不宣称“只是 CSS，不影响数据”。
3. 完整节点、Prompt、引用图、hover/selection/keyboard、创建/分组/整理和一套 Composer/参数控件同时接通。
4. 接 Governor/Task/Result/lineage，完成两轮 fixture、多方案、失败与恢复；先把图像工作流做成可连续使用的主体，不交孤立按钮给用户验收。
5. 接完整就近编辑/预览/Fabric；crop、mask、outpaint、brush、grid、export 的取消/应用与来源绑定全部完成。
6. 接媒体节点、视频参数、视频离线 Task/封面/播放/抽帧；补音频素材、LLM、循环/片段等真实缺口到现有合同。真实 Provider 能力不足明确报告，不用静态按钮充数。
7. 工作流传递/资产库/日志/快捷键，主题、小窗口/DPI、性能与失败注入统一收口。
8. 运行 packaged 完整旅程并内部修完发现的问题，再交用户一个可验收候选。普通实现问题不逐项确认；付费、真实数据破坏或不可逆变更仍需停下确认。
9. 用户产品验收后才考虑 legacy migration/read-back、rollback、Validated/Promote；本计划不授权当前正式版换位。

## 8. 交用户验收前必须完成的证据

### 完整旅程

- 空白 → 从零创建 Prompt/生成节点 → 参数/模型 → Governor 取消：不创建 Task、不丢草稿。
- 真实图片导入/粘贴/拖入 → hover 删除/撤销 → Prompt 与参考输入 → 两轮生成 fixture → 多方案并存 → 继续参考。
- 单选、多选、分组/解组、重接/删除连线：输入集合正确变化，旧 preview stale；其他无关场景变化不造成无故失效。
- 选当前 Result → 局部编辑/crop/outpaint/brush → Fabric 精修 → 取消或保存 → 原位返回 → lineage 精确。
- 视频导入/播放/首尾/当前帧 → 图作为视频输入 → 参数/preview → 明确标为 offline 的 Task 输出 → 回填/播放/重启。
- 模板/分段/循环/片段工作流：完整输入、预算、状态、停止调度与恢复，不能只验证按钮打开。
- 模型同步失败、无 eligible、保存冲突、断网、缺媒体、编辑保存失败、Task 中断均有有效恢复。

### 可靠性与体验

- 用用户同等级的多张 2K/4K 大图验证，不只两张小 fixture；图片字节不进 scene/localStorage。
- 代表性 30/100 节点与媒体混合场景测量加载、选择、输入、视图响应；不是用空节点数量冒充真实性能。
- 保存失败不能显示成功勾号；失败不能覆盖最后好快照；关闭/重启前确认 durable 状态。
- 正常、小窗口，100/125/150% DPI；各 popover/composer/editor 的命中、翻转、限高、滚动、焦点和来源可见性。
- hover/selected/dragging/focus 四种状态连续操作；中文 IME 组合态、Tab/Escape、Delete/快捷键作用域。
- Light/Dark 只改软件 UI，用户图片、文字、shape 像素与文档 hash 不发生主题导致的变化。
- 任务/Result/receipt/attempt 恢复前后不变；恢复只能查询/回填，不能重新生成。
- 最终提交源、冻结 compiled prompt/参数与实际 Task input 一致；Provider 默认 0 调用，live 验证单独授权。
- 每个 inventory 条目都有 PA disposition 和验证状态；未接通功能不能隐藏在“全部通过”的声明下。

## 9. 明确尚不能承诺的部分

这不是几次 CSS 修改：持久化 codec、真实视频准入、音频资产、LLM、循环/片段合同属于真实工程增量。现有图像 canary 不证明视频/LLM，更不证明生成质量排名。

第一次 packaged 验收必须明确区分本地真功能、offline execution evidence 和 live-verified capability。若实装过程中某功能无法在当前接口闭环，报告准确阻塞；不默默删掉，也不伪造成功结果。

上游资产库的 workflow tab 本身标为预留，与其独立 JSON/ZIP 导入导出不是同一能力。全量对标不意味着复制尚未实现的占位。

不整体复制上游账户/Provider/ComfyUI/RunningHub 后端。可用 PA Provider route 用 adapter 接入；特定供应商能力是否可用由真实 Catalog、contract、admission/evidence 决定。

## 附录 A：107 个静态按钮声明

这是源码定位清单，不是运行时点击验收。没有 id 的按钮由 data/onclick 定位；事件绑定继续追踪 smart-canvas.js。

| HTML 行 | 标签/语义 | ID / data 定位 | 直接动作 | PA 验收 |
| --- | --- | --- | --- | --- |
| [40](https://github.com/hero8152/Infinite-Canvas/blob/67f49e4b9a0ac090bad1f5bd62ae385f8fb5394e/static/smart-canvas.html#L40) | 图片 | data-kind="image" | 事件绑定 | 未完成 PA 对应验收 |
| [41](https://github.com/hero8152/Infinite-Canvas/blob/67f49e4b9a0ac090bad1f5bd62ae385f8fb5394e/static/smart-canvas.html#L41) | 视频 | data-kind="video" | 事件绑定 | 未完成 PA 对应验收 |
| [49](https://github.com/hero8152/Infinite-Canvas/blob/67f49e4b9a0ac090bad1f5bd62ae385f8fb5394e/static/smart-canvas.html#L49) | 模板库 | composerTemplateBtn | 事件绑定 | 未完成 PA 对应验收 |
| [59](https://github.com/hero8152/Infinite-Canvas/blob/67f49e4b9a0ac090bad1f5bd62ae385f8fb5394e/static/smart-canvas.html#L59) | 一键运行 | cascadeRunBtn | 事件绑定 | 未完成 PA 对应验收 |
| [60](https://github.com/hero8152/Infinite-Canvas/blob/67f49e4b9a0ac090bad1f5bd62ae385f8fb5394e/static/smart-canvas.html#L60) | 运行 | runBtn | 事件绑定 | 未完成 PA 对应验收 |
| [65](https://github.com/hero8152/Infinite-Canvas/blob/67f49e4b9a0ac090bad1f5bd62ae385f8fb5394e/static/smart-canvas.html#L65) | 返回画布列表 | — | backToCanvasList() | 未完成 PA 对应验收 |
| [69](https://github.com/hero8152/Infinite-Canvas/blob/67f49e4b9a0ac090bad1f5bd62ae385f8fb5394e/static/smart-canvas.html#L69) | 上传 图片、音频、视频都能导入 | data-create-type="image" | 事件绑定 | 未完成 PA 对应验收 |
| [76](https://github.com/hero8152/Infinite-Canvas/blob/67f49e4b9a0ac090bad1f5bd62ae385f8fb5394e/static/smart-canvas.html#L76) | 分组 把提示词、图片、循环收进同一组 | data-create-type="group" | 事件绑定 | 未完成 PA 对应验收 |
| [83](https://github.com/hero8152/Infinite-Canvas/blob/67f49e4b9a0ac090bad1f5bd62ae385f8fb5394e/static/smart-canvas.html#L83) | 提示词 手写或用 LLM 生成文本 | data-create-type="prompt" | 事件绑定 | 未完成 PA 对应验收 |
| [90](https://github.com/hero8152/Infinite-Canvas/blob/67f49e4b9a0ac090bad1f5bd62ae385f8fb5394e/static/smart-canvas.html#L90) | 循环 控制运行轮数、批次和变量 | data-create-type="loop" | 事件绑定 | 未完成 PA 对应验收 |
| [97](https://github.com/hero8152/Infinite-Canvas/blob/67f49e4b9a0ac090bad1f5bd62ae385f8fb5394e/static/smart-canvas.html#L97) | MiniMax 时间轴片段生成视频 | data-create-type="minimax" | 事件绑定 | 未完成 PA 对应验收 |
| [110](https://github.com/hero8152/Infinite-Canvas/blob/67f49e4b9a0ac090bad1f5bd62ae385f8fb5394e/static/smart-canvas.html#L110) | 关闭 | promptPresetClose | 事件绑定 | 未完成 PA 对应验收 |
| [119](https://github.com/hero8152/Infinite-Canvas/blob/67f49e4b9a0ac090bad1f5bd62ae385f8fb5394e/static/smart-canvas.html#L119) | 应用 | promptPresetApply | 事件绑定 | 未完成 PA 对应验收 |
| [120](https://github.com/hero8152/Infinite-Canvas/blob/67f49e4b9a0ac090bad1f5bd62ae385f8fb5394e/static/smart-canvas.html#L120) | 删除 | promptPresetDelete | 事件绑定 | 未完成 PA 对应验收 |
| [122](https://github.com/hero8152/Infinite-Canvas/blob/67f49e4b9a0ac090bad1f5bd62ae385f8fb5394e/static/smart-canvas.html#L122) | 存为新预设 | promptPresetNew | 事件绑定 | 未完成 PA 对应验收 |
| [123](https://github.com/hero8152/Infinite-Canvas/blob/67f49e4b9a0ac090bad1f5bd62ae385f8fb5394e/static/smart-canvas.html#L123) | 保存修改 | promptPresetSave | 事件绑定 | 未完成 PA 对应验收 |
| [132](https://github.com/hero8152/Infinite-Canvas/blob/67f49e4b9a0ac090bad1f5bd62ae385f8fb5394e/static/smart-canvas.html#L132) | 关闭 | promptTemplateClose | 事件绑定 | 未完成 PA 对应验收 |
| [142](https://github.com/hero8152/Infinite-Canvas/blob/67f49e4b9a0ac090bad1f5bd62ae385f8fb5394e/static/smart-canvas.html#L142) | 整理选中 | smartArrangeBtn | 事件绑定 | 未完成 PA 对应验收 |
| [148](https://github.com/hero8152/Infinite-Canvas/blob/67f49e4b9a0ac090bad1f5bd62ae385f8fb5394e/static/smart-canvas.html#L148) | 工作流 | smartWorkflowToggle | 事件绑定 | 未完成 PA 对应验收 |
| [149](https://github.com/hero8152/Infinite-Canvas/blob/67f49e4b9a0ac090bad1f5bd62ae385f8fb5394e/static/smart-canvas.html#L149) | 快捷键 | smartShortcutToggle | openSmartCanvasShortcuts() | 未完成 PA 对应验收 |
| [150](https://github.com/hero8152/Infinite-Canvas/blob/67f49e4b9a0ac090bad1f5bd62ae385f8fb5394e/static/smart-canvas.html#L150) | 日志 | smartLogToggle | openSmartCanvasLog() | 未完成 PA 对应验收 |
| [151](https://github.com/hero8152/Infinite-Canvas/blob/67f49e4b9a0ac090bad1f5bd62ae385f8fb5394e/static/smart-canvas.html#L151) | 资产库 | assetToggle | 事件绑定 | 未完成 PA 对应验收 |
| [158](https://github.com/hero8152/Infinite-Canvas/blob/67f49e4b9a0ac090bad1f5bd62ae385f8fb5394e/static/smart-canvas.html#L158) | 关闭 | — | closeSmartWorkflowTransferModal() | 未完成 PA 对应验收 |
| [166](https://github.com/hero8152/Infinite-Canvas/blob/67f49e4b9a0ac090bad1f5bd62ae385f8fb5394e/static/smart-canvas.html#L166) | 导出 JSON | — | exportSelectedSmartWorkflow(false) | 未完成 PA 对应验收 |
| [167](https://github.com/hero8152/Infinite-Canvas/blob/67f49e4b9a0ac090bad1f5bd62ae385f8fb5394e/static/smart-canvas.html#L167) | 导出并包含资源 | — | exportSelectedSmartWorkflow(true) | 未完成 PA 对应验收 |
| [185](https://github.com/hero8152/Infinite-Canvas/blob/67f49e4b9a0ac090bad1f5bd62ae385f8fb5394e/static/smart-canvas.html#L185) | 收起 | assetCloseBtn | 事件绑定 | 未完成 PA 对应验收 |
| [188](https://github.com/hero8152/Infinite-Canvas/blob/67f49e4b9a0ac090bad1f5bd62ae385f8fb5394e/static/smart-canvas.html#L188) | 图片资产 | data-asset-tab="image" | 事件绑定 | 未完成 PA 对应验收 |
| [189](https://github.com/hero8152/Infinite-Canvas/blob/67f49e4b9a0ac090bad1f5bd62ae385f8fb5394e/static/smart-canvas.html#L189) | 工作流 | data-asset-tab="workflow" | 事件绑定 | 未完成 PA 对应验收 |
| [195](https://github.com/hero8152/Infinite-Canvas/blob/67f49e4b9a0ac090bad1f5bd62ae385f8fb5394e/static/smart-canvas.html#L195) | 新建文件夹 | assetAddCategoryBtn | 事件绑定 | 未完成 PA 对应验收 |
| [196](https://github.com/hero8152/Infinite-Canvas/blob/67f49e4b9a0ac090bad1f5bd62ae385f8fb5394e/static/smart-canvas.html#L196) | 重命名文件夹 | assetRenameCategoryBtn | 事件绑定 | 未完成 PA 对应验收 |
| [208](https://github.com/hero8152/Infinite-Canvas/blob/67f49e4b9a0ac090bad1f5bd62ae385f8fb5394e/static/smart-canvas.html#L208) | 取消 | assetDialogCancel | 事件绑定 | 未完成 PA 对应验收 |
| [209](https://github.com/hero8152/Infinite-Canvas/blob/67f49e4b9a0ac090bad1f5bd62ae385f8fb5394e/static/smart-canvas.html#L209) | 确定 | assetDialogOk | 事件绑定 | 未完成 PA 对应验收 |
| [222](https://github.com/hero8152/Infinite-Canvas/blob/67f49e4b9a0ac090bad1f5bd62ae385f8fb5394e/static/smart-canvas.html#L222) | 关闭 | — | closeSmartCanvasLog() | 未完成 PA 对应验收 |
| [231](https://github.com/hero8152/Infinite-Canvas/blob/67f49e4b9a0ac090bad1f5bd62ae385f8fb5394e/static/smart-canvas.html#L231) | 关闭 | — | closeSmartCanvasShortcuts() | 未完成 PA 对应验收 |
| [260](https://github.com/hero8152/Infinite-Canvas/blob/67f49e4b9a0ac090bad1f5bd62ae385f8fb5394e/static/smart-canvas.html#L260) | 预览 | data-image-edit-mode="preview" | 事件绑定 | 未完成 PA 对应验收 |
| [261](https://github.com/hero8152/Infinite-Canvas/blob/67f49e4b9a0ac090bad1f5bd62ae385f8fb5394e/static/smart-canvas.html#L261) | 裁剪 | data-image-edit-mode="crop" | 事件绑定 | 未完成 PA 对应验收 |
| [262](https://github.com/hero8152/Infinite-Canvas/blob/67f49e4b9a0ac090bad1f5bd62ae385f8fb5394e/static/smart-canvas.html#L262) | Expand | data-image-edit-mode="outpaint" | 事件绑定 | 未完成 PA 对应验收 |
| [263](https://github.com/hero8152/Infinite-Canvas/blob/67f49e4b9a0ac090bad1f5bd62ae385f8fb5394e/static/smart-canvas.html#L263) | 遮罩 | data-image-edit-mode="mask" | 事件绑定 | 未完成 PA 对应验收 |
| [264](https://github.com/hero8152/Infinite-Canvas/blob/67f49e4b9a0ac090bad1f5bd62ae385f8fb5394e/static/smart-canvas.html#L264) | 画笔 | data-image-edit-mode="brush" | 事件绑定 | 未完成 PA 对应验收 |
| [265](https://github.com/hero8152/Infinite-Canvas/blob/67f49e4b9a0ac090bad1f5bd62ae385f8fb5394e/static/smart-canvas.html#L265) | 缩放 | data-image-edit-mode="resize" | 事件绑定 | 未完成 PA 对应验收 |
| [266](https://github.com/hero8152/Infinite-Canvas/blob/67f49e4b9a0ac090bad1f5bd62ae385f8fb5394e/static/smart-canvas.html#L266) | 宫格切分 | data-image-edit-mode="grid" | 事件绑定 | 未完成 PA 对应验收 |
| [268](https://github.com/hero8152/Infinite-Canvas/blob/67f49e4b9a0ac090bad1f5bd62ae385f8fb5394e/static/smart-canvas.html#L268) | 关闭 | — | closeImageEditor() | 未完成 PA 对应验收 |
| [272](https://github.com/hero8152/Infinite-Canvas/blob/67f49e4b9a0ac090bad1f5bd62ae385f8fb5394e/static/smart-canvas.html#L272) | 自由 | data-crop-ratio="free" | 事件绑定 | 未完成 PA 对应验收 |
| [273](https://github.com/hero8152/Infinite-Canvas/blob/67f49e4b9a0ac090bad1f5bd62ae385f8fb5394e/static/smart-canvas.html#L273) | 原图 | data-crop-ratio="source" | 事件绑定 | 未完成 PA 对应验收 |
| [274](https://github.com/hero8152/Infinite-Canvas/blob/67f49e4b9a0ac090bad1f5bd62ae385f8fb5394e/static/smart-canvas.html#L274) | 1:1 | data-crop-ratio="1:1" | 事件绑定 | 未完成 PA 对应验收 |
| [275](https://github.com/hero8152/Infinite-Canvas/blob/67f49e4b9a0ac090bad1f5bd62ae385f8fb5394e/static/smart-canvas.html#L275) | 4:3 | data-crop-ratio="4:3" | 事件绑定 | 未完成 PA 对应验收 |
| [276](https://github.com/hero8152/Infinite-Canvas/blob/67f49e4b9a0ac090bad1f5bd62ae385f8fb5394e/static/smart-canvas.html#L276) | 3:4 | data-crop-ratio="3:4" | 事件绑定 | 未完成 PA 对应验收 |
| [277](https://github.com/hero8152/Infinite-Canvas/blob/67f49e4b9a0ac090bad1f5bd62ae385f8fb5394e/static/smart-canvas.html#L277) | 16:9 | data-crop-ratio="16:9" | 事件绑定 | 未完成 PA 对应验收 |
| [278](https://github.com/hero8152/Infinite-Canvas/blob/67f49e4b9a0ac090bad1f5bd62ae385f8fb5394e/static/smart-canvas.html#L278) | 9:16 | data-crop-ratio="9:16" | 事件绑定 | 未完成 PA 对应验收 |
| [279](https://github.com/hero8152/Infinite-Canvas/blob/67f49e4b9a0ac090bad1f5bd62ae385f8fb5394e/static/smart-canvas.html#L279) | 3:2 | data-crop-ratio="3:2" | 事件绑定 | 未完成 PA 对应验收 |
| [280](https://github.com/hero8152/Infinite-Canvas/blob/67f49e4b9a0ac090bad1f5bd62ae385f8fb5394e/static/smart-canvas.html#L280) | 2:3 | data-crop-ratio="2:3" | 事件绑定 | 未完成 PA 对应验收 |
| [288](https://github.com/hero8152/Infinite-Canvas/blob/67f49e4b9a0ac090bad1f5bd62ae385f8fb5394e/static/smart-canvas.html#L288) | 1:1 | — | 事件绑定 | 未完成 PA 对应验收 |
| [289](https://github.com/hero8152/Infinite-Canvas/blob/67f49e4b9a0ac090bad1f5bd62ae385f8fb5394e/static/smart-canvas.html#L289) | 2:3 | — | 事件绑定 | 未完成 PA 对应验收 |
| [290](https://github.com/hero8152/Infinite-Canvas/blob/67f49e4b9a0ac090bad1f5bd62ae385f8fb5394e/static/smart-canvas.html#L290) | 3:2 | — | 事件绑定 | 未完成 PA 对应验收 |
| [291](https://github.com/hero8152/Infinite-Canvas/blob/67f49e4b9a0ac090bad1f5bd62ae385f8fb5394e/static/smart-canvas.html#L291) | 3:4 | — | 事件绑定 | 未完成 PA 对应验收 |
| [292](https://github.com/hero8152/Infinite-Canvas/blob/67f49e4b9a0ac090bad1f5bd62ae385f8fb5394e/static/smart-canvas.html#L292) | 4:3 | — | 事件绑定 | 未完成 PA 对应验收 |
| [293](https://github.com/hero8152/Infinite-Canvas/blob/67f49e4b9a0ac090bad1f5bd62ae385f8fb5394e/static/smart-canvas.html#L293) | 9:16 | — | 事件绑定 | 未完成 PA 对应验收 |
| [294](https://github.com/hero8152/Infinite-Canvas/blob/67f49e4b9a0ac090bad1f5bd62ae385f8fb5394e/static/smart-canvas.html#L294) | 16:9 | — | 事件绑定 | 未完成 PA 对应验收 |
| [295](https://github.com/hero8152/Infinite-Canvas/blob/67f49e4b9a0ac090bad1f5bd62ae385f8fb5394e/static/smart-canvas.html#L295) | 21:9 | — | 事件绑定 | 未完成 PA 对应验收 |
| [296](https://github.com/hero8152/Infinite-Canvas/blob/67f49e4b9a0ac090bad1f5bd62ae385f8fb5394e/static/smart-canvas.html#L296) | 9:21 | — | 事件绑定 | 未完成 PA 对应验收 |
| [297](https://github.com/hero8152/Infinite-Canvas/blob/67f49e4b9a0ac090bad1f5bd62ae385f8fb5394e/static/smart-canvas.html#L297) | 自定义 | — | 事件绑定 | 未完成 PA 对应验收 |
| [309](https://github.com/hero8152/Infinite-Canvas/blob/67f49e4b9a0ac090bad1f5bd62ae385f8fb5394e/static/smart-canvas.html#L309) | 撤销 | maskUndoBtn | undoEditDrawing() | 未完成 PA 对应验收 |
| [310](https://github.com/hero8152/Infinite-Canvas/blob/67f49e4b9a0ac090bad1f5bd62ae385f8fb5394e/static/smart-canvas.html#L310) | 恢复 | maskRedoBtn | redoEditDrawing() | 未完成 PA 对应验收 |
| [311](https://github.com/hero8152/Infinite-Canvas/blob/67f49e4b9a0ac090bad1f5bd62ae385f8fb5394e/static/smart-canvas.html#L311) | 清空 | — | clearEditDrawing() | 未完成 PA 对应验收 |
| [315](https://github.com/hero8152/Infinite-Canvas/blob/67f49e4b9a0ac090bad1f5bd62ae385f8fb5394e/static/smart-canvas.html#L315) | 自由画笔 | — | setBrushTool('free') | 未完成 PA 对应验收 |
| [316](https://github.com/hero8152/Infinite-Canvas/blob/67f49e4b9a0ac090bad1f5bd62ae385f8fb5394e/static/smart-canvas.html#L316) | 矩形 | — | setBrushTool('rect') | 未完成 PA 对应验收 |
| [317](https://github.com/hero8152/Infinite-Canvas/blob/67f49e4b9a0ac090bad1f5bd62ae385f8fb5394e/static/smart-canvas.html#L317) | 椭圆 | — | setBrushTool('ellipse') | 未完成 PA 对应验收 |
| [318](https://github.com/hero8152/Infinite-Canvas/blob/67f49e4b9a0ac090bad1f5bd62ae385f8fb5394e/static/smart-canvas.html#L318) | 数字标签 | — | setBrushTool('label') | 未完成 PA 对应验收 |
| [319](https://github.com/hero8152/Infinite-Canvas/blob/67f49e4b9a0ac090bad1f5bd62ae385f8fb5394e/static/smart-canvas.html#L319) | 文字标签 | — | setBrushTool('text') | 未完成 PA 对应验收 |
| [322](https://github.com/hero8152/Infinite-Canvas/blob/67f49e4b9a0ac090bad1f5bd62ae385f8fb5394e/static/smart-canvas.html#L322) | 撤销 | brushUndoBtn | undoEditDrawing() | 未完成 PA 对应验收 |
| [323](https://github.com/hero8152/Infinite-Canvas/blob/67f49e4b9a0ac090bad1f5bd62ae385f8fb5394e/static/smart-canvas.html#L323) | 恢复 | brushRedoBtn | redoEditDrawing() | 未完成 PA 对应验收 |
| [324](https://github.com/hero8152/Infinite-Canvas/blob/67f49e4b9a0ac090bad1f5bd62ae385f8fb5394e/static/smart-canvas.html#L324) | 清空 | — | clearEditDrawing() | 未完成 PA 对应验收 |
| [333](https://github.com/hero8152/Infinite-Canvas/blob/67f49e4b9a0ac090bad1f5bd62ae385f8fb5394e/static/smart-canvas.html#L333) | 宫格切分 | gridSplitModeBtn | setGridOperationMode('split') | 未完成 PA 对应验收 |
| [334](https://github.com/hero8152/Infinite-Canvas/blob/67f49e4b9a0ac090bad1f5bd62ae385f8fb5394e/static/smart-canvas.html#L334) | 宫格拼接 | gridJoinModeBtn | setGridOperationMode('join') | 未完成 PA 对应验收 |
| [336](https://github.com/hero8152/Infinite-Canvas/blob/67f49e4b9a0ac090bad1f5bd62ae385f8fb5394e/static/smart-canvas.html#L336) | 自定义 | gridCustomToggle | toggleGridCustomMode() | 未完成 PA 对应验收 |
| [339](https://github.com/hero8152/Infinite-Canvas/blob/67f49e4b9a0ac090bad1f5bd62ae385f8fb5394e/static/smart-canvas.html#L339) | 1×2 | — | applyGridPreset(1, 2) | 未完成 PA 对应验收 |
| [340](https://github.com/hero8152/Infinite-Canvas/blob/67f49e4b9a0ac090bad1f5bd62ae385f8fb5394e/static/smart-canvas.html#L340) | 2×1 | — | applyGridPreset(2, 1) | 未完成 PA 对应验收 |
| [341](https://github.com/hero8152/Infinite-Canvas/blob/67f49e4b9a0ac090bad1f5bd62ae385f8fb5394e/static/smart-canvas.html#L341) | 2×2 | — | applyGridPreset(2, 2) | 未完成 PA 对应验收 |
| [342](https://github.com/hero8152/Infinite-Canvas/blob/67f49e4b9a0ac090bad1f5bd62ae385f8fb5394e/static/smart-canvas.html#L342) | 2×3 | — | applyGridPreset(2, 3) | 未完成 PA 对应验收 |
| [343](https://github.com/hero8152/Infinite-Canvas/blob/67f49e4b9a0ac090bad1f5bd62ae385f8fb5394e/static/smart-canvas.html#L343) | 3×2 | — | applyGridPreset(3, 2) | 未完成 PA 对应验收 |
| [344](https://github.com/hero8152/Infinite-Canvas/blob/67f49e4b9a0ac090bad1f5bd62ae385f8fb5394e/static/smart-canvas.html#L344) | 3×3 | — | applyGridPreset(3, 3) | 未完成 PA 对应验收 |
| [348](https://github.com/hero8152/Infinite-Canvas/blob/67f49e4b9a0ac090bad1f5bd62ae385f8fb5394e/static/smart-canvas.html#L348) | 1×2 | — | applyGridJoinPreset(1, 2) | 未完成 PA 对应验收 |
| [349](https://github.com/hero8152/Infinite-Canvas/blob/67f49e4b9a0ac090bad1f5bd62ae385f8fb5394e/static/smart-canvas.html#L349) | 2×1 | — | applyGridJoinPreset(2, 1) | 未完成 PA 对应验收 |
| [350](https://github.com/hero8152/Infinite-Canvas/blob/67f49e4b9a0ac090bad1f5bd62ae385f8fb5394e/static/smart-canvas.html#L350) | 2×2 | — | applyGridJoinPreset(2, 2) | 未完成 PA 对应验收 |
| [351](https://github.com/hero8152/Infinite-Canvas/blob/67f49e4b9a0ac090bad1f5bd62ae385f8fb5394e/static/smart-canvas.html#L351) | 2×3 | — | applyGridJoinPreset(2, 3) | 未完成 PA 对应验收 |
| [352](https://github.com/hero8152/Infinite-Canvas/blob/67f49e4b9a0ac090bad1f5bd62ae385f8fb5394e/static/smart-canvas.html#L352) | 3×2 | — | applyGridJoinPreset(3, 2) | 未完成 PA 对应验收 |
| [353](https://github.com/hero8152/Infinite-Canvas/blob/67f49e4b9a0ac090bad1f5bd62ae385f8fb5394e/static/smart-canvas.html#L353) | 3×3 | — | applyGridJoinPreset(3, 3) | 未完成 PA 对应验收 |
| [354](https://github.com/hero8152/Infinite-Canvas/blob/67f49e4b9a0ac090bad1f5bd62ae385f8fb5394e/static/smart-canvas.html#L354) | 自动 | — | resetGridJoinLayout() | 未完成 PA 对应验收 |
| [358](https://github.com/hero8152/Infinite-Canvas/blob/67f49e4b9a0ac090bad1f5bd62ae385f8fb5394e/static/smart-canvas.html#L358) | 1K | data-grid-join-size="1024" | setGridJoinOutputSize(1024) | 未完成 PA 对应验收 |
| [359](https://github.com/hero8152/Infinite-Canvas/blob/67f49e4b9a0ac090bad1f5bd62ae385f8fb5394e/static/smart-canvas.html#L359) | 2K | data-grid-join-size="2048" | setGridJoinOutputSize(2048) | 未完成 PA 对应验收 |
| [360](https://github.com/hero8152/Infinite-Canvas/blob/67f49e4b9a0ac090bad1f5bd62ae385f8fb5394e/static/smart-canvas.html#L360) | 4K | data-grid-join-size="4096" | setGridJoinOutputSize(4096) | 未完成 PA 对应验收 |
| [367](https://github.com/hero8152/Infinite-Canvas/blob/67f49e4b9a0ac090bad1f5bd62ae385f8fb5394e/static/smart-canvas.html#L367) | 水平 | gridOrientH | setGridCustomOrientation('h') | 未完成 PA 对应验收 |
| [368](https://github.com/hero8152/Infinite-Canvas/blob/67f49e4b9a0ac090bad1f5bd62ae385f8fb5394e/static/smart-canvas.html#L368) | 垂直 | gridOrientV | setGridCustomOrientation('v') | 未完成 PA 对应验收 |
| [369](https://github.com/hero8152/Infinite-Canvas/blob/67f49e4b9a0ac090bad1f5bd62ae385f8fb5394e/static/smart-canvas.html#L369) | canvas.gridUndo | gridUndoBtn | undoGridCustomLine() | 未完成 PA 对应验收 |
| [370](https://github.com/hero8152/Infinite-Canvas/blob/67f49e4b9a0ac090bad1f5bd62ae385f8fb5394e/static/smart-canvas.html#L370) | canvas.gridClearLines | — | clearGridCustomLines() | 未完成 PA 对应验收 |
| [376](https://github.com/hero8152/Infinite-Canvas/blob/67f49e4b9a0ac090bad1f5bd62ae385f8fb5394e/static/smart-canvas.html#L376) | 上一张 | previewPrevBtn | navigatePreviewImage(-1) | 未完成 PA 对应验收 |
| [377](https://github.com/hero8152/Infinite-Canvas/blob/67f49e4b9a0ac090bad1f5bd62ae385f8fb5394e/static/smart-canvas.html#L377) | 下一张 | previewNextBtn | navigatePreviewImage(1) | 未完成 PA 对应验收 |
| [420](https://github.com/hero8152/Infinite-Canvas/blob/67f49e4b9a0ac090bad1f5bd62ae385f8fb5394e/static/smart-canvas.html#L420) | Download | previewDownloadBtn | downloadPreviewImage() | 未完成 PA 对应验收 |
| [421](https://github.com/hero8152/Infinite-Canvas/blob/67f49e4b9a0ac090bad1f5bd62ae385f8fb5394e/static/smart-canvas.html#L421) | 下载全部 | previewDownloadAllBtn | downloadPreviewGroup() | 未完成 PA 对应验收 |
| [426](https://github.com/hero8152/Infinite-Canvas/blob/67f49e4b9a0ac090bad1f5bd62ae385f8fb5394e/static/smart-canvas.html#L426) | 360全景 | panoramaToggleBtn | togglePanoramaPreview() | 未完成 PA 对应验收 |
| [427](https://github.com/hero8152/Infinite-Canvas/blob/67f49e4b9a0ac090bad1f5bd62ae385f8fb5394e/static/smart-canvas.html#L427) | 导出画面 | panoramaExportBtn | exportPanoramaFrame() | 未完成 PA 对应验收 |
| [428](https://github.com/hero8152/Infinite-Canvas/blob/67f49e4b9a0ac090bad1f5bd62ae385f8fb5394e/static/smart-canvas.html#L428) | 对比原图 | compareToggleBtn | togglePreviewCompare() | 未完成 PA 对应验收 |
| [435](https://github.com/hero8152/Infinite-Canvas/blob/67f49e4b9a0ac090bad1f5bd62ae385f8fb5394e/static/smart-canvas.html#L435) | 导出首帧 | — | exportVideoFrame('first') | 未完成 PA 对应验收 |
| [436](https://github.com/hero8152/Infinite-Canvas/blob/67f49e4b9a0ac090bad1f5bd62ae385f8fb5394e/static/smart-canvas.html#L436) | 导出当前帧 | — | exportVideoFrame('current') | 未完成 PA 对应验收 |
| [437](https://github.com/hero8152/Infinite-Canvas/blob/67f49e4b9a0ac090bad1f5bd62ae385f8fb5394e/static/smart-canvas.html#L437) | 导出尾帧 | — | exportVideoFrame('last') | 未完成 PA 对应验收 |
| [439](https://github.com/hero8152/Infinite-Canvas/blob/67f49e4b9a0ac090bad1f5bd62ae385f8fb5394e/static/smart-canvas.html#L439) | 取消 | imageEditCancelBtn | closeImageEditor() | 未完成 PA 对应验收 |
| [440](https://github.com/hero8152/Infinite-Canvas/blob/67f49e4b9a0ac090bad1f5bd62ae385f8fb5394e/static/smart-canvas.html#L440) | 应用裁剪 | imageEditApplyBtn | applyImageEdit() | 未完成 PA 对应验收 |

## 附录 B：134 个动态按钮模板声明

定位按模板出现的源码行与最近顶层函数机械归属；条件分支/map 会展开更多实际控件。需结合动作数组、事件委托和状态运行，不把此表当作完整调用图或已理解/已验收证明。

| JS 行 | 模板所在函数 | data 定位 | PA 验收 |
| --- | --- | --- | --- |
| [2638](https://github.com/hero8152/Infinite-Canvas/blob/67f49e4b9a0ac090bad1f5bd62ae385f8fb5394e/static/js/smart-canvas.js#L2638) | renderVideoProviderControl | — | 未完成 PA 对应验收 |
| [2642](https://github.com/hero8152/Infinite-Canvas/blob/67f49e4b9a0ac090bad1f5bd62ae385f8fb5394e/static/js/smart-canvas.js#L2642) | renderVideoProviderControl | data-smart-param, data-smart-value | 未完成 PA 对应验收 |
| [2649](https://github.com/hero8152/Infinite-Canvas/blob/67f49e4b9a0ac090bad1f5bd62ae385f8fb5394e/static/js/smart-canvas.js#L2649) | renderVideoModelControl | — | 未完成 PA 对应验收 |
| [2653](https://github.com/hero8152/Infinite-Canvas/blob/67f49e4b9a0ac090bad1f5bd62ae385f8fb5394e/static/js/smart-canvas.js#L2653) | renderVideoModelControl | data-smart-param, data-smart-value | 未完成 PA 对应验收 |
| [2662](https://github.com/hero8152/Infinite-Canvas/blob/67f49e4b9a0ac090bad1f5bd62ae385f8fb5394e/static/js/smart-canvas.js#L2662) | renderVideoDurationControl | — | 未完成 PA 对应验收 |
| [2666](https://github.com/hero8152/Infinite-Canvas/blob/67f49e4b9a0ac090bad1f5bd62ae385f8fb5394e/static/js/smart-canvas.js#L2666) | renderVideoDurationControl | data-smart-param, data-smart-value | 未完成 PA 对应验收 |
| [2683](https://github.com/hero8152/Infinite-Canvas/blob/67f49e4b9a0ac090bad1f5bd62ae385f8fb5394e/static/js/smart-canvas.js#L2683) | renderVideoAspectControl | — | 未完成 PA 对应验收 |
| [2687](https://github.com/hero8152/Infinite-Canvas/blob/67f49e4b9a0ac090bad1f5bd62ae385f8fb5394e/static/js/smart-canvas.js#L2687) | renderVideoAspectControl | data-smart-param, data-smart-value | 未完成 PA 对应验收 |
| [2697](https://github.com/hero8152/Infinite-Canvas/blob/67f49e4b9a0ac090bad1f5bd62ae385f8fb5394e/static/js/smart-canvas.js#L2697) | renderVideoResolutionControl | — | 未完成 PA 对应验收 |
| [2701](https://github.com/hero8152/Infinite-Canvas/blob/67f49e4b9a0ac090bad1f5bd62ae385f8fb5394e/static/js/smart-canvas.js#L2701) | renderVideoResolutionControl | data-smart-param, data-smart-value | 未完成 PA 对应验收 |
| [2708](https://github.com/hero8152/Infinite-Canvas/blob/67f49e4b9a0ac090bad1f5bd62ae385f8fb5394e/static/js/smart-canvas.js#L2708) | renderVideoToggleControl | data-toggle-param | 未完成 PA 对应验收 |
| [2711](https://github.com/hero8152/Infinite-Canvas/blob/67f49e4b9a0ac090bad1f5bd62ae385f8fb5394e/static/js/smart-canvas.js#L2711) | renderTempShUploadControl | data-temp-sh-upload-video | 未完成 PA 对应验收 |
| [2714](https://github.com/hero8152/Infinite-Canvas/blob/67f49e4b9a0ac090bad1f5bd62ae385f8fb5394e/static/js/smart-canvas.js#L2714) | renderManualVideoUrlControl | data-manual-video-url | 未完成 PA 对应验收 |
| [2723](https://github.com/hero8152/Infinite-Canvas/blob/67f49e4b9a0ac090bad1f5bd62ae385f8fb5394e/static/js/smart-canvas.js#L2723) | renderVideoTrustedAssetControl | data-trusted-source | 未完成 PA 对应验收 |
| [2724](https://github.com/hero8152/Infinite-Canvas/blob/67f49e4b9a0ac090bad1f5bd62ae385f8fb5394e/static/js/smart-canvas.js#L2724) | renderVideoTrustedAssetControl | data-trusted-source | 未完成 PA 对应验收 |
| [2725](https://github.com/hero8152/Infinite-Canvas/blob/67f49e4b9a0ac090bad1f5bd62ae385f8fb5394e/static/js/smart-canvas.js#L2725) | renderVideoTrustedAssetControl | data-trusted-source | 未完成 PA 对应验收 |
| [2911](https://github.com/hero8152/Infinite-Canvas/blob/67f49e4b9a0ac090bad1f5bd62ae385f8fb5394e/static/js/smart-canvas.js#L2911) | renderJimengUpscaleControl | — | 未完成 PA 对应验收 |
| [2913](https://github.com/hero8152/Infinite-Canvas/blob/67f49e4b9a0ac090bad1f5bd62ae385f8fb5394e/static/js/smart-canvas.js#L2913) | renderJimengUpscaleControl | data-smart-param, data-smart-value | 未完成 PA 对应验收 |
| [3019](https://github.com/hero8152/Infinite-Canvas/blob/67f49e4b9a0ac090bad1f5bd62ae385f8fb5394e/static/js/smart-canvas.js#L3019) | renderRhConfigControl | data-smart-param, data-smart-value | 未完成 PA 对应验收 |
| [3023](https://github.com/hero8152/Infinite-Canvas/blob/67f49e4b9a0ac090bad1f5bd62ae385f8fb5394e/static/js/smart-canvas.js#L3023) | renderRhConfigControl | — | 未完成 PA 对应验收 |
| [3036](https://github.com/hero8152/Infinite-Canvas/blob/67f49e4b9a0ac090bad1f5bd62ae385f8fb5394e/static/js/smart-canvas.js#L3036) | renderRhPaymentControl | — | 未完成 PA 对应验收 |
| [3040](https://github.com/hero8152/Infinite-Canvas/blob/67f49e4b9a0ac090bad1f5bd62ae385f8fb5394e/static/js/smart-canvas.js#L3040) | renderRhPaymentControl | data-smart-param, data-smart-value | 未完成 PA 对应验收 |
| [3049](https://github.com/hero8152/Infinite-Canvas/blob/67f49e4b9a0ac090bad1f5bd62ae385f8fb5394e/static/js/smart-canvas.js#L3049) | renderRhMachineControl | — | 未完成 PA 对应验收 |
| [3053](https://github.com/hero8152/Infinite-Canvas/blob/67f49e4b9a0ac090bad1f5bd62ae385f8fb5394e/static/js/smart-canvas.js#L3053) | renderRhMachineControl | data-smart-param, data-smart-value | 未完成 PA 对应验收 |
| [3087](https://github.com/hero8152/Infinite-Canvas/blob/67f49e4b9a0ac090bad1f5bd62ae385f8fb5394e/static/js/smart-canvas.js#L3087) | renderComfyParams | data-toggle-param | 未完成 PA 对应验收 |
| [3090](https://github.com/hero8152/Infinite-Canvas/blob/67f49e4b9a0ac090bad1f5bd62ae385f8fb5394e/static/js/smart-canvas.js#L3090) | renderComfyParams | data-toggle-param | 未完成 PA 对应验收 |
| [3100](https://github.com/hero8152/Infinite-Canvas/blob/67f49e4b9a0ac090bad1f5bd62ae385f8fb5394e/static/js/smart-canvas.js#L3100) | renderComfyParams | — | 未完成 PA 对应验收 |
| [3104](https://github.com/hero8152/Infinite-Canvas/blob/67f49e4b9a0ac090bad1f5bd62ae385f8fb5394e/static/js/smart-canvas.js#L3104) | renderComfyParams | data-smart-param, data-smart-value | 未完成 PA 对应验收 |
| [3115](https://github.com/hero8152/Infinite-Canvas/blob/67f49e4b9a0ac090bad1f5bd62ae385f8fb5394e/static/js/smart-canvas.js#L3115) | renderUpscalePill | — | 未完成 PA 对应验收 |
| [3119](https://github.com/hero8152/Infinite-Canvas/blob/67f49e4b9a0ac090bad1f5bd62ae385f8fb5394e/static/js/smart-canvas.js#L3119) | renderUpscalePill | data-smart-param, data-smart-value | 未完成 PA 对应验收 |
| [3129](https://github.com/hero8152/Infinite-Canvas/blob/67f49e4b9a0ac090bad1f5bd62ae385f8fb5394e/static/js/smart-canvas.js#L3129) | renderComfyWorkflowControl | — | 未完成 PA 对应验收 |
| [3133](https://github.com/hero8152/Infinite-Canvas/blob/67f49e4b9a0ac090bad1f5bd62ae385f8fb5394e/static/js/smart-canvas.js#L3133) | renderComfyWorkflowControl | data-smart-param, data-smart-value | 未完成 PA 对应验收 |
| [3234](https://github.com/hero8152/Infinite-Canvas/blob/67f49e4b9a0ac090bad1f5bd62ae385f8fb5394e/static/js/smart-canvas.js#L3234) | renderProviderControl | — | 未完成 PA 对应验收 |
| [3238](https://github.com/hero8152/Infinite-Canvas/blob/67f49e4b9a0ac090bad1f5bd62ae385f8fb5394e/static/js/smart-canvas.js#L3238) | renderProviderControl | data-smart-param, data-smart-value | 未完成 PA 对应验收 |
| [3245](https://github.com/hero8152/Infinite-Canvas/blob/67f49e4b9a0ac090bad1f5bd62ae385f8fb5394e/static/js/smart-canvas.js#L3245) | renderModelControl | — | 未完成 PA 对应验收 |
| [3249](https://github.com/hero8152/Infinite-Canvas/blob/67f49e4b9a0ac090bad1f5bd62ae385f8fb5394e/static/js/smart-canvas.js#L3249) | renderModelControl | data-smart-param, data-smart-value | 未完成 PA 对应验收 |
| [3260](https://github.com/hero8152/Infinite-Canvas/blob/67f49e4b9a0ac090bad1f5bd62ae385f8fb5394e/static/js/smart-canvas.js#L3260) | renderMsFunctionControl | — | 未完成 PA 对应验收 |
| [3264](https://github.com/hero8152/Infinite-Canvas/blob/67f49e4b9a0ac090bad1f5bd62ae385f8fb5394e/static/js/smart-canvas.js#L3264) | renderMsFunctionControl | data-smart-param, data-smart-value | 未完成 PA 对应验收 |
| [3274](https://github.com/hero8152/Infinite-Canvas/blob/67f49e4b9a0ac090bad1f5bd62ae385f8fb5394e/static/js/smart-canvas.js#L3274) | renderMsCustomModelPill | — | 未完成 PA 对应验收 |
| [3278](https://github.com/hero8152/Infinite-Canvas/blob/67f49e4b9a0ac090bad1f5bd62ae385f8fb5394e/static/js/smart-canvas.js#L3278) | renderMsCustomModelPill | data-smart-param, data-smart-value | 未完成 PA 对应验收 |
| [3293](https://github.com/hero8152/Infinite-Canvas/blob/67f49e4b9a0ac090bad1f5bd62ae385f8fb5394e/static/js/smart-canvas.js#L3293) | renderRatioControl | — | 未完成 PA 对应验收 |
| [3297](https://github.com/hero8152/Infinite-Canvas/blob/67f49e4b9a0ac090bad1f5bd62ae385f8fb5394e/static/js/smart-canvas.js#L3297) | renderRatioControl | data-smart-param, data-smart-value | 未完成 PA 对应验收 |
| [3308](https://github.com/hero8152/Infinite-Canvas/blob/67f49e4b9a0ac090bad1f5bd62ae385f8fb5394e/static/js/smart-canvas.js#L3308) | renderResolutionControl | — | 未完成 PA 对应验收 |
| [3312](https://github.com/hero8152/Infinite-Canvas/blob/67f49e4b9a0ac090bad1f5bd62ae385f8fb5394e/static/js/smart-canvas.js#L3312) | renderResolutionControl | data-smart-param, data-smart-value | 未完成 PA 对应验收 |
| [3363](https://github.com/hero8152/Infinite-Canvas/blob/67f49e4b9a0ac090bad1f5bd62ae385f8fb5394e/static/js/smart-canvas.js#L3363) | renderSizePickerControl | — | 未完成 PA 对应验收 |
| [3368](https://github.com/hero8152/Infinite-Canvas/blob/67f49e4b9a0ac090bad1f5bd62ae385f8fb5394e/static/js/smart-canvas.js#L3368) | renderSizePickerControl | data-size-scope, data-size-prefix | 未完成 PA 对应验收 |
| [3369](https://github.com/hero8152/Infinite-Canvas/blob/67f49e4b9a0ac090bad1f5bd62ae385f8fb5394e/static/js/smart-canvas.js#L3369) | renderSizePickerControl | data-size-scope, data-size-prefix | 未完成 PA 对应验收 |
| [3370](https://github.com/hero8152/Infinite-Canvas/blob/67f49e4b9a0ac090bad1f5bd62ae385f8fb5394e/static/js/smart-canvas.js#L3370) | renderSizePickerControl | data-size-scope, data-size-prefix | 未完成 PA 对应验收 |
| [3376](https://github.com/hero8152/Infinite-Canvas/blob/67f49e4b9a0ac090bad1f5bd62ae385f8fb5394e/static/js/smart-canvas.js#L3376) | renderSizePickerControl | data-smart-param, data-smart-value | 未完成 PA 对应验收 |
| [3379](https://github.com/hero8152/Infinite-Canvas/blob/67f49e4b9a0ac090bad1f5bd62ae385f8fb5394e/static/js/smart-canvas.js#L3379) | renderSizePickerControl | data-smart-param, data-smart-value | 未完成 PA 对应验收 |
| [3420](https://github.com/hero8152/Infinite-Canvas/blob/67f49e4b9a0ac090bad1f5bd62ae385f8fb5394e/static/js/smart-canvas.js#L3420) | renderQualityControl | — | 未完成 PA 对应验收 |
| [3424](https://github.com/hero8152/Infinite-Canvas/blob/67f49e4b9a0ac090bad1f5bd62ae385f8fb5394e/static/js/smart-canvas.js#L3424) | renderQualityControl | data-smart-param, data-smart-value | 未完成 PA 对应验收 |
| [3432](https://github.com/hero8152/Infinite-Canvas/blob/67f49e4b9a0ac090bad1f5bd62ae385f8fb5394e/static/js/smart-canvas.js#L3432) | renderCountVisualControl | — | 未完成 PA 对应验收 |
| [3436](https://github.com/hero8152/Infinite-Canvas/blob/67f49e4b9a0ac090bad1f5bd62ae385f8fb5394e/static/js/smart-canvas.js#L3436) | renderCountVisualControl | data-smart-param, data-smart-value | 未完成 PA 对应验收 |
| [3464](https://github.com/hero8152/Infinite-Canvas/blob/67f49e4b9a0ac090bad1f5bd62ae385f8fb5394e/static/js/smart-canvas.js#L3464) | renderComfySettingField | data-comfy-bool | 未完成 PA 对应验收 |
| [3469](https://github.com/hero8152/Infinite-Canvas/blob/67f49e4b9a0ac090bad1f5bd62ae385f8fb5394e/static/js/smart-canvas.js#L3469) | renderComfySettingField | — | 未完成 PA 对应验收 |
| [3473](https://github.com/hero8152/Infinite-Canvas/blob/67f49e4b9a0ac090bad1f5bd62ae385f8fb5394e/static/js/smart-canvas.js#L3473) | renderComfySettingField | data-comfy-pick, data-comfy-value | 未完成 PA 对应验收 |
| [3490](https://github.com/hero8152/Infinite-Canvas/blob/67f49e4b9a0ac090bad1f5bd62ae385f8fb5394e/static/js/smart-canvas.js#L3490) | renderComfySettingField | data-comfy-random | 未完成 PA 对应验收 |
| [4005](https://github.com/hero8152/Infinite-Canvas/blob/67f49e4b9a0ac090bad1f5bd62ae385f8fb5394e/static/js/smart-canvas.js#L4005) | renderRhSettingField | data-rh-bool | 未完成 PA 对应验收 |
| [4013](https://github.com/hero8152/Infinite-Canvas/blob/67f49e4b9a0ac090bad1f5bd62ae385f8fb5394e/static/js/smart-canvas.js#L4013) | renderRhSettingField | — | 未完成 PA 对应验收 |
| [4023](https://github.com/hero8152/Infinite-Canvas/blob/67f49e4b9a0ac090bad1f5bd62ae385f8fb5394e/static/js/smart-canvas.js#L4023) | renderRhSettingField | — | 未完成 PA 对应验收 |
| [4027](https://github.com/hero8152/Infinite-Canvas/blob/67f49e4b9a0ac090bad1f5bd62ae385f8fb5394e/static/js/smart-canvas.js#L4027) | renderRhSettingField | data-rh-pick, data-rh-value | 未完成 PA 对应验收 |
| [4039](https://github.com/hero8152/Infinite-Canvas/blob/67f49e4b9a0ac090bad1f5bd62ae385f8fb5394e/static/js/smart-canvas.js#L4039) | renderRhSettingField | data-rh-random | 未完成 PA 对应验收 |
| [4678](https://github.com/hero8152/Infinite-Canvas/blob/67f49e4b9a0ac090bad1f5bd62ae385f8fb5394e/static/js/smart-canvas.js#L4678) | renderPromptTemplatePanel | data-template-cat-new | 未完成 PA 对应验收 |
| [4679](https://github.com/hero8152/Infinite-Canvas/blob/67f49e4b9a0ac090bad1f5bd62ae385f8fb5394e/static/js/smart-canvas.js#L4679) | renderPromptTemplatePanel | data-template-group-edit | 未完成 PA 对应验收 |
| [4685](https://github.com/hero8152/Infinite-Canvas/blob/67f49e4b9a0ac090bad1f5bd62ae385f8fb5394e/static/js/smart-canvas.js#L4685) | renderPromptTemplatePanel | data-template-cat | 未完成 PA 对应验收 |
| [4689](https://github.com/hero8152/Infinite-Canvas/blob/67f49e4b9a0ac090bad1f5bd62ae385f8fb5394e/static/js/smart-canvas.js#L4689) | renderPromptTemplatePanel | data-template-cat-edit | 未完成 PA 对应验收 |
| [4690](https://github.com/hero8152/Infinite-Canvas/blob/67f49e4b9a0ac090bad1f5bd62ae385f8fb5394e/static/js/smart-canvas.js#L4690) | renderPromptTemplatePanel | data-template-cat-delete | 未完成 PA 对应验收 |
| [4699](https://github.com/hero8152/Infinite-Canvas/blob/67f49e4b9a0ac090bad1f5bd62ae385f8fb5394e/static/js/smart-canvas.js#L4699) | renderPromptTemplatePanel | data-template-cat | 未完成 PA 对应验收 |
| [4705](https://github.com/hero8152/Infinite-Canvas/blob/67f49e4b9a0ac090bad1f5bd62ae385f8fb5394e/static/js/smart-canvas.js#L4705) | renderPromptTemplatePanel | data-template-group-edit | 未完成 PA 对应验收 |
| [4727](https://github.com/hero8152/Infinite-Canvas/blob/67f49e4b9a0ac090bad1f5bd62ae385f8fb5394e/static/js/smart-canvas.js#L4727) | renderPromptTemplatePanel | data-template-save-current | 未完成 PA 对应验收 |
| [4728](https://github.com/hero8152/Infinite-Canvas/blob/67f49e4b9a0ac090bad1f5bd62ae385f8fb5394e/static/js/smart-canvas.js#L4728) | renderPromptTemplatePanel | data-template-new | 未完成 PA 对应验收 |
| [4730](https://github.com/hero8152/Infinite-Canvas/blob/67f49e4b9a0ac090bad1f5bd62ae385f8fb5394e/static/js/smart-canvas.js#L4730) | renderPromptTemplatePanel | data-template-id | 未完成 PA 对应验收 |
| [4748](https://github.com/hero8152/Infinite-Canvas/blob/67f49e4b9a0ac090bad1f5bd62ae385f8fb5394e/static/js/smart-canvas.js#L4748) | renderPromptTemplatePanel | data-template-edit | 未完成 PA 对应验收 |
| [4749](https://github.com/hero8152/Infinite-Canvas/blob/67f49e4b9a0ac090bad1f5bd62ae385f8fb5394e/static/js/smart-canvas.js#L4749) | renderPromptTemplatePanel | data-template-delete | 未完成 PA 对应验收 |
| [4782](https://github.com/hero8152/Infinite-Canvas/blob/67f49e4b9a0ac090bad1f5bd62ae385f8fb5394e/static/js/smart-canvas.js#L4782) | renderPromptTemplatePanel | data-template-edit-cancel | 未完成 PA 对应验收 |
| [4783](https://github.com/hero8152/Infinite-Canvas/blob/67f49e4b9a0ac090bad1f5bd62ae385f8fb5394e/static/js/smart-canvas.js#L4783) | renderPromptTemplatePanel | data-template-delete | 未完成 PA 对应验收 |
| [4784](https://github.com/hero8152/Infinite-Canvas/blob/67f49e4b9a0ac090bad1f5bd62ae385f8fb5394e/static/js/smart-canvas.js#L4784) | renderPromptTemplatePanel | data-template-edit-save | 未完成 PA 对应验收 |
| [4786](https://github.com/hero8152/Infinite-Canvas/blob/67f49e4b9a0ac090bad1f5bd62ae385f8fb5394e/static/js/smart-canvas.js#L4786) | renderPromptTemplatePanel | data-template-apply | 未完成 PA 对应验收 |
| [4787](https://github.com/hero8152/Infinite-Canvas/blob/67f49e4b9a0ac090bad1f5bd62ae385f8fb5394e/static/js/smart-canvas.js#L4787) | renderPromptTemplatePanel | data-template-apply | 未完成 PA 对应验收 |
| [5620](https://github.com/hero8152/Infinite-Canvas/blob/67f49e4b9a0ac090bad1f5bd62ae385f8fb5394e/static/js/smart-canvas.js#L5620) | renderAssetLibrary | data-rename-workflow-asset | 未完成 PA 对应验收 |
| [5621](https://github.com/hero8152/Infinite-Canvas/blob/67f49e4b9a0ac090bad1f5bd62ae385f8fb5394e/static/js/smart-canvas.js#L5621) | renderAssetLibrary | data-delete-workflow-asset | 未完成 PA 对应验收 |
| [5622](https://github.com/hero8152/Infinite-Canvas/blob/67f49e4b9a0ac090bad1f5bd62ae385f8fb5394e/static/js/smart-canvas.js#L5622) | renderAssetLibrary | data-rename-local-asset | 未完成 PA 对应验收 |
| [5623](https://github.com/hero8152/Infinite-Canvas/blob/67f49e4b9a0ac090bad1f5bd62ae385f8fb5394e/static/js/smart-canvas.js#L5623) | renderAssetLibrary | data-delete-local-asset | 未完成 PA 对应验收 |
| [5623](https://github.com/hero8152/Infinite-Canvas/blob/67f49e4b9a0ac090bad1f5bd62ae385f8fb5394e/static/js/smart-canvas.js#L5623) | renderAssetLibrary | data-rename-asset | 未完成 PA 对应验收 |
| [5624](https://github.com/hero8152/Infinite-Canvas/blob/67f49e4b9a0ac090bad1f5bd62ae385f8fb5394e/static/js/smart-canvas.js#L5624) | renderAssetLibrary | data-delete-asset | 未完成 PA 对应验收 |
| [6688](https://github.com/hero8152/Infinite-Canvas/blob/67f49e4b9a0ac090bad1f5bd62ae385f8fb5394e/static/js/smart-canvas.js#L6688) | thumbMediaHtml | — | 未完成 PA 对应验收 |
| [6759](https://github.com/hero8152/Infinite-Canvas/blob/67f49e4b9a0ac090bad1f5bd62ae385f8fb5394e/static/js/smart-canvas.js#L6759) | singleMediaHtml | — | 未完成 PA 对应验收 |
| [7135](https://github.com/hero8152/Infinite-Canvas/blob/67f49e4b9a0ac090bad1f5bd62ae385f8fb5394e/static/js/smart-canvas.js#L7135) | openSmartLogLightbox | — | 未完成 PA 对应验收 |
| [7264](https://github.com/hero8152/Infinite-Canvas/blob/67f49e4b9a0ac090bad1f5bd62ae385f8fb5394e/static/js/smart-canvas.js#L7264) | promptNodeBodyHtml | — | 未完成 PA 对应验收 |
| [7265](https://github.com/hero8152/Infinite-Canvas/blob/67f49e4b9a0ac090bad1f5bd62ae385f8fb5394e/static/js/smart-canvas.js#L7265) | promptNodeBodyHtml | — | 未完成 PA 对应验收 |
| [7272](https://github.com/hero8152/Infinite-Canvas/blob/67f49e4b9a0ac090bad1f5bd62ae385f8fb5394e/static/js/smart-canvas.js#L7272) | promptNodeBodyHtml | — | 未完成 PA 对应验收 |
| [7273](https://github.com/hero8152/Infinite-Canvas/blob/67f49e4b9a0ac090bad1f5bd62ae385f8fb5394e/static/js/smart-canvas.js#L7273) | promptNodeBodyHtml | — | 未完成 PA 对应验收 |
| [7274](https://github.com/hero8152/Infinite-Canvas/blob/67f49e4b9a0ac090bad1f5bd62ae385f8fb5394e/static/js/smart-canvas.js#L7274) | promptNodeBodyHtml | — | 未完成 PA 对应验收 |
| [7300](https://github.com/hero8152/Infinite-Canvas/blob/67f49e4b9a0ac090bad1f5bd62ae385f8fb5394e/static/js/smart-canvas.js#L7300) | loopNumberControlHtml | — | 未完成 PA 对应验收 |
| [7303](https://github.com/hero8152/Infinite-Canvas/blob/67f49e4b9a0ac090bad1f5bd62ae385f8fb5394e/static/js/smart-canvas.js#L7303) | loopNumberControlHtml | data-loop-number, data-loop-value | 未完成 PA 对应验收 |
| [7317](https://github.com/hero8152/Infinite-Canvas/blob/67f49e4b9a0ac090bad1f5bd62ae385f8fb5394e/static/js/smart-canvas.js#L7317) | smartLoopTokenChipHtml | — | 未完成 PA 对应验收 |
| [7849](https://github.com/hero8152/Infinite-Canvas/blob/67f49e4b9a0ac090bad1f5bd62ae385f8fb5394e/static/js/smart-canvas.js#L7849) | smartLoopBodyHtml | data-loop-mode | 未完成 PA 对应验收 |
| [7850](https://github.com/hero8152/Infinite-Canvas/blob/67f49e4b9a0ac090bad1f5bd62ae385f8fb5394e/static/js/smart-canvas.js#L7850) | smartLoopBodyHtml | data-loop-mode | 未完成 PA 对应验收 |
| [7854](https://github.com/hero8152/Infinite-Canvas/blob/67f49e4b9a0ac090bad1f5bd62ae385f8fb5394e/static/js/smart-canvas.js#L7854) | smartLoopBodyHtml | data-loop-toggle | 未完成 PA 对应验收 |
| [7855](https://github.com/hero8152/Infinite-Canvas/blob/67f49e4b9a0ac090bad1f5bd62ae385f8fb5394e/static/js/smart-canvas.js#L7855) | smartLoopBodyHtml | data-loop-toggle | 未完成 PA 对应验收 |
| [7875](https://github.com/hero8152/Infinite-Canvas/blob/67f49e4b9a0ac090bad1f5bd62ae385f8fb5394e/static/js/smart-canvas.js#L7875) | smartLoopBodyHtml | data-loop-prompt-delete | 未完成 PA 对应验收 |
| [7880](https://github.com/hero8152/Infinite-Canvas/blob/67f49e4b9a0ac090bad1f5bd62ae385f8fb5394e/static/js/smart-canvas.js#L7880) | smartLoopBodyHtml | data-loop-token | 未完成 PA 对应验收 |
| [7882](https://github.com/hero8152/Infinite-Canvas/blob/67f49e4b9a0ac090bad1f5bd62ae385f8fb5394e/static/js/smart-canvas.js#L7882) | smartLoopBodyHtml | data-loop-prompt-add | 未完成 PA 对应验收 |
| [7888](https://github.com/hero8152/Infinite-Canvas/blob/67f49e4b9a0ac090bad1f5bd62ae385f8fb5394e/static/js/smart-canvas.js#L7888) | smartLoopBodyHtml | data-loop-run | 未完成 PA 对应验收 |
| [7915](https://github.com/hero8152/Infinite-Canvas/blob/67f49e4b9a0ac090bad1f5bd62ae385f8fb5394e/static/js/smart-canvas.js#L7915) | smartGroupBodyHtml | data-image-index | 未完成 PA 对应验收 |
| [7925](https://github.com/hero8152/Infinite-Canvas/blob/67f49e4b9a0ac090bad1f5bd62ae385f8fb5394e/static/js/smart-canvas.js#L7925) | smartGroupBodyHtml | data-image-index | 未完成 PA 对应验收 |
| [7964](https://github.com/hero8152/Infinite-Canvas/blob/67f49e4b9a0ac090bad1f5bd62ae385f8fb5394e/static/js/smart-canvas.js#L7964) | smartMinimaxBodyHtml | data-minimax-segment-delete | 未完成 PA 对应验收 |
| [7986](https://github.com/hero8152/Infinite-Canvas/blob/67f49e4b9a0ac090bad1f5bd62ae385f8fb5394e/static/js/smart-canvas.js#L7986) | smartMinimaxBodyHtml | data-minimax-ref-thumb-delete | 未完成 PA 对应验收 |
| [8013](https://github.com/hero8152/Infinite-Canvas/blob/67f49e4b9a0ac090bad1f5bd62ae385f8fb5394e/static/js/smart-canvas.js#L8013) | smartMinimaxBodyHtml | data-minimax-download-material | 未完成 PA 对应验收 |
| [8014](https://github.com/hero8152/Infinite-Canvas/blob/67f49e4b9a0ac090bad1f5bd62ae385f8fb5394e/static/js/smart-canvas.js#L8014) | smartMinimaxBodyHtml | data-minimax-use-material | 未完成 PA 对应验收 |
| [8034](https://github.com/hero8152/Infinite-Canvas/blob/67f49e4b9a0ac090bad1f5bd62ae385f8fb5394e/static/js/smart-canvas.js#L8034) | smartMinimaxBodyHtml | data-minimax-export-selected | 未完成 PA 对应验收 |
| [8035](https://github.com/hero8152/Infinite-Canvas/blob/67f49e4b9a0ac090bad1f5bd62ae385f8fb5394e/static/js/smart-canvas.js#L8035) | smartMinimaxBodyHtml | data-minimax-export-full | 未完成 PA 对应验收 |
| [8055](https://github.com/hero8152/Infinite-Canvas/blob/67f49e4b9a0ac090bad1f5bd62ae385f8fb5394e/static/js/smart-canvas.js#L8055) | smartMinimaxBodyHtml | data-minimax-play-timeline | 未完成 PA 对应验收 |
| [8056](https://github.com/hero8152/Infinite-Canvas/blob/67f49e4b9a0ac090bad1f5bd62ae385f8fb5394e/static/js/smart-canvas.js#L8056) | smartMinimaxBodyHtml | data-minimax-toggle-mute | 未完成 PA 对应验收 |
| [8062](https://github.com/hero8152/Infinite-Canvas/blob/67f49e4b9a0ac090bad1f5bd62ae385f8fb5394e/static/js/smart-canvas.js#L8062) | smartMinimaxBodyHtml | data-minimax-add-segment | 未完成 PA 对应验收 |
| [8088](https://github.com/hero8152/Infinite-Canvas/blob/67f49e4b9a0ac090bad1f5bd62ae385f8fb5394e/static/js/smart-canvas.js#L8088) | smartMinimaxBodyHtml | data-minimax-run | 未完成 PA 对应验收 |
| [8123](https://github.com/hero8152/Infinite-Canvas/blob/67f49e4b9a0ac090bad1f5bd62ae385f8fb5394e/static/js/smart-canvas.js#L8123) | nodeBodyHtml | data-image-index | 未完成 PA 对应验收 |
| [8125](https://github.com/hero8152/Infinite-Canvas/blob/67f49e4b9a0ac090bad1f5bd62ae385f8fb5394e/static/js/smart-canvas.js#L8125) | nodeBodyHtml | data-image-index | 未完成 PA 对应验收 |
| [8141](https://github.com/hero8152/Infinite-Canvas/blob/67f49e4b9a0ac090bad1f5bd62ae385f8fb5394e/static/js/smart-canvas.js#L8141) | jimengPendingBodyHtml | data-jimeng-query | 未完成 PA 对应验收 |
| [8158](https://github.com/hero8152/Infinite-Canvas/blob/67f49e4b9a0ac090bad1f5bd62ae385f8fb5394e/static/js/smart-canvas.js#L8158) | imageTaskRecoverBodyHtml | data-image-task-query, data-task-id | 未完成 PA 对应验收 |
| [8191](https://github.com/hero8152/Infinite-Canvas/blob/67f49e4b9a0ac090bad1f5bd62ae385f8fb5394e/static/js/smart-canvas.js#L8191) | smartNodeToolbarHtml | data-smart-node-action, data-node-id | 未完成 PA 对应验收 |
| [8304](https://github.com/hero8152/Infinite-Canvas/blob/67f49e4b9a0ac090bad1f5bd62ae385f8fb5394e/static/js/smart-canvas.js#L8304) | smartGroupToolbarHtml | data-smart-group-action, data-node-id | 未完成 PA 对应验收 |
| [8428](https://github.com/hero8152/Infinite-Canvas/blob/67f49e4b9a0ac090bad1f5bd62ae385f8fb5394e/static/js/smart-canvas.js#L8428) | render | — | 未完成 PA 对应验收 |
| [8433](https://github.com/hero8152/Infinite-Canvas/blob/67f49e4b9a0ac090bad1f5bd62ae385f8fb5394e/static/js/smart-canvas.js#L8433) | render | — | 未完成 PA 对应验收 |
| [8500](https://github.com/hero8152/Infinite-Canvas/blob/67f49e4b9a0ac090bad1f5bd62ae385f8fb5394e/static/js/smart-canvas.js#L8500) | render | — | 未完成 PA 对应验收 |
| [8503](https://github.com/hero8152/Infinite-Canvas/blob/67f49e4b9a0ac090bad1f5bd62ae385f8fb5394e/static/js/smart-canvas.js#L8503) | render | — | 未完成 PA 对应验收 |
| [11261](https://github.com/hero8152/Infinite-Canvas/blob/67f49e4b9a0ac090bad1f5bd62ae385f8fb5394e/static/js/smart-canvas.js#L11261) | refreshComparePanel | data-compare-idx | 未完成 PA 对应验收 |
| [13041](https://github.com/hero8152/Infinite-Canvas/blob/67f49e4b9a0ac090bad1f5bd62ae385f8fb5394e/static/js/smart-canvas.js#L13041) | renderInputThumbsRow | data-input-add-reference | 未完成 PA 对应验收 |
| [13066](https://github.com/hero8152/Infinite-Canvas/blob/67f49e4b9a0ac090bad1f5bd62ae385f8fb5394e/static/js/smart-canvas.js#L13066) | renderInputThumbsRow | data-input-remove-reference | 未完成 PA 对应验收 |
| [14297](https://github.com/hero8152/Infinite-Canvas/blob/67f49e4b9a0ac090bad1f5bd62ae385f8fb5394e/static/js/smart-canvas.js#L14297) | renderMentionPicker | data-mention-index | 未完成 PA 对应验收 |
| [14308](https://github.com/hero8152/Infinite-Canvas/blob/67f49e4b9a0ac090bad1f5bd62ae385f8fb5394e/static/js/smart-canvas.js#L14308) | renderMentionPicker | data-mention-folder | 未完成 PA 对应验收 |
| [14314](https://github.com/hero8152/Infinite-Canvas/blob/67f49e4b9a0ac090bad1f5bd62ae385f8fb5394e/static/js/smart-canvas.js#L14314) | renderMentionPicker | data-mention-source | 未完成 PA 对应验收 |
| [14317](https://github.com/hero8152/Infinite-Canvas/blob/67f49e4b9a0ac090bad1f5bd62ae385f8fb5394e/static/js/smart-canvas.js#L14317) | renderMentionPicker | data-mention-source | 未完成 PA 对应验收 |
