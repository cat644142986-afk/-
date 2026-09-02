export const CANVAS_DOCUMENT_SCHEMA_VERSION = 1;
export const CANVAS_PAGE_SIZE = 40;
export const CANVAS_COORDINATE_SYSTEM = Object.freeze({
  unit: 'canvas-pixel',
  origin: 'top-left',
  x_axis: 'right',
  y_axis: 'down',
});

const CANVAS_ID_PATTERN = /^[a-z][a-z0-9._:-]{2,127}$/;

function clone(value) {
  return JSON.parse(JSON.stringify(value));
}

function finiteNumber(value, fallback = 0) {
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : fallback;
}

function rounded(value, precision = 100) {
  return Math.round(finiteNumber(value) * precision) / precision;
}

function canvasId(value, label) {
  const candidate = String(value || '').trim();
  if (!CANVAS_ID_PATTERN.test(candidate)) throw new Error(`${label} is not a valid canvas id`);
  return candidate;
}

function assetDimensions(asset) {
  const width = Math.round(finiteNumber(asset?.width));
  const height = Math.round(finiteNumber(asset?.height));
  if (width < 1 || height < 1) throw new Error('素材缺少可验证的原始像素信息');
  return { width, height };
}

export function artboardForAsset(asset) {
  const source = assetDimensions(asset);
  const longest = Math.max(source.width, source.height);
  const scale = 2048 / longest;
  return {
    id: 'artboard:main',
    name: '主画板',
    rect: {
      x: 0,
      y: 0,
      width: Math.max(64, Math.round(source.width * scale)),
      height: Math.max(64, Math.round(source.height * scale)),
    },
    export: {
      pixel_width: Math.max(64, Math.round(source.width * scale)),
      pixel_height: Math.max(64, Math.round(source.height * scale)),
      color_space: 'srgb',
    },
  };
}

function layerPlacement(asset, artboard, index) {
  const source = assetDimensions(asset);
  const firstLayer = index === 0;
  const targetWidth = artboard.rect.width * (firstLayer ? 0.7 : 0.3);
  const targetHeight = artboard.rect.height * (firstLayer ? 0.7 : 0.3);
  const scale = Math.min(targetWidth / source.width, targetHeight / source.height, 1);
  const renderedWidth = source.width * scale;
  const renderedHeight = source.height * scale;
  if (firstLayer) {
    return {
      x: rounded((artboard.rect.width - renderedWidth) / 2),
      y: rounded((artboard.rect.height - renderedHeight) / 2),
      scale,
    };
  }
  const column = (index - 1) % 3;
  const row = Math.floor((index - 1) / 3) % 3;
  const cellWidth = artboard.rect.width / 3;
  const cellHeight = artboard.rect.height / 3;
  return {
    x: rounded(column * cellWidth + (cellWidth - renderedWidth) / 2),
    y: rounded(row * cellHeight + (cellHeight - renderedHeight) / 2),
    scale,
  };
}

function layerSourceKind(asset) {
  return String(asset?.role || '').startsWith('result_') ? 'result' : 'asset';
}

function layerFromAsset(asset, artboard, index) {
  const sourceId = canvasId(asset?.id, 'asset.id');
  const dimensions = assetDimensions(asset);
  const placement = layerPlacement(asset, artboard, index);
  return {
    id: canvasId(`layer:${sourceId}`, 'layer.id'),
    artboard_id: artboard.id,
    source: {
      kind: layerSourceKind(asset),
      id: sourceId,
      proxy_ref: 'proxy:thumbnail:512',
      original_pixel_width: dimensions.width,
      original_pixel_height: dimensions.height,
    },
    transform: {
      x: placement.x,
      y: placement.y,
      scale_x: rounded(placement.scale, 10000),
      scale_y: rounded(placement.scale, 10000),
      rotation_degrees: 0,
      opacity: 1,
    },
    z_index: index,
    visible: true,
    locked: false,
  };
}

export function createCanvasDocument(mode, asset, timestamp = new Date().toISOString()) {
  const safeMode = canvasId(String(mode || '').toLowerCase(), 'mode');
  const artboard = artboardForAsset(asset);
  const layer = layerFromAsset(asset, artboard, 0);
  return {
    id: canvasId(`canvas:${safeMode}`, 'CanvasDocument.id'),
    schema_version: CANVAS_DOCUMENT_SCHEMA_VERSION,
    coordinate_system: { ...CANVAS_COORDINATE_SYSTEM },
    revision: 0,
    active_artboard_id: artboard.id,
    source_asset_ids: layer.source.kind === 'asset' ? [layer.source.id] : [],
    artboards: [artboard],
    layers: [layer],
    operations: [],
    undo_cursor: -1,
    created_at: timestamp,
    updated_at: timestamp,
  };
}

export function addAssetLayer(document, asset, timestamp = new Date().toISOString()) {
  const sourceId = canvasId(asset?.id, 'asset.id');
  if (document.layers.some((layer) => layer.source.id === sourceId)) {
    return { document, layer: null, added: false };
  }
  const artboard = document.artboards.find((item) => item.id === document.active_artboard_id);
  if (!artboard) throw new Error('当前画布缺少活动画板');
  const layer = layerFromAsset(asset, artboard, document.layers.length);
  document.layers.push(layer);
  if (layer.source.kind === 'asset') {
    document.source_asset_ids = [...new Set([...document.source_asset_ids, sourceId])];
  }
  document.updated_at = timestamp;
  return { document, layer, added: true };
}

export function layerSnapshot(layer) {
  const snapshot = {
    transform: clone(layer.transform),
    z_index: layer.z_index,
    visible: layer.visible,
    locked: layer.locked,
  };
  if (arguments[1]?.includeSource) snapshot.source = clone(layer.source);
  return snapshot;
}

function mergeSnapshot(before, patch = {}) {
  const snapshot = {
    transform: {
      ...before.transform,
      ...(patch.transform || {}),
    },
    z_index: patch.z_index ?? before.z_index,
    visible: patch.visible ?? before.visible,
    locked: patch.locked ?? before.locked,
  };
  if (before.source || patch.source) snapshot.source = clone(patch.source || before.source);
  return snapshot;
}

function recomputeSourceAssetIds(document) {
  document.source_asset_ids = [...new Set(
    document.layers
      .filter((layer) => layer.source?.kind === 'asset')
      .map((layer) => String(layer.source.id)),
  )];
}

function applySnapshot(document, layer, snapshot) {
  layer.transform = clone(snapshot.transform);
  layer.z_index = snapshot.z_index;
  layer.visible = snapshot.visible;
  layer.locked = snapshot.locked;
  if (snapshot.source) layer.source = clone(snapshot.source);
  recomputeSourceAssetIds(document);
}

export function appendLayerMutation(
  document,
  layerId,
  patch,
  commandId = 'command:transform-layer',
  timestamp = new Date().toISOString(),
  operationId = '',
) {
  const layer = document.layers.find((item) => item.id === layerId);
  if (!layer) throw new Error(`Unknown layer: ${layerId}`);
  const before = layerSnapshot(layer, { includeSource: Boolean(patch?.source) });
  const after = mergeSnapshot(before, patch);
  const retained = document.operations.slice(0, document.undo_cursor + 1);
  const ordinal = retained.length + 1;
  const operation = {
    id: canvasId(operationId || `operation:canvas-${String(ordinal).padStart(6, '0')}`, 'operation.id'),
    command_id: canvasId(commandId, 'operation.command_id'),
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
  applySnapshot(document, layer, after);
  document.operations = [...retained, operation];
  document.undo_cursor = document.operations.length - 1;
  document.updated_at = timestamp;
  return operation;
}

export function undoCanvas(document, timestamp = new Date().toISOString()) {
  if (document.undo_cursor < 0) return null;
  const operation = document.operations[document.undo_cursor];
  const layer = document.layers.find((item) => item.id === operation?.mutation?.target_layer_id);
  if (!layer) throw new Error('撤销目标图层已经不存在');
  applySnapshot(document, layer, operation.mutation.before);
  document.undo_cursor -= 1;
  document.updated_at = timestamp;
  return operation;
}

export function redoCanvas(document, timestamp = new Date().toISOString()) {
  const nextIndex = document.undo_cursor + 1;
  if (nextIndex >= document.operations.length) return null;
  const operation = document.operations[nextIndex];
  const layer = document.layers.find((item) => item.id === operation?.mutation?.target_layer_id);
  if (!layer) throw new Error('重做目标图层已经不存在');
  applySnapshot(document, layer, operation.mutation.after);
  document.undo_cursor = nextIndex;
  document.updated_at = timestamp;
  return operation;
}

export function layerObjectScale(layer, proxyWidth, proxyHeight) {
  return {
    scaleX: layer.transform.scale_x * layer.source.original_pixel_width / Math.max(1, proxyWidth),
    scaleY: layer.transform.scale_y * layer.source.original_pixel_height / Math.max(1, proxyHeight),
  };
}

export function transformFromFabricObject(layer, object) {
  const proxyWidth = Math.max(1, finiteNumber(object.width, 1));
  const proxyHeight = Math.max(1, finiteNumber(object.height, 1));
  return {
    x: rounded(object.left),
    y: rounded(object.top),
    scale_x: Math.max(0.0001, rounded(object.scaleX * proxyWidth / layer.source.original_pixel_width, 10000)),
    scale_y: Math.max(0.0001, rounded(object.scaleY * proxyHeight / layer.source.original_pixel_height, 10000)),
    rotation_degrees: Math.max(-360, Math.min(360, rounded(object.angle))),
    opacity: Math.max(0, Math.min(1, rounded(object.opacity, 1000))),
  };
}

export function segmentedItems(items, visibleLimit, pageSize = CANVAS_PAGE_SIZE) {
  const safeItems = Array.from(items || []);
  const limit = Math.max(pageSize, Math.floor(finiteNumber(visibleLimit, pageSize) / pageSize) * pageSize);
  return {
    items: safeItems.slice(0, limit),
    visible: Math.min(limit, safeItems.length),
    total: safeItems.length,
    hasMore: safeItems.length > limit,
    nextLimit: limit + pageSize,
  };
}

export function canvasDocumentClone(document) {
  return clone(document);
}
