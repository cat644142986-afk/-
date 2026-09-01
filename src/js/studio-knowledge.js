import {
  memoryGovernancePresentation,
  memorySuggestionsForFilter,
} from './memory-governance.js';

export function memoryFilterCounts(items) {
  const suggestions = Array.isArray(items) ? items : [];
  return {
    pending: suggestions.filter((item) => item.status === 'pending').length,
    approved: suggestions.filter((item) => item.status === 'approved').length,
    disabled: suggestions.filter((item) => item.status === 'disabled').length,
    all: suggestions.length,
  };
}

export function memoryQueueEmptyCopy(filter) {
  return {
    pending: ['暂无待审核建议', '继续完成结果评审后，重复模式会在这里出现。'],
    approved: ['暂无已采用规则', '只有你亲自采用的建议，才会介入之后的匹配任务。'],
    disabled: ['暂无已停用规则', '停用后不再介入新任务，历史证据仍然保留。'],
    all: ['暂无知识建议', '终稿反馈会先沉淀成可审核建议，不会直接修改正式知识。'],
  }[filter] || ['暂无内容', '请稍后刷新。'];
}

export function memoryHistoryLabel(entry) {
  const actions = {
    created: '建立建议', evidence_refresh: '证据更新', edit: '编辑', approve: '采用',
    reject: '拒绝', postpone: '稍后处理', disable: '停用', enable: '重新启用',
    reopen: '重新审核', undo: '撤销', redo: '恢复撤销', dismiss: '系统撤回',
  };
  return actions[String(entry?.action || '')] || '版本变更';
}

export function memorySuggestionCardMarkup(item, {
  targetId = '',
  expandedIds = new Set(),
  editingIds = new Set(),
  mutationsInFlight = new Set(),
  escapeHtml,
} = {}) {
  if (typeof escapeHtml !== 'function') throw new TypeError('escapeHtml is required');
  const id = String(item?.id || '');
  const key = id.replace(/[^a-zA-Z0-9_-]/g, '-');
  const proposed = item?.proposed_value && typeof item.proposed_value === 'object'
    ? item.proposed_value : {};
  const view = memoryGovernancePresentation(item);
  const governance = item?.governance && typeof item.governance === 'object'
    ? item.governance : {};
  const expanded = expandedIds.has(id) || id === targetId;
  const editing = editingIds.has(id);
  const busy = mutationsInFlight.has(id);
  const evidence = Array.isArray(item?.evidence) ? item.evidence : [];
  const sources = Array.isArray(item?.source_results) ? item.source_results : [];
  const sourceByResult = new Map(sources.map((source) => [String(source.result_asset_id || ''), source]));
  const support = Number(proposed.distinct_sessions || evidence.length || 0);
  const threshold = Number(proposed.min_support || (String(item?.rule_key || '').startsWith('feedback.recurring.') ? 3 : 2));
  const contradictions = Array.isArray(proposed.contradiction_examples)
    ? proposed.contradiction_examples.filter(Boolean) : [];
  const impact = item?.status === 'approved'
    ? `将介入之后的${view.scopeLabel}匹配任务，不改写旧任务。`
    : item?.status === 'disabled'
      ? '已停止介入新任务；旧任务与证据保持不变。'
      : '审核前不会介入任何生成任务。';
  const evidenceMarkup = evidence.length
    ? `<ol class="memory-evidence-list">${evidence.map((entry, index) => {
      const source = sourceByResult.get(String(entry.result_asset_id || '')) || {};
      const canOpen = Boolean((source.job_id || entry.job_id) && (source.result_asset_id || entry.result_asset_id));
      return `<li><div><strong>来源评审 ${index + 1}</strong><p>${escapeHtml(entry.reason || '未填写补充说明')}</p></div>${canOpen ? `<button type="button" data-memory-source data-job-id="${escapeHtml(source.job_id || entry.job_id)}" data-result-id="${escapeHtml(source.result_asset_id || entry.result_asset_id)}">打开精确结果</button>` : '<span>旧记录无结果游标</span>'}</li>`;
    }).join('')}</ol>`
    : '<p class="memory-detail-empty">没有可展开的原始评审文本。</p>';
  const contradictionMarkup = contradictions.length
    ? `<ul class="memory-contradiction-list">${contradictions.map((text) => `<li>${escapeHtml(text)}</li>`).join('')}</ul>`
    : '<p class="memory-detail-empty">当前没有相反反馈。</p>';
  const history = Array.isArray(governance.history) ? [...governance.history].reverse().slice(0, 6) : [];
  const historyMarkup = history.length
    ? `<ol class="memory-history-list">${history.map((entry) => `<li><strong>v${escapeHtml(entry.revision || '?')} · ${escapeHtml(memoryHistoryLabel(entry))}</strong><span>${escapeHtml(entry.directive || entry.label || '状态变更')}</span></li>`).join('')}</ol>`
    : '<p class="memory-detail-empty">当前是首个版本。</p>';
  const editMarkup = editing ? `<form class="memory-edit-form" data-memory-edit-form novalidate>
    <label for="memory-label-${key}">建议名称 <span>1–80 字</span></label>
    <input id="memory-label-${key}" name="label" maxlength="80" value="${escapeHtml(proposed.label || '')}" aria-describedby="memory-edit-error-${key}" />
    <label for="memory-directive-${key}">任务指令 <span>1–600 字</span></label>
    <textarea id="memory-directive-${key}" name="directive" maxlength="600" rows="4" aria-describedby="memory-edit-error-${key}">${escapeHtml(proposed.directive || '')}</textarea>
    <p class="memory-edit-error" id="memory-edit-error-${key}" role="alert"></p>
    <div><button class="memory-action memory-action--primary" type="submit">保存新版本</button><button class="memory-action" type="button" data-memory-edit-cancel>取消</button></div>
  </form>` : '';
  const detailMarkup = expanded ? `<div class="memory-item__details">
    ${editMarkup}
    <section><h4>原始反馈与来源结果</h4>${evidenceMarkup}</section>
    <section><h4>反例</h4>${contradictionMarkup}</section>
    <section><h4>版本记录</h4>${historyMarkup}</section>
  </div>` : '';
  const actions = view.actions.map((action) => `<button class="memory-action memory-action--${action.tone}" type="button" data-memory-action="${action.action}"${action.confirm ? ' data-memory-confirm="true"' : ''}${busy ? ' disabled' : ''}>${escapeHtml(action.label)}</button>`).join('');
  return `<article class="memory-item ${id === targetId ? 'is-target' : ''}" data-id="${escapeHtml(id)}" tabindex="-1" aria-busy="${busy ? 'true' : 'false'}">
    <div class="memory-item__head"><div class="memory-item__status"><span data-status="${escapeHtml(view.status)}">${escapeHtml(view.statusLabel)}</span><span>${escapeHtml(view.scopeLabel)}</span><span>v${view.revision}</span>${governance.postponed_at ? '<span>已标记稍后</span>' : ''}</div><strong>${Math.round(Number(item?.confidence || 0) * 100)}% 置信度</strong></div>
    <h3>${escapeHtml(proposed.label || item?.rule_key || '新偏好建议')}</h3>
    <p class="memory-item__directive">${escapeHtml(proposed.directive || JSON.stringify(proposed))}</p>
    <div class="memory-facts" aria-label="建议证据摘要"><span><b>${support}/${threshold}</b>独立会话 / 阈值</span><span><b>${Number(proposed.support_count || support)}</b>条支持证据</span><span><b>${Number(proposed.contradiction_count || 0)}</b>条反例</span></div>
    <p class="memory-impact"><strong>作用范围：</strong>${escapeHtml(impact)}</p>
    ${detailMarkup}
    <footer class="memory-item__footer"><button class="memory-details-toggle" type="button" data-memory-expand aria-expanded="${expanded ? 'true' : 'false'}">${expanded ? '收起证据' : '查看证据与版本'}</button><div class="memory-actions">${actions || '<span>当前无可用操作</span>'}</div></footer>
  </article>`;
}

export function createKnowledgeController({
  api,
  state,
  query,
  queryAll,
  escapeHtml,
  statusPanelHtml,
  memoryProjectionController,
  openMemorySourceResult,
  switchPage,
  toast,
  formatApiError,
  windowRef,
}) {
  const escapeSelector = (value) => windowRef.CSS.escape(String(value));

  function updateFilterControls() {
    const counts = memoryFilterCounts(state.memorySuggestions);
    queryAll('[data-memory-filter]').forEach((button) => {
      const active = button.dataset.memoryFilter === state.memoryFilter;
      button.classList.toggle('is-active', active);
      button.setAttribute('aria-selected', active ? 'true' : 'false');
      button.tabIndex = active ? 0 : -1;
    });
    Object.entries(counts).forEach(([filter, count]) => {
      const target = query(`[data-memory-filter-count="${filter}"]`);
      if (target) target.textContent = String(count);
    });
    const pendingNode = query('[data-memory-node="待审核建议"]');
    query('#memory-pending-count').textContent = String(counts.pending);
    pendingNode?.classList.toggle('memory-dna-node--pending', counts.pending > 0);
    if (pendingNode) {
      pendingNode.dataset.memoryDetail = `${counts.pending} 条建议等待人工确认；未批准前不参与未来生成。`;
      if (pendingNode.classList.contains('is-selected')) memoryProjectionController.selectNode(pendingNode);
    }
  }

  function cardMarkup(item, targetId = '') {
    return memorySuggestionCardMarkup(item, {
      targetId,
      expandedIds: state.memoryExpandedIds,
      editingIds: state.memoryEditingIds,
      mutationsInFlight: state.memoryMutationsInFlight,
      escapeHtml,
    });
  }

  function replaceSuggestion(next) {
    const index = state.memorySuggestions.findIndex((item) => String(item.id) === String(next?.id));
    if (index >= 0) state.memorySuggestions.splice(index, 1, next);
    else if (next?.id) state.memorySuggestions.unshift(next);
  }

  function render(targetId = '') {
    const list = query('#memory-list');
    updateFilterControls();
    const suggestions = memorySuggestionsForFilter(state.memorySuggestions, state.memoryFilter)
      .sort((left, right) => Number(Boolean(left.governance?.postponed_at)) - Number(Boolean(right.governance?.postponed_at)));
    if (!suggestions.length) {
      const [title, detail] = memoryQueueEmptyCopy(state.memoryFilter);
      list.innerHTML = statusPanelHtml('empty', { title, detail, fill: true });
      return;
    }
    list.innerHTML = suggestions.map((item) => cardMarkup(item, targetId)).join('');
    queryAll('[data-memory-source]', list).forEach((button) => button.addEventListener('click', () => {
      openMemorySourceResult({
        job_id: button.dataset.jobId,
        result_asset_id: button.dataset.resultId,
      }, button);
    }));
    queryAll('[data-memory-expand]', list).forEach((button) => button.addEventListener('click', () => {
      const id = String(button.closest('.memory-item')?.dataset.id || '');
      if (state.memoryExpandedIds.has(id)) {
        state.memoryExpandedIds.delete(id);
        state.memoryEditingIds.delete(id);
      } else state.memoryExpandedIds.add(id);
      render(id);
      windowRef.setTimeout(() => query(`.memory-item[data-id="${escapeSelector(id)}"]`)?.focus({ preventScroll: true }), 0);
    }));
    queryAll('[data-memory-action]', list).forEach((button) => button.addEventListener('click', () => {
      const id = String(button.closest('.memory-item')?.dataset.id || '');
      if (button.dataset.memoryAction === 'edit') {
        state.memoryExpandedIds.add(id);
        state.memoryEditingIds.add(id);
        render(id);
        const key = id.replace(/[^a-zA-Z0-9_-]/g, '-');
        windowRef.setTimeout(() => query(`#memory-label-${escapeSelector(key)}`)?.focus(), 0);
        return;
      }
      performAction(id, button.dataset.memoryAction, button);
    }));
    queryAll('[data-memory-edit-cancel]', list).forEach((button) => button.addEventListener('click', () => {
      const id = String(button.closest('.memory-item')?.dataset.id || '');
      state.memoryEditingIds.delete(id);
      render(id);
    }));
    queryAll('[data-memory-edit-form]', list).forEach((form) => form.addEventListener('submit', (event) => {
      event.preventDefault();
      const item = form.closest('.memory-item');
      const label = String(form.elements.label?.value || '').trim();
      const directive = String(form.elements.directive?.value || '').trim();
      const error = query('.memory-edit-error', form);
      if (!label || label.length > 80) {
        error.textContent = '建议名称需要 1–80 个字符。';
        form.elements.label?.focus();
        return;
      }
      if (!directive || directive.length > 600) {
        error.textContent = '任务指令需要 1–600 个字符。';
        form.elements.directive?.focus();
        return;
      }
      error.textContent = '';
      performAction(item.dataset.id, 'edit', query('button[type="submit"]', form), { label, directive });
    }));
    if (targetId) {
      windowRef.setTimeout(() => {
        const target = query(`.memory-item[data-id="${escapeSelector(targetId)}"]`, list);
        target?.scrollIntoView({ block: 'nearest', behavior: windowRef.matchMedia('(prefers-reduced-motion: reduce)').matches ? 'auto' : 'smooth' });
        target?.focus({ preventScroll: true });
      }, 0);
    }
  }

  async function performAction(id, action, button, fields = {}) {
    const item = state.memorySuggestions.find((candidate) => String(candidate.id) === String(id));
    const card = button?.closest('.memory-item');
    if (!item || state.memoryMutationsInFlight.has(id) || card?.getAttribute('aria-busy') === 'true') return;
    if (button?.dataset.memoryConfirm === 'true') {
      const confirmed = windowRef.confirm(action === 'disable'
        ? '停用后，这条规则不再介入新任务，但可重新启用。继续吗？'
        : '拒绝后会保留证据和历史，也可重新审核。继续吗？');
      if (!confirmed) return;
    }
    card?.setAttribute('aria-busy', 'true');
    state.memoryMutationsInFlight.add(id);
    queryAll('button, input, textarea', card || windowRef.document.createElement('div')).forEach((control) => { control.disabled = true; });
    try {
      const updated = await api.governMemorySuggestion(id, {
        action,
        expected_revision: Number(item.governance?.revision || 1),
        ...fields,
      });
      replaceSuggestion(updated);
      state.memoryEditingIds.delete(id);
      state.memoryMutationsInFlight.delete(id);
      const messages = {
        edit: '已保存新版本，证据未改变', approve: '已采用；从下一个匹配的新任务开始生效',
        reject: '已拒绝并保留在历史中', postpone: '已标记稍后处理，建议仍保留在待审核队列',
        disable: '已停用；不再介入新任务', enable: '已重新启用', reopen: '已恢复为待审核',
        undo: '已撤销上次变更', redo: '已恢复被撤销的变更',
      };
      render('');
      const canUndo = Array.isArray(updated.governance?.available_actions)
        && updated.governance.available_actions.includes('undo') && action !== 'undo';
      toast(messages[action] || '知识建议已更新', 'success', canUndo ? 5200 : 2200, canUndo ? {
        label: '撤销',
        onClick: () => performAction(id, 'undo', null),
      } : null);
    } catch (error) {
      if (Number(error?.status) === 409 && error?.detail?.code === 'MEMORY_REVISION_CONFLICT') {
        state.memoryMutationsInFlight.delete(id);
        if (error.detail.current) replaceSuggestion(error.detail.current);
        render(id);
        toast('这条建议已在别处更新，已载入最新版本，请重新确认', 'error', 6200);
      } else {
        state.memoryMutationsInFlight.delete(id);
        card?.setAttribute('aria-busy', 'false');
        queryAll('button, input, textarea', card || windowRef.document.createElement('div')).forEach((control) => { control.disabled = false; });
        toast(`知识建议更新失败：${formatApiError(error, '本地账本暂不可写')}`, 'error', 6200);
      }
    }
  }

  async function load(targetSuggestionId = state.memoryTargetSuggestionId) {
    const list = query('#memory-list');
    list.innerHTML = statusPanelHtml('loading', { title: '正在读取知识建议', detail: '正在核对证据、版本与当前生效状态。', fill: true });
    try {
      const targetId = String(targetSuggestionId || '').trim();
      const [ledger, allSuggestions, knowledgeStatus] = await Promise.all([
        api.getLedgerStatus(),
        api.getMemorySuggestions('all'),
        api.getKnowledgeStatus().catch(() => state.knowledgeStatus || {}),
      ]);
      const suggestions = [...allSuggestions];
      if (targetId && !suggestions.some((item) => String(item.id) === targetId)) {
        const target = await api.getMemorySuggestion(targetId).catch(() => null);
        if (target) suggestions.unshift(target);
      }
      const target = suggestions.find((item) => String(item.id) === targetId);
      if (target) {
        state.memoryFilter = ['pending', 'approved', 'disabled'].includes(target.status)
          ? target.status : 'all';
        state.memoryExpandedIds.add(targetId);
      }
      state.memorySuggestions = suggestions;
      state.knowledgeStatus = knowledgeStatus;
      const pendingSuggestions = suggestions.filter((item) => item.status === 'pending');
      memoryProjectionController.render(ledger, pendingSuggestions, knowledgeStatus);
      render(targetId);
      state.memoryTargetSuggestionId = '';
    } catch (error) {
      list.innerHTML = statusPanelHtml('offline', {
        title: '审核队列暂时离线',
        detail: formatApiError(error, '反馈证据与待审核建议暂不可用'),
        fill: true,
        action: { label: '重新读取', attribute: 'data-memory-status-action', value: 'retry' },
      });
      query('[data-memory-status-action="retry"]', list)?.addEventListener('click', () => load());
    }
  }

  function openSuggestion(suggestionId) {
    const target = String(suggestionId || '').trim();
    if (!target) return;
    state.memoryTargetSuggestionId = target;
    state.memoryExpandedIds.add(target);
    switchPage('memory');
  }

  function bind() {
    query('#btn-refresh-memory').addEventListener('click', () => load());
    const memoryFilters = queryAll('[data-memory-filter]');
    memoryFilters.forEach((button, index) => {
      button.addEventListener('click', () => {
        state.memoryFilter = button.dataset.memoryFilter || 'pending';
        state.memoryTargetSuggestionId = '';
        render('');
      });
      button.addEventListener('keydown', (event) => {
        const targets = { ArrowRight: index + 1, ArrowLeft: index - 1, Home: 0, End: memoryFilters.length - 1 };
        if (!(event.key in targets)) return;
        event.preventDefault();
        const targetIndex = event.key === 'ArrowRight' || event.key === 'ArrowLeft'
          ? (targets[event.key] + memoryFilters.length) % memoryFilters.length
          : targets[event.key];
        memoryFilters[targetIndex]?.click();
        memoryFilters[targetIndex]?.focus({ preventScroll: true });
      });
    });
  }

  return { bind, load, openSuggestion, performAction, render };
}
