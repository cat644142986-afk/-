# 无限画布 IC6 Windows 探索性实机记录（2026-09-03）

## 最新状态（2026-09-05，优先于下文历史游标）

IC6 尚未全部通过。`d4df75e550b063d3ecefe79877664bd492f37dfd` 候选修复了视频回填触发异常空 scene 的问题，本轮通过真实窗口验证视频生成、按需播放、原视频导出与同一隔离账本两次自然关闭/恢复；同时发现重复 `onChange` 使自动保存防抖计时持续后移，须修复后重建候选。不得以自然关闭的强制保存成功，替代运行期间自动保存通过。

### 本轮真实窗口证据

- artifact：`d4df75e550b063d3ecefe79877664bd492f37dfd`；contract `2026-09-02.4`；schema v8；`378,354,298 bytes`（360.83 MiB）。App / sidecar / manifest SHA-256：`21372522B19100B4C22A00176FE875C2F82BCA1A5B5F5C365A3C7CF9788AD218` / `446FE3096A15C52247F65688DDC3B44A7CCA38A96CA735FDD43C56BE971B3880` / `BD86B985E6D72CC23A191466088568D00115F26D06E4B6E7D7F7DE6A8CD7FD8C`。
- identity receipt SHA-256：`BC1D4B3A6D1F512669311FE959AC31056E2AA26982D9644BD01A4F0F51104D57`；tree SHA-256：`D532BBDAEE3F2E504190476459068BDC1EDE8FF3774333E453BC2E54D3A5BA8D`。
- 离线图生视频完成后保留原画布节点，结果位于父图旁并保留任务/血缘。视频只在选中或主动播放时加载，移开选择后恢复封面；重启后仍可主动播放。测试为 320×320、5 秒的离线夹具，不代表云端视频质量验收，付费调用为 0。
- 业务图片属性栏、右键与 Enter 未进入 Crop；双击进入现有 Fabric，真实原图画板为 2048×2048，返回画布后节点保留。尚未完成编辑结果回填专项，不把“进入/返回成功”误写为该项通过。
- 原视频通过系统“另存为”导出，13,828 bytes，源/导出 SHA-256 均为 `CE81681ECBA8C5ABA09EEF4A975EA7F5ED4AE7FD35A7AD8027808A2276885C5E`。
- `manual_restart_acceptance.py` 同一隔离数据完成两次自然关闭，均 exit 0，最终输出 `runs_completed=2`、`isolated_data_cleaned=true`。第二次打开画布恢复列表缩略图、位置、图片、图形、连线、文字及视频封面，检查器默认收起。
- 关闭后的隔离账本快照含 16 个不可变 scene 版本；最新有 9 个非删除元素（2 图片、2 矩形、3 连线、1 文字、1 视频）。除初始空画布版本外，没有后续空 scene 覆盖。最小原始证据留在 `D:\ProductAtelier-Temp\ic6-d4df75e-restart-export-2026-09-05`；SQLite、日志和视频原件不提交 Git。

### 自动保存缺陷与修复门禁

- 复现：视频/选择等瞬态回调携带相同持久化内容时仍重置 240ms 定时器，页脚长期“正在保存画布”。自然关闭 flush 成功，因此本轮未丢数据，但运行期间保存存在延迟风险。
- 回归先红后绿：每 80ms 发送同内容回调，修复前持久化调用为 0；修复后按首个期限写入且后续瞬态回调不重复保存。
- 修复按现有持久化合同计算稳定内容签名；JSON 对象键顺序不算修改，视口缩放/位置仍保存。比较顺序覆盖待保存和正在保存的最新内容，保证“保存过程中撤销”不被误去重；失败后的同内容回调仍可重试。
- 源码门禁：前端 **255/255**、Vite production build、lazy bundle、Git whitespace 通过。工作区 dist `10,727,426 bytes`、静态预计目录 `368.79 MiB`；新候选实际包体积待 clean build，不沿用估算。contract `.4` / schema v8 不变。
- 回滚点为已推送的 `d4df75e`，仅供源码追溯，其自动保存缺陷未通过，不可作为发布备选。需从本修复的干净、已推送提交完整重建，再执行双 smoke、迁移和真实保存/视频/重启复验。

### 后续固定顺序

新候选自动保存复验 → Explorer 图片/视频拖拽与空间编辑 → Fabric 实际回填、原图导出及视频任务异常闭环 → 同一隔离账本两轮自然关闭/恢复 → 三窗口尺寸与 100%/125%/150% DPI、DWM/圆角/启动黑帧，并覆盖已并入的 UI M1–M3 → 同一身份 NSIS 隔离安装/运行/卸载 → 恢复显示配置、复核正式版/账本/快捷方式保护。全部通过前不关闭 IC6，任何情况下本轮均不提升正式便携版。

## 结论

旧候选 `3e469a0c7703aafa1302561b0cb13ee0fa7d1e44` 完成了第一轮真实 Tauri 窗口探索，但不能作为 IC6 最终验收证据。实机发现双击业务图片仍会进入 Excalidraw 内置 Crop，违反“Excalidraw 管空间、Fabric 管像素”的引擎边界。该缺陷已在源码中修复并通过自动测试，必须从新的干净 Git 身份重建候选后再次实机复验。

## 已验证行为

- 150% DPI 下的 `1280x720`、`960x720` 和 `960x600` 逻辑窗口可用，无明显内容遮挡。
- 图片加载、移动、缩放、框选、多选、分组、锁定、Frame、连线、撤销与重做可用。
- 检查器“精细修改”可进入 Fabric，并恢复 `1536x2048` 原始画板与局部编辑工具；返回无限画布可用。
- 验收只使用仓库内 Wikimedia 授权测试夹具和隔离数据，不读取用户素材、API Key 或正式账本，不调用付费接口。
- 测试 App PID `13428` 与 sidecar PID `36748` 已按绝对路径和父子关系精确停止；主屏缩放保持 150%。

## 实机发现与修复

失败证据：`artifacts/excalidraw-spatial-ic6-real-ui-exploratory-2026-09-03/960x600-logical-150pct-doubleclick-crop-conflict.png`。双击图片出现英文 Crop 提示和裁剪状态，证明旧的 `pointerdown.detail` 路由在 WebView2 中不可靠。

当前修复：

- 在画布宿主 capture 阶段处理真实 `dblclick`，仅 Canvas 上的业务图片进入 Fabric。
- 异步打开期间防重入，避免一次手势重复创建精修会话。
- 通过 Excalidraw 公开 `registerAction()` 禁用 `cropEditor`，并在 Product Atelier 宿主范围隐藏残余 Crop 菜单和错误英文提示。
- `onChange` 发现业务图片 Crop 状态时只清除该状态，不自动跳页，避免恢复或撤销时误开 Fabric。
- 工具栏、普通图形和非业务图片双击保持 Excalidraw 原行为；卸载画布时移除宿主监听。

自动门禁：前端 `232/232`、Vite production build、无限画布 bundle、相关 JavaScript 语法和 Git whitespace 均通过。production dist 为 `10,663,963 bytes`，预计正式目录 `368.73 MiB`，低于 `450 MiB` 上限。

## 证据与保护基线

本目录 5 张截图只包含 Product Atelier UI 和仓库测试夹具，其中 Crop 冲突截图保留为失败证据，不得表述为最终通过截图。

正式便携版仍保持 Git `93539f0c9ec857d22d3751bb836ff722579cd8db`、contract `2026-09-02.3`、schema v7：

- App SHA-256：`F0D1A07313A47258CD17FA4143DF4F0069D6E075CAA9D22B00EB7220A15685A7`
- sidecar SHA-256：`2AF3FC31A90961D77A41BE6CECAFD3559E0D0FFFB505FD9BBEA9EC7C61293894`
- manifest SHA-256：`287042C051B97CC9F60C42F670C470EBEC965042378B790E0F4F13FCC9718A38`
- 正式账本 SHA-256：`9682B3E11FF4D73EDBAE09FB2C4AEF21FA747F6B4C399584B717428808E0C4A5`
- 正式桌面快捷方式 SHA-256：`B74D4853F471A0A9B5E56C1CF6CFD34C9E66CD1D4393144CA03E4479FD9A4BEC`

## 下一步

从包含本修复的干净且已推送 HEAD 重建 IC6 候选，再完成双击 Fabric、Crop 全入口消失、视频、重启恢复、DWM/DPI、启动黑帧、圆角、Explorer 系统拖拽以及未签名 NSIS 隔离安装/卸载验收。全部通过后仍只保留候选，不自动提升正式便携版。

## 新集成候选预检（前序 headless 证据）

- 候选软件源码身份：`codex/excalidraw-infinite-canvas` @ `dc53c3b75b42e0b76f28c5e46f2da25d0fa7cf46`。
- 当前候选：`build/portable-candidate-current`，contract `2026-09-02.4`、schema v8、`378,354,298 bytes`（约 360.83 MiB）。
- App / sidecar / manifest SHA-256：`5CF280610A6E364DF5A155F7939E18CAF753A8C6AA281ECE4C3786E32C4F3A9B` / `B35AE0871FA1EAAAD28A4E60E6C1D4C47259E2B5B53B7B3FA3AACF4D1A8CB004` / `37DD80166D27BD2A6F8C20AD13D9E2E2E3744B6EF8E45EF16BCE02C13DEA0E8E`。
- source fingerprint / tree / identity receipt SHA-256：`5DF9B0CA10C8C8C6AEA2123D7ECD8AD0FE02FC4840A07FE906BF02F05404282F` / `C4ECB6FAD4CEA9AFECA740046614C267E294C5F316EB91D3252AFB1168323CF5` / `F4DC1EA149C91E4462CE280DA12C33DB658C05623A7A045F9C5DA79DEDDA7478`。
- clean build 门禁：前端 `249/249`、Python `534/534`（4 项平台条件跳过）、Rust `42/42`、Vite、lazy bundle、PyInstaller sidecar 与 Tauri release 全部通过。
- 打包门禁：候选 sidecar smoke 通过；packaged v7→v8 与 legacy v5→v8、迁移备份、十项画布命令、ProductProfile、严格 outpaint、离线视频完成/重启/幂等重放通过，网络调用为 0。
- 正式 App、sidecar、manifest、账本和桌面快捷方式已在候选构建后再次按冻结 SHA-256 只读复核，全部未变；快捷方式目标和工作目录也未变。

本节记录的是路线图提交前已通过的 headless 候选。NSIS 发布入口要求分支 HEAD、upstream 与 canonical candidate receipt 完全一致，因此路线图提交后必须从最终文档 HEAD 重新 stage 候选，并以新 identity receipt 进入晚间 App/GUI/installer 链；不得把本节 `dc53c3b` receipt 继续用于 NSIS。

21:00 后只启动以上身份的隔离候选，先完成 `Test-Portable-App.ps1`，再用真实 Tauri 窗口复验属性栏、右键、命令面板和 Enter 均无 Crop，双击业务图片只进入 Fabric，撤销历史无 Crop 污染。随后完成系统拖拽、空间操作、Fabric 回填、原图导出、视频闭环、同一隔离账本两轮自然关闭、三窗口尺寸、三档 DPI、DWM/圆角/启动黑帧和未签名 NSIS 隔离安装卸载。未产生最终截图、交互记录和恢复后的保护哈希前，本节不得改写为通过结论。
