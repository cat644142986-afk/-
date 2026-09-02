import assert from 'node:assert/strict';
import test from 'node:test';

import {
  SPATIAL_DRAG_MIME,
  SPATIAL_REFERENCE_FIELDS,
  buildSpatialNodeBatch,
  mergeSpatialNodeBatch,
  parseSpatialDragItem,
  selectedSpatialBusinessElement,
  serializeSpatialDragItem,
  spatialBusinessKey,
  spatialContextActions,
  spatialCustomData,
  spatialItemFromAsset,
  spatialItemFromJob,
  spatialLineageFocusElements,
} from '../../src/js/spatial-canvas-items.js';

test('spatial items keep only ledger references in customData', () => {
  const item = spatialItemFromAsset({
    id: 'ast:result-2',
    name: '白底候选',
    role: 'result_main',
    mime: 'image/png',
    width: 2048,
    height: 2048,
    metadata: { lineage_parent_id: 'ast:result-1', ignored_path: 'D:\\private.png' },
  }, {
    task_id: 'job:7',
    product_profile_version_id: 'product-profile-version:3',
  });
  assert.equal(item.business_kind, 'result');
  assert.deepEqual(Object.keys(spatialCustomData(item)), [...SPATIAL_REFERENCE_FIELDS]);
  assert.deepEqual(spatialCustomData(item), {
    asset_id: 'ast:result-2',
    result_id: 'ast:result-2',
    task_id: 'job:7',
    product_profile_version_id: 'product-profile-version:3',
    lineage_parent_id: 'ast:result-1',
  });
  assert.doesNotMatch(JSON.stringify(spatialCustomData(item)), /private|path|base64|data:image/i);
  assert.equal(spatialBusinessKey(item), 'result:ast:result-2');
});

test('drag payload round-trips without URLs or file bytes', () => {
  assert.equal(SPATIAL_DRAG_MIME, 'application/x-product-atelier-spatial-item');
  const item = spatialItemFromAsset({
    id: 'ast:source-1', name: '商品原图', role: 'workspace_source', width: 1600, height: 1200,
  });
  const payload = serializeSpatialDragItem(item);
  assert.doesNotMatch(payload, /https?:|file:|base64|data:image/i);
  assert.deepEqual(parseSpatialDragItem(payload), item);
});

test('task nodes expose only task-compatible context actions', () => {
  const item = spatialItemFromJob({
    id: 'job:9', title: '商业主图', status: 'partial', total_items: 4, completed_items: 3,
    snapshot: { product_profile_version_id: 'product-profile-version:8' },
  });
  assert.equal(spatialBusinessKey(item), 'task:job:9');
  assert.deepEqual(spatialContextActions(item), ['open-task']);
  assert.equal(item.references.product_profile_version_id, 'product-profile-version:8');
});

test('result nodes are placed beside their parent and receive a lineage arrow', () => {
  let id = 0;
  const parent = {
    id: 'parent', type: 'image', x: 100, y: 120, width: 300, height: 220,
    isDeleted: false,
    customData: { asset_id: 'ast:parent', result_id: 'ast:parent' },
  };
  const item = spatialItemFromAsset({
    id: 'ast:child', name: '精修结果', role: 'result_local_edit', width: 1600, height: 1200,
  }, { lineage_parent_id: 'ast:parent', task_id: 'job:10' });
  const batch = buildSpatialNodeBatch([item], {
    elements: [parent],
    appState: { width: 1200, height: 800, zoom: { value: 1 }, scrollX: 0, scrollY: 0 },
    idFactory: (prefix) => `${prefix}_${++id}`,
  });
  assert.equal(batch.skeletons.length, 2);
  assert.equal(batch.skeletons[0].type, 'arrow');
  assert.equal(batch.skeletons[1].type, 'image');
  assert.equal(batch.skeletons[1].x, 560);
  assert.equal(batch.skeletons[1].customData.lineage_parent_id, 'ast:parent');
  assert.deepEqual(batch.nodeIds, ['spatial_node_1']);
  assert.deepEqual(batch.proxyRequests, [{
    elementId: 'spatial_node_1', fileId: 'proxy_ast_child', assetId: 'ast:child',
  }]);
  assert.deepEqual(batch.lineageBindings, [{
    arrow_id: 'spatial_lineage_2',
    parent_element_id: 'parent',
    child_element_id: 'spatial_node_1',
  }]);
  const converted = batch.skeletons.map((element) => ({
    ...element,
    isDeleted: false,
    boundElements: null,
    ...(element.type === 'arrow' ? { startBinding: null, endBinding: null } : {}),
  }));
  const merged = mergeSpatialNodeBatch([parent], converted, batch.lineageBindings);
  assert.deepEqual(merged[0].boundElements, [{ id: 'spatial_lineage_2', type: 'arrow' }]);
  assert.deepEqual(merged[1].startBinding, { elementId: 'parent', focus: 0, gap: 12 });
  assert.deepEqual(merged[1].endBinding, { elementId: 'spatial_node_1', focus: 0, gap: 12 });
  assert.deepEqual(merged[2].boundElements, [{ id: 'spatial_lineage_2', type: 'arrow' }]);
  assert.equal(parent.boundElements, undefined);
  const mergedAgain = mergeSpatialNodeBatch([], merged, batch.lineageBindings);
  assert.deepEqual(mergedAgain[0].boundElements, [{ id: 'spatial_lineage_2', type: 'arrow' }]);
  assert.deepEqual(mergedAgain[2].boundElements, [{ id: 'spatial_lineage_2', type: 'arrow' }]);
  assert.deepEqual(
    spatialLineageFocusElements([item], [parent], batch.skeletons).map((element) => element.id),
    ['parent', 'spatial_lineage_2', 'spatial_node_1'],
  );
});

test('independent imports focus only their new additions', () => {
  const item = spatialItemFromAsset({ id: 'ast:standalone', name: '独立素材' });
  const additions = [{ id: 'standalone', type: 'image', isDeleted: false }];
  assert.deepEqual(spatialLineageFocusElements(item ? [item] : [], [], additions), additions);
  assert.deepEqual(mergeSpatialNodeBatch([], additions), additions);
});

test('selection returns one compatible business element and hides multi-selection actions', () => {
  const elements = [
    { id: 'image-1', type: 'image', isDeleted: false, customData: { asset_id: 'ast:1' } },
    { id: 'rect-1', type: 'rectangle', isDeleted: false, customData: {} },
  ];
  assert.equal(selectedSpatialBusinessElement(elements, { selectedElementIds: { 'image-1': true } })?.id, 'image-1');
  assert.equal(selectedSpatialBusinessElement(elements, { selectedElementIds: { 'image-1': true, 'rect-1': true } })?.id, 'image-1');
  assert.equal(selectedSpatialBusinessElement([
    ...elements,
    { id: 'task-1', type: 'rectangle', isDeleted: false, customData: { task_id: 'job:1' } },
  ], { selectedElementIds: { 'image-1': true, 'task-1': true } }), null);
});

test('context actions distinguish source and result images', () => {
  assert.deepEqual(spatialContextActions({ customData: { asset_id: 'ast:source' } }), [
    'cutout', 'white-background', 'outpaint', 'local-edit', 'generate-image', 'generate-video', 'fine-edit',
  ]);
  assert.deepEqual(spatialContextActions({ customData: { asset_id: 'ast:result', result_id: 'ast:result' } }), [
    'outpaint', 'local-edit', 'generate-image', 'generate-video', 'compare', 'export', 'fine-edit',
  ]);
});
