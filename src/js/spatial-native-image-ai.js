export const SPATIAL_IMAGE_AI_COMMAND_ID = 'command:existing-generate-single';
export const SPATIAL_WHITE_BACKGROUND_ACTION = 'white-background';
export const SPATIAL_RESULT_VARIATION_ACTION = 'generate-image';
export const SPATIAL_IMAGE_AI_SKILL_ID = 'comfyui-food-product-main-image';
export const SPATIAL_CANVAS_CONVERSATION_SURFACE = 'canvas-conversation';
export const SPATIAL_CANVAS_CONVERSATION_CONTRACT = 'canvas-conversation-input-v1';
export const SPATIAL_CANVAS_REFERENCE_SURFACE = 'canvas-reference';
export const SPATIAL_CANVAS_REFERENCE_CONTRACT = 'canvas-reference-input-v1';

const ACTIVE_JOB_STATUSES = new Set(['queued', 'running', 'paused', 'canceling']);
const SETTLED_JOB_STATUSES = new Set(['completed', 'partial', 'failed', 'canceled', 'interrupted']);
const SPATIAL_CANVAS_ID = /^[a-z][a-z0-9._:-]{2,127}$/;

const ACTIONS = Object.freeze({
  [SPATIAL_WHITE_BACKGROUND_ACTION]: Object.freeze({
    action: SPATIAL_WHITE_BACKGROUND_ACTION,
    title: '白底图',
    sourceKind: 'source',
    sourceLabel: '所选原始素材',
    defaultUserRequest: '保留商品结构、数量、包装文字与 Logo，生成干净纯白背景电商主图',
    objective: '生成干净纯白背景电商主图',
    requestPrefix: 'spatial-white-background',
  }),
  [SPATIAL_RESULT_VARIATION_ACTION]: Object.freeze({
    action: SPATIAL_RESULT_VARIATION_ACTION,
    title: '生图变体',
    sourceKind: 'result',
    sourceLabel: '所选结果',
    defaultUserRequest: '保留商品结构、数量、包装文字与 Logo，基于当前结果生成同系列高质量电商主图变体',
    objective: '基于当前结果生成同系列高质量电商主图变体',
    requestPrefix: 'spatial-result-variation',
  }),
});

function cleanText(value, maximum = 1200) {
  return String(value || '').trim().replace(/\s+/g, ' ').slice(0, maximum);
}

function actionDefinition(action) {
  const definition = ACTIONS[String(action || '')];
  if (!definition) throw new Error('当前 Canvas Native AI 动作不受支持');
  return definition;
}

function draftDefinition(draft) {
  const definition = actionDefinition(draft?.action);
  if (draft?.inputSurface === SPATIAL_CANVAS_REFERENCE_SURFACE) return {
    ...definition,
    title: '参考生成',
    sourceLabel: draft?.sourceResultId ? '所选结果' : '所选素材',
    defaultUserRequest: '参考所选图片创作可并列比较的新方案，保留商品结构、包装文字与 Logo',
    objective: '以所选图片为参考创作新的商业视觉方案，不覆写原图',
    requestPrefix: 'spatial-canvas-reference',
  };
  if (draft?.inputSurface !== SPATIAL_CANVAS_CONVERSATION_SURFACE) return definition;
  return {
    ...definition,
    title: '对话修改',
    sourceLabel: draft?.sourceResultId ? '所选结果' : '所选素材',
    defaultUserRequest: '',
    objective: '根据当前要求修改所选图片',
    requestPrefix: 'spatial-canvas-conversation',
  };
}

function inputUiContext(draft) {
  if (draft?.inputSurface === SPATIAL_CANVAS_REFERENCE_SURFACE) return {
    input_surface: SPATIAL_CANVAS_REFERENCE_SURFACE,
    contract_version: SPATIAL_CANVAS_REFERENCE_CONTRACT,
  };
  if (draft?.inputSurface !== SPATIAL_CANVAS_CONVERSATION_SURFACE) return null;
  return {
    input_surface: SPATIAL_CANVAS_CONVERSATION_SURFACE,
    contract_version: SPATIAL_CANVAS_CONVERSATION_CONTRACT,
  };
}

function requestId(definition) {
  const suffix = globalThis.crypto?.randomUUID?.()
    || `${Date.now()}-${Math.random().toString(16).slice(2)}`;
  return `${definition.requestPrefix}:${String(suffix).toLowerCase()}`;
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
  const definition = draftDefinition(draft);
  return {
    objective: definition.objective,
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
  const definition = draftDefinition(draft);
  const inputSurface = String(draft.inputSurface || '');
  if (inputSurface && ![
    SPATIAL_CANVAS_CONVERSATION_SURFACE, SPATIAL_CANVAS_REFERENCE_SURFACE,
  ].includes(inputSurface)) {
    throw new Error('当前 Canvas AI 输入来源不受支持');
  }
  if (
    [SPATIAL_CANVAS_CONVERSATION_SURFACE, SPATIAL_CANVAS_REFERENCE_SURFACE].includes(inputSurface)
    && draft.action !== SPATIAL_RESULT_VARIATION_ACTION
  ) {
    throw new Error('Canvas 创作必须复用现有单图生成链');
  }
  if (!SPATIAL_CANVAS_ID.test(String(draft.canvasId || ''))) {
    throw new Error(`${definition.title}任务需要已保存的无限画布`);
  }
  if (!draft.sourceElementId || !draft.sourceAssetId) {
    throw new Error(`${definition.title}任务缺少${definition.sourceLabel}`);
  }
  if (inputSurface === SPATIAL_CANVAS_CONVERSATION_SURFACE
    || inputSurface === SPATIAL_CANVAS_REFERENCE_SURFACE) {
    if (
      draft.sourceResultId
      && String(draft.sourceResultId) !== String(draft.sourceAssetId || '')
    ) throw new Error('Canvas 创作必须精确使用当前选中的 Result');
  } else if (
    definition.sourceKind === 'result'
    && String(draft.sourceResultId || '') !== String(draft.sourceAssetId || '')
  ) {
    throw new Error('生图变体必须精确使用当前选中的 Result');
  }
  if (cleanText(draft.userRequest).length < 2) {
    throw new Error(`请写明本次${definition.title}要求`);
  }
}

export function spatialImageAiDefinition(action, draft = null) {
  return { ...(draft ? draftDefinition({ ...draft, action }) : actionDefinition(action)) };
}

export function createSpatialImageAiDraft(action, {
  canvasId = '',
  sourceElementId = '',
  sourceAssetId = '',
  sourceResultId = '',
  productProfileVersionId = '',
  userRequest = '',
  model = 'gpt-image-2',
  promptVersion = 'prompt_v1',
  materialProfile = 'unknown',
  outputRatio = '1:1',
  outputResolution = '2k',
  designSkillId = '',
  inputSurface = '',
} = {}) {
  const definition = draftDefinition({ action, inputSurface, sourceResultId });
  return {
    action: definition.action,
    canvasId: String(canvasId || ''),
    sourceElementId: String(sourceElementId || ''),
    sourceAssetId: String(sourceAssetId || ''),
    sourceResultId: String(sourceResultId || ''),
    productProfileVersionId: String(productProfileVersionId || ''),
    userRequest: cleanText(
      inputSurface === SPATIAL_CANVAS_CONVERSATION_SURFACE
        ? userRequest
        : userRequest || definition.defaultUserRequest,
    ),
    model: String(model || 'gpt-image-2'),
    promptVersion: String(promptVersion || 'prompt_v1'),
    materialProfile: String(materialProfile || 'unknown'),
    outputRatio: String(outputRatio || '1:1'),
    outputResolution: String(outputResolution || '2k'),
    designSkillId: designSkillId === SPATIAL_IMAGE_AI_SKILL_ID ? designSkillId : '',
    inputSurface: String(inputSurface || ''),
    preview: null,
    requestId: '',
  };
}

export function updateSpatialImageAiDraft(draft, patch = {}) {
  const current = { ...createSpatialImageAiDraft(draft?.action, draft), ...draft };
  const next = { ...current };
  [
    'userRequest', 'model', 'promptVersion', 'materialProfile', 'outputRatio',
    'outputResolution', 'designSkillId',
    'inputSurface',
  ].forEach((field) => {
    if (Object.hasOwn(patch, field)) next[field] = patch[field];
  });
  next.userRequest = cleanText(next.userRequest);
  next.designSkillId = next.designSkillId === SPATIAL_IMAGE_AI_SKILL_ID
    ? next.designSkillId : '';
  const changed = Object.keys(patch).some((field) => next[field] !== current[field]);
  if (changed) {
    next.preview = null;
    next.requestId = '';
  }
  return next;
}

export function spatialImageAiPreviewPayload(draft) {
  const current = { ...createSpatialImageAiDraft(draft?.action, draft), ...draft };
  validateDraft(current);
  const uiContext = inputUiContext(current);
  return {
    ...creativeBrief(current),
    command_id: SPATIAL_IMAGE_AI_COMMAND_ID,
    source_asset_ids: [current.sourceAssetId],
    model: current.model,
    prompt_version: current.promptVersion,
    generation_strategy: 'single_pass',
    material_profile: current.materialProfile,
    design_skill_id: current.designSkillId,
    spatial_action: current.action,
    spatial_canvas_id: current.canvasId,
    spatial_source_element_id: current.sourceElementId,
    ...(uiContext ? { ui_context: uiContext } : {}),
  };
}

export function applySpatialImageAiPreview(draft, bundle) {
  const current = { ...createSpatialImageAiDraft(draft?.action, draft), ...draft };
  const context = bundle?.execution_context;
  const spatial = bundle?.spatial_context;
  if (
    !context?.context_sha256
    || context?.binding !== 'preview'
    || spatial?.action !== current.action
    || spatial?.spatial_canvas_id !== current.canvasId
    || spatial?.source_element_id !== current.sourceElementId
    || spatial?.source_asset_id !== current.sourceAssetId
    || (
      current.inputSurface
      && spatial?.input_surface !== current.inputSurface
    )
  ) throw new Error(`${draftDefinition(current).title}执行上下文返回不完整，请重新预览`);
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

export function spatialImageAiCommandPayload(draft, idFactory = null) {
  const current = { ...createSpatialImageAiDraft(draft?.action, draft), ...draft };
  const definition = draftDefinition(current);
  validateDraft(current);
  if (!current.preview?.executionContext?.context_sha256) {
    throw new Error('提交前需要先核对本次执行上下文');
  }
  const stableRequestId = current.requestId || String(
    typeof idFactory === 'function' ? idFactory() : requestId(definition),
  );
  const brief = creativeBrief(current);
  const uiContext = inputUiContext(current);
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
        spatial_action: current.action,
        provider_call_confirmed: true,
        automatic_paid_retry: false,
        execution_context: current.preview.executionContext,
        ...(uiContext ? { ui_context: uiContext } : {}),
      },
    },
  };
}

function jobParameters(job) {
  return job?.parameters || job?.snapshot?.parameters || {};
}

export function spatialImageAiAction(job) {
  const action = String(jobParameters(job).spatial_action || '');
  return ACTIONS[action] ? action : '';
}

export function isSpatialImageAiJob(job) {
  return (
    String(job?.snapshot?.command_id || job?.command_id || '') === SPATIAL_IMAGE_AI_COMMAND_ID
    && Boolean(spatialImageAiAction(job))
  );
}

export function spatialImageAiCanvasId(job) {
  const candidate = String(jobParameters(job).spatial_canvas_id || '').trim();
  return SPATIAL_CANVAS_ID.test(candidate) ? candidate : '';
}

export function spatialImageAiSourceElementId(job) {
  return String(jobParameters(job).spatial_source_element_id || '');
}

export function spatialImageAiJobIsActive(job) {
  return isSpatialImageAiJob(job) && ACTIVE_JOB_STATUSES.has(String(job?.status || ''));
}

export function spatialImageAiJobIsSettled(job) {
  return isSpatialImageAiJob(job) && SETTLED_JOB_STATUSES.has(String(job?.status || ''));
}

export function spatialImageAiResultAssetIds(job) {
  if (!isSpatialImageAiJob(job)) return [];
  return [...new Set(Array.from(job?.items || []).flatMap((item) => (
    Array.isArray(item?.result_asset_ids) ? item.result_asset_ids.map(String) : []
  )))];
}
