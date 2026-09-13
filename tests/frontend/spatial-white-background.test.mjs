import test from 'node:test';
import assert from 'node:assert/strict';

import {
  SPATIAL_WHITE_BACKGROUND_COMMAND_ID,
  SPATIAL_WHITE_BACKGROUND_SKILL_ID,
  applySpatialWhiteBackgroundPreview,
  createSpatialWhiteBackgroundDraft,
  isSpatialWhiteBackgroundJob,
  spatialWhiteBackgroundCanvasId,
  spatialWhiteBackgroundCommandPayload,
  spatialWhiteBackgroundJobIsActive,
  spatialWhiteBackgroundJobIsSettled,
  spatialWhiteBackgroundPreviewPayload,
  spatialWhiteBackgroundResultAssetIds,
  updateSpatialWhiteBackgroundDraft,
} from '../../src/js/spatial-white-background.js';

function previewBundle(draft) {
  return {
    execution_context: {
      binding: 'preview',
      context_sha256: 'a'.repeat(64),
      summary: { source_count: 2, positive_rule_count: 4 },
    },
    spatial_context: {
      action: 'white-background',
      spatial_canvas_id: draft.canvasId,
      source_element_id: draft.sourceElementId,
      source_asset_id: draft.sourceAssetId,
      fingerprint: 'b'.repeat(64),
    },
    skill_snapshot: {
      id: SPATIAL_WHITE_BACKGROUND_SKILL_ID,
      version: 'content-51f473282be9',
      content_sha256: '51f473282be9'.padEnd(64, '0'),
      adapter_version: 'context-skill-adapter-v1',
    },
  };
}

test('canvas white-background preview freezes hard intent, semantic source and design method', () => {
  const draft = createSpatialWhiteBackgroundDraft({
    canvasId: 'spatial:g4b-canvas',
    sourceElementId: 'source-product',
    sourceAssetId: 'ast:g4b-source',
    productProfileVersionId: 'profilever:g4b-v1',
    designSkillId: SPATIAL_WHITE_BACKGROUND_SKILL_ID,
  });
  const payload = spatialWhiteBackgroundPreviewPayload(draft);

  assert.equal(payload.command_id, SPATIAL_WHITE_BACKGROUND_COMMAND_ID);
  assert.deepEqual(payload.source_asset_ids, ['ast:g4b-source']);
  assert.equal(payload.spatial_canvas_id, 'spatial:g4b-canvas');
  assert.equal(payload.spatial_source_element_id, 'source-product');
  assert.equal(payload.design_skill_id, SPATIAL_WHITE_BACKGROUND_SKILL_ID);
  assert.equal(payload.generation_strategy, 'single_pass');
  assert.deepEqual(payload.intent_locks, {
    subject_shape: true,
    product_count: true,
    packaging_text: true,
    logo: true,
  });
});

test('editing after preview invalidates confirmation while submission stays idempotent', () => {
  const draft = createSpatialWhiteBackgroundDraft({
    canvasId: 'spatial:g4b-canvas',
    sourceElementId: 'source-product',
    sourceAssetId: 'ast:g4b-source',
  });
  const previewed = applySpatialWhiteBackgroundPreview(draft, previewBundle(draft));
  const first = spatialWhiteBackgroundCommandPayload(previewed, () => 'spatial-white-background:fixed');
  const replay = spatialWhiteBackgroundCommandPayload(first.draft, () => 'must-not-replace');

  assert.equal(first.payload.client_request_id, 'spatial-white-background:fixed');
  assert.equal(replay.payload.client_request_id, first.payload.client_request_id);
  assert.equal(first.payload.max_attempts, 1);
  assert.equal(first.payload.parameters.provider_call_confirmed, true);
  assert.equal(first.payload.parameters.execution_context.context_sha256, 'a'.repeat(64));

  const changed = updateSpatialWhiteBackgroundDraft(first.draft, {
    userRequest: '保持 Logo，并让阴影更轻',
  });
  assert.equal(changed.preview, null);
  assert.equal(changed.requestId, '');
  assert.throws(() => spatialWhiteBackgroundCommandPayload(changed), /先核对/);
});

test('white-background job ownership and results are recovered from existing task ledger', () => {
  const job = {
    id: 'job:g4b',
    status: 'running',
    snapshot: {
      command_id: SPATIAL_WHITE_BACKGROUND_COMMAND_ID,
      parameters: {
        spatial_action: 'white-background',
        spatial_canvas_id: 'spatial:g4b-canvas',
      },
    },
    items: [
      { result_asset_ids: ['ast:result-a'] },
      { result_asset_ids: ['ast:result-a', 'ast:result-b'] },
    ],
  };

  assert.equal(isSpatialWhiteBackgroundJob(job), true);
  assert.equal(spatialWhiteBackgroundCanvasId(job), 'spatial:g4b-canvas');
  assert.equal(spatialWhiteBackgroundJobIsActive(job), true);
  assert.equal(spatialWhiteBackgroundJobIsSettled(job), false);
  assert.deepEqual(spatialWhiteBackgroundResultAssetIds(job), ['ast:result-a', 'ast:result-b']);
  job.status = 'completed';
  assert.equal(spatialWhiteBackgroundJobIsSettled(job), true);
});
