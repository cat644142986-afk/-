# Excalidraw 空间工作区 PoC 检查点（2026-09-02）

## 结论

隔离 PoC 通过，可以进入生产接入阶段；这不等于独立无限画布已经进入正式软件，也不授权提升正式便携版。

PoC 使用 `@excalidraw/excalidraw@0.18.1`、`react@18.3.1`、`react-dom@18.3.1` 和 `vite@6.4.3`。React 从原 ADR 的 19.2.8 校正到 18.3.1，原因是 Excalidraw 依赖树中的 Radix peer 只接受 React 16–18，未使用 `--force` 掩盖冲突。

## 范围与边界

- 270 个 Excalidraw 元素：200 张合成代理图、20 个 `renderEmbeddable` 视频封面、5 个 Frame、40 条结果血缘和 5 个标题。
- 图片和视频节点完整保存 `asset_id`、`result_id`、`task_id`、`product_profile_version_id`、`lineage_parent_id`。
- 持久 scene 只保存布局、视口和业务引用，不保存代理文件字节、Base64、原图、视频、机器绝对路径或 API 凭据。
- 视频节点只在选中时进入加载态，播放由选中对象的外部情境工具触发，不依赖 Excalidraw 未激活嵌入层的内部指针事件。
- PoC 未读取正式 SQLite、用户图片、Prompt、API Key 或 build 证据，未调用网络模型、付费 API 或视频 provider。

## 真实交互证据

| 门禁 | 结果 |
| --- | --- |
| 视频按需加载 | 未选中 0；选中后 1；播放后 1；停止后播放数回到 0 |
| 绘制与移动 | 新矩形从 `(3420, 2090)` 拖到 `(3920, 2340)` |
| 撤销/重做 | 撤销回 `(3420, 2090)`；重做回 `(3920, 2340)` |
| 保存 | 270 元素 scene 为 `207.4 KiB`，防抖 420ms，内容指纹去重 |
| 重启恢复 | 无 `?reset=1` 重载后元素、20% 缩放、Frame、分组、锁定、血缘和业务引用恢复 |
| 视频媒体策略 | 20 个封面可见，未选中不进入加载态，无自动播放 |
| 控制台 | Edge 与内置浏览器均为 0 error / 0 warning |

真实浏览器曾发现并修复三类问题：URL 编码 SVG 触发 `atob` 错误、错误 `scrollToContent` 导致 `NaN%`、无变化的 `onChange` 反复排定保存。最终版本改用 UTF-8 Base64、固定初始视口和内容指纹去重。

## 窗口与性能

| 窗口 | 页面/主容器 | 横向溢出 | 纵向溢出 | 截图 |
| --- | --- | ---: | ---: | --- |
| 1440×900 | 1440×900 | 0 | 0 | `artifacts/excalidraw-spatial-poc/viewport-1440x900.png` |
| 1280×720 | 1280×720 | 0 | 0 | `artifacts/excalidraw-spatial-poc/viewport-1280x720.png` |
| 960×600 | 960×600 | 0 | 0 | `artifacts/excalidraw-spatial-poc/viewport-960x600.png` |

- 观察到的首次可用时间为 417–2004ms；差异来自冷启动、字体和依赖缓存，生产阶段仍须在真实 Tauri WebView 建立基线。
- PoC production dist 为 365 个文件、`21,117,338 bytes`（20.14 MiB），其中 JS 7.42 MiB、字体 12.58 MiB、Mermaid/图表类按需 chunk 约 4.03 MiB。
- 当前正式便携版为 358.56 MiB；即使把 PoC dist 全量相加也约 378.70 MiB，低于 450 MiB 门禁。生产懒加载和未进入画布时不执行 Excalidraw 尚未由独立 PoC 证明，必须在下一阶段验证。

## 许可证与供应链

- 根 NOTICE 与随应用分发的 NOTICE 已包含 Excalidraw 0.18.1、React/React DOM 18.3.1 的 MIT 全文和源码链接。
- 253 个生产依赖实例的许可证扫描为 MIT、ISC、Apache-2.0、BSD、MPL/Apache 双许可、CC0、0BSD 或 Unlicense。`khroma@2.1.0` 的 package metadata 缺少 license 字段，但包内 `license` 文件明确为 MIT；`fsevents` 是 Windows 未安装的 macOS 可选依赖。
- `npm audit` 从 11 个告警降到 3 个：2 moderate、1 high、0 critical。残余全部来自隐藏的 Mermaid 转换链及其固定 `nanoid@4.0.2`；修复要求跨大版本或降级 Excalidraw，当前不强行覆盖。生产阶段继续隐藏 Mermaid 入口、禁止不可信 Mermaid 输入，并跟踪上游修复。
- `package-lock.json` SHA-256：`07215340BAFCF69055551CA67E13DD0011AE196DEA731AE0A5FCC7C277E109C7`。

## 工程门禁

- PoC：7/7。
- 前端：154/154。
- Python：339 项，338 通过、1 项平台预期跳过。
- PoC Vite build、生产 Vite build、Rust/Tauri `--locked --features custom-protocol`、Python compileall、Git whitespace：通过。

## 正式版保护与回滚

- 正式版仍为 Git `93539f0c9ec857d22d3751bb836ff722579cd8db`、contract `2026-09-02.3`、schema v7、358.56 MiB。
- 正式 App、sidecar、manifest SHA-256 仍为 `F0D1A073...`、`2AF3FC31...`、`287042C0...`，与 G3 发布证据一致。
- 本检查点只在 `codex/excalidraw-infinite-canvas` 分支工作；回滚基线为 `9faf87e0328fe89ee70ea5eeeff8944893cdb54d`。PoC 检查点使用标签 `checkpoint-2026-09-02-excalidraw-spatial-poc`。

## 下一执行游标

先完成生产依赖与懒加载边界、左侧“无限画布”一级入口、画布列表壳和移除 Studio 顶部“自由画布”入口；随后才做 schema v8 不可变 scene 版本、素材/任务拖入、Fabric 精修血缘桥和视频任务第一阶段。任何生产检查点均不得修改正式便携目录。
