import test from 'node:test';
import assert from 'node:assert/strict';

import {
  createSemanticCutoutState,
  semanticCutoutPayload,
  semanticCutoutReadiness,
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
