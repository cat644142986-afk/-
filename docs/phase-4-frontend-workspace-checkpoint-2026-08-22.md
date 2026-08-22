# Phase 4 前端工作区检查点

> 日期：2026-08-22  
> 分支：`codex/master-roadmap-phase-0-1`  
> sidecar contract：`2026-08-22.4`  
> 定位：正式前端骨架的可运行检查点，不代表 Phase 4 全部收尾。

## 1. 本检查点已经解决什么

### 1.1 三个素材域与四个独立现场

- `single` 与 `multi-file` 读取同一个 `product` 素材域，但分别保存选择、描述、参数、活动任务和结果指针。
- `group-split` 使用独立 `group` 素材域；`cutout-batch` 使用独立 `cutout` 素材域。
- 切换工作流时先捕获当前现场，再从后端工作区接口恢复目标现场；素材域共享不等于草稿共享。
- localStorage 只保留当前工作流这类瞬时 UI 偏好，不再承担素材、任务或草稿的事实来源。

### 1.2 草稿、提交与异步安全

- 前端新增 `studio-config.js`、`studio-state.js` 和 `studio-shell.js`，集中维护模式契约、集合映射、状态机、纯快照函数与窄屏任务控制器。
- 草稿通过 `GET /api/workspaces/{mode}` 和 `PUT /api/workspaces/{mode}/draft` 持久化，使用 revision 乐观并发控制。
- 输入和核心参数防抖自动保存；页面隐藏时主动落盘；异步返回只写回发起操作的工作流。
- 提交任务前先创建不可变前端快照并确认草稿已持久化，防止等待知识编译期间切换模式导致任务串台。
- 活动任务、最近结果和当前结果素材 ID 写回相应工作流；返回时从账本恢复，不依赖页面内存猜测。

### 1.3 主界面减法与连续工作

- 正式 Studio 改为融合式左轨、68–72px 顶栏、中央素材/结果舞台和单一右侧任务控制区。
- 移除一级“对比”和底部两张常驻摘要大卡；对比保留在结果上下文中。
- Logo 视觉尺寸为 44px，外框 CSS 圆角为 24px，红黄绿视觉点为 12px。
- 右侧任务控制区在 1280×720 实测无内部滚动；960×600 时切换为可关闭抽屉，不再挤压中央舞台。
- 窄屏抽屉关闭时使用 `inert` 阻止键盘误入，打开后移动焦点，`Esc` 关闭并恢复到触发按钮。
- Studio 核心文案采用 micro/caption/control/body 分级 token；中文输入、模式、参数和空态正文不再全部压到 7–10px。
- Result、会话、成长、设置和 Task Dock 的核心中文与控制文字也已提升到 caption/control token；960px 设置页改为单一外层滚动，不在每张设置卡里制造嵌套滚动条。
- 素材卡加入“移出当前域”入口，调用后端软删除而非破坏历史物理文件。

### 1.4 Windows 壳与 DPI 坐标契约

- Tauri 外壳底色与 CSS 根底色统一为 `#F1EEE8`，减少 WebView 边缘出现异色底层的机会。
- Windows 11 显式请求系统 `DWMWCP_ROUND` 圆角并将 DWM 额外边框设为 `DWMWA_COLOR_NONE`；保留系统阴影，不叠加一圈边框。
- monitor、window size/position 与 set bounds 统一使用物理像素；同时返回 logical 尺寸和 scale factor，修复 125%/150% 及混合 DPI 下“物理坐标读取、逻辑坐标写回”的尺寸偏差。
- `PRODUCT_ATELIER_DATA_DIR` 现在同时隔离 Rust 外壳配置/日志与 Python 账本；整包测试不再把 shell 日志写进用户正式 AppData。

### 1.5 快速抠图不再伪装成语义选物

- `cutout-batch` 隐藏 Creative Brief、云端模型、构图参数和 Intent Locks。
- 界面明确说明当前能力是“分离全部前景”，不理解物体名称、数量或“只保留某物体”的文字要求。
- 快速抠图不调用知识编译；知识卡变为不可交互的能力回执，并说明文字与知识规则不会进入执行链。
- “只保留两个汉堡”属于后续“智能选物”工作流，不再被错误包装为当前 BiRefNet 去背景能力。

## 2. 本次发现并修复的生产故障

Phase 3 新增了工作区 `PUT` 和素材 `DELETE` 接口，但 CORS 只声明了 `GET/POST/PATCH/OPTIONS`。因此浏览器会出现“读取正常、草稿保存与素材移除失败”的假连接状态。

修复后允许：

```text
GET / POST / PUT / PATCH / DELETE / OPTIONS
```

并增加预检回归。sidecar contract 升级为 `2026-08-22.4`，发布包必须重新构建，不能继续使用 `.3` sidecar。

## 3. 实际验证证据

- Python：87/87 通过。
- 前端 Node：29/29 通过。
- Vite：生产构建通过。
- Rust：`cargo check` 通过。
- 浏览器实测：四个工作流逐一切换后，前三个显示创作描述，快速抠图只显示真实能力边界；模块拆分后的 Studio、会话、成长、设置与任务抽屉控制台 0 error / 0 warning。
- 1440×900、1280×720：文档与视口一致；Task Dock `clientHeight === scrollHeight`；会话、成长和设置主页面无文档级溢出。
- 960×600：中央舞台独占主网格；任务控制抽屉 353×513，`clientHeight === scrollHeight`，主 CTA 可见，关闭/打开时 `inert` 与 `aria-expanded` 状态正确。
- 960×600 设置页：四张卡片无内部滚动，只有 `.settings-layout` 一个明确外层滚动容器。
- 隔离临时账本实测：四个工作流分别保存不同描述，连续切换 20 次后内容与 revision 正确；无变化切换不会继续制造无意义 revision。
- 便携版整包：正式 Tauri 外壳成功拉起动态端口 sidecar，contract `2026-08-22.4`、schema v3 与隔离账本创建均通过；测试结束只清理本轮应用、子进程和临时数据。

所有自动化任务使用 mock 或本地临时数据，不调用真实云端模型、不消耗生图额度。

## 4. 仍未完成，不能误报

Phase 4 后续仍需：

1. 将 `app.js` 继续按 workspace/assets/jobs/review/knowledge/settings 拆分；当前已抽出 config/state/shell，业务控制器仍在主文件。
2. 在 Tauri 便携壳完成 Windows 100%/125%/150% DPI、最大化、最小尺寸和真实多显示器拖动验证；代码坐标契约已修复，但不能把单显示器当前倍率冒充三档实测。
3. 验证窗口 24px CSS 几何与 Windows 11 实际外框在所有 DPI 下无底层漏出。
4. 继续完成真实有数据状态下的结果评审和 10+20 任务详情排版；空态/基础态的非 Studio 字体与滚动层级已经收口。
5. 补齐空、加载、离线、冲突恢复等状态的统一组件和键盘/焦点回归。

Phase 5 仍需：完整素材管理器、撤销提示、回收站和引用解释 UI；Task Dock 的跨工作流筛选与真实 10+20 并发场景。

## 5. 下一步执行顺序

1. 先完成 Phase 4 的打包壳/DPI 与模块边界；960×600 窄屏门禁已经通过。
2. 再进入 Phase 5：素材管理、回收站、工作流定位和 Task Dock 场景验收。
3. 随后进入 Phase 6：Result Review 与情境对比。
4. 智能选物与抠图模型评测留在 Phase 7；在此之前继续保持快速去背景的真实边界。
