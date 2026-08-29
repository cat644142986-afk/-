import test from 'node:test';
import assert from 'node:assert/strict';

import {
  createSemanticCutoutState,
  semanticCutoutPayload,
  semanticCutoutReadiness,
  semanticGroundingPresentation,
  updateSemanticCutoutState,
} from '../../src/js/semantic-cutout.js';

test('quick cutout remains an all-foreground operation', () => {
  const state = createSemanticCutoutState();
  assert.equal(state.strategy, 'foreground');
  assert.deepEqual(semanticCutoutPayload(state, ['asset-1', 'asset-2']), {
    strategy: 'foreground',
  });
  assert.deepEqual(semanticCutoutReadiness(state, ['asset-1', 'asset-2']), {
    action: 'submit',
    ready: true,
    message: '2 张素材 · 本地分离全部前景',
  });
});

test('semantic cutout requires one source, a name, the requested count, and backend confirmation', () => {
  let state = createSemanticCutoutState({ strategy: 'semantic' });
  assert.equal(semanticCutoutReadiness(state, []).action, 'select-source');

  state = updateSemanticCutoutState(state, { query: '汉堡', target_count: 2 });
  assert.equal(semanticCutoutReadiness(state, ['asset-1', 'asset-2']).action, 'single-source');
  assert.equal(semanticCutoutReadiness(state, ['asset-1']).action, 'confirm');

  state = updateSemanticCutoutState(state, {
    source_asset_id: 'asset-1',
    status: 'confirmed',
    digest: 'sha256:confirmed',
    regions: [
      { id: 'target-1', label: '汉堡', bbox: [0.05, 0.1, 0.35, 0.6] },
      { id: 'target-2', label: '汉堡', bbox: [0.55, 0.12, 0.35, 0.58] },
    ],
  });
  assert.equal(semanticCutoutReadiness(state, ['asset-1']).action, 'submit');
  assert.equal(semanticCutoutReadiness(state, ['asset-1']).ready, true);
});

test('changing the semantic query or requested count invalidates an old confirmation', () => {
  const confirmed = createSemanticCutoutState({
    strategy: 'semantic',
    query: '汉堡',
    target_count: 2,
    source_asset_id: 'asset-1',
    status: 'confirmed',
    digest: 'sha256:confirmed',
    regions: [
      { id: 'target-1', label: '汉堡', bbox: [0.05, 0.1, 0.35, 0.6] },
      { id: 'target-2', label: '汉堡', bbox: [0.55, 0.12, 0.35, 0.58] },
    ],
  });
  const changed = updateSemanticCutoutState(confirmed, { query: '薯条' });
  assert.equal(changed.status, 'draft');
  assert.equal(changed.digest, '');
  assert.deepEqual(changed.regions, []);
});

test('temporarily using quick cutout does not discard a valid semantic confirmation', () => {
  const confirmed = createSemanticCutoutState({
    strategy: 'semantic',
    query: '汉堡',
    target_count: 1,
    source_asset_id: 'asset-1',
    status: 'confirmed',
    digest: 'sha256:confirmed',
    regions: [{ id: 'target-1', label: '汉堡', bbox: [0.1, 0.1, 0.8, 0.8] }],
  });
  const quick = updateSemanticCutoutState(confirmed, { strategy: 'foreground' });
  const restored = updateSemanticCutoutState(quick, { strategy: 'semantic' });
  assert.equal(restored.status, 'confirmed');
  assert.equal(restored.digest, 'sha256:confirmed');
  assert.equal(semanticCutoutReadiness(restored, ['asset-1']).ready, true);
});

test('confirmed semantic selection compiles to a per-source immutable job plan', () => {
  const state = createSemanticCutoutState({
    strategy: 'semantic',
    query: '汉堡',
    target_count: 2,
    source_asset_id: 'asset-1',
    status: 'confirmed',
    digest: 'sha256:confirmed',
    method: 'manual-box',
    regions: [
      { id: 'target-1', label: '汉堡', bbox: [0.05, 0.1, 0.35, 0.6] },
      { id: 'target-2', label: '汉堡', bbox: [0.55, 0.12, 0.35, 0.58] },
    ],
  });
  assert.deepEqual(semanticCutoutPayload(state, ['asset-1']), {
    strategy: 'semantic',
    query: '汉堡',
    model_query: '',
    target_count: 2,
    sources: {
      'asset-1': {
        source_asset_id: 'asset-1',
        status: 'confirmed',
        method: 'manual-box',
        digest: 'sha256:confirmed',
        regions: state.regions,
      },
    },
  });
});

test('resolved model query never leaks into the editable override and changing the override invalidates confirmation', () => {
  const confirmed = createSemanticCutoutState({
    strategy: 'semantic',
    query: '汉堡',
    model_query: 'hamburger',
    model_query_override: '',
    target_count: 1,
    source_asset_id: 'asset-1',
    status: 'confirmed',
    method: 'model-candidate-confirmed',
    digest: 'sha256:model-confirmed',
    regions: [{
      id: 'candidate-1',
      label: '汉堡',
      bbox: [0.1, 0.1, 0.8, 0.8],
      origin: 'automatic',
      confidence: 0.91,
    }],
  });
  assert.equal(confirmed.regions[0].origin, 'automatic');
  assert.equal(confirmed.regions[0].confidence, 0.91);
  assert.equal(confirmed.model_query_override, '');
  const renamed = updateSemanticCutoutState(confirmed, { query: '月球齿轮' });
  assert.equal(renamed.model_query, '');
  assert.equal(renamed.model_query_override, '');
  const changed = updateSemanticCutoutState(confirmed, { model_query_override: 'burger' });
  assert.equal(changed.status, 'draft');
  assert.equal(changed.model_query, '');
  assert.equal(changed.model_query_override, 'burger');
  assert.equal(changed.digest, '');
  assert.deepEqual(changed.regions, []);
});

test('automatic candidate states always explain confirmation or manual recovery', () => {
  assert.deepEqual(semanticGroundingPresentation({
    candidate_status: 'candidates',
    message: '本地模型找到 2 个候选，请逐个检查后确认',
  }), {
    status: 'candidates',
    tone: 'candidate',
    message: '本地模型找到 2 个候选，请逐个检查后确认',
  });
  const low = semanticGroundingPresentation({ candidate_status: 'low_confidence' });
  assert.equal(low.tone, 'warning');
  assert.match(low.message, /修正选区|补充框选/);
  const failed = semanticGroundingPresentation({ candidate_status: 'failed' });
  assert.equal(failed.tone, 'error');
  assert.match(failed.message, /手动框选/);
  const unavailable = semanticGroundingPresentation({ candidate_status: 'unavailable' });
  assert.equal(unavailable.tone, 'manual');
  assert.match(unavailable.message, /手动框选/);
  const unmapped = semanticGroundingPresentation({ candidate_status: 'query_unmapped' });
  assert.equal(unmapped.tone, 'manual');
  assert.match(unmapped.message, /英文识别词|手动框选/);
});

test('an adopted review suggestion keeps its lower-trust origin in the durable draft', () => {
  const state = createSemanticCutoutState({
    strategy: 'semantic',
    query: '红酒杯',
    target_count: 1,
    regions: [{
      id: 'candidate-1',
      label: 'wine glass',
      bbox: [0.1, 0.1, 0.5, 0.8],
      origin: 'automatic-review',
      confidence: 0.72,
    }],
  });
  assert.equal(state.regions[0].origin, 'automatic-review');
  assert.equal(state.regions[0].confidence, 0.72);
});
