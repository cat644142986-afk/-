const COLLECTION_COPY = {
  product: {
    eyebrow: 'PRODUCT ASSETS',
    title: '产品素材',
    note: '单产品与多文件共用素材，但各自保留任务现场。',
  },
  group: {
    eyebrow: 'GROUP ASSETS',
    title: '合照素材',
    note: '只服务合照拆分，不与产品或抠图素材混合。',
  },
  cutout: {
    eyebrow: 'CUTOUT ASSETS',
    title: '抠图素材',
    note: '只服务快速去背景；切换工作流不会清空任务现场。',
  },
};

export function assetCollectionCopy(collection) {
  return COLLECTION_COPY[collection] || {
    eyebrow: 'ASSET LIBRARY',
    title: '素材管理',
    note: '当前素材域与其他工作流相互隔离。',
  };
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

export function assetReferenceCopy(summary = {}) {
  const references = summary.references || {};
  const labels = {
    drafts: '工作草稿',
    jobs: '任务',
    child_assets: '派生结果',
    feedback: '反馈',
    reviews: '评审',
    workspace_previews: '结果预览',
    job_snapshots: '任务快照',
    generation_results: '生成版本',
    knowledge_evidence: '学习证据',
    execution_traces: '执行记录',
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
  if (summary.retention_pending) {
    return {
      title: '当前没有历史引用',
      detail: `仍处于 ${Number(summary.retention_days || 30)} 天保护期，避免误删原始素材。`,
      tone: 'retained',
    };
  }
  return {
    title: '当前没有历史引用',
    detail: summary.purge_allowed ? '保护期已结束，可在后续永久清理流程中处理。' : '素材仍受本地安全规则保护。',
    tone: summary.purge_allowed ? 'clear' : 'retained',
  };
}

export function createAssetManagerController({
  api,
  state,
  query,
  queryAll,
  modeConfig,
  escapeHtml,
  assetUrl,
  hydrateAssetUrls,
  loadWorkspace,
  syncLegacySelection,
  renderQueue,
  toast,
  openDrawer,
}) {
  let view = 'active';
  let trash = [];
  let requestVersion = 0;
  let bound = false;

  const collection = () => modeConfig[state.currentMode].collection;
  const drawerOpen = () => !query('#asset-drawer').hidden;

  function updateHeading() {
    const copy = assetCollectionCopy(collection());
    query('#asset-drawer-eyebrow').textContent = copy.eyebrow;
    query('#asset-drawer-title').textContent = copy.title;
    query('#asset-drawer-note').textContent = copy.note;
  }

  function renderList(items, kind) {
    const list = query('#asset-manager-list');
    if (!items.length) {
      list.innerHTML = `<div class="asset-manager-empty"><strong>${kind === 'trash' ? '回收站是空的' : '当前还没有素材'}</strong><p>${kind === 'trash' ? '移出的图片会保留在这里，可随时恢复。' : '从创作工作台导入图片后，会持久保存在这个素材域。'}</p></div>`;
      return;
    }
    list.innerHTML = items.map((asset) => {
      const dimensions = asset.width && asset.height ? `${asset.width}×${asset.height}` : '已持久化';
      const action = kind === 'trash'
        ? `<div class="asset-manager-item__actions"><button type="button" data-asset-reference-id="${escapeHtml(asset.id)}">占用</button><button class="is-primary" type="button" data-asset-restore-id="${escapeHtml(asset.id)}">恢复</button></div>`
        : `<div class="asset-manager-item__actions"><button type="button" data-asset-reference-id="${escapeHtml(asset.id)}">占用</button><button class="is-danger" type="button" data-asset-remove-id="${escapeHtml(asset.id)}">移出</button></div>`;
      return `<article class="asset-manager-item">
        <img src="${escapeHtml(assetUrl(asset))}" alt="" loading="lazy" />
        <div class="asset-manager-item__copy"><strong title="${escapeHtml(asset.name)}">${escapeHtml(asset.name || '未命名素材')}</strong><small>${escapeHtml(dimensions)} · ${kind === 'trash' ? '可恢复' : '当前素材域'}</small></div>
        ${action}
      </article>`;
    }).join('');
    queryAll('[data-asset-remove-id]', list).forEach((button) => button.addEventListener('click', () => remove(button.dataset.assetRemoveId)));
    queryAll('[data-asset-restore-id]', list).forEach((button) => button.addEventListener('click', () => restore(collection(), button.dataset.assetRestoreId)));
    queryAll('[data-asset-reference-id]', list).forEach((button) => button.addEventListener('click', () => inspectReferences(button.dataset.assetReferenceId)));
  }

  async function loadTrash() {
    const version = ++requestVersion;
    query('#asset-manager-list').innerHTML = '<div class="asset-manager-empty"><strong>正在读取回收站…</strong></div>';
    try {
      const targetCollection = collection();
      const result = await api.getTrash(targetCollection);
      if (version !== requestVersion || targetCollection !== collection()) return;
      trash = result.collections?.[targetCollection] || [];
      await hydrateAssetUrls(trash);
      if (version !== requestVersion || view !== 'trash') return;
      renderList(trash, 'trash');
      query('#asset-trash-count').textContent = String(trash.length);
    } catch (error) {
      if (version !== requestVersion) return;
      query('#asset-manager-list').innerHTML = `<div class="asset-manager-empty"><strong>回收站暂不可用</strong><p>${escapeHtml(error)}</p></div>`;
    }
  }

  function render() {
    updateHeading();
    query('#asset-reference-insight').hidden = true;
    queryAll('[data-asset-view]').forEach((button) => {
      const active = button.dataset.assetView === view;
      button.classList.toggle('active', active);
      button.setAttribute('aria-selected', String(active));
    });
    const activeItems = state.assetsByCollection[collection()] || [];
    query('#asset-active-count').textContent = String(activeItems.length);
    if (view === 'trash') loadTrash();
    else renderList(activeItems, 'active');
  }

  async function remove(assetId) {
    if (!assetId || !state.backendReady) return;
    const mode = state.currentMode;
    const targetCollection = modeConfig[mode].collection;
    const asset = (state.assetsByCollection[targetCollection] || []).find((item) => item.id === assetId);
    try {
      await api.removeAssetFromCollection(targetCollection, assetId);
      state.modeSelections = removeAssetFromCollectionSelections(
        state.modeSelections,
        modeConfig,
        targetCollection,
        assetId,
      );
      await Promise.all(
        modesForAssetCollection(modeConfig, targetCollection)
          .map((affectedMode) => loadWorkspace(affectedMode, true)),
      );
      syncLegacySelection();
      if (state.currentMode === mode) renderQueue();
      if (drawerOpen()) render();
      toast(`${asset?.name || '素材'} 已移入当前域回收站`, 'success', 8000, {
        label: '撤销',
        onClick: () => restore(targetCollection, assetId, mode),
      });
    } catch (error) {
      toast(`移除失败：${error}`, 'error');
    }
  }

  async function restore(targetCollection, assetId, originatingMode = state.currentMode) {
    if (!assetId || !targetCollection || !state.backendReady) return;
    try {
      await api.restoreAssetToCollection(targetCollection, assetId);
      const activeMode = modeConfig[state.currentMode]?.collection === targetCollection
        ? state.currentMode
        : originatingMode;
      if (modeConfig[activeMode]?.collection === targetCollection) await loadWorkspace(activeMode, true);
      if (drawerOpen()) render();
      toast('素材已恢复到当前素材域', 'success');
    } catch (error) {
      toast(`恢复失败：${error}`, 'error');
    }
  }

  async function inspectReferences(assetId) {
    const insight = query('#asset-reference-insight');
    insight.hidden = false;
    insight.dataset.tone = 'loading';
    query('#asset-reference-title').textContent = '正在检查素材占用…';
    query('#asset-reference-detail').textContent = '读取任务、草稿、结果和学习证据。';
    try {
      const copy = assetReferenceCopy(await api.getAssetReferences(assetId));
      insight.dataset.tone = copy.tone;
      query('#asset-reference-title').textContent = copy.title;
      query('#asset-reference-detail').textContent = copy.detail;
    } catch (error) {
      insight.dataset.tone = 'error';
      query('#asset-reference-title').textContent = '暂时无法读取占用信息';
      query('#asset-reference-detail').textContent = String(error);
    }
  }

  function open() {
    view = 'active';
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
      render();
    }));
    bound = true;
  }

  return { bind, inspectReferences, open, remove, render, restore, sync };
}
