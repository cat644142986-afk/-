import assert from 'node:assert/strict';
import test from 'node:test';
import { savePromptTemplate, removePromptTemplate } from '../src/prompt-templates.js';
import { createNodeCanvasAdapter } from '../src/node-canvas-adapter.js';
import { encodeNodeScene } from '../src/node-scene-codec.js';

test('personal template CRUD preserves text and does not modify builtins or other templates', () => {
  const value = { id: 'template_test', name: '保留文字', category: '商业', text: '第一行\n第二行' };
  let metadata = savePromptTemplate({}, value);
  metadata = savePromptTemplate(metadata, { ...value, name: '新名称' });
  assert.equal(metadata.templates.length, 1); assert.equal(metadata.templates[0].text, value.text);
  assert.throws(() => savePromptTemplate(metadata, { ...value, id: 'builtin_reference' }), /无效/);
  assert.throws(() => savePromptTemplate(metadata, { ...value, text: '' }), /填写/);
  assert.deepEqual(removePromptTemplate(metadata, value.id).templates, []);
});

test('templates share the existing Canvas scene and survive save/reopen and ordinary saves', async () => {
  let record = { id: 'canvas', current_revision: 1, scene: encodeNodeScene([], [], { x: 0, y: 0, zoom: 1 }) };
  const api = {
    createSpatialCanvas: async () => record, openSpatialCanvas: async () => structuredClone(record),
    saveSpatialCanvasScene: async (_, payload) => (record = { ...record, current_revision: record.current_revision + 1, scene: payload.scene }),
  };
  const data = new Map(); const storage = { getItem: key => data.get(key), setItem: (key, value) => data.set(key, value) };
  const adapter = createNodeCanvasAdapter({ api, storage }); await adapter.load();
  const metadata = savePromptTemplate({}, { id: 'template_test', name: '精确修改', category: '', text: '只改数字' });
  await adapter.save([], [], { x: 0, y: 0, zoom: 1 }, metadata);
  await adapter.save([], [], { x: 10, y: 0, zoom: 1 });
  const restored = await createNodeCanvasAdapter({ api, storage }).load();
  assert.deepEqual(restored.metadata, metadata); assert.ok([...data.values()][0].length < 100);
});
