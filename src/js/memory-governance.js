export const MEMORY_FILTERS = Object.freeze(['pending', 'approved', 'disabled', 'all']);

const STATUS_COPY = Object.freeze({
  pending: '待审核',
  approved: '已采用',
  rejected: '已拒绝',
  dismissed: '已撤回',
  disabled: '已停用',
});

const ACTION_COPY = Object.freeze({
  edit: { label: '编辑建议', tone: 'secondary' },
  approve: { label: '采用', tone: 'primary' },
  reject: { label: '拒绝', tone: 'danger', confirm: true },
  postpone: { label: '稍后处理', tone: 'secondary' },
  disable: { label: '停用', tone: 'danger', confirm: true },
  enable: { label: '重新启用', tone: 'primary' },
  reopen: { label: '重新审核', tone: 'secondary' },
  undo: { label: '撤销上次变更', tone: 'secondary' },
  redo: { label: '恢复撤销', tone: 'secondary' },
});

export function memoryStatusLabel(status) {
  return STATUS_COPY[String(status || '')] || String(status || '待审核');
}

export function memoryScopeLabel(item) {
  const type = String(item?.scope_type || 'designer');
  const id = String(item?.scope_id || '').trim();
  const category = String(item?.category || 'general').trim();
  if (type === 'project') return id ? `项目 · ${id}` : '当前项目';
  if (type === 'brand') return id ? `品牌 · ${id}` : '当前品牌';
  if (type === 'category') return `品类 · ${id || category || '通用'}`;
  return '个人通用';
}

export function memorySuggestionsForFilter(items, filter = 'pending') {
  const source = Array.isArray(items) ? items : [];
  const selected = MEMORY_FILTERS.includes(filter) ? filter : 'pending';
  if (selected === 'all') return [...source];
  return source.filter((item) => String(item?.status || '') === selected);
}

export function memoryGovernancePresentation(item) {
  const governance = item?.governance && typeof item.governance === 'object'
    ? item.governance : {};
  const available = Array.isArray(governance.available_actions)
    ? governance.available_actions : [];
  const actions = available
    .filter((action) => ACTION_COPY[action])
    .map((action) => ({ action, ...ACTION_COPY[action] }));
  return {
    revision: Math.max(1, Number(governance.revision || 1)),
    historyCount: Math.max(0, Number(governance.history_count || 0)),
    redoCount: Math.max(0, Number(governance.redo_count || 0)),
    status: String(item?.status || 'pending'),
    statusLabel: memoryStatusLabel(item?.status),
    scopeLabel: memoryScopeLabel(item),
    actions,
  };
}
