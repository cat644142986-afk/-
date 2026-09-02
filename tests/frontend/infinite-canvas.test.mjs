import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';

import { createMemorySpatialCanvasAdapter } from '../../src/js/infinite-canvas-adapter.js';

const packageJson = JSON.parse(readFileSync(new URL('../../package.json', import.meta.url), 'utf8'));
const packageLock = JSON.parse(readFileSync(new URL('../../package-lock.json', import.meta.url), 'utf8'));
const html = readFileSync(new URL('../../src/index.html', import.meta.url), 'utf8');
const app = readFileSync(new URL('../../src/js/app.js', import.meta.url), 'utf8');
const workspace = readFileSync(new URL('../../src/js/infinite-canvas-workspace.js', import.meta.url), 'utf8');
const island = readFileSync(new URL('../../src/js/infinite-canvas-island.jsx', import.meta.url), 'utf8');
const adapterSource = readFileSync(new URL('../../src/js/infinite-canvas-adapter.js', import.meta.url), 'utf8');
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

test('scene adapter keeps clean Excalidraw defaults and no embedded file bytes', () => {
  const adapter = createMemorySpatialCanvasAdapter({ idFactory: () => 'clean' });
  const record = adapter.create();
  assert.equal(record.scene.appState.currentItemRoughness, 0);
  assert.equal(record.scene.appState.currentItemStrokeStyle, 'solid');
  assert.deepEqual(record.scene.files, {});
  assert.doesNotMatch(JSON.stringify(record.scene), /data:image|;base64,/i);
});
