export const CONTRACT_VERSION = 'growth-foundation-v1';
export const STORAGE_KEY = 'product-atelier:g1a-canvas:v1';
export const PROXY_COUNT = 200;
export const ARTBOARD = Object.freeze({
  id: 'artboard:synthetic-main',
  name: '主画板 1920 x 1080',
  x: 0,
  y: 0,
  width: 1920,
  height: 1080,
  pixelWidth: 1920,
  pixelHeight: 1080,
});

const PROFILE_ID = 'profile:synthetic-catalog';

function clone(value) {
  return JSON.parse(JSON.stringify(value));
}

function pad(value, width = 3) {
  return String(value).padStart(width, '0');
}

function createLayer(index) {
  const ordinal = index + 1;
  const column = index % 20;
  const row = Math.floor(index / 20);
  return {
    id: `layer:synthetic-proxy-${pad(ordinal)}`,
    artboard_id: ARTBOARD.id,
    source: {
      kind: 'asset',
      id: `asset:synthetic-product-${pad(ordinal)}`,
      proxy_ref: `fixture:synthetic-product-proxy-${pad(ordinal)}`,
      original_pixel_width: 4096,
      original_pixel_height: 4096,
    },
    transform: {
      x: 82 + column * 92,
      y: 70 + row * 98,
      scale_x: 0.72,
      scale_y: 0.72,
      rotation_degrees: 0,
      opacity: 1,
    },
    z_index: index,
    visible: true,
    locked: false,
  };
}

export function createSyntheticBundle(count = PROXY_COUNT, timestamp = new Date().toISOString()) {
  const layers = Array.from({ length: count }, (_, index) => createLayer(index));
  return {
    contract_version: CONTRACT_VERSION,
    canvas_document: {
      id: 'canvas:synthetic-g1a',
      revision: 0,
      active_artboard_id: ARTBOARD.id,
      source_asset_ids: layers.map((layer) => layer.source.id),
      artboards: [{
        id: ARTBOARD.id,
        name: ARTBOARD.name,
        rect: { x: ARTBOARD.x, y: ARTBOARD.y, width: ARTBOARD.width, height: ARTBOARD.height },
        export: {
          pixel_width: ARTBOARD.pixelWidth,
          pixel_height: ARTBOARD.pixelHeight,
          color_space: 'srgb',
        },
      }],
      layers,
      operations: [],
      undo_cursor: -1,
      created_at: timestamp,
      updated_at: timestamp,
    },
    masks: [],
    rois: [],
    product_profiles: [{
      id: PROFILE_ID,
      sku: 'SYNTHETIC-CATALOG',
      revision: 1,
      category: 'synthetic-product-grid',
      facts: { source: 'generated-fixture', proxy_count: count, original_edge: 4096 },
      components: [{
        id: 'component:synthetic-product-body',
        name: '商品主体',
        policy: 'must_preserve',
      }],
      approved_reference_ids: layers.length ? [layers[0].source.id] : [],
    }],
    quality_issues: [],
    recipes: [{
      id: 'recipe:existing-quick-cutout',
      name: '快速抠图',
      revision: 1,
      product_profile_id: PROFILE_ID,
      steps: [{
        order: 1,
        command_id: 'command:existing-remove-background',
        tool_contract_version: '2026-08-31.2',
        paid: false,
        requires_user_confirmation: false,
      }],
      quality_gate_codes: ['quality:non-empty-alpha'],
      outputs: [{
        role: 'output:transparent-cutout',
        pixel_width: 4096,
        pixel_height: 4096,
        format: 'png',
      }],
      failure_policy: {
        partial_success: 'preserve_succeeded_items',
        paid_retry: 'manual_confirmation_only',
      },
    }],
  };
}

export function artboardPlacement(artboard = ARTBOARD) {
  return {
    left: artboard.x,
    top: artboard.y,
    width: artboard.width,
    height: artboard.height,
    originX: 'left',
    originY: 'top',
  };
}

export function layerSnapshot(layer) {
  return {
    transform: clone(layer.transform),
    z_index: layer.z_index,
    visible: layer.visible,
    locked: layer.locked,
  };
}

function mergeSnapshot(before, patch) {
  return {
    transform: { ...before.transform, ...(patch.transform || {}) },
    z_index: patch.z_index ?? before.z_index,
    visible: patch.visible ?? before.visible,
    locked: patch.locked ?? before.locked,
  };
}

function applySnapshot(layer, snapshot) {
  layer.transform = clone(snapshot.transform);
  layer.z_index = snapshot.z_index;
  layer.visible = snapshot.visible;
  layer.locked = snapshot.locked;
}

function touchDocument(document, timestamp) {
  document.revision += 1;
  document.updated_at = timestamp;
}

export function appendLayerMutation(
  bundle,
  layerId,
  patch,
  commandId = 'command:transform-layer',
  timestamp = new Date().toISOString(),
) {
  const document = bundle.canvas_document;
  const layer = document.layers.find((item) => item.id === layerId);
  if (!layer) throw new Error(`Unknown layer: ${layerId}`);

  const before = layerSnapshot(layer);
  const after = mergeSnapshot(before, patch);
  const retained = document.operations.slice(0, document.undo_cursor + 1);
  const nextOrdinal = retained.length + 1;
  const operation = {
    id: `operation:canvas-${pad(nextOrdinal, 6)}`,
    command_id: commandId,
    input_layer_ids: [layerId],
    output_layer_id: layerId,
    roi_id: null,
    mask_id: null,
    product_profile_id: null,
    mutation: {
      target_layer_id: layerId,
      before,
      after,
    },
    cost: {
      mode: 'free',
      confirmed_call_count: 0,
      user_confirmation_required: false,
      automatic_paid_retry: false,
    },
    status: 'succeeded',
    created_at: timestamp,
  };

  applySnapshot(layer, after);
  document.operations = [...retained, operation];
  document.undo_cursor = document.operations.length - 1;
  touchDocument(document, timestamp);
  return operation;
}

export function undo(bundle, timestamp = new Date().toISOString()) {
  const document = bundle.canvas_document;
  if (document.undo_cursor < 0) return null;
  const operation = document.operations[document.undo_cursor];
  const mutation = operation?.mutation;
  if (mutation) {
    const layer = document.layers.find((item) => item.id === mutation.target_layer_id);
    if (!layer) throw new Error(`Undo target is missing: ${mutation.target_layer_id}`);
    applySnapshot(layer, mutation.before);
  }
  document.undo_cursor -= 1;
  touchDocument(document, timestamp);
  return operation;
}

export function redo(bundle, timestamp = new Date().toISOString()) {
  const document = bundle.canvas_document;
  const nextIndex = document.undo_cursor + 1;
  if (nextIndex >= document.operations.length) return null;
  const operation = document.operations[nextIndex];
  const mutation = operation?.mutation;
  if (mutation) {
    const layer = document.layers.find((item) => item.id === mutation.target_layer_id);
    if (!layer) throw new Error(`Redo target is missing: ${mutation.target_layer_id}`);
    applySnapshot(layer, mutation.after);
  }
  document.undo_cursor = nextIndex;
  touchDocument(document, timestamp);
  return operation;
}

export function compileExistingQuickCutout(bundle, layerId, requestId = `canvas-${Date.now()}`) {
  const layer = bundle.canvas_document.layers.find((item) => item.id === layerId);
  if (!layer) throw new Error(`Unknown layer: ${layerId}`);
  return {
    command_id: 'command:existing-remove-background',
    tool_contract_version: '2026-08-31.2',
    request: {
      mode: 'cutout-batch',
      source_asset_ids: [layer.source.id],
      parameters: {
        model: 'local-rembg/birefnet-general',
        variations: 1,
        batch: 1,
        refine: false,
        brief: '',
        intent_locks: {},
        category: 'general',
        cutout_selection: { strategy: 'foreground' },
      },
      client_request_id: requestId,
    },
    execution: 'preview-only',
    paid: false,
  };
}

export function serializeBundle(bundle) {
  return JSON.stringify(bundle);
}

export function restoreBundle(serialized) {
  const value = typeof serialized === 'string' ? JSON.parse(serialized) : clone(serialized);
  if (value?.contract_version !== CONTRACT_VERSION) throw new Error('Unsupported canvas contract');
  if (!Array.isArray(value?.canvas_document?.layers)) throw new Error('Canvas layers are missing');
  if (!Array.isArray(value?.canvas_document?.operations)) throw new Error('Canvas history is missing');
  return value;
}

export function bundleMetrics(bundle) {
  const started = performance.now();
  const serialized = serializeBundle(bundle);
  const durationMs = performance.now() - started;
  const bytes = new TextEncoder().encode(serialized).byteLength;
  const original4kReferences = bundle.canvas_document.layers.filter((layer) => (
    layer.source.original_pixel_width >= 4096 || layer.source.original_pixel_height >= 4096
  )).length;
  return {
    layerCount: bundle.canvas_document.layers.length,
    operationCount: bundle.canvas_document.operations.length,
    original4kReferences,
    bytes,
    durationMs,
  };
}
