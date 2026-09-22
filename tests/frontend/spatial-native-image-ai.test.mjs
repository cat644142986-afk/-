import test from 'node:test';
import assert from 'node:assert/strict';

import {
  SPATIAL_CANVAS_CONVERSATION_CONTRACT,
  SPATIAL_CANVAS_CONVERSATION_SURFACE,
  SPATIAL_CANVAS_REFERENCE_CONTRACT,
  SPATIAL_CANVAS_REFERENCE_SURFACE,
  SPATIAL_IMAGE_AI_COMMAND_ID,
  SPATIAL_IMAGE_AI_SKILL_ID,
  SPATIAL_RESULT_VARIATION_ACTION,
  SPATIAL_WHITE_BACKGROUND_ACTION,
  applySpatialImageAiPreview,
  createSpatialImageAiDraft,
  isSpatialImageAiJob,
  spatialImageAiAction,
  spatialImageAiCanvasId,
  spatialImageAiCommandPayload,
  spatialImageAiPreviewPayload,
  updateSpatialImageAiDraft,
} from '../../src/js/spatial-native-image-ai.js';

function previewBundle(draft) {
  return {
    execution_context: {
      contract_version: 'execution-context-v1',
      binding: 'preview',
      context_sha256: 'c'.repeat(64),
      summary: { source_count: 2, positive_rule_count: 5, negative_rule_count: 1 },
    },
    spatial_context: {
      action: draft.action,
      spatial_canvas_id: draft.canvasId,
      source_element_id: draft.sourceElementId,
      source_asset_id: draft.sourceAssetId,
      lineage_parent_id: draft.sourceAssetId,
      fingerprint: 'd'.repeat(64),
      ...(draft.inputSurface ? { input_surface: draft.inputSurface } : {}),
    },
    skill_snapshot: {
      skill_id: SPATIAL_IMAGE_AI_SKILL_ID,
      version: 'content-a1b2c3d4e5f6',
      content_sha256: 'a'.repeat(64),
      adapter_version: 'context-skill-adapter-v1',
      mode: 'context-only',
    },
  };
}

test('white-background and result variation share one Canvas Native AI draft contract', () => {
  const white = createSpatialImageAiDraft(SPATIAL_WHITE_BACKGROUND_ACTION, {
    canvasId: 'spatial:shared-canvas',
    sourceElementId: 'source-element',
    sourceAssetId: 'ast:source',
    designSkillId: SPATIAL_IMAGE_AI_SKILL_ID,
  });
  const variation = createSpatialImageAiDraft(SPATIAL_RESULT_VARIATION_ACTION, {
    canvasId: 'spatial:shared-canvas',
    sourceElementId: 'result-element',
    sourceAssetId: 'ast:result',
    sourceResultId: 'ast:result',
    designSkillId: SPATIAL_IMAGE_AI_SKILL_ID,
  });

  assert.equal(spatialImageAiPreviewPayload(white).command_id, SPATIAL_IMAGE_AI_COMMAND_ID);
  const payload = spatialImageAiPreviewPayload(variation);
  assert.equal(payload.command_id, SPATIAL_IMAGE_AI_COMMAND_ID);
  assert.equal(payload.spatial_action, SPATIAL_RESULT_VARIATION_ACTION);
  assert.deepEqual(payload.source_asset_ids, ['ast:result']);
  assert.equal(payload.design_skill_id, SPATIAL_IMAGE_AI_SKILL_ID);
  assert.deepEqual(payload.intent_locks, {
    subject_shape: true,
    product_count: true,
    packaging_text: true,
    logo: true,
  });
  assert.match(payload.user_request, /当前结果/);
});

test('result variation freezes the exact selected Result and remains idempotent', () => {
  const draft = createSpatialImageAiDraft(SPATIAL_RESULT_VARIATION_ACTION, {
    canvasId: 'spatial:result-canvas',
    sourceElementId: 'result-element',
    sourceAssetId: 'ast:selected-result',
    sourceResultId: 'ast:selected-result',
    productProfileVersionId: 'profilever:exact',
    designSkillId: SPATIAL_IMAGE_AI_SKILL_ID,
  });
  const previewed = applySpatialImageAiPreview(draft, previewBundle(draft));
  const first = spatialImageAiCommandPayload(previewed, () => 'spatial-result-variation:fixed');
  const replay = spatialImageAiCommandPayload(first.draft, () => 'must-not-replace');

  assert.equal(first.payload.client_request_id, 'spatial-result-variation:fixed');
  assert.equal(replay.payload.client_request_id, first.payload.client_request_id);
  assert.deepEqual(first.payload.source_asset_ids, ['ast:selected-result']);
  assert.equal(first.payload.parameters.spatial_action, SPATIAL_RESULT_VARIATION_ACTION);
  assert.equal(first.payload.parameters.max_attempts, undefined);
  assert.equal(first.payload.max_attempts, 1);
  assert.equal(first.payload.parameters.automatic_paid_retry, false);
  assert.equal(
    first.payload.parameters.execution_context.context_sha256,
    previewed.preview.executionContext.context_sha256,
  );

  const changed = updateSpatialImageAiDraft(previewed, { userRequest: '生成另一种构图' });
  assert.equal(changed.preview, null);
  assert.throws(() => spatialImageAiCommandPayload(changed), /先核对/);
});

test('result variation rejects a silently substituted original source', () => {
  const draft = createSpatialImageAiDraft(SPATIAL_RESULT_VARIATION_ACTION, {
    canvasId: 'spatial:result-canvas',
    sourceElementId: 'result-element',
    sourceAssetId: 'ast:original-source',
    sourceResultId: 'ast:selected-result',
  });
  assert.throws(() => spatialImageAiPreviewPayload(draft), /精确使用当前选中的 Result/);
});

test('Canvas Conversation reuses the governed image chain for one exact source or Result', () => {
  const source = createSpatialImageAiDraft(SPATIAL_RESULT_VARIATION_ACTION, {
    canvasId: 'spatial:conversation-canvas',
    sourceElementId: 'source-element',
    sourceAssetId: 'ast:source',
    userRequest: '把背景改成暖灰色摄影棚，保留包装文字和 Logo',
    designSkillId: SPATIAL_IMAGE_AI_SKILL_ID,
    inputSurface: SPATIAL_CANVAS_CONVERSATION_SURFACE,
  });
  const previewPayload = spatialImageAiPreviewPayload(source);

  assert.deepEqual(previewPayload.source_asset_ids, ['ast:source']);
  assert.equal(previewPayload.spatial_action, SPATIAL_RESULT_VARIATION_ACTION);
  assert.equal(previewPayload.objective, '根据当前要求修改所选图片');
  assert.equal(previewPayload.user_request, '把背景改成暖灰色摄影棚，保留包装文字和 Logo');
  assert.deepEqual(previewPayload.ui_context, {
    input_surface: SPATIAL_CANVAS_CONVERSATION_SURFACE,
    contract_version: SPATIAL_CANVAS_CONVERSATION_CONTRACT,
  });

  const previewed = applySpatialImageAiPreview(source, previewBundle(source));
  const submission = spatialImageAiCommandPayload(
    previewed,
    () => 'spatial-canvas-conversation:fixed',
  );
  assert.equal(submission.payload.client_request_id, 'spatial-canvas-conversation:fixed');
  assert.deepEqual(submission.payload.source_asset_ids, ['ast:source']);
  assert.equal(submission.payload.max_attempts, 1);
  assert.equal(submission.payload.parameters.automatic_paid_retry, false);
  assert.deepEqual(submission.payload.parameters.ui_context, previewPayload.ui_context);

  const result = createSpatialImageAiDraft(SPATIAL_RESULT_VARIATION_ACTION, {
    canvasId: 'spatial:conversation-canvas',
    sourceElementId: 'result-element',
    sourceAssetId: 'ast:current-result',
    sourceResultId: 'ast:current-result',
    userRequest: '把阴影再柔和一点',
    inputSurface: SPATIAL_CANVAS_CONVERSATION_SURFACE,
  });
  assert.deepEqual(spatialImageAiPreviewPayload(result).source_asset_ids, ['ast:current-result']);

  const changed = updateSpatialImageAiDraft(previewed, { userRequest: '改成冷灰背景' });
  assert.equal(changed.preview, null);
  assert.throws(() => spatialImageAiCommandPayload(changed), /先核对/);
  assert.throws(() => spatialImageAiPreviewPayload(createSpatialImageAiDraft(
    SPATIAL_RESULT_VARIATION_ACTION,
    {
      canvasId: 'spatial:conversation-canvas',
      sourceElementId: 'result-element',
      sourceAssetId: 'ast:original',
      sourceResultId: 'ast:current-result',
      userRequest: '继续调整',
      inputSurface: SPATIAL_CANVAS_CONVERSATION_SURFACE,
    },
  )), /精确使用当前选中的 Result/);
});

test('all governed Canvas image actions share task ownership and result recovery helpers', () => {
  const job = {
    id: 'job:result-variation',
    status: 'completed',
    snapshot: {
      command_id: SPATIAL_IMAGE_AI_COMMAND_ID,
      source_asset_ids: ['ast:selected-result'],
    },
    parameters: {
      spatial_action: SPATIAL_RESULT_VARIATION_ACTION,
      spatial_canvas_id: 'spatial:result-canvas',
      spatial_source_element_id: 'result-element',
    },
    items: [{ result_asset_ids: ['ast:new-result'] }],
  };

  assert.equal(isSpatialImageAiJob(job), true);
  assert.equal(spatialImageAiAction(job), SPATIAL_RESULT_VARIATION_ACTION);
  assert.equal(spatialImageAiCanvasId(job), 'spatial:result-canvas');
});

test('one reference image enters the existing governed single-image Task without weakening exact Result binding', () => {
  for (const sourceResultId of ['', 'ast:selected-result']) {
    const sourceAssetId = sourceResultId || 'ast:source';
    const draft = createSpatialImageAiDraft(SPATIAL_RESULT_VARIATION_ACTION, {
      canvasId: 'spatial:reference-canvas',
      sourceElementId: sourceResultId ? 'result-element' : 'source-element',
      sourceAssetId,
      sourceResultId,
      inputSurface: SPATIAL_CANVAS_REFERENCE_SURFACE,
      promptVersion: 'prompt_v1',
      designSkillId: '',
    });
    const preview = spatialImageAiPreviewPayload(draft);
    assert.equal(preview.command_id, SPATIAL_IMAGE_AI_COMMAND_ID);
    assert.deepEqual(preview.source_asset_ids, [sourceAssetId]);
    assert.equal(preview.objective, '以所选图片为参考创作新的商业视觉方案，不覆写原图');
    assert.deepEqual(preview.ui_context, {
      input_surface: SPATIAL_CANVAS_REFERENCE_SURFACE,
      contract_version: SPATIAL_CANVAS_REFERENCE_CONTRACT,
    });
    assert.equal(preview.design_skill_id, '');
    const frozen = applySpatialImageAiPreview(draft, previewBundle(draft));
    const task = spatialImageAiCommandPayload(frozen, () => 'reference:fixed');
    assert.deepEqual(task.payload.source_asset_ids, [sourceAssetId]);
    assert.equal(task.payload.max_attempts, 1);
    assert.equal(task.payload.parameters.automatic_paid_retry, false);
    assert.equal(task.payload.parameters.execution_context.context_sha256, 'c'.repeat(64));
    assert.deepEqual(task.payload.parameters.ui_context, preview.ui_context);
    const changed = updateSpatialImageAiDraft(frozen, { outputRatio: '16:9' });
    assert.equal(changed.preview, null);
    assert.throws(() => spatialImageAiCommandPayload(changed), /先核对/);
  }
  assert.throws(() => spatialImageAiPreviewPayload(createSpatialImageAiDraft(
    SPATIAL_RESULT_VARIATION_ACTION,
    {
      canvasId: 'spatial:reference-canvas',
      sourceElementId: 'result-element',
      sourceAssetId: 'ast:original',
      sourceResultId: 'ast:selected-result',
      inputSurface: SPATIAL_CANVAS_REFERENCE_SURFACE,
    },
  )), /精确使用当前选中的 Result/);
});
