import assert from 'node:assert/strict';
import test from 'node:test';
import { activeCanvasReference, canvasReferenceOptions } from '../../src/js/canvas-reference-adapter.js';

test('Retake picker options come only from durable Canvas images, without a second board store', () => {
  const elements = [
    { id: 'source', type: 'image', fileId: 'file-source', customData: { asset_id: 'ast:source' } },
    { id: 'result', type: 'image', fileId: 'file-result', customData: { asset_id: 'ast:result', result_id: 'ast:result' } },
    { id: 'bad-result', type: 'image', customData: { asset_id: 'ast:source', result_id: 'ast:result' } },
    { id: 'loose', type: 'image', fileId: 'file-loose' },
    { id: 'deleted', type: 'image', isDeleted: true, customData: { asset_id: 'ast:deleted' } },
    { id: 'task', type: 'rectangle', customData: { task_id: 'job:1' } },
  ];
  const options = canvasReferenceOptions(elements, {
    'file-source': { dataURL: 'blob:source' },
    'file-result': { dataURL: 'blob:result' },
  }, 'result', { id: 'ast:result', name: '精确结果.png' });
  assert.deepEqual(options, [
    { elementId: 'source', assetId: 'ast:source', resultId: '', previewUrl: 'blob:source', title: '素材 · ast:source' },
    { elementId: 'result', assetId: 'ast:result', resultId: 'ast:result', previewUrl: 'blob:result', title: '精确结果.png' },
  ]);
});

test('reference projection never dereferences a missing selection or reference', () => {
  const options = [{ elementId: 'source', assetId: 'ast:source', title: 'source' }];
  assert.equal(activeCanvasReference(null, undefined, options), null);
  assert.equal(activeCanvasReference(null, undefined, []), null);
  assert.equal(activeCanvasReference({ elementId: 'source' }, undefined, options), null);
  assert.equal(activeCanvasReference({ elementId: 'source' }, 'other', options), null);
  assert.deepEqual(activeCanvasReference({ elementId: 'source' }, 'source', options), options[0]);
});
