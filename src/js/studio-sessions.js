export function formatStudioTime(value) {
  if (!value) return '—';
  const date = new Date(value);
  return Number.isNaN(date.getTime())
    ? String(value)
    : date.toLocaleString('zh-CN', {
      month: '2-digit',
      day: '2-digit',
      hour: '2-digit',
      minute: '2-digit',
    });
}

export function sessionProjectName(session) {
  return String(session?.project_name || '').trim() || '未归类项目';
}

export function sessionStatusCopy(status) {
  return ({
    completed: '已完成',
    partial: '部分完成',
    processing: '处理中',
    draft: '草稿',
    failed: '需要处理',
    canceled: '已取消',
  })[status] || '已保存';
}

export function sessionActionCopy(session) {
  if (['failed', 'partial'].includes(session?.status)) return '处理';
  if (session?.status === 'completed') return '查看';
  return '继续';
}

export function createSessionsController({
  api,
  state,
  query,
  queryAll,
  modeConfig,
  resultIdsForJob,
  jobCounts,
  openJobResults,
  openJobWorkspace,
  applyTaskKnowledgeBundle,
  knowledgeBundleFromEvidence,
  openDrawer,
  toast,
  formatApiError,
  statusPanelHtml,
  escapeHtml,
}) {
  function sessionJob(sessionId) {
    const matches = state.jobs.filter((job) => (
      String(job.session_id || '') === String(sessionId || '')
    ));
    return matches.find((job) => resultIdsForJob(job).length) || matches[0] || null;
  }

  async function open(sessionId) {
    try {
      const session = await api.getSession(sessionId);
      const job = sessionJob(sessionId);
      if (job) {
        if (resultIdsForJob(job).length) await openJobResults(job.id);
        else await openJobWorkspace(job);
        return;
      }
      const generations = session.generations || [];
      const generation = generations[generations.length - 1];
      state.currentSessionId = session.id || '';
      applyTaskKnowledgeBundle(knowledgeBundleFromEvidence({
        brief: session.brief || {},
        generation,
      }));
      openDrawer('intelligence');
      toast('这条旧会话没有任务快照，已打开可追溯证据');
    } catch (error) {
      toast(`无法恢复会话：${formatApiError(error, '本地创作账本暂不可用')}`, 'error');
    }
  }

  function renderTimeline(session) {
    const steps = queryAll('#history-timeline span');
    const hasAssets = Number(session?.asset_count || 0) > 0;
    const hasDirection = Boolean(session?.brief?.objective || session?.brief?.user_request);
    const hasReview = Number(session?.feedback_count || 0) > 0;
    const hasKnowledge = state.sessionPendingKnowledgeCount > 0;
    const states = [hasAssets, hasDirection, hasReview, hasKnowledge];
    const current = states.findIndex((done) => !done);
    steps.forEach((step, index) => {
      step.classList.toggle('is-done', states[index]);
      step.classList.toggle(
        'is-current',
        index === current || (current < 0 && index === steps.length - 1),
      );
    });
    query('#history-memory-copy').textContent = session
      ? `${modeConfig[session.mode]?.label || '创作工作流'}已保留素材、参数、任务和评审证据。`
      : '素材、参数、任务和评审证据相互独立，随时可以继续。';
  }

  function render() {
    const grid = query('#history-grid');
    const recentCard = query('#sessions-recent-card');
    const toggle = query('#btn-toggle-history');
    const filter = state.sessionProjectFilter;
    const sessions = filter === 'all'
      ? state.sessions
      : state.sessions.filter((session) => sessionProjectName(session) === filter);
    const projectTitle = filter === 'all' ? '全部创作项目' : filter;
    query('#history-project-title').textContent = projectTitle;
    query('#history-session-count').textContent = String(sessions.length);
    query('#history-result-count').textContent = String(sessions.reduce(
      (sum, session) => sum + Number(session.generation_count || 0),
      0,
    ));
    query('#history-pending-count').textContent = String(state.sessionPendingKnowledgeCount);
    const completed = sessions.filter((session) => session.status === 'completed').length;
    query('#history-complete-rate').textContent = sessions.length
      ? `${Math.round((completed / sessions.length) * 100)}%`
      : '0%';
    renderTimeline(sessions[0] || null);
    const canExpand = sessions.length > 6;
    if (!canExpand) state.sessionShowAll = false;
    recentCard.classList.toggle('is-expanded', state.sessionShowAll);
    toggle.hidden = !canExpand;
    toggle.textContent = state.sessionShowAll ? '收起列表' : `查看全部 ${sessions.length} 个`;
    toggle.setAttribute('aria-expanded', String(state.sessionShowAll));
    if (!sessions.length) {
      grid.innerHTML = statusPanelHtml('empty', {
        title: '还没有创作现场',
        detail: '完成第一项任务后，现场会自动保存在这里。',
        fill: true,
      });
      return;
    }
    const visibleSessions = state.sessionShowAll ? sessions : sessions.slice(0, 6);
    grid.innerHTML = visibleSessions.map((session) => {
      const job = sessionJob(session.id);
      const counts = job ? jobCounts(job) : null;
      const progress = counts
        ? `${counts.completed}/${counts.total} 项完成`
        : `${Number(session.generation_count || 0)} 个版本`;
      const summary = `${modeConfig[session.mode]?.label || '创作任务'} · ${progress} · ${sessionStatusCopy(session.status)}`;
      return `<button class="session-card" type="button" data-session-id="${escapeHtml(session.id)}"><span class="session-card__color" aria-hidden="true"></span><span class="session-card__copy"><strong>${escapeHtml(session.title || sessionProjectName(session))}</strong><small>${escapeHtml(summary)} · ${escapeHtml(formatStudioTime(session.updated_at))}</small></span><span class="session-card__action">${sessionActionCopy(session)}</span><span class="session-card__chevron" aria-hidden="true">›</span></button>`;
    }).join('');
    queryAll('.session-card', grid).forEach((card) => {
      card.addEventListener('click', () => open(card.dataset.sessionId));
    });
  }

  async function load() {
    const grid = query('#history-grid');
    grid.innerHTML = statusPanelHtml('loading', {
      title: '正在读取创作账本',
      detail: '正在恢复最近项目与任务现场。',
      fill: true,
    });
    try {
      const [sessions, suggestions] = await Promise.all([
        api.getSessions(60),
        api.getMemorySuggestions('pending').catch(() => []),
      ]);
      state.sessions = Array.isArray(sessions) ? sessions : [];
      state.sessionPendingKnowledgeCount = Array.isArray(suggestions) ? suggestions.length : 0;
      const projectFilter = query('#history-project-filter');
      const projects = [...new Set(state.sessions.map(sessionProjectName))];
      const available = state.sessionProjectFilter === 'all'
        || projects.includes(state.sessionProjectFilter);
      if (!available) state.sessionProjectFilter = 'all';
      projectFilter.innerHTML = `<option value="all">全部项目</option>${projects.map((name) => `<option value="${escapeHtml(name)}">${escapeHtml(name)}</option>`).join('')}`;
      projectFilter.value = state.sessionProjectFilter;
      render();
    } catch (error) {
      grid.innerHTML = statusPanelHtml('offline', {
        title: '创作账本暂时离线',
        detail: formatApiError(error, '本地创作账本暂不可用'),
        fill: true,
        action: { label: '重新读取', attribute: 'data-history-status-action', value: 'retry' },
      });
      query('[data-history-status-action="retry"]', grid)?.addEventListener('click', load);
    }
  }

  function bind() {
    query('#btn-refresh-history').addEventListener('click', load);
    query('#history-project-filter').addEventListener('change', (event) => {
      state.sessionProjectFilter = event.target.value || 'all';
      state.sessionShowAll = false;
      render();
    });
    query('#btn-toggle-history').addEventListener('click', () => {
      state.sessionShowAll = !state.sessionShowAll;
      render();
    });
  }

  return { bind, load, open, render };
}
