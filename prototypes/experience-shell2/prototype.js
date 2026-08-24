const motionReduced = window.matchMedia('(prefers-reduced-motion: reduce)');

document.querySelectorAll('button svg').forEach((icon) => icon.setAttribute('aria-hidden', 'true'));

const pageMeta = {
  studio: {
    eyebrow: 'CREATIVE STUDIO',
    title: '下午好，Miyo',
    subtitle: '继续完成牛油果饮品的主图方向',
  },
  sessions: {
    eyebrow: 'PROJECT SESSIONS',
    title: '会话与项目',
    subtitle: '每个工作流都保留自己的素材、任务和评审现场',
  },
  growth: {
    eyebrow: 'DESIGN INTELLIGENCE',
    title: '成长与知识',
    subtitle: '看见知识怎样参与，也决定系统可以学到什么',
  },
  settings: {
    eyebrow: 'SYSTEM & KNOWLEDGE',
    title: '应用设置',
    subtitle: '管理模型、唯一知识库与成品交付位置',
  },
};

const title = document.querySelector('#pageTitle');
const eyebrow = document.querySelector('#pageEyebrow');
const subtitle = document.querySelector('#pageSubtitle');
const views = [...document.querySelectorAll('[data-view]')];
const navButtons = [...document.querySelectorAll('[data-view-target]')];
const railButtons = [...document.querySelectorAll('.rail-button[data-view-target]')];
const taskDrawer = document.querySelector('#taskDrawer');
const explainDrawer = document.querySelector('#explainDrawer');
const reviewOverlay = document.querySelector('#reviewOverlay');
const scrim = document.querySelector('#scrim');
const toast = document.querySelector('#toast');
const liveRegion = document.querySelector('#liveRegion');
const simulateButton = document.querySelector('#simulateButton');
const knowledgeCompact = document.querySelector('#knowledgeCompact');
const taskGlanceTitle = document.querySelector('#taskGlanceTitle');
const taskGlancePercent = document.querySelector('#taskGlancePercent');
const taskProgressBar = document.querySelector('#taskProgressBar');

let currentView = 'studio';
let lastFocused = null;
let toastTimer = null;
let generationTimers = [];
let generationRunning = false;

function announce(message) {
  liveRegion.textContent = '';
  window.requestAnimationFrame(() => {
    liveRegion.textContent = message;
  });
}

function showToast(message) {
  window.clearTimeout(toastTimer);
  toast.textContent = message;
  toast.classList.add('is-visible');
  announce(message);
  toastTimer = window.setTimeout(() => toast.classList.remove('is-visible'), 2600);
}

function restartEntryMotion(element) {
  if (!element || motionReduced.matches) return;
  element.classList.remove('is-entering');
  void element.offsetWidth;
  element.classList.add('is-entering');
}

function switchView(nextView) {
  if (!pageMeta[nextView]) return;

  currentView = nextView;
  views.forEach((view) => {
    const active = view.dataset.view === nextView;
    view.hidden = !active;
    view.classList.toggle('is-active', active);
    view.classList.remove('is-entering');
    if (active) restartEntryMotion(view);
  });

  railButtons.forEach((button) => {
    const active = button.dataset.viewTarget === nextView;
    button.classList.toggle('is-active', active);
    if (active) button.setAttribute('aria-current', 'page');
    else button.removeAttribute('aria-current');
  });

  const meta = pageMeta[nextView];
  eyebrow.textContent = meta.eyebrow;
  title.textContent = meta.title;
  subtitle.textContent = meta.subtitle;

  if (nextView === 'growth') {
    restartEntryMotion(document.querySelector('.dna-panel'));
  }

  announce(`已进入${meta.title}`);
}

navButtons.forEach((button) => {
  button.addEventListener('click', () => {
    closeDrawer(taskDrawer, false);
    closeDrawer(explainDrawer, false);
    switchView(button.dataset.viewTarget);
  });
});

function updateScrim() {
  const shouldShow = taskDrawer.classList.contains('is-open') || explainDrawer.classList.contains('is-open');
  if (shouldShow) {
    scrim.hidden = false;
    window.requestAnimationFrame(() => scrim.classList.add('is-visible'));
  } else {
    scrim.classList.remove('is-visible');
    window.setTimeout(() => {
      if (!taskDrawer.classList.contains('is-open') && !explainDrawer.classList.contains('is-open')) scrim.hidden = true;
    }, motionReduced.matches ? 0 : 300);
  }
}

function openDrawer(drawer, trigger) {
  const other = drawer === taskDrawer ? explainDrawer : taskDrawer;
  closeDrawer(other, false);
  lastFocused = trigger || document.activeElement;
  drawer.classList.add('is-open');
  drawer.setAttribute('aria-hidden', 'false');
  document.querySelectorAll(`[data-action="${drawer === taskDrawer ? 'open-dock' : 'open-explain'}"]`).forEach((button) => button.setAttribute('aria-expanded', 'true'));
  updateScrim();
  drawer.querySelector('button')?.focus({ preventScroll: true });
}

function closeDrawer(drawer, restoreFocus = true) {
  if (!drawer.classList.contains('is-open')) return;
  drawer.classList.remove('is-open');
  drawer.setAttribute('aria-hidden', 'true');
  document.querySelectorAll(`[data-action="${drawer === taskDrawer ? 'open-dock' : 'open-explain'}"]`).forEach((button) => button.setAttribute('aria-expanded', 'false'));
  updateScrim();
  if (restoreFocus && lastFocused instanceof HTMLElement) lastFocused.focus({ preventScroll: true });
}

function openReview(trigger) {
  closeDrawer(taskDrawer, false);
  closeDrawer(explainDrawer, false);
  lastFocused = trigger || document.activeElement;
  reviewOverlay.classList.add('is-open');
  reviewOverlay.setAttribute('aria-hidden', 'false');
  reviewOverlay.querySelector('.close-button')?.focus({ preventScroll: true });
}

function closeReview(restoreFocus = true) {
  if (!reviewOverlay.classList.contains('is-open')) return;
  reviewOverlay.classList.remove('is-open');
  reviewOverlay.setAttribute('aria-hidden', 'true');
  if (restoreFocus && lastFocused instanceof HTMLElement) lastFocused.focus({ preventScroll: true });
}

document.addEventListener('click', (event) => {
  const actionButton = event.target.closest('[data-action]');
  if (!actionButton) return;
  const action = actionButton.dataset.action;
  if (action === 'open-dock') openDrawer(taskDrawer, actionButton);
  if (action === 'close-dock') closeDrawer(taskDrawer);
  if (action === 'open-explain') openDrawer(explainDrawer, actionButton);
  if (action === 'close-explain') closeDrawer(explainDrawer);
  if (action === 'open-review') openReview(actionButton);
  if (action === 'close-review') closeReview();
  if (action === 'prototype-exit') showToast('这是视觉原型，不会关闭正在运行的 Product Atelier');
  if (action === 'simulate-generation') toggleGenerationDemo();
  if (action === 'retry-failed') retryFailedItem(actionButton);
  if (action === 'replay-knowledge') replayKnowledgeMotion();
});

scrim.addEventListener('click', () => {
  if (explainDrawer.classList.contains('is-open')) closeDrawer(explainDrawer);
  else closeDrawer(taskDrawer);
});

function clearGenerationTimers() {
  generationTimers.forEach((timer) => window.clearTimeout(timer));
  generationTimers = [];
}

function setGenerationState(state) {
  taskGlanceTitle.textContent = state.title;
  taskGlancePercent.textContent = `${state.percent}%`;
  taskProgressBar.style.setProperty('--progress', String(state.percent / 100));

  if (state.knowledge) {
    knowledgeCompact.classList.remove('is-compiling');
    void knowledgeCompact.offsetWidth;
    knowledgeCompact.classList.add('is-compiling');
    document.querySelector('#knowledgeCompactTitle').textContent = '已采用 3 条批准规则';
    document.querySelector('#knowledgeCompactDetail').textContent = '忽略 1 条冲突规则 · 可查看原因';
  }

  announce(`${state.title}，${state.percent}%`);
}

function finishGenerationDemo() {
  generationRunning = false;
  clearGenerationTimers();
  simulateButton.querySelector('span').textContent = '再次演示生成';
  simulateButton.querySelector('small').textContent = '本次演示没有调用真实模型';
  showToast('演示完成：结果已进入评审，知识建议仍需你确认');
}

function toggleGenerationDemo() {
  if (generationRunning) {
    generationRunning = false;
    clearGenerationTimers();
    simulateButton.querySelector('span').textContent = '继续演示生成';
    simulateButton.querySelector('small').textContent = '已安全停止，不改变任务事实';
    showToast('已停止动效演示，当前状态保持可读');
    return;
  }

  generationRunning = true;
  simulateButton.querySelector('span').textContent = '停止演示';
  simulateButton.querySelector('small').textContent = '成功项目不会重复执行';

  const states = [
    { title: '正在理解商品与画面目标', percent: 18 },
    { title: '正在采用已批准知识', percent: 38, knowledge: true },
    { title: '正在生成商业画面', percent: 68 },
    { title: '正在核对品牌一致性', percent: 86 },
    { title: '已完成，等待结果评审', percent: 100, final: true },
  ];

  if (motionReduced.matches) {
    setGenerationState(states.at(-1));
    finishGenerationDemo();
    return;
  }

  states.forEach((state, index) => {
    const timer = window.setTimeout(() => {
      if (!generationRunning) return;
      setGenerationState(state);
      if (state.final) finishGenerationDemo();
    }, index * 850);
    generationTimers.push(timer);
  });
}

function retryFailedItem(button) {
  if (button.disabled) return;
  button.disabled = true;
  button.textContent = '正在重试 1 项';
  const message = document.querySelector('#partialJobMessage');
  const count = document.querySelector('#partialJobCount');
  message.innerHTML = '<i class="dot dot--active"></i>只重试可恢复失败项；8 个成功项保持锁定';
  announce('正在单独重试一个可恢复失败项，已经成功的八项不会重复执行');

  window.setTimeout(() => {
    count.textContent = '9/10';
    message.innerHTML = '<i class="dot dot--warning"></i>重试项已完成 · 1 项因源文件损坏永久失败';
    button.textContent = '失败原因';
    button.disabled = false;
    button.dataset.action = 'open-explain';
    showToast('失败项已单独重试完成；永久失败原因继续保留');
  }, motionReduced.matches ? 0 : 1300);
}

function replayKnowledgeMotion() {
  const panel = document.querySelector('.dna-panel');
  restartEntryMotion(panel);
  document.querySelectorAll('.knowledge-trace li').forEach((item, index) => {
    item.style.opacity = '0';
    item.style.transform = 'translateY(8px)';
    window.setTimeout(() => {
      item.style.opacity = '1';
      item.style.transform = 'translateY(0)';
      item.style.transition = motionReduced.matches ? 'none' : 'opacity 220ms ease, transform 220ms ease';
    }, motionReduced.matches ? 0 : index * 130);
  });
  showToast('正在回放：意图 → 已批准规则 → 设计决策 → 终稿反馈');
}

document.querySelectorAll('.workflow-switch button').forEach((button) => {
  button.addEventListener('click', () => {
    document.querySelectorAll('.workflow-switch button').forEach((item) => {
      const selected = item === button;
      item.classList.toggle('is-selected', selected);
      item.setAttribute('aria-selected', String(selected));
    });
    showToast(`已切换到${button.textContent}工作流；其他现场仍在后台保留`);
  });
});

document.querySelectorAll('.dna-node').forEach((node) => {
  node.addEventListener('click', () => {
    document.querySelectorAll('.dna-node').forEach((item) => item.classList.toggle('is-selected', item === node));
    const caption = document.querySelector('#dnaCaption');
    caption.querySelector('strong').textContent = node.dataset.dna;
    caption.querySelector('small').textContent = node.querySelector('small').textContent;
    announce(`已选择${node.dataset.dna}`);
  });
});

document.querySelector('#knowledgeReviewList').addEventListener('click', (event) => {
  const button = event.target.closest('[data-knowledge-action]');
  if (!button) return;
  const card = button.closest('[data-knowledge-card]');
  const action = button.dataset.knowledgeAction;
  if (action === 'inspect') {
    openDrawer(explainDrawer, button);
    return;
  }

  const approved = action === 'approve';
  card.classList.add('is-leaving');
  const finish = () => {
    card.hidden = true;
    const visibleCards = [...document.querySelectorAll('[data-knowledge-card]')].filter((item) => !item.hidden).length;
    document.querySelector('#pendingCount').textContent = String(visibleCards);
    showToast(approved ? '你已批准这条规则；它将从下一个新任务开始生效' : '建议已拒绝并保留在审计历史中');
  };
  window.setTimeout(finish, motionReduced.matches ? 0 : 300);
});

const reviewChoiceLabels = {
  use: '可以直接使用',
  adjust: '需要小幅调整',
  wrong: '整体方向不对',
};

document.querySelectorAll('[data-review-choice]').forEach((button) => {
  button.addEventListener('click', () => {
    document.querySelectorAll('[data-review-choice]').forEach((item) => item.classList.toggle('is-selected', item === button));
    const scope = document.querySelector('#learningScope');
    scope.hidden = false;
    document.querySelector('#learningScopeTitle').textContent = `已选择：${reviewChoiceLabels[button.dataset.reviewChoice]}`;
    announce(`已选择${reviewChoiceLabels[button.dataset.reviewChoice]}，请选择反馈作用范围`);
  });
});

document.querySelectorAll('[data-scope]').forEach((button) => {
  button.addEventListener('click', () => {
    const suggestion = button.dataset.scope === 'suggest';
    showToast(suggestion ? '已形成 1 条待审核建议，尚未修改正式知识' : '本次判断已记录，不会用于未来生成');
  });
});

document.querySelectorAll('.version-rail button').forEach((button) => {
  button.addEventListener('click', () => {
    document.querySelectorAll('.version-rail button').forEach((item) => item.classList.toggle('is-selected', item === button));
  });
});

function getTopLayer() {
  if (explainDrawer.classList.contains('is-open')) return explainDrawer;
  if (reviewOverlay.classList.contains('is-open')) return reviewOverlay;
  if (taskDrawer.classList.contains('is-open')) return taskDrawer;
  return null;
}

document.addEventListener('keydown', (event) => {
  const layer = getTopLayer();
  if (event.key === 'Escape' && layer) {
    event.preventDefault();
    if (layer === reviewOverlay) closeReview();
    else closeDrawer(layer);
    return;
  }

  if (event.key !== 'Tab' || !layer) return;
  const focusable = [...layer.querySelectorAll('button:not([disabled]), input:not([disabled]), select:not([disabled]), [tabindex="0"]')].filter((item) => !item.hidden);
  if (!focusable.length) return;
  const first = focusable[0];
  const last = focusable.at(-1);
  if (event.shiftKey && document.activeElement === first) {
    event.preventDefault();
    last.focus();
  } else if (!event.shiftKey && document.activeElement === last) {
    event.preventDefault();
    first.focus();
  }
});

switchView(currentView);
