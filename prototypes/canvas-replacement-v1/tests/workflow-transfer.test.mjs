import assert from 'node:assert/strict';
import test from 'node:test';
import { zipSync, strToU8 } from 'fflate';
import { workflowScene, exportWorkflow, importWorkflow, readWorkflowZip, WORKFLOW_FORMAT } from '../src/workflow-transfer.js';

const media = { asset_id: 'ast_original', role: 'result_main', result_id: 'ast_original', task_id: 'job_old', kind: 'image', width: 3000, height: 2000, mime: 'image/png', name: 'sample.png' };
const node = { id: 'source', type: 'image', position: { x: 100, y: 20 }, style: { width: 450, height: 300 }, data: { media, execution: { task_id: 'job_old' }, draft: { prompt: '只改数字', references: [media] } } };

test('export selected child uses world coordinates and excludes active execution/runtime data', () => {
  const group = { id: 'group', type: 'group', position: { x: 500, y: 500 }, style: { width: 900, height: 700 }, data: {} };
  const scene = workflowScene([group, { ...node, parentId: 'group' }], [], {}, {}, ['source']);
  assert.equal(scene.nodes.length, 1); assert.equal(scene.nodes[0].parentId, undefined);
  assert.deepEqual(scene.nodes[0].position, { x: 600, y: 520 }); assert.equal(scene.nodes[0].data.execution, undefined);
});

test('ZIP roundtrip retains original bytes, remaps node/ref IDs and never imports Task execution', async () => {
  const scene = workflowScene([node], [], {}, { templates: [{ id: 'my_template', name: '精修', category: '', text: '只改数字' }] });
  const api = { validateNodeWorkflow: async scene => structuredClone(scene), getAssetBlob: async () => new Blob(['pixel-bytes'], { type: 'image/png' }) };
  const bundle = await exportWorkflow({ scene, api, includeResources: true });
  const calls = [];
  const restored = await importWorkflow(new File([bundle], 'workflow.zip'), { api, canvasAdapter: { importMedia: async files => {
    calls.push(await files[0].text()); return { assets: [{ media: { ...media, asset_id: 'ast_imported', role: 'workspace_source', result_id: undefined, task_id: undefined } }], errors: [] };
  } } });
  assert.deepEqual(calls, ['pixel-bytes']); assert.notEqual(restored.nodes[0].id, 'source');
  assert.equal(restored.nodes[0].data.media.asset_id, 'ast_imported'); assert.equal(restored.nodes[0].data.draft.references[0].asset_id, 'ast_imported');
  assert.equal(restored.nodes[0].data.media.task_id, undefined); assert.equal(restored.nodes[0].data.execution, undefined);
  assert.notEqual(restored.templates[0].id, 'my_template');
});

test('unsafe paths, duplicates, truncation and actual expansion limit reject before append', () => {
  const valid = strToU8('{}');
  assert.throws(() => readWorkflowZip(zipSync({ '../escape.png': valid, 'workflow.json': valid })), /不安全/);
  const zip = zipSync({ 'workflow.json': strToU8('a'.repeat(20000)) });
  assert.throws(() => readWorkflowZip(zip, { maxBytes: 2000 }), /限制|超限/);
  assert.throws(() => readWorkflowZip(zip.subarray(0, zip.length - 22)), /完整/);
});

test('missing JSON asset refuses append instead of inventing a reference or task', async () => {
  const scene = workflowScene([node], [], {}, {});
  const file = new File([JSON.stringify({ format: WORKFLOW_FORMAT, scene, assets: [] })], 'workflow.json');
  await assert.rejects(importWorkflow(file, { api: { validateNodeWorkflow: async value => value, getAsset: async () => { throw new Error('missing'); } }, canvasAdapter: {} }), /缺少资源/);
});
