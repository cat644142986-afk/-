# 无限画布 IC6 Windows 探索性实机记录（2026-09-03）

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
