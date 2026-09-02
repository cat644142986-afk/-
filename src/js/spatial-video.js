export const SPATIAL_VIDEO_CONTRACT_VERSION = 'image-to-video-v1';
export const SPATIAL_VIDEO_OFFLINE_PROVIDER = 'offline-preview-v1';
export const SPATIAL_VIDEO_COMMAND_ID = 'command:image-to-video';
export const SPATIAL_VIDEO_RATIOS = Object.freeze(['1:1', '16:9', '9:16', '4:3', '3:4']);
export const SPATIAL_VIDEO_DURATIONS = Object.freeze([3, 5, 8, 10]);

const ACTIVE_JOB_STATUSES = new Set(['queued', 'running', 'paused', 'canceling']);
const SETTLED_JOB_STATUSES = new Set(['completed', 'partial', 'failed', 'canceled', 'interrupted']);

const ASSET_ID = /^ast[_:][A-Za-z0-9:_-]{1,155}$/;
const SPATIAL_CANVAS_ID = /^[a-z][a-z0-9._:-]{2,127}$/;
const PARAMETER_FIELDS = new Set([
  'prompt',
  'output_ratio',
  'duration_seconds',
  'motion_intensity',
  'first_frame_asset_id',
  'last_frame_asset_id',
  'provider',
  'provider_call_confirmed',
  'automatic_paid_retry',
  'output_root',
]);
const COST_DRAFT_FIELDS = Object.freeze([
  'sourceAssetId',
  'prompt',
  'outputRatio',
  'durationSeconds',
  'motionIntensity',
  'lastFrameAssetId',
  'provider',
]);

function cleanText(value) {
  return String(value || '').trim().replace(/\s+/g, ' ');
}

function assetId(value, label, optional = false) {
  const normalized = String(value || '').trim();
  if (optional && !normalized) return null;
  if (!ASSET_ID.test(normalized)) throw new TypeError(`${label} is invalid`);
  return normalized;
}

function enumNumber(value, allowed, label, fallback) {
  const candidate = value === undefined || value === null || value === '' ? fallback : Number(value);
  if (!Number.isInteger(candidate) || !allowed.includes(candidate)) {
    throw new RangeError(`video ${label} is unsupported`);
  }
  return candidate;
}

function requestId() {
  const suffix = globalThis.crypto?.randomUUID?.()
    || `${Date.now()}-${Math.random().toString(16).slice(2)}`;
  return `video-request:${String(suffix).toLowerCase()}`;
}

export function normalizeSpatialVideoParameters(parameters = {}, { sourceAssetId } = {}) {
  const raw = parameters && typeof parameters === 'object' ? { ...parameters } : {};
  const unknown = Object.keys(raw).filter((key) => !PARAMETER_FIELDS.has(key));
  if (unknown.length) throw new TypeError(`unsupported image-to-video parameters: ${unknown.sort().join(', ')}`);

  const prompt = cleanText(raw.prompt);
  if (prompt.length < 2 || prompt.length > 600) {
    throw new RangeError('video prompt must contain 2 to 600 characters');
  }
  const outputRatio = String(raw.output_ratio || '1:1').trim();
  if (!SPATIAL_VIDEO_RATIOS.includes(outputRatio)) {
    throw new RangeError('video output ratio is unsupported');
  }
  const durationSeconds = enumNumber(
    raw.duration_seconds,
    SPATIAL_VIDEO_DURATIONS,
    'duration',
    5,
  );
  const motionIntensity = enumNumber(
    raw.motion_intensity,
    [1, 2, 3, 4, 5, 6, 7, 8, 9, 10],
    'motion intensity',
    3,
  );
  const sourceId = assetId(sourceAssetId, 'source_asset_id');
  const firstFrameId = assetId(raw.first_frame_asset_id || sourceId, 'first_frame_asset_id');
  if (firstFrameId !== sourceId) throw new RangeError('first frame must match the selected source asset');
  const lastFrameId = assetId(raw.last_frame_asset_id, 'last_frame_asset_id', true);
  const provider = String(raw.provider || SPATIAL_VIDEO_OFFLINE_PROVIDER).trim();
  if (provider !== SPATIAL_VIDEO_OFFLINE_PROVIDER) {
    throw new RangeError('video provider is unsupported');
  }
  if (raw.provider_call_confirmed && provider === SPATIAL_VIDEO_OFFLINE_PROVIDER) {
    throw new RangeError('offline video preview cannot record a paid call');
  }
  if (raw.automatic_paid_retry) throw new RangeError('video tasks cannot authorize automatic paid retry');

  const normalized = {
    contract_version: SPATIAL_VIDEO_CONTRACT_VERSION,
    prompt,
    output_ratio: outputRatio,
    duration_seconds: durationSeconds,
    motion_intensity: motionIntensity,
    first_frame_asset_id: firstFrameId,
    last_frame_asset_id: lastFrameId,
    provider,
    provider_call_confirmed: false,
    automatic_paid_retry: false,
  };
  if (Object.hasOwn(raw, 'output_root')) normalized.output_root = String(raw.output_root || '');
  return normalized;
}

export function createSpatialVideoDraft({
  canvasId = '',
  sourceAssetId = '',
  lineageParentId = '',
  productProfileVersionId = '',
  prompt = '',
  outputRatio = '1:1',
  durationSeconds = 5,
  motionIntensity = 3,
  lastFrameAssetId = '',
  provider = SPATIAL_VIDEO_OFFLINE_PROVIDER,
} = {}) {
  return {
    canvasId: String(canvasId || ''),
    sourceAssetId: String(sourceAssetId || ''),
    lineageParentId: String(lineageParentId || ''),
    productProfileVersionId: String(productProfileVersionId || ''),
    prompt: String(prompt || ''),
    outputRatio: String(outputRatio || '1:1'),
    durationSeconds: Number(durationSeconds || 5),
    motionIntensity: Number(motionIntensity || 3),
    lastFrameAssetId: String(lastFrameAssetId || ''),
    provider: String(provider || SPATIAL_VIDEO_OFFLINE_PROVIDER),
    callConfirmed: false,
    requestId: '',
  };
}

export function updateSpatialVideoDraft(draft, patch = {}) {
  const current = createSpatialVideoDraft(draft || {});
  current.callConfirmed = Boolean(draft?.callConfirmed);
  current.requestId = String(draft?.requestId || '');
  const next = { ...current };
  COST_DRAFT_FIELDS.forEach((field) => {
    if (Object.hasOwn(patch, field)) next[field] = patch[field];
  });
  ['canvasId', 'lineageParentId', 'productProfileVersionId'].forEach((field) => {
    if (Object.hasOwn(patch, field)) next[field] = String(patch[field] || '');
  });
  next.sourceAssetId = String(next.sourceAssetId || '');
  next.prompt = String(next.prompt || '');
  next.outputRatio = String(next.outputRatio || '1:1');
  next.durationSeconds = Number(next.durationSeconds || 5);
  next.motionIntensity = Number(next.motionIntensity || 3);
  next.lastFrameAssetId = String(next.lastFrameAssetId || '');
  next.provider = String(next.provider || SPATIAL_VIDEO_OFFLINE_PROVIDER);
  const costChanged = COST_DRAFT_FIELDS.some((field) => next[field] !== current[field]);
  if (costChanged) {
    next.callConfirmed = false;
    next.requestId = '';
  }
  return next;
}

export function confirmSpatialVideoDraft(draft, confirmed, idFactory = requestId) {
  const current = updateSpatialVideoDraft(draft || {});
  if (!confirmed) return { ...current, callConfirmed: false, requestId: '' };
  normalizeSpatialVideoParameters({
    prompt: current.prompt,
    output_ratio: current.outputRatio,
    duration_seconds: current.durationSeconds,
    motion_intensity: current.motionIntensity,
    first_frame_asset_id: current.sourceAssetId,
    last_frame_asset_id: current.lastFrameAssetId || null,
    provider: current.provider,
    provider_call_confirmed: false,
    automatic_paid_retry: false,
  }, { sourceAssetId: current.sourceAssetId });
  return {
    ...current,
    callConfirmed: true,
    requestId: current.requestId || String(idFactory()),
  };
}

export function spatialVideoCommandPayload(draft) {
  const current = updateSpatialVideoDraft(draft || {});
  if (!current.callConfirmed || !current.requestId) {
    throw new Error('video parameters must be confirmed before submission');
  }
  if (!SPATIAL_CANVAS_ID.test(current.canvasId)) {
    throw new Error('video task requires a durable spatial canvas');
  }
  const parameters = Object.freeze(normalizeSpatialVideoParameters({
    prompt: current.prompt,
    output_ratio: current.outputRatio,
    duration_seconds: current.durationSeconds,
    motion_intensity: current.motionIntensity,
    first_frame_asset_id: current.sourceAssetId,
    last_frame_asset_id: current.lastFrameAssetId || null,
    provider: current.provider,
    provider_call_confirmed: false,
    automatic_paid_retry: false,
  }, { sourceAssetId: current.sourceAssetId }));
  return Object.freeze({
    client_request_id: current.requestId,
    source_asset_ids: Object.freeze([current.sourceAssetId]),
    spatial_canvas_id: current.canvasId,
    parameters,
    requested_concurrency: 1,
    max_attempts: 1,
  });
}

export function spatialVideoCanvasId(job) {
  const candidate = String(
    job?.spatial_canvas_id
      || job?.parameters?.spatial_canvas_id
      || job?.snapshot?.parameters?.spatial_canvas_id
      || '',
  ).trim();
  return SPATIAL_CANVAS_ID.test(candidate) ? candidate : '';
}

export function isSpatialVideoJob(job) {
  return String(job?.snapshot?.command_id || job?.command_id || '') === SPATIAL_VIDEO_COMMAND_ID;
}

export function spatialVideoJobIsActive(job) {
  return isSpatialVideoJob(job) && ACTIVE_JOB_STATUSES.has(String(job?.status || ''));
}

export function spatialVideoJobIsSettled(job) {
  return isSpatialVideoJob(job) && SETTLED_JOB_STATUSES.has(String(job?.status || ''));
}

export function spatialVideoResultAssetIds(job) {
  if (!isSpatialVideoJob(job)) return [];
  return [...new Set(Array.from(job?.items || []).flatMap((item) => (
    Array.isArray(item?.result_asset_ids) ? item.result_asset_ids.map(String) : []
  )))];
}

export function createSpatialVideoPlaybackState(canvasId = '') {
  return { canvasId: String(canvasId || ''), selectedId: '', playingId: '' };
}

export function selectSpatialVideo(state, elementId = '') {
  const current = state || createSpatialVideoPlaybackState();
  const selectedId = String(elementId || '');
  return {
    canvasId: String(current.canvasId || ''),
    selectedId,
    playingId: current.playingId === selectedId ? selectedId : '',
  };
}

export function playSpatialVideo(state, elementId) {
  const current = state || createSpatialVideoPlaybackState();
  const playingId = String(elementId || '');
  if (!playingId) return stopSpatialVideoPlayback(current);
  return {
    canvasId: String(current.canvasId || ''),
    selectedId: playingId,
    playingId,
  };
}

export function pauseSpatialVideo(state, elementId = state?.selectedId) {
  const current = state || createSpatialVideoPlaybackState();
  const selectedId = String(elementId || '');
  return {
    canvasId: String(current.canvasId || ''),
    selectedId,
    playingId: '',
  };
}

export function stopSpatialVideoPlayback(state) {
  return createSpatialVideoPlaybackState(state?.canvasId);
}

export function switchSpatialVideoCanvas(_state, canvasId) {
  return createSpatialVideoPlaybackState(canvasId);
}

export function restoreSpatialVideoPlaybackState(value = {}) {
  return createSpatialVideoPlaybackState(value?.canvasId);
}

export function spatialVideoNodeRuntime(state, elementId) {
  const id = String(elementId || '');
  const activeId = String(state?.playingId || state?.selectedId || '');
  return {
    loaded: Boolean(id && activeId === id),
    playing: Boolean(id && state?.playingId === id),
  };
}

export function spatialVideoRuntimeSnapshot(elementIds, state) {
  const ids = Array.from(elementIds || [], String);
  const activeId = String(state?.playingId || state?.selectedId || '');
  const playingId = String(state?.playingId || '');
  const loadedIds = activeId && ids.includes(activeId) ? [activeId] : [];
  const playingIds = playingId && ids.includes(playingId) ? [playingId] : [];
  return {
    loadedIds,
    playingIds,
    loadedCount: loadedIds.length,
    playingCount: playingIds.length,
  };
}
