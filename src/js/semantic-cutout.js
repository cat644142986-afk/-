const STRATEGIES = new Set(['foreground', 'semantic']);

function clamp(value, min, max) {
  return Math.min(max, Math.max(min, Number(value) || 0));
}

function normalizeRegion(region, index, query) {
  const raw = Array.isArray(region?.bbox) ? region.bbox : [];
  const x = clamp(raw[0], 0, 1);
  const y = clamp(raw[1], 0, 1);
  const width = clamp(raw[2], 0, 1 - x);
  const height = clamp(raw[3], 0, 1 - y);
  return {
    id: String(region?.id || `target-${index + 1}`),
    label: String(region?.label || query || `目标 ${index + 1}`).trim(),
    bbox: [x, y, width, height].map((value) => Number(value.toFixed(6))),
  };
}

export function createSemanticCutoutState(raw = {}) {
  const strategy = STRATEGIES.has(raw?.strategy) ? raw.strategy : 'foreground';
  const query = String(raw?.query || '').trim().slice(0, 80);
  const targetCount = Math.round(clamp(raw?.target_count ?? 1, 1, 8));
  const regions = Array.from(raw?.regions || [], (region, index) => normalizeRegion(region, index, query))
    .filter((region) => region.bbox[2] > 0 && region.bbox[3] > 0);
  return {
    strategy,
    query,
    target_count: targetCount,
    source_asset_id: String(raw?.source_asset_id || ''),
    status: raw?.status === 'confirmed' ? 'confirmed' : 'draft',
    method: raw?.method === 'manual-box' ? 'manual-box' : 'manual-box',
    digest: String(raw?.digest || ''),
    regions,
  };
}

export function updateSemanticCutoutState(current, patch = {}) {
  const before = createSemanticCutoutState(current);
  const next = createSemanticCutoutState({ ...before, ...patch });
  const invalidatesConfirmation = (
    next.query !== before.query
    || next.target_count !== before.target_count
    || (
      Object.prototype.hasOwnProperty.call(patch, 'source_asset_id')
      && next.source_asset_id !== before.source_asset_id
    )
  );
  const carriesFreshConfirmation = (
    patch?.status === 'confirmed'
    && Boolean(patch?.digest)
    && Array.isArray(patch?.regions)
  );
  if (invalidatesConfirmation && !carriesFreshConfirmation) {
    next.status = 'draft';
    next.digest = '';
    next.source_asset_id = '';
    next.regions = [];
  }
  return next;
}

export function semanticCutoutReadiness(rawState, selectedAssetIds = []) {
  const state = createSemanticCutoutState(rawState);
  const sourceIds = Array.from(selectedAssetIds || [], String);
  if (state.strategy === 'foreground') {
    return sourceIds.length
      ? { action: 'submit', ready: true, message: `${sourceIds.length} 张素材 · 本地分离全部前景` }
      : { action: 'select-source', ready: false, message: '从抠图素材中选择后可入队' };
  }
  if (!sourceIds.length) return { action: 'select-source', ready: false, message: '先选择 1 张需要选物的图片' };
  if (sourceIds.length !== 1) return { action: 'single-source', ready: false, message: '智能选物首版每次确认 1 张；请只保留一张选中素材' };
  if (!state.query) return { action: 'describe', ready: false, message: '填写要保留的物体名称，例如“汉堡”' };
  const confirmed = (
    state.status === 'confirmed'
    && Boolean(state.digest)
    && state.source_asset_id === sourceIds[0]
    && state.regions.length === state.target_count
  );
  if (!confirmed) {
    return {
      action: 'confirm',
      ready: false,
      message: `请在原图上确认 ${state.target_count} 个“${state.query}”`,
    };
  }
  return {
    action: 'submit',
    ready: true,
    message: `已确认 ${state.target_count} 个“${state.query}” · 本地执行，不消耗生图额度`,
  };
}

export function semanticCutoutPayload(rawState, selectedAssetIds = []) {
  const state = createSemanticCutoutState(rawState);
  if (state.strategy === 'foreground') return { strategy: 'foreground' };
  const sourceId = String(selectedAssetIds?.[0] || state.source_asset_id || '');
  return {
    strategy: 'semantic',
    query: state.query,
    target_count: state.target_count,
    sources: {
      [sourceId]: {
        source_asset_id: sourceId,
        status: state.status,
        method: state.method,
        digest: state.digest,
        regions: state.regions.map((region) => ({ ...region, bbox: [...region.bbox] })),
      },
    },
  };
}

export function semanticCutoutStageCopy(stage) {
  return {
    recognition: '目标名称无法识别，请改写名称或手动框选',
    selection: '目标数量或选区不一致，请重新确认',
    segmentation: '框选区域内没有得到有效前景，请扩大选区',
    edge: '边缘精修失败，请改用快速去背景或重新框选',
  }[stage] || '智能选物暂时无法继续，请检查目标确认状态';
}
