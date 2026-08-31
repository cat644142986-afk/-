import { statusPanelHtml } from './status-view.js';

const ASSET_PAGE_SIZE = 40;

const COLLECTION_COPY = {
  product: { eyebrow: 'PRODUCT ASSETS', title: '产品素材', note: '单产品与多文件共用素材，但各自保留任务现场。' },
  group: { eyebrow: 'GROUP ASSETS', title: '合照素材', note: '只服务合照拆分，不与产品或抠图素材混合。' },
  cutout: { eyebrow: 'CUTOUT ASSETS', title: '抠图素材', note: '只服务快速去背景；切换工作流不会清空任务现场。' },
};

export function assetCollectionCopy(collection) {
  return COLLECTION_COPY[collection] || { eyebrow: 'ASSET LIBRARY', title: '素材管理', note: '当前素材域与其他工作流相互隔离。' };
}

export function removeAssetFromCollectionSelections(modeSelections, modeConfig, collection, assetId) {
  const next = {};
  Object.entries(modeSelections || {}).forEach(([mode, ids]) => {
    next[mode] = modeConfig[mode]?.collection === collection
      ? Array.from(ids || []).filter((id) => id !== assetId)
      : Array.from(ids || []);
  });
  return next;
}

export function modesForAssetCollection(modeConfig, collection) {
  return Object.entries(modeConfig || {})
    .filter(([, config]) => config?.collection === collection)
    .map(([mode]) => mode);
}

export function filterAndSortAssets(items, options = {}) {
  const needle = String(options.query || '').trim().toLocaleLowerCase('zh-CN');
  const filtered = Array.from(items || []).filter((asset) => {
    if (!needle) return true;
    const dimensions = asset?.width && asset?.height ? `${asset.width}x${asset.height}` : '';
    return `${asset?.name || ''} ${dimensions} ${dimensions.replace('x', '×')}`
      .toLocaleLowerCase('zh-CN')
      .includes(needle);
  });
  const sort = String(options.sort || 'custom');
  if (sort === 'custom') return filtered;
  const createdAt = (asset) => Date.parse(asset?.created_at || '') || 0;
  const compare = {
    newest: (left, right) => createdAt(right) - createdAt(left),
    oldest: (left, right) => createdAt(left) - createdAt(right),
    name: (left, right) => String(left?.name || '').localeCompare(
      String(right?.name || ''), 'zh-CN', { numeric: true, sensitivity: 'base' },
    ),
    size: (left, right) => Number(right?.size_bytes || 0) - Number(left?.size_bytes || 0),
  }[sort];
  return compare ? filtered.sort(compare) : filtered;
}

export function moveAssetInOrder(items, assetId, delta) {
  const next = Array.from(items || []);
  const from = next.findIndex((asset) => String(asset?.id) === String(assetId));
  const to = from + Number(delta || 0);
  if (from < 0 || to < 0 || to >= next.length || from === to) return next;
  const [moved] = next.splice(from, 1);
  next.splice(to, 0, moved);
  return next;
}

export function boundedAssetRenderList(items, selectedIds, limit = 60) {
  const all = Array.from(items || []);
  const bounded = all.slice(0, Math.max(1, Number(limit) || 60));
  const included = new Set(bounded.map((asset) => String(asset.id)));
  const selected = new Set(Array.from(selectedIds || [], String));
  all.forEach((asset) => {
    if (selected.has(String(asset.id)) && !included.has(String(asset.id))) {
      bounded.push(asset);
      included.add(String(asset.id));
    }
  });
  return bounded;
}

export function assetReferenceCopy(summary = {}) {
  const references = summary.references || {};
  const labels = {
    drafts: '工作草稿', jobs: '任务', child_assets: '派生结果', feedback: '反馈', reviews: '评审',
    workspace_previews: '结果预览', job_snapshots: '任务快照', generation_results: '生成版本',
    knowledge_evidence: '学习证据', execution_traces: '执行记录',
  };
  const parts = Object.entries(references)
    .filter(([, ids]) => Array.isArray(ids) && ids.length)
    .map(([key, ids]) => `${ids.length} 条${labels[key] || '引用'}`);
  if (parts.length) {
    return {
      title: `仍被 ${Number(summary.reference_count || 0)} 处历史记录引用`,
      detail: `${parts.join(' · ')}。移入回收站不会破坏这些记录。`,
      tone: 'protected',
    };
  }
  if (Array.isArray(summary.active_memberships) && summary.active_memberships.length) {
    return { title: '仍在其他素材域中使用', detail: '先从所有素材域移出，才可能进入永久清理流程。', tone: 'protected' };
  }
  if (summary.retention_pending) {
    const remaining = Number(summary.retention_remaining_days || summary.retention_days || 30);
    return { title: '当前没有历史引用', detail: `仍有约 ${remaining} 天保护期，期满前不会开放永久删除。`, tone: 'retained' };
  }
  return {
    title: '当前没有历史引用',
    detail: summary.purge_allowed ? '保护期已结束，可以永久删除；删除后无法恢复。' : '素材仍受本地安全规则保护。',
    tone: summary.purge_allowed ? 'clear' : 'retained',
  };
}

async function runWithConcurrency(items, limit, worker) {
  const pending = Array.from(items || []);
  const results = new Array(pending.length);
  let cursor = 0;
  async function runner() {
    while (cursor < pending.length) {
      const index = cursor;
      cursor += 1;
      try { results[index] = { ok: true, value: await worker(pending[index], index) }; }
      catch (error) { results[index] = { ok: false, error }; }
    }
  }
  await Promise.all(Array.from(
    { length: Math.min(Math.max(1, Number(limit) || 1), pending.length) },
    () => runner(),
  ));
  return results;
}

function formatBytes(value) {
  const bytes = Number(value || 0);
  if (!bytes) return '';
  if (bytes < 1024 * 1024) return `${Math.max(1, Math.round(bytes / 1024))} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(bytes >= 10 * 1024 * 1024 ? 0 : 1)} MB`;
}

export function createAssetManagerController({
  api, state, query, queryAll, modeConfig, escapeHtml, assetUrl, hydrateAssetUrls,
  loadWorkspace, syncLegacySelection, renderQueue, toggleAssetSelection, toast, openDrawer,
}) {
  let view = 'active';
  let trash = [];
  let search = '';
  let sort = 'custom';
  let visibleLimit = ASSET_PAGE_SIZE;
  let requestVersion = 0;
  let bound = false;
  let busy = false;
  let purgeSummary = null;
  let purgeAssetId = '';
  let armedPurgeAssetId = '';
  let purgeTimer = null;
  const selected = { active: new Set(), trash: new Set() };

  const collection = () => modeConfig[state.currentMode].collection;
  const drawerOpen = () => !query('#asset-drawer').hidden;
  const currentItems = () => view === 'trash' ? trash : (state.assetsByCollection[collection()] || []);

  function updateHeading() {
    const copy = assetCollectionCopy(collection());
    query('#asset-drawer-eyebrow').textContent = copy.eyebrow;
    query('#asset-drawer-title').textContent = copy.title;
    query('#asset-drawer-note').textContent = copy.note;
  }

  function clearPurgeState() {
    if (purgeTimer) window.clearTimeout(purgeTimer);
    purgeTimer = null;
    purgeSummary = null;
    purgeAssetId = '';
    armedPurgeAssetId = '';
    query('#asset-reference-insight').hidden = true;
    const button = query('#asset-purge-action');
    button.hidden = true;
    button.textContent = '永久删除';
  }

  function visibleState() {
    const filtered = filterAndSortAssets(currentItems(), { query: search, sort });
    return { filtered, visible: filtered.slice(0, visibleLimit) };
  }

  function pruneSelection() {
    const available = new Set(currentItems().map((asset) => String(asset.id)));
    Array.from(selected[view]).forEach((assetId) => {
      if (!available.has(String(assetId))) selected[view].delete(assetId);
    });
  }

  function renderControls(filtered, visible) {
    pruneSelection();
    const selection = selected[view];
    const visibleIds = visible.map((asset) => String(asset.id));
    const allVisibleSelected = visibleIds.length > 0 && visibleIds.every((assetId) => selection.has(assetId));
    query('#asset-manager-status').textContent = search ? `找到 ${filtered.length} 张 · 已显示 ${visible.length}` : `已显示 ${visible.length} / ${filtered.length}`;
    query('#asset-select-visible').textContent = allVisibleSelected ? '取消当前' : '选择当前';
    query('#asset-select-visible').disabled = busy || !visible.length;
    query('#asset-clear-selection').hidden = selection.size === 0;
    query('#asset-clear-selection').disabled = busy;
    const bulk = query('#asset-bulk-action');
    bulk.hidden = selection.size === 0;
    bulk.disabled = busy;
    bulk.textContent = busy ? '正在处理…' : `${view === 'trash' ? '批量恢复' : '批量移出'} ${selection.size} 张`;
  }

  function renderList() {
    const list = query('#asset-manager-list');
    const { filtered, visible } = visibleState();
    renderControls(filtered, visible);
    if (!currentItems().length) {
      list.innerHTML = statusPanelHtml('empty', {
        title: view === 'trash' ? '回收站是空的' : '当前还没有素材',
        detail: view === 'trash' ? '移出的图片会保留在这里，可随时恢复。' : '导入图片后，会持久保存在这个素材域。',
        fill: true,
      });
      return;
    }
    if (!filtered.length) {
      list.innerHTML = statusPanelHtml('empty', { title: '没有匹配的素材', detail: '换一个文件名或尺寸关键词试试。', fill: true });
      return;
    }
    const manualOrder = view === 'active' && sort === 'custom' && !search;
    const taskSelection = new Set(state.modeSelections[state.currentMode] || []);
    const allItems = currentItems();
    list.innerHTML = visible.map((asset) => {
      const assetId = String(asset.id);
      const dimensions = asset.width && asset.height ? `${asset.width}×${asset.height}` : '已持久化';
      const bytes = formatBytes(asset.size_bytes);
      const checked = selected[view].has(assetId);
      const itemIndex = allItems.findIndex((item) => String(item.id) === assetId);
      const orderActions = manualOrder
        ? `<span class="asset-manager-item__order"><button type="button" data-asset-move-id="${escapeHtml(assetId)}" data-asset-move-delta="-1" aria-label="上移 ${escapeHtml(asset.name)}" ${itemIndex <= 0 ? 'disabled' : ''}>↑</button><button type="button" data-asset-move-id="${escapeHtml(assetId)}" data-asset-move-delta="1" aria-label="下移 ${escapeHtml(asset.name)}" ${itemIndex >= allItems.length - 1 ? 'disabled' : ''}>↓</button></span>`
        : '';
      const taskAction = view === 'active'
        ? `<button class="${taskSelection.has(assetId) ? 'is-selected' : ''}" type="button" data-asset-use-id="${escapeHtml(assetId)}">${taskSelection.has(assetId) ? '已选' : '用于任务'}</button>` : '';
      const stateAction = view === 'trash'
        ? `<button class="is-primary" type="button" data-asset-restore-id="${escapeHtml(assetId)}">恢复</button>`
        : `<button class="is-danger" type="button" data-asset-remove-id="${escapeHtml(assetId)}">移出</button>`;
      return `<article class="asset-manager-item ${checked ? 'is-checked' : ''}">
        <label class="asset-manager-item__check"><input type="checkbox" data-asset-select-id="${escapeHtml(assetId)}" ${checked ? 'checked' : ''} /><span aria-hidden="true">${checked ? '✓' : ''}</span><span class="sr-only">批量选择 ${escapeHtml(asset.name)}</span></label>
        <img src="${escapeHtml(assetUrl(asset))}" alt="" loading="lazy" decoding="async" />
        <div class="asset-manager-item__copy"><strong title="${escapeHtml(asset.name)}">${escapeHtml(asset.name || '未命名素材')}</strong><small>${escapeHtml([dimensions, bytes, view === 'trash' ? '可恢复' : '当前素材域'].filter(Boolean).join(' · '))}</small></div>
        ${orderActions}<div class="asset-manager-item__actions">${taskAction}<button type="button" data-asset-reference-id="${escapeHtml(assetId)}">占用</button>${stateAction}</div>
      </article>`;
    }).join('') + (visible.length < filtered.length
      ? `<button class="asset-manager-more" id="asset-manager-more" type="button">再显示 ${Math.min(ASSET_PAGE_SIZE, filtered.length - visible.length)} 张 <small>剩余 ${filtered.length - visible.length} 张</small></button>` : '');
    queryAll('[data-asset-select-id]', list).forEach((input) => input.addEventListener('change', () => {
      if (input.checked) selected[view].add(input.dataset.assetSelectId);
      else selected[view].delete(input.dataset.assetSelectId);
      renderList();
    }));
    queryAll('[data-asset-use-id]', list).forEach((button) => button.addEventListener('click', () => { toggleAssetSelection(button.dataset.assetUseId); renderList(); }));
    queryAll('[data-asset-remove-id]', list).forEach((button) => button.addEventListener('click', () => remove(button.dataset.assetRemoveId)));
    queryAll('[data-asset-restore-id]', list).forEach((button) => button.addEventListener('click', () => restore(collection(), button.dataset.assetRestoreId)));
    queryAll('[data-asset-reference-id]', list).forEach((button) => button.addEventListener('click', () => inspectReferences(button.dataset.assetReferenceId)));
    queryAll('[data-asset-move-id]', list).forEach((button) => button.addEventListener('click', () => reorder(button.dataset.assetMoveId, Number(button.dataset.assetMoveDelta))));
    query('#asset-manager-more')?.addEventListener('click', () => { visibleLimit += ASSET_PAGE_SIZE; renderList(); });
  }

  async function loadTrash() {
    const version = ++requestVersion;
    query('#asset-manager-list').innerHTML = statusPanelHtml('loading', { title: '正在读取回收站', detail: '正在核对可恢复素材。', fill: true });
    try {
      const targetCollection = collection();
      const result = await api.getTrash(targetCollection);
      if (version !== requestVersion || targetCollection !== collection()) return;
      trash = result.collections?.[targetCollection] || [];
      await hydrateAssetUrls(trash);
      if (version !== requestVersion || view !== 'trash') return;
      query('#asset-trash-count').textContent = String(trash.length);
      renderList();
    } catch (error) {
      if (version !== requestVersion) return;
      const list = query('#asset-manager-list');
      list.innerHTML = statusPanelHtml('offline', {
        title: '回收站暂时离线',
        detail: String(error?.message || error || '本地素材服务暂不可用'),
        fill: true,
        action: { label: '重新读取', attribute: 'data-asset-status-action', value: 'retry-trash' },
      });
      query('[data-asset-status-action="retry-trash"]', list)?.addEventListener('click', loadTrash);
    }
  }

  function render() {
    updateHeading();
    queryAll('[data-asset-view]').forEach((button) => {
      const active = button.dataset.assetView === view;
      button.classList.toggle('active', active);
      button.setAttribute('aria-selected', String(active));
    });
    query('#asset-manager-search').value = search;
    query('#asset-manager-sort').value = sort;
    const activeItems = state.assetsByCollection[collection()] || [];
    query('#asset-active-count').textContent = String(activeItems.length);
    if (view === 'trash') loadTrash();
    else renderList();
  }

  async function refreshAfterMutation(targetCollection) {
    const activeMode = modeConfig[state.currentMode]?.collection === targetCollection ? state.currentMode : modesForAssetCollection(modeConfig, targetCollection)[0];
    if (activeMode) await loadWorkspace(activeMode, true);
    syncLegacySelection();
    if (modeConfig[state.currentMode]?.collection === targetCollection) renderQueue();
  }

  async function remove(assetId) {
    if (!assetId || !state.backendReady || busy) return;
    const mode = state.currentMode;
    const targetCollection = modeConfig[mode].collection;
    const asset = (state.assetsByCollection[targetCollection] || []).find((item) => item.id === assetId);
    try {
      await api.removeAssetFromCollection(targetCollection, assetId);
      state.modeSelections = removeAssetFromCollectionSelections(state.modeSelections, modeConfig, targetCollection, assetId);
      await refreshAfterMutation(targetCollection);
      if (drawerOpen()) renderList();
      toast(`${asset?.name || '素材'} 已移入当前域回收站`, 'success', 8000, { label: '撤销', onClick: () => restore(targetCollection, assetId) });
    } catch (error) { toast(`移除失败：${error}`, 'error'); }
  }

  async function restore(targetCollection, assetId) {
    if (!assetId || !targetCollection || !state.backendReady || busy) return;
    try {
      await api.restoreAssetToCollection(targetCollection, assetId);
      await refreshAfterMutation(targetCollection);
      trash = trash.filter((asset) => String(asset.id) !== String(assetId));
      selected.trash.delete(String(assetId));
      if (drawerOpen()) view === 'trash' ? loadTrash() : renderList();
      toast('素材已恢复到当前素材域', 'success');
    } catch (error) { toast(`恢复失败：${error}`, 'error'); }
  }

  async function mutateSelected() {
    if (busy || !state.backendReady) return;
    const mutationView = view;
    const targetCollection = collection();
    const ids = Array.from(selected[mutationView]);
    if (!ids.length) return;
    busy = true;
    renderList();
    const operation = mutationView === 'trash'
      ? (assetId) => api.restoreAssetToCollection(targetCollection, assetId)
      : (assetId) => api.removeAssetFromCollection(targetCollection, assetId);
    const results = await runWithConcurrency(ids, 4, operation);
    const succeeded = ids.filter((_, index) => results[index]?.ok);
    const failed = ids.filter((_, index) => !results[index]?.ok);
    if (mutationView === 'active') succeeded.forEach((assetId) => {
      state.modeSelections = removeAssetFromCollectionSelections(state.modeSelections, modeConfig, targetCollection, assetId);
    });
    selected[mutationView] = new Set(failed);
    await refreshAfterMutation(targetCollection);
    busy = false;
    clearPurgeState();
    if (view === 'trash') await loadTrash();
    else renderList();
    toast(failed.length ? `${succeeded.length} 张处理完成，${failed.length} 张失败` : `${succeeded.length} 张素材已${mutationView === 'trash' ? '恢复' : '移入回收站'}`, failed.length ? 'error' : 'success', 5200);
  }

  async function reorder(assetId, delta) {
    if (busy || view !== 'active' || sort !== 'custom' || search) return;
    const targetCollection = collection();
    const current = state.assetsByCollection[targetCollection] || [];
    const ordered = moveAssetInOrder(current, assetId, delta);
    if (ordered.every((asset, index) => asset.id === current[index]?.id)) return;
    busy = true;
    renderList();
    try {
      const response = await api.reorderCollectionAssets(targetCollection, ordered.map((asset) => asset.id));
      const assets = Array.isArray(response?.assets) ? response.assets : ordered;
      await hydrateAssetUrls(assets);
      state.assetsByCollection[targetCollection] = assets;
      if (modeConfig[state.currentMode]?.collection === targetCollection) state.assets = assets;
      renderQueue();
      toast('素材顺序已保存', 'success');
    } catch (error) { toast(`排序保存失败：${error}`, 'error'); }
    finally { busy = false; renderList(); }
  }

  function configurePurgeAction(summary, assetId) {
    const button = query('#asset-purge-action');
    const allowed = view === 'trash' && Boolean(summary?.purge_allowed);
    button.hidden = !allowed;
    button.disabled = false;
    button.textContent = armedPurgeAssetId === assetId ? '再次确认永久删除' : '永久删除';
  }

  async function inspectReferences(assetId) {
    const insight = query('#asset-reference-insight');
    insight.hidden = false;
    insight.dataset.tone = 'loading';
    query('#asset-reference-title').textContent = '正在检查素材占用…';
    query('#asset-reference-detail').textContent = '读取任务、草稿、结果和学习证据。';
    query('#asset-purge-action').hidden = true;
    try {
      const summary = await api.getAssetReferences(assetId);
      const copy = assetReferenceCopy(summary);
      purgeSummary = summary;
      purgeAssetId = assetId;
      armedPurgeAssetId = '';
      insight.dataset.tone = copy.tone;
      query('#asset-reference-title').textContent = copy.title;
      query('#asset-reference-detail').textContent = copy.detail;
      configurePurgeAction(summary, assetId);
    } catch (error) {
      purgeSummary = null;
      purgeAssetId = '';
      insight.dataset.tone = 'error';
      query('#asset-reference-title').textContent = '暂时无法读取占用信息';
      query('#asset-reference-detail').textContent = String(error);
    }
  }

  async function purgeInspectedAsset() {
    if (busy || !purgeSummary?.purge_allowed || !purgeAssetId || view !== 'trash') return;
    if (armedPurgeAssetId !== purgeAssetId) {
      armedPurgeAssetId = purgeAssetId;
      configurePurgeAction(purgeSummary, purgeAssetId);
      query('#asset-reference-detail').textContent = '此操作会删除本地原文件且无法恢复。请在 8 秒内再次点击确认。';
      purgeTimer = window.setTimeout(() => {
        armedPurgeAssetId = '';
        if (purgeAssetId) inspectReferences(purgeAssetId);
      }, 8000);
      return;
    }
    const assetId = purgeAssetId;
    busy = true;
    const button = query('#asset-purge-action');
    button.disabled = true;
    button.textContent = '正在永久删除…';
    try {
      await api.purgeAsset(assetId);
      trash = trash.filter((asset) => String(asset.id) !== String(assetId));
      selected.trash.delete(String(assetId));
      clearPurgeState();
      renderList();
      query('#asset-trash-count').textContent = String(trash.length);
      toast('素材与无引用的本地原文件已永久删除', 'success', 5200);
    } catch (error) {
      armedPurgeAssetId = '';
      toast(`永久删除失败：${error}`, 'error', 6500);
      await inspectReferences(assetId);
    } finally { busy = false; }
  }

  function open() {
    view = 'active';
    search = '';
    sort = 'custom';
    visibleLimit = ASSET_PAGE_SIZE;
    clearPurgeState();
    openDrawer('assets');
    render();
  }

  function sync() {
    requestVersion += 1;
    if (drawerOpen()) render();
  }

  function bind() {
    if (bound) return;
    query('#btn-asset-manager').addEventListener('click', open);
    queryAll('[data-asset-view]').forEach((button) => button.addEventListener('click', () => {
      view = button.dataset.assetView;
      search = '';
      sort = 'custom';
      visibleLimit = ASSET_PAGE_SIZE;
      clearPurgeState();
      render();
    }));
    query('#asset-manager-search').addEventListener('input', (event) => { search = event.target.value.trim(); visibleLimit = ASSET_PAGE_SIZE; renderList(); });
    query('#asset-manager-sort').addEventListener('change', (event) => { sort = event.target.value; visibleLimit = ASSET_PAGE_SIZE; renderList(); });
    query('#asset-select-visible').addEventListener('click', () => {
      const { visible } = visibleState();
      const ids = visible.map((asset) => String(asset.id));
      const allSelected = ids.length && ids.every((assetId) => selected[view].has(assetId));
      ids.forEach((assetId) => allSelected ? selected[view].delete(assetId) : selected[view].add(assetId));
      renderList();
    });
    query('#asset-clear-selection').addEventListener('click', () => { selected[view].clear(); renderList(); });
    query('#asset-bulk-action').addEventListener('click', mutateSelected);
    query('#asset-purge-action').addEventListener('click', purgeInspectedAsset);
    bound = true;
  }

  return { bind, inspectReferences, open, remove, render, restore, sync };
}
