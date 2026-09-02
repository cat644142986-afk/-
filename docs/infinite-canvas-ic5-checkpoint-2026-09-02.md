# 无限画布 IC5 视频第一阶段源码检查点（2026-09-02）

## 结论

IC5 视频第一阶段已完成源码、隔离账本和零付费离线闭环。用户可以从画布图片发起图生视频，设置提示词、比例、时长、首尾帧和运动强度；任务进入现有耐久队列，支持进度、取消、失败解释、重新确认后重试、结果回填、重启恢复、血缘和原始视频导出。视频节点由 Excalidraw `renderEmbeddable` 承载，但媒体字节仍由素材账本管理。

这不是正式便携版发布。真实 Tauri WebView、DWM、100%/125%/150% DPI、Explorer 系统拖拽、视频播放/暂停/seek、候选双 smoke 和 NSIS 安装卸载仍属于 IC6 候选门禁。正式目录、正式快捷方式和正式用户账本均未修改。

## 实现范围

- 实现提交：`b2e06ec77b753e3a303b7546f842ad83a64b3ee2`；父提交/回滚点：`6042832aef28aa3293baf2c5c4502ad5d25d29c5`，标签 `checkpoint-2026-09-02-ai-local-edit-candidate`。
- sidecar contract 继续为 `2026-09-02.4`；开发 schema 继续为 v8；复用现有素材、任务、trace、空间 scene 和结果血缘，不建立第二套业务账本。
- 冻结 `image-to-video-v1`：比例 `1:1 / 16:9 / 9:16 / 4:3 / 3:4`，时长 `3 / 5 / 8 / 10` 秒，运动强度 `1-10`，首帧必须等于选中源素材，尾帧可选。
- 第一 provider 固定为 `offline-preview-v1`。未授权 provider、付费确认和自动付费重试全部拒绝；20 个小型 WebM 固定夹具覆盖 5 种比例和 4 种时长，共 `350,606 bytes`，不含模型或用户素材。
- 视频任务进入现有幂等命令和耐久队列。任务完成后回填一个原始视频资产与一个 JPEG 封面资产；封面是辅助结果，不增加业务结果计数。
- 结果默认位于父图片右侧并建立血缘；任务节点与结果节点分离。切换画布、慢响应和 409 冲突副本不会把原画布视频任务回填到错误画布。
- 视频节点只在选中或主动播放时加载；20 个节点最多保持一个已加载或播放实例，切换、停止和重启会释放媒体。
- 原视频导出走 Tauri 原生二进制流和原子替换，不进入图片 Base64 路径；重定向、截断响应、编码传输和非普通目标文件均失败关闭。
- 标题栏关闭和两个自定义关闭按钮统一走 `prevent_close -> prepareForClose -> complete_close_app`。保存失败或超时会保留窗口、sidecar 和未保存 scene，允许再次关闭重试。

## 隔离验证

- 离线视频任务验证创建、完成、原始二进制 SHA-256、封面尺寸、trace、重启恢复和同 request ID 幂等重放；网络和付费 provider 调用均为 0。
- schema 迁移 fixture 覆盖正式 v7 -> v8 与旧 v5 -> v8：分别保留唯一 backup-v7/backup-v5，校验备份内容、sentinel 和重启幂等。v5 fixture 还包含完整字段的 session、asset、job，以及 v5 新增的 ProductProfile、不可变版本、参考素材关系、job snapshot 和 execution trace 版本绑定；迁移与第二次启动后逐字段一致才算内容保留。2026-09-03 IC6 复核发现，当时的真实 PyInstaller sidecar 门禁只启动验证了 v7，v5 仍停留在源码内 ledger fixture；IC6 已补入 v5 打包进程两次启动门禁，须由绑定修正后新 HEAD 的 sidecar 复跑后才能关闭该证据缺口。
- 画布竞态专项覆盖切换画布、延迟回填、409 冲突副本、保存失败、关闭重试、永久引用错误和视频 owner 约束。
- 原视频二进制导出 Rust 测试覆盖合法 ID/文件名、精确 loopback 端点、拒绝重定向、截断不覆盖目标、成功原子替换。

## 工程门禁

- 前端：`212/212`。
- Python：共运行 `386` 项，`385` 通过、`1` 项平台预期跳过。
- Vite production build：通过；production dist `10,659,989 bytes`。
- 无限画布懒加载 bundle：通过；快捷处理首屏 `modulepreload=0`；预计正式便携目录 `368.73 MiB`，低于 `450 MiB`。
- Rust/Tauri `--locked --features custom-protocol` check：通过；Rust 二进制导出 `5/5`。
- Python compileall、PyInstaller spec 编译、Git whitespace：通过。
- `package-lock.json` SHA-256：`A0758630BC38B94F76D71D0CB74D72AE5BC20829073C2A613F0F1651719F86BD`。
- 结构化证据：`artifacts/excalidraw-spatial-ic5/metrics.json`。

## 正式版保护与回滚

- 正式版仍为 Git `93539f0c9ec857d22d3751bb836ff722579cd8db`、contract `2026-09-02.3`、schema v7、`375,974,706 bytes`（358.56 MiB）。
- 正式 App、sidecar、manifest SHA-256 分别为 `F0D1A07313A47258CD17FA4143DF4F0069D6E075CAA9D22B00EB7220A15685A7`、`2AF3FC31A90961D77A41BE6CECAFD3559E0D0FFFB505FD9BBEA9EC7C61293894`、`287042C051B97CC9F60C42F670C470EBEC965042378B790E0F4F13FCC9718A38`。
- IC5 实施前回滚点为 `checkpoint-2026-09-02-ai-local-edit-candidate`。源码检查点标签在本说明提交后创建。
- schema v8 账本不能交给 schema v7 sidecar 继续写入。降级时必须退出候选进程、保留当前账本副本，并从自动生成的 backup-v7 在隔离目录验证后恢复；不得覆盖运行中的正式目录。

## 下一游标

进入 IC6：从干净检查点构建隔离 sidecar、Tauri EXE、portable 和未签名 NSIS；完成 packaged v7 -> v8 与 v5 -> v8、候选双 smoke、真实 Tauri WebView、DWM、三档 DPI、Explorer 系统拖拽、视频播放/暂停/seek/导出、重启恢复和 NSIS 安装卸载。全部门禁通过前不提升正式便携版；IC6 关闭后自动进入 G5 结构化质量检查。

未签名 NSIS 只能使用显式 `UnsignedInternal` 验收模式：安装器及安装后的 App、sidecar、uninstaller 必须全部为 `NotSigned`，安装使用 `/NS` 禁止创建快捷方式，并对既有桌面/开始菜单入口做安装前、安装后和卸载后的只读指纹不变断言。允许在 20:00 前跳过 App smoke 做无窗口预检，但不得把预检写成完整安装态通过；真实 App smoke 仍须在获准的桌面窗口完成。

开发机隔离卸载固定使用 `/S /UPDATE`：它验证隔离目录、卸载注册项和受保护用户状态能够恢复，同时避免普通卸载会清理的 Jump List、最近记录和开机启动项。安装前同时在 safetyRoot 持久化快捷方式副本和类型化 HKCU 注册表快照；恢复失败时必须保留并报告该目录。该门禁不冒充普通用户卸载的完整语义；普通卸载体验只能在可丢弃的 Windows Sandbox/虚拟机中另行验收，不得拿当前正式用户环境做破坏性测试。
