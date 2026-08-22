import * as API from './api.js';
import {
  collectResultItems,
  createSubmissionSnapshot,
  itemCompletionProgress,
  jobCompletionProgress,
  jobLifecycleActions,
  jobsRenderSignature,
  processResultItems,
  queueCompletionProgress,
  selectionAfterImport,
  submissionFingerprint,
} from './workspace-state.js';
import { JOB_STATUS, MODE_CONFIG, MODE_IDS, PAGE_CONFIG, STAGE_IDS } from './studio-config.js';
import { createWorkflowDockController } from './studio-shell.js';
import { createStudioState, draftPayloadFromSnapshot, snapshotFromDraft } from './studio-state.js';

const MODE_STATE_KEY = 'pa-workspace-ui-v2';
const PENDING_SUBMISSION_KEY = 'pa-pending-job-v1';
const $ = (selector, root = document) => root.querySelector(selector);
const $$ = (selector, root = document) => Array.from(root.querySelectorAll(selector));
let modalReturnFocus = null;
let drawerReturnFocus = null;

const state = createStudioState(MODE_IDS);
const workflowDock = createWorkflowDockController();
window.ProductAtelier = { state };

function escapeHtml(value) {
  const node = document.createElement('div');
  node.textContent = String(value ?? '');
  return node.innerHTML;
}

function selectedAssetIds(mode = state.currentMode) {
  return state.modeSelections[mode] || [];
}

function selectedAssets(mode = state.currentMode) {
  const ids = new Set(selectedAssetIds(mode));
  return state.assets.filter((asset) => ids.has(asset.id));
}

function createClientRequestId() {
  if (globalThis.crypto?.randomUUID) return globalThis.crypto.randomUUID();
  return `web-${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

function restorePendingSubmission() {
  try {
    const saved = JSON.parse(localStorage.getItem(PENDING_SUBMISSION_KEY) || 'null');
    if (
      saved && typeof saved === 'object'
      && typeof saved.fingerprint === 'string'
      && typeof saved.requestId === 'string'
      && saved.payload && typeof saved.payload === 'object'
    ) state.pendingSubmission = saved;
  } catch (_) { /* an obsolete pending request should not block startup */ }
}

function persistPendingSubmission(pending) {
  state.pendingSubmission = pending;
  try {
    if (pending) localStorage.setItem(PENDING_SUBMISSION_KEY, JSON.stringify(pending));
    else localStorage.removeItem(PENDING_SUBMISSION_KEY);
  } catch (_) { /* in-memory retry protection remains available */ }
}

function clearPendingSubmission(requestId) {
  if (!state.pendingSubmission || state.pendingSubmission.requestId !== requestId) return;
  persistPendingSubmission(null);
}

function isDefinitiveJobRejection(error) {
  const status = Number(error?.status || 0);
  return status >= 400 && status < 500 && ![408, 429].includes(status);
}

function persistWorkspaceState() {
  try {
    localStorage.setItem(MODE_STATE_KEY, JSON.stringify({
      currentMode: state.currentMode,
    }));
  } catch (_) { /* local preferences are best effort */ }
}

function restoreWorkspaceState() {
  try {
    const saved = JSON.parse(localStorage.getItem(MODE_STATE_KEY) || '{}');
    if (MODE_CONFIG[saved.currentMode]) state.currentMode = saved.currentMode;
  } catch (_) { /* ignore an obsolete local snapshot */ }
}

function syncLegacySelection() {
  state.selectedFiles = selectedAssets();
  state.originalDataUrl = state.selectedFiles[0]?.content_url || '';
}

function formatApiError(error, fallback = '本地服务暂不可用') {
  const message = String(error?.message || error || fallback).replace(/^Error:\s*/, '');
  if (error?.code === 'REQUEST_TIMEOUT') return `${fallback}（请求超时）`;
  if (/HTTP 404/.test(message)) return `${fallback}（接口尚未就绪）`;
  if (/Failed to fetch|NetworkError|Load failed/i.test(message)) return `${fallback}（无法连接后端）`;
  return message.slice(0, 280);
}

function captureModeSnapshot(mode = state.currentMode) {
  if (!MODE_CONFIG[mode] || !$('#brief-input')) return;
  const previous = state.modeSnapshots[mode] || {};
  const next = {
    brief: $('#brief-input').value,
    model: $('#param-model').value,
    angle: $('#param-angle').value,
    fidelity: Number($('#param-fidelity').value),
    batch: Number($('#param-batch').value),
    platter: getPlatter(),
    refine: $('#param-refine').checked,
    intent_locks: getIntentLocks(),
    active_job_id: previous.active_job_id || state.workspaceDrafts[mode]?.active_job_id || null,
    current_generation_id: previous.current_generation_id || state.workspaceDrafts[mode]?.current_generation_id || null,
    current_result_asset_id: previous.current_result_asset_id || state.workspaceDrafts[mode]?.current_result_asset_id || null,
    compare_state: previous.compare_state || state.workspaceDrafts[mode]?.compare_state || {},
    ui_state: previous.ui_state || state.workspaceDrafts[mode]?.ui_state || {},
    mask_state: previous.mask_state || state.workspaceDrafts[mode]?.mask_state || {},
  };
  state.modeSnapshots[mode] = next;
  if (JSON.stringify(previous) !== JSON.stringify(next)) scheduleWorkspaceDraftSave(mode);
}

function modeBrief(mode) {
  if (mode === state.currentMode && $('#brief-input')) return buildBrief(mode);
  const snapshot = state.modeSnapshots[mode] || {};
  return {
    objective: snapshot.brief || '将产品原图转化为可交付的商业图片',
    user_request: snapshot.brief || '',
    mode,
    category: 'general',
    platform: 'ecommerce',
    output_kind: MODE_CONFIG[mode].outputKind,
    angle: snapshot.angle || 'auto',
    platter: snapshot.platter || 'auto',
    fidelity: Number(snapshot.fidelity ?? 40),
    intent_locks: snapshot.intent_locks || {},
  };
}

function draftSavePayload(mode) {
  return draftPayloadFromSnapshot({
    revision: state.workspaceRevisions[mode],
    selectedAssetIds: selectedAssetIds(mode),
    snapshot: state.modeSnapshots[mode],
    brief: modeBrief(mode),
  });
}

function scheduleWorkspaceDraftSave(mode = state.currentMode, delay = 320) {
  if (state.hydratingWorkspace || !state.backendReady || !MODE_CONFIG[mode]) return;
  state.draftSaveVersions[mode] += 1;
  const previous = state.draftSaveTimers.get(mode);
  if (previous) window.clearTimeout(previous);
  state.draftSaveTimers.set(mode, window.setTimeout(() => {
    state.draftSaveTimers.delete(mode);
    flushWorkspaceDraft(mode, true);
  }, delay));
}

async function flushWorkspaceDraft(mode = state.currentMode, silent = false) {
  if (!state.backendReady || !MODE_CONFIG[mode]) return null;
  const timer = state.draftSaveTimers.get(mode);
  if (timer) window.clearTimeout(timer);
  state.draftSaveTimers.delete(mode);
  if (state.draftSavesInFlight.has(mode)) {
    state.draftSaveQueued.add(mode);
    return null;
  }
  state.draftSavesInFlight.add(mode);
  const saveVersion = state.draftSaveVersions[mode];
  try {
    const response = await API.saveWorkspaceDraft(mode, draftSavePayload(mode));
    const draft = response?.draft || response;
    state.workspaceDrafts[mode] = draft;
    state.workspaceRevisions[mode] = Number(draft?.revision || state.workspaceRevisions[mode]);
    return draft;
  } catch (error) {
    if (Number(error?.status) === 409 && error?.detail?.current) {
      const current = error.detail.current;
      state.workspaceDrafts[mode] = current;
      state.workspaceRevisions[mode] = Number(current.revision || 1);
      state.draftSaveQueued.add(mode);
    } else if (!silent) {
      toast(`草稿保存失败：${formatApiError(error, '工作区暂不可写')}`, 'error');
    }
    return null;
  } finally {
    state.draftSavesInFlight.delete(mode);
    if (state.draftSaveQueued.delete(mode) || saveVersion !== state.draftSaveVersions[mode]) {
      scheduleWorkspaceDraftSave(mode, 0);
    }
  }
}

function hydrateWorkspace(mode, payload) {
  const draft = payload?.draft || {};
  const collection = payload?.collection || MODE_CONFIG[mode].collection;
  const assets = Array.isArray(payload?.assets) ? payload.assets : [];
  const activeAssetIds = new Set(assets.map((asset) => String(asset.id)));
  state.hydratingWorkspace = true;
  try {
    state.workspaceDrafts[mode] = draft;
    state.workspaceRevisions[mode] = Number(draft.revision || 1);
    state.modeSelections[mode] = Array.from(draft.selected_asset_ids || [], String)
      .filter((assetId) => activeAssetIds.has(assetId))
      .slice(0, MODE_CONFIG[mode].maxFiles);
    state.modeSnapshots[mode] = snapshotFromDraft(draft, state.modeSnapshots[mode] || {});
    state.assetsByCollection[collection] = assets;
    if (MODE_CONFIG[state.currentMode]?.collection === collection) state.assets = assets;
    state.workspaceLoaded.add(mode);
    state.restoredModes.add(mode);
  } finally {
    state.hydratingWorkspace = false;
  }
}

async function loadWorkspace(mode = state.currentMode, silent = false) {
  if (!MODE_CONFIG[mode]) return false;
  const requestVersion = ++state.workspaceRequestVersions[mode];
  try {
    const payload = await API.getWorkspace(mode, { timeoutMs: 12000 });
    if (requestVersion !== state.workspaceRequestVersions[mode]) return null;
    hydrateWorkspace(mode, payload);
    await hydrateAssetUrls(payload.assets || []);
    const currentCollectionMatches = MODE_CONFIG[state.currentMode]?.collection === (payload.collection || MODE_CONFIG[mode].collection);
    if (mode === state.currentMode || currentCollectionMatches) {
      state.assets = state.assetsByCollection[MODE_CONFIG[state.currentMode].collection] || [];
      state.assetsAvailable = true;
      state.hydratingWorkspace = true;
      try {
        if (mode === state.currentMode) restoreModeSnapshot(mode);
        syncLegacySelection();
        renderQueue();
        updateQuickControls();
      } finally {
        state.hydratingWorkspace = false;
      }
      if (mode === state.currentMode) {
        const activeJob = payload.active_jobs?.[0] || null;
        state.currentTaskId = draftActiveJobId(mode) || activeJob?.id || state.currentTaskId;
        await restoreWorkspaceResult(mode, payload);
      }
    }
    return true;
  } catch (error) {
    if (requestVersion !== state.workspaceRequestVersions[mode]) return null;
    if (mode === state.currentMode) state.assetsAvailable = false;
    if (!silent) toast(`工作区读取失败：${formatApiError(error, '持久工作区接口不可用')}`, 'error', 6000);
    return false;
  }
}

function draftActiveJobId(mode = state.currentMode) {
  return state.workspaceDrafts[mode]?.active_job_id || state.modeSnapshots[mode]?.active_job_id || '';
}

async function restoreWorkspaceResult(mode, payload) {
  if (mode !== state.currentMode || !Array.isArray(payload?.jobs)) return false;
  const preferredId = payload?.draft?.active_job_id || '';
  const preferredResultId = payload?.draft?.current_result_asset_id || '';
  const job = payload.jobs.find((entry) => entry.id === preferredId && resultIdsForJob(entry).length)
    || payload.jobs.find((entry) => preferredResultId && resultIdsForJob(entry).includes(preferredResultId))
    || payload.jobs.find((entry) => resultIdsForJob(entry).length);
  if (!job) return false;
  const resultIds = resultIdsForJob(job);
  const recentById = new Map((payload.recent_results || []).map((asset) => [asset.id, asset]));
  const assets = resultIds.map((assetId, index) => {
    const asset = recentById.get(assetId) || {};
    return {
      asset_id: assetId,
      name: asset.name || `product-atelier-${index + 1}.${mode === 'cutout-batch' ? 'png' : 'jpg'}`,
      url: asset.content_url || '',
      content_url: asset.content_url || '',
      thumbnail_url: asset.thumbnail_url || '',
      role: asset.role || (mode === 'cutout-batch' ? 'result_cutout' : 'result_main'),
      id: assetId,
    };
  });
  await hydrateAssetUrls(assets);
  const source = state.assets.find((asset) => asset.id === job.items?.[0]?.source_asset_id)
    || selectedAssets(mode)[0];
  state.originalDataUrl = assetUrl(source, 'content');
  state.currentTaskId = job.id;
  state.currentSessionId = job.session_id || '';
  state.currentGenerationId = job.items?.[0]?.generation_id || '';
  state.results = {
    main: assets.filter((item) => item.role !== 'result_cutout'),
    cutout: assets.filter((item) => item.role === 'result_cutout'),
  };
  state.resultTab = mode === 'cutout-batch' || !state.results.main.length ? 'cutout' : 'main';
  state.viewerIndex = Math.max(0, resultIds.indexOf(preferredResultId));
  renderResults();
  setStage('success');
  return true;
}

function restoreModeSnapshot(mode = state.currentMode) {
  const snapshot = state.modeSnapshots[mode];
  if (!snapshot) return;
  $('#brief-input').value = snapshot.brief || '';
  if (snapshot.model && $(`#param-model option[value="${CSS.escape(snapshot.model)}"]`)) $('#param-model').value = snapshot.model;
  if (snapshot.angle && $(`#param-angle option[value="${CSS.escape(snapshot.angle)}"]`)) $('#param-angle').value = snapshot.angle;
  if (Number.isFinite(snapshot.fidelity)) $('#param-fidelity').value = String(snapshot.fidelity);
  if (Number.isFinite(snapshot.batch)) $('#param-batch').value = String(snapshot.batch);
  if (snapshot.platter) {
    const platter = $(`input[name="platter"][value="${CSS.escape(snapshot.platter)}"]`);
    if (platter) platter.checked = true;
  }
  $('#param-refine').checked = snapshot.refine !== false;
  $$('[data-lock]').forEach((input) => {
    input.checked = Boolean(snapshot.intent_locks?.[input.dataset.lock]);
    input.closest('.lock-chip').classList.toggle('active', input.checked);
  });
}

function assetUrl(asset, kind = 'thumbnail') {
  if (!asset) return '';
  const relative = kind === 'content' ? asset.content_url : asset.thumbnail_url;
  if (/^https?:\/\//.test(relative || '')) return relative;
  const cached = state.assetUrls.get(`${asset.id}:${kind}`);
  return cached || relative || '';
}

async function hydrateAssetUrls(assets) {
  await Promise.all((assets || []).map(async (asset) => {
    try {
      const [content, thumbnail] = await Promise.all([
        API.getAssetContentUrl(asset.id), API.getAssetThumbnailUrl(asset.id, 360),
      ]);
      state.assetUrls.set(`${asset.id}:content`, content);
      state.assetUrls.set(`${asset.id}:thumbnail`, thumbnail);
      asset.content_url = content;
      asset.thumbnail_url = thumbnail;
    } catch (_) { /* the API response may already contain absolute URLs */ }
  }));
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
  state.backendReady = status === 'connected';
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
  if (page !== 'process') workflowDock.close(false);
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

function clearSession(keepMode = true) {
  captureModeSnapshot();
  state.modeSelections[state.currentMode] = [];
  syncLegacySelection();
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
  $('#summary-result').textContent = '等待选择任务素材';
  $('#summary-result-note').textContent = '共享素材和已有任务不会被清空';
  $('#knowledge-summary').textContent = '等待知识编译';
  renderKnowledge(null);
  state.modeSnapshots[state.currentMode] = null;
  if (!keepMode) switchMode('single', false);
  renderQueue();
  persistWorkspaceState();
  scheduleWorkspaceDraftSave(state.currentMode, 0);
  updateCtaState();
}

function switchMode(mode, preserveCurrent = true, loadDurable = true) {
  if (!MODE_CONFIG[mode]) return;
  if (preserveCurrent && state.currentMode !== mode) captureModeSnapshot();
  state.currentMode = mode;
  state.assets = state.assetsByCollection[MODE_CONFIG[mode].collection] || [];
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
  const quickCutout = mode === 'cutout-batch';
  $('#creative-command').hidden = quickCutout;
  $('#cutout-capability').hidden = !quickCutout;
  $('#field-model').hidden = quickCutout;
  $('#field-composition').hidden = quickCutout;
  $('#field-intent').hidden = quickCutout;
  $('#btn-knowledge-card').disabled = quickCutout;
  $('#btn-knowledge-card').title = quickCutout
    ? '快速去背景当前不读取知识库或文字描述'
    : '查看本次知识介入';
  $('#field-refine').hidden = mode !== 'group-split';
  $('#batch-field-label').firstChild.textContent = mode === 'multi-file' ? '每图方案数 ' : '生成方案数 ';
  if (mode === 'multi-file' && $('#param-model').value === 'gpt-image-2') $('#model-reason').textContent = '每张独立处理';
  else if (mode === 'cutout-batch') $('#model-reason').textContent = '本地 BiRefNet';
  else $('#model-reason').textContent = '质量优先';
  state.resultTab = mode === 'cutout-batch' ? 'cutout' : 'main';
  state.hydratingWorkspace = true;
  try {
    restoreModeSnapshot(mode);
    syncLegacySelection();
    renderQueue();
    renderFileMeta();
    updateQuickControls();
  } finally {
    state.hydratingWorkspace = false;
  }
  persistWorkspaceState();
  updateCtaState();
  if (state.backendReady && loadDurable) loadWorkspace(mode, true);
  if (quickCutout) renderCutoutCapability();
  else {
    state.knowledgeBundle = null;
    renderKnowledge(null);
  }
}

function renderFileMeta() {
  const selection = selectedAssets();
  const count = selection.length;
  $('#asset-count').textContent = `${state.assets.length} 素材`;
  $('#btn-replace').hidden = false;
  $('#btn-replace').disabled = state.importing;
  $('#btn-clear').hidden = count === 0;
  if (count) {
    $('#info-filename').textContent = count === 1 ? selection[0].name : `${count} 张源图已选中`;
    $('#ready-count').textContent = `${count} SOURCE${count > 1 ? 'S' : ''} READY`;
  } else {
    $('#info-filename').textContent = '从素材工作台选择任务输入';
    $('#ready-count').textContent = '0 SELECTED';
  }
}

function renderQueue() {
  const ready = $('#canvas-image');
  const queue = $('#file-queue');
  ready.classList.add('is-queue', 'is-workspace');
  if (!state.assets.length) {
    queue.innerHTML = '';
    setStage('empty');
    renderFileMeta();
    return;
  }
  const selection = new Set(selectedAssetIds());
  queue.innerHTML = state.assets.map((asset, index) => {
    const selected = selection.has(asset.id);
    const dimensions = asset.width && asset.height ? `${asset.width}×${asset.height}` : '已持久化';
    return `<article class="queue-item asset-card ${selected ? 'selected' : ''}">
      <button class="asset-card__select" type="button" data-asset-id="${escapeHtml(asset.id)}" aria-pressed="${selected}" aria-label="${selected ? '取消选择' : '选择'} ${escapeHtml(asset.name)}">
        <span class="asset-card__visual"><img src="${escapeHtml(assetUrl(asset))}" alt="" loading="lazy" /><span class="asset-card__check" aria-hidden="true">${selected ? '✓' : '+'}</span></span>
        <span class="asset-card__meta"><strong title="${escapeHtml(asset.name)}">${escapeHtml(asset.name || `素材 ${index + 1}`)}</strong><small>${escapeHtml(dimensions)}</small></span>
      </button>
      <button class="asset-card__remove" type="button" data-remove-asset-id="${escapeHtml(asset.id)}" aria-label="将 ${escapeHtml(asset.name)} 移入回收站" title="移入回收站"><svg viewBox="0 0 24 24"><path d="M3 6h18M8 6V4h8v2M19 6l-1 15H6L5 6M10 11v6M14 11v6"/></svg></button>
    </article>`;
  }).join('');
  $$('[data-asset-id]', queue).forEach((button) => button.addEventListener('click', () => toggleAssetSelection(button.dataset.assetId)));
  $$('[data-remove-asset-id]', queue).forEach((button) => button.addEventListener('click', () => removeWorkspaceAsset(button.dataset.removeAssetId)));
  setStage('ready');
  renderFileMeta();
}

async function handleFiles(fileList) {
  const incoming = Array.from(fileList || []);
  if (!incoming.length) return;
  if (state.importing) { toast('上一批素材仍在导入，请稍候', 'error'); return; }
  const importMode = state.currentMode;
  const valid = incoming.filter((file) => {
    if (!/^image\/(png|jpeg|webp)$/i.test(file.type)) { toast(`${file.name} 不是支持的图片格式`, 'error'); return false; }
    if (file.size > 20 * 1024 * 1024) { toast(`${file.name} 超过 20 MB`, 'error'); return false; }
    return true;
  });
  if (!valid.length) return;
  if (valid.length > 100) { toast('一次最多导入 100 张素材', 'error'); return; }
  state.importing = true;
  renderFileMeta();
  $('#btn-browse').disabled = true;
  toast(`正在导入 ${valid.length} 张素材…`);
  try {
    const result = await API.importAssets(valid, MODE_CONFIG[importMode].collection);
    const imported = Array.isArray(result?.assets) ? result.assets : [];
    const errors = Array.isArray(result?.errors) ? result.errors : [];
    await loadWorkspace(importMode, true);
    const config = MODE_CONFIG[importMode];
    state.modeSelections[importMode] = selectionAfterImport(
      selectedAssetIds(importMode),
      imported.map((asset) => asset.id),
      config,
    );
    scheduleWorkspaceDraftSave(importMode, 0);
    await flushWorkspaceDraft(importMode, true);
    syncLegacySelection();
    if (state.currentMode === importMode) renderQueue();
    persistWorkspaceState();
    $('#summary-result').textContent = `${imported.length} 张素材已写入工作台`;
    $('#summary-result-note').textContent = '切换模式或重启后仍可继续使用';
    if (errors.length) toast(`${imported.length} 张导入成功，${errors.length} 张失败`, 'error', 5200);
    else toast(`已导入 ${imported.length} 张素材`, 'success');
    updateCtaState();
    await compileKnowledgePreview();
  } catch (error) {
    state.assetsAvailable = false;
    toast(`素材导入失败：${formatApiError(error, '持久素材接口不可用')}`, 'error', 6000);
  } finally {
    state.importing = false;
    $('#btn-browse').disabled = false;
    $('#file-input').value = '';
    renderFileMeta();
  }
}

function toggleAssetSelection(assetId) {
  const config = MODE_CONFIG[state.currentMode];
  const current = selectedAssetIds();
  const selected = current.includes(assetId);
  let next;
  if (selected) next = current.filter((id) => id !== assetId);
  else if (!config.multiple) next = [assetId];
  else if (current.length >= config.maxFiles) {
    toast(`当前模式最多选择 ${config.maxFiles} 张`, 'error');
    return;
  } else next = [...current, assetId];
  state.modeSelections[state.currentMode] = next;
  syncLegacySelection();
  renderQueue();
  persistWorkspaceState();
  scheduleWorkspaceDraftSave(state.currentMode);
  updateCtaState();
  scheduleKnowledgeCompile();
}

async function loadAssets(silent = false) {
  return loadWorkspace(state.currentMode, silent);
}

async function removeWorkspaceAsset(assetId) {
  if (!assetId || !state.backendReady) return;
  const mode = state.currentMode;
  const collection = MODE_CONFIG[mode].collection;
  try {
    await API.removeAssetFromCollection(collection, assetId);
    state.modeSelections[mode] = selectedAssetIds(mode).filter((id) => id !== assetId);
    await loadWorkspace(mode, true);
    scheduleWorkspaceDraftSave(mode, 0);
    await flushWorkspaceDraft(mode, true);
    toast('素材已移入回收站，可在素材管理中恢复', 'success');
  } catch (error) {
    toast(`移除失败：${formatApiError(error, '素材接口不可用')}`, 'error');
  }
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

function buildBrief(mode = state.currentMode) {
  const request = $('#brief-input').value.trim();
  return {
    objective: request || '将产品原图转化为可交付的商业图片',
    user_request: request,
    mode,
    category: 'general',
    platform: 'ecommerce',
    output_kind: MODE_CONFIG[mode].outputKind,
    angle: $('#param-angle').value,
    platter: getPlatter(),
    fidelity: Number($('#param-fidelity').value),
    intent_locks: getIntentLocks(),
    output_spec: { ratio: '1:1', size: '2048x2048', format: mode === 'cutout-batch' ? 'transparent PNG' : 'JPG+transparent PNG' },
  };
}

let compileTimer = null;
async function compileKnowledgePreview(context = null) {
  const mode = context?.mode || state.currentMode;
  if (mode === 'cutout-batch') {
    if (mode === state.currentMode) renderCutoutCapability();
    return null;
  }
  const brief = context?.brief || buildBrief(mode);
  const hasInput = Boolean(selectedAssetIds(mode).length || brief.user_request || context?.force);
  if (!hasInput) return null;
  const requestVersion = ++state.knowledgeRequestVersion;
  try {
    const bundle = await API.compileKnowledge(brief);
    if (requestVersion === state.knowledgeRequestVersion && mode === state.currentMode) {
      state.knowledgeBundle = bundle;
      renderKnowledge(bundle);
    }
    return bundle;
  } catch (error) {
    if (requestVersion === state.knowledgeRequestVersion && mode === state.currentMode) {
      $('#knowledge-summary').textContent = '知识编译暂不可用，使用安全默认值';
    }
    return null;
  }
}

function scheduleKnowledgeCompile() {
  window.clearTimeout(compileTimer);
  captureModeSnapshot();
  persistWorkspaceState();
  compileTimer = window.setTimeout(compileKnowledgePreview, 480);
}

function renderKnowledge(bundle) {
  if (state.currentMode === 'cutout-batch') {
    renderCutoutCapability();
    return;
  }
  if (!bundle) {
    $('#knowledge-summary').textContent = '等待知识编译';
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

function renderCutoutCapability() {
  $('#knowledge-summary').textContent = '本地分割 · 不读取文字描述';
  $('#knowledge-source-count').textContent = '0 条执行知识';
  $('#knowledge-source-list').innerHTML = '<div class="page-empty">快速去背景只执行本地前景分割。</div>';
  $('#knowledge-conflicts').innerHTML = '<div class="conflict-item ok"><span>i</span><p>需要按名称或数量选物时，请等待“智能选物”工作流。</p></div>';
  $('#intelligence-brief').textContent = '当前能力：分离全部前景';
  $('#intelligence-context').textContent = '文字描述、物体数量和知识规则不会进入本次执行链。';
}

function updateCtaState() {
  const button = $('#btn-generate');
  const count = selectedAssetIds().length;
  const hasFiles = count > 0;
  button.disabled = !hasFiles || state.submitting || !state.assetsAvailable;
  button.classList.toggle('loading', state.submitting);
  if (state.submitting) $('#generate-text').textContent = '正在加入任务 Dock';
  else if (!hasFiles) $('#generate-text').textContent = '选择图片开始';
  else $('#generate-text').textContent = MODE_CONFIG[state.currentMode].action;
  if (!hasFiles) $('#cta-hint').textContent = state.currentMode === 'cutout-batch'
    ? '从抠图素材中选择后可入队'
    : '从当前素材区选择后可入队';
  else if (state.currentMode === 'cutout-batch') $('#cta-hint').textContent = `${count} 张素材 · 本地分离全部前景`;
  else $('#cta-hint').textContent = `${count} 张素材 · ${Object.values(getIntentLocks()).filter(Boolean).length} 项锁定`;
}

function updateQuickControls() {
  const angleLabels = { auto: 'Auto', keep: 'Locked', front: 'Front', '45top': '45° Top', '30side': '30° Side', '90top': 'Top' };
  $('#quick-angle').textContent = angleLabels[$('#param-angle').value] || $('#param-angle').value;
  $('#quick-fidelity').textContent = `${$('#param-fidelity').value}%`;
  $('#quick-batch').textContent = state.currentMode === 'multi-file' ? `${$('#param-batch').value} / file` : $('#param-batch').value;
  $('#fid-val').textContent = `${$('#param-fidelity').value}%`;
  $('#batch-val').textContent = $('#param-batch').value;
  captureModeSnapshot();
  persistWorkspaceState();
  updateCtaState();
  scheduleKnowledgeCompile();
}

function captureSubmissionDraft() {
  const mode = state.currentMode;
  const brief = buildBrief(mode);
  return createSubmissionSnapshot({
    mode,
    sourceAssetIds: selectedAssetIds(mode),
    parameters: {
      model: $('#param-model').value,
      variations: Number($('#param-batch').value),
      batch: Number($('#param-batch').value),
      platter: getPlatter(),
      fidelity: Number($('#param-fidelity').value),
      angle: $('#param-angle').value,
      refine: $('#param-refine').checked,
      brief,
      intent_locks: getIntentLocks(),
      category: 'general',
    },
  });
}

async function compileSubmissionPayload(draft) {
  const fingerprint = submissionFingerprint(draft);
  const pending = state.pendingSubmission;
  if (
    pending?.fingerprint === fingerprint
    && pending.payload?.client_request_id === pending.requestId
  ) return pending.payload;

  const bundle = await compileKnowledgePreview({
    mode: draft.mode,
    brief: draft.parameters.brief,
    force: true,
  });
  const requestId = createClientRequestId();
  const payload = {
    ...createSubmissionSnapshot({
      mode: draft.mode,
      sourceAssetIds: draft.source_asset_ids,
      parameters: {
        ...draft.parameters,
        knowledge_refs: bundle?.sources || [],
      },
    }),
    client_request_id: requestId,
  };
  persistPendingSubmission({ fingerprint, requestId, payload });
  return payload;
}

async function handleGenerate() {
  if (state.submitting) return;
  const submissionDraft = captureSubmissionDraft();
  if (!submissionDraft.source_asset_ids.length) return;
  state.submitting = true;
  updateCtaState();
  let payload = null;
  try {
    captureModeSnapshot(submissionDraft.mode);
    let savedDraft = await flushWorkspaceDraft(submissionDraft.mode, false);
    if (!savedDraft) savedDraft = await flushWorkspaceDraft(submissionDraft.mode, false);
    if (!savedDraft) throw new Error('当前工作草稿未能安全保存，请重试');
    payload = await compileSubmissionPayload(submissionDraft);
    const response = await API.createJob(payload);
    clearPendingSubmission(payload.client_request_id);
    const job = response?.job || response;
    const jobId = job?.id || response?.job_id || '';
    const sessionId = job?.session_id || response?.session_id || '';
    const modeSnapshot = state.modeSnapshots[submissionDraft.mode] || {};
    state.modeSnapshots[submissionDraft.mode] = { ...modeSnapshot, active_job_id: jobId };
    if (state.workspaceDrafts[submissionDraft.mode]) {
      state.workspaceDrafts[submissionDraft.mode] = {
        ...state.workspaceDrafts[submissionDraft.mode],
        active_job_id: jobId,
      };
    }
    scheduleWorkspaceDraftSave(submissionDraft.mode, 0);
    if (state.currentMode === submissionDraft.mode) {
      state.currentTaskId = jobId;
      state.currentSessionId = sessionId;
      $('#summary-result').textContent = `${submissionDraft.source_asset_ids.length} 项任务已入队`;
      $('#summary-result-note').textContent = '可继续选素材、切换模式或发起新任务';
    }
    toast('任务已加入 Dock，可继续组织素材', 'success');
    await loadJobs(true);
    workflowDock.close(false);
    openDrawer('jobs');
  } catch (error) {
    if (payload && isDefinitiveJobRejection(error)) clearPendingSubmission(payload.client_request_id);
    toast(`任务提交失败：${formatApiError(error, '持久任务接口不可用')}`, 'error', 6500);
  } finally {
    state.submitting = false;
    updateCtaState();
  }
}

function jobProgress(job) {
  return jobCompletionProgress(job);
}

function jobCounts(job) {
  const items = Array.isArray(job?.items) ? job.items : [];
  const count = (status) => items.filter((item) => item.status === status).length;
  const completed = Number(job.completed_items ?? count('completed'));
  const failed = Number(job.failed_items ?? count('failed'));
  const decided = completed + failed;
  return {
    total: Number(job.total_items ?? items.length ?? 0),
    completed,
    failed,
    canceled: Number(job.canceled_items ?? count('canceled')),
    successRate: decided ? Math.round((completed / decided) * 100) : null,
  };
}

function resultIdsForJob(job) {
  return [...new Set((job?.items || []).flatMap((item) => Array.isArray(item.result_asset_ids) ? item.result_asset_ids : []))];
}

function renderJobDockSummary() {
  const jobs = state.jobs;
  const ongoing = jobs.filter((job) => ['queued', 'running', 'paused', 'canceling', 'interrupted'].includes(job.status));
  const progressing = ongoing.filter((job) => job.status !== 'paused');
  const paused = ongoing.filter((job) => job.status === 'paused');
  const progressScope = ongoing.length ? ongoing : jobs;
  const items = progressScope.flatMap((job) => Array.isArray(job.items) ? job.items : []);
  const overall = queueCompletionProgress(progressScope);
  const completed = items.filter((item) => item.status === 'completed').length;
  const failed = items.filter((item) => item.status === 'failed').length;
  const decided = completed + failed;
  const outcomeCopy = decided ? ` · 已结束项成功率 ${Math.round((completed / decided) * 100)}%` : '';
  const percent = Math.round(overall * 100);
  $('#job-dock-progress').textContent = `${percent}%`;
  $('#job-dock-summary').textContent = state.jobsAvailable
    ? (ongoing.length
      ? `${ongoing.length} 个未结束${paused.length ? ` · ${paused.length} 个已暂停` : ''} · ${jobs.length} 个记录`
      : (jobs.length ? `${jobs.length} 个任务已恢复` : '暂无任务'))
    : '任务接口未就绪';
  $('#job-dock-dot').className = `job-dock-dot ${progressing.length ? 'active' : ''} ${paused.length && !progressing.length ? 'paused' : ''} ${state.jobsAvailable ? '' : 'error'}`.trim();
  $('#jobs-overall-percent').textContent = `${percent}%`;
  $('#jobs-overall-bar').style.width = `${percent}%`;
  $('#jobs-overall-copy').textContent = progressing.length
    ? `${progressing.length} 个任务正在推进${paused.length ? `，${paused.length} 个已暂停` : ''}，可继续编排新素材${outcomeCopy}`
    : (paused.length
      ? `${paused.length} 个任务已暂停，继续后将领取剩余排队项${outcomeCopy}`
      : (jobs.length ? `任务状态已从本地账本恢复${outcomeCopy}` : '新任务会在这里持续追踪'));
}

const MUTATING_JOB_ACTIONS = new Set(['pause', 'resume', 'cancel', 'retry-item', 'retry-failed']);

function jobActionKey(action, jobId = '', itemId = '') {
  return `${action}:${jobId}:${itemId}`;
}

function jobActionDisabled(action, jobId = '', itemId = '') {
  return state.jobActionsInFlight.has(jobActionKey(action, jobId, itemId))
    || (MUTATING_JOB_ACTIONS.has(action) && state.jobMutationsInFlight.has(jobId));
}

function captureJobListView(list) {
  const active = document.activeElement;
  const action = active instanceof HTMLElement ? active.closest('[data-job-action]') : null;
  const card = active instanceof HTMLElement ? active.closest('.job-card') : null;
  return {
    scrollTop: list.scrollTop,
    itemScroll: new Map($$('.job-card', list).map((jobCard) => [
      jobCard.dataset.jobId,
      $('.job-items', jobCard)?.scrollTop || 0,
    ])),
    focus: action ? {
      kind: 'action',
      action: action.dataset.jobAction || '',
      jobId: action.dataset.jobId || '',
      itemId: action.dataset.itemId || '',
    } : (card ? { kind: 'card', jobId: card.dataset.jobId || '' } : null),
  };
}

function restoreJobListView(list, view) {
  list.scrollTop = view.scrollTop;
  $$('.job-card', list).forEach((card) => {
    const items = $('.job-items', card);
    if (items) items.scrollTop = view.itemScroll.get(card.dataset.jobId) || 0;
  });
  if (!view.focus) return;
  let target = null;
  if (view.focus.kind === 'action') {
    target = $$('[data-job-action]', list).find((button) => (
      (button.dataset.jobAction || '') === view.focus.action
      && (button.dataset.jobId || '') === view.focus.jobId
      && (button.dataset.itemId || '') === view.focus.itemId
    ));
    if (target?.disabled) target = null;
  }
  if (!target && view.focus.jobId) {
    target = $$('.job-card', list).find((card) => card.dataset.jobId === view.focus.jobId);
  }
  target?.focus({ preventScroll: true });
}

function renderJobs(force = false) {
  const signature = jobsRenderSignature(
    state.jobs,
    state.jobsAvailable,
    state.jobActionsInFlight,
  );
  if (!force && signature === state.jobsRenderSignature) return;
  renderJobDockSummary();
  const list = $('#job-list');
  const view = captureJobListView(list);
  if (!state.jobsAvailable) {
    list.innerHTML = `<div class="job-empty job-empty--error"><strong>持久任务接口暂不可用</strong><p>请确认后端已提供 /api/jobs；素材与当前选择不会被清空。</p><button class="secondary-button" type="button" data-job-action="refresh" ${jobActionDisabled('refresh') ? 'disabled aria-busy="true"' : ''}>重试连接</button></div>`;
  } else if (!state.jobs.length) {
    list.innerHTML = '<div class="job-empty"><strong>还没有任务</strong><p>从素材工作台选择图片并入队后，进度会在此从后端恢复。</p></div>';
  } else {
    list.innerHTML = state.jobs.map((job) => {
      const status = JOB_STATUS[job.status] || { label: job.status || '未知', tone: 'unknown' };
      const progress = Math.round(jobProgress(job) * 100);
      const counts = jobCounts(job);
      const retryable = (job.items || []).filter((item) => ['failed', 'interrupted'].includes(item.status));
      const hasResults = resultIdsForJob(job).length > 0;
      const lifecycleActions = jobLifecycleActions(job.status);
      const canPause = lifecycleActions.includes('pause');
      const canResume = lifecycleActions.includes('resume');
      const canCancel = lifecycleActions.includes('cancel');
      const items = (job.items || []).map((item, index) => {
        const source = state.assets.find((asset) => asset.id === item.source_asset_id);
        const itemStatus = JOB_STATUS[item.status] || { label: item.status || '未知', tone: 'unknown' };
        const itemProgress = Math.round(itemCompletionProgress(item) * 100);
        const error = item.error_message || item.error || '';
        const canRetryItem = ['failed', 'interrupted'].includes(item.status);
        return `<li class="job-item job-item--${escapeHtml(itemStatus.tone)}">
          <img src="${escapeHtml(assetUrl(source))}" alt="" loading="lazy" />
          <span class="job-item__copy"><strong>${escapeHtml(source?.name || `素材 ${index + 1}`)}</strong><span>${escapeHtml(itemStatus.label)} · 完成度 ${itemProgress}%</span>${error ? `<small title="${escapeHtml(error)}">${escapeHtml(error)}</small>` : ''}</span>
          <span class="job-item__bar"><i style="width:${itemProgress}%"></i></span>
          ${canRetryItem ? `<button type="button" data-job-action="retry-item" data-job-id="${escapeHtml(job.id)}" data-item-id="${escapeHtml(item.id)}" ${jobActionDisabled('retry-item', job.id, item.id) ? 'disabled aria-busy="true"' : ''}>重试</button>` : ''}
        </li>`;
      }).join('');
      return `<article class="job-card job-card--${escapeHtml(status.tone)}" data-job-id="${escapeHtml(job.id)}" tabindex="-1">
        <header><span><small>${escapeHtml(MODE_CONFIG[job.mode]?.badge || String(job.mode || '').toUpperCase())}</small><strong>${escapeHtml(MODE_CONFIG[job.mode]?.label || job.title || '创作任务')}</strong></span><span class="job-status job-status--${escapeHtml(status.tone)}">${escapeHtml(status.label)}</span></header>
        <div class="job-progress"><span><i style="width:${progress}%"></i></span><strong>${progress}%</strong></div>
        <div class="job-counts"><span>${counts.total} 项</span><span>${counts.completed} 成功</span><span>${counts.failed} 失败</span><span>${counts.canceled} 取消</span><span>成功率 ${counts.successRate === null ? '—' : `${counts.successRate}%`}</span><time>${escapeHtml(formatTime(job.updated_at || job.created_at))}</time></div>
        ${items ? `<ul class="job-items">${items}</ul>` : ''}
        <footer>
          ${hasResults ? `<button type="button" data-job-action="open-results" data-job-id="${escapeHtml(job.id)}" ${jobActionDisabled('open-results', job.id) ? 'disabled aria-busy="true"' : ''}>打开结果</button>` : ''}
          ${retryable.length ? `<button type="button" data-job-action="retry-failed" data-job-id="${escapeHtml(job.id)}" ${jobActionDisabled('retry-failed', job.id) ? 'disabled aria-busy="true"' : ''}>重试失败项</button>` : ''}
          ${canPause ? `<button type="button" data-job-action="pause" data-job-id="${escapeHtml(job.id)}" ${jobActionDisabled('pause', job.id) ? 'disabled aria-busy="true"' : ''}>暂停任务</button>` : ''}
          ${canResume ? `<button type="button" data-job-action="resume" data-job-id="${escapeHtml(job.id)}" ${jobActionDisabled('resume', job.id) ? 'disabled aria-busy="true"' : ''}>继续任务</button>` : ''}
          ${canCancel ? `<button class="danger" type="button" data-job-action="cancel" data-job-id="${escapeHtml(job.id)}" ${jobActionDisabled('cancel', job.id) ? 'disabled aria-busy="true"' : ''}>取消任务</button>` : ''}
        </footer>
      </article>`;
    }).join('');
  }
  $$('[data-job-action]', list).forEach((button) => button.addEventListener('click', () => handleJobAction(button)));
  state.jobsRenderSignature = signature;
  restoreJobListView(list, view);
}

function announceJobChanges(jobs) {
  jobs.forEach((job) => {
    const before = state.knownJobStatuses.get(job.id);
    state.knownJobStatuses.set(job.id, job.status);
    if (!before || before === job.status) return;
    let message = '';
    if (job.status === 'completed') { message = `${MODE_CONFIG[job.mode]?.label || '任务'}已完成`; toast(message, 'success'); }
    else if (job.status === 'partial') { message = `${MODE_CONFIG[job.mode]?.label || '任务'}部分完成，失败项可重试`; toast(message, 'error', 5200); }
    else if (job.status === 'failed') { message = `${MODE_CONFIG[job.mode]?.label || '任务'}失败，详情已保留`; toast(message, 'error', 5200); }
    else if (job.status === 'canceled') { message = `${MODE_CONFIG[job.mode]?.label || '任务'}已取消`; toast(message); }
    if (message) $('#job-status-announcer').textContent = message;
  });
}

function invalidateJobsRead() {
  state.jobsRequestVersion += 1;
  state.jobsAbortController?.abort();
  state.jobsAbortController = null;
}

async function loadJobs(silent = false) {
  const requestVersion = ++state.jobsRequestVersion;
  state.jobsAbortController?.abort();
  const controller = new AbortController();
  state.jobsAbortController = controller;
  try {
    const result = await API.getJobs(100, { signal: controller.signal, timeoutMs: 8000 });
    if (requestVersion !== state.jobsRequestVersion) return null;
    const jobs = Array.isArray(result) ? result : (result?.jobs || []);
    announceJobChanges(jobs);
    state.jobs = jobs;
    state.jobsAvailable = true;
    renderJobs();
    return true;
  } catch (error) {
    if (requestVersion !== state.jobsRequestVersion) return null;
    state.jobsAvailable = false;
    renderJobs();
    if (!silent) toast(`任务 Dock 读取失败：${formatApiError(error, '持久任务接口不可用')}`, 'error', 6000);
    return false;
  } finally {
    if (requestVersion === state.jobsRequestVersion) state.jobsAbortController = null;
  }
}

function startJobPolling() {
  if (state.jobPollTimer) window.clearTimeout(state.jobPollTimer);
  const tick = async () => {
    const ok = await loadJobs(true);
    const hasActive = state.jobs.some((job) => (
      ['queued', 'running', 'canceling', 'interrupted'].includes(job.status)
      || (job.status === 'paused' && (job.items || []).some((item) => item.status === 'running'))
    ));
    state.jobPollTimer = window.setTimeout(tick, ok !== false ? (hasActive ? 1100 : 4200) : 8500);
  };
  tick();
}

async function handleJobAction(button) {
  const action = button.dataset.jobAction;
  const jobId = button.dataset.jobId;
  const itemId = button.dataset.itemId || '';
  const actionKey = jobActionKey(action, jobId, itemId);
  const isMutation = MUTATING_JOB_ACTIONS.has(action);
  if (jobActionDisabled(action, jobId, itemId)) return;
  state.jobActionsInFlight.add(actionKey);
  if (isMutation) state.jobMutationsInFlight.add(jobId);
  button.disabled = true;
  if (isMutation) {
    $$('[data-job-action]', button.closest('.job-card')).forEach((control) => {
      if (MUTATING_JOB_ACTIONS.has(control.dataset.jobAction)) control.disabled = true;
    });
  }
  try {
    if (action === 'refresh') await loadJobs(false);
    else if (action === 'pause') { invalidateJobsRead(); await API.pauseJob(jobId); toast('已暂停新任务项领取'); }
    else if (action === 'resume') { invalidateJobsRead(); await API.resumeJob(jobId); toast('任务已继续', 'success'); }
    else if (action === 'cancel') { invalidateJobsRead(); await API.cancelJob(jobId); toast('已发出取消请求'); }
    else if (action === 'retry-item') { invalidateJobsRead(); await API.retryJob(jobId, [itemId]); toast('失败项已重新入队', 'success'); }
    else if (action === 'retry-failed') {
      invalidateJobsRead();
      const job = state.jobs.find((entry) => entry.id === jobId);
      const ids = (job?.items || []).filter((item) => ['failed', 'interrupted'].includes(item.status)).map((item) => item.id);
      await API.retryJob(jobId, ids);
      toast(`${ids.length} 个失败项已重新入队`, 'success');
    } else if (action === 'open-results') await openJobResults(jobId);
    await loadJobs(true);
  } catch (error) {
    toast(`任务操作失败：${formatApiError(error, '任务接口不可用')}`, 'error', 6000);
  } finally {
    state.jobActionsInFlight.delete(actionKey);
    if (isMutation) state.jobMutationsInFlight.delete(jobId);
    button.disabled = false;
    renderJobs(true);
  }
}

async function openJobResults(jobId) {
  const response = await API.getJob(jobId);
  const job = response?.job || response;
  const resultIds = resultIdsForJob(job);
  if (!resultIds.length) throw new Error('该任务还没有可打开的结果');
  const items = await Promise.all(resultIds.map(async (assetId, index) => {
    let asset = null;
    try { asset = await API.getAsset(assetId); } catch (_) { /* content URL remains useful */ }
    return {
      asset_id: assetId,
      name: asset?.name || `product-atelier-${index + 1}.${job.mode === 'cutout-batch' ? 'png' : 'jpg'}`,
      url: await API.getAssetContentUrl(assetId),
      role: asset?.role || (job.mode === 'cutout-batch' ? 'result_cutout' : 'result_main'),
    };
  }));
  if (job.mode && MODE_CONFIG[job.mode] && state.currentMode !== job.mode) {
    switchMode(job.mode, true, false);
    await loadWorkspace(job.mode, true);
  }
  const source = state.assets.find((asset) => asset.id === job.items?.[0]?.source_asset_id);
  state.originalDataUrl = assetUrl(source, 'content');
  state.currentTaskId = job.id;
  state.currentSessionId = job.session_id || '';
  state.currentGenerationId = job.items?.[0]?.generation_id || '';
  state.results = {
    main: items.filter((item) => item.role !== 'result_cutout'),
    cutout: items.filter((item) => item.role === 'result_cutout'),
  };
  state.resultTab = job.mode === 'cutout-batch' || !state.results.main.length ? 'cutout' : 'main';
  state.viewerIndex = 0;
  const modeSnapshot = state.modeSnapshots[job.mode] || {};
  state.modeSnapshots[job.mode] = {
    ...modeSnapshot,
    active_job_id: job.id,
    current_generation_id: state.currentGenerationId || null,
    current_result_asset_id: items[0]?.asset_id || null,
  };
  scheduleWorkspaceDraftSave(job.mode, 0);
  renderResults();
  setStage('success');
  switchPage('process');
  closeDrawer('jobs');
  $('#summary-result').textContent = `${items.length} 个结果已从任务账本恢复`;
  $('#summary-result-note').textContent = '可先检查成功项，再在任务 Dock 重试失败项';
}

function getResultItems(tab = state.resultTab) {
  return state.results && Array.isArray(state.results[tab]) ? state.results[tab] : [];
}

function getAllResultItems() {
  return collectResultItems(state.results);
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
  if (state.exporting) return;
  const items = getAllResultItems();
  if (!items.length) { toast('当前没有可导出的结果', 'error'); return; }
  const button = $('#btn-save-all');
  const previousLabel = button.textContent;
  state.exporting = true;
  button.disabled = true;
  button.textContent = '正在导出…';
  let outcome = { succeeded: [], failed: [] };
  try {
    outcome = await processResultItems(items, async (item, index) => {
      let data = item.data ? item.data.replace(/^data:[^,]+,/, '') : '';
      if (!data && (item.url || item.asset_id)) {
        const response = await fetch(item.url || await API.getAssetContentUrl(item.asset_id));
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        const bytes = new Uint8Array(await response.arrayBuffer());
        let binary = '';
        for (let offset = 0; offset < bytes.length; offset += 0x8000) binary += String.fromCharCode(...bytes.subarray(offset, offset + 0x8000));
        data = btoa(binary);
      }
      if (!data) throw new Error('结果内容为空');
      const isCutout = item.role === 'result_cutout';
      await API.saveImage(item.name || `product-atelier-${index + 1}.${isCutout ? 'png' : 'jpg'}`, data);
    });
  } finally {
    state.exporting = false;
    button.disabled = false;
    button.textContent = previousLabel;
  }
  const saved = outcome.succeeded.length;
  const failures = outcome.failed;
  if (saved && !failures.length) toast(`已导出全部 ${saved} 个结果`, 'success');
  else if (saved) toast(`已导出 ${saved} 个，${failures.length} 个失败`, 'error', 6000);
  else toast(`导出失败：${String(failures[0]?.error || '未知错误')}`, 'error', 6000);
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
  const drawers = { advanced: $('#advanced-drawer'), intelligence: $('#intelligence-drawer'), jobs: $('#job-drawer') };
  const layer = drawers[name];
  if (!layer) return;
  drawerReturnFocus = document.activeElement;
  layer.hidden = false;
  if (name === 'jobs') {
    state.jobDrawerOpen = true;
    $('#btn-job-dock').setAttribute('aria-expanded', 'true');
    loadJobs(true);
  }
  $('.drawer-close', layer)?.focus();
}

function closeDrawer(name) {
  const drawers = { advanced: $('#advanced-drawer'), intelligence: $('#intelligence-drawer'), jobs: $('#job-drawer') };
  const layer = drawers[name];
  if (!layer) return;
  layer.hidden = true;
  if (name === 'jobs') {
    state.jobDrawerOpen = false;
    $('#btn-job-dock').setAttribute('aria-expanded', 'false');
  }
  if (name === 'advanced') updateQuickControls();
  if (drawerReturnFocus instanceof HTMLElement) drawerReturnFocus.focus();
  drawerReturnFocus = null;
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
    const hasModeSnapshot = state.restoredModes.has(state.currentMode);
    if (settings.default_model) { $('#setting-model').value = settings.default_model; if (!hasModeSnapshot) $('#param-model').value = settings.default_model; }
    if (settings.default_platter) { if (!hasModeSnapshot) { const radio = $(`input[name="platter"][value="${settings.default_platter}"]`); if (radio) radio.checked = true; } $('#setting-platter').value = settings.default_platter; }
    if (settings.default_angle) { $('#setting-angle').value = settings.default_angle; if (!hasModeSnapshot) $('#param-angle').value = settings.default_angle; }
    if (settings.default_fidelity) { $('#setting-fidelity').value = settings.default_fidelity; if (!hasModeSnapshot) $('#param-fidelity').value = settings.default_fidelity; }
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
  $('#btn-error-reset').addEventListener('click', () => { setStage(state.selectedFiles.length ? 'ready' : 'empty'); updateCtaState(); });
  $('#brief-input').addEventListener('input', scheduleKnowledgeCompile);
  $('#param-model').addEventListener('change', updateQuickControls);
  $('#param-angle').addEventListener('change', updateQuickControls);
  $('#param-fidelity').addEventListener('input', updateQuickControls);
  $('#param-batch').addEventListener('input', updateQuickControls);
  $$('input[name="platter"]').forEach((radio) => radio.addEventListener('change', updateQuickControls));
  $('#param-refine').addEventListener('change', updateQuickControls);
  $$('[data-lock]').forEach((input) => input.addEventListener('change', () => { input.closest('.lock-chip').classList.toggle('active', input.checked); updateCtaState(); scheduleKnowledgeCompile(); }));
  $('#btn-advanced').addEventListener('click', () => openDrawer('advanced'));
  $('#btn-workflow-drawer').addEventListener('click', workflowDock.open);
  $('#task-dock-backdrop').addEventListener('click', () => workflowDock.close());
  $$('[data-open-advanced]').forEach((button) => button.addEventListener('click', () => openDrawer('advanced')));
  $('#btn-open-intelligence').addEventListener('click', () => openDrawer('intelligence'));
  $('#btn-job-dock').addEventListener('click', () => openDrawer('jobs'));
  $('#btn-refresh-jobs').addEventListener('click', async () => {
    const button = $('#btn-refresh-jobs');
    if (button.disabled) return;
    button.disabled = true;
    try { await loadJobs(false); } finally { button.disabled = false; }
  });
  $('#btn-knowledge-card').addEventListener('click', () => openDrawer('intelligence'));
  $$('[data-close-drawer]').forEach((button) => button.addEventListener('click', () => closeDrawer(button.dataset.closeDrawer)));
  $$('.result-tab').forEach((button) => button.addEventListener('click', () => { state.resultTab = button.dataset.rtab; state.viewerIndex = 0; renderResults(); }));
  $('#viewer-prev').addEventListener('click', () => { const items = getResultItems(); if (items.length) { state.viewerIndex = (state.viewerIndex - 1 + items.length) % items.length; renderResults(); } });
  $('#viewer-next').addEventListener('click', () => { const items = getResultItems(); if (items.length) { state.viewerIndex = (state.viewerIndex + 1) % items.length; renderResults(); } });
  $('#btn-open-compare').addEventListener('click', () => { renderCompare(); switchPage('compare'); });
  $('#btn-compare-back').addEventListener('click', () => switchPage('process'));
  $('#btn-save-all').addEventListener('click', saveCurrentResults);
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
  canvas.addEventListener('dragover', (event) => { event.preventDefault(); canvas.style.outline = '2px solid var(--coral)'; });
  canvas.addEventListener('dragleave', () => { canvas.style.outline = ''; });
  canvas.addEventListener('drop', (event) => { event.preventDefault(); canvas.style.outline = ''; handleFiles(event.dataTransfer.files); });
  document.addEventListener('keydown', (event) => {
    const workflowLayer = $('#settings-panel').classList.contains('is-open') ? $('#settings-panel') : null;
    const openLayer = [$('#img-modal'), $('#job-drawer'), $('#advanced-drawer'), $('#intelligence-drawer'), workflowLayer].find((layer) => layer && !layer.hidden);
    if (event.key === 'Tab' && openLayer) {
      const focusRoot = openLayer.id === 'img-modal' ? $('.modal-card', openLayer) : openLayer.id === 'settings-panel' ? openLayer : $('.drawer', openLayer);
      const focusable = $$('button:not(:disabled), input:not(:disabled), select:not(:disabled), [tabindex]:not([tabindex="-1"])', focusRoot).filter((element) => element.offsetParent !== null);
      const first = focusable[0];
      const last = focusable[focusable.length - 1];
      if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last?.focus(); }
      else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first?.focus(); }
    }
    if (event.key !== 'Escape') return;
    if (!$('#img-modal').hidden) closeModal();
    if (!$('#advanced-drawer').hidden) closeDrawer('advanced');
    if (!$('#intelligence-drawer').hidden) closeDrawer('intelligence');
    if (!$('#job-drawer').hidden) closeDrawer('jobs');
    if ($('#settings-panel').classList.contains('is-open')) workflowDock.close();
  });
  workflowDock.bind();
  window.addEventListener('beforeunload', () => {
    workflowDock.destroy();
    if (state.jobPollTimer) window.clearTimeout(state.jobPollTimer);
    state.draftSaveTimers.forEach((timer) => window.clearTimeout(timer));
    state.draftSaveTimers.clear();
    state.assetsRequestVersion += 1;
    state.assetsAbortController?.abort();
    invalidateJobsRead();
  });
  document.addEventListener('visibilitychange', () => {
    if (document.visibilityState === 'hidden' && state.backendReady) flushWorkspaceDraft(state.currentMode, true);
  });
}

async function connectBackend() {
  setBackendStatus('connecting', '连接中');
  for (let attempt = 0; attempt < 20; attempt += 1) {
    const health = await API.checkHealth();
    if (health.ok) {
      setBackendStatus('connected', '已连接');
      try { const status = await API.getKnowledgeStatus(); renderKnowledgeStatus(status); } catch (_) { /* keep app usable */ }
      await Promise.all([loadAssets(false), loadJobs(true)]);
      startJobPolling();
      return;
    }
    await new Promise((resolve) => window.setTimeout(resolve, 700));
  }
  setBackendStatus('disconnected', '未连接');
  state.assetsAvailable = false;
  state.jobsAvailable = false;
  renderJobs();
  updateCtaState();
  toast('本地服务尚未就绪，稍后可重试', 'error');
}

async function init() {
  setupTheme();
  bindEvents();
  workflowDock.sync();
  setupCompare();
  restoreWorkspaceState();
  restorePendingSubmission();
  switchMode(state.currentMode, false);
  setStage('empty');
  renderJobs();
  updateQuickControls();
  connectBackend();
  loadSettings();
}

document.addEventListener('DOMContentLoaded', init);
