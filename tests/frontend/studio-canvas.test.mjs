import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import path from 'node:path';

import {
  CANVAS_COORDINATE_SYSTEM,
  CANVAS_DOCUMENT_SCHEMA_VERSION,
  CANVAS_PAGE_SIZE,
  addAssetLayer,
  appendLayerMutation,
  canvasDocumentClone,
  createCanvasDocument,
  layerObjectScale,
  redoCanvas,
  segmentedItems,
  transformFromFabricObject,
  undoCanvas,
} from '../../src/js/canvas-model.js';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..');
const [controllerSource, modelSource, appSource, apiSource, htmlSource] = await Promise.all([
  readFile(path.join(root, 'src/js/studio-canvas.js'), 'utf8'),
  readFile(path.join(root, 'src/js/canvas-model.js'), 'utf8'),
  readFile(path.join(root, 'src/js/app.js'), 'utf8'),
  readFile(path.join(root, 'src/js/api.js'), 'utf8'),
  readFile(path.join(root, 'src/index.html'), 'utf8'),
]);

function asset(id, width = 1200, height = 800) {
  return { id, name: `${id}.png`, width, height };
}

test('CanvasDocument starts with the frozen schema and top-left pixel contract', () => {
  const document = createCanvasDocument('single', asset('asset:first'), '2026-09-01T00:00:00.000Z');
  assert.equal(document.schema_version, CANVAS_DOCUMENT_SCHEMA_VERSION);
  assert.deepEqual(document.coordinate_system, CANVAS_COORDINATE_SYSTEM);
  assert.equal(document.revision, 0);
  assert.equal(document.layers.length, 1);
  assert.deepEqual(document.source_asset_ids, ['asset:first']);
  assert.equal(document.layers[0].source.proxy_ref, 'proxy:thumbnail:512');
  assert.equal(document.layers[0].source.original_pixel_width, 1200);
  assert.equal(document.layers[0].source.original_pixel_height, 800);
  assert.equal(document.undo_cursor, -1);
});

test('adding an asset is idempotent and keeps source references unique', () => {
  const document = createCanvasDocument('single', asset('asset:first'));
  const added = addAssetLayer(document, asset('asset:second', 900, 1400));
  const replay = addAssetLayer(document, asset('asset:second', 900, 1400));
  assert.equal(added.added, true);
  assert.equal(replay.added, false);
  assert.equal(document.layers.length, 2);
  assert.deepEqual(document.source_asset_ids, ['asset:first', 'asset:second']);
});

test('transform, visibility, and lock mutations stay reversible', () => {
  const document = createCanvasDocument('single', asset('asset:first'));
  const layer = document.layers[0];
  appendLayerMutation(document, layer.id, {
    transform: { ...layer.transform, x: 42, y: 64, scale_x: 0.5, scale_y: 0.75, rotation_degrees: 15 },
  });
  appendLayerMutation(document, layer.id, { visible: false }, 'command:toggle-layer');
  appendLayerMutation(document, layer.id, { locked: true }, 'command:toggle-layer-lock');
  assert.equal(layer.transform.x, 42);
  assert.equal(layer.transform.scale_y, 0.75);
  assert.equal(layer.visible, false);
  assert.equal(layer.locked, true);
  assert.equal(document.undo_cursor, 2);

  undoCanvas(document);
  undoCanvas(document);
  assert.equal(layer.locked, false);
  assert.equal(layer.visible, true);
  redoCanvas(document);
  assert.equal(layer.visible, false);
});

test('editing after undo truncates the redo branch without mutating a clone', () => {
  const document = createCanvasDocument('single', asset('asset:first'));
  const original = canvasDocumentClone(document);
  const originalX = original.layers[0].transform.x;
  const layer = document.layers[0];
  appendLayerMutation(document, layer.id, { transform: { ...layer.transform, x: 10 } });
  appendLayerMutation(document, layer.id, { transform: { ...layer.transform, x: 20 } });
  undoCanvas(document);
  appendLayerMutation(document, layer.id, { transform: { ...layer.transform, x: 30 } });
  assert.equal(document.operations.length, 2);
  assert.equal(document.undo_cursor, 1);
  assert.equal(redoCanvas(document), null);
  assert.equal(layer.transform.x, 30);
  assert.equal(original.layers[0].transform.x, originalX);
  assert.notEqual(original.layers[0].transform.x, layer.transform.x);
});

test('Fabric proxy scaling round-trips the authoritative original-pixel transform', () => {
  const document = createCanvasDocument('single', asset('asset:first', 4000, 3000));
  const layer = document.layers[0];
  const scale = layerObjectScale(layer, 512, 384);
  const transform = transformFromFabricObject(layer, {
    left: 25,
    top: 30,
    width: 512,
    height: 384,
    scaleX: scale.scaleX,
    scaleY: scale.scaleY,
    angle: 12,
    opacity: 0.8,
  });
  assert.equal(transform.scale_x, layer.transform.scale_x);
  assert.equal(transform.scale_y, layer.transform.scale_y);
  assert.deepEqual(
    { x: transform.x, y: transform.y, rotation_degrees: transform.rotation_degrees, opacity: transform.opacity },
    { x: 25, y: 30, rotation_degrees: 12, opacity: 0.8 },
  );
});

test('two hundred rows render in stable forty-item segments', () => {
  const rows = Array.from({ length: 200 }, (_, index) => ({ id: `layer:${index + 1}` }));
  const first = segmentedItems(rows, CANVAS_PAGE_SIZE);
  const third = segmentedItems(rows, CANVAS_PAGE_SIZE * 3);
  const final = segmentedItems(rows, 200);
  assert.deepEqual([first.visible, first.total, first.nextLimit, first.hasMore], [40, 200, 80, true]);
  assert.deepEqual([third.visible, third.nextLimit, third.hasMore], [120, 160, true]);
  assert.deepEqual([final.visible, final.hasMore], [200, false]);
});

test('production canvas uses SQLite APIs and is wired into the Studio lifecycle', () => {
  assert.match(apiSource, /export async function getCanvas\(mode/);
  assert.match(apiSource, /export async function saveCanvas\(mode/);
  assert.match(apiSource, /export async function exportCanvas\(mode/);
  assert.match(apiSource, /X-Canvas-Source/);
  assert.match(apiSource, /export async function getCommands\(/);
  assert.match(apiSource, /export async function executeCommand\(commandId/);
  assert.match(apiSource, /import\.meta\.env\?\.DEV/);
  assert.match(apiSource, /\['127\.0\.0\.1', 'localhost', '::1'\]/);
  assert.match(apiSource, /DEV_API_BASE \|\| 'http:\/\/127\.0\.0\.1:8765'/);
  assert.match(appSource, /createCanvasController\(/);
  assert.match(appSource, /canvasController\.hydrate\(mode, payload\?\.canvas\)/);
  assert.match(appSource, /await hydrateAssetUrls\(payload\.assets \|\| \[\]\);\s+canvasController\.syncAssets\(\)/);
  assert.match(appSource, /canvasController\.setMode\(mode\)/);
  assert.match(appSource, /canvasController\.setPage\(page === 'process'\)/);
  assert.match(appSource, /canvasController\.bind\(\)/);
  assert.match(appSource, /if \(view === 'canvas'\) workflowDock\.close\(false\)/);
  assert.match(htmlSource, /data-studio-view="quick"/);
  assert.match(htmlSource, /data-studio-view="canvas"/);
  assert.match(htmlSource, /id="studio-fabric-canvas"/);
});

test('canvas persistence never uses browser storage, embedded images, or machine paths', () => {
  const productionCanvas = `${controllerSource}\n${modelSource}`;
  assert.doesNotMatch(productionCanvas, /localStorage|sessionStorage/);
  assert.doesNotMatch(productionCanvas, /data:image|;base64,/i);
  assert.doesNotMatch(productionCanvas, /(?:[A-Z]:\\\\|file:\/\/)/i);
  assert.match(controllerSource, /FabricImage\.fromURL\(url, \{ crossOrigin: 'anonymous' \}\)/);
  assert.match(controllerSource, /fabricRuntimePromise = import\('fabric'\)/);
  assert.doesNotMatch(controllerSource, /from 'fabric'/);
  assert.match(controllerSource, /proxy\?\.max_edge \|\| 512/);
  assert.match(controllerSource, /api\.getAssetThumbnailUrl\(layer\.source\.id, proxySize\)/);
  assert.match(controllerSource, /width: layer\.source\.original_pixel_width/);
  assert.match(controllerSource, /height: layer\.source\.original_pixel_height/);
  assert.doesNotMatch(controllerSource, /CSS\.escape/);
  assert.match(controllerSource, /api\.saveCanvas\(mode, pending/);
  assert.match(controllerSource, /applyCanvasResponse\(mode, response, \{ rebuildCanvas: false \}\)/);
  assert.match(controllerSource, /entry\.saving = false;\s+if \(mode === currentMode\) updateExportControl\(\)/);
  assert.match(htmlSource, /id="canvas-export"[\s\S]*data-lucide="download"/);
  assert.match(controllerSource, /if \(entry\.dirty && !await saveMode\(currentMode\)\) return/);
  assert.match(controllerSource, /api\.exportCanvas\(currentMode, \{/);
  assert.match(controllerSource, /expected_revision: entry\.currentRevision/);
  assert.match(controllerSource, /exported\.source !== 'original-assets'/);
  assert.match(controllerSource, /api\.saveImage\(exported\.filename, dataB64\)/);
});
