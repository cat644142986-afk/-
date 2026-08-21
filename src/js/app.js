import * as API from './api.js';

const PAGE_CONFIG = {
  process: { eyebrow: 'CREATIVE WORKSPACE', title: '你好，伊建', subtitle: '让知识、判断与生成留在同一条创作链里' },
  compare: { eyebrow: 'QUALITY REVIEW', title: '版本对比', subtitle: '检查轮廓、材质、颜色与构图偏差' },
  history: { eyebrow: 'CREATION LEDGER', title: '创作会话', subtitle: '每次生成都有来源、理由和版本' },
  memory: { eyebrow: 'DESIGN DNA', title: '成长中心', subtitle: '把反复出现的判断沉淀为可审核的偏好' },
  settings: { eyebrow: 'SYSTEM & KNOWLEDGE', title: '应用设置', subtitle: '管理模型、知识库与本地存储' },
};

const MODE_CONFIG = {
  single: {
    label: '单产品商业精修', badge: 'SINGLE', action: '开始生成', multiple: false, maxFiles: 1,
    title: '从一张可信的产品图开始', eyebrow: 'START WITH ONE SOURCE', limit: '1 FILE · 20 MB',
    description: '主体完整、包装清晰；系统会保留素材血缘并调用你的设计知识。',
    note: '一张主图，保真生成与透明底同步输出', outputKind: 'ecommerce-main',
  },
  'multi-file': {
    label: '多文件独立批量', badge: 'BATCH', action: '运行批量队列', multiple: true, maxFiles: 12,
    title: '建立一组独立商品队列', eyebrow: 'MULTI-SOURCE QUEUE', limit: 'UP TO 12 FILES · 160 MB',
    description: '每张图片都是独立任务；可统一风格，也会保留各自素材与失败状态。',
    note: '多张源图逐一生成，不把它们误当成同一画面', outputKind: 'ecommerce-main',
  },
  'group-split': {
    label: '组合图智能拆分', badge: 'GROUP SPLIT', action: '识别并拆分', multiple: false, maxFiles: 1,
    title: '上传一张包含多个产品的合照', eyebrow: 'ONE GROUP SHOT', limit: '1 GROUP IMAGE · 20 MB',
    description: '先识别画面中的产品，再逐个裁切、精修和抠图；不等同于多文件批量。',
    note: '一张合照识别多个主体，再分别生成交付图', outputKind: 'group-split',
  },
  'cutout-batch': {
    label: '本地批量抠图', badge: 'LOCAL CUTOUT', action: '开始批量抠图', multiple: true, maxFiles: 24,
    title: '一次导入多张待抠图素材', eyebrow: 'LOCAL MULTI-CUTOUT', limit: 'UP TO 24 FILES · 160 MB',
    description: '逐张输出透明 PNG，记录边缘结果；不调用云端生成，不消耗生图额度。',
    note: '多文件本地抠图，逐张保留结果与失败原因', outputKind: 'cutout',
  },
};

const STAGE_IDS = { empty: 'canvas-empty', ready: 'canvas-image', processing: 'canvas-processing', success: 'canvas-results', error: 'canvas-error' };
const $ = (selector, root = document) => root.querySelector(selector);
const $$ = (selector, root = document) => Array.from(root.querySelectorAll(selector));
let modalReturnFocus = null;

const state = {
  currentPage: 'process', currentMode: 'single', stage: 'empty', selectedFiles: [], fileUrls: [],
  originalDataUrl: '', results: null, resultTab: 'main', viewerIndex: 0, compareData: null,
  currentTaskId: '', currentSessionId: '', currentGenerationId: '', generating: false,
  knowledgeStatus: null, knowledgeBundle: null, settings: null, lastFeedbackSignal: '',
};
window.ProductAtelier = { state };

function escapeHtml(value) {
  const node = document.createElement('div');
  node.textContent = String(value ?? '');
  return node.innerHTML;
}

function toast(message, type = 'info', duration = 3200) {
  const wrap = $('#toast-wrap');
  const item = document.createElement('div');
  item.className = `toast ${type}`;
  item.textContent = message;
  wrap.appendChild(item);
  window.setTimeout(() => {
    item.style.opacity = '0';
    item.style.transform = 'translateX(12px)';
    window.setTimeout(() => item.remove(), 220);
  }, duration);
}

function setBackendStatus(status, text) {
  const dot = $('#conn-dot');
  dot.className = `conn-dot ${status}`;
  $('#conn-text').textContent = text;
  $('#conn-status').title = `后端状态：${text}`;
}

function setStage(stage) {
  if (!STAGE_IDS[stage]) return;
  state.stage = stage;
  $('#preview-card').dataset.stage = stage;
  Object.entries(STAGE_IDS).forEach(([name, id]) => { document.getElementById(id).hidden = name !== stage; });
  renderFileMeta();
}

function switchPage(page) {
  if (!PAGE_CONFIG[page]) return;
  state.currentPage = page;
  $$('.app-page').forEach((section) => {
    const active = section.dataset.pageName === page;
    section.hidden = !active;
    section.classList.toggle('active', active);
  });
  $$('.rail-button[data-page]').forEach((button) => {
    const active = button.dataset.page === page;
    button.classList.toggle('active', active);
    if (active) button.setAttribute('aria-current', 'page'); else button.removeAttribute('aria-current');
  });
  const config = PAGE_CONFIG[page];
  $('#page-eyebrow').textContent = config.eyebrow;
  $('#page-title').textContent = config.title;
  $('#page-subtitle').textContent = config.subtitle;
  if (page === 'history') loadSessions();
  if (page === 'memory') loadMemory();
  if (page === 'settings') loadSettings();
}

function setupTheme() {
  const saved = localStorage.getItem('pa-theme') || 'light';
  document.documentElement.dataset.theme = saved;
  const paint = () => {
    const dark = document.documentElement.dataset.theme === 'dark';
    $('#theme-icon-moon').hidden = dark;
    $('#theme-icon-sun').hidden = !dark;
  };
  paint();
  $('#theme-toggle').addEventListener('click', () => {
    const next = document.documentElement.dataset.theme === 'dark' ? 'light' : 'dark';
    document.documentElement.dataset.theme = next;
    localStorage.setItem('pa-theme', next);
    paint();
  });
}

function releaseFileUrls() {
  state.fileUrls.forEach((url) => URL.revokeObjectURL(url));
  state.fileUrls = [];
}

function clearSession(keepMode = true) {
  if (state.generating) return;
  API.stopPolling();
  releaseFileUrls();
  state.selectedFiles = [];
  state.originalDataUrl = '';
  state.results = null;
  state.compareData = null;
  state.currentTaskId = '';
  state.currentSessionId = '';
  state.currentGenerationId = '';
  state.resultTab = state.currentMode === 'cutout-batch' ? 'cutout' : 'main';
  state.viewerIndex = 0;
  state.knowledgeBundle = null;
  $('#file-input').value = '';
  $('#brief-input').value = '';
  $('#canvas-img-preview').removeAttribute('src');
  $('#file-queue').innerHTML = '';
  $('#source-preview').parentElement.classList.remove('is-queue');
  $('#summary-result').textContent = '等待第一张素材';
  $('#summary-result-note').textContent = '每次生成、采用与调整都会留下可解释证据';
  $('#knowledge-summary').textContent = '等待知识编译';
  renderKnowledge(null);
  setStage('empty');
  if (!keepMode) switchMode('single', false);
  updateCtaState();
}

function switchMode(mode, clearExisting = true) {
  if (!MODE_CONFIG[mode] || state.generating) return;
  const changed = state.currentMode !== mode;
  if (changed && clearExisting && state.selectedFiles.length) clearSession(true);
  state.currentMode = mode;
  const config = MODE_CONFIG[mode];
  $$('.mode-button').forEach((button) => button.classList.toggle('active', button.dataset.mode === mode));
  const input = $('#file-input');
  input.multiple = config.multiple;
  $('#upload-eyebrow').textContent = config.eyebrow;
  $('#upload-title').textContent = config.title;
  $('#upload-description').textContent = config.description;
  $('#upload-limit').textContent = config.limit;
  $('#canvas-title').textContent = config.label.replace('商业', ' · 商业');
  $('#info-mode-badge').textContent = config.badge;
  $('#summary-mode').textContent = config.label;
  $('#summary-note').textContent = config.note;
  $('#field-model').hidden = mode === 'cutout-batch';
  $('#field-composition').hidden = mode === 'cutout-batch';
  $('#field-refine').hidden = mode !== 'group-split';
  $('#batch-field-label').firstChild.textContent = mode === 'multi-file' ? '每图方案数 ' : '生成方案数 ';
  if (mode === 'multi-file' && $('#param-model').value === 'gpt-image-2') $('#model-reason').textContent = '每张独立处理';
  else if (mode === 'cutout-batch') $('#model-reason').textContent = '本地 BiRefNet';
  else $('#model-reason').textContent = '质量优先';
  state.resultTab = mode === 'cutout-batch' ? 'cutout' : 'main';
  renderFileMeta();
  updateCtaState();
}

function renderFileMeta() {
  const count = state.selectedFiles.length;
  $('#btn-replace').hidden = count === 0 || state.generating;
  $('#btn-clear').hidden = count === 0 || state.generating;
  if (count) {
    $('#info-filename').textContent = count === 1 ? state.selectedFiles[0].name : `${count} 张源图已加入队列`;
    $('#ready-count').textContent = `${count} SOURCE${count > 1 ? 'S' : ''} READY`;
  }
}

function renderQueue() {
  const ready = $('#canvas-image');
  const queue = $('#file-queue');
  const isQueue = MODE_CONFIG[state.currentMode].multiple;
  ready.classList.toggle('is-queue', isQueue);
  if (!state.selectedFiles.length) return;
  $('#canvas-img-preview').src = state.fileUrls[0];
  queue.innerHTML = state.selectedFiles.map((file, index) => `
    <article class="queue-item" data-index="${index}">
      <img src="${state.fileUrls[index]}" alt="${escapeHtml(file.name)}" />
      <div><strong title="${escapeHtml(file.name)}">${escapeHtml(file.name)}</strong><span>${String(index + 1).padStart(2, '0')}</span></div>
    </article>`).join('');
}

async function handleFiles(fileList) {
  if (state.generating) return;
  const incoming = Array.from(fileList || []);
  if (!incoming.length) return;
  const config = MODE_CONFIG[state.currentMode];
  const valid = incoming.filter((file) => {
    if (!/^image\/(png|jpeg|webp)$/i.test(file.type)) { toast(`${file.name} 不是支持的图片格式`, 'error'); return false; }
    if (file.size > 20 * 1024 * 1024) { toast(`${file.name} 超过 20 MB`, 'error'); return false; }
    return true;
  });
  if (!valid.length) return;
  let next = config.multiple ? [...state.selectedFiles, ...valid] : [valid[0]];
  if (next.length > config.maxFiles) {
    toast(`当前模式单次最多 ${config.maxFiles} 张`, 'error');
    next = next.slice(0, config.maxFiles);
  }
  releaseFileUrls();
  state.selectedFiles = next;
  state.fileUrls = next.map((file) => URL.createObjectURL(file));
  state.originalDataUrl = state.fileUrls[0] || '';
  state.results = null;
  state.compareData = null;
  renderQueue();
  setStage('ready');
  $('#summary-result').textContent = `${next.length} 张素材已就绪`;
  $('#summary-result-note').textContent = '知识规则正在按任务语义预编译';
  updateCtaState();
  await compileKnowledgePreview();
  $('#file-input').value = '';
}

function getIntentLocks() {
  const locks = {};
  $$('[data-lock]').forEach((input) => { locks[input.dataset.lock] = Boolean(input.checked); });
  if ($('#param-angle').value === 'keep') locks.angle = true;
  return locks;
}

function getPlatter() {
  return $('input[name="platter"]:checked')?.value || 'auto';
}

function buildBrief() {
  const request = $('#brief-input').value.trim();
  return {
    objective: request || '将产品原图转化为可交付的商业图片',
    user_request: request,
    mode: state.currentMode,
    category: 'general',
    platform: 'ecommerce',
    output_kind: MODE_CONFIG[state.currentMode].outputKind,
    angle: $('#param-angle').value,
    platter: getPlatter(),
    fidelity: Number($('#param-fidelity').value),
    intent_locks: getIntentLocks(),
    output_spec: { ratio: '1:1', size: '2048x2048', format: state.currentMode === 'cutout-batch' ? 'transparent PNG' : 'JPG+transparent PNG' },
  };
}

let compileTimer = null;
async function compileKnowledgePreview() {
  if (!state.selectedFiles.length && !$('#brief-input').value.trim()) return;
  try {
    const bundle = await API.compileKnowledge(buildBrief());
    state.knowledgeBundle = bundle;
    renderKnowledge(bundle);
  } catch (error) {
    $('#knowledge-summary').textContent = '知识编译暂不可用，使用安全默认值';
  }
}

function scheduleKnowledgeCompile() {
  window.clearTimeout(compileTimer);
  compileTimer = window.setTimeout(compileKnowledgePreview, 480);
}

function renderKnowledge(bundle) {
  if (!bundle) {
    $('#knowledge-source-count').textContent = '0 条来源';
    $('#knowledge-source-list').innerHTML = '<div class="page-empty">尚未编译知识。</div>';
    $('#knowledge-conflicts').innerHTML = '<div class="conflict-item ok"><span>✓</span><p>当前没有检测到规则冲突</p></div>';
    $('#intelligence-brief').textContent = '等待输入创作意图';
    $('#intelligence-context').textContent = '选择模式与素材后，系统会把目标编译成可检查的创作合同。';
    return;
  }
  const sources = bundle.sources || [];
  const rules = (bundle.positive_rules || []).length + (bundle.negative_rules || []).length;
  $('#knowledge-summary').textContent = `${sources.length} 份知识 · ${rules} 条执行规则`;
  $('#knowledge-source-count').textContent = `${sources.length} 条来源`;
  $('#knowledge-source-list').innerHTML = sources.length ? sources.map((source, index) => `<div class="source-item"><span>${String(index + 1).padStart(2, '0')}</span><div><strong>${escapeHtml(source.title || source.id || '设计规则')}</strong><small>${escapeHtml(source.relative_path || source.path || '')}</small></div></div>`).join('') : '<div class="page-empty">本次使用安全默认规则。</div>';
  const brief = bundle.creative_brief || {};
  $('#intelligence-brief').textContent = brief.objective || '本次商业图片任务';
  $('#intelligence-context').textContent = `${MODE_CONFIG[state.currentMode].label} · ${brief.output_kind || '商业输出'} · ${Object.values(brief.intent_locks || {}).filter(Boolean).length} 项 Intent Locks`;
  const conflicts = bundle.conflicts || [];
  $('#knowledge-conflicts').innerHTML = conflicts.length ? conflicts.map((item) => `<div class="conflict-item"><span>!</span><p>${escapeHtml(item.message)}</p></div>`).join('') : '<div class="conflict-item ok"><span>✓</span><p>当前没有检测到规则冲突</p></div>';
}

function updateCtaState() {
  const button = $('#btn-generate');
  const hasFiles = state.selectedFiles.length > 0;
  button.disabled = !hasFiles || state.generating;
  if (state.generating) $('#generate-text').textContent = 'Atelier 正在工作';
  else if (!hasFiles) $('#generate-text').textContent = '选择图片开始';
  else $('#generate-text').textContent = MODE_CONFIG[state.currentMode].action;
  $('#cta-hint').textContent = !hasFiles ? '上传后会先编译 Creative Brief' : `${state.selectedFiles.length} 张素材 · ${Object.values(getIntentLocks()).filter(Boolean).length} 项锁定`;
}

function updateQuickControls() {
  const angleLabels = { auto: 'Auto', keep: 'Locked', front: 'Front', '45top': '45° Top', '30side': '30° Side', '90top': 'Top' };
  $('#quick-angle').textContent = angleLabels[$('#param-angle').value] || $('#param-angle').value;
  $('#quick-fidelity').textContent = `${$('#param-fidelity').value}%`;
  $('#quick-batch').textContent = state.currentMode === 'multi-file' ? `${$('#param-batch').value} / file` : $('#param-batch').value;
  $('#fid-val').textContent = `${$('#param-fidelity').value}%`;
  $('#batch-val').textContent = $('#param-batch').value;
  updateCtaState();
  scheduleKnowledgeCompile();
}

function showProgress(message = '建立本地创作会话…') {
  state.generating = true;
  $('#progress-title').textContent = '编译创作意图';
  $('#progress-percent').textContent = '0%';
  $('#progress-bar-fill').style.width = '0%';
  $('#progress-log').textContent = message;
  setStage('processing');
  updateCtaState();
}

function updateProgress(data) {
  const progress = Math.max(0, Math.min(1, Number(data.progress || 0)));
  $('#progress-percent').textContent = `${Math.round(progress * 100)}%`;
  $('#progress-bar-fill').style.width = `${Math.round(progress * 100)}%`;
  $('#progress-title').textContent = data.message || (progress < .18 ? '读取知识与素材' : progress < .72 ? '生成商业版本' : '整理交付结果');
  const logs = Array.isArray(data.logs) ? data.logs : [];
  $('#progress-log').textContent = logs.length ? logs[logs.length - 1] : (data.message || '处理中…');
}

function showError(error) {
  state.generating = false;
  $('#error-message').textContent = String(error || '未知错误').replace(/^Error:\s*/, '').slice(0, 280);
  setStage('error');
  updateCtaState();
  toast('任务没有完成，创作会话已保留错误证据', 'error');
}

async function handleGenerate() {
  if (state.generating || !state.selectedFiles.length) return;
  showProgress();
  try {
    await compileKnowledgePreview();
    const params = {
      files: state.selectedFiles,
      file: state.selectedFiles[0],
      model: $('#param-model').value,
      variations: Number($('#param-batch').value),
      batch: Number($('#param-batch').value),
      platter: getPlatter(),
      fidelity: Number($('#param-fidelity').value),
      angle: $('#param-angle').value,
      refine: $('#param-refine').checked,
      brief: buildBrief(),
      intent_locks: getIntentLocks(),
      category: 'general',
    };
    let response;
    if (state.currentMode === 'single') response = await API.startSingle(params);
    else if (state.currentMode === 'multi-file') response = await API.startMultiFile(params);
    else if (state.currentMode === 'group-split') response = await API.startGroupSplit(params);
    else response = await API.cutoutBatch(params);
    state.currentTaskId = response.task_id || '';
    state.currentSessionId = response.session_id || '';
    state.currentGenerationId = response.generation_id || '';
    API.startPolling(state.currentTaskId, handleTaskUpdate, 900);
  } catch (error) {
    showError(error);
  }
}

async function handleTaskUpdate(data) {
  if (data.status === 'error') { showError(data.error || '任务失败'); return; }
  updateProgress(data);
  if (data.status !== 'completed') return;
  state.generating = false;
  state.results = data.results || { main: [], cutout: [] };
  if (state.currentMode === 'cutout-batch') state.resultTab = 'cutout';
  else state.resultTab = (state.results.main || []).length ? 'main' : 'cutout';
  state.viewerIndex = 0;
  await refreshCurrentSession();
  renderResults();
  setStage('success');
  updateCtaState();
  const count = (state.results.main || []).length + (state.results.cutout || []).length;
  $('#summary-result').textContent = `${count} 个结果已写入创作账本`;
  $('#summary-result-note').textContent = '请选择、反馈或回传终稿，让下一版更接近你';
  toast('任务完成：知识来源、Prompt 和结果血缘已记录', 'success');
}

async function refreshCurrentSession() {
  if (!state.currentSessionId) return;
  try {
    const session = await API.getSession(state.currentSessionId);
    const generations = session.generations || [];
    if (!state.currentGenerationId && generations.length) state.currentGenerationId = generations[generations.length - 1].id;
    const generation = generations.find((item) => item.id === state.currentGenerationId) || generations[generations.length - 1];
    if (generation?.knowledge_refs?.length) {
      const bundle = { ...(state.knowledgeBundle || {}), sources: generation.knowledge_refs };
      state.knowledgeBundle = bundle;
      renderKnowledge(bundle);
    }
  } catch (_) { /* ledger is non-blocking */ }
}

function getResultItems(tab = state.resultTab) {
  return state.results && Array.isArray(state.results[tab]) ? state.results[tab] : [];
}

function resultDataUrl(item, tab = state.resultTab) {
  if (!item) return '';
  if (item.data && String(item.data).startsWith('data:')) return item.data;
  if (item.data) return API.b64ToDataURL(item.data, tab === 'cutout' ? 'image/png' : 'image/jpeg');
  return item.url || '';
}

function renderResults() {
  $$('.result-tab').forEach((button) => {
    const active = button.dataset.rtab === state.resultTab;
    button.classList.toggle('active', active);
    button.setAttribute('aria-selected', String(active));
  });
  const items = getResultItems();
  if (!items.length) {
    const fallback = state.resultTab === 'main' ? 'cutout' : 'main';
    if (getResultItems(fallback).length) { state.resultTab = fallback; renderResults(); }
    return;
  }
  state.viewerIndex = Math.max(0, Math.min(state.viewerIndex, items.length - 1));
  const item = items[state.viewerIndex];
  const src = resultDataUrl(item);
  $('#viewer-main-img').src = src;
  $('#viewer-main-img').onclick = () => openModal(src);
  $('#viewer-nav').hidden = items.length < 2;
  $('#viewer-counter').textContent = `${state.viewerIndex + 1} / ${items.length}`;
  $('#viewer-thumbs').innerHTML = items.map((entry, index) => `<button class="viewer-thumb ${index === state.viewerIndex ? 'active' : ''}" type="button" data-index="${index}"><img src="${resultDataUrl(entry)}" alt="结果 ${index + 1}" /></button>`).join('');
  $$('.viewer-thumb').forEach((button) => button.addEventListener('click', () => { state.viewerIndex = Number(button.dataset.index); renderResults(); }));
  if (state.originalDataUrl && src) state.compareData = { original: state.originalDataUrl, result: src };
}

async function saveCurrentResults() {
  const items = getResultItems();
  if (!items.length) return;
  let saved = 0;
  for (const item of items) {
    if (!item.data) continue;
    try { await API.saveImage(item.name || `product-atelier-${saved + 1}.${state.resultTab === 'cutout' ? 'png' : 'jpg'}`, item.data.replace(/^data:[^,]+,/, '')); saved += 1; }
    catch (error) { toast(`保存失败：${error}`, 'error'); break; }
  }
  if (saved) toast(`已保存 ${saved} 个结果`, 'success');
}

async function openOutputFolder() {
  const item = getResultItems()[state.viewerIndex];
  if (!item?.path) { toast('当前结果没有可定位的本地路径', 'error'); return; }
  try { await API.openInFolder(item.path); } catch (error) { toast(`无法打开目录：${error}`, 'error'); }
}

async function recordFeedback(signal, reason = '') {
  if (!state.currentSessionId) { toast('当前没有可反馈的创作会话', 'error'); return; }
  try {
    await API.recordFeedback(state.currentSessionId, {
      signal,
      generation_id: state.currentGenerationId || null,
      reason,
      scope: 'session',
      structured: { mode: state.currentMode, result_tab: state.resultTab, result_index: state.viewerIndex, brief: $('#brief-input').value.trim() },
    });
    toast(signal === 'adopted' ? '已记录采用：这版会成为成功证据' : '反馈已进入本地学习证据', 'success');
    $('#feedback-input').value = '';
  } catch (error) { toast(`反馈记录失败：${error}`, 'error'); }
}

function openDrawer(name) {
  const drawer = name === 'advanced' ? $('#advanced-drawer') : $('#intelligence-drawer');
  drawer.hidden = false;
}

function closeDrawer(name) {
  const drawer = name === 'advanced' ? $('#advanced-drawer') : $('#intelligence-drawer');
  drawer.hidden = true;
  updateQuickControls();
}

function openModal(src) {
  if (!src) return;
  modalReturnFocus = document.activeElement;
  $('#modal-img').src = src;
  $('#img-modal').hidden = false;
  $('#modal-close').focus();
}
function closeModal() {
  $('#img-modal').hidden = true;
  $('#modal-img').removeAttribute('src');
  if (modalReturnFocus instanceof HTMLElement) modalReturnFocus.focus();
  modalReturnFocus = null;
}

function renderCompare() {
  const has = Boolean(state.compareData?.original && state.compareData?.result);
  $('#compare-empty').hidden = has;
  $('#compare-view').hidden = !has;
  if (!has) return;
  $('#compare-img-original').src = state.compareData.original;
  $('#compare-img-result').src = state.compareData.result;
  setComparePosition(50);
}

function setComparePosition(percent) {
  const value = Math.max(3, Math.min(97, percent));
  $('#compare-after').style.left = `${value}%`;
  $('#compare-slider').style.left = `${value}%`;
  $('#compare-slider').setAttribute('aria-valuenow', String(Math.round(value)));
}

function setupCompare() {
  const view = $('#compare-view');
  const slider = $('#compare-slider');
  let dragging = false;
  const move = (clientX) => {
    const rect = view.getBoundingClientRect();
    if (rect.width) setComparePosition(((clientX - rect.left) / rect.width) * 100);
  };
  slider.addEventListener('pointerdown', (event) => { dragging = true; slider.setPointerCapture(event.pointerId); move(event.clientX); });
  slider.addEventListener('pointermove', (event) => { if (dragging) move(event.clientX); });
  slider.addEventListener('pointerup', () => { dragging = false; });
  view.addEventListener('click', (event) => move(event.clientX));
  slider.addEventListener('keydown', (event) => {
    const current = Number(slider.getAttribute('aria-valuenow') || 50);
    const delta = event.shiftKey ? 10 : 2;
    if (event.key === 'ArrowLeft') { event.preventDefault(); setComparePosition(current - delta); }
    if (event.key === 'ArrowRight') { event.preventDefault(); setComparePosition(current + delta); }
    if (event.key === 'Home') { event.preventDefault(); setComparePosition(3); }
    if (event.key === 'End') { event.preventDefault(); setComparePosition(97); }
  });
}

function formatTime(value) {
  if (!value) return '—';
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? String(value) : date.toLocaleString('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' });
}

async function loadSessions() {
  const grid = $('#history-grid');
  grid.innerHTML = '<div class="page-empty">正在读取本地创作账本…</div>';
  try {
    const sessions = await API.getSessions(60);
    if (!sessions.length) { grid.innerHTML = '<div class="page-empty">还没有创作会话。完成第一项任务后，版本链会出现在这里。</div>'; return; }
    grid.innerHTML = sessions.map((session) => `<article class="session-card" data-session-id="${escapeHtml(session.id)}"><div class="session-card__top"><span class="session-card__mode">${escapeHtml(MODE_CONFIG[session.mode]?.badge || String(session.mode).toUpperCase())}</span><span class="session-card__status">${escapeHtml(session.status || 'draft')}</span></div><div><h3>${escapeHtml(session.title || '未命名会话')}</h3><p>${escapeHtml(session.brief?.objective || session.brief?.user_request || '保留了素材、参数与生成证据')}</p></div><div class="session-card__meta"><span>${session.asset_count || 0} assets · ${session.generation_count || 0} versions</span><span>${formatTime(session.updated_at)}</span></div></article>`).join('');
    $$('.session-card', grid).forEach((card) => card.addEventListener('click', async () => {
      try {
        const session = await API.getSession(card.dataset.sessionId);
        const generations = session.generations || [];
        const generation = generations[generations.length - 1];
        state.knowledgeBundle = { creative_brief: session.brief || {}, sources: generation?.knowledge_refs || [], positive_rules: [], negative_rules: [], conflicts: [] };
        renderKnowledge(state.knowledgeBundle);
        openDrawer('intelligence');
      } catch (error) { toast(`无法读取会话：${error}`, 'error'); }
    }));
  } catch (error) { grid.innerHTML = `<div class="page-empty">读取失败：${escapeHtml(error)}</div>`; }
}

async function loadMemory() {
  try {
    await API.synthesizeMemory().catch(() => null);
    const [ledger, suggestions] = await Promise.all([API.getLedgerStatus(), API.getMemorySuggestions('pending')]);
    const counts = ledger.counts || {};
    $('#memory-session-count').textContent = counts.sessions || 0;
    $('#memory-feedback-count').textContent = counts.feedback || 0;
    $('#memory-pending-count').textContent = ledger.pending_memory || suggestions.length || 0;
    const list = $('#memory-list');
    if (!suggestions.length) { list.innerHTML = '<div class="page-empty">暂无待审核偏好。继续使用后，重复模式会在这里出现。</div>'; return; }
    list.innerHTML = suggestions.map((item) => {
      const proposed = item.proposed_value || {};
      const evidenceCount = Number(proposed.distinct_sessions || (item.evidence || []).length || 0);
      const contradictionCount = Number(proposed.contradiction_count || 0);
      return `<article class="memory-item" data-id="${escapeHtml(item.id)}"><div class="memory-item__copy"><div class="memory-item__meta"><span>${escapeHtml(item.scope_type || 'designer')} · ${escapeHtml(item.category || 'general')}</span><strong>${Math.round(Number(item.confidence || 0) * 100)}%</strong></div><h3>${escapeHtml(proposed.label || item.rule_key || '新偏好建议')}</h3><p>${escapeHtml(proposed.directive || JSON.stringify(proposed))}</p><small>${evidenceCount} 个独立会话支持${contradictionCount ? ` · ${contradictionCount} 条反例` : ' · 暂无反例'}</small></div><div class="memory-actions"><button type="button" data-review="approved">采用</button><button type="button" data-review="rejected">拒绝</button></div></article>`;
    }).join('');
    $$('[data-review]', list).forEach((button) => button.addEventListener('click', async () => {
      const item = button.closest('.memory-item');
      try { await API.reviewMemorySuggestion(item.dataset.id, button.dataset.review); item.remove(); toast('审核结果已记录', 'success'); loadMemory(); }
      catch (error) { toast(`审核失败：${error}`, 'error'); }
    }));
  } catch (error) { $('#memory-list').innerHTML = `<div class="page-empty">读取失败：${escapeHtml(error)}</div>`; }
}

async function loadSettings() {
  try {
    const settings = await API.getSettings();
    state.settings = settings;
    if (settings.default_model) { $('#setting-model').value = settings.default_model; $('#param-model').value = settings.default_model; }
    if (settings.default_platter) { const radio = $(`input[name="platter"][value="${settings.default_platter}"]`); if (radio) radio.checked = true; $('#setting-platter').value = settings.default_platter; }
    if (settings.default_angle) { $('#setting-angle').value = settings.default_angle; $('#param-angle').value = settings.default_angle; }
    if (settings.default_fidelity) { $('#setting-fidelity').value = settings.default_fidelity; $('#param-fidelity').value = settings.default_fidelity; }
    $('#setting-fid-val').textContent = `${$('#setting-fidelity').value}%`;
    $('#output-dir').textContent = settings.output_dir || '—';
    $('#setting-api-key').placeholder = settings.api_key_set ? '已配置（留空不修改）' : '输入 API Key';
    $('#setting-knowledge-path').value = settings.knowledge_base_path || '';
    renderKnowledgeStatus(settings.knowledge);
    updateQuickControls();
  } catch (error) { toast(`读取设置失败：${error}`, 'error'); }
}

function renderKnowledgeStatus(status) {
  if (!status) return;
  state.knowledgeStatus = status;
  $('#knowledge-pill-text').textContent = status.available ? `${status.document_count} docs · ${status.rule_count} rules` : '知识库未连接';
  $('#setting-knowledge-status').textContent = status.available ? '只读连接正常' : '未找到知识库';
  $('#setting-knowledge-detail').textContent = status.available ? `${status.document_count} 份文档 · ${status.rule_count} 条规则` : status.design_path || '—';
}

async function saveSettings(includeKey = false) {
  const payload = {
    default_model: $('#setting-model').value,
    default_platter: $('#setting-platter').value,
    default_angle: $('#setting-angle').value,
    default_fidelity: Number($('#setting-fidelity').value),
    knowledge_base_path: $('#setting-knowledge-path').value.trim(),
  };
  if (includeKey && $('#setting-api-key').value.trim()) payload.api_key = $('#setting-api-key').value.trim();
  try { const result = await API.saveSettings(payload); state.settings = result; $('#setting-api-key').value = ''; renderKnowledgeStatus(result.knowledge); toast('设置已保存', 'success'); }
  catch (error) { toast(`保存失败：${error}`, 'error'); }
}

async function reloadKnowledge() {
  try { const status = await API.reloadKnowledge($('#setting-knowledge-path').value.trim()); renderKnowledgeStatus(status); toast('知识库已重新编译', 'success'); await compileKnowledgePreview(); }
  catch (error) { toast(`知识编译失败：${error}`, 'error'); }
}

async function checkBalance() {
  $('#balance-display').textContent = '正在查询…';
  try { const result = await API.checkBalance(); $('#balance-display').textContent = result.error ? result.error : `当前余额：${result.balance}`; }
  catch (error) { $('#balance-display').textContent = `查询失败：${error}`; }
}

function bindEvents() {
  $$('.rail-button[data-page]').forEach((button) => button.addEventListener('click', () => switchPage(button.dataset.page)));
  $$('.mode-button').forEach((button) => button.addEventListener('click', () => switchMode(button.dataset.mode)));
  const input = $('#file-input');
  $('#btn-browse').addEventListener('click', () => input.click());
  $('#btn-replace').addEventListener('click', () => input.click());
  input.addEventListener('change', () => handleFiles(input.files));
  $('#btn-clear').addEventListener('click', () => clearSession(true));
  $('#btn-new-session').addEventListener('click', () => { clearSession(true); switchPage('process'); });
  $('#btn-generate').addEventListener('click', handleGenerate);
  $('#btn-retry').addEventListener('click', handleGenerate);
  $('#btn-error-reset').addEventListener('click', () => { state.generating = false; setStage(state.selectedFiles.length ? 'ready' : 'empty'); updateCtaState(); });
  $('#brief-input').addEventListener('input', scheduleKnowledgeCompile);
  $('#param-angle').addEventListener('change', updateQuickControls);
  $('#param-fidelity').addEventListener('input', updateQuickControls);
  $('#param-batch').addEventListener('input', updateQuickControls);
  $$('input[name="platter"]').forEach((radio) => radio.addEventListener('change', updateQuickControls));
  $$('[data-lock]').forEach((input) => input.addEventListener('change', () => { input.closest('.lock-chip').classList.toggle('active', input.checked); updateCtaState(); scheduleKnowledgeCompile(); }));
  $('#btn-advanced').addEventListener('click', () => openDrawer('advanced'));
  $$('[data-open-advanced]').forEach((button) => button.addEventListener('click', () => openDrawer('advanced')));
  $('#btn-open-intelligence').addEventListener('click', () => openDrawer('intelligence'));
  $('#btn-knowledge-card').addEventListener('click', () => openDrawer('intelligence'));
  $$('[data-close-drawer]').forEach((button) => button.addEventListener('click', () => closeDrawer(button.dataset.closeDrawer)));
  $$('.result-tab').forEach((button) => button.addEventListener('click', () => { state.resultTab = button.dataset.rtab; state.viewerIndex = 0; renderResults(); }));
  $('#viewer-prev').addEventListener('click', () => { const items = getResultItems(); if (items.length) { state.viewerIndex = (state.viewerIndex - 1 + items.length) % items.length; renderResults(); } });
  $('#viewer-next').addEventListener('click', () => { const items = getResultItems(); if (items.length) { state.viewerIndex = (state.viewerIndex + 1) % items.length; renderResults(); } });
  $('#btn-open-compare').addEventListener('click', () => { renderCompare(); switchPage('compare'); });
  $('#btn-compare-back').addEventListener('click', () => switchPage('process'));
  $('#btn-save-all').addEventListener('click', saveCurrentResults);
  $('#btn-open-folder').addEventListener('click', openOutputFolder);
  $('#btn-adopt').addEventListener('click', () => { state.lastFeedbackSignal = 'adopted'; recordFeedback('adopted', $('#feedback-input').value.trim()); });
  $('#btn-reject').addEventListener('click', () => { state.lastFeedbackSignal = 'rejected'; $('#feedback-input').focus(); toast('请说出具体原因，它会成为下一版证据'); });
  $('#btn-feedback').addEventListener('click', () => { const reason = $('#feedback-input').value.trim(); if (!reason) { toast('请先写下具体判断'); return; } recordFeedback(state.lastFeedbackSignal === 'rejected' ? 'rejected' : 'note', reason); });
  $('#btn-refresh-history').addEventListener('click', loadSessions);
  $('#btn-refresh-memory').addEventListener('click', loadMemory);
  $('#btn-save-key').addEventListener('click', () => saveSettings(true));
  $('#btn-save-settings').addEventListener('click', () => saveSettings(false));
  $('#btn-reload-knowledge').addEventListener('click', reloadKnowledge);
  $('#btn-check-balance').addEventListener('click', checkBalance);
  $('#setting-fidelity').addEventListener('input', () => { $('#setting-fid-val').textContent = `${$('#setting-fidelity').value}%`; });
  $('#modal-backdrop').addEventListener('click', closeModal);
  $('#modal-close').addEventListener('click', closeModal);
  $('#btn-min-dot').addEventListener('click', () => API.minimizeWindow().catch(() => {}));
  $('#btn-max-dot').addEventListener('click', () => API.toggleMaximize().catch(() => {}));
  $('#btn-close-dot').addEventListener('click', () => API.closeApp().catch(() => window.close()));
  const canvas = $('#preview-canvas');
  canvas.addEventListener('dragover', (event) => { event.preventDefault(); if (!state.generating) canvas.style.outline = '2px solid var(--coral)'; });
  canvas.addEventListener('dragleave', () => { canvas.style.outline = ''; });
  canvas.addEventListener('drop', (event) => { event.preventDefault(); canvas.style.outline = ''; handleFiles(event.dataTransfer.files); });
  document.addEventListener('keydown', (event) => {
    if (event.key !== 'Escape') return;
    if (!$('#img-modal').hidden) closeModal();
    if (!$('#advanced-drawer').hidden) closeDrawer('advanced');
    if (!$('#intelligence-drawer').hidden) closeDrawer('intelligence');
  });
}

async function connectBackend() {
  setBackendStatus('connecting', '连接中');
  for (let attempt = 0; attempt < 20; attempt += 1) {
    const health = await API.checkHealth();
    if (health.ok) {
      setBackendStatus('connected', '已连接');
      try { const status = await API.getKnowledgeStatus(); renderKnowledgeStatus(status); } catch (_) { /* keep app usable */ }
      return;
    }
    await new Promise((resolve) => window.setTimeout(resolve, 700));
  }
  setBackendStatus('disconnected', '未连接');
  toast('本地服务尚未就绪，稍后可重试', 'error');
}

async function init() {
  setupTheme();
  bindEvents();
  setupCompare();
  switchMode('single', false);
  setStage('empty');
  updateQuickControls();
  connectBackend();
  loadSettings();
}

document.addEventListener('DOMContentLoaded', init);
