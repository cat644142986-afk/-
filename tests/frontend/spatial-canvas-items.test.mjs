import assert from 'node:assert/strict';
import test from 'node:test';

import {
  SPATIAL_DRAG_MIME,
  SPATIAL_REFERENCE_FIELDS,
  buildSpatialNodeBatch,
  mergeSpatialNodeBatch,
  normalizeSpatialBusinessPresentation,
  parseSpatialDragItem,
  selectedSpatialBusinessElement,
  serializeSpatialDragItem,
  spatialBusinessKey,
  spatialContextActions,
  spatialCustomData,
  spatialItemFromAsset,
  spatialItemFromJob,
  spatialScenePointFromViewport,
  spatialTaskLabel,
  updateSpatialTaskElements,
  spatialLineageFocusElements,
  uniqueSpatialBusinessItems,
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

test('viewport drop coordinates convert to scene coordinates using the live Excalidraw view', () => {
  assert.deepEqual(spatialScenePointFromViewport({ clientX: 470, clientY: 320 }, {
    offsetLeft: 70,
    offsetTop: 20,
    scrollX: 40,
    scrollY: -30,
    zoom: { value: 2 },
  }), { x: 160, y: 180 });
  assert.equal(spatialScenePointFromViewport({ clientX: NaN, clientY: 20 }, {}), null);
});

test('explicit drops honor their scene point and repeated center imports remain visible', () => {
  let id = 0;
  const item = spatialItemFromAsset({
    id: 'ast:pointer', name: '拖入素材', width: 1200, height: 900,
  });
  const first = buildSpatialNodeBatch([item], {
    insertionPoint: { x: 240, y: 180 },
    idFactory: (prefix) => `${prefix}_${++id}`,
  });
  const firstNode = first.skeletons.find((element) => element.type === 'image');
  assert.equal(firstNode.x + firstNode.width / 2, 240);
  assert.equal(firstNode.y + firstNode.height / 2, 180);

  const second = buildSpatialNodeBatch([item], {
    elements: [{ ...firstNode, isDeleted: false }],
    insertionPoint: { x: 840, y: 620 },
    idFactory: (prefix) => `${prefix}_${++id}`,
  });
  const secondNode = second.skeletons.find((element) => element.type === 'image');
  assert.equal(secondNode.x + secondNode.width / 2, 840);
  assert.equal(secondNode.y + secondNode.height / 2, 620);

  const repeated = buildSpatialNodeBatch([item], {
    elements: [{ ...firstNode, isDeleted: false }],
    insertionPoint: { x: 240, y: 180 },
    idFactory: (prefix) => `${prefix}_${++id}`,
  });
  const repeatedNode = repeated.skeletons.find((element) => element.type === 'image');
  assert.equal(repeatedNode.x + repeatedNode.width / 2, 276);
  assert.equal(repeatedNode.y + repeatedNode.height / 2, 216);
});

test('task nodes expose only task-compatible context actions', () => {
  const item = spatialItemFromJob({
    id: 'job:9', title: '商业主图', status: 'partial', total_items: 4, completed_items: 3,
    snapshot: { product_profile_version_id: 'product-profile-version:8' },
  }, { lineage_parent_id: 'ast:first-frame' });
  assert.equal(spatialBusinessKey(item), 'task:job:9');
  assert.deepEqual(spatialContextActions(item), ['open-task']);
  assert.equal(item.references.product_profile_version_id, 'product-profile-version:8');
  assert.equal(item.references.lineage_parent_id, 'ast:first-frame');
  assert.equal(parseSpatialDragItem(serializeSpatialDragItem(item)).references.lineage_parent_id, 'ast:first-frame');
  assert.equal(spatialTaskLabel(item), '商业主图\n部分完成 · 3/4 项');
});

test('durable job progress updates an existing task label without replacing its business identity', () => {
  const task = {
    id: 'task-node', type: 'rectangle', customData: { task_id: 'job:video' },
    boundElements: [{ id: 'task-label', type: 'text' }],
  };
  const label = {
    id: 'task-label', type: 'text', containerId: 'task-node',
    text: '生成视频预览\n排队中 · 0/1 项', originalText: '生成视频预览\n排队中 · 0/1 项',
  };
  const item = spatialItemFromJob({
    id: 'job:video', title: '生成视频预览', status: 'failed', total_items: 1, completed_items: 0,
  });
  const update = updateSpatialTaskElements([task, label], item);
  assert.equal(update.changed, true);
  assert.equal(update.elements[0], task);
  assert.equal(update.elements[1].text, '生成视频预览\n失败 · 0/1 项');
  assert.equal(update.elements[1].originalText, '生成视频预览\n失败 · 0/1 项');
  assert.equal(update.taskElement.customData.task_id, 'job:video');
  assert.equal(updateSpatialTaskElements(update.elements, item).changed, false);
});

test('video task nodes sit below their source while sibling results use separate lanes', () => {
  let id = 0;
  const parent = {
    id: 'source', type: 'image', x: 100, y: 100, width: 300, height: 200, isDeleted: false,
    customData: { asset_id: 'ast:source' },
  };
  const task = spatialItemFromJob({
    id: 'job:video', title: '生成视频预览', status: 'queued', total_items: 1,
  }, { lineage_parent_id: 'ast:source' });
  const taskBatch = buildSpatialNodeBatch([task], {
    elements: [parent], idFactory: (prefix) => `${prefix}_${++id}`,
  });
  const taskNode = taskBatch.skeletons.find((element) => element.type === 'rectangle');
  assert.equal(taskNode.x, parent.x);
  assert.equal(taskNode.y, parent.y + parent.height + 120);
  assert.equal(taskBatch.skeletons.some((element) => element.type === 'arrow'), false);

  const first = spatialItemFromAsset({ id: 'ast:video-1', role: 'result_video', kind: 'video' }, {
    lineage_parent_id: 'ast:source',
  });
  const firstBatch = buildSpatialNodeBatch([first], {
    elements: [parent], idFactory: (prefix) => `${prefix}_${++id}`,
  });
  const firstNode = firstBatch.skeletons.find((element) => element.type === 'embeddable');
  const second = spatialItemFromAsset({ id: 'ast:video-2', role: 'result_video', kind: 'video' }, {
    lineage_parent_id: 'ast:source',
  });
  const secondBatch = buildSpatialNodeBatch([second], {
    elements: [parent, { ...firstNode, isDeleted: false }], idFactory: (prefix) => `${prefix}_${++id}`,
  });
  const secondNode = secondBatch.skeletons.find((element) => element.type === 'embeddable');
  assert.notEqual(secondNode.y, firstNode.y);
});

test('idempotent imports reject existing and repeated business references', () => {
  const existing = [{
    id: 'existing', type: 'embeddable', isDeleted: false,
    customData: { asset_id: 'ast:video', result_id: 'ast:video' },
  }];
  const duplicate = spatialItemFromAsset({ id: 'ast:video', role: 'result_video', kind: 'video' });
  const fresh = spatialItemFromAsset({ id: 'ast:fresh', role: 'result_video', kind: 'video' });
  assert.deepEqual(uniqueSpatialBusinessItems([duplicate, fresh, fresh], existing), [fresh]);
});

test('result nodes retain lineage data without painting a permanent arrow', () => {
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
  assert.equal(batch.skeletons.length, 1);
  assert.equal(batch.skeletons[0].type, 'image');
  assert.equal(batch.skeletons[0].x, 560);
  assert.equal(batch.skeletons[0].roundness, null);
  assert.equal(batch.skeletons[0].customData.lineage_parent_id, 'ast:parent');
  assert.deepEqual(batch.nodeIds, ['spatial_node_1']);
  assert.deepEqual(batch.proxyRequests, [{
    elementId: 'spatial_node_1', fileId: 'proxy_ast_child', assetId: 'ast:child',
  }]);
  assert.deepEqual(batch.lineageBindings, []);
  const merged = mergeSpatialNodeBatch([parent], batch.skeletons);
  assert.equal(merged[1].id, 'spatial_node_1');
  assert.equal(parent.boundElements, undefined);
  assert.deepEqual(
    spatialLineageFocusElements([item], [parent], batch.skeletons).map((element) => element.id),
    ['parent', 'spatial_node_1'],
  );
});

test('video results become embeddables without image proxy bytes or visual lineage', () => {
  let id = 0;
  const parent = {
    id: 'parent-video-frame', type: 'image', x: 80, y: 120, width: 320, height: 240,
    isDeleted: false,
    customData: { asset_id: 'ast:first-frame', result_id: null },
  };
  const item = spatialItemFromAsset({
    id: 'ast:video-result',
    name: '商品运镜预览',
    role: 'result_video',
    kind: 'video',
    mime: 'video/webm',
    width: 1280,
    height: 720,
    metadata: { lineage_parent_id: 'ast:first-frame', duration_seconds: 5 },
  }, {
    task_id: 'job:video-1',
    product_profile_version_id: 'product-profile-version:video-1',
  });
  assert.equal(item.kind, 'video');
  assert.equal(item.business_kind, 'result');
  assert.equal(parseSpatialDragItem(serializeSpatialDragItem(item)).kind, 'video');

  const batch = buildSpatialNodeBatch([item], {
    elements: [parent],
    appState: { width: 1200, height: 800, zoom: { value: 1 }, scrollX: 0, scrollY: 0 },
    idFactory: (prefix) => `${prefix}_${++id}`,
  });
  assert.deepEqual(batch.proxyRequests, []);
  assert.equal(batch.skeletons.length, 1);
  assert.equal(batch.skeletons[0].type, 'embeddable');
  assert.equal(batch.skeletons[0].link, 'product-atelier-video://ast:video-result');
  assert.deepEqual(Object.keys(batch.skeletons[0].customData), [...SPATIAL_REFERENCE_FIELDS]);
  assert.deepEqual(batch.skeletons[0].customData, {
    asset_id: 'ast:video-result',
    result_id: 'ast:video-result',
    task_id: 'job:video-1',
    product_profile_version_id: 'product-profile-version:video-1',
    lineage_parent_id: 'ast:first-frame',
  });
  const serialized = JSON.stringify(batch.skeletons[0]);
  assert.doesNotMatch(serialized, /base64|data:video|[A-Za-z]:\\\\/i);

  assert.deepEqual(batch.lineageBindings, []);
  assert.equal(mergeSpatialNodeBatch([parent], batch.skeletons)[1].id, batch.skeletons[0].id);
});

test('PA image placement preserves extreme source ratios and only PA decoration is removed', () => {
  const wide = buildSpatialNodeBatch([spatialItemFromAsset({
    id: 'ast:wide', role: 'workspace_source', width: 3000, height: 300,
  })], { idFactory: (prefix) => `${prefix}_wide` }).skeletons[0];
  assert.equal(wide.width, 420);
  assert.equal(wide.height, 42);
  const source = [
    { id: 'business', type: 'image', customData: { asset_id: 'ast:old' }, roundness: { type: 3 }, crop: { x: 3 }, width: 320, height: 240, boundElements: [{ id: 'spatial_lineage_old', type: 'arrow' }, { id: 'user-arrow', type: 'arrow' }] },
    { id: 'spatial_lineage_old', type: 'arrow' },
    { id: 'user-arrow', type: 'arrow' },
    { id: 'user-text', type: 'text', text: '原样文字', fontFamily: 1, fontSize: 37 },
  ];
  const cleaned = normalizeSpatialBusinessPresentation(source);
  assert.deepEqual(cleaned.map((element) => element.id), ['business', 'user-arrow', 'user-text']);
  assert.equal(cleaned[0].roundness, null);
  assert.deepEqual(cleaned[0].crop, { x: 3 });
  assert.deepEqual(cleaned[0].boundElements, [{ id: 'user-arrow', type: 'arrow' }]);
  assert.deepEqual(cleaned[2], source[3]);
  assert.equal(normalizeSpatialBusinessPresentation(cleaned), cleaned);
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
