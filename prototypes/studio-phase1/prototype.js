const modes = {
  single: { title: '产品素材', badge: '单产品', count: '12 张 · 已选 1 张', header: '单产品草稿已恢复，可继续精修', action: '开始精修 1 张', brief: true },
  batch: { title: '产品素材', badge: '多文件', count: '4 张 · 已选 4 张', header: '多文件任务进行中，其他草稿已保留', action: '开始处理 4 张', brief: true },
  group: { title: '合照素材', badge: '合照拆分', count: '3 张 · 已选 1 张', header: '合照识别框已保留，后台任务继续运行', action: '识别并拆分对象', brief: true },
  cutout: { title: '抠图素材', badge: '批量抠图', count: '10 张 · 已选 10 张', header: '抠图任务 8/10，返回位置已恢复', action: '继续快速去背景', brief: false },
};

const $ = (selector, root = document) => root.querySelector(selector);
const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];
let toastTimer;

function showToast(message) {
  const toast = $('#prototype-toast');
  toast.textContent = message;
  toast.classList.add('is-visible');
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => toast.classList.remove('is-visible'), 2200);
}

function setMode(modeKey) {
  const mode = modes[modeKey];
  if (!mode) return;
  $$('.workflow-grid button').forEach((button) => button.classList.toggle('is-active', button.dataset.mode === modeKey));
  $('#workspace-title').textContent = mode.title;
  $('#mode-badge').textContent = mode.badge;
  $('#asset-count').textContent = mode.count;
  $('#header-status').textContent = mode.header;
  $('#primary-action span').textContent = mode.action;
  $('#brief-field').hidden = !mode.brief;
  showToast(`${mode.badge}现场已恢复，其他工作流没有被清空`);
}

$$('.workflow-grid button').forEach((button) => button.addEventListener('click', () => setMode(button.dataset.mode)));

$$('.rail-action[data-page]').forEach((button) => button.addEventListener('click', () => {
  if (button.dataset.page !== 'studio') {
    showToast(`${button.querySelector('span').textContent}将在对应阶段实现；当前原型聚焦创作主流程`);
    return;
  }
  $$('.rail-action[data-page]').forEach((item) => item.classList.toggle('is-active', item === button));
}));

function setDock(open) {
  const dock = $('#task-dock');
  dock.classList.toggle('is-open', open);
  dock.setAttribute('aria-hidden', String(!open));
  $('#task-dock-toggle').setAttribute('aria-expanded', String(open));
}
$('#task-dock-toggle').addEventListener('click', () => setDock(!$('#task-dock').classList.contains('is-open')));
$('#task-dock-close').addEventListener('click', () => setDock(false));

function setControls(open) {
  $('#control-panel').classList.toggle('is-open', open);
  $('#control-toggle').setAttribute('aria-expanded', String(open));
}
$('#control-toggle').addEventListener('click', () => setControls(!$('#control-panel').classList.contains('is-open')));
$('#control-close').addEventListener('click', () => setControls(false));

$('#theme-toggle').addEventListener('click', () => {
  document.body.classList.toggle('is-dark');
  showToast(document.body.classList.contains('is-dark') ? '已切换深色原型' : '已恢复暖色原型');
});

$('#open-review').addEventListener('click', () => $('#review-dialog').showModal());
$('#advanced-open').addEventListener('click', () => $('#advanced-dialog').showModal());

$('#compare-range').addEventListener('input', (event) => {
  const value = `${event.target.value}%`;
  $('#compare-stage').style.setProperty('--split', value);
});

$$('[data-decision]').forEach((button) => button.addEventListener('click', () => {
  $$('[data-decision]').forEach((item) => item.classList.toggle('is-selected', item === button));
  const copy = {
    adopt: ['已记录为采用结果', '只有你确认后，才会形成可复用偏好证据'],
    adjust: ['将修改当前结果', '本次调整与长期学习会分开确认'],
    reject: ['将重做并询问原因', '原因只会先形成待审核建议，不会自动改规则'],
  }[button.dataset.decision];
  $('#learning-receipt strong').textContent = copy[0];
  $('#learning-receipt small').textContent = copy[1];
}));

$$('.reason-chips button').forEach((button) => button.addEventListener('click', () => button.classList.toggle('is-selected')));
$('#review-primary').addEventListener('click', () => showToast('判断已保存；学习建议仍等待你的审核'));
$('#primary-action').addEventListener('click', () => showToast('原型演示：任务会生成不可变快照并在后台并发'));
$('#manage-assets').addEventListener('click', () => showToast('素材管理将支持多选、移除、撤销和回收站'));
$('#knowledge-pill').addEventListener('click', () => showToast('本次采用：食品暖调、包装文字保护、克制阴影'));
$$('.traffic').forEach((button) => button.addEventListener('click', () => showToast(`${button.getAttribute('aria-label')}只在正式 Tauri 窗口中执行`)));

$$('.view-segment button, .dock-filters button').forEach((button) => button.addEventListener('click', () => {
  button.parentElement.querySelectorAll('button').forEach((item) => item.classList.toggle('is-active', item === button));
}));

window.addEventListener('keydown', (event) => {
  if (event.key === 'Escape') {
    setDock(false);
    setControls(false);
  }
});
