import {
  Canvas,
  FabricImage,
  Point,
  Rect,
} from 'fabric';
import {
  Check,
  Eye,
  EyeOff,
  Hand,
  Lock,
  LockOpen,
  MousePointer2,
  Move,
  Redo2,
  RotateCcw,
  Scan,
  Scissors,
  Undo2,
  X,
  ZoomIn,
  ZoomOut,
  createIcons,
} from 'lucide';

import {
  ARTBOARD,
  PROXY_COUNT,
  STORAGE_KEY,
  appendLayerMutation,
  artboardPlacement,
  bundleMetrics,
  compileExistingQuickCutout,
  createSyntheticBundle,
  layerSnapshot,
  redo,
  restoreBundle,
  serializeBundle,
  undo,
} from './canvas-model.js';

const ICONS = {
  Check,
  Eye,
  EyeOff,
  Hand,
  Lock,
  LockOpen,
  MousePointer2,
  Move,
  Redo2,
  RotateCcw,
  Scan,
  Scissors,
  Undo2,
  X,
  ZoomIn,
  ZoomOut,
};

const $ = (selector) => document.querySelector(selector);
const canvasHost = $('#canvas-host');
const loading = $('#canvas-loading');
const layerList = $('#layer-list');
const transformInputs = {
  x: $('#transform-x'),
  y: $('#transform-y'),
  scaleX: $('#transform-scale-x'),
  scaleY: $('#transform-scale-y'),
  rotation: $('#transform-rotation'),
};

let bundle = loadPersistedBundle();
let canvas = null;
let activeTool = 'select';
let toolBeforeSpace = null;
let isPanning = false;
let lastPanPoint = null;
let selectedLayerId = '';
let transformBeforePointer = null;
let toastTimer = null;
let buildDurationMs = 0;
let suppressSelectionClear = false;
const objectByLayer = new Map();

function loadPersistedBundle() {
  const persisted = localStorage.getItem(STORAGE_KEY);
  if (!persisted) return createSyntheticBundle();
  try {
    const restored = restoreBundle(persisted);
    if (restored.canvas_document.layers.length !== PROXY_COUNT) throw new Error('Unexpected proxy count');
    return restored;
  } catch (error) {
    console.warn('Discarding invalid G1A canvas state', error);
    localStorage.removeItem(STORAGE_KEY);
    return createSyntheticBundle();
  }
}

function refreshIcons(root = document) {
  createIcons({ icons: ICONS, nameAttr: 'data-lucide', root });
}

function roundedRect(context, x, y, width, height, radius) {
  const r = Math.min(radius, width / 2, height / 2);
  context.beginPath();
  context.moveTo(x + r, y);
  context.lineTo(x + width - r, y);
  context.quadraticCurveTo(x + width, y, x + width, y + r);
  context.lineTo(x + width, y + height - r);
  context.quadraticCurveTo(x + width, y + height, x + width - r, y + height);
  context.lineTo(x + r, y + height);
  context.quadraticCurveTo(x, y + height, x, y + height - r);
  context.lineTo(x, y + r);
  context.quadraticCurveTo(x, y, x + r, y);
  context.closePath();
}

function createProxyCanvas(index) {
  const element = document.createElement('canvas');
  element.width = 96;
  element.height = 96;
  const context = element.getContext('2d', { alpha: true });
  const palettes = [
    ['#ff6b43', '#ffd351', '#7b2d1f'],
    ['#2e8b76', '#d5f0e4', '#155848'],
    ['#476fbe', '#dce7ff', '#263d74'],
    ['#d85378', '#f8dce6', '#7f2945'],
    ['#865eb6', '#eadff5', '#4c326d'],
    ['#d68a2e', '#f8e5c4', '#744713'],
  ];
  const [body, label, ink] = palettes[index % palettes.length];
  const variant = index % 4;

  context.clearRect(0, 0, 96, 96);
  context.fillStyle = 'rgba(23, 26, 29, 0.14)';
  context.beginPath();
  context.ellipse(48, 82, 27, 6, 0, 0, Math.PI * 2);
  context.fill();

  if (variant === 0) {
    context.fillStyle = body;
    roundedRect(context, 23, 17, 50, 62, 10);
    context.fill();
    context.fillStyle = label;
    roundedRect(context, 29, 39, 38, 23, 5);
    context.fill();
  } else if (variant === 1) {
    context.fillStyle = ink;
    roundedRect(context, 38, 11, 20, 9, 4);
    context.fill();
    context.fillStyle = body;
    roundedRect(context, 28, 18, 40, 61, 13);
    context.fill();
    context.fillStyle = label;
    roundedRect(context, 33, 43, 30, 22, 5);
    context.fill();
  } else if (variant === 2) {
    context.fillStyle = body;
    roundedRect(context, 19, 25, 58, 51, 7);
    context.fill();
    context.fillStyle = label;
    roundedRect(context, 25, 32, 46, 28, 4);
    context.fill();
    context.fillStyle = ink;
    context.fillRect(25, 66, 46, 3);
  } else {
    context.fillStyle = body;
    context.beginPath();
    context.arc(48, 47, 30, 0, Math.PI * 2);
    context.fill();
    context.fillStyle = label;
    context.beginPath();
    context.arc(48, 47, 19, 0, Math.PI * 2);
    context.fill();
  }

  context.fillStyle = ink;
  context.font = '700 10px Segoe UI';
  context.textAlign = 'center';
  context.textBaseline = 'middle';
  context.fillText(String(index + 1).padStart(3, '0'), 48, variant === 3 ? 47 : 51);
  return element;
}

function objectStateFromLayer(layer) {
  return {
    left: layer.transform.x,
    top: layer.transform.y,
    scaleX: layer.transform.scale_x,
    scaleY: layer.transform.scale_y,
    angle: layer.transform.rotation_degrees,
    opacity: layer.transform.opacity,
    visible: layer.visible,
    selectable: !layer.locked,
    evented: !layer.locked,
    lockMovementX: layer.locked,
    lockMovementY: layer.locked,
    lockRotation: layer.locked,
    lockScalingX: layer.locked,
    lockScalingY: layer.locked,
  };
}

function createFabricLayer(layer, index) {
  const image = new FabricImage(createProxyCanvas(index), {
    ...objectStateFromLayer(layer),
    originX: 'center',
    originY: 'center',
    objectCaching: true,
    noScaleCache: false,
    borderColor: '#ff6b43',
    cornerColor: '#ff6b43',
    cornerStrokeColor: '#ffffff',
    cornerStyle: 'circle',
    cornerSize: 14,
    touchCornerSize: 24,
    transparentCorners: false,
    padding: 5,
  });
  image.set('layerId', layer.id);
  return image;
}

function buildCanvasObjects({ fit = true } = {}) {
  const started = performance.now();
  canvas.discardActiveObject();
  canvas.clear();
  objectByLayer.clear();

  const artboard = new Rect({
    ...artboardPlacement(),
    fill: '#fffefd',
    stroke: '#c6bdab',
    strokeWidth: 2,
    selectable: false,
    evented: false,
    objectCaching: false,
  });
  artboard.set('objectRole', 'artboard');
  canvas.add(artboard);

  const layers = [...bundle.canvas_document.layers].sort((a, b) => a.z_index - b.z_index);
  layers.forEach((layer, index) => {
    const object = createFabricLayer(layer, index);
    objectByLayer.set(layer.id, object);
    canvas.add(object);
  });
  canvas.requestRenderAll();
  buildDurationMs = performance.now() - started;
  if (fit) fitArtboard();
  renderLayerList();
  updateSelectionPanel();
  updateHistoryControls();
  saveBundle('已保存到隔离原型');
}

function setCanvasDimensions() {
  if (!canvas) return;
  const rect = canvasHost.getBoundingClientRect();
  if (rect.width < 1 || rect.height < 1) return;
  canvas.setDimensions({ width: Math.floor(rect.width), height: Math.floor(rect.height) });
}

function fitArtboard() {
  if (!canvas) return;
  const width = canvas.getWidth();
  const height = canvas.getHeight();
  const padding = 88;
  const zoom = Math.max(0.08, Math.min(
    (width - padding) / ARTBOARD.width,
    (height - padding) / ARTBOARD.height,
  ));
  canvas.setViewportTransform([
    zoom,
    0,
    0,
    zoom,
    (width - ARTBOARD.width * zoom) / 2,
    (height - ARTBOARD.height * zoom) / 2,
  ]);
  canvas.requestRenderAll();
  updateZoomOutput();
}

function setZoom(nextZoom) {
  const zoom = Math.max(0.08, Math.min(4, nextZoom));
  canvas.zoomToPoint(new Point(canvas.getWidth() / 2, canvas.getHeight() / 2), zoom);
  canvas.requestRenderAll();
  updateZoomOutput();
}

function updateZoomOutput() {
  const zoom = canvas?.getZoom() || 1;
  $('#zoom-output').value = `${Math.round(zoom * 100)}%`;
  $('#zoom-output').textContent = `${Math.round(zoom * 100)}%`;
}

function layerById(layerId) {
  return bundle.canvas_document.layers.find((layer) => layer.id === layerId) || null;
}

function ordinalForLayer(layer) {
  const match = layer?.id?.match(/(\d+)$/);
  return match ? Number(match[1]) : 0;
}

function layerLabel(layer) {
  return `商品代理 ${String(ordinalForLayer(layer)).padStart(3, '0')}`;
}

function objectPatch(object) {
  return {
    transform: {
      x: Math.round(object.left * 100) / 100,
      y: Math.round(object.top * 100) / 100,
      scale_x: Math.max(0.01, Math.round(object.scaleX * 10000) / 10000),
      scale_y: Math.max(0.01, Math.round(object.scaleY * 10000) / 10000),
      rotation_degrees: Math.round(object.angle * 100) / 100,
      opacity: Math.max(0, Math.min(1, Math.round(object.opacity * 1000) / 1000)),
    },
  };
}

function syncObjectFromLayer(layerId) {
  const layer = layerById(layerId);
  const object = objectByLayer.get(layerId);
  if (!layer || !object) return;
  object.set(objectStateFromLayer(layer));
  object.setCoords();
  canvas.moveObjectTo(object, layer.z_index + 1);
  if (layer.locked && canvas.getActiveObject() === object) canvas.discardActiveObject();
  canvas.requestRenderAll();
}

function saveBundle(message = '已保存') {
  const metrics = bundleMetrics(bundle);
  try {
    localStorage.setItem(STORAGE_KEY, serializeBundle(bundle));
    $('#save-dot').className = 'status-dot is-saved';
    $('#save-status').textContent = message;
  } catch (error) {
    console.error('Unable to persist G1A canvas', error);
    $('#save-dot').className = 'status-dot is-error';
    $('#save-status').textContent = '保存失败';
  }
  $('#metric-build').textContent = `${buildDurationMs.toFixed(1)} ms`;
  $('#metric-serialize').textContent = `${metrics.durationMs.toFixed(1)} ms`;
  $('#metric-size').textContent = `${(metrics.bytes / 1024).toFixed(1)} KB`;
  $('#metric-refs').textContent = String(metrics.original4kReferences);
  $('#header-layer-count').textContent = `${metrics.layerCount} 图层`;
  $('#layer-total').textContent = String(metrics.layerCount);
  const heap = performance.memory?.usedJSHeapSize;
  $('#metric-memory').textContent = heap ? `${(heap / 1024 / 1024).toFixed(1)} MB` : '不可用';
}

function updateHistoryControls() {
  const document = bundle.canvas_document;
  $('#undo-button').disabled = document.undo_cursor < 0;
  $('#redo-button').disabled = document.undo_cursor + 1 >= document.operations.length;
}

function updateSelectionPanel() {
  const layer = layerById(selectedLayerId);
  const hasLayer = Boolean(layer);
  const editable = hasLayer && !layer.locked;
  $('#selection-title').textContent = hasLayer ? layerLabel(layer) : '未选择图层';
  $('#selection-index').textContent = hasLayer ? `#${String(ordinalForLayer(layer)).padStart(3, '0')}` : '--';
  $('#selection-context').textContent = hasLayer
    ? `${layer.source.id} · ${layer.source.original_pixel_width} x ${layer.source.original_pixel_height}`
    : '未选择图层';

  Object.values(transformInputs).forEach((input) => { input.disabled = !editable; });
  $('#apply-transform-button').disabled = !editable;
  if (!hasLayer) {
    Object.values(transformInputs).forEach((input) => { input.value = ''; });
    return;
  }
  transformInputs.x.value = String(Math.round(layer.transform.x));
  transformInputs.y.value = String(Math.round(layer.transform.y));
  transformInputs.scaleX.value = String(Math.round(layer.transform.scale_x * 100));
  transformInputs.scaleY.value = String(Math.round(layer.transform.scale_y * 100));
  transformInputs.rotation.value = String(Math.round(layer.transform.rotation_degrees));
}

function renderLayerList() {
  const layers = [...bundle.canvas_document.layers].sort((a, b) => b.z_index - a.z_index);
  const fragment = document.createDocumentFragment();
  layers.forEach((layer) => {
    const ordinal = ordinalForLayer(layer);
    const row = document.createElement('div');
    row.className = `layer-row${layer.visible ? '' : ' is-hidden'}${layer.locked ? ' is-locked' : ''}`;
    row.dataset.layerId = layer.id;
    row.setAttribute('role', 'option');
    row.setAttribute('aria-selected', String(layer.id === selectedLayerId));
    row.innerHTML = `
      <button class="layer-select" type="button" data-action="select" title="选择 ${layerLabel(layer)}">
        <span class="layer-thumbnail" aria-hidden="true">${String(ordinal).padStart(3, '0')}</span>
        <span class="layer-copy">
          <strong>${layerLabel(layer)}</strong>
          <small>${layer.source.proxy_ref}</small>
        </span>
      </button>
      <button class="layer-action" type="button" data-action="visibility" aria-label="${layer.visible ? '隐藏' : '显示'} ${layerLabel(layer)}" title="${layer.visible ? '隐藏图层' : '显示图层'}">
        <i data-lucide="${layer.visible ? 'eye' : 'eye-off'}" aria-hidden="true"></i>
      </button>
      <button class="layer-action" type="button" data-action="lock" aria-label="${layer.locked ? '解锁' : '锁定'} ${layerLabel(layer)}" title="${layer.locked ? '解锁图层' : '锁定图层'}">
        <i data-lucide="${layer.locked ? 'lock' : 'lock-open'}" aria-hidden="true"></i>
      </button>
    `;
    fragment.append(row);
  });
  layerList.replaceChildren(fragment);
  refreshIcons(layerList);
}

function setSelectedLayer(layerId, { scroll = false } = {}) {
  const layer = layerById(layerId);
  if (!layer) return;
  selectedLayerId = layer.id;
  const object = objectByLayer.get(layer.id);
  if (object && layer.visible && !layer.locked) {
    canvas.setActiveObject(object);
    canvas.requestRenderAll();
  } else {
    suppressSelectionClear = true;
    canvas.discardActiveObject();
    suppressSelectionClear = false;
    canvas.requestRenderAll();
  }
  renderLayerList();
  updateSelectionPanel();
  if (scroll) {
    layerList.querySelector(`[data-layer-id="${CSS.escape(layer.id)}"]`)?.scrollIntoView({ block: 'nearest' });
  }
}

function commitMutation(layerId, patch, commandId) {
  appendLayerMutation(bundle, layerId, patch, commandId);
  syncObjectFromLayer(layerId);
  updateHistoryControls();
  renderLayerList();
  updateSelectionPanel();
  saveBundle();
}

function snapshotsEqual(left, right) {
  return JSON.stringify(left) === JSON.stringify(right);
}

function applyTransformInputs() {
  const layer = layerById(selectedLayerId);
  if (!layer || layer.locked) return;
  const values = {
    x: Number(transformInputs.x.value),
    y: Number(transformInputs.y.value),
    scaleX: Number(transformInputs.scaleX.value),
    scaleY: Number(transformInputs.scaleY.value),
    rotation: Number(transformInputs.rotation.value),
  };
  if (!Object.values(values).every(Number.isFinite) || values.scaleX < 5 || values.scaleY < 5) {
    showToast('变换数值无效');
    return;
  }
  commitMutation(layer.id, {
    transform: {
      x: values.x,
      y: values.y,
      scale_x: Math.min(8, values.scaleX / 100),
      scale_y: Math.min(8, values.scaleY / 100),
      rotation_degrees: Math.max(-360, Math.min(360, values.rotation)),
    },
  }, 'command:transform-layer');
}

function performUndo() {
  const operation = undo(bundle);
  if (!operation) return;
  if (operation.mutation) syncObjectFromLayer(operation.mutation.target_layer_id);
  updateHistoryControls();
  renderLayerList();
  updateSelectionPanel();
  saveBundle('已撤销并保存');
}

function performRedo() {
  const operation = redo(bundle);
  if (!operation) return;
  if (operation.mutation) syncObjectFromLayer(operation.mutation.target_layer_id);
  updateHistoryControls();
  renderLayerList();
  updateSelectionPanel();
  saveBundle('已重做并保存');
}

function setTool(tool) {
  activeTool = tool;
  document.querySelectorAll('[data-tool]').forEach((button) => {
    const active = button.dataset.tool === tool;
    button.classList.toggle('is-active', active);
    button.setAttribute('aria-pressed', String(active));
  });
  canvas.selection = tool === 'select';
  canvas.skipTargetFind = tool === 'hand';
  canvas.defaultCursor = tool === 'hand' ? 'grab' : 'default';
  canvas.hoverCursor = tool === 'hand' ? 'grab' : 'move';
  canvas.setCursor(canvas.defaultCursor);
}

function openCommandPreview() {
  const layer = layerById(selectedLayerId);
  if (!layer) {
    showToast('请先选择一个商品图层');
    return;
  }
  const compiled = compileExistingQuickCutout(bundle, layer.id);
  $('#command-id').textContent = compiled.command_id;
  $('#command-source').textContent = layer.source.id;
  $('#command-payload').textContent = JSON.stringify(compiled.request, null, 2);
  $('#command-dialog').showModal();
  refreshIcons($('#command-dialog'));
}

function showToast(message) {
  const toast = $('#toast');
  toast.textContent = message;
  toast.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { toast.hidden = true; }, 2600);
}

function resetPrototype() {
  localStorage.removeItem(STORAGE_KEY);
  bundle = createSyntheticBundle();
  selectedLayerId = '';
  buildCanvasObjects();
  showToast('合成画布已重置');
}

function bindCanvasEvents() {
  canvas.on('selection:created', ({ selected }) => {
    const layerId = selected?.[0]?.get('layerId');
    if (layerId) setSelectedLayer(layerId, { scroll: true });
  });
  canvas.on('selection:updated', ({ selected }) => {
    const layerId = selected?.[0]?.get('layerId');
    if (layerId) setSelectedLayer(layerId, { scroll: true });
  });
  canvas.on('selection:cleared', () => {
    if (suppressSelectionClear) return;
    if (!selectedLayerId) return;
    selectedLayerId = '';
    renderLayerList();
    updateSelectionPanel();
  });
  canvas.on('mouse:down', ({ e, target }) => {
    if (activeTool === 'hand') {
      isPanning = true;
      lastPanPoint = new Point(e.clientX, e.clientY);
      canvas.setCursor('grabbing');
      return;
    }
    const layerId = target?.get('layerId');
    const layer = layerById(layerId);
    transformBeforePointer = layer ? layerSnapshot(layer) : null;
  });
  canvas.on('mouse:move', ({ e }) => {
    if (!isPanning || !lastPanPoint) return;
    const nextPoint = new Point(e.clientX, e.clientY);
    const viewport = canvas.viewportTransform;
    viewport[4] += nextPoint.x - lastPanPoint.x;
    viewport[5] += nextPoint.y - lastPanPoint.y;
    lastPanPoint = nextPoint;
    canvas.requestRenderAll();
  });
  canvas.on('mouse:up', () => {
    if (isPanning) {
      isPanning = false;
      lastPanPoint = null;
      canvas.setCursor(activeTool === 'hand' ? 'grab' : 'default');
    }
  });
  canvas.on('object:modified', ({ target }) => {
    const layerId = target?.get('layerId');
    const layer = layerById(layerId);
    if (!layer) return;
    const before = transformBeforePointer || layerSnapshot(layer);
    const patch = objectPatch(target);
    const after = {
      ...before,
      transform: { ...before.transform, ...patch.transform },
    };
    transformBeforePointer = null;
    if (snapshotsEqual(before, after)) return;
    commitMutation(layerId, patch, 'command:transform-layer');
  });
  canvas.on('mouse:wheel', ({ e }) => {
    e.preventDefault();
    e.stopPropagation();
    const zoom = Math.max(0.08, Math.min(4, canvas.getZoom() * (0.999 ** e.deltaY)));
    canvas.zoomToPoint(new Point(e.offsetX, e.offsetY), zoom);
    canvas.requestRenderAll();
    updateZoomOutput();
  });
}

function bindInterfaceEvents() {
  document.querySelectorAll('[data-tool]').forEach((button) => {
    button.addEventListener('click', () => setTool(button.dataset.tool));
  });
  $('#undo-button').addEventListener('click', performUndo);
  $('#redo-button').addEventListener('click', performRedo);
  $('#fit-button').addEventListener('click', fitArtboard);
  $('#zoom-in-button').addEventListener('click', () => setZoom(canvas.getZoom() * 1.2));
  $('#zoom-out-button').addEventListener('click', () => setZoom(canvas.getZoom() / 1.2));
  $('#apply-transform-button').addEventListener('click', applyTransformInputs);
  $('#quick-cutout-button').addEventListener('click', openCommandPreview);
  $('#reset-button').addEventListener('click', () => $('#reset-dialog').showModal());
  $('#confirm-reset-button').addEventListener('click', resetPrototype);

  document.querySelectorAll('[data-dialog-close]').forEach((button) => {
    button.addEventListener('click', () => document.getElementById(button.dataset.dialogClose)?.close());
  });

  layerList.addEventListener('click', (event) => {
    const button = event.target.closest('button[data-action]');
    const row = event.target.closest('[data-layer-id]');
    if (!button || !row) return;
    const layer = layerById(row.dataset.layerId);
    if (!layer) return;
    if (button.dataset.action === 'select') setSelectedLayer(layer.id);
    if (button.dataset.action === 'visibility') {
      commitMutation(layer.id, { visible: !layer.visible }, 'command:toggle-layer');
      setSelectedLayer(layer.id);
    }
    if (button.dataset.action === 'lock') {
      commitMutation(layer.id, { locked: !layer.locked }, 'command:toggle-layer-lock');
      setSelectedLayer(layer.id);
    }
  });

  document.addEventListener('keydown', (event) => {
    const editable = event.target instanceof HTMLInputElement || event.target instanceof HTMLTextAreaElement;
    if (editable || document.querySelector('dialog[open]')) return;
    if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 'z') {
      event.preventDefault();
      if (event.shiftKey) performRedo();
      else performUndo();
      return;
    }
    if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 'y') {
      event.preventDefault();
      performRedo();
      return;
    }
    if (event.code === 'Space' && !event.repeat) {
      event.preventDefault();
      toolBeforeSpace = activeTool;
      setTool('hand');
      return;
    }
    if (event.key === 'Escape') {
      selectedLayerId = '';
      canvas.discardActiveObject();
      canvas.requestRenderAll();
      renderLayerList();
      updateSelectionPanel();
    }
  });

  document.addEventListener('keyup', (event) => {
    if (event.code === 'Space' && toolBeforeSpace) {
      setTool(toolBeforeSpace);
      toolBeforeSpace = null;
    }
  });
}

function initialize() {
  refreshIcons();
  const rect = canvasHost.getBoundingClientRect();
  canvas = new Canvas('fabric-canvas', {
    width: Math.max(1, Math.floor(rect.width)),
    height: Math.max(1, Math.floor(rect.height)),
    selection: true,
    preserveObjectStacking: true,
    renderOnAddRemove: false,
    enableRetinaScaling: true,
  });
  bindCanvasEvents();
  bindInterfaceEvents();
  buildCanvasObjects();
  setTool('select');

  const resizeObserver = new ResizeObserver(() => {
    setCanvasDimensions();
    fitArtboard();
  });
  resizeObserver.observe(canvasHost);

  loading.hidden = true;
  $('#app').setAttribute('aria-busy', 'false');
}

requestAnimationFrame(initialize);
