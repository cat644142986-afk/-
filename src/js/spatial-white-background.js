export const SPATIAL_WHITE_BACKGROUND_COMMAND_ID = 'command:existing-generate-single';
export const SPATIAL_WHITE_BACKGROUND_ACTION = 'white-background';
export const SPATIAL_WHITE_BACKGROUND_SKILL_ID = 'comfyui-food-product-main-image';

const ACTIVE_JOB_STATUSES = new Set(['queued', 'running', 'paused', 'canceling']);
const SETTLED_JOB_STATUSES = new Set(['completed', 'partial', 'failed', 'canceled', 'interrupted']);
const SPATIAL_CANVAS_ID = /^[a-z][a-z0-9._:-]{2,127}$/;

function cleanText(value, maximum = 1200) {
  return String(value || '').trim().replace(/\s+/g, ' ').slice(0, maximum);
}

function requestId() {
  const suffix = globalThis.crypto?.randomUUID?.()
    || `${Date.now()}-${Math.random().toString(16).slice(2)}`;
  return `spatial-white-background:${String(suffix).toLowerCase()}`;
}

export function createSpatialWhiteBackgroundDraft({
  canvasId = '',
  sourceElementId = '',
  sourceAssetId = '',
  productProfileVersionId = '',
  userRequest = '保留商品结构、数量、包装文字与 Logo，生成干净纯白背景电商主图',
  model = 'gpt-image-2',
  promptVersion = 'prompt_v1',
  materialProfile = 'unknown',
  outputRatio = '1:1',
  outputResolution = '2k',
  designSkillId = '',
} = {}) {
  return {
    canvasId: String(canvasId || ''),
    sourceElementId: String(sourceElementId || ''),
    sourceAssetId: String(sourceAssetId || ''),
    productProfileVersionId: String(productProfileVersionId || ''),
    userRequest: cleanText(userRequest),
    model: String(model || 'gpt-image-2'),
    promptVersion: String(promptVersion || 'prompt_v1'),
    materialProfile: String(materialProfile || 'unknown'),
    outputRatio: String(outputRatio || '1:1'),
    outputResolution: String(outputResolution || '2k'),
    designSkillId: designSkillId === SPATIAL_WHITE_BACKGROUND_SKILL_ID
      ? designSkillId : '',
    preview: null,
    requestId: '',
  };
}

export function updateSpatialWhiteBackgroundDraft(draft, patch = {}) {
  const current = { ...createSpatialWhiteBackgroundDraft(draft), ...draft };
  const next = { ...current };
  [
    'userRequest', 'model', 'promptVersion', 'materialProfile', 'outputRatio',
    'outputResolution', 'designSkillId',
  ].forEach((field) => {
    if (Object.hasOwn(patch, field)) next[field] = patch[field];
  });
  next.userRequest = cleanText(next.userRequest);
  next.designSkillId = next.designSkillId === SPATIAL_WHITE_BACKGROUND_SKILL_ID
    ? next.designSkillId : '';
  const changed = Object.keys(patch).some((field) => next[field] !== current[field]);
  if (changed) {
    next.preview = null;
    next.requestId = '';
  }
  return next;
}

function intentLocks() {
  return {
    subject_shape: true,
    product_count: true,
    packaging_text: true,
    logo: true,
  };
}

function creativeBrief(draft) {
  return {
    objective: '生成干净纯白背景电商主图',
    user_request: cleanText(draft.userRequest),
    mode: 'single',
    category: 'general',
    platform: 'ecommerce',
    output_kind: 'ecommerce-main-image',
    material_profile: draft.materialProfile,
    intent_locks: intentLocks(),
    output_spec: {
      ratio: draft.outputRatio,
      resolution: draft.outputResolution,
      format: 'JPG+transparent PNG',
    },
  };
}

function validateDraft(draft) {
  if (!SPATIAL_CANVAS_ID.test(String(draft.canvasId || ''))) {
    throw new Error('白底图任务需要已保存的无限画布');
  }
  if (!draft.sourceElementId || !draft.sourceAssetId) {
    throw new Error('白底图任务缺少所选原始素材');
  }
  if (cleanText(draft.userRequest).length < 2) {
    throw new Error('请写明本次白底图要求');
  }
}

export function spatialWhiteBackgroundPreviewPayload(draft) {
  const current = { ...createSpatialWhiteBackgroundDraft(draft), ...draft };
  validateDraft(current);
  return {
    ...creativeBrief(current),
    command_id: SPATIAL_WHITE_BACKGROUND_COMMAND_ID,
    source_asset_ids: [current.sourceAssetId],
    model: current.model,
    prompt_version: current.promptVersion,
    generation_strategy: 'single_pass',
    material_profile: current.materialProfile,
    design_skill_id: current.designSkillId,
    spatial_action: SPATIAL_WHITE_BACKGROUND_ACTION,
    spatial_canvas_id: current.canvasId,
    spatial_source_element_id: current.sourceElementId,
  };
}

export function applySpatialWhiteBackgroundPreview(draft, bundle) {
  const current = { ...createSpatialWhiteBackgroundDraft(draft), ...draft };
  const context = bundle?.execution_context;
  const spatial = bundle?.spatial_context;
  if (
    !context?.context_sha256
    || context?.binding !== 'preview'
    || spatial?.action !== SPATIAL_WHITE_BACKGROUND_ACTION
    || spatial?.spatial_canvas_id !== current.canvasId
    || spatial?.source_element_id !== current.sourceElementId
    || spatial?.source_asset_id !== current.sourceAssetId
  ) throw new Error('白底图执行上下文返回不完整，请重新预览');
  return {
    ...current,
    preview: {
      executionContext: context,
      spatialContext: spatial,
      skillSnapshot: bundle?.skill_snapshot || null,
      summary: context.summary || {},
    },
    requestId: '',
  };
}

export function spatialWhiteBackgroundCommandPayload(draft, idFactory = requestId) {
  const current = { ...createSpatialWhiteBackgroundDraft(draft), ...draft };
  validateDraft(current);
  if (!current.preview?.executionContext?.context_sha256) {
    throw new Error('提交前需要先核对本次执行上下文');
  }
  const stableRequestId = current.requestId || String(idFactory());
  const brief = creativeBrief(current);
  return {
    draft: { ...current, requestId: stableRequestId },
    payload: {
      client_request_id: stableRequestId,
      source_asset_ids: [current.sourceAssetId],
      spatial_canvas_id: current.canvasId,
      spatial_source_element_id: current.sourceElementId,
      requested_concurrency: 1,
      max_attempts: 1,
      parameters: {
        batch: 1,
        variations: 1,
        model: current.model,
        brief,
        intent_locks: brief.intent_locks,
        material_profile: current.materialProfile,
        output_ratio: current.outputRatio,
        output_resolution: current.outputResolution,
        prompt_version: current.promptVersion,
        prompt_version_source: 'user',
        generation_strategy: 'single_pass',
        generation_strategy_source: 'user',
        design_skill_id: current.designSkillId,
        spatial_action: SPATIAL_WHITE_BACKGROUND_ACTION,
        provider_call_confirmed: true,
        automatic_paid_retry: false,
        execution_context: current.preview.executionContext,
      },
    },
  };
}

function jobParameters(job) {
  return job?.parameters || job?.snapshot?.parameters || {};
}

export function isSpatialWhiteBackgroundJob(job) {
  return (
    String(job?.snapshot?.command_id || job?.command_id || '')
      === SPATIAL_WHITE_BACKGROUND_COMMAND_ID
    && String(jobParameters(job).spatial_action || '') === SPATIAL_WHITE_BACKGROUND_ACTION
  );
}

export function spatialWhiteBackgroundCanvasId(job) {
  const candidate = String(jobParameters(job).spatial_canvas_id || '').trim();
  return SPATIAL_CANVAS_ID.test(candidate) ? candidate : '';
}

export function spatialWhiteBackgroundSourceElementId(job) {
  return String(jobParameters(job).spatial_source_element_id || '');
}

export function spatialWhiteBackgroundJobIsActive(job) {
  return isSpatialWhiteBackgroundJob(job) && ACTIVE_JOB_STATUSES.has(String(job?.status || ''));
}

export function spatialWhiteBackgroundJobIsSettled(job) {
  return isSpatialWhiteBackgroundJob(job) && SETTLED_JOB_STATUSES.has(String(job?.status || ''));
}

export function spatialWhiteBackgroundResultAssetIds(job) {
  if (!isSpatialWhiteBackgroundJob(job)) return [];
  return [...new Set(Array.from(job?.items || []).flatMap((item) => (
    Array.isArray(item?.result_asset_ids) ? item.result_asset_ids.map(String) : []
  )))];
}
