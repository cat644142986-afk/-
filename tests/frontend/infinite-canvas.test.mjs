import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';

import {
  createApiSpatialCanvasAdapter,
  createMemorySpatialCanvasAdapter,
} from '../../src/js/infinite-canvas-adapter.js';

const packageJson = JSON.parse(readFileSync(new URL('../../package.json', import.meta.url), 'utf8'));
const packageLock = JSON.parse(readFileSync(new URL('../../package-lock.json', import.meta.url), 'utf8'));
const html = readFileSync(new URL('../../src/index.html', import.meta.url), 'utf8');
const app = readFileSync(new URL('../../src/js/app.js', import.meta.url), 'utf8');
const workspace = readFileSync(new URL('../../src/js/infinite-canvas-workspace.js', import.meta.url), 'utf8');
const island = readFileSync(new URL('../../src/js/infinite-canvas-island.jsx', import.meta.url), 'utf8');
const adapterSource = readFileSync(new URL('../../src/js/infinite-canvas-adapter.js', import.meta.url), 'utf8');
const apiSource = readFileSync(new URL('../../src/js/api.js', import.meta.url), 'utf8');
const stableUiCss = readFileSync(new URL('../../src/css/stable-ui.css', import.meta.url), 'utf8');
const viteConfig = readFileSync(new URL('../../vite.config.js', import.meta.url), 'utf8');
const bundleVerifier = readFileSync(new URL('../../tools/verify-infinite-canvas-bundle.mjs', import.meta.url), 'utf8');

test('production dependencies pin the approved Excalidraw and React versions', () => {
  assert.equal(packageJson.dependencies['@excalidraw/excalidraw'], '0.18.1');
  assert.equal(packageJson.dependencies.react, '18.3.1');
  assert.equal(packageJson.dependencies['react-dom'], '18.3.1');
  assert.equal(packageLock.packages['node_modules/@excalidraw/excalidraw'].version, '0.18.1');
  assert.equal(packageLock.packages['node_modules/react'].version, '18.3.1');
  assert.equal(packageLock.packages['node_modules/react-dom'].version, '18.3.1');
});

test('spatial workspace is a primary route and the old Studio switch is gone', () => {
  assert.match(html, /data-page="canvas"[^>]*aria-label="无限画布"/);
  assert.match(html, /id="page-canvas"[^>]*data-page-name="canvas"/);
  assert.match(html, /id="spatial-canvas-list"/);
  assert.match(html, /id="btn-spatial-new"/);
  assert.match(html, /id="btn-spatial-rename"[^>]*aria-label="重命名当前画布"/);
  assert.match(html, /id="spatial-inspector"[^>]*hidden/);
  assert.match(html, /class="spatial-workspace" data-adapter="sqlite-v8"/);
  assert.doesNotMatch(html, /data-studio-view=/);
  assert.doesNotMatch(html, />自由画布</);
  assert.match(app, /infiniteCanvasWorkspace\.setPage\(page === 'canvas'\)/);
  assert.match(workspace, /function thumbnailElements\(scene\)/);
});

test('Excalidraw is isolated behind a user-triggered dynamic import', () => {
  assert.match(workspace, /runtimeLoader = \(\) => import\('\.\/infinite-canvas-island\.jsx'\)/);
  assert.doesNotMatch(workspace, /from ['"]@excalidraw\/excalidraw/);
  assert.doesNotMatch(app, /from ['"]@excalidraw\/excalidraw/);
  assert.match(island, /from '@excalidraw\/excalidraw'/);
  assert.match(island, /@excalidraw\/excalidraw\/index\.css/);
  assert.match(adapterSource, /currentItemRoughness:\s*0/);
  assert.match(island, /changeViewBackgroundColor: false/);
  assert.match(island, /saveAsImage: false/);
  assert.match(island, /aiEnabled=\{false\}/);
  assert.match(stableUiCss, /\.spatial-canvas-host \.App-toolbar__extra-tools-dropdown \[data-testid="toolbar-embeddable"\]/);
  assert.match(stableUiCss, /\[data-testid="toolbar-laser"\]/);
  assert.match(viteConfig, /manifest:\s*true/);
  assert.match(bundleVerifier, /isDynamicEntry/);
  assert.match(bundleVerifier, /modulepreload/);
});

test('IC2 memory adapter provides list, recent, rename and scene continuity without a ledger', () => {
  let tick = 0;
  const adapter = createMemorySpatialCanvasAdapter({
    now: () => new Date(Date.UTC(2026, 8, 2, 12, tick++)),
    idFactory: () => `canvas-${tick}`,
  });
  const first = adapter.create({ name: '  商品   主图方案  ' });
  const second = adapter.create({ name: '候选分支' });
  assert.equal(first.name, '商品 主图方案');
  assert.equal(adapter.list()[0].id, second.id);

  const renamed = adapter.rename(first.id, '白底图对比');
  assert.equal(renamed.name, '白底图对比');
  adapter.open(first.id);
  assert.equal(adapter.list()[0].id, first.id);

  const updated = adapter.updateScene(first.id, {
    elements: [{ id: 'frame-1', type: 'frame', isDeleted: false }],
    appState: { scrollX: 42, zoom: { value: 0.8 } },
    files: { forbidden: { dataURL: 'data:image/png;base64,AAAA' } },
  });
  assert.equal(updated.summary.frame_count, 1);
  assert.equal(updated.scene.appState.scrollX, 42);
  assert.deepEqual(updated.scene.files, {});
  assert.doesNotMatch(adapterSource, /localStorage|sessionStorage|fetch\(|\.saveCanvas\(/);
});

test('IC3 production adapter uses schema v8 APIs and strips scene file bytes', async () => {
  const calls = [];
  const base = {
    id: 'spatial:api',
    name: 'API 画布',
    current_revision: 1,
    current_version_id: 'spatialver:1',
    created_at: '2026-09-02T00:00:00Z',
    updated_at: '2026-09-02T00:00:00Z',
    last_opened_at: '2026-09-02T00:00:00Z',
    summary: { element_count: 0, image_count: 0, video_count: 0, frame_count: 0 },
    thumbnail: { element_count: 0, image_count: 0, video_count: 0, frame_count: 0, elements: [] },
  };
  const api = {
    async listSpatialCanvases() { calls.push(['list']); return { canvases: [base] }; },
    async createSpatialCanvas(payload) {
      calls.push(['create', payload]);
      return { ...base, scene: { elements: [], app_state: { zoom: { value: 1 } }, files: {} } };
    },
    async openSpatialCanvas(id) {
      calls.push(['open', id]);
      return { ...base, scene: { elements: [], app_state: { zoom: { value: 0.8 }, scrollX: 42 }, files: {} } };
    },
    async renameSpatialCanvas(id, payload) {
      calls.push(['rename', id, payload]);
      return { ...base, name: payload.name };
    },
    async saveSpatialCanvasScene(id, payload) {
      calls.push(['save', id, payload]);
      return {
        ...base,
        current_revision: 2,
        current_version_id: 'spatialver:2',
        scene: payload.scene,
      };
    },
  };
  const adapter = createApiSpatialCanvasAdapter({ api });
  await adapter.load();
  assert.equal(adapter.kind, 'sqlite-v8');
  assert.equal(adapter.list()[0].id, base.id);
  const opened = await adapter.open(base.id);
  assert.equal(opened.scene.appState.zoom.value, 0.8);
  assert.equal(opened.scene.appState.scrollX, 42);
  const saved = await adapter.updateScene(base.id, {
    elements: [{ id: 'rect-1', type: 'rectangle', x: 1, y: 2, width: 3, height: 4 }],
    appState: { zoom: { value: 0.7 }, scrollX: 9 },
    files: { forbidden: { dataURL: 'data:image/png;base64,AAAA' } },
  });
  assert.equal(saved.current_revision, 2);
  const savePayload = calls.find(([kind]) => kind === 'save')[2];
  assert.equal(savePayload.expected_revision, 1);
  assert.equal(savePayload.scene.schema_version, 1);
  assert.deepEqual(savePayload.scene.files, {});
  assert.equal(savePayload.scene.app_state.zoom.value, 0.7);
  assert.match(apiSource, /export async function listSpatialCanvases/);
  assert.match(apiSource, /export async function saveSpatialCanvasScene/);
  assert.match(workspace, /createApiSpatialCanvasAdapter\(\{ api: API \}\)/);
});

test('IC3 production adapter serializes saves and refreshes revision after a conflict', async () => {
  const canvas = {
    id: 'spatial:serial',
    name: '串行保存',
    current_revision: 1,
    current_version_id: 'spatialver:1',
    created_at: '2026-09-02T00:00:00Z',
    updated_at: '2026-09-02T00:00:00Z',
    last_opened_at: '2026-09-02T00:00:00Z',
    summary: { element_count: 0, image_count: 0, video_count: 0, frame_count: 0 },
    thumbnail: { element_count: 0, image_count: 0, video_count: 0, frame_count: 0, elements: [] },
  };
  const expectedRevisions = [];
  let saveCount = 0;
  const api = {
    async listSpatialCanvases() { return { canvases: [canvas] }; },
    async openSpatialCanvas() {
      return {
        ...canvas,
        current_revision: 4,
        current_version_id: 'spatialver:4',
        scene: { elements: [], app_state: {}, files: {} },
      };
    },
    async saveSpatialCanvasScene(id, payload) {
      assert.equal(id, canvas.id);
      expectedRevisions.push(payload.expected_revision);
      saveCount += 1;
      if (saveCount === 1) {
        const conflict = new Error('revision conflict');
        conflict.status = 409;
        throw conflict;
      }
      return {
        ...canvas,
        current_revision: payload.expected_revision + 1,
        current_version_id: `spatialver:${payload.expected_revision + 1}`,
        scene: payload.scene,
      };
    },
  };
  const adapter = createApiSpatialCanvasAdapter({ api });
  await adapter.load();
  const first = adapter.updateScene(canvas.id, { elements: [], appState: {}, files: {} });
  const second = adapter.updateScene(canvas.id, {
    elements: [{ id: 'frame-2', type: 'frame', x: 0, y: 0, width: 100, height: 100 }],
    appState: {},
    files: {},
  });
  await assert.rejects(first, /revision conflict/);
  const saved = await second;
  assert.deepEqual(expectedRevisions, [1, 4]);
  assert.equal(saved.current_revision, 5);
  assert.equal(adapter.get(canvas.id).current_revision, 5);
  assert.equal((await first.catch((error) => error)).current.current_revision, 4);
  assert.match(workspace, /savePromise = savePromise/);
  assert.match(workspace, /mountedIsland\?\.updateScene\?\.\(error\.current\.scene\)/);
  assert.match(island, /canvasApi\?\.updateScene/);
});

test('scene adapter keeps clean Excalidraw defaults and no embedded file bytes', () => {
  const adapter = createMemorySpatialCanvasAdapter({ idFactory: () => 'clean' });
  const record = adapter.create();
  assert.equal(record.scene.appState.currentItemRoughness, 0);
  assert.equal(record.scene.appState.currentItemStrokeStyle, 'solid');
  assert.deepEqual(record.scene.files, {});
  assert.doesNotMatch(JSON.stringify(record.scene), /data:image|;base64,/i);
});
