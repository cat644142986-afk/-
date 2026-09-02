# 无限画布 IC2 生产入口检查点（2026-09-02）

## 结论

IC2 源码检查点通过。独立“无限画布”已成为左侧一级入口，Studio 顶部旧“自由画布”切换已移除；Excalidraw 只在用户新建或打开画布后加载，快捷处理首屏和画布列表均不加载重型 island。

这仍不是正式便携版发布。当前画布列表使用隔离内存 adapter，不写 schema v7、正式 SQLite、浏览器存储或用户账本；正式目录、正式快捷方式和正式 artifact 均未修改。

## 实现范围

- 根依赖精确锁定 `@excalidraw/excalidraw@0.18.1`、`react@18.3.1`、`react-dom@18.3.1`，实现提交为 `ff9bb0c1a89dd54f110785d0e9b3d52055c50d34`。
- 左侧新增“无限画布”一级入口；进入后隐藏普通页头，只保留全局左侧栏和空间工作区工具栏，右侧检查器默认隐藏。
- 提供画布列表、新建、重命名、最近打开排序、真实 scene 轻量缩略图和跨页面返回现场。
- Excalidraw 通过动态 `import('./infinite-canvas-island.jsx')` 挂载 React island；生产 Vite manifest 和独立门禁共同锁定动态 chunk、CSS 与首屏零 preload。
- scene 只保存元素和视口；`files` 强制为空。IC2 不接素材字节、不写 Base64、不建立第二套账本。
- 默认图形使用 `roughness=0` 和实线样式；隐藏 Excalidraw 导出、载入、清空、主题及图片入口，继续由 Product Atelier 控制业务数据和正式导出。

## 懒加载与包体

| 门禁 | 结果 |
| --- | --- |
| 快捷处理首屏 | 0 个 Excalidraw / React island 请求 |
| 进入画布列表 | 仍为 0 个 Excalidraw / React island 请求 |
| 新建或打开画布 | 才加载 island JS 与 CSS |
| 初始 HTML modulepreload | 0 |
| production dist | `10,570,424 bytes`（10.08 MiB） |
| 预计正式便携目录 | 368.64 MiB，低于 450 MiB 上限 |

Vite 构建会输出第三方 Radix 包的 `use client` 忽略提示和大 chunk 提示，但运行时浏览器控制台为 0 error / 0 warning；bundle 门禁确认这些 chunk 不进入快捷处理初始加载。

## 真实交互与窗口

真实浏览器完成新建、绘制矩形、撤销、重命名、返回列表、真实缩略图和跨页面返回现场。键盘焦点进入一级入口/新建/重命名路径，重命名支持 Esc 取消并恢复焦点；动态加载和保存状态使用 `aria-live`，全局 reduced-motion 规则覆盖加载动画。

| 窗口 | 视图 | 横向溢出 | 纵向溢出 | 截图 |
| --- | --- | ---: | ---: | --- |
| 1440×900 | 编辑器与属性面板 | 0 | 0 | `artifacts/excalidraw-spatial-ic2/viewport-1440x900-editor.png` |
| 1280×720 | 编辑器 | 0 | 0 | `artifacts/excalidraw-spatial-ic2/viewport-1280x720-editor.png` |
| 960×600 | 最近画布列表 | 0 | 0 | `artifacts/excalidraw-spatial-ic2/viewport-960x600-library.png` |

## G1-G3 与工程门禁

- 前端：159/159，通过新增入口、旧 Studio 切换移除、Fabric 生命周期、原始像素导出与 G1-G3 静态回归。
- Python：339 项，338 通过、1 项平台预期跳过；IC2 未改 Python、schema、ROI/Mask、strict compose 或 outpaint。
- Vite production build、`verify:canvas-bundle`、Rust/Tauri locked custom-protocol、Python compileall、52 个受控 JavaScript/ESM 文件语法、Git whitespace：通过。
- `npm audit --omit=dev`：0 漏洞。相对 IC1 lockfile 新增 239 个依赖实例，已有依赖版本变化 0、删除 0。
- `package-lock.json` SHA-256：`A0758630BC38B94F76D71D0CB74D72AE5BC20829073C2A613F0F1651719F86BD`。

## 正式版保护与回滚

- 正式版仍为 Git `93539f0c9ec857d22d3751bb836ff722579cd8db`、contract `2026-09-02.3`、schema v7、`375,974,706 bytes`（358.56 MiB）。
- 正式 App、sidecar、manifest SHA-256 仍为 `F0D1A073...`、`2AF3FC31...`、`287042C0...`，与 `build/last-portable-promotion.json` 和 G3 发布证据一致。
- IC2 回滚点为 IC1 标签 `checkpoint-2026-09-02-excalidraw-spatial-poc`；本检查点标签为 `checkpoint-2026-09-02-infinite-canvas-ic2`。

## 下一执行游标

进入 IC3：冻结 schema v8 scene envelope、业务引用、内容指纹、乐观并发和缩略图合同，再以临时 SQLite 完成 v7→v8 可恢复迁移、不可变 scene 版本、重启恢复、冲突和损坏数据门禁。IC3 完成前不接正式账本，不提升正式便携版。
