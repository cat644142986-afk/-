# Product Atelier - AI商拍工作台

> AI驱动的电商产品摄影桌面工作台 · Tauri v2 + FastAPI · Windows 桌面应用

## 功能特性

- **单产品商拍**: 上传产品图，VLM自动识别产品，两阶段AI生成纯白底商业影棚级主图
- **自动抠图**: BiRefNet-HD本地模型，输出透明背景PNG
- **多产品批量**: 自动检测多产品布局，逐一切割→AI优化→抠图
- **快速抠图**: 纯本地BiRefNet抠图，无需API
- **原图/效果图对比**: 拖动滑块对比前后差异
- **参数调节**: 模型选择、器皿保留/去除、拍摄角度、美化度、生成数量
- **配置持久化**: API密钥和偏好设置本地存储，重启自动保留
- **原生交互**: 拖拽上传、Ctrl+V粘贴、系统保存对话框、打开输出文件夹

## UI设计

- 简约B端轻玻璃拟态风格，暖橙色 #ff6b35 唯一强调色
- 深色侧边栏 + 圆角半透明白色毛玻璃主面板
- 自定义标题栏（最小化/最大化/关闭）
- 底部状态栏实时显示处理进度
- 1280×800 默认窗口，最小 960×600，支持自由缩放

## 技术栈

| 层级 | 技术 |
|------|------|
| 桌面外壳 | Tauri v2 (Rust) |
| 前端 | 原生 HTML/CSS/JS + Vite |
| 后端 | Python FastAPI |
| AI生成 | LK AI API (GPT-Image-2, Gemini Nano Banana, 千问等) |
| 本地抠图 | BiRefNet-General (ONNX Runtime) |
| 打包 | NSIS安装包 + PyInstaller onefile后端 |

## 环境依赖

### 构建环境
- **Node.js** v20+ (`C:\Program Files\nodejs`)
- **Rust** 1.97+ with **x86_64-pc-windows-gnu** toolchain (`C:\Users\64414\.cargo`)
- **MinGW-w64** 14.2+ (`C:\mingw64`)
- **Python** 3.12 with packages:
  ```
  pip install fastapi uvicorn rembg[cpu] onnxruntime pillow python-multipart
  ```

### 环境变量 (构建前必须设置)
```powershell
$env:PATH = "C:\mingw64\bin;C:\Users\64414\.cargo\bin;C:\Program Files\nodejs;$env:PATH"
$env:CARGO_TARGET_DIR = "D:\rust-target"
```

## 构建命令

### 方式一：一键构建安装包
```powershell
cd D:\ProductAtelier-Desktop
# 设置环境变量后运行:
.\build-installer.bat
```
输出: `D:\rust-target\release\bundle\nsis\Product Atelier_1.0.0_x64-setup.exe` (~113MB)

### 方式二：分步构建

```powershell
cd D:\ProductAtelier-Desktop
$env:PATH = "C:\mingw64\bin;C:\Users\64414\.cargo\bin;C:\Program Files\nodejs;$env:PATH"
$env:CARGO_TARGET_DIR = "D:\rust-target"

# 1. 构建Python后端 (PyInstaller onedir)
pyinstaller python-server.spec --distpath src-tauri/bin --workpath build/pyinstaller --noconfirm

# 2. 构建完整Tauri应用 (前端Vite + Rust + NSIS)
npm run tauri build
```

### 方式三：便携版
```powershell
.\build-portable.bat
```
输出: `dist\ProductAtelier-Portable\` (~375MB，双击exe直接运行)

### 开发调试
```powershell
.\dev.bat
# 或:
npm run tauri dev
```

## 目录结构

```
ProductAtelier-Desktop/
├── src/                    # 前端源码
│   ├── index.html          # 主页面 (含自定义标题栏/侧边栏/工作区)
│   ├── css/style.css       # 玻璃拟态样式
│   └── js/
│       ├── api.js          # HTTP + Tauri invoke 封装
│       └── app.js          # 应用逻辑
├── python/
│   └── server.py           # FastAPI后端 (AI生成/抠图/VLM)
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
| 生成的图片 | `%APPDATA%\ProductAtelier\output\` |
| 抠图模型 | `%USERPROFILE%\.u2net\birefnet-general.onnx` (首次运行自动下载，~928MB) |
| 运行日志 | `%APPDATA%\ProductAtelier\output\workbench.log` |

## API配置

应用使用LK AI平台API（已内置API Key）。如需更换，在设置页面输入自定义API Key即可。
支持的模型:
- GPT-Image-2 (最高质量)
- Nano Banana Pro / Nano Banana 2 (Gemini系列)
- 千问-Image (中文优化)

## 注意事项

- 首次抠图需下载BiRefNet模型 (~928MB)，请保持网络连接
- 项目路径含中文字符时无法链接编译（GNU linker限制），构建目录使用 `D:\ProductAtelier-Desktop\`
- NSIS安装包默认安装到 `%LOCALAPPDATA%\Programs\product-atelier\`
- 便携版解压后直接运行 `Product Atelier.exe`，无需安装
