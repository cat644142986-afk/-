import { memoryProjectionState } from './memory-projection.js';

export function memoryProjectionDetails({
  documents = 0,
  sessions = 0,
  feedback = 0,
  pending = 0,
  knowledgeRules = 0,
} = {}) {
  return {
    设计判断: `${documents} 份正式知识、${sessions} 个创作现场和 ${feedback} 条反馈共同构成当前投影。`,
    正式知识: `唯一主库当前只读加载 ${documents} 份文档、${knowledgeRules} 条规则；正式页面不会被后台修改。`,
    创作现场: `${sessions} 个会话保留各自素材、参数、知识引用与结果版本。`,
    终稿反馈: `${feedback} 条有效反馈作为学习证据，不会直接覆盖正式知识。`,
    待审核建议: `${pending} 条建议等待人工确认；未批准前不参与未来生成。`,
  };
}

export function createMemoryProjectionController({
  state,
  query,
  queryAll,
  getResultItems,
  toast,
  windowRef,
}) {
  function selectNode(node) {
    if (!node) return;
    queryAll('[data-memory-node]').forEach((item) => {
      item.classList.toggle('is-selected', item === node);
    });
    const caption = query('#memory-dna-caption');
    query('strong', caption).textContent = node.dataset.memoryNode || '设计判断';
    query('small', caption).textContent = node.dataset.memoryDetail
      || '所有关系都来自真实账本和唯一知识库。';
  }

  function replayMotion() {
    const panel = query('#memory-dna-panel');
    const trace = query('#memory-trace');
    [panel, trace].forEach((element) => {
      element.classList.remove('is-replaying');
      void element.offsetWidth;
      element.classList.add('is-replaying');
    });
    windowRef.setTimeout(() => {
      panel.classList.remove('is-replaying');
      trace.classList.remove('is-replaying');
    }, windowRef.matchMedia('(prefers-reduced-motion: reduce)').matches ? 0 : 900);
    toast('正在回放：正式知识 → 创作现场 → 终稿反馈 → 待审核建议', 'success');
  }

  function render(ledger, suggestions, knowledgeStatus) {
    const counts = ledger?.counts || {};
    const pendingSuggestions = Array.isArray(suggestions) ? suggestions : [];
    const sessions = Number(counts.sessions || 0);
    const feedback = Number(counts.feedback || 0);
    const pending = Number(ledger?.pending_memory || pendingSuggestions.length || 0);
    const documents = Number(knowledgeStatus?.document_count || 0);
    const knowledgeRules = Number(knowledgeStatus?.rule_count || 0);
    const bundle = state.knowledgeBundle || {};
    const currentItem = getResultItems()[state.viewerIndex] || null;
    const currentProjection = memoryProjectionState({
      currentTaskId: state.currentTaskId,
      knowledgeBundle: bundle,
      reviews: state.resultReviews,
      resultAssetId: currentItem?.asset_id || '',
    });
    const sources = Array.isArray(bundle.sources) ? bundle.sources : [];
    const executionRules = (Array.isArray(bundle.positive_rules) ? bundle.positive_rules.length : 0)
      + (Array.isArray(bundle.negative_rules) ? bundle.negative_rules.length : 0);
    const brief = bundle.creative_brief || {};
    const intent = brief.objective || brief.user_request || query('#brief-input').value.trim();
    const memorySources = sources.filter((source) => source.relative_path === '记忆反馈/已批准');
    const appliedRuleTexts = [
      ...(Array.isArray(bundle.positive_rules) ? bundle.positive_rules : [])
        .map((rule) => rule.text || rule),
      ...(Array.isArray(bundle.negative_rules) ? bundle.negative_rules : [])
        .map((rule) => rule.text || rule),
      ...(Array.isArray(bundle.intent_lock_rules) ? bundle.intent_lock_rules : []),
    ].filter(Boolean);

    query('#memory-session-count').textContent = sessions;
    query('#memory-feedback-count').textContent = feedback;
    query('#memory-pending-count').textContent = pending;
    const pendingNode = query('[data-memory-node="待审核建议"]');
    pendingNode?.classList.toggle('memory-dna-node--pending', pending > 0);
    query('#memory-rule-count').textContent = knowledgeStatus?.available === false
      ? '主库暂不可用'
      : `${documents} 份文档 · ${knowledgeRules} 条规则`;
    query('#memory-core-summary').textContent = `${documents} 份正式知识 · ${sessions} 个现场`;
    query('#memory-trace-title').textContent = currentProjection.title;
    query('#btn-open-memory-trace').disabled = !currentProjection.hasTask;
    query('#memory-trace-intent').textContent = currentProjection.hasTask
      ? intent || '当前任务尚未输入创作目标'
      : '当前未选择任务；全局现场数量不会被伪装成当前步骤';
    query('#memory-trace-knowledge').textContent = currentProjection.hasTask
      ? sources.length
        ? memorySources.length
          ? `本次引用 ${sources.length} 份依据，其中 ${memorySources.length} 条是你已批准的反馈`
          : `本次引用 ${sources.length} 份正式知识`
        : '当前任务尚未编译知识来源'
      : `${documents} 份正式知识可用；选择任务后显示实际引用`;
    query('#memory-trace-rules').textContent = currentProjection.hasTask
      ? executionRules
        ? `已应用 ${executionRules} 条可检查执行规则：${appliedRuleTexts.slice(0, 3).join('；')}${appliedRuleTexts.length > 3 ? '…' : ''}`
        : '只采用已批准规则，不使用待审核建议'
      : '未选择任务，不展示最后一次前端知识包';
    query('#memory-trace-feedback-title').textContent = currentProjection.hasTask
      ? currentProjection.title
      : '当前未选择任务';
    query('#memory-trace-feedback').textContent = currentProjection.detail;

    const traceSteps = queryAll('#memory-trace li');
    if (!currentProjection.hasTask) {
      traceSteps.forEach((step) => step.classList.remove('is-complete', 'is-current'));
    } else {
      const done = [Boolean(intent), sources.length > 0, executionRules > 0, currentProjection.reviewed];
      const currentIndex = done.findIndex((value) => !value);
      traceSteps.forEach((step, index) => {
        step.classList.toggle('is-complete', done[index]);
        step.classList.toggle('is-current', currentIndex === index);
      });
    }

    const details = memoryProjectionDetails({
      documents,
      sessions,
      feedback,
      pending,
      knowledgeRules,
    });
    queryAll('[data-memory-node]').forEach((node) => {
      node.dataset.memoryDetail = details[node.dataset.memoryNode] || '';
    });
    selectNode(query('.memory-dna-node.is-selected') || query('[data-memory-node]'));
  }

  function bind() {
    query('#btn-replay-memory').addEventListener('click', replayMotion);
    queryAll('[data-memory-node]').forEach((node) => {
      node.addEventListener('click', () => selectNode(node));
    });
  }

  return { bind, render, replayMotion, selectNode };
}
