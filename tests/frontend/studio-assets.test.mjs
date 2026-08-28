import test from 'node:test';
import assert from 'node:assert/strict';

import {
  assetCollectionCopy,
  assetReferenceCopy,
  boundedAssetRenderList,
  filterAndSortAssets,
  modesForAssetCollection,
  moveAssetInOrder,
  removeAssetFromCollectionSelections,
} from '../../src/js/studio-assets.js';

const modeConfig = {
  single: { collection: 'product' },
  'multi-file': { collection: 'product' },
  'group-split': { collection: 'group' },
  'cutout-batch': { collection: 'cutout' },
};

test('removing a shared product asset clears both product selections only', () => {
  const result = removeAssetFromCollectionSelections({
    single: ['a', 'b'],
    'multi-file': ['a', 'c'],
    'group-split': ['a'],
    'cutout-batch': ['d'],
  }, modeConfig, 'product', 'a');
  assert.deepEqual(result.single, ['b']);
  assert.deepEqual(result['multi-file'], ['c']);
  assert.deepEqual(result['group-split'], ['a']);
  assert.deepEqual(result['cutout-batch'], ['d']);
  assert.deepEqual(modesForAssetCollection(modeConfig, 'product'), ['single', 'multi-file']);
  assert.deepEqual(modesForAssetCollection(modeConfig, 'cutout'), ['cutout-batch']);
});

test('asset domain copy explains sharing and isolation in Chinese', () => {
  assert.match(assetCollectionCopy('product').note, /单产品与多文件共用/);
  assert.match(assetCollectionCopy('group').note, /不与产品或抠图素材混合/);
  assert.match(assetCollectionCopy('cutout').note, /切换工作流不会清空/);
});

test('reference copy explains why safe removal preserves history', () => {
  const protectedCopy = assetReferenceCopy({
    reference_count: 3,
    references: { jobs: ['j1', 'j2'], drafts: ['d1'] },
  });
  assert.equal(protectedCopy.tone, 'protected');
  assert.match(protectedCopy.title, /3 处历史记录/);
  assert.match(protectedCopy.detail, /不会破坏/);

  const retainedCopy = assetReferenceCopy({ references: {}, retention_pending: true, retention_days: 30, retention_remaining_days: 12 });
  assert.equal(retainedCopy.tone, 'retained');
  assert.match(retainedCopy.detail, /12 天保护期/);
});

test('asset search and sorting stay deterministic for a two-hundred item library', () => {
  const assets = Array.from({ length: 200 }, (_, index) => ({
    id: `asset-${index + 1}`,
    name: `商品-${String(index + 1).padStart(3, '0')}.png`,
    width: index === 149 ? 1600 : 1200,
    height: index === 149 ? 900 : 1200,
    size_bytes: (index + 1) * 1000,
    created_at: `2026-08-${String((index % 27) + 1).padStart(2, '0')}T00:00:00+00:00`,
  }));
  assert.deepEqual(filterAndSortAssets(assets, { query: '1600×900' }).map((item) => item.id), ['asset-150']);
  assert.equal(filterAndSortAssets(assets, { sort: 'size' })[0].id, 'asset-200');
  assert.equal(filterAndSortAssets(assets, { sort: 'name' })[0].id, 'asset-1');
});

test('manual order moves one asset without dropping rows', () => {
  const assets = [{ id: 'a' }, { id: 'b' }, { id: 'c' }];
  assert.deepEqual(moveAssetInOrder(assets, 'b', -1).map((item) => item.id), ['b', 'a', 'c']);
  assert.deepEqual(moveAssetInOrder(assets, 'a', -1).map((item) => item.id), ['a', 'b', 'c']);
  assert.deepEqual(assets.map((item) => item.id), ['a', 'b', 'c']);
});

test('bounded canvas rendering keeps an offscreen selected asset available', () => {
  const assets = Array.from({ length: 200 }, (_, index) => ({ id: `asset-${index + 1}` }));
  const rendered = boundedAssetRenderList(assets, ['asset-180'], 60);
  assert.equal(rendered.length, 61);
  assert.equal(rendered.at(-1).id, 'asset-180');
  assert.equal(new Set(rendered.map((asset) => asset.id)).size, 61);
});
