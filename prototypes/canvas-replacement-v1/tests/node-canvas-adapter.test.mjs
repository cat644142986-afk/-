import test from 'node:test';
import assert from 'node:assert/strict';
import { createNodeCanvasAdapter, CANVAS_POINTER_KEY, LEGACY_SCENE_KEY } from '../src/node-canvas-adapter.js';
import { decodeNodeScene, encodeNodeScene, mediaIdentity } from '../src/node-scene-codec.js';

const empty = encodeNodeScene([], [], { x: 0, y: 0, zoom: 1 });
function fixture() {
  const values = new Map();
  const storage = { getItem: (key) => values.get(key) || null, setItem: (key, value) => values.set(key, value) };
  const docs = new Map();
  const receipts = new Map();
  const calls = [];
  const api = {
    async createSpatialCanvas(payload) { const doc = { id: 'spatial:fixture', current_revision: 1, scene: structuredClone(payload.scene) }; docs.set(doc.id, doc); return structuredClone(doc); },
    async openSpatialCanvas(id) { if (!docs.has(id)) throw new Error('missing'); return structuredClone(docs.get(id)); },
    async saveSpatialCanvasScene(id, payload) {
      calls.push(structuredClone(payload));
      if (receipts.has(payload.client_request_id)) return structuredClone(receipts.get(payload.client_request_id));
      const doc = { id, current_revision: docs.get(id).current_revision + 1, scene: structuredClone(payload.scene) };
      docs.set(id, doc); receipts.set(payload.client_request_id, doc); return structuredClone(doc);
    },
    async getAsset(id) { return { id, role: 'workspace_source', kind: 'image' }; },
    async getAssetContentUrl(id) { return `http://127.0.0.1:8765/api/assets/${id}/content`; },
    async getAssetThumbnailUrl(id) { return `http://127.0.0.1:8765/api/assets/${id}/thumbnail`; },
  };
  return { api, storage, docs, calls, values };
}

test('durable scene strips image bytes, URLs, selection and callbacks; keeps exact draft/media', () => {
  const media = mediaIdentity({ id: 'ast_exact', role: 'result_main', kind: 'image', name: 'result', width: 4096, height: 2048 });
  const scene = encodeNodeScene([{ id: 'n1', type: 'image', position: { x: 4, y: 5 }, selected: true, dragging: true, style: { width: 460, height: 230 },
    data: { media, name: '原图', src: 'data:image/png;base64,big', onRemove() {}, previewUrl: 'blob:temporary', referenceViews: { big: 'cache' }, draft: { prompt: '修改数字，其余不变', references: [media] } },
  }], [], { x: 20, y: -30, zoom: .9 });
  assert.ok(!JSON.stringify(scene).includes('data:'));
  assert.ok(!JSON.stringify(scene).includes('blob:'));
  assert.equal(scene.nodes[0].data.draft.references[0].asset_id, 'ast_exact');
  assert.equal(scene.nodes[0].data.media.result_id, 'ast_exact');
  assert.equal(decodeNodeScene(scene).nodes[0].selected, false);
});

test('save + reopen use the existing Canvas service; localStorage is only a small pointer', async () => {
  const f = fixture();
  const adapter = createNodeCanvasAdapter(f);
  await adapter.load();
  await adapter.save([{ id: 'prompt', type: 'prompt', position: { x: 0, y: 0 }, style: { width: 280, height: 200 }, data: { text: '中文\n保留换行', draft: { prompt: '草稿不丢' } } }], [], { x: 42, y: -17, zoom: 1.25 });
  const restored = await createNodeCanvasAdapter(f).load();
  assert.equal(restored.nodes[0].data.text, '中文\n保留换行');
  assert.equal(restored.nodes[0].data.draft.prompt, '草稿不丢');
  assert.deepEqual(restored.viewport, { x: 42, y: -17, zoom: 1.25 });
  assert.equal(f.values.size, 1);
  assert.ok(f.storage.getItem(CANVAS_POINTER_KEY).length < 100);
});

test('lost successful response replays the same receipt before saving newer content', async () => {
  const f = fixture(); const adapter = createNodeCanvasAdapter(f); await adapter.load();
  const original = f.api.saveSpatialCanvasScene; let loseResponse = true;
  f.api.saveSpatialCanvasScene = async (...args) => { const saved = await original(...args); if (loseResponse) { loseResponse = false; throw new Error('response lost'); } return saved; };
  await assert.rejects(adapter.save([], [], { x: 10, y: 0, zoom: 1 }), /lost/);
  await adapter.save([], [], { x: 20, y: 0, zoom: 1 });
  assert.equal(f.calls[0].client_request_id, f.calls[1].client_request_id);
  assert.equal(f.calls[0].expected_revision, f.calls[1].expected_revision);
  assert.equal(adapter.get().current_revision, 3);
  assert.equal(adapter.get().scene.viewport.x, 20);
});

test('broken pointer/legacy never silently opens and saves an empty canvas', async () => {
  const f = fixture(); f.storage.setItem(LEGACY_SCENE_KEY, 'broken-json');
  await assert.rejects(createNodeCanvasAdapter(f).load());
  assert.equal(f.docs.size, 0); assert.equal(f.storage.getItem(LEGACY_SCENE_KEY), 'broken-json');
});

test('an old Excalidraw scene cannot be mistaken for a new node document', () => {
  assert.throws(() => decodeNodeScene({ schema_version: 1, elements: [], files: {}, app_state: {} }), /兼容/);
});

test('packaged startup waits for read-only health before any document mutation', async () => {
  const f = fixture(); let checks = 0;
  f.api.checkHealth = async () => ({ ok: ++checks === 3 });
  await createNodeCanvasAdapter({ ...f, pause: async () => { assert.equal(f.docs.size, 0); } }).load();
  assert.equal(checks, 3); assert.equal(f.docs.size, 1);
});

test('startup failure preserves the pointer and never creates an empty document', async () => {
  const f = fixture(); f.api.checkHealth = async () => ({ ok: false });
  await assert.rejects(createNodeCanvasAdapter({ ...f, startupTimeoutMs: 0 }).load(), /服务尚未就绪/);
  assert.equal(f.docs.size, 0); assert.equal(f.values.size, 0);
});
