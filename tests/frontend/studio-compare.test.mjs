import test from 'node:test';
import assert from 'node:assert/strict';

import {
  activeResultEntry,
  clampCompareDivider,
  pannedCompareTransform,
  resultReviewEntries,
  steppedCompareTransform,
} from '../../src/js/studio-compare.js';

test('compare version entries keep main and cutout result identities stable', () => {
  const entries = resultReviewEntries({
    main: [
      { asset_id: 'main-a', version_label: '主图 V1' },
      { asset_id: 'main-b' },
    ],
    cutout: [{ asset_id: 'cutout-a', version_label: '透明底' }],
  });

  assert.deepEqual(entries.map(({ tab, index, label, item }) => ({
    tab, index, label, assetId: item.asset_id,
  })), [
    { tab: 'main', index: 0, label: '主图 V1', assetId: 'main-a' },
    { tab: 'main', index: 1, label: '结果 2', assetId: 'main-b' },
    { tab: 'cutout', index: 0, label: '透明底', assetId: 'cutout-a' },
  ]);
  assert.equal(activeResultEntry(entries, 'main', 1)?.item.asset_id, 'main-b');
  assert.equal(activeResultEntry(entries, 'cutout', 1), null);
});

test('compare divider and zoom remain inside the persisted interaction contract', () => {
  assert.equal(clampCompareDivider(-10), 3);
  assert.equal(clampCompareDivider(64.5), 64.5);
  assert.equal(clampCompareDivider(120), 97);
  assert.equal(clampCompareDivider('invalid'), 50);

  const reset = steppedCompareTransform({ zoom: 1.25, pan_x: 30, pan_y: -20 }, -0.25);
  assert.equal(reset.zoom, 1);
  assert.equal(reset.pan_x, 0);
  assert.equal(reset.pan_y, 0);
  assert.equal(steppedCompareTransform({ zoom: 3.9 }, 0.25).zoom, 4);
});

test('compare panning uses relative canvas movement and normalized bounds', () => {
  const moved = pannedCompareTransform({ zoom: 2, pan_x: 10, pan_y: -5 }, {
    deltaX: 50,
    deltaY: -25,
    width: 200,
    height: 100,
  });
  assert.equal(moved.pan_x, 35);
  assert.equal(moved.pan_y, -30);

  const clamped = pannedCompareTransform({ zoom: 2, pan_x: 90, pan_y: -90 }, {
    deltaX: 100,
    deltaY: -100,
    width: 100,
    height: 100,
  });
  assert.equal(clamped.pan_x, 100);
  assert.equal(clamped.pan_y, -100);
  assert.deepEqual(
    pannedCompareTransform({ zoom: 2, pan_x: 12, pan_y: 8 }, { width: 0, height: 100 }),
    {
      divider: 50,
      zoom: 2,
      pan_x: 12,
      pan_y: 8,
      secondary_result_asset_id: '',
      guide_dismissed: false,
    },
  );
});
