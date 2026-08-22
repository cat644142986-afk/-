# Product Atelier - AI商拍工作台

> AI驱动的电商产品摄影桌面工作台 · Tauri v2 + FastAPI · Windows 桌面应用

## 功能特性

- **单产品商拍**: 上传产品图，VLM自动识别产品，两阶段AI生成纯白底商业影棚级主图
- **自动抠图**: BiRefNet-HD本地模型，输出透明背景PNG
- **多产品批量**: 自动检测多产品布局，逐一切割→AI优化→抠图
- **快速抠图**: 纯本地BiRefNet抠图，无需API
- **持久素材工作台**: 图片按 SHA-256 去重保存，切模式、刷新和重启后仍可继续使用
- **分域任务现场**: 单/多产品共享产品素材，但草稿独立；合照与抠图各自保留素材、选择、任务和预览
- **安全回收站**: 支持软删除、恢复、引用说明与带保留期的受控永久清理
- **可靠批量任务**: SQLite 持久队列、有界并发、公平调度、取消、失败项重试与崩溃恢复
- **任务 Dock**: 恢复数据库中的总体/逐项完成度、独立成功率、错误和部分结果，支持暂停/继续、取消与失败项重试
- **原图/效果图对比**: 拖动滑块对比前后差异
- **参数调节**: 模型选择、器皿保留/去除、拍摄角度、美化度、生成数量
- **配置持久化**: API密钥和偏好设置本地存储，重启自动保留
- **可解释成长记录**: 保存实际 Prompt、采用/忽略知识、模型参数、失败阶段和结果评审，避免反馈无去向
- **原生交互**: 拖拽上传、Ctrl+V粘贴、系统保存对话框
- **真实能力边界**: 快速抠图只显示会被执行器读取的控制，不把文字描述伪装成语义选物

## UI设计

- 暖中性、墨黑与珊瑚橙构成的克制创作工作台，英文只用于短标签装饰
- 24px 不透明圆角壳、融合式左轨、中央素材/结果舞台和单一右侧任务控制区
- 960×600 时任务控制变为带焦点管理的抽屉，关闭后不会占用键盘导航，也不会挤压主舞台
- 自定义标题栏，红黄绿视觉点为 12px；Logo 只作品牌标识
- 对比位于结果上下文，完整任务进度位于 Task Dock，不用常驻卡片堆满首屏
- 1280×800 默认窗口，最小 960×600，支持自由缩放

## 技术栈

| 层级 | 技术 |
|------|------|
| 桌面外壳 | Tauri v2 (Rust) |
| 前端 | 原生 HTML/CSS/JS + Vite |
| 后端 | Python FastAPI |
| 本地数据 | SQLite schema v3 + 分域工作区 + 内容寻址素材目录 |
| 任务执行 | 持久 JobEngine + 有界线程池 + 分资源并发闸门 |
| AI生成 | LK AI API (GPT-Image-2, Gemini Nano Banana, 千问等) |
| 本地抠图 | BiRefNet-General (ONNX Runtime) |
| 打包 | NSIS安装包 + PyInstaller onefile后端 |

## 环境依赖

### 构建环境
- **Node.js** v20+ (`C:\Program Files\nodejs`)
- **Rust** 1.97+ with **x86_64-pc-windows-gnu** toolchain (`%USERPROFILE%\.cargo`)
- **MinGW-w64** 14.2+ (`C:\mingw64`)
- **Python** 3.12 with packages:
  ```
  pip install -r python/requirements.txt
  ```

### 环境变量 (构建前必须设置)
```powershell
$env:PATH = "C:\mingw64\bin;$env:USERPROFILE\.cargo\bin;C:\Program Files\nodejs;$env:PATH"
$env:CARGO_TARGET_DIR = "D:\rust-target"
```

## 构建命令

### 方式一：一键构建安装包
```powershell
cd C:\ProductAtelier-Desktop
# 设置环境变量后运行:
.\build-installer.bat
```
输出: `D:\rust-target\release\bundle\nsis\Product Atelier_1.0.0_x64-setup.exe` (~113MB)

### 方式二：分步构建（推荐）

```powershell
cd D:\ProductAtelier-Desktop
$env:PATH = "C:\mingw64\bin;$env:USERPROFILE\.cargo\bin;C:\Program Files\nodejs;$env:PATH"
$env:CARGO_TARGET_DIR = "D:\rust-target"

# 1. 从当前源码重建Python后端、生成源码指纹并同步便携版
powershell -ExecutionPolicy Bypass -File tools\Build-Sidecar.ps1 -DeployPortable

# 2. 静态哈希 + 独立运行时健康检查
powershell -ExecutionPolicy Bypass -File tools\Test-Portable.ps1

# 3. 构建完整Tauri应用 (前端Vite + Rust + NSIS)
npm run tauri build

# 4. 部署便携外壳后，按正式启动链执行整包冒烟测试
powershell -ExecutionPolicy Bypass -File tools\Test-Portable-App.ps1
```

不要直接把旧 `python-server` 目录复制进发布包。正式 sidecar 必须带
`sidecar-manifest.json`，且 `/api/health` 返回的 contract、源码指纹与清单一致。
`Test-Portable-App.ps1` 会从桌面壳实际拉起动态端口 sidecar，使用隔离数据目录验证
应用进程、服务契约和 SQLite 账本，并只清理本轮创建的测试进程与临时数据。

### 方式三：便携版
```powershell
.\build-portable.bat
```
输出: `dist\ProductAtelier-Portable\` (~375MB，双击exe直接运行)

### 开发调试
```powershell
# 默认重建 sidecar、前端和 Rust，验证后再启动
powershell -ExecutionPolicy Bypass -File tools\dev.ps1

# 只跳过 Rust；sidecar 仍会重建
powershell -ExecutionPolicy Bypass -File tools\dev.ps1 -Quick
```

## 目录结构

```
ProductAtelier-Desktop/
├── src/                    # 前端源码
│   ├── index.html          # 主页面 (含自定义标题栏/侧边栏/工作区)
│   ├── css/style.css       # 玻璃拟态样式
│   └── js/
│       ├── api.js          # HTTP + Tauri invoke 封装
│       ├── studio-config.js# 工作流、集合与状态契约
│       ├── studio-state.js # 状态树与纯快照函数
│       └── app.js          # 当前应用编排（仍在继续按职责拆分）
├── python/
│   ├── server.py           # FastAPI、四工作流与持久任务 API
│   ├── atelier_ledger.py   # SQLite schema、迁移、素材血缘与任务状态
│   ├── asset_store.py      # 内容寻址素材存储与安全读取
│   └── job_engine.py       # 有界并发、公平调度、取消、重试与恢复
├── tests/                  # 全离线迁移、素材、任务与崩溃恢复测试
├── src-tauri/
│   ├── src/main.rs         # Rust外壳 (窗口管理/Python sidecar/原生对话框)
│   ├── Cargo.toml
│   ├── tauri.conf.json     # Tauri配置 (1280×800, 无边框, NSIS)
│   ├── capabilities/       # 权限配置
│   ├── icons/              # 应用图标
│   └── bin/python-server/  # PyInstaller编译的后端 (构建后生成)
├── dist/                   # Vite构建输出 + 便携版
├── python-server.spec      # PyInstaller配置
├── dev.bat                 # 开发模式启动
├── build-installer.bat     # NSIS安装包一键构建
├── build-portable.bat      # 便携版一键构建
└── build-python.bat        # 仅编译Python后端
```

## 已构建的可分发文件

| 文件 | 路径 | 大小 |
|------|------|------|
| NSIS安装包 | `D:\rust-target\release\bundle\nsis\Product Atelier_1.0.0_x64-setup.exe` | ~113 MB |
| 便携版ZIP | `D:\ProductAtelier-Desktop\dist\ProductAtelier-Portable-v1.0.0.zip` | ~157 MB |
| 便携版文件夹 | `D:\ProductAtelier-Desktop\dist\ProductAtelier-Portable\` | ~374 MB |

## 数据存储

| 数据 | 位置 |
|------|------|
| 配置文件 | `%APPDATA%\ProductAtelier\config.json` |
| 创作账本 | `%APPDATA%\ProductAtelier\atelier.sqlite3` |
| 持久源素材 | `%APPDATA%\ProductAtelier\assets\` |
| 生成的图片 | `%APPDATA%\ProductAtelier\output\` |
| 抠图模型 | `%USERPROFILE%\.u2net\birefnet-general.onnx` (首次运行自动下载，~928MB) |
| 运行日志 | `%APPDATA%\ProductAtelier\output\workbench.log` |

## API配置

应用使用 LK AI 平台 API。API Key 仅保存在本机配置中，可在设置页面录入或更换，不进入 Git。
支持的模型:
- GPT-Image-2 (最高质量)
- Nano Banana Pro / Nano Banana 2 (Gemini系列)
- 千问-Image (中文优化)

## 注意事项

- 首次抠图需下载BiRefNet模型 (~928MB)，请保持网络连接
- 项目路径含中文字符时无法链接编译（GNU linker限制），构建目录使用 `D:\ProductAtelier-Desktop\`
- NSIS安装包默认安装到 `%LOCALAPPDATA%\Programs\product-atelier\`
- 便携版解压后直接运行 `Product Atelier.exe`，无需安装

## 离线验收

```bash
python -m unittest discover -s tests -v
npm run test:frontend
npm run build
```

测试使用临时数据库、mock 引擎和被强制终止的测试子进程；不会调用真实云端生成或消耗额度。底层任务契约见 `docs/ledger-schema-v2.md`，分域工作区与不可变快照见 `docs/ledger-schema-v3.md`。
