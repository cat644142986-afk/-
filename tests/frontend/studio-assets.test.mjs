import test from 'node:test';
import assert from 'node:assert/strict';

import {
  assetCollectionCopy,
  assetReferenceCopy,
  modesForAssetCollection,
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

  const retainedCopy = assetReferenceCopy({ references: {}, retention_pending: true, retention_days: 30 });
  assert.equal(retainedCopy.tone, 'retained');
  assert.match(retainedCopy.detail, /30 天保护期/);
});
