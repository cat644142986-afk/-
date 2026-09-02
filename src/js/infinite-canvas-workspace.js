import * as API from './api.js';
import { createApiSpatialCanvasAdapter } from './infinite-canvas-adapter.js';

function escapeHtml(value) {
  return String(value || '')
    .replaceAll('&', '&amp;')
    .replaceAll('<', '&lt;')
    .replaceAll('>', '&gt;')
    .replaceAll('"', '&quot;')
    .replaceAll("'", '&#039;');
}

function formatRecent(iso) {
  const date = new Date(iso);
  if (!Number.isFinite(date.getTime())) return '刚刚';
  return new Intl.DateTimeFormat('zh-CN', {
    month: 'numeric', day: 'numeric', hour: '2-digit', minute: '2-digit', hour12: false,
  }).format(date);
}

function thumbnailElements(scene) {
  const elements = Array.from(scene?.elements || [])
    .filter((element) => !element?.isDeleted && Number.isFinite(element?.x) && Number.isFinite(element?.y))
    .slice(-12);
  if (!elements.length) return '';
  const geometry = elements.map((element) => {
    const width = Math.max(2, Math.abs(Number(element.width) || 2));
    const height = Math.max(2, Math.abs(Number(element.height) || 2));
    return { element, x: Number(element.x), y: Number(element.y), width, height };
  });
  const minX = Math.min(...geometry.map((item) => item.x));
  const minY = Math.min(...geometry.map((item) => item.y));
  const maxX = Math.max(...geometry.map((item) => item.x + item.width));
  const maxY = Math.max(...geometry.map((item) => item.y + item.height));
  const spanX = Math.max(40, maxX - minX);
  const spanY = Math.max(40, maxY - minY);
  const allowed = new Set(['arrow', 'diamond', 'ellipse', 'frame', 'freedraw', 'image', 'line', 'rectangle', 'text']);
  return geometry.map(({ element, x, y, width, height }) => {
    const kind = allowed.has(element.type) ? element.type : 'rectangle';
    const left = 8 + ((x - minX) / spanX) * 76;
    const top = 8 + ((y - minY) / spanY) * 76;
    const scaledWidth = Math.max(3, Math.min(80 - left, (width / spanX) * 76));
    const scaledHeight = Math.max(3, Math.min(80 - top, (height / spanY) * 76));
    return `<i class="is-${kind}" style="left:${left.toFixed(2)}%;top:${top.toFixed(2)}%;width:${scaledWidth.toFixed(2)}%;height:${scaledHeight.toFixed(2)}%"></i>`;
  }).join('');
}

export function createInfiniteCanvasWorkspaceController({
  documentRef = document,
  windowRef = window,
  adapter = createApiSpatialCanvasAdapter({ api: API }),
  runtimeLoader = () => import('./infinite-canvas-island.jsx'),
} = {}) {
  const query = (selector) => documentRef.querySelector(selector);
  let bound = false;
  let active = false;
  let currentId = '';
  let mountedIsland = null;
  let runtimePromise = null;
  let recordsPromise = null;
  let pendingScene = null;
  let sceneTimer = null;
  let savePromise = Promise.resolve();
  let openEpoch = 0;
  let renameReturnFocus = null;

  function recordsHtml(records) {
    return records.map((record, index) => `
      <article class="spatial-canvas-card" data-spatial-record="${escapeHtml(record.id)}">
        <button class="spatial-canvas-card__open" type="button" data-spatial-open="${escapeHtml(record.id)}" aria-label="打开画布 ${escapeHtml(record.name)}">
          <span class="spatial-thumbnail" data-empty="${record.summary.element_count ? 'false' : 'true'}" aria-hidden="true">${thumbnailElements(record.thumbnail || record.scene)}</span>
          <span class="spatial-canvas-card__copy"><strong>${escapeHtml(record.name)}</strong><small>${index === 0 ? '最近打开' : '本次会话'} · ${formatRecent(record.last_opened_at)}</small></span>
        </button>
        <button class="spatial-card-action" type="button" data-spatial-rename="${escapeHtml(record.id)}" aria-label="重命名 ${escapeHtml(record.name)}" title="重命名">
          <svg viewBox="0 0 24 24" aria-hidden="true"><path d="m4 20 4.5-1 10-10a2.1 2.1 0 0 0-3-3l-10 10Z"/><path d="m13.5 7.5 3 3"/></svg>
        </button>
      </article>
    `).join('');
  }

  function renderLibrary() {
    const list = query('#spatial-canvas-list');
    const empty = query('#spatial-library-empty');
    if (!list || !empty) return;
    const records = adapter.list();
    list.innerHTML = recordsHtml(records);
    list.hidden = records.length === 0;
    empty.hidden = records.length !== 0;
    query('#spatial-canvas-count').textContent = `${records.length} 个画布`;
  }

  function syncEditorHeading() {
    const record = currentId ? adapter.get(currentId) : null;
    query('#spatial-current-name').textContent = record?.name || '无限画布';
    query('#btn-spatial-rename').hidden = !record;
  }

  function showLibrary({ restoreFocus = false } = {}) {
    openEpoch += 1;
    flushScene();
    mountedIsland?.unmount?.();
    mountedIsland = null;
    query('#spatial-library').hidden = false;
    query('#spatial-editor').hidden = true;
    query('#btn-spatial-home').hidden = true;
    query('#btn-spatial-rename').hidden = true;
    query('#spatial-current-name').textContent = '画布空间';
    renderLibrary();
    if (restoreFocus) windowRef.requestAnimationFrame(() => query('#btn-spatial-new')?.focus());
  }

  function queueScene(scene) {
    pendingScene = scene;
    query('#spatial-save-state').textContent = '正在保存画布';
    windowRef.clearTimeout(sceneTimer);
    sceneTimer = windowRef.setTimeout(flushScene, 240);
  }

  function flushScene() {
    windowRef.clearTimeout(sceneTimer);
    sceneTimer = null;
    if (!currentId || !pendingScene) return savePromise;
    const saveId = currentId;
    const scene = pendingScene;
    pendingScene = null;
    savePromise = savePromise
      .catch(() => {})
      .then(() => adapter.updateScene(saveId, scene))
      .then((record) => {
        if (record && currentId === saveId) {
          query('#spatial-save-state').textContent = record.unchanged
            ? '画布无变化'
            : `已保存 · 版本 ${record.current_revision}`;
          syncEditorHeading();
        }
        return record;
      })
      .catch((error) => {
        const resolvedConflict = error?.status === 409 && Boolean(error.current?.scene);
        if (currentId === saveId) {
          if (resolvedConflict) {
            pendingScene = null;
            mountedIsland?.updateScene?.(error.current.scene);
            query('#spatial-save-state').textContent = '保存冲突 · 已载入最新版本';
          } else {
            pendingScene ||= scene;
            query('#spatial-save-state').textContent = '保存失败 · 等待下次修改重试';
          }
        }
        if (!resolvedConflict) console.error('Infinite canvas scene save failed', error);
        return null;
      });
    return savePromise;
  }

  function ensureRecords(force = false) {
    if (!adapter.load) return Promise.resolve(adapter.list());
    if (!recordsPromise || force) {
      query('#spatial-save-state').textContent = '正在读取画布列表';
      recordsPromise = Promise.resolve(adapter.load({ force }))
        .then((records) => {
          renderLibrary();
          query('#spatial-save-state').textContent = `${records.length} 个画布 · 已同步`;
          return records;
        })
        .catch((error) => {
          recordsPromise = null;
          query('#spatial-save-state').textContent = '画布列表读取失败';
          console.error('Infinite canvas list failed to load', error);
          return [];
        });
    }
    return recordsPromise;
  }

  async function ensureRuntime() {
    if (!runtimePromise) runtimePromise = runtimeLoader();
    return runtimePromise;
  }

  async function openCanvas(id) {
    await flushScene();
    const epoch = ++openEpoch;
    mountedIsland?.unmount?.();
    mountedIsland = null;
    query('#spatial-library').hidden = true;
    query('#spatial-editor').hidden = false;
    query('#btn-spatial-home').hidden = false;
    query('#spatial-editor-loading').hidden = false;
    query('#spatial-editor-loading').innerHTML = '<span></span><strong>正在载入画布</strong>';
    query('#spatial-canvas-host').hidden = true;
    query('#spatial-save-state').textContent = '正在载入画布';
    try {
      const record = await adapter.open(id);
      if (!record || epoch !== openEpoch) return;
      currentId = record.id;
      syncEditorHeading();
      const runtime = await ensureRuntime();
      const host = query('#spatial-canvas-host');
      if (!host || currentId !== record.id || epoch !== openEpoch) return;
      mountedIsland = runtime.mountInfiniteCanvas(host, {
        canvasDocument: adapter.get(record.id),
        onChange: queueScene,
        onReady: () => {
          documentRef.documentElement.dataset.spatialRuntime = 'loaded';
          query('#spatial-editor-loading').hidden = true;
          host.hidden = false;
          query('#spatial-save-state').textContent = '本次会话 · 已打开';
        },
      });
    } catch (error) {
      query('#spatial-editor-loading').hidden = false;
      query('#spatial-editor-loading').innerHTML = '<strong>画布加载失败</strong><button type="button" data-spatial-retry>重试</button>';
      query('#spatial-save-state').textContent = '画布暂不可用';
      console.error('Infinite canvas runtime failed to load', error);
    }
  }

  async function createCanvas() {
    const ordinal = adapter.list().length + 1;
    const buttons = [query('#btn-spatial-new'), query('#btn-spatial-empty-new')];
    buttons.forEach((button) => { button.disabled = true; });
    query('#spatial-save-state').textContent = '正在新建画布';
    try {
      const record = await adapter.create({ name: `未命名画布 ${ordinal}` });
      renderLibrary();
      return await openCanvas(record.id);
    } catch (error) {
      query('#spatial-save-state').textContent = '新建画布失败';
      console.error('Infinite canvas creation failed', error);
      return null;
    } finally {
      buttons.forEach((button) => { button.disabled = false; });
    }
  }

  function beginRename(id, returnFocus) {
    const record = adapter.get(id);
    if (!record) return;
    renameReturnFocus = returnFocus || documentRef.activeElement;
    const form = query('#spatial-rename-form');
    const input = query('#spatial-rename-input');
    form.dataset.canvasId = record.id;
    input.value = record.name;
    form.hidden = false;
    windowRef.requestAnimationFrame(() => { input.focus(); input.select(); });
  }

  function closeRename(restoreFocus = true) {
    query('#spatial-rename-form').hidden = true;
    query('#spatial-rename-form').dataset.canvasId = '';
    if (restoreFocus) renameReturnFocus?.focus?.();
    renameReturnFocus = null;
  }

  async function submitRename(event) {
    event.preventDefault();
    const form = query('#spatial-rename-form');
    const buttons = form.querySelectorAll('button');
    buttons.forEach((button) => { button.disabled = true; });
    try {
      const record = await adapter.rename(
        form.dataset.canvasId,
        query('#spatial-rename-input').value,
      );
      if (!record) return closeRename();
      renderLibrary();
      syncEditorHeading();
      query('#spatial-save-state').textContent = '画布已重命名';
      closeRename();
    } catch (error) {
      query('#spatial-save-state').textContent = '重命名失败';
      console.error('Infinite canvas rename failed', error);
    } finally {
      buttons.forEach((button) => { button.disabled = false; });
    }
  }

  function onClick(event) {
    const openButton = event.target.closest('[data-spatial-open]');
    if (openButton) return openCanvas(openButton.dataset.spatialOpen);
    const renameButton = event.target.closest('[data-spatial-rename]');
    if (renameButton) return beginRename(renameButton.dataset.spatialRename, renameButton);
    if (event.target.closest('[data-spatial-retry]') && currentId) return openCanvas(currentId);
  }

  function bind() {
    if (bound) return;
    bound = true;
    query('#btn-spatial-new').addEventListener('click', createCanvas);
    query('#btn-spatial-empty-new').addEventListener('click', createCanvas);
    query('#btn-spatial-home').addEventListener('click', () => showLibrary({ restoreFocus: true }));
    query('#btn-spatial-rename').addEventListener('click', (event) => beginRename(currentId, event.currentTarget));
    query('#spatial-rename-form').addEventListener('submit', submitRename);
    query('#spatial-rename-cancel').addEventListener('click', () => closeRename());
    query('#spatial-rename-input').addEventListener('keydown', (event) => {
      if (event.key === 'Escape') { event.preventDefault(); closeRename(); }
    });
    query('#page-canvas').addEventListener('click', onClick);
    renderLibrary();
  }

  function setPage(isActive) {
    active = Boolean(isActive);
    if (!active) {
      flushScene();
      closeRename(false);
      return;
    }
    ensureRecords().then(() => {
      if (!active) return;
      if (!currentId) showLibrary();
      windowRef.requestAnimationFrame(() => {
        const target = query('#spatial-editor').hidden
          ? query('#btn-spatial-new')
          : query('#spatial-canvas-host');
        target?.focus?.({ preventScroll: true });
      });
    });
  }

  function destroy() {
    flushScene();
    mountedIsland?.unmount?.();
    mountedIsland = null;
  }

  return {
    adapter,
    bind,
    createCanvas,
    destroy,
    flush: flushScene,
    openCanvas,
    setPage,
    showLibrary,
    get active() { return active; },
    get currentId() { return currentId; },
    get runtimeLoaded() { return Boolean(runtimePromise); },
  };
}
