import assert from 'node:assert/strict';
import test from 'node:test';

import {
  ARTBOARD,
  appendLayerMutation,
  artboardPlacement,
  bundleMetrics,
  compileExistingQuickCutout,
  createSyntheticBundle,
  redo,
  restoreBundle,
  serializeBundle,
  undo,
} from '../canvas-model.js';

const FIXED_TIME = '2026-09-01T00:00:00Z';

test('artboard placement uses the contract top-left instead of Fabric center defaults', () => {
  assert.deepEqual(artboardPlacement(), {
    left: 0,
    top: 0,
    width: ARTBOARD.width,
    height: ARTBOARD.height,
    originX: 'left',
    originY: 'top',
  });
});

test('synthetic document uses 200 lightweight proxies with 4K source references', () => {
  const bundle = createSyntheticBundle(200, FIXED_TIME);
  const document = bundle.canvas_document;
  assert.equal(document.layers.length, 200);
  assert.equal(document.source_asset_ids.length, 200);
  assert.equal(new Set(document.layers.map((layer) => layer.id)).size, 200);
  assert.ok(document.layers.every((layer) => layer.source.proxy_ref.startsWith('fixture:')));
  assert.ok(document.layers.every((layer) => layer.source.original_pixel_width === 4096));
  assert.ok(document.layers.every((layer) => layer.source.original_pixel_height === 4096));

  const serialized = serializeBundle(bundle).toLowerCase();
  assert.equal(serialized.includes('base64'), false);
  assert.equal(serialized.includes('data:image'), false);
  assert.equal(/[a-z]:\\/.test(serialized), false);
});

test('layer mutations survive serialization and keep restart-safe undo and redo', () => {
  const bundle = createSyntheticBundle(200, FIXED_TIME);
  const layerId = bundle.canvas_document.layers[0].id;
  const originalX = bundle.canvas_document.layers[0].transform.x;

  appendLayerMutation(bundle, layerId, {
    transform: { x: 460, rotation_degrees: 12 },
    locked: true,
  }, 'command:transform-layer', '2026-09-01T00:01:00Z');

  const restored = restoreBundle(serializeBundle(bundle));
  assert.equal(restored.canvas_document.layers[0].transform.x, 460);
  assert.equal(restored.canvas_document.layers[0].locked, true);
  assert.equal(restored.canvas_document.undo_cursor, 0);
  assert.equal(restored.canvas_document.operations[0].mutation.before.transform.x, originalX);

  undo(restored, '2026-09-01T00:02:00Z');
  assert.equal(restored.canvas_document.layers[0].transform.x, originalX);
  assert.equal(restored.canvas_document.layers[0].locked, false);
  assert.equal(restored.canvas_document.undo_cursor, -1);

  redo(restored, '2026-09-01T00:03:00Z');
  assert.equal(restored.canvas_document.layers[0].transform.x, 460);
  assert.equal(restored.canvas_document.layers[0].locked, true);
  assert.equal(restored.canvas_document.undo_cursor, 0);
});

test('a new mutation after undo truncates the abandoned redo branch', () => {
  const bundle = createSyntheticBundle(2, FIXED_TIME);
  const layerId = bundle.canvas_document.layers[0].id;
  appendLayerMutation(bundle, layerId, { visible: false }, 'command:toggle-layer', '2026-09-01T00:01:00Z');
  appendLayerMutation(bundle, layerId, { locked: true }, 'command:toggle-layer-lock', '2026-09-01T00:02:00Z');
  undo(bundle, '2026-09-01T00:03:00Z');
  appendLayerMutation(bundle, layerId, { transform: { y: 420 } }, 'command:transform-layer', '2026-09-01T00:04:00Z');

  assert.equal(bundle.canvas_document.operations.length, 2);
  assert.equal(bundle.canvas_document.undo_cursor, 1);
  assert.equal(bundle.canvas_document.operations[1].command_id, 'command:transform-layer');
  assert.equal(bundle.canvas_document.layers[0].locked, false);
});

test('existing quick cutout compiles the durable local job shape without executing it', () => {
  const bundle = createSyntheticBundle(1, FIXED_TIME);
  const layer = bundle.canvas_document.layers[0];
  const compiled = compileExistingQuickCutout(bundle, layer.id, 'canvas-request-001');

  assert.equal(compiled.command_id, 'command:existing-remove-background');
  assert.equal(compiled.execution, 'preview-only');
  assert.equal(compiled.paid, false);
  assert.equal(compiled.request.mode, 'cutout-batch');
  assert.deepEqual(compiled.request.source_asset_ids, [layer.source.id]);
  assert.deepEqual(compiled.request.parameters.cutout_selection, { strategy: 'foreground' });
  assert.equal(compiled.request.client_request_id, 'canvas-request-001');
});

test('200-layer contract stays compact and reports all 4K references', () => {
  const bundle = createSyntheticBundle(200, FIXED_TIME);
  const metrics = bundleMetrics(bundle);
  assert.equal(metrics.layerCount, 200);
  assert.equal(metrics.original4kReferences, 200);
  assert.ok(metrics.bytes < 500_000, `serialized contract is ${metrics.bytes} bytes`);
  assert.ok(metrics.durationMs < 250, `serialization took ${metrics.durationMs}ms`);
});
