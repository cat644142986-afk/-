const STRATEGIES = new Set(['foreground', 'semantic']);

function clamp(value, min, max) {
  return Math.min(max, Math.max(min, Number(value) || 0));
}

function normalizeModelQuery(value) {
  return String(value || '').trim().toLowerCase().replace(/\.+$/, '').slice(0, 80);
}

function normalizeRegion(region, index, query) {
  const raw = Array.isArray(region?.bbox) ? region.bbox : [];
  const x = clamp(raw[0], 0, 1);
  const y = clamp(raw[1], 0, 1);
  const width = clamp(raw[2], 0, 1 - x);
  const height = clamp(raw[3], 0, 1 - y);
  const rawOrigin = String(region?.origin || '');
  const origin = ['automatic', 'automatic-review'].includes(rawOrigin) ? rawOrigin : 'manual';
  const normalized = {
    id: String(region?.id || `target-${index + 1}`),
    label: String(region?.label || query || `目标 ${index + 1}`).trim(),
    bbox: [x, y, width, height].map((value) => Number(value.toFixed(6))),
    origin,
  };
  if (['automatic', 'automatic-review'].includes(normalized.origin)) {
    normalized.confidence = Number(clamp(region?.confidence, 0, 1).toFixed(4));
  }
  return normalized;
}

function normalizeMaskEdit(edit) {
  const mode = edit?.mode === 'include' ? 'include' : edit?.mode === 'exclude' ? 'exclude' : '';
  const points = Array.from(edit?.points || [], (point) => [
    Number(clamp(point?.[0], 0, 1).toFixed(6)),
    Number(clamp(point?.[1], 0, 1).toFixed(6)),
  ]).filter((point) => point.every(Number.isFinite));
  if (!mode || !points.length) return null;
  return {
    mode,
    points,
    radius: Number(clamp(edit?.radius ?? 0.018, 0.003, 0.1).toFixed(6)),
  };
}

export function createSemanticCutoutState(raw = {}) {
  const strategy = STRATEGIES.has(raw?.strategy) ? raw.strategy : 'foreground';
  const query = String(raw?.query || '').trim().slice(0, 80);
  const modelQuery = normalizeModelQuery(raw?.model_query);
  const modelQueryOverride = normalizeModelQuery(raw?.model_query_override);
  const targetCount = Math.round(clamp(raw?.target_count ?? 1, 1, 8));
  const regions = Array.from(raw?.regions || [], (region, index) => normalizeRegion(region, index, query))
    .filter((region) => region.bbox[2] > 0 && region.bbox[3] > 0);
  const maskEdits = Array.from(raw?.mask_edits || [], normalizeMaskEdit).filter(Boolean);
  return {
    strategy,
    query,
    model_query: modelQuery,
    model_query_override: modelQueryOverride,
    target_count: targetCount,
    source_asset_id: String(raw?.source_asset_id || ''),
    status: raw?.status === 'confirmed' ? 'confirmed' : 'draft',
    method: ['manual-box', 'model-candidate-confirmed', 'model-assisted-confirmed'].includes(raw?.method)
      ? raw.method
      : 'manual-box',
    digest: String(raw?.digest || ''),
    regions,
    mask_edits: maskEdits,
  };
}

export function updateSemanticCutoutState(current, patch = {}) {
  const before = createSemanticCutoutState(current);
  const next = createSemanticCutoutState({ ...before, ...patch });
  const invalidatesConfirmation = (
    next.query !== before.query
    || next.model_query_override !== before.model_query_override
    || next.target_count !== before.target_count
    || (
      Object.prototype.hasOwnProperty.call(patch, 'source_asset_id')
      && next.source_asset_id !== before.source_asset_id
    )
    || (
      Object.prototype.hasOwnProperty.call(patch, 'mask_edits')
      && JSON.stringify(next.mask_edits) !== JSON.stringify(before.mask_edits)
    )
  );
  const carriesFreshConfirmation = (
    patch?.status === 'confirmed'
    && Boolean(patch?.digest)
    && Array.isArray(patch?.regions)
  );
  if (invalidatesConfirmation && !carriesFreshConfirmation) {
    next.status = 'draft';
    next.model_query = '';
    next.digest = '';
    next.source_asset_id = '';
    next.regions = [];
    next.mask_edits = [];
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
    model_query: state.model_query,
    target_count: state.target_count,
    sources: {
      [sourceId]: {
        source_asset_id: sourceId,
        status: state.status,
        method: state.method,
        digest: state.digest,
        regions: state.regions.map((region) => ({ ...region, bbox: [...region.bbox] })),
        mask_edits: state.mask_edits.map((edit) => ({
          ...edit,
          points: edit.points.map((point) => [...point]),
        })),
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

export function semanticGroundingPresentation(rawPreview = {}) {
  const grounding = rawPreview?.grounding || {};
  const status = String(rawPreview?.candidate_status || grounding?.status || 'unavailable');
  const fallback = {
    candidates: '本地模型已给出候选，请逐个检查；确认前不会开始抠图',
    low_confidence: '候选置信度不足，请修正选区或补充框选',
    no_match: '没有找到可靠候选，请手动框选目标',
    query_unmapped: '当前名称还没有离线识别词；可填写英文识别词或手动框选',
    unavailable: '当前未配置本地目标定位模型，请手动框选',
    failed: '本地自动定位失败，请手动框选；当前图片仍可继续处理',
    manual_regions: '请检查手动选区后确认',
  }[status] || '请检查目标选区后确认';
  const tone = {
    candidates: 'candidate',
    low_confidence: 'warning',
    failed: 'error',
  }[status] || 'manual';
  return {
    status,
    tone,
    message: String(rawPreview?.message || grounding?.message || fallback),
  };
}
