// ============================================================
// Product Atelier - API Layer
// ============================================================
import { invoke } from '@tauri-apps/api/core';
import { getCurrentWindow } from '@tauri-apps/api/window';

const isTauriRuntime = typeof window !== 'undefined' && Boolean(window.__TAURI_INTERNALS__);
const DEV_API_BASE = (() => {
  const candidate = String(import.meta.env?.DEV ? import.meta.env?.VITE_API_BASE || '' : '').trim();
  if (!candidate) return '';
  try {
    const url = new URL(candidate);
    const loopback = ['127.0.0.1', 'localhost', '::1'].includes(url.hostname);
    return url.protocol === 'http:' && loopback ? url.origin : '';
  } catch (_) {
    return '';
  }
})();
let API_BASE = null;
let progressPollTimer = null;
let batchPollTimer = null;
const DEFAULT_TIMEOUT_MS = 15000;
// The first local grounding request must load the vision model into memory.
// Keep the ordinary API budget tight, but give this one offline operation a
// cold-start allowance so a successful inference is not reported as failed.
const SEMANTIC_GROUNDING_TIMEOUT_MS = 180000;

function currentWindow() {
  if (!isTauriRuntime) return null;
  try { return getCurrentWindow(); } catch (_) { return null; }
}

async function getPort() {
  if (API_BASE) return API_BASE;
  if (!isTauriRuntime) {
    API_BASE = DEV_API_BASE || 'http://127.0.0.1:8765';
    return API_BASE;
  }
  const port = await invoke('get_api_port');
  API_BASE = 'http://127.0.0.1:' + port;
  return API_BASE;
}

async function fetchWithTimeout(url, options, timeoutMs) {
  const fetchOptions = Object.assign({}, options || {});
  const externalSignal = fetchOptions.signal;
  delete fetchOptions.timeoutMs;
  const controller = new AbortController();
  let timedOut = false;
  const abortFromCaller = function() { controller.abort(); };
  if (externalSignal?.aborted) controller.abort();
  else externalSignal?.addEventListener('abort', abortFromCaller, { once: true });
  const timer = setTimeout(function() {
    timedOut = true;
    controller.abort();
  }, Math.max(1, Number(timeoutMs) || DEFAULT_TIMEOUT_MS));
  try {
    return await fetch(url, Object.assign({}, fetchOptions, { signal: controller.signal }));
  } catch (error) {
    if (timedOut) {
      const timeoutError = new Error('Request timed out');
      timeoutError.code = 'REQUEST_TIMEOUT';
      throw timeoutError;
    }
    throw error;
  } finally {
    clearTimeout(timer);
    externalSignal?.removeEventListener('abort', abortFromCaller);
  }
}

async function fetchJSON(url, options) {
  options = options || {};
  const base = await getPort();
  const resp = await fetchWithTimeout(base + url, Object.assign({}, options, {
    headers: Object.assign({}, options.headers || {}),
  }), options.timeoutMs || DEFAULT_TIMEOUT_MS);
  if (!resp.ok) {
    const text = await resp.text().catch(function() { return ''; });
    let message = text.slice(0, 300);
    let detail = null;
    try {
      const parsed = JSON.parse(text);
      detail = parsed?.detail || parsed;
      message = parsed?.detail?.message || parsed?.detail || parsed?.message || message;
    } catch (_) { /* keep the response text */ }
    const error = new Error('HTTP ' + resp.status + ': ' + message);
    error.status = resp.status;
    error.code = 'HTTP_ERROR';
    error.detail = detail;
    throw error;
  }
  return resp.json();
}

async function absoluteApiUrl(path) {
  return (await getPort()) + path;
}

async function postForm(url, formData) {
  const base = await getPort();
  const resp = await fetchWithTimeout(
    base + url,
    { method: 'POST', body: formData },
    120000,
  );
  if (!resp.ok) {
    const text = await resp.text().catch(function() { return ''; });
    throw new Error('HTTP ' + resp.status + ': ' + text.slice(0, 200));
  }
  return resp.json();
}

// Health
export async function checkHealth() {
  try {
    const base = await getPort();
    const ctrl = new AbortController();
    const to = setTimeout(function() { ctrl.abort(); }, 5000);
    const resp = await fetch(base + '/api/health', { signal: ctrl.signal }).catch(function() { return null; });
    clearTimeout(to);
    if (!resp || !resp.ok) {
      if (isTauriRuntime) {
        try {
          const port = await invoke('ensure_python_sidecar');
          API_BASE = 'http://127.0.0.1:' + port;
        } catch (_) { /* the connection UI owns the retry/error state */ }
      }
      return { ok: false };
    }
    const data = await resp.json();
    return Object.assign({ ok: true }, data);
  } catch(e) { return { ok: false }; }
}

// Settings
export async function getSettings() { return fetchJSON('/api/settings'); }
export async function saveSettings(settings) {
  const base = await getPort();
  const resp = await fetch(base + '/api/settings', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(settings),
  });
  if (!resp.ok) {
    const payload = await resp.json().catch(() => ({}));
    const detail = payload?.detail;
    throw new Error(detail?.message || detail || `设置保存失败（HTTP ${resp.status}）`);
  }
  return resp.json();
}

async function fetchBinary(url, options) {
  options = options || {};
  const base = await getPort();
  const resp = await fetchWithTimeout(base + url, Object.assign({}, options, {
    headers: Object.assign({}, options.headers || {}),
  }), options.timeoutMs || DEFAULT_TIMEOUT_MS);
  if (!resp.ok) {
    const text = await resp.text().catch(function() { return ''; });
    let message = text.slice(0, 300);
    let detail = null;
    try {
      const parsed = JSON.parse(text);
      detail = parsed?.detail || parsed;
      message = parsed?.detail?.message || parsed?.detail || parsed?.message || message;
    } catch (_) { /* keep the response text */ }
    const error = new Error('HTTP ' + resp.status + ': ' + message);
    error.status = resp.status;
    error.code = 'HTTP_ERROR';
    error.detail = detail;
    throw error;
  }
  const disposition = resp.headers.get('Content-Disposition') || '';
  const filenameMatch = disposition.match(/filename="?([^";]+)"?/i);
  return {
    blob: await resp.blob(),
    filename: filenameMatch?.[1] || 'ProductAtelier-canvas.png',
    revision: Number(resp.headers.get('X-Canvas-Revision') || 0),
    artboardId: resp.headers.get('X-Canvas-Artboard') || '',
    pixelWidth: Number(resp.headers.get('X-Canvas-Pixel-Width') || 0),
    pixelHeight: Number(resp.headers.get('X-Canvas-Pixel-Height') || 0),
    renderedLayerCount: Number(resp.headers.get('X-Canvas-Rendered-Layers') || 0),
    source: resp.headers.get('X-Canvas-Source') || '',
  };
}
export async function verifyGroundingPack() {
  return fetchJSON('/api/grounding-pack/verify', {
    method: 'POST',
    timeoutMs: 180000,
  });
}
export async function getAppConfig() { return isTauriRuntime ? invoke('get_app_config') : {}; }
export async function setAppConfig(config) { return isTauriRuntime ? invoke('set_app_config', { config: config }) : config; }
export async function reportStartupMilestone(milestone) {
  if (!isTauriRuntime) return;
  try { await invoke('report_startup_milestone', { milestone }); } catch (_) { /* diagnostics only */ }
}

// Balance
export async function checkBalance() { return fetchJSON('/api/balance'); }

// Generation
export async function startSingle(params) {
  const fd = new FormData();
  fd.append('file', params.file);
  if (params.product_name) fd.append('product_name', params.product_name);
  fd.append('model', params.model || 'gpt-image-2');
  fd.append('batch', String(params.batch || 1));
  fd.append('platter', params.platter || 'auto');
  fd.append('fidelity', String(params.fidelity || 40));
  fd.append('angle', params.angle || 'auto');
  if (params.session_id) fd.append('session_id', params.session_id);
  if (params.category) fd.append('category', params.category);
  if (params.brief) fd.append('brief', JSON.stringify(params.brief));
  if (params.intent_locks) fd.append('intent_locks', JSON.stringify(params.intent_locks));
  return postForm('/api/single', fd);
}
export async function startGroupSplit(params) {
  const fd = new FormData();
  fd.append('file', params.file);
  fd.append('model', params.model || 'gemini-3.1-flash-image-preview');
  fd.append('platter', params.platter || 'auto');
  fd.append('refine', String(params.refine !== false));
  fd.append('fidelity', String(params.fidelity || 35));
  fd.append('angle', params.angle || 'auto');
  if (params.session_id) fd.append('session_id', params.session_id);
  if (params.category) fd.append('category', params.category);
  if (params.brief) fd.append('brief', JSON.stringify(params.brief));
  if (params.intent_locks) fd.append('intent_locks', JSON.stringify(params.intent_locks));
  return postForm('/api/group-split', fd);
}
export const startMulti = startGroupSplit;

export async function startMultiFile(params) {
  const fd = new FormData();
  (params.files || []).forEach(function(file) { fd.append('files', file); });
  fd.append('model', params.model || 'gpt-image-2');
  fd.append('variations', String(params.variations || 1));
  fd.append('platter', params.platter || 'auto');
  fd.append('fidelity', String(params.fidelity || 40));
  fd.append('angle', params.angle || 'auto');
  if (params.session_id) fd.append('session_id', params.session_id);
  if (params.category) fd.append('category', params.category);
  if (params.brief) fd.append('brief', JSON.stringify(params.brief));
  if (params.intent_locks) fd.append('intent_locks', JSON.stringify(params.intent_locks));
  return postForm('/api/multi-file', fd);
}

export async function cutoutBatch(params) {
  const fd = new FormData();
  (params.files || []).forEach(function(file) { fd.append('files', file); });
  if (params.session_id) fd.append('session_id', params.session_id);
  if (params.brief) fd.append('brief', JSON.stringify(params.brief));
  return postForm('/api/cutout-batch', fd);
}

export async function cutoutOnly(file, sessionId) {
  const fd = new FormData();
  fd.append('file', file);
  if (sessionId) fd.append('session_id', sessionId);
  return postForm('/api/cutout', fd);
}

// Persistent asset workspace
export async function getAssets(limit, options) {
  return fetchJSON('/api/assets?limit=' + encodeURIComponent(limit || 500), options);
}
export async function importAssets(files, collection = 'product') {
  const fd = new FormData();
  Array.from(files || []).forEach(function(file) { fd.append('files', file); });
  return postForm('/api/assets/import-batch?collection=' + encodeURIComponent(collection), fd);
}
export async function importFolderSources(folderPath) {
  return fetchJSON('/api/folder-sources/import', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ folder_path: String(folderPath || '') }),
    timeoutMs: 120000,
  });
}
export async function getAsset(assetId, options = {}) {
  return fetchJSON('/api/assets/' + encodeURIComponent(assetId), options);
}
export async function getAssetContentUrl(assetId) {
  return absoluteApiUrl('/api/assets/' + encodeURIComponent(assetId) + '/content');
}
export async function getAssetDownloadUrl(assetId) {
  return absoluteApiUrl('/api/assets/' + encodeURIComponent(assetId) + '/content?download=true');
}
export async function getAssetThumbnailUrl(assetId, size) {
  return absoluteApiUrl('/api/assets/' + encodeURIComponent(assetId) + '/thumbnail?size=' + encodeURIComponent(size || 320));
}

export async function previewSemanticCutout(payload) {
  return fetchJSON('/api/semantic-cutout/preview', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload || {}),
    timeoutMs: SEMANTIC_GROUNDING_TIMEOUT_MS,
  });
}

export async function confirmSemanticCutout(payload) {
  return fetchJSON('/api/semantic-cutout/confirm', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload || {}),
  });
}

export async function previewSemanticCutoutMask(payload) {
  return fetchJSON('/api/semantic-cutout/mask-preview', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload || {}),
    timeoutMs: SEMANTIC_GROUNDING_TIMEOUT_MS,
  });
}

export async function getCollectionAssets(collection, options = {}) {
  const limit = Number(options.limit || 200);
  const offset = Number(options.offset || 0);
  const includeTrashed = Boolean(options.includeTrashed);
  return fetchJSON(
    '/api/collections/' + encodeURIComponent(collection)
      + '/assets?limit=' + encodeURIComponent(limit)
      + '&offset=' + encodeURIComponent(offset)
      + '&include_trashed=' + encodeURIComponent(includeTrashed),
    options,
  );
}

export async function getWorkspace(mode, options = {}) {
  return fetchJSON('/api/workspaces/' + encodeURIComponent(mode), options);
}

export async function saveWorkspaceDraft(mode, payload, options = {}) {
  return fetchJSON('/api/workspaces/' + encodeURIComponent(mode) + '/draft', {
    ...options,
    method: 'PUT',
    headers: { 'Content-Type': 'application/json', ...(options.headers || {}) },
    body: JSON.stringify(payload || {}),
  });
}

export async function getCanvas(mode, options = {}) {
  return fetchJSON('/api/workspaces/' + encodeURIComponent(mode) + '/canvas', options);
}

export async function saveCanvas(mode, payload, options = {}) {
  return fetchJSON('/api/workspaces/' + encodeURIComponent(mode) + '/canvas', {
    ...options,
    method: 'PUT',
    headers: { 'Content-Type': 'application/json', ...(options.headers || {}) },
    body: JSON.stringify(payload || {}),
  });
}

export async function listSpatialCanvases(limit = 100, options = {}) {
  return fetchJSON('/api/spatial-canvases?limit=' + encodeURIComponent(limit), options);
}

export async function createSpatialCanvas(payload, options = {}) {
  return fetchJSON('/api/spatial-canvases', {
    ...options,
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...(options.headers || {}) },
    body: JSON.stringify(payload || {}),
  });
}

export async function openSpatialCanvas(canvasId, options = {}) {
  return fetchJSON('/api/spatial-canvases/' + encodeURIComponent(canvasId), options);
}

export async function renameSpatialCanvas(canvasId, payload, options = {}) {
  return fetchJSON('/api/spatial-canvases/' + encodeURIComponent(canvasId), {
    ...options,
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json', ...(options.headers || {}) },
    body: JSON.stringify(payload || {}),
  });
}

export async function saveSpatialCanvasScene(canvasId, payload, options = {}) {
  return fetchJSON('/api/spatial-canvases/' + encodeURIComponent(canvasId) + '/scene', {
    ...options,
    method: 'PUT',
    headers: { 'Content-Type': 'application/json', ...(options.headers || {}) },
    body: JSON.stringify(payload || {}),
  });
}

export async function getSpatialSceneVersion(versionId, options = {}) {
  return fetchJSON('/api/spatial-scene-versions/' + encodeURIComponent(versionId), options);
}

export async function exportCanvas(mode, payload, options = {}) {
  return fetchBinary('/api/workspaces/' + encodeURIComponent(mode) + '/canvas/export', {
    ...options,
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...(options.headers || {}) },
    body: JSON.stringify(payload || {}),
  });
}

export async function createCanvasRoi(payload, options = {}) {
  return fetchJSON('/api/canvas-rois', {
    ...options,
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...(options.headers || {}) },
    body: JSON.stringify(payload || {}),
  });
}

export async function getCanvasRois(versionId, sourceLayerId = '', options = {}) {
  const query = sourceLayerId
    ? '?source_layer_id=' + encodeURIComponent(sourceLayerId)
    : '';
  return fetchJSON('/api/canvas-versions/' + encodeURIComponent(versionId) + '/rois' + query, options);
}

export async function getCanvasMask(roiId, options = {}) {
  return fetchJSON('/api/canvas-rois/' + encodeURIComponent(roiId) + '/mask', options);
}

export async function saveCanvasMask(roiId, payload, options = {}) {
  return fetchJSON('/api/canvas-rois/' + encodeURIComponent(roiId) + '/mask', {
    ...options,
    method: 'PUT',
    headers: { 'Content-Type': 'application/json', ...(options.headers || {}) },
    body: JSON.stringify(payload || {}),
  });
}

export async function getCanvasMaskVersions(maskId, limit = 100, options = {}) {
  return fetchJSON(
    '/api/canvas-masks/' + encodeURIComponent(maskId)
      + '/versions?limit=' + encodeURIComponent(Math.max(1, Number(limit) || 100)),
    options,
  );
}

export async function getCanvasMaskVersion(versionId, options = {}) {
  return fetchJSON('/api/canvas-mask-versions/' + encodeURIComponent(versionId), options);
}

export async function createLocalEditSpec(payload, options = {}) {
  return fetchJSON('/api/local-edit-specs', {
    ...options,
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...(options.headers || {}) },
    body: JSON.stringify(payload || {}),
  });
}

export async function getLocalEditSpec(specId, options = {}) {
  return fetchJSON('/api/local-edit-specs/' + encodeURIComponent(specId), options);
}

export async function getLatestLocalEditSpec({
  canvasVersionId,
  sourceLayerId,
  roiId,
  mode,
  maskVersionId = '',
}, options = {}) {
  const params = new URLSearchParams({
    canvas_version_id: String(canvasVersionId),
    source_layer_id: String(sourceLayerId),
    roi_id: String(roiId),
    mode: String(mode),
  });
  if (maskVersionId) params.set('mask_version_id', String(maskVersionId));
  return fetchJSON('/api/local-edit-specs/latest?' + params.toString(), options);
}

export async function composeLocalEdit(mode, payload, options = {}) {
  return fetchJSON('/api/workspaces/' + encodeURIComponent(mode) + '/local-edit/compose', {
    ...options,
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...(options.headers || {}) },
    body: JSON.stringify(payload || {}),
  });
}

export async function getCommands(options = {}) {
  return fetchJSON('/api/commands', options);
}

export async function executeCommand(commandId, payload, options = {}) {
  return fetchJSON('/api/commands/' + encodeURIComponent(commandId) + '/execute', {
    ...options,
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...(options.headers || {}) },
    body: JSON.stringify(payload || {}),
  });
}

export async function getProductProfiles(limit = 200, options = {}) {
  return fetchJSON(
    '/api/product-profiles?limit=' + encodeURIComponent(Math.max(1, Number(limit) || 200)),
    options,
  );
}

export async function getProductProfile(profileId, options = {}) {
  return fetchJSON('/api/product-profiles/' + encodeURIComponent(profileId), options);
}

export async function getProductProfileVersions(profileId, limit = 100, options = {}) {
  return fetchJSON(
    '/api/product-profiles/' + encodeURIComponent(profileId)
      + '/versions?limit=' + encodeURIComponent(Math.max(1, Number(limit) || 100)),
    options,
  );
}

export async function getProductProfileVersion(versionId, options = {}) {
  return fetchJSON('/api/product-profile-versions/' + encodeURIComponent(versionId), options);
}

export async function saveProductProfile(profileId, payload, options = {}) {
  return fetchJSON('/api/product-profiles/' + encodeURIComponent(profileId), {
    ...options,
    method: 'PUT',
    headers: { 'Content-Type': 'application/json', ...(options.headers || {}) },
    body: JSON.stringify(payload || {}),
  });
}

export async function removeAssetFromCollection(collection, assetId) {
  return fetchJSON(
    '/api/collections/' + encodeURIComponent(collection) + '/assets/' + encodeURIComponent(assetId),
    { method: 'DELETE' },
  );
}

export async function restoreAssetToCollection(collection, assetId) {
  return fetchJSON(
    '/api/collections/' + encodeURIComponent(collection) + '/assets/' + encodeURIComponent(assetId) + '/restore',
    { method: 'POST' },
  );
}

export async function reorderCollectionAssets(collection, assetIds) {
  return fetchJSON('/api/collections/' + encodeURIComponent(collection) + '/order', {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ asset_ids: Array.from(assetIds || [], String) }),
  });
}

export async function getAssetReferences(assetId) {
  return fetchJSON('/api/assets/' + encodeURIComponent(assetId) + '/references');
}

export async function getTrash(collection = '') {
  const query = collection ? '?collection=' + encodeURIComponent(collection) : '';
  return fetchJSON('/api/trash' + query);
}

export async function purgeAsset(assetId) {
  return fetchJSON(
    '/api/trash/assets/' + encodeURIComponent(assetId)
      + '?confirm_asset_id=' + encodeURIComponent(assetId),
    { method: 'DELETE' },
  );
}

// Durable jobs
export async function createJob(payload) {
  return fetchJSON('/api/jobs', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload || {}),
  });
}
export async function getJobs(limit, options) {
  return fetchJSON('/api/jobs?limit=' + encodeURIComponent(limit || 100), options);
}
export async function getJobRuntime(options) {
  return fetchJSON('/api/jobs/runtime', options);
}
export async function getJob(jobId) {
  return fetchJSON('/api/jobs/' + encodeURIComponent(jobId));
}
export async function cancelJob(jobId) {
  return fetchJSON('/api/jobs/' + encodeURIComponent(jobId) + '/cancel', { method: 'POST' });
}
export async function pauseJob(jobId) {
  return fetchJSON('/api/jobs/' + encodeURIComponent(jobId) + '/pause', { method: 'POST' });
}
export async function resumeJob(jobId) {
  return fetchJSON('/api/jobs/' + encodeURIComponent(jobId) + '/resume', { method: 'POST' });
}
export async function retryJob(jobId, itemIds) {
  const body = Array.isArray(itemIds) && itemIds.length ? { item_ids: itemIds } : {};
  return fetchJSON('/api/jobs/' + encodeURIComponent(jobId) + '/retry', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
}

export async function getJobTraces(jobId) {
  return fetchJSON('/api/jobs/' + encodeURIComponent(jobId) + '/traces');
}

export async function getJobReviews(jobId) {
  return fetchJSON('/api/jobs/' + encodeURIComponent(jobId) + '/reviews');
}

export async function submitResultReview(jobId, payload) {
  return fetchJSON('/api/jobs/' + encodeURIComponent(jobId) + '/reviews', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload || {}),
  });
}

export async function adjustResult(jobId, payload) {
  return fetchJSON('/api/jobs/' + encodeURIComponent(jobId) + '/adjustments', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload || {}),
  });
}

export async function completeWorkspace(mode, payload) {
  return fetchJSON('/api/workspaces/' + encodeURIComponent(mode) + '/complete', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload || {}),
  });
}

// Knowledge + creation ledger
export async function getKnowledgeStatus() { return fetchJSON('/api/knowledge/status'); }
export async function compileKnowledge(context) {
  return fetchJSON('/api/knowledge/compile', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(context || {}),
  });
}
export async function reloadKnowledge(path) {
  return fetchJSON('/api/knowledge/reload', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(path ? { path: path } : {}),
  });
}
export async function getLedgerStatus() { return fetchJSON('/api/ledger/status'); }
export async function getSessions(limit) { return fetchJSON('/api/sessions?limit=' + encodeURIComponent(limit || 30)); }
export async function getSession(sessionId) { return fetchJSON('/api/sessions/' + encodeURIComponent(sessionId)); }
export async function recordFeedback(sessionId, data) {
  return fetchJSON('/api/sessions/' + encodeURIComponent(sessionId) + '/feedback', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(data || {}),
  });
}
export async function getMemorySuggestions(status, limit = 200) {
  return fetchJSON(
    '/api/memory/suggestions?status=' + encodeURIComponent(status || 'pending')
      + '&limit=' + encodeURIComponent(Math.max(1, Math.min(Number(limit) || 200, 200))),
  );
}
export async function getMemorySuggestion(id) {
  return fetchJSON('/api/memory/suggestions/' + encodeURIComponent(id));
}
export async function synthesizeMemory() {
  return fetchJSON('/api/memory/synthesize', { method: 'POST' });
}
export async function reviewMemorySuggestion(id, status) {
  return fetchJSON('/api/memory/suggestions/' + encodeURIComponent(id) + '/review', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ status: status }),
  });
}
export async function governMemorySuggestion(id, data) {
  return fetchJSON('/api/memory/suggestions/' + encodeURIComponent(id), {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(data || {}),
  });
}

// Progress
export async function pollProgress(taskId) { return fetchJSON('/api/progress/' + taskId); }
export function startPolling(taskId, onUpdate, intervalMs) {
  intervalMs = intervalMs || 1500;
  stopPolling();
  function tick() {
    pollProgress(taskId).then(function(data) {
      onUpdate(data);
      if (data.status === 'completed' || data.status === 'error') { progressPollTimer = null; return; }
      progressPollTimer = setTimeout(tick, intervalMs);
    }).catch(function(e) {
      onUpdate({ status: 'error', error: String(e) });
      progressPollTimer = null;
    });
  }
  tick();
}
export function stopPolling() { if (progressPollTimer) { clearTimeout(progressPollTimer); progressPollTimer = null; } }

// Thumbnail
export async function getThumbnailUrl(path) {
  const base = await getPort();
  return base + '/api/thumbnail?path=' + encodeURIComponent(path);
}

// History
export async function getHistory() { return fetchJSON('/api/history'); }

// File dialogs
export async function saveBinary(suggestedName, blob) {
  if (!(blob instanceof Blob)) throw new TypeError('saveBinary requires a Blob');
  const objectUrl = URL.createObjectURL(blob);
  const link = document.createElement('a');
  link.href = objectUrl;
  link.download = String(suggestedName || 'ProductAtelier-result.bin');
  document.body.appendChild(link);
  link.click();
  link.remove();
  setTimeout(() => URL.revokeObjectURL(objectUrl), 1000);
  return true;
}

export async function downloadAsset(assetId, suggestedName = '') {
  const filename = String(suggestedName || 'ProductAtelier-result.bin');
  if (isTauriRuntime) {
    return invoke('save_binary_asset', {
      assetId: String(assetId || ''),
      suggestedName: filename,
    });
  }
  const response = await fetchBinary(
    '/api/assets/' + encodeURIComponent(assetId) + '/content?download=true',
    { timeoutMs: 120000 },
  );
  await saveBinary(suggestedName || response.filename, response.blob);
  return { filename: suggestedName || response.filename, size: response.blob.size };
}

export async function saveImage(suggestedName, dataB64) {
  if (isTauriRuntime) return invoke('save_base64_image', { suggestedName: suggestedName, dataB64: dataB64 });
  const link = document.createElement('a');
  link.href = 'data:application/octet-stream;base64,' + dataB64;
  link.download = suggestedName;
  document.body.appendChild(link);
  link.click();
  link.remove();
  return true;
}
export async function openInFolder(path) { return invoke('open_in_folder', { path: path }); }

// Window controls
export async function minimizeWindow() { const appWindow = currentWindow(); return appWindow ? appWindow.minimize() : false; }
export async function toggleMaximize() {
  const appWindow = currentWindow();
  if (!appWindow) return false;
  const max = await appWindow.isMaximized();
  if (max) { await appWindow.unmaximize(); return false; }
  else { await appWindow.maximize(); return true; }
}
export async function isWindowMaximized() { const appWindow = currentWindow(); return appWindow ? appWindow.isMaximized() : false; }
export async function onAppCloseRequested(handler) {
  const appWindow = currentWindow();
  if (!appWindow || typeof appWindow.onCloseRequested !== 'function') return () => {};
  return appWindow.onCloseRequested(handler);
}
export async function completeAppClose() { return isTauriRuntime ? invoke('complete_close_app') : false; }

// Folder selection dialog
export async function selectFolder() { return invoke('select_folder_dialog'); }
export async function verifyFolder(path) { return invoke('verify_folder_exists', { path }); }

// Batch folder processing
export async function startBatchFolder(folderPath, params) {
  const base = await getPort();
  const fd = new FormData();
  fd.append('folder_path', folderPath);
  fd.append('mode', params.mode || 'single');
  fd.append('model', params.model || 'gpt-image-2');
  fd.append('platter', params.platter || 'auto');
  fd.append('fidelity', String(params.fidelity || 40));
  fd.append('angle', params.angle || 'auto');
  fd.append('refine', params.refine !== false ? '1' : '0');
  fd.append('output_dir', params.output_dir || 'D:/图像处理');
  const resp = await fetch(base + '/api/batch-folder', { method: 'POST', body: fd });
  if (!resp.ok) {
    const text = await resp.text().catch(() => '');
    throw new Error('HTTP ' + resp.status + ': ' + text.slice(0, 200));
  }
  return resp.json();
}

export async function getBatchProgress(taskId) {
  const base = await getPort();
  const resp = await fetch(base + '/api/batch-progress/' + encodeURIComponent(taskId));
  if (!resp.ok) throw new Error('Failed to get batch progress');
  return resp.json();
}

export function startBatchPolling(taskId, onUpdate, intervalMs) {
  intervalMs = intervalMs || 2000;
  if (batchPollTimer) clearTimeout(batchPollTimer);
  function tick() {
    getBatchProgress(taskId).then(data => {
      onUpdate(data);
      if (data.status === 'completed' || data.status === 'error') { batchPollTimer = null; return; }
      batchPollTimer = setTimeout(tick, intervalMs);
    }).catch(e => {
      onUpdate({ status: 'error', error: String(e) });
      batchPollTimer = null;
    });
  }
  tick();
}

export async function closeApp() { return isTauriRuntime ? invoke('close_app') : false; }

// Utilities
export function dataURLtoBytes(dataUrl) {
  const arr = dataUrl.split(',');
  const bstr = atob(arr[1]);
  let n = bstr.length;
  const u8 = new Uint8Array(n);
  while (n--) u8[n] = bstr.charCodeAt(n);
  return u8;
}
export function b64ToDataURL(b64, mime) {
  return 'data:' + mime + ';base64,' + b64;
}
