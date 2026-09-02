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
import {
  appendMaskStroke,
  buildFreeLocalEditContract,
  buildPaidOutpaintContract,
  compatibleLocalEditCandidates,
  createMaskDefinition,
  defaultOutpaintConfig,
  expectedLocalEditCandidateSize,
  invertMaskDefinition,
  normalizeOutpaintConfig,
  normalizeSourceRoi,
  roiFromSceneDrag,
  sceneRectFromSourceRoi,
  setMaskBase,
  setMaskFeather,
} from '../../src/js/local-edit-model.js';

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

test('result assets remain result-backed layers and never enter source asset membership', () => {
  const result = { ...asset('ast:result', 1600, 1200), role: 'result_local_edit' };
  const resultDocument = createCanvasDocument('single', result);
  assert.equal(resultDocument.layers[0].source.kind, 'result');
  assert.deepEqual(resultDocument.source_asset_ids, []);

  const sourceDocument = createCanvasDocument('single', asset('asset:first'));
  const added = addAssetLayer(sourceDocument, result);
  assert.equal(added.layer.source.kind, 'result');
  assert.deepEqual(sourceDocument.source_asset_ids, ['asset:first']);
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

test('source replacement undo and redo recalculate authoritative asset references', () => {
  const document = createCanvasDocument('single', asset('asset:first', 1200, 800));
  const layer = document.layers[0];
  appendLayerMutation(document, layer.id, {
    source: {
      kind: 'result',
      id: 'ast_local_result',
      proxy_ref: 'proxy:thumbnail:512',
      original_pixel_width: 1200,
      original_pixel_height: 800,
    },
  }, 'command:local-edit-compose', '2026-09-02T01:00:00.000Z', 'operation:local-compose');

  assert.equal(layer.source.kind, 'result');
  assert.equal(layer.source.id, 'ast_local_result');
  assert.deepEqual(document.source_asset_ids, []);
  assert.equal(document.operations[0].mutation.before.source.id, 'asset:first');
  assert.equal(document.operations[0].mutation.after.source.id, 'ast_local_result');

  undoCanvas(document);
  assert.equal(layer.source.kind, 'asset');
  assert.equal(layer.source.id, 'asset:first');
  assert.deepEqual(document.source_asset_ids, ['asset:first']);

  redoCanvas(document);
  assert.equal(layer.source.kind, 'result');
  assert.equal(layer.source.id, 'ast_local_result');
  assert.deepEqual(document.source_asset_ids, []);
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

test('manual ROI supports drag and exact numeric entry without leaving source pixels', () => {
  const layer = createCanvasDocument('single', asset('asset:first', 1200, 800)).layers[0];
  layer.transform = {
    ...layer.transform,
    x: 100,
    y: 50,
    scale_x: 0.5,
    scale_y: 0.5,
  };
  assert.deepEqual(
    roiFromSceneDrag(layer, { x: 150, y: 100 }, { x: 350, y: 250 }),
    { x: 100, y: 100, width: 400, height: 300 },
  );
  assert.deepEqual(
    sceneRectFromSourceRoi(layer, { x: 100, y: 100, width: 400, height: 300 }),
    { x: 150, y: 100, width: 200, height: 150 },
  );
  assert.deepEqual(
    normalizeSourceRoi(layer, { x: 10, y: 20, width: 300, height: 200 }),
    { x: 10, y: 20, width: 300, height: 200 },
  );
  assert.throws(
    () => normalizeSourceRoi(layer, { x: 1100, y: 20, width: 300, height: 200 }),
    /不能超出原图像素范围/,
  );
  layer.transform.rotation_degrees = 15;
  assert.throws(() => roiFromSceneDrag(layer, { x: 0, y: 0 }, { x: 10, y: 10 }), /旋转为 0°/);
});

test('manual mask keeps reversible base, brush, inverse, feather, and free spec facts', () => {
  const layer = createCanvasDocument('single', asset('asset:first', 1200, 800)).layers[0];
  const roi = { id: 'roi:test', rect: { x: 100, y: 100, width: 400, height: 300 } };
  let definition = createMaskDefinition(layer);
  definition = appendMaskStroke(definition, 'include', 24, [
    { x: 120, y: 130 },
    { x: 180, y: 190 },
    { x: 900, y: 700 },
  ], roi.rect);
  assert.equal(definition.strokes.length, 1);
  assert.equal(definition.strokes[0].points.length, 2);
  definition = setMaskFeather(definition, 8);
  assert.equal(definition.feather_radius, 8);
  definition = invertMaskDefinition(definition);
  assert.equal(definition.base, 'full');
  assert.equal(definition.strokes[0].mode, 'exclude');
  definition = setMaskBase(definition, 'empty');
  assert.equal(definition.strokes.length, 0);

  const mask = {
    version: {
      id: 'maskver:test',
      pixel_sha256: 'a'.repeat(64),
      definition: createMaskDefinition(layer, 'full'),
    },
  };
  const contract = buildFreeLocalEditContract({
    operationId: 'operation:test-local-edit',
    canvasVersionId: 'canvasver:test',
    layer,
    sourceSha256: 'b'.repeat(64),
    sourcePixelSha256: 'c'.repeat(64),
    roi,
    mask,
  });
  assert.equal(contract.mask.id, 'maskver:test');
  assert.equal(contract.mask.sha256, 'A'.repeat(64));
  assert.equal(contract.source_sha256, 'B'.repeat(64));
  assert.equal(contract.source_pixel_sha256, 'C'.repeat(64));
  assert.equal(contract.cost.mode, 'free');
  assert.equal(contract.cost.automatic_paid_retry, false);
});

test('local edit candidates merge current and recoverable results under the frozen pixel contract', () => {
  const layer = createCanvasDocument('single', asset('asset:first', 1200, 800)).layers[0];
  const contract = { mode: 'inpaint', source_size: { width: 1200, height: 800 } };
  const current = {
    main: [
      { asset_id: 'ast_current', role: 'result_main', width: 1200, height: 800, name: '当前结果' },
      { asset_id: 'ast_wrong_size', role: 'result_main', width: 1024, height: 1024, name: '尺寸不符' },
    ],
  };
  const recent = [
    { id: 'ast_current', role: 'result_main', width: 1200, height: 800, name: '重复结果' },
    { id: 'ast_recoverable', role: 'result_local_edit', width: 1200, height: 800, name: '历史结果' },
    { id: 'ast_source', role: 'workspace_source', width: 1200, height: 800, name: '非结果素材' },
  ];
  assert.deepEqual(expectedLocalEditCandidateSize(contract, layer), { width: 1200, height: 800 });
  assert.deepEqual(
    compatibleLocalEditCandidates(current, recent, contract, layer).map((item) => item.id),
    ['ast_current', 'ast_recoverable'],
  );
  assert.deepEqual(expectedLocalEditCandidateSize({
    mode: 'outpaint',
    outpaint: { output_width: 1600, output_height: 1200 },
  }, layer), { width: 1600, height: 1200 });
});

test('outpaint freezes explicit output placement transition and one paid call without retry', () => {
  const layer = createCanvasDocument('single', asset('asset:first', 1200, 800)).layers[0];
  const defaults = defaultOutpaintConfig(layer);
  assert.deepEqual(defaults, {
    output_width: 1800,
    output_height: 1200,
    source_x: 300,
    source_y: 200,
    transition_width: 0,
  });
  const normalized = normalizeOutpaintConfig(layer, {
    ...defaults,
    transition_width: 32,
  });
  const roi = {
    id: 'roi:outpaint',
    coordinate_space: 'output-pixel',
    rect: { x: 0, y: 0, width: 1800, height: 1200 },
  };
  const contract = buildPaidOutpaintContract({
    operationId: 'operation:outpaint',
    canvasVersionId: 'canvasver:one',
    layer,
    sourceSha256: 'b'.repeat(64),
    sourcePixelSha256: 'c'.repeat(64),
    roi,
    outpaint: normalized,
    confirmed: true,
  });
  assert.equal(contract.mode, 'outpaint');
  assert.deepEqual(contract.outpaint, normalized);
  assert.equal(contract.mask, null);
  assert.equal(contract.cost.mode, 'paid');
  assert.equal(contract.cost.confirmed_call_count, 1);
  assert.equal(contract.cost.user_confirmed, true);
  assert.equal(contract.cost.automatic_paid_retry, false);
  assert.throws(
    () => buildPaidOutpaintContract({
      operationId: 'operation:unconfirmed',
      canvasVersionId: 'canvasver:one',
      layer,
      roi,
      outpaint: normalized,
      confirmed: false,
    }),
    /确认本次扩图规格/,
  );
});

test('production canvas uses SQLite APIs and is wired into the Studio lifecycle', () => {
  assert.match(apiSource, /export async function getCanvas\(mode/);
  assert.match(apiSource, /export async function saveCanvas\(mode/);
  assert.match(apiSource, /export async function exportCanvas\(mode/);
  assert.match(apiSource, /X-Canvas-Source/);
  assert.match(apiSource, /export async function getCommands\(/);
  assert.match(apiSource, /export async function createCanvasRoi\(/);
  assert.match(apiSource, /export async function getCanvasRois\(/);
  assert.match(apiSource, /export async function saveCanvasMask\(/);
  assert.match(apiSource, /export async function createLocalEditSpec\(/);
  assert.match(apiSource, /export async function getLatestLocalEditSpec\(/);
  assert.match(apiSource, /export async function composeLocalEdit\(mode/);
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
  assert.doesNotMatch(htmlSource, /data-studio-view="quick"/);
  assert.doesNotMatch(htmlSource, /data-studio-view="canvas"/);
  assert.match(htmlSource, /data-page="canvas"[^>]*aria-label="无限画布"/);
  assert.match(htmlSource, /id="page-canvas"[^>]*data-page-name="canvas"/);
  assert.match(htmlSource, /id="studio-fabric-canvas"/);
  assert.match(controllerSource, /api\.getCanvasRois\(entry\.currentVersionId, layer\.id/);
  assert.match(controllerSource, /api\.createCanvasRoi\(local\.roiRequest/);
  assert.match(controllerSource, /api\.saveCanvasMask\(local\.roi\.id/);
  assert.match(controllerSource, /api\.createLocalEditSpec\(local\.specRequest/);
  assert.match(controllerSource, /api\.getLatestLocalEditSpec\(\{/);
  assert.match(controllerSource, /api\.composeLocalEdit\(currentMode, request/);
  assert.match(controllerSource, /onSpatialResult\(\{/);
  assert.match(controllerSource, /async function openAsset\(assetId/);
  assert.match(htmlSource, /id="canvas-return-spatial"/);
  assert.match(controllerSource, /buildPaidOutpaintContract\(\{/);
  assert.match(controllerSource, /purpose: 'outpaint'/);
  assert.match(controllerSource, /localEditMode === 'outpaint'/);
  assert.match(controllerSource, /api\.getWorkspace\(currentMode/);
  assert.match(controllerSource, /api\.getAsset\(assetId/);
  assert.match(controllerSource, /const assetDetails = new Map\(\)/);
  assert.match(controllerSource, /reloadObjectFromLayer\(operation\.mutation\.target_layer_id\)/);
  assert.match(controllerSource, /const selectedBeforeBuild = selectedLayerId/);
  assert.match(controllerSource, /const selectedBeforeReload = selectedLayerId === layerId/);
  assert.match(controllerSource, /if \(suppressSelectionCleared \|\| activeTool !== 'select'\) return/);
  assert.match(controllerSource, /setSaveState\('saved', '画布已保存', `revision \$\{response\.canvas\.current_revision\}`\)/);
  assert.match(controllerSource, /dataset\.localEditBase/);
  assert.match(controllerSource, /key === 'r' \? 'roi'/);
  assert.match(controllerSource, /key === 'b' \? 'brush-include'/);
  assert.match(controllerSource, /key === 'e' \? 'brush-exclude'/);
  assert.match(controllerSource, /objectRole', 'local-edit-roi'/);
  assert.match(htmlSource, /id="canvas-panel-local-edit"/);
  assert.match(htmlSource, /id="local-edit-save-mask"/);
  assert.match(htmlSource, /id="local-edit-candidate"/);
  assert.match(htmlSource, /id="local-edit-apply"/);
  assert.match(htmlSource, /data-local-edit-mode="outpaint"/);
  assert.match(htmlSource, /id="local-edit-outpaint-width"/);
  assert.match(htmlSource, /id="local-edit-outpaint-confirm"/);
  assert.match(htmlSource, /id="local-edit-prepare-outpaint"/);
  assert.match(htmlSource, /role="status" aria-live="polite" aria-atomic="true"/);
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
