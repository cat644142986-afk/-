# UI 素材库使用指南

> 文件：`src/css/ui-materials.css`（20KB，50个即用class）
> 所有class以 `ui-mat-` 开头，不与现有样式冲突。

---

## 快速引入

在 `src/css/style.css` 顶部加一行：

```css
@import url("./ui-materials.css");
```

引入后**不会改变任何现有样式**，只在你给元素加class时生效。

---

## 素材速查表

### 毛玻璃（你最需要的）

| Class | 效果 | 用在哪 |
|---|---|---|
| `ui-mat-glass-light` | 轻量模糊16px | 下拉菜单、tooltip、小浮层 |
| `ui-mat-glass-med` | 中等模糊24px+阴影+边框 | **AI聊天面板、设置面板**（推荐） |
| `ui-mat-glass-heavy` | 重度模糊36px+大阴影 | Modal弹层、重要对话框 |
| `ui-mat-glass-dark` | 深色毛玻璃 | 暗色sidebar上的浮层 |
| `ui-mat-glass-accent` | +顶部橙色暖光晕 | AI面板叠加上（搭配med/heavy） |
| `ui-mat-backdrop` | 模糊遮罩8px | Modal背景遮罩（替代纯黑rgba） |

**示例 - AI面板：**
```html
<div class="ui-mat-glass-med ui-mat-glass-accent">
  AI 对话内容（内容需要position:relative;z-index:1才在光晕上方）
</div>
```

### 软分区（替代硬卡片）

| Class | 背景色 | 说明 |
|---|---|---|
| `ui-mat-soft-1` | #f5f6f8 | 浅色软背景，圆角16px，无边框无阴影 |
| `ui-mat-soft-2` | #f0f1f4 | 深一级软背景 |
| `ui-mat-soft-inset` | #f5f6f8 + 内嵌光边 | 最高级感，有极细的顶亮/底暗 |

**使用方式：** 给现有 `.progress-panel`、`.results-panel` 等加 `class="progress-panel ui-mat-soft-1"` 替换白卡背景。

### 按钮升级

| Class | 效果 |
|---|---|
| `ui-mat-btn-shine` | hover时白色光泽扫过按钮表面 |
| `ui-mat-btn-press` | 点击时弹性下压（scale 0.98 + 下移1px） |
| `ui-mat-pulse-accent` | 空闲时脉冲呼吸光晕引导点击 |

**推荐：** 给主按钮加：`class="btn-primary ui-mat-btn-shine ui-mat-btn-press"`

### 动画入场

| Class | 效果 | 时长 |
|---|---|---|
| `ui-mat-fade-in` | 从下方8px淡入 | 0.3s |
| `ui-mat-slide-in-right` | 从右侧16px滑入 | 0.3s |
| `ui-mat-pop-in` | 弹性缩放出现 | 0.35s（Modal推荐） |
| `ui-mat-lift` | hover时上浮2px | 卡片/可点击项 |
| `ui-mat-scale` | hover放大1.02/按下0.98 | 图片/图标 |

### 状态指示

| Class | 效果 |
|---|---|
| `ui-mat-dot-online` | 绿色呼吸灯（替conn-dot.online） |
| `ui-mat-thinking` | 三点跳动AI思考中（`<span></span>`×3） |
| `ui-mat-progress-shine` | 进度条光带流动效果 |
| `ui-mat-skeleton` | 骨架屏闪烁加载 |

### 滚动条

| Class | 效果 |
|---|---|
| `ui-mat-scroll` | 4px细灰色滚动条 |
| `ui-mat-scroll-dark` | 4px细白色滚动条（深色区域） |

**用法：** 给需要滚动的容器加 `class="... ui-mat-scroll"`

### Tooltip

```html
<button class="ui-mat-tooltip" data-tip="保存图片">...</button>
```

### AI专属效果

| Class | 效果 |
|---|---|
| `ui-mat-ai-ring` | 元素周围旋转的橙光渐变环（头像/AI按钮） |
| `ui-mat-dots-bg` | 微妙圆点装饰背景 |
| `ui-mat-msg-in` | 消息气泡入场动画 |

---

## 即取配方（Recipes）

这些是组合好的效果，直接抄class即可。

### 配方A：AI玻璃面板（毛玻璃+橙光晕+阴影）
```html
<div class="ui-mat-recipe-ai-panel">
  <div style="position:relative;z-index:1">
    面板内容...
  </div>
</div>
```

### 配方B：图片卡片（悬停上浮+图片缩放）
```html
<div class="ui-mat-recipe-img-card" onclick="...">
  <img src="..." />
</div>
```
可替代现有 `.result-item` 和 `.history-item` 的阴影/hover效果。

### 配方C：主按钮光泽效果
```html
<button class="btn-primary ui-mat-recipe-cta">开始生成</button>
```
包含：光泽扫过 + 弹性按压 + 平滑过渡。

### 配方D：拖拽区发光
JS中dragover事件触发时：`dropzoneEl.classList.add('ui-mat-recipe-dragover')`
dragleave/drop时：`dropzoneEl.classList.remove('ui-mat-recipe-dragover')`

---

## Ripple涟漪效果（需要JS）

在 `app.js` 中加入以下代码即可让所有按钮点击时有涟漪：

```javascript
function uimRipple(e) {
  const el = e.currentTarget;
  const rect = el.getBoundingClientRect();
  const size = Math.max(rect.width, rect.height) * 2;
  const x = e.clientX - rect.left - size / 2;
  const y = e.clientY - rect.top - size / 2;
  const dot = document.createElement("span");
  dot.className = "uim-ripple-dot";
  dot.style.width = dot.style.height = size + "px";
  dot.style.left = x + "px";
  dot.style.top = y + "px";
  el.appendChild(dot);
  setTimeout(() => dot.remove(), 600);
}
document.querySelectorAll(".btn-primary,.icon-btn,.dropzone-btn,.preview-replace").forEach(btn => {
  btn.classList.add("ui-mat-ripple");
  btn.addEventListener("click", uimRipple);
});
```

---

## 性能注意事项

1. **backdrop-filter嵌套不超过2层**，否则GPU压力大
2. **大面积区域不要用backdrop-filter**（如整个content-wrapper），只用于浮层
3. 动画全部用transform/opacity，不触发layout/paint
4. `@supports not (backdrop-filter: blur(1px))` 已做降级
5. 所有动画0.15s-0.4s之间，符合Apple HIG
