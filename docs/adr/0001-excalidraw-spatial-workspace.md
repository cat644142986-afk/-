# ADR-0001: Excalidraw 空间工作区与 Fabric 精修分层

- 状态：Accepted
- 日期：2026-09-02
- 决策来源：用户批准的独立无限画布目标
- 基线：Git `9faf87e0328fe89ee70ea5eeeff8944893cdb54d`
- 正式版保护基线：artifact `93539f0c9ec857d22d3751bb836ff722579cd8db`、contract `2026-09-02.3`、schema v7

## 背景

当前 Fabric.js 自由画布已经证明 CanvasDocument、不可变 SQLite 版本、任务/trace、ProductProfile、原始像素导出和 ROI/Mask/outpaint 可以在正式版稳定工作，但它仍嵌在快捷处理页，并承担了过多空间组织职责。下一阶段需要独立的多媒体无限工作区，同时必须保留已经通过逐像素门禁的精修内核，避免重写成熟画布能力或建立第二套业务账本。

## 决策

1. 空间画布采用 `@excalidraw/excalidraw@0.18.1`，上游标签 `v0.18.1` 对应提交 `a2ec2889babf7d2295469c6d90ebe77fae57df84`。
2. 固定 React peer runtime 为 `react@18.3.1` 和 `react-dom@18.3.1`。Excalidraw 虽声明支持 React 19，但其锁定的 Radix Tabs peer 只接受 React 16–18；PoC 不允许用强制安装掩盖该冲突。PoC 先锁入原型自己的 `package.json` 和 `package-lock.json`，生产接入阶段再锁入仓库根 package/lock，禁止范围升级。
3. Excalidraw 只负责平移缩放、选择、Frame、分组、锁定、层级、对齐、连线、文字、图片、可嵌入视频节点和空间布局。
4. Fabric.js `7.4.0` 继续作为单张图片精确编辑器，独占 ROI、Mask、局部精修、扩图边界、严格选区外零差异、原始像素导出和逐像素合成。
5. Excalidraw 通过公开 API 集成，只使用 `customData`、`onChange`、`renderEmbeddable`、公开 scene/file API 和应用侧组件；不修改 `node_modules`，不复制上游内部实现。
6. tldraw SDK 因生产许可条件不进入实现。InvokeAI 只作为交互参考，不引入后端、本地模型或节点编辑器。

## 产品边界

- 左侧主任务栏新增“无限画布”一级入口；快捷处理页移除顶部“自由画布”切换。
- 无限画布占据主窗口，只保留全局左侧栏；检查器按选中对象渐进出现，默认收起。
- 快捷处理、素材库、历史结果和任务中心共享同一素材/任务事实，并提供“发送到画布”。
- 双击图片或执行“精细修改”进入现有 Fabric 精修界面；完成后返回空间画布，在父节点右侧创建新版本和血缘连线，不覆盖父节点。
- 视频第一阶段只有生成、排队、进度、取消、失败解释、重试、预览、版本和导出；多轨时间线延后。

## 数据合同

- 复用 `CanvasDocument`、SQLite 不可变版本、任务、trace、ProductProfile、素材和结果血缘，不建立第二套业务账本。
- Excalidraw scene 只保存空间布局、元素和业务引用。业务元素 `customData` 至少支持：
  - `asset_id`
  - `result_id`
  - `task_id`
  - `product_profile_version_id`
  - `lineage_parent_id`
- scene JSON 禁止保存 4K 原图、视频字节、Base64、机器绝对路径或 API 凭据。图片使用可重建代理图，视频使用封面；原件继续由素材系统管理。
- `onChange` 只更新内存草稿，并通过防抖、内容指纹和乐观并发追加 CanvasDocument 不可变版本。重启必须恢复 scene、视口、Frame、分组和业务关系。
- PoC 通过前不修改 schema。生产接入若需 schema v8，只允许从 v7 可恢复迁移，并继续由 candidate-first 打包门禁验证。

## 性能与交付门禁

- Excalidraw 和 React 必须动态懒加载；未进入无限画布时不得下载或执行对应 chunk，不影响快捷处理首帧。
- 隔离 PoC 必须通过 200 张代理图、20 个视频封面、保存/恢复以及 1440x900、1280x720、960x600 三档尺寸。
- 视频仅在选中或用户主动播放时加载，禁止批量自动播放。
- 正式便携目录总大小不得超过 450 MiB，不打包图像或视频大模型。
- PoC 失败、生产包超限、快捷处理启动回归或 G1-G3 像素/迁移门禁失败时，停止生产接入并保持正式版不变。
- 完成候选构建、正式 WebView 截图和真实交互验收前，禁止提升 `release/ProductAtelier-Portable`、修改正式快捷方式或读取正式用户账本。

## 许可证

| 组件 | 固定版本 | 许可证 | 源码 |
| --- | --- | --- | --- |
| Excalidraw | 0.18.1 | MIT | https://github.com/excalidraw/excalidraw/tree/v0.18.1 |
| React | 18.3.1 | MIT | https://github.com/facebook/react/tree/v18.3.1 |
| React DOM | 18.3.1 | MIT | https://github.com/facebook/react/tree/v18.3.1 |
| Fabric.js | 7.4.0 | MIT | https://github.com/fabricjs/fabric.js/tree/v7.4.0 |

许可证全文进入根 `THIRD_PARTY_NOTICES.md` 和随应用分发的 `src/public/THIRD_PARTY_NOTICES.txt`。PoC 安装依赖后还必须对锁定依赖树执行许可证与漏洞审计。当前残余审计告警来自隐藏的 Mermaid 转换链及其 `nanoid@4.0.2`；在上游提供兼容修复前隐藏入口、拒绝不可信 Mermaid 输入并保留风险记录，不用跨大版本 override 破坏锁定实现。

## 回滚

本工作在 `codex/excalidraw-infinite-canvas` 独立分支推进。正式 artifact `93539f0`、schema v7、正式目录、桌面快捷方式和正式账本保持不变。任一检查点可以通过丢弃该开发分支回到 `9faf87e`，无需迁移或修复正式用户数据。
