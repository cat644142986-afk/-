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

// PWC-1 keeps failure meaning separate from the surface that renders it. The
// category answers what was affected; the recovery entry answers what the one
// valid next action is on that surface.
export const FAILURE_POLICIES = Object.freeze({
  read: Object.freeze({ preservation: '已提交成果未受影响。', paidCall: 'not-started' }),
  save: Object.freeze({ preservation: '未提交修改仍保留在当前工作区。', paidCall: 'not-started' }),
  conflict: Object.freeze({ preservation: '不会覆盖其他已提交版本；重新同步会明确以账本最新版本为准。', paidCall: 'not-started' }),
  reference: Object.freeze({ preservation: '来源与已提交结果仍保留，可在引用恢复后继续。', paidCall: 'not-started' }),
  contract: Object.freeze({ preservation: '已阻止不兼容写入，现有账本未改变。', paidCall: 'not-started' }),
  runtime: Object.freeze({ preservation: '运行时失败不会清除已提交数据。', paidCall: 'not-started' }),
  task: Object.freeze({ preservation: '已完成项目和任务记录保持不变。', paidCall: 'reconcile-before-retry' }),
  provider: Object.freeze({ preservation: '已有结果保持不变；重试前必须核对调用回执。', paidCall: 'reconcile-before-retry' }),
});

export const RECOVERY_DEFINITIONS = Object.freeze({
  'fabric-read': Object.freeze({ family: 'read', kind: 'error', title: '画布读取失败', fallback: '本地画布接口暂不可用', action: 'retry-read', actionLabel: '重新读取' }),
  'fabric-save': Object.freeze({ family: 'save', kind: 'error', title: '画布尚未保存', fallback: '画布保存失败', action: 'retry-save', actionLabel: '重试保存' }),
  'fabric-conflict': Object.freeze({ family: 'conflict', kind: 'conflict', title: '检测到更新冲突', fallback: '另一版本已先写入', action: 'reload-conflict', actionLabel: '重新同步' }),
  'fabric-runtime': Object.freeze({ family: 'runtime', kind: 'error', title: '画布组件加载失败', fallback: '自由画布运行时暂不可用', action: 'retry-runtime', actionLabel: '重新加载' }),
  'fabric-export': Object.freeze({ family: 'reference', kind: 'error', title: '画板导出失败', fallback: '原始素材或导出服务暂不可用', action: 'retry-export', actionLabel: '重试导出' }),
  'fine-edit-entry': Object.freeze({ family: 'reference', kind: 'error', title: '精细修改无法打开', fallback: '素材或画布前置条件不可用', action: 'retry-fine-edit', actionLabel: '重新打开' }),
  'spatial-list-read': Object.freeze({ family: 'read', kind: 'error', title: '画布列表读取失败', fallback: '无法读取本地画布列表', action: 'retry-list', actionLabel: '重新读取' }),
  'spatial-open': Object.freeze({ family: 'runtime', kind: 'error', title: '画布加载失败', fallback: '无限画布暂不可用', action: 'retry-open', actionLabel: '重新加载' }),
  'spatial-save': Object.freeze({ family: 'save', kind: 'error', title: '画布尚未保存', fallback: '无限画布保存失败', action: 'retry-save', actionLabel: '重试保存' }),
  'spatial-conflict-copy': Object.freeze({ family: 'conflict', kind: 'error', title: '冲突副本尚未保存', fallback: '无法保存本地冲突副本', action: 'retry-conflict-copy', actionLabel: '重试保存副本' }),
  'spatial-import': Object.freeze({ family: 'reference', kind: 'error', title: '内容尚未加入画布', fallback: '画布内容导入失败', action: 'retry-import', actionLabel: '重试加入' }),
  'spatial-return': Object.freeze({ family: 'reference', kind: 'error', title: '精修结果尚未回填', fallback: '返回无限画布失败', action: 'retry-spatial-return', actionLabel: '重试回填' }),
  'task-resume': Object.freeze({ family: 'task', kind: 'partial', title: '任务尚未完成', fallback: '任务执行中断', action: 'resume-task', actionLabel: '恢复任务' }),
  'provider-reconcile': Object.freeze({ family: 'provider', kind: 'error', title: '调用结果需要核对', fallback: 'Provider 调用状态未知', action: 'reconcile-provider', actionLabel: '核对调用记录' }),
  'contract-refresh': Object.freeze({ family: 'contract', kind: 'error', title: '当前界面与命令合同不一致', fallback: '已阻止不兼容操作', action: 'reload-contract', actionLabel: '重新载入' }),
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

export function recoveryCause(error, fallback = '当前操作未完成') {
  const detail = error?.detail;
  const raw = detail?.message || error?.message || error || fallback;
  let message = String(raw).replace(/^Error:\s*/, '').trim() || String(fallback);
  if (error?.code === 'REQUEST_TIMEOUT' && !/超时/.test(message)) message = `${message}（请求超时）`;
  if (/HTTP 404/.test(message) && !/不存在|尚未就绪/.test(message)) message = `${fallback}（引用不存在或接口尚未就绪）`;
  if (/Failed to fetch|NetworkError|Load failed/i.test(message)) message = `${fallback}（无法连接本地服务）`;
  const code = String(detail?.code || error?.code || '').trim();
  if (code && !['HTTP_ERROR', 'REQUEST_TIMEOUT'].includes(code) && !message.includes(code)) message = `${message} [${code}]`;
  return message.slice(0, 280);
}

export function recoveryViewModel(recoveryId, error, overrides = {}) {
  const definition = RECOVERY_DEFINITIONS[recoveryId];
  if (!definition) throw new Error(`Unknown recovery definition: ${recoveryId}`);
  const policy = FAILURE_POLICIES[definition.family];
  const cause = String(overrides.cause || recoveryCause(error, definition.fallback));
  const preservation = String(overrides.preservation ?? policy.preservation);
  const detail = String(overrides.detail || [cause, preservation].filter(Boolean).join('；'));
  return {
    ...statusViewModel(definition.kind, {
      ...overrides,
      title: overrides.title || definition.title,
      detail,
      busy: false,
      role: overrides.role || 'alert',
      live: overrides.live || 'assertive',
      action: overrides.action || { label: definition.actionLabel, value: definition.action },
    }),
    recoveryId,
    family: definition.family,
    cause,
    preservation,
    paidCall: policy.paidCall,
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
    role: String(overrides.role ?? base.role),
    live: String(overrides.live ?? base.live),
    busy: Boolean(overrides.busy ?? base.busy),
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
