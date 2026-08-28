import test from 'node:test';
import assert from 'node:assert/strict';

import {
  createStudioState,
  draftPayloadFromSnapshot,
  snapshotFromDraft,
} from '../../src/js/studio-state.js';

test('studio state isolates four drafts while sharing only the product collection', () => {
  const modes = ['single', 'multi-file', 'group-split', 'cutout-batch'];
  const state = createStudioState(modes);
  state.modeSelections.single.push('product-a');

  assert.deepEqual(state.modeSelections['multi-file'], []);
  assert.deepEqual(Object.keys(state.assetsByCollection), ['product', 'group', 'cutout']);
  assert.notEqual(state.modeSelections.single, state.modeSelections['multi-file']);
});

test('backend draft hydrates editable controls and durable result pointers', () => {
  const snapshot = snapshotFromDraft({
    brief: { objective: '只保留两个汉堡', user_request: '只保留两个汉堡' },
    parameters: {
      model: 'local-birefnet', fidelity: 75, variations: 3,
      output_ratio: '4:5', output_resolution: '4k',
    },
    intent: { subject_count: true },
    active_job_id: 'job-7',
    current_result_asset_id: 'asset-result-2',
  });

  assert.equal(snapshot.brief, '只保留两个汉堡');
  assert.equal(snapshot.fidelity, 75);
  assert.equal(snapshot.batch, 3);
  assert.equal(snapshot.output_ratio, '4:5');
  assert.equal(snapshot.output_resolution, '4k');
  assert.deepEqual(snapshot.intent_locks, { subject_count: true });
  assert.equal(snapshot.active_job_id, 'job-7');
  assert.equal(snapshot.current_result_asset_id, 'asset-result-2');
});

test('draft save payload preserves revision, ordered selection, intent, and workspace pointers', () => {
  const payload = draftPayloadFromSnapshot({
    revision: 4,
    selectedAssetIds: ['asset-b', 'asset-a'],
    brief: { objective: '商品主图', user_request: '保留包装字' },
    snapshot: {
      model: 'gpt-image-2', angle: 'front', fidelity: 80, batch: 2,
      output_ratio: 'original', output_resolution: '4k',
      platter: 'keep', refine: false, intent_locks: { packaging_text: true },
      active_job_id: 'job-8', current_generation_id: 'gen-3',
      current_result_asset_id: 'result-9', compare_state: { zoom: 1.5 },
    },
  });

  assert.equal(payload.expected_revision, 4);
  assert.deepEqual(payload.selected_asset_ids, ['asset-b', 'asset-a']);
  assert.equal(payload.parameters.variations, 2);
  assert.equal(payload.parameters.output_ratio, 'original');
  assert.equal(payload.parameters.output_resolution, '4k');
  assert.deepEqual(payload.intent, { packaging_text: true });
  assert.equal(payload.active_job_id, 'job-8');
  assert.equal(payload.current_result_asset_id, 'result-9');
  assert.deepEqual(payload.compare_state, { zoom: 1.5 });
});
