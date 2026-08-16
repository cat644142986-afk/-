// ============================================================
// Product Atelier — Application Logic
// All business features preserved; IA & interaction restructured
// ============================================================
import * as API from './api.js';

// ── Constants ──
const COLLAPSED_W = 68;
const EXPANDED_MIN_W = 960;
const DEFAULT_EXPANDED = { w: 1280, h: 800 };

// ── State ──
const state = {
  currentMode: 'single',
  currentPage: 'process',
  selectedFiles: [],
  originalDataUrl: null,
  generating: false,
  currentTaskId: null,
  results: null,
  resultTab: 'main',
  compareData: null,
  settings: null,
  sidebarCollapsed: true,
  lastExpandedSize: { ...DEFAULT_EXPANDED },
  isMaximized: false,
  pollTimer: null,
};

// ── DOM ──
const $ = s => document.querySelector(s);
const $$ = s => document.querySelectorAll(s);

// ── Toast (minimal, for errors/success only) ──
function toast(msg, type = 'info', dur = 2800) {
  const wrap = $('#toast-wrap');
  const el = document.createElement('div');
  el.className = `toast ${type}`;
  el.textContent = msg;
  wrap.appendChild(el);
  setTimeout(() => {
    el.style.transition = 'opacity .25s, transform .25s';
    el.style.opacity = '0';
    el.style.transform = 'translateX(16px)';
    setTimeout(() => el.remove(), 260);
  }, dur);
}

// ── Sidebar ──
async function toggleSidebar() {
  const { appWindow, LogicalSize } = await import('@tauri-apps/api/window');
  if (state.sidebarCollapsed) {
    state.sidebarCollapsed = false;
    document.body.classList.remove('sidebar-collapsed');
    localStorage.setItem('sidebarCollapsed', '0');
    if (state.isMaximized) return;
    const s = state.lastExpandedSize;
    await appWindow.setSize(new LogicalSize(Math.max(s.w, EXPANDED_MIN_W), Math.max(s.h, 600))).catch(() => {});
    await appWindow.center().catch(() => {});
  } else {
    if (state.isMaximized) {
      await appWindow.unmaximize().catch(() => {});
      state.isMaximized = false;
    }
    try {
      const cur = await appWindow.innerSize();
      if (cur.width >= EXPANDED_MIN_W) state.lastExpandedSize = { w: cur.width, h: cur.height };
    } catch {}
    state.sidebarCollapsed = true;
    document.body.classList.add('sidebar-collapsed');
    await appWindow.setSize(new LogicalSize(COLLAPSED_W, state.lastExpandedSize.h)).catch(() => {});
  }
}

// ── Connection status ──
function setBackendStatus(online, text) {
  const dot = $('#conn-dot'), txt = $('#conn-text');
  if (!dot || !txt) return;
  dot.className = 'conn-dot' + (online ? ' online' : '');
  txt.textContent = online ? (text || '已连接') : (text || '连接中');
}

// ── Navigation ──
function switchPage(page) {
  state.currentPage = page;
  $$('.nav-item').forEach(b => b.classList.toggle('active', b.dataset.page === page));
  $$('.page').forEach(p => p.classList.toggle('active', p.id === `page-${page}`));
  if (state.sidebarCollapsed) toggleSidebar();
  if (page === 'history') loadHistory();
}

// ── Mode switching ──
function switchMode(mode) {
  state.currentMode = mode;
  state.resultTab = mode === 'cutout' ? 'cutout' : 'main';
  $$('.seg-btn').forEach(b => b.classList.toggle('active', b.dataset.mode === mode));

  const sub = $('#page-subtitle');
  const fidField = $('#field-fidelity');
  const angleField = $('#field-angle');
  const platterField = $('#field-platter');
  const batchField = $('#field-batch');
  const refineField = $('#field-refine');
  const modelSel = $('#param-model');
  const ctaHint = $('#cta-hint');
  const genText = $('#generate-text');

  const show = el => { if (el) el.style.display = ''; };
  const hide = el => { if (el) el.style.display = 'none'; };

  if (mode === 'single') {
    show(platterField); show(angleField); show(fidField); show(batchField); hide(refineField);
    sub.textContent = '上传产品图片，一键生成商业影棚级主图';
    genText.textContent = '开始生成';
  } else if (mode === 'multi') {
    show(platterField); show(angleField); show(fidField); hide(batchField); show(refineField);
    if (modelSel.value === 'gpt-image-2') modelSel.value = 'gemini-3.1-flash-image-preview';
    sub.textContent = '上传包含多个产品的图片，自动分割并逐个精修';
    genText.textContent = '批量处理';
  } else { // cutout
    hide(platterField); hide(angleField); hide(fidField); hide(batchField); hide(refineField);
    sub.textContent = '上传图片，使用本地 BiRefNet 模型自动抠图';
    genText.textContent = '开始抠图';
  }

  // Update group visibility — open first group, collapse others as needed
  // (model group always visible, composition hidden for cutout, output hidden for cutout/multi)
  const compGroup = document.querySelector('[data-group="composition"]');
  const outGroup = document.querySelector('[data-group="output"]');
  if (mode === 'cutout') {
    compGroup.style.display = 'none';
    outGroup.style.display = 'none';
  } else {
    compGroup.style.display = '';
    outGroup.style.display = mode === 'multi' ? 'none' : '';
  }

  updateCtaState();
}

// ── Settings groups (collapsible) ──
function setupGroups() {
  $$('.group-toggle').forEach(btn => {
    btn.addEventListener('click', () => {
      btn.closest('.setting-group').classList.toggle('collapsed');
    });
  });
  // Expand model & composition by default, collapse output
  document.querySelector('[data-group="model"]')?.classList.remove('collapsed');
  document.querySelector('[data-group="composition"]')?.classList.remove('collapsed');
  document.querySelector('[data-group="output"]')?.classList.add('collapsed');
}

// ── Sliders with fill ──
function updateSliderFill(slider) {
  const min = parseFloat(slider.min) || 0;
  const max = parseFloat(slider.max) || 100;
  const val = parseFloat(slider.value);
  const pct = ((val - min) / (max - min)) * 100;
  slider.style.background = `linear-gradient(to right, var(--accent) 0%, var(--accent) ${pct}%, rgba(0,0,0,0.06) ${pct}%, rgba(0,0,0,0.06) 100%)`;
}

function setupSliders() {
  const batch = $('#param-batch'), fid = $('#param-fidelity'), sfid = $('#setting-fidelity');
  [batch, fid, sfid].forEach(s => {
    if (!s) return;
    const update = () => {
      updateSliderFill(s);
      if (s === batch) $('#batch-val').textContent = s.value;
      if (s === fid) $('#fid-val').textContent = s.value + '%';
      if (s === sfid) $('#setting-fid-val').textContent = s.value + '%';
    };
    s.addEventListener('input', update);
    update();
  });
}

// ── File handling ──
function handleFiles(fileList) {
  const files = Array.from(fileList).filter(f => f.type.startsWith('image/'));
  if (!files.length) { toast('请选择图片文件', 'error'); return; }
  state.selectedFiles = files;

  const reader0 = new FileReader();
  reader0.onload = e => { state.originalDataUrl = e.target.result; };
  reader0.readAsDataURL(files[0]);

  renderPreview();
  hideProgress();
  hideResults();
  updateCtaState();
}

function renderPreview() {
  const empty = $('#dropzone-empty');
  const preview = $('#dropzone-preview');
  const grid = $('#preview-grid');
  const dz = $('#dropzone');

  if (!state.selectedFiles.length) {
    empty.style.display = '';
    preview.style.display = 'none';
    dz.classList.remove('has-preview');
    return;
  }
  empty.style.display = 'none';
  preview.style.display = '';
  dz.classList.add('has-preview');
  grid.innerHTML = '';

  state.selectedFiles.forEach((file, i) => {
    const reader = new FileReader();
    reader.onload = e => {
      const img = document.createElement('img');
      img.src = e.target.result;
      img.className = 'preview-thumb';
      img.title = file.name;
      grid.appendChild(img);
    };
    reader.readAsDataURL(file);
  });
}

function clearFiles() {
  state.selectedFiles = [];
  state.originalDataUrl = null;
  renderPreview();
  hideProgress();
  hideResults();
  updateCtaState();
}

function setupDropzone() {
  const dz = $('#dropzone');
  const input = $('#file-input');

  dz.addEventListener('click', e => {
    if (e.target.closest('.preview-replace') || e.target.closest('.preview-clear')) return;
    if (state.selectedFiles.length > 0 && !e.target.closest('#btn-browse')) return;
    input.click();
  });
  input.addEventListener('change', () => { if (input.files.length) handleFiles(input.files); });

  // Browse button
  $('#btn-browse')?.addEventListener('click', e => { e.stopPropagation(); input.click(); });
  $('#btn-replace')?.addEventListener('click', e => { e.stopPropagation(); input.click(); });
  $('#btn-clear')?.addEventListener('click', e => { e.stopPropagation(); clearFiles(); input.value = ''; });

  // Drag & drop
  dz.addEventListener('dragover', e => { e.preventDefault(); dz.classList.add('dragover'); });
  dz.addEventListener('dragleave', () => dz.classList.remove('dragover'));
  dz.addEventListener('drop', e => {
    e.preventDefault(); dz.classList.remove('dragover');
    if (e.dataTransfer.files.length) handleFiles(e.dataTransfer.files);
  });

  // Paste
  document.addEventListener('paste', e => {
    if (state.currentPage !== 'process') return;
    if (document.activeElement.tagName === 'INPUT' || document.activeElement.tagName === 'TEXTAREA' || document.activeElement.type === 'password') return;
    const items = e.clipboardData?.items;
    if (!items) return;
    const files = [];
    for (const item of items) {
      if (item.type.startsWith('image/')) { const f = item.getAsFile(); if (f) files.push(f); }
    }
    if (files.length) handleFiles(files);
  });
}

// ── CTA button state ──
function updateCtaState() {
  const btn = $('#btn-generate');
  const hint = $('#cta-hint');
  if (state.generating) {
    btn.disabled = false;
    btn.classList.add('loading');
    return;
  }
  btn.classList.remove('loading');
  const hasFiles = state.selectedFiles.length > 0;
  btn.disabled = !hasFiles;
  if (hint) {
    if (state.currentMode === 'cutout') {
      hint.textContent = hasFiles ? '准备就绪，点击按钮开始抠图' : '请先上传图片';
    } else {
      hint.textContent = hasFiles ? '准备就绪，点击按钮开始生成' : '请先上传图片';
    }
    hint.className = 'cta-hint' + (hasFiles ? ' ready' : '');
  }
}

// ── Progress ──
function showProgress() {
  $('#progress-panel').style.display = '';
  $('#results-panel').style.display = 'none';
  updateProgress(0, '准备中...', []);
}
function hideProgress() { $('#progress-panel').style.display = 'none'; }
function updateProgress(pct, msg, logs) {
  const p = Math.min(100, Math.max(0, Math.round((pct || 0) * 100)));
  $('#progress-percent').textContent = p + '%';
  $('#progress-bar-fill').style.width = p + '%';
  if (msg) $('#progress-title').textContent = msg;
  if (logs && logs.length) {
    const logEl = $('#progress-log');
    logEl.innerHTML = logs.slice(-20).map(l => `<div>${escapeHtml(l)}</div>`).join('');
    logEl.scrollTop = logEl.scrollHeight;
  }
}

// ── Results ──
function showResults(results) {
  state.results = results;
  hideProgress();
  const panel = $('#results-panel');
  panel.style.display = '';

  // Show compare button if we have original
  const cmpBtn = $('#btn-compare-now');
  if (cmpBtn) cmpBtn.style.display = state.originalDataUrl ? '' : 'none';

  renderResults();
}
function hideResults() {
  state.results = null;
  const el = $('#results-panel');
  if (el) el.style.display = 'none';
}

function renderResults() {
  if (!state.results) return;
  const grid = $('#results-grid');
  grid.innerHTML = '';
  const items = state.resultTab === 'main' ? state.results.main : state.results.cutout;
  const isPng = state.resultTab === 'cutout';

  $$('.r-tab').forEach(b => b.classList.toggle('active', b.dataset.rtab === state.resultTab));

  if (!items || !items.length) {
    grid.innerHTML = '<div style="grid-column:1/-1;text-align:center;padding:40px;color:var(--t3);font-size:13px;">暂无结果</div>';
    return;
  }
  items.forEach((item, idx) => {
    const div = document.createElement('div');
    div.className = 'result-item';
    const dataUrl = API.b64ToDataURL(item.data, isPng ? 'image/png' : 'image/jpeg');
    div.innerHTML = `
      <img src="${dataUrl}" alt="${item.name}" loading="lazy" />
      <div class="result-item-ovr">
        ${state.originalDataUrl ? `<button class="act-compare" title="对比原图" data-idx="${idx}">
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="4" width="7" height="16" rx="2"/><rect x="14" y="4" width="7" height="16" rx="2"/></svg>
        </button>` : ''}
        <button class="act-save" title="保存" data-idx="${idx}">
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M19 21H5a2 2 0 01-2-2V5a2 2 0 012-2h11l5 5v11a2 2 0 01-2 2z"/><polyline points="17 21 17 13 7 13 7 21"/><polyline points="7 3 7 8 15 8"/></svg>
        </button>
        ${item.path ? `<button class="act-folder" title="打开位置" data-idx="${idx}">
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M22 19a2 2 0 01-2 2H4a2 2 0 01-2-2V5a2 2 0 012-2h5l2 3h9a2 2 0 012 2z"/></svg>
        </button>` : ''}
      </div>`;
    div.querySelector('img').addEventListener('click', () => openModal(dataUrl, item));
    div.querySelector('.act-save').addEventListener('click', e => { e.stopPropagation(); saveResultImage(item); });
    const folderBtn = div.querySelector('.act-folder');
    if (folderBtn) folderBtn.addEventListener('click', e => { e.stopPropagation(); API.openInFolder(item.path).catch(err => toast('打开失败: ' + err, 'error')); });
    const cmpBtn2 = div.querySelector('.act-compare');
    if (cmpBtn2) cmpBtn2.addEventListener('click', e => { e.stopPropagation(); setCompareView(state.originalDataUrl, dataUrl); });
    grid.appendChild(div);
  });
}

async function saveResultImage(item) {
  try {
    const path = await API.saveImage(item.name, item.data);
    toast('已保存', 'success', 2000);
  } catch (e) {
    if (String(e).includes('取消')) return;
    toast('保存失败: ' + e, 'error');
  }
}

// ── Generate flow ──
async function handleGenerate() {
  if (state.generating) return;
  if (!state.selectedFiles.length) { toast('请先上传图片', 'error'); return; }

  state.generating = true;
  state.currentTaskId = null;
  const btn = $('#btn-generate');
  btn.classList.add('loading');
  btn.disabled = false;
  hideResults();
  showProgress();

  try {
    const file = state.selectedFiles[0];
    let result;

    if (state.currentMode === 'cutout') {
      updateProgress(15, '本地抠图中 (BiRefNet)...', []);
      result = await API.cutoutOnly(file);
      updateProgress(100, '抠图完成！', ['抠图完成']);
      state.results = { main: [], cutout: [{ name: 'cutout.png', data: result.data, path: result.path || '' }] };
      state.resultTab = 'cutout';
      showResults(state.results);
      toast('抠图完成！', 'success');
    } else {
      const params = {
        file,
        model: $('#param-model').value,
        platter: document.querySelector('input[name="platter"]:checked')?.value || 'auto',
        fidelity: parseInt($('#param-fidelity').value),
        angle: $('#param-angle').value,
      };
      if (state.currentMode === 'single') {
        params.product_name = ''; // VLM auto-detect
        params.batch = parseInt($('#param-batch').value);
        result = await API.startSingle(params);
      } else {
        params.refine = $('#param-refine').checked;
        result = await API.startMulti(params);
      }
      state.currentTaskId = result.task_id;

      await new Promise((resolve, reject) => {
        API.startPolling(state.currentTaskId, data => {
          updateProgress(data.progress, data.message || '处理中...', data.logs);
          if (data.status === 'completed') {
            showResults(data.results);
            state.results = data.results;
            toast('生成完成！', 'success');
            resolve();
          } else if (data.status === 'error') {
            toast('生成失败: ' + (data.error || '未知错误'), 'error', 6000);
            hideProgress();
            reject(new Error(data.error || '生成失败'));
          }
        }, 2000);
      });
    }
  } catch (e) {
    console.error(e);
    if (!String(e.message).includes('生成失败')) toast('出错了: ' + e.message, 'error', 5000);
    hideProgress();
  } finally {
    state.generating = false;
    btn.classList.remove('loading');
    updateCtaState();
  }
}

// ── Image modal ──
function openModal(dataUrl, item) {
  const modal = $('#img-modal'), img = $('#modal-img'), actions = $('#modal-actions');
  img.src = dataUrl;
  actions.innerHTML = '';
  if (item) {
    const saveBtn = document.createElement('button');
    saveBtn.className = 'm-save';
    saveBtn.textContent = '保存图片';
    saveBtn.addEventListener('click', () => saveResultImage(item));
    actions.appendChild(saveBtn);
    if (item.path) {
      const folderBtn = document.createElement('button');
      folderBtn.className = 'm-folder';
      folderBtn.textContent = '打开位置';
      folderBtn.addEventListener('click', () => { API.openInFolder(item.path).catch(err => toast('打开失败: ' + err, 'error')); });
      actions.appendChild(folderBtn);
    }
  }
  modal.style.display = '';
}
function closeModal() { $('#img-modal').style.display = 'none'; $('#modal-img').src = ''; }

// ── Compare view ──
function setupCompare() {
  const container = $('.compare-view');
  if (!container) return;
  const slider = $('#compare-slider');
  const resultDiv = $('#compare-after');
  let dragging = false;

  function setPos(pct) {
    pct = Math.max(0, Math.min(100, pct));
    slider.style.left = pct + '%';
    resultDiv.style.clipPath = `inset(0 0 0 ${pct}%)`;
  }
  function getPct(e) {
    const rect = container.getBoundingClientRect();
    const cx = e.touches ? e.touches[0].clientX : e.clientX;
    return ((cx - rect.left) / rect.width) * 100;
  }
  container.addEventListener('mousedown', e => { if (!state.compareData) return; dragging = true; setPos(getPct(e)); });
  container.addEventListener('touchstart', e => { if (!state.compareData) return; dragging = true; setPos(getPct(e)); });
  document.addEventListener('mousemove', e => { if (dragging) setPos(getPct(e)); });
  document.addEventListener('touchmove', e => { if (dragging) setPos(getPct(e)); });
  document.addEventListener('mouseup', () => dragging = false);
  document.addEventListener('touchend', () => dragging = false);
}

function setCompareView(originalUrl, resultUrl) {
  state.compareData = { original: originalUrl, result: resultUrl };
  $('#compare-empty').style.display = 'none';
  $('#compare-view').style.display = '';
  $('#compare-img-original').src = originalUrl;
  $('#compare-img-result').src = resultUrl;
  switchPage('compare');
  setTimeout(() => {
    $('#compare-slider').style.left = '50%';
    $('#compare-after').style.clipPath = 'inset(0 0 0 50%)';
  }, 100);
}

// ── History ──
async function loadHistory() {
  const grid = $('#history-grid');
  grid.innerHTML = '<div class="history-empty"><p>加载中...</p></div>';
  try {
    const items = await API.getHistory();
    grid.innerHTML = '';
    if (!items.length) {
      grid.innerHTML = '<div class="history-empty"><svg width="48" height="48" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1" opacity="0.3"><rect x="3" y="3" width="18" height="18" rx="4"/><circle cx="8.5" cy="8.5" r="1.5"/><path d="M21 15l-5-5L5 21"/></svg><p>暂无历史记录</p></div>';
      return;
    }
    const thumbs = await Promise.all(items.map(async item => {
      if (item.batch) return { item, url: null };
      try { return { item, url: await API.getThumbnailUrl(item.path) }; }
      catch { return { item, url: null }; }
    }));
    thumbs.forEach(({ item, url }) => {
      const div = document.createElement('div');
      div.className = 'history-item';
      if (url) {
        const img = document.createElement('img'); img.alt = item.name; img.src = url; img.loading = 'lazy'; div.appendChild(img);
      } else {
        const fb = document.createElement('div');
        fb.style.cssText = 'display:flex;align-items:center;justify-content:center;height:100%;color:var(--t3);font-size:11px;padding:12px;text-align:center;';
        fb.textContent = item.batch ? item.name : item.name;
        div.appendChild(fb);
      }
      const label = document.createElement('div');
      label.className = 'history-item-label';
      const d = new Date(item.time * 1000);
      label.textContent = `${item.name} · ${d.getMonth()+1}/${d.getDate()} ${String(d.getHours()).padStart(2,'0')}:${String(d.getMinutes()).padStart(2,'0')}`;
      div.appendChild(label);
      div.addEventListener('click', () => {
        if (item.batch) { toast('批量文件夹，请前往输出目录查看', 'info'); API.openInFolder(item.path).catch(() => {}); }
        else { API.openInFolder(item.path).catch(() => {}); }
      });
      grid.appendChild(div);
    });
  } catch (e) {
    grid.innerHTML = `<div class="history-empty"><p>加载失败: ${escapeHtml(e.message)}</p></div>`;
  }
}

// ── Settings ──
async function loadSettings() {
  try {
    const settings = await API.getSettings();
    state.settings = settings;
    if (settings.api_key_set) $('#setting-api-key').placeholder = '已配置（留空则不修改）';
    if (settings.default_model) $('#setting-model').value = settings.default_model;
    if (settings.default_platter) $('#setting-platter').value = settings.default_platter;
    if (settings.default_angle) $('#setting-angle').value = settings.default_angle;
    if (settings.default_fidelity) {
      $('#setting-fidelity').value = settings.default_fidelity;
      updateSliderFill($('#setting-fidelity'));
      $('#setting-fid-val').textContent = settings.default_fidelity + '%';
    }
    if (settings.output_dir) $('#output-dir').textContent = settings.output_dir;
    if (settings.default_model) $('#param-model').value = settings.default_model;
    if (settings.default_platter) {
      const r = document.querySelector(`input[name="platter"][value="${settings.default_platter}"]`);
      if (r) r.checked = true;
    }
    if (settings.default_angle) $('#param-angle').value = settings.default_angle;
    if (settings.default_fidelity) {
      $('#param-fidelity').value = settings.default_fidelity;
      updateSliderFill($('#param-fidelity'));
      $('#fid-val').textContent = settings.default_fidelity + '%';
    }
  } catch (e) { console.error('Settings load error:', e); }
}

async function saveAppSettings() {
  const key = $('#setting-api-key').value.trim();
  const payload = {
    default_model: $('#setting-model').value,
    default_platter: $('#setting-platter').value,
    default_angle: $('#setting-angle').value,
    default_fidelity: parseInt($('#setting-fidelity').value),
  };
  if (key) payload.api_key = key;
  try {
    await API.saveSettings(payload);
    const cfg = await API.getAppConfig();
    cfg.default_model = payload.default_model;
    cfg.default_platter = payload.default_platter;
    cfg.default_angle = payload.default_angle;
    cfg.default_fidelity = payload.default_fidelity;
    if (key) cfg.api_key = key;
    await API.setAppConfig(cfg);
    toast('设置已保存', 'success');
    $('#setting-api-key').value = '';
    $('#setting-api-key').placeholder = '已配置（留空则不修改）';
    await loadSettings();
  } catch (e) { toast('保存失败: ' + e.message, 'error'); }
}

async function checkBalance() {
  const el = $('#balance-display');
  el.textContent = '查询中...';
  try {
    const r = await API.checkBalance();
    if (r.error) { el.textContent = '查询失败: ' + r.error; el.style.color = '#f87171'; }
    else { el.textContent = '余额: ' + r.balance; el.style.color = ''; }
  } catch (e) { el.textContent = '查询失败: ' + e.message; }
}

// ── Window controls ──
function setupWindowControls() {
  $('#btn-minimize').addEventListener('click', () => API.minimizeWindow());
  $('#btn-maximize').addEventListener('click', () => API.toggleMaximize());
  $('#btn-close').addEventListener('click', () => API.closeApp());
}

// ── Keyboard ──
function setupKeyboard() {
  document.addEventListener('keydown', e => {
    if (e.key === 'Escape') { if ($('#img-modal').style.display !== 'none') closeModal(); }
    if ((e.ctrlKey || e.metaKey) && e.key === 'Enter') { if (state.currentPage === 'process' && !state.generating && state.selectedFiles.length) handleGenerate(); }
    if ((e.ctrlKey || e.metaKey) && e.key === 'b') { e.preventDefault(); toggleSidebar(); }
  });
}

// ── Maximize tracking ──
async function trackMaximize() {
  try {
    const { appWindow } = await import('@tauri-apps/api/window');
    const check = async () => { try { state.isMaximized = await appWindow.isMaximized(); } catch {} };
    await check();
    await appWindow.onResized(() => check());
  } catch {}
}

// ── Double-click dragbar to toggle maximize ──
function setupDragbar() {
  const dragbar = $('.sidebar-drag-region');
  if (dragbar) {
    dragbar.addEventListener('dblclick', async e => {
      e.stopPropagation();
      try { const { appWindow } = await import('@tauri-apps/api/window'); await appWindow.toggleMaximize(); } catch {}
    });
  }
  const cdb = $('.content-dragbar');
  if (cdb) {
    cdb.addEventListener('dblclick', async e => {
      try { const { appWindow } = await import('@tauri-apps/api/window'); await appWindow.toggleMaximize(); } catch {}
    });
  }
}

// ── Util ──
function escapeHtml(s) { const d = document.createElement('div'); d.textContent = s; return d.innerHTML; }

// ── Init ──
async function init() {
  // Restore sidebar state
  const saved = localStorage.getItem('sidebarCollapsed');
  if (saved === '0') {
    state.sidebarCollapsed = false;
    document.body.classList.remove('sidebar-collapsed');
  } else {
    state.sidebarCollapsed = true;
    document.body.classList.add('sidebar-collapsed');
  }

  setupWindowControls();
  setupDropzone();
  setupSliders();
  setupGroups();
  setupCompare();
  setupKeyboard();
  setupDragbar();

  // Sidebar
  $('#sidebar-collapse').addEventListener('click', e => { e.stopPropagation(); toggleSidebar(); });
  $('#sidebar-logo').addEventListener('click', e => { e.stopPropagation(); if (state.sidebarCollapsed) toggleSidebar(); });

  // Nav
  $$('.nav-item').forEach(btn => btn.addEventListener('click', () => switchPage(btn.dataset.page)));

  // Mode segmented
  $$('.seg-btn').forEach(btn => btn.addEventListener('click', () => switchMode(btn.dataset.mode)));

  // Result tabs
  $$('.r-tab').forEach(btn => btn.addEventListener('click', () => {
    state.resultTab = btn.dataset.rtab;
    renderResults();
  }));

  // Result action buttons
  $('#btn-save-all')?.addEventListener('click', () => {
    if (!state.results) return;
    const items = state.resultTab === 'main' ? state.results.main : state.results.cutout;
    if (items?.length) { items.forEach((item, i) => setTimeout(() => saveResultImage(item), i * 300)); toast(`正在保存 ${items.length} 张图片...`, 'info'); }
    else toast('没有可保存的结果', 'error');
  });
  $('#btn-open-folder')?.addEventListener('click', () => {
    API.getSettings().then(s => { if (s.output_dir) API.openInFolder(s.output_dir).catch(() => toast('打开文件夹失败', 'error')); }).catch(() => toast('无法获取输出目录', 'error'));
  });
  $('#btn-compare-now')?.addEventListener('click', () => {
    if (state.originalDataUrl && state.results) {
      const items = state.resultTab === 'main' ? state.results.main : state.results.cutout;
      if (items?.[0]) setCompareView(state.originalDataUrl, API.b64ToDataURL(items[0].data, state.resultTab === 'cutout' ? 'image/png' : 'image/jpeg'));
    }
  });

  // Primary CTA
  $('#btn-generate').addEventListener('click', handleGenerate);

  // Modal
  $('#modal-close').addEventListener('click', closeModal);
  $('#modal-backdrop').addEventListener('click', closeModal);

  // Settings
  $('#btn-save-key').addEventListener('click', saveAppSettings);
  $('#btn-save-settings').addEventListener('click', saveAppSettings);
  $('#btn-check-balance').addEventListener('click', checkBalance);
  $('#btn-refresh-history').addEventListener('click', loadHistory);

  // Init mode
  switchMode('single');
  trackMaximize();

  // Backend health
  setBackendStatus(false, '连接中');
  let retries = 0;
  const hc = setInterval(async () => {
    try {
      const h = await API.checkHealth();
      if (h.status === 'ok' || h.ok) {
        clearInterval(hc);
        setBackendStatus(true, '已连接');
        await loadSettings();
      } else {
        retries++;
        if (retries >= 30) { clearInterval(hc); setBackendStatus(false, '离线'); toast('无法连接到后端，请重启应用', 'error', 5000); }
      }
    } catch {
      retries++;
      if (retries >= 30) { clearInterval(hc); setBackendStatus(false, '离线'); }
    }
  }, 1500);

  // Initial collapsed size
  if (state.sidebarCollapsed) {
    try {
      const { appWindow, LogicalSize } = await import('@tauri-apps/api/window');
      await appWindow.setSize(new LogicalSize(COLLAPSED_W, DEFAULT_EXPANDED.h)).catch(() => {});
    } catch {}
  }
}

document.addEventListener('DOMContentLoaded', init);
