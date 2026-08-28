const STATUS_DEFINITIONS = Object.freeze({
  loading: {
    eyebrow: 'LOADING', title: '正在读取', detail: '请稍候，本地数据正在同步。',
    symbol: '…', role: 'status', live: 'polite', busy: true,
  },
  empty: {
    eyebrow: 'READY', title: '这里还是空的', detail: '完成第一项操作后，内容会出现在这里。',
    symbol: '○', role: 'status', live: 'polite', busy: false,
  },
  offline: {
    eyebrow: 'OFFLINE', title: '本地服务暂不可用', detail: '已保留当前内容；恢复连接后可以继续。',
    symbol: '×', role: 'alert', live: 'assertive', busy: false,
  },
  conflict: {
    eyebrow: 'MERGING', title: '检测到另一处更新', detail: '已读取最新版本，正在合并本次修改。',
    symbol: '↕', role: 'status', live: 'polite', busy: true,
  },
  partial: {
    eyebrow: 'PARTIAL', title: '部分项目需要处理', detail: '成功项目已保留，只需处理失败部分。',
    symbol: '!', role: 'status', live: 'polite', busy: false,
  },
  recovered: {
    eyebrow: 'RECOVERED', title: '现场已恢复', detail: '素材、参数和任务进度已经回到上次状态。',
    symbol: '✓', role: 'status', live: 'polite', busy: false,
  },
  error: {
    eyebrow: 'ATTENTION', title: '当前操作未完成', detail: '请检查提示后重试。',
    symbol: '!', role: 'alert', live: 'assertive', busy: false,
  },
});

function escapeStatusHtml(value) {
  return String(value ?? '')
    .replaceAll('&', '&amp;')
    .replaceAll('<', '&lt;')
    .replaceAll('>', '&gt;')
    .replaceAll('"', '&quot;')
    .replaceAll("'", '&#39;');
}

function normalizeAction(action) {
  if (!action?.label) return null;
  const attribute = /^data-[a-z0-9-]+$/.test(String(action.attribute || ''))
    ? String(action.attribute)
    : 'data-status-action';
  return {
    label: String(action.label),
    attribute,
    value: String(action.value || 'retry'),
    disabled: Boolean(action.disabled),
    busy: Boolean(action.busy),
  };
}

export function statusViewModel(kind = 'empty', overrides = {}) {
  const normalizedKind = STATUS_DEFINITIONS[kind] ? kind : 'error';
  const base = STATUS_DEFINITIONS[normalizedKind];
  return {
    ...base,
    kind: normalizedKind,
    eyebrow: String(overrides.eyebrow ?? base.eyebrow),
    title: String(overrides.title ?? base.title),
    detail: String(overrides.detail ?? base.detail),
    symbol: String(overrides.symbol ?? base.symbol),
    compact: Boolean(overrides.compact),
    fill: Boolean(overrides.fill),
    inline: Boolean(overrides.inline),
    action: normalizeAction(overrides.action),
  };
}

export function statusPanelHtml(kind = 'empty', overrides = {}) {
  const model = statusViewModel(kind, overrides);
  const classes = [
    'status-panel',
    `status-panel--${model.kind}`,
    model.compact ? 'status-panel--compact' : '',
    model.fill ? 'status-panel--fill' : '',
    model.inline ? 'status-panel--inline' : '',
  ].filter(Boolean).join(' ');
  const action = model.action
    ? `<button class="status-panel__action secondary-button" type="button" ${model.action.attribute}="${escapeStatusHtml(model.action.value)}"${model.action.disabled ? ' disabled' : ''}${model.action.busy ? ' aria-busy="true"' : ''}>${escapeStatusHtml(model.action.label)}</button>`
    : '';
  return `<section class="${classes}" data-status-kind="${model.kind}" role="${model.role}" aria-live="${model.live}" aria-atomic="true" aria-busy="${model.busy}">
    <span class="status-panel__mark" aria-hidden="true">${escapeStatusHtml(model.symbol)}</span>
    <div class="status-panel__copy"><span class="status-panel__eyebrow">${escapeStatusHtml(model.eyebrow)}</span><strong>${escapeStatusHtml(model.title)}</strong><p>${escapeStatusHtml(model.detail)}</p></div>
    ${action}
  </section>`;
}

export const STATUS_KINDS = Object.freeze(Object.keys(STATUS_DEFINITIONS));
