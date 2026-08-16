// ============================================================
// Product Atelier - API Layer
// Communicates with Python sidecar backend via HTTP,
// and with Rust shell via Tauri invoke.
// ============================================================

import { invoke } from '@tauri-apps/api/core';
import { getCurrentWindow } from '@tauri-apps/api/window';

const appWindow = getCurrentWindow();

let API_BASE = null;
let pollTimer = null;

async function getPort() {
  if (API_BASE) return API_BASE;
  const port = await invoke('get_api_port');
  API_BASE = `http://127.0.0.1:${port}`;
  return API_BASE;
}

async function fetchJSON(url, options = {}) {
  const base = await getPort();
  const resp = await fetch(base + url, {
    ...options,
    headers: {
      ...(options.headers || {}),
    },
  });
  if (!resp.ok) {
    const text = await resp.text().catch(() => '');
    throw new Error(`HTTP ${resp.status}: ${text.slice(0, 200)}`);
  }
  return resp.json();
}

async function postForm(url, formData) {
  const base = await getPort();
  const resp = await fetch(base + url, {
    method: 'POST',
    body: formData,
  });
  if (!resp.ok) {
    const text = await resp.text().catch(() => '');
    throw new Error(`HTTP ${resp.status}: ${text.slice(0, 200)}`);
  }
  return resp.json();
}

// ---- Health ----
export async function checkHealth() {
  try {
    const base = await getPort();
    const ctrl = new AbortController();
    const to = setTimeout(() => ctrl.abort(), 5000);
    const resp = await fetch(base + '/api/health', { signal: ctrl.signal }).catch(() => null);
    clearTimeout(to);
    if (!resp || !resp.ok) return { ok: false };
    const data = await resp.json();
    return { ok: true, ...data };
  } catch {
    return { ok: false };
  }
}

// ---- Settings ----
export async function getSettings() {
  return fetchJSON('/api/settings');
}

export async function saveSettings(settings) {
  const base = await getPort();
  const resp = await fetch(base + '/api/settings', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(settings),
  });
  if (!resp.ok) throw new Error('保存设置失败');
  return resp.json();
}

export async function getAppConfig() {
  return invoke('get_app_config');
}

export async function setAppConfig(config) {
  return invoke('set_app_config', { config });
}

// ---- Balance ----
export async function checkBalance() {
  return fetchJSON('/api/balance');
}

// ---- Generation: Single Product ----
export async function startSingle(params) {
  const fd = new FormData();
  fd.append('file', params.file);
  if (params.product_name) fd.append('product_name', params.product_name);
  fd.append('model', params.model || 'gpt-image-2');
  fd.append('batch', String(params.batch || 1));
  fd.append('platter', params.platter || 'auto');
  fd.append('fidelity', String(params.fidelity || 40));
  fd.append('angle', params.angle || 'auto');
  return postForm('/api/single', fd);
}

// ---- Generation: Multi Product Batch ----
export async function startMulti(params) {
  const fd = new FormData();
  fd.append('file', params.file);
  fd.append('model', params.model || 'gemini-3.1-flash-image-preview');
  fd.append('platter', params.platter || 'auto');
  fd.append('refine', String(params.refine !== false));
  fd.append('fidelity', String(params.fidelity || 35));
  fd.append('angle', params.angle || 'auto');
  return postForm('/api/multi', fd);
}

// ---- Quick Cutout ----
export async function cutoutOnly(file) {
  const fd = new FormData();
  fd.append('file', file);
  return postForm('/api/cutout', fd);
}

// ---- Progress Polling ----
export async function pollProgress(taskId) {
  return fetchJSON(`/api/progress/${taskId}`);
}

export function startPolling(taskId, onUpdate, intervalMs = 1500) {
  stopPolling();
  const tick = async () => {
    try {
      const data = await pollProgress(taskId);
      onUpdate(data);
      if (data.status === 'completed' || data.status === 'error') {
        pollTimer = null;
        return;
      }
    } catch (e) {
      onUpdate({ status: 'error', error: String(e) });
      pollTimer = null;
      return;
    }
    pollTimer = setTimeout(tick, intervalMs);
  };
  tick();
}

export function stopPolling() {
  if (pollTimer) {
    clearTimeout(pollTimer);
    pollTimer = null;
  }
}

// ---- Thumbnail URL ----
export async function getThumbnailUrl(path) {
  const base = await getPort();
  return `${base}/api/thumbnail?path=${encodeURIComponent(path)}`;
}

// ---- History ----
export async function getHistory() {
  return fetchJSON('/api/history');
}

// ---- File operations (Tauri native dialogs) ----
export async function saveImage(suggestedName, dataB64) {
  return invoke('save_base64_image', { suggestedName, dataB64 });
}

export async function openInFolder(path) {
  return invoke('open_in_folder', { path });
}



export async function minimizeWindow() {
  return appWindow.minimize();
}

export async function toggleMaximize() {
  return appWindow.toggleMaximize();
}

export async function closeApp() {
  return invoke('close_app');
}

// ---- Utility ----
export function dataURLtoBytes(dataUrl) {
  const arr = dataUrl.split(',');
  const bstr = atob(arr[1]);
  let n = bstr.length;
  const u8 = new Uint8Array(n);
  while (n--) u8[n] = bstr.charCodeAt(n);
  return u8;
}

export function b64ToDataURL(b64, mime) {
  return `data:${mime};base64,${b64}`;
}
