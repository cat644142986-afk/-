# 无限画布 IC3 持久化检查点（2026-09-02）

## 结论

IC3 源码与真实交互门禁通过。无限画布 scene 已接入现有 SQLite 账本的 schema v8，不新建第二套业务账本；文档元数据与不可变 scene 版本分离，完整 scene 只在打开画布时读取。

这仍不是正式便携版发布。全部迁移、重启、冲突和损坏数据测试只使用隔离临时 SQLite；正式目录、正式快捷方式、正式用户账本和正式 artifact 均未修改。

## 实现范围

- 实现提交：`5bd693282e816b172cb493bfb2d64bfe05da8364`。
- sidecar contract：`2026-09-02.4`；开发 schema：v8。
- 新增 `spatial_canvas_documents`、`spatial_canvas_scene_versions`、`spatial_scene_requests`、`spatial_scene_references`，继续复用现有素材、任务、ProductProfile 和血缘外键。
- 保存采用 expected revision 乐观并发、请求幂等、scene 内容去重、scene/thumbnail SHA-256；重复 `onChange` 不制造空版本。
- 前端 adapter 串行保存；真实 409 冲突时回载服务端最新 scene，绝不覆盖较新版本。
- scene 合同拒绝 Base64、非空 `files`、机器绝对路径、悬空业务引用和损坏哈希；4K 原图与视频字节仍由素材系统管理。
- `boundElements` 缺失或为 `null` 时规范化为 `[]`；Excalidraw NanoID 兼容 `_`、`-` 开头。
- Product Atelier 作用域隐藏无关 Web Embed、Laser 和 Mermaid，仅保留 Frame；Fabric `CanvasDocument v1` 与像素编辑职责未改。

## 真实恢复与冲突验收

隔离临时 SQLite 完成新建、重命名、绘制、撤销、重做、Frame、分组、锁定、缩放和平移保存。sidecar 重启后恢复 4 个元素、1 个 Frame、2 个锁定元素和 2 个分组元素。

重开前后 revision 均为 14，证明只读打开不会追加空版本。真实 409 路径中，外部写入把 revision 16 更新为 17 并把缩放改为 55%；旧页面保存时正确回载 revision 17 和 55% 视口，最终仍为 4 个元素且没有覆盖或新增版本，控制台 0 error / 0 warning。

## 窗口与性能

| 窗口 | 横向溢出 | 纵向溢出 | 截图 |
| --- | ---: | ---: | --- |
| 1440×900 | 0 | 0 | `artifacts/excalidraw-spatial-ic3/viewport-1440x900.png` |
| 1280×720 | 0 | 0 | `artifacts/excalidraw-spatial-ic3/viewport-1280x720.png` |
| 960×600 | 0 | 0 | `artifacts/excalidraw-spatial-ic3/viewport-960x600.png` |

- production `dist`：`10,575,407 bytes`。
- 预计正式便携目录：368.65 MiB，低于 450 MiB 上限。
- 快捷处理初始 HTML `modulepreload` 为 0；Excalidraw 继续只在新建或打开无限画布时懒加载。
- `npm audit --omit=dev`：0 漏洞。

## G1-G3 与工程门禁

- 前端：161/161。
- Python：352 项，351 通过、1 项平台预期跳过。
- 发布事务专项：31/31。
- G1/G3 命名专项：4/4，覆盖原始像素导出、严格选区外零差异、扩图只写新增区域、旋转/放置/像素回执。
- Vite production build、画布 bundle 合同、Rust/Tauri locked custom-protocol、Python compileall、64 个受控 JavaScript 文件语法、PowerShell parser 和 Git whitespace：通过。
- `package-lock.json` SHA-256：`A0758630BC38B94F76D71D0CB74D72AE5BC20829073C2A613F0F1651719F86BD`。

## 正式版保护与回滚

- 正式版仍为 Git `93539f0c9ec857d22d3751bb836ff722579cd8db`、contract `2026-09-02.3`、schema v7、`375,974,706 bytes`（358.56 MiB）。
- 正式 App、sidecar、manifest SHA-256 分别为 `F0D1A07313A47258CD17FA4143DF4F0069D6E075CAA9D22B00EB7220A15685A7`、`2AF3FC31A90961D77A41BE6CECAFD3559E0D0FFFB505FD9BBEA9EC7C61293894`、`287042C051B97CC9F60C42F670C470EBEC965042378B790E0F4F13FCC9718A38`。
- IC3 实施前回滚点为 `checkpoint-2026-09-02-infinite-canvas-ic2`；本检查点标签为 `checkpoint-2026-09-02-infinite-canvas-ic3`。

## 下一执行游标

进入 IC4：把快捷处理结果、素材库、历史结果和任务发送或拖入画布，并建立画布图片到现有 Fabric 精修的往返桥。精修结果必须在父节点右侧生成新版本、写入现有血缘并连线，禁止覆盖父节点。
