import {
  Download,
  Eye,
  EyeOff,
  Focus,
  Hand,
  Layers3,
  Lock,
  LockOpen,
  MousePointer2,
  PackagePlus,
  Redo2,
  RefreshCw,
  RotateCcw,
  Undo2,
  ZoomIn,
  ZoomOut,
  createIcons,
} from 'lucide';

import {
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
} from './canvas-model.js';

const CANVAS_COMMAND_CONTRACT = 'canvas-command-v1';
const REQUIRED_MUTATION_COMMANDS = new Set([
  'command:transform-layer',
  'command:toggle-layer',
  'command:toggle-layer-lock',
]);
const EMPTY_ARTBOARD = Object.freeze({
  id: 'artboard:main',
  name: '主画板',
  rect: { x: 0, y: 0, width: 1600, height: 1200 },
  export: { pixel_width: 1600, pixel_height: 1200, color_space: 'srgb' },
});
const ICONS = {
  Download,
  Eye,
  EyeOff,
  Focus,
  Hand,
  Layers3,
  Lock,
  LockOpen,
  MousePointer2,
  PackagePlus,
  Redo2,
  RefreshCw,
  RotateCcw,
  Undo2,
  ZoomIn,
  ZoomOut,
};
let fabricRuntimePromise = null;

function loadFabricRuntime() {
  if (!fabricRuntimePromise) {
    fabricRuntimePromise = import('fabric').then(({ Canvas, FabricImage, Point, Rect }) => ({
      Canvas,
      FabricImage,
      Point,
      Rect,
    }));
  }
  return fabricRuntimePromise;
}

function blankEntry() {
  return {
    hydrated: false,
    loading: false,
    document: null,
    currentRevision: 0,
    currentVersionId: null,
    proxies: new Map(),
    dirty: false,
    saving: false,
    exporting: false,
    blocked: false,
    pendingSave: null,
    saveTimer: null,
  };
}

function createRequestId() {
  if (globalThis.crypto?.randomUUID) return `canvas-save:${globalThis.crypto.randomUUID().toLowerCase()}`;
  return `canvas-save:${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

function sameTransform(left, right) {
  return ['x', 'y', 'scale_x', 'scale_y', 'rotation_degrees', 'opacity']
    .every((key) => Math.abs(Number(left?.[key]) - Number(right?.[key])) < 0.0001);
}

function eventTargetIsEditable(event) {
  return Boolean(event.target?.closest?.('input, textarea, select, [contenteditable="true"]'));
}

function blobToBase64(blob) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onerror = () => reject(reader.error || new Error('读取导出图片失败'));
    reader.onload = () => resolve(String(reader.result || '').split(',', 2)[1] || '');
    reader.readAsDataURL(blob);
  });
}

export function createCanvasController({
  api,
  state,
  query,
  queryAll,
  escapeHtml,
  assetUrl,
  toast,
  formatApiError,
  onViewChange = () => {},
} = {}) {
  const entries = new Map();
  const objectByLayer = new Map();
  let currentMode = 'single';
  let currentView = 'quick';
  let activePanel = 'layers';
  let activeTool = 'select';
  let canvas = null;
  let fabricRuntime = null;
  let resizeObserver = null;
  let selectedLayerId = '';
  let layerVisibleLimit = CANVAS_PAGE_SIZE;
  let assetVisibleLimit = CANVAS_PAGE_SIZE;
  let buildToken = 0;
  let isPanning = false;
  let lastPanPoint = null;
  let toolBeforeSpace = null;
  let bound = false;

  function entryFor(mode = currentMode) {
    if (!entries.has(mode)) entries.set(mode, blankEntry());
    return entries.get(mode);
  }

  function assets() {
    return Array.from(state.assets || []);
  }

  function assetById(assetId) {
    return assets().find((asset) => String(asset.id) === String(assetId)) || null;
  }

  function activeDocument() {
    return entryFor().document;
  }

  function activeArtboard() {
    const document = activeDocument();
    return document?.artboards?.find((item) => item.id === document.active_artboard_id) || EMPTY_ARTBOARD;
  }

  function activeLayer() {
    return activeDocument()?.layers?.find((layer) => layer.id === selectedLayerId) || null;
  }

  function layerName(layer) {
    const asset = assetById(layer?.source?.id);
    return asset?.name || `素材 ${String(layer?.source?.id || '').slice(-8)}`;
  }

  function refreshIcons(root = document) {
    createIcons({ icons: ICONS, nameAttr: 'data-lucide', root });
  }

  function setSaveState(kind, title, detail = '') {
    const host = query('#canvas-save-state');
    if (!host) return;
    host.dataset.kind = kind;
    query('#canvas-save-title').textContent = title;
    query('#canvas-save-detail').textContent = detail;
    query('#canvas-save-retry').hidden = kind !== 'error';
    query('#canvas-save-reload').hidden = kind !== 'conflict';
  }

  function setInteractionDisabled(disabled) {
    const entry = entryFor();
    queryAll('[data-canvas-edit-action]').forEach((button) => {
      button.disabled = disabled || button.dataset.historyUnavailable === 'true';
    });
    queryAll('#canvas-transform-controls input, #canvas-apply-transform').forEach((control) => {
      control.disabled = disabled || !activeLayer() || activeLayer()?.locked;
    });
    if (canvas) {
      canvas.selection = !disabled && activeTool === 'select';
      canvas.skipTargetFind = disabled || activeTool === 'pan';
      canvas.forEachObject((object) => {
        if (object.get('objectRole') === 'artboard') return;
        const layer = entry.document?.layers?.find((item) => item.id === object.get('layerId'));
        object.set({ selectable: !disabled && !layer?.locked, evented: !disabled && !layer?.locked });
      });
      canvas.requestRenderAll();
    }
    updateExportControl();
  }

  function updateExportControl() {
    const button = query('#canvas-export');
    if (!button) return;
    const entry = entryFor();
    button.disabled = !entry.document || entry.saving || entry.exporting || entry.blocked;
    button.setAttribute('aria-busy', String(entry.exporting));
    button.title = entry.exporting ? '正在导出当前画板' : '导出当前画板 PNG';
  }

  function applyCanvasResponse(mode, response, { rebuildCanvas = true } = {}) {
    const entry = entryFor(mode);
    entry.hydrated = true;
    entry.loading = false;
    entry.document = response?.document ? canvasDocumentClone(response.document) : null;
    entry.currentRevision = Number(response?.current_revision || entry.document?.revision || 0);
    entry.currentVersionId = response?.current_version_id || null;
    entry.proxies = new Map((response?.proxies || []).map((proxy) => [String(proxy.layer_id), proxy]));
    entry.dirty = false;
    entry.blocked = false;
    entry.pendingSave = null;
    if (mode === currentMode) {
      selectedLayerId = entry.document?.layers?.some((layer) => layer.id === selectedLayerId)
        ? selectedLayerId
        : '';
      updateDocumentMeta();
      updateExportControl();
      updateHistoryControls();
      renderLists();
      if (currentView === 'canvas') {
        if (rebuildCanvas) renderCanvas();
        else entry.document?.layers?.forEach((layer) => syncObjectFromLayer(layer.id));
      }
    }
  }

  async function ensureCommandContract() {
    const response = await api.getCommands({ timeoutMs: 10000 });
    const commands = Array.from(response?.commands || []);
    const available = new Set(commands.map((command) => command.id));
    if (
      response?.contract_version !== CANVAS_COMMAND_CONTRACT
      || [...REQUIRED_MUTATION_COMMANDS].some((id) => !available.has(id))
    ) throw new Error('画布命令合同与当前界面不一致');
  }

  async function ensureHydrated(mode = currentMode) {
    const entry = entryFor(mode);
    if (entry.hydrated || entry.loading) return;
    entry.loading = true;
    if (mode === currentMode) {
      setSaveState('loading', '正在恢复画布', '从本地 SQLite 读取当前模式的最新版本');
      renderCanvasLoading(true);
    }
    try {
      await ensureCommandContract();
      const response = await api.getCanvas(mode, { timeoutMs: 12000 });
      applyCanvasResponse(mode, response);
      if (mode === currentMode) setSaveState('saved', '画布已同步', entry.document ? `revision ${entry.currentRevision}` : '添加素材后创建第一版');
    } catch (error) {
      entry.loading = false;
      entry.blocked = true;
      if (mode === currentMode) {
        renderCanvasLoading(false);
        setSaveState('error', '画布读取失败', formatApiError(error, '本地画布接口暂不可用'));
        setInteractionDisabled(true);
      }
    }
  }

  function hydrate(mode, response) {
    if (!mode || response === undefined) return;
    applyCanvasResponse(mode, response || {});
    if (mode === currentMode) setSaveState('saved', '画布已同步', response?.document ? `revision ${response.current_revision}` : '添加素材后创建第一版');
  }

  function documentForSave(entry) {
    const document = canvasDocumentClone(entry.document);
    document.revision = entry.currentRevision;
    return document;
  }

  async function saveMode(mode = currentMode, retry = false) {
    const entry = entryFor(mode);
    if (!entry.document || entry.saving) return false;
    if (!entry.dirty && !retry) return true;
    if (entry.saveTimer) window.clearTimeout(entry.saveTimer);
    entry.saveTimer = null;
    const pending = retry && entry.pendingSave
      ? entry.pendingSave
      : {
        expected_revision: entry.currentRevision,
        client_request_id: createRequestId(),
        document: documentForSave(entry),
      };
    entry.pendingSave = pending;
    entry.saving = true;
    entry.blocked = false;
    if (mode === currentMode) {
      setSaveState('saving', '正在保存画布', `即将生成 revision ${pending.expected_revision + 1}`);
      setInteractionDisabled(true);
    }
    try {
      const response = await api.saveCanvas(mode, pending, { timeoutMs: 15000 });
      applyCanvasResponse(mode, response, { rebuildCanvas: false });
      if (mode === currentMode) {
        setSaveState('saved', '画布已保存', `revision ${response.current_revision}`);
        setInteractionDisabled(false);
      }
      return true;
    } catch (error) {
      entry.dirty = true;
      entry.blocked = true;
      if (mode === currentMode) {
        const conflict = error?.detail?.code === 'CANVAS_REVISION_CONFLICT';
        setSaveState(
          conflict ? 'conflict' : 'error',
          conflict ? '检测到更新冲突' : '画布尚未保存',
          conflict
            ? '另一版本已先写入；重新同步后再继续，避免覆盖历史。'
            : formatApiError(error, '保留了本次修改，可直接重试'),
        );
        setInteractionDisabled(true);
        if (!conflict) toast('画布保存失败，本次修改仍保留在当前界面', 'error', 5200);
      }
      return false;
    } finally {
      entry.saving = false;
      if (mode === currentMode) updateExportControl();
    }
  }

  async function exportArtboard() {
    const entry = entryFor();
    if (!entry.document || entry.saving || entry.exporting || entry.blocked) return;
    if (entry.dirty && !await saveMode(currentMode)) return;
    const artboard = activeArtboard();
    entry.exporting = true;
    updateExportControl();
    setSaveState(
      'exporting',
      '正在导出画板',
      `${artboard.export.pixel_width} × ${artboard.export.pixel_height} px · 使用原始素材`,
    );
    try {
      const exported = await api.exportCanvas(currentMode, {
        expected_revision: entry.currentRevision,
        artboard_id: artboard.id,
        format: 'png',
      }, { timeoutMs: 120000 });
      if (exported.source !== 'original-assets') throw new Error('导出来源合同不一致');
      const dataB64 = await blobToBase64(exported.blob);
      await api.saveImage(exported.filename, dataB64);
      setSaveState(
        'saved',
        '画板已导出',
        `${exported.pixelWidth} × ${exported.pixelHeight} px · ${exported.renderedLayerCount} 个可见图层`,
      );
      toast('画板 PNG 已按原始像素导出', 'success');
    } catch (error) {
      if (String(error?.message || '').includes('保存已取消')) {
        setSaveState('saved', '已取消导出', `画布仍保持 revision ${entry.currentRevision}`);
      } else if (error?.detail?.code === 'CANVAS_REVISION_CONFLICT') {
        entry.blocked = true;
        setSaveState('conflict', '导出前检测到更新冲突', '重新同步画布后再导出，避免下载错误版本。');
      } else {
        setSaveState('export-error', '画板导出失败', formatApiError(error, '原始素材可能暂不可用'));
        toast('画板导出失败，请检查素材后重试', 'error', 5200);
      }
    } finally {
      entry.exporting = false;
      updateExportControl();
    }
  }

  function scheduleSave(delay = 260) {
    const entry = entryFor();
    if (!entry.document || entry.blocked) return;
    entry.dirty = true;
    if (entry.saveTimer) window.clearTimeout(entry.saveTimer);
    entry.saveTimer = window.setTimeout(() => saveMode(currentMode), delay);
    setSaveState('dirty', '有未保存修改', '短暂停顿后自动写入本地版本账本');
  }

  async function reloadCurrent() {
    const entry = entryFor();
    if (entry.saveTimer) window.clearTimeout(entry.saveTimer);
    entry.saveTimer = null;
    entry.hydrated = false;
    entry.loading = false;
    entry.blocked = false;
    entry.pendingSave = null;
    entry.dirty = false;
    setInteractionDisabled(true);
    await ensureHydrated(currentMode);
  }

  function renderCanvasLoading(loading) {
    const overlay = query('#canvas-stage-loading');
    if (overlay) overlay.hidden = !loading;
  }

  function updateDocumentMeta() {
    const entry = entryFor();
    const document = entry.document;
    const artboard = activeArtboard();
    query('#canvas-mode-label').textContent = document ? `${currentMode} · revision ${entry.currentRevision}` : `${currentMode} · 尚未创建`;
    query('#canvas-layer-count').textContent = `${document?.layers?.length || 0} 图层`;
    query('#canvas-artboard-spec').textContent = `${artboard.export.pixel_width} × ${artboard.export.pixel_height} px`;
    const empty = query('#canvas-empty-state');
    if (empty) empty.hidden = Boolean(document?.layers?.length);
  }

  function updateHistoryControls() {
    const document = activeDocument();
    const undo = query('#canvas-undo');
    const redo = query('#canvas-redo');
    const canUndo = Boolean(document && document.undo_cursor >= 0);
    const canRedo = Boolean(document && document.undo_cursor + 1 < document.operations.length);
    undo.disabled = !canUndo || entryFor().saving || entryFor().blocked;
    redo.disabled = !canRedo || entryFor().saving || entryFor().blocked;
    undo.dataset.historyUnavailable = String(!canUndo);
    redo.dataset.historyUnavailable = String(!canRedo);
  }

  function updateSelectionPanel() {
    const layer = activeLayer();
    const title = query('#canvas-selection-title');
    const detail = query('#canvas-selection-detail');
    const controls = query('#canvas-transform-controls');
    title.textContent = layer ? layerName(layer) : '未选择图层';
    detail.textContent = layer
      ? `${layer.source.original_pixel_width} × ${layer.source.original_pixel_height} px · ${layer.locked ? '已锁定' : '可编辑'}`
      : '从画板或图层列表选择素材';
    controls.hidden = !layer;
    if (layer) {
      query('#canvas-transform-x').value = String(Math.round(layer.transform.x));
      query('#canvas-transform-y').value = String(Math.round(layer.transform.y));
      query('#canvas-transform-scale').value = String(Math.round(layer.transform.scale_x * 100));
      query('#canvas-transform-rotation').value = String(Math.round(layer.transform.rotation_degrees));
    }
    setInteractionDisabled(entryFor().saving || entryFor().blocked);
  }

  function layerRowMarkup(layer) {
    const asset = assetById(layer.source.id);
    const thumbnail = assetUrl(asset, 'thumbnail');
    const selected = layer.id === selectedLayerId;
    return `<article class="canvas-layer-row${selected ? ' is-selected' : ''}${layer.visible ? '' : ' is-hidden'}${layer.locked ? ' is-locked' : ''}" data-canvas-layer-id="${escapeHtml(layer.id)}" role="option" aria-selected="${selected}">
      <button class="canvas-layer-select" type="button" data-canvas-layer-action="select" aria-label="选择 ${escapeHtml(layerName(layer))}">
        <span class="canvas-layer-thumb">${thumbnail ? `<img src="${escapeHtml(thumbnail)}" alt="" />` : '<i data-lucide="layers-3" aria-hidden="true"></i>'}</span>
        <span><strong>${escapeHtml(layerName(layer))}</strong><small>${escapeHtml(layer.source.proxy_ref)}</small></span>
      </button>
      <button class="canvas-layer-action" type="button" data-canvas-layer-action="visibility" data-canvas-edit-action aria-label="${layer.visible ? '隐藏' : '显示'} ${escapeHtml(layerName(layer))}" title="${layer.visible ? '隐藏图层' : '显示图层'}"><i data-lucide="${layer.visible ? 'eye' : 'eye-off'}" aria-hidden="true"></i></button>
      <button class="canvas-layer-action" type="button" data-canvas-layer-action="lock" data-canvas-edit-action aria-label="${layer.locked ? '解锁' : '锁定'} ${escapeHtml(layerName(layer))}" title="${layer.locked ? '解锁图层' : '锁定图层'}"><i data-lucide="${layer.locked ? 'lock' : 'lock-open'}" aria-hidden="true"></i></button>
    </article>`;
  }

  function renderLayerList() {
    const host = query('#canvas-layer-list');
    const layers = [...(activeDocument()?.layers || [])].sort((a, b) => b.z_index - a.z_index);
    const segment = segmentedItems(layers, layerVisibleLimit);
    host.innerHTML = segment.items.length
      ? segment.items.map(layerRowMarkup).join('')
      : '<div class="canvas-panel-empty"><i data-lucide="layers-3" aria-hidden="true"></i><strong>画布还没有图层</strong><p>切换到“素材”并添加一张图片。</p></div>';
    query('#canvas-layer-segment-status').textContent = `已显示 ${segment.visible} / ${segment.total}`;
    query('#canvas-layer-more').hidden = !segment.hasMore;
    refreshIcons(host);
  }

  function assetRowMarkup(asset, document) {
    const added = Boolean(document?.layers?.some((layer) => layer.source.kind === 'asset' && layer.source.id === asset.id));
    const thumbnail = assetUrl(asset, 'thumbnail');
    const dimensions = asset.width && asset.height ? `${asset.width} × ${asset.height}` : '像素信息待补充';
    return `<article class="canvas-asset-row">
      <span class="canvas-asset-thumb">${thumbnail ? `<img src="${escapeHtml(thumbnail)}" alt="" />` : '<i data-lucide="package-plus" aria-hidden="true"></i>'}</span>
      <span><strong>${escapeHtml(asset.name || '未命名素材')}</strong><small>${escapeHtml(dimensions)}</small></span>
      <button type="button" data-canvas-add-asset="${escapeHtml(asset.id)}" ${added ? 'disabled' : ''}>${added ? '已添加' : '添加'}</button>
    </article>`;
  }

  function renderAssetList() {
    const host = query('#canvas-asset-list');
    const segment = segmentedItems(assets(), assetVisibleLimit);
    host.innerHTML = segment.items.length
      ? segment.items.map((asset) => assetRowMarkup(asset, activeDocument())).join('')
      : '<div class="canvas-panel-empty"><i data-lucide="package-plus" aria-hidden="true"></i><strong>当前素材域为空</strong><p>返回快捷处理导入图片后即可添加。</p></div>';
    query('#canvas-asset-segment-status').textContent = `已显示 ${segment.visible} / ${segment.total}`;
    query('#canvas-asset-more').hidden = !segment.hasMore;
    refreshIcons(host);
  }

  function renderLists() {
    renderLayerList();
    renderAssetList();
    updateSelectionPanel();
  }

  function syncObjectFromLayer(layerId) {
    const layer = activeDocument()?.layers?.find((item) => item.id === layerId);
    const object = objectByLayer.get(layerId);
    if (!layer || !object || !canvas) return;
    const scale = layerObjectScale(layer, object.width, object.height);
    object.set({
      left: layer.transform.x,
      top: layer.transform.y,
      scaleX: scale.scaleX,
      scaleY: scale.scaleY,
      angle: layer.transform.rotation_degrees,
      opacity: layer.transform.opacity,
      visible: layer.visible,
      selectable: !layer.locked && !entryFor().saving && !entryFor().blocked,
      evented: !layer.locked && !entryFor().saving && !entryFor().blocked,
    });
    object.setCoords();
    canvas.moveObjectTo(object, layer.z_index + 1);
    if (layer.locked && canvas.getActiveObject() === object) canvas.discardActiveObject();
    canvas.requestRenderAll();
  }

  function setSelectedLayer(layerId, focusRow = false) {
    const layer = activeDocument()?.layers?.find((item) => item.id === layerId);
    if (!layer) return;
    selectedLayerId = layerId;
    const object = objectByLayer.get(layerId);
    if (object && !layer.locked) {
      canvas.setActiveObject(object);
      canvas.requestRenderAll();
    } else canvas?.discardActiveObject();
    renderLayerList();
    updateSelectionPanel();
    if (focusRow) {
      queryAll('[data-canvas-layer-id]')
        .find((row) => row.dataset.canvasLayerId === layerId)
        ?.scrollIntoView({ block: 'nearest' });
    }
  }

  function setTool(tool) {
    activeTool = tool === 'pan' ? 'pan' : 'select';
    queryAll('[data-canvas-tool]').forEach((button) => {
      const active = button.dataset.canvasTool === activeTool;
      button.classList.toggle('is-active', active);
      button.setAttribute('aria-pressed', String(active));
    });
    if (!canvas) return;
    const disabled = entryFor().saving || entryFor().blocked;
    canvas.selection = !disabled && activeTool === 'select';
    canvas.skipTargetFind = disabled || activeTool === 'pan';
    canvas.defaultCursor = activeTool === 'pan' ? 'grab' : 'default';
    canvas.requestRenderAll();
  }

  function setCanvasDimensions() {
    if (!canvas) return;
    const host = query('#canvas-stage');
    const rect = host.getBoundingClientRect();
    if (rect.width < 1 || rect.height < 1) return;
    canvas.setDimensions({ width: Math.floor(rect.width), height: Math.floor(rect.height) });
  }

  function updateZoomOutput() {
    const zoom = canvas?.getZoom() || 1;
    query('#canvas-zoom-output').textContent = `${Math.round(zoom * 100)}%`;
  }

  function fitArtboard() {
    if (!canvas) return;
    const artboard = activeArtboard();
    const width = canvas.getWidth();
    const height = canvas.getHeight();
    const padding = 72;
    const zoom = Math.max(0.04, Math.min(
      (width - padding) / artboard.rect.width,
      (height - padding) / artboard.rect.height,
    ));
    canvas.setViewportTransform([
      zoom,
      0,
      0,
      zoom,
      (width - artboard.rect.width * zoom) / 2,
      (height - artboard.rect.height * zoom) / 2,
    ]);
    canvas.requestRenderAll();
    updateZoomOutput();
  }

  function setZoom(nextZoom) {
    if (!canvas) return;
    const zoom = Math.max(0.04, Math.min(4, nextZoom));
    canvas.zoomToPoint(new fabricRuntime.Point(canvas.getWidth() / 2, canvas.getHeight() / 2), zoom);
    canvas.requestRenderAll();
    updateZoomOutput();
  }

  async function fabricObjectForLayer(layer) {
    const { FabricImage, Rect } = fabricRuntime;
    const proxy = entryFor().proxies.get(layer.id);
    const proxySize = Math.max(64, Number(proxy?.max_edge || 512));
    const url = await api.getAssetThumbnailUrl(layer.source.id, proxySize);
    try {
      const image = await FabricImage.fromURL(url, { crossOrigin: 'anonymous' });
      const scale = layerObjectScale(layer, image.width, image.height);
      image.set({
        left: layer.transform.x,
        top: layer.transform.y,
        originX: 'left',
        originY: 'top',
        scaleX: scale.scaleX,
        scaleY: scale.scaleY,
        angle: layer.transform.rotation_degrees,
        opacity: layer.transform.opacity,
        visible: layer.visible,
        selectable: !layer.locked,
        evented: !layer.locked,
        borderColor: '#f16745',
        cornerColor: '#f16745',
        cornerStrokeColor: '#ffffff',
        cornerStyle: 'circle',
        cornerSize: 14,
        touchCornerSize: 24,
        transparentCorners: false,
        padding: 4,
      });
      image.set('layerId', layer.id);
      return image;
    } catch (_) {
      const placeholder = new Rect({
        left: layer.transform.x,
        top: layer.transform.y,
        width: layer.source.original_pixel_width,
        height: layer.source.original_pixel_height,
        originX: 'left',
        originY: 'top',
        scaleX: layer.transform.scale_x,
        scaleY: layer.transform.scale_y,
        angle: layer.transform.rotation_degrees,
        opacity: layer.transform.opacity,
        visible: layer.visible,
        fill: '#ece7de',
        stroke: '#bdb4a7',
        strokeWidth: 2,
        strokeUniform: true,
        selectable: !layer.locked,
        evented: !layer.locked,
      });
      placeholder.set('layerId', layer.id);
      return placeholder;
    }
  }

  async function renderCanvas() {
    if (!canvas || currentView !== 'canvas') return;
    const { Rect } = fabricRuntime;
    const token = ++buildToken;
    renderCanvasLoading(true);
    setCanvasDimensions();
    canvas.discardActiveObject();
    canvas.clear();
    objectByLayer.clear();
    const artboard = activeArtboard();
    const artboardObject = new Rect({
      left: artboard.rect.x,
      top: artboard.rect.y,
      width: artboard.rect.width,
      height: artboard.rect.height,
      originX: 'left',
      originY: 'top',
      fill: '#fffefd',
      stroke: '#c8c0b4',
      strokeWidth: 2,
      selectable: false,
      evented: false,
      objectCaching: false,
    });
    artboardObject.set('objectRole', 'artboard');
    canvas.add(artboardObject);
    const layers = [...(activeDocument()?.layers || [])].sort((a, b) => a.z_index - b.z_index);
    for (let offset = 0; offset < layers.length; offset += 12) {
      const batch = layers.slice(offset, offset + 12);
      const objects = await Promise.all(batch.map(fabricObjectForLayer));
      if (token !== buildToken || currentView !== 'canvas') return;
      objects.forEach((object, index) => {
        const layer = batch[index];
        objectByLayer.set(layer.id, object);
        canvas.add(object);
      });
    }
    if (token !== buildToken) return;
    canvas.requestRenderAll();
    fitArtboard();
    if (selectedLayerId) setSelectedLayer(selectedLayerId);
    renderCanvasLoading(false);
    updateDocumentMeta();
    setInteractionDisabled(entryFor().saving || entryFor().blocked);
  }

  async function ensureCanvas() {
    if (canvas) return;
    fabricRuntime = await loadFabricRuntime();
    const { Canvas, Point } = fabricRuntime;
    const element = query('#studio-fabric-canvas');
    canvas = new Canvas(element, {
      backgroundColor: '#d7d3cb',
      preserveObjectStacking: true,
      selection: true,
      stopContextMenu: true,
    });
    canvas.on('selection:created', ({ selected }) => {
      const id = selected?.[0]?.get('layerId');
      if (id) setSelectedLayer(id);
    });
    canvas.on('selection:updated', ({ selected }) => {
      const id = selected?.[0]?.get('layerId');
      if (id) setSelectedLayer(id);
    });
    canvas.on('selection:cleared', () => {
      selectedLayerId = '';
      renderLayerList();
      updateSelectionPanel();
    });
    canvas.on('object:modified', ({ target }) => {
      const layerId = target?.get('layerId');
      const layer = activeDocument()?.layers?.find((item) => item.id === layerId);
      if (!layer || layer.locked || entryFor().blocked) return;
      const transform = transformFromFabricObject(layer, target);
      if (sameTransform(layer.transform, transform)) return;
      appendLayerMutation(activeDocument(), layerId, { transform }, 'command:transform-layer');
      syncObjectFromLayer(layerId);
      renderLists();
      updateHistoryControls();
      scheduleSave();
    });
    canvas.on('mouse:down', ({ e }) => {
      if (activeTool !== 'pan' && !toolBeforeSpace) return;
      isPanning = true;
      lastPanPoint = { x: e.clientX, y: e.clientY };
      canvas.defaultCursor = 'grabbing';
    });
    canvas.on('mouse:move', ({ e }) => {
      if (!isPanning || !lastPanPoint) return;
      const viewport = canvas.viewportTransform;
      viewport[4] += e.clientX - lastPanPoint.x;
      viewport[5] += e.clientY - lastPanPoint.y;
      lastPanPoint = { x: e.clientX, y: e.clientY };
      canvas.requestRenderAll();
    });
    canvas.on('mouse:up', () => {
      isPanning = false;
      lastPanPoint = null;
      canvas.defaultCursor = activeTool === 'pan' ? 'grab' : 'default';
    });
    canvas.on('mouse:wheel', ({ e }) => {
      const zoom = Math.max(0.04, Math.min(4, canvas.getZoom() * (0.999 ** e.deltaY)));
      canvas.zoomToPoint(new Point(e.offsetX, e.offsetY), zoom);
      e.preventDefault();
      e.stopPropagation();
      updateZoomOutput();
    });
    resizeObserver = new ResizeObserver(() => {
      setCanvasDimensions();
      if (currentView === 'canvas') fitArtboard();
    });
    resizeObserver.observe(query('#canvas-stage'));
    setTool('select');
  }

  async function addAsset(assetId) {
    const entry = entryFor();
    if (entry.saving || entry.blocked) return;
    const asset = assetById(assetId);
    if (!asset) return;
    try {
      if (!entry.document) entry.document = createCanvasDocument(currentMode, asset);
      else addAssetLayer(entry.document, asset);
      entry.dirty = true;
      selectedLayerId = `layer:${asset.id}`;
      updateDocumentMeta();
      updateExportControl();
      renderLists();
      await renderCanvas();
      scheduleSave(0);
    } catch (error) {
      toast(formatApiError(error, '素材无法加入画布'), 'error', 5200);
    }
  }

  function mutateLayer(layerId, patch, commandId) {
    const entry = entryFor();
    if (!entry.document || entry.saving || entry.blocked) return;
    appendLayerMutation(entry.document, layerId, patch, commandId);
    syncObjectFromLayer(layerId);
    renderLists();
    updateHistoryControls();
    scheduleSave();
  }

  function applyTransform() {
    const layer = activeLayer();
    if (!layer || layer.locked) return;
    const x = Number(query('#canvas-transform-x').value);
    const y = Number(query('#canvas-transform-y').value);
    const scale = Math.max(1, Number(query('#canvas-transform-scale').value)) / 100;
    const rotation = Math.max(-360, Math.min(360, Number(query('#canvas-transform-rotation').value)));
    if (![x, y, scale, rotation].every(Number.isFinite)) {
      toast('请输入有效的坐标、缩放和旋转数值', 'error');
      return;
    }
    mutateLayer(layer.id, {
      transform: {
        ...layer.transform,
        x,
        y,
        scale_x: scale,
        scale_y: scale,
        rotation_degrees: rotation,
      },
    }, 'command:transform-layer');
  }

  function undo() {
    const document = activeDocument();
    if (!document || entryFor().saving || entryFor().blocked) return;
    const operation = undoCanvas(document);
    if (!operation) return;
    syncObjectFromLayer(operation.mutation.target_layer_id);
    renderLists();
    updateHistoryControls();
    scheduleSave();
  }

  function redo() {
    const document = activeDocument();
    if (!document || entryFor().saving || entryFor().blocked) return;
    const operation = redoCanvas(document);
    if (!operation) return;
    syncObjectFromLayer(operation.mutation.target_layer_id);
    renderLists();
    updateHistoryControls();
    scheduleSave();
  }

  async function setView(view) {
    const next = view === 'canvas' ? 'canvas' : 'quick';
    if (next === currentView) return;
    currentView = next;
    queryAll('[data-studio-view]').forEach((button) => {
      const active = button.dataset.studioView === next;
      button.classList.toggle('is-active', active);
      button.setAttribute('aria-pressed', String(active));
    });
    query('#quick-workspace').hidden = next !== 'quick';
    query('#canvas-workspace').hidden = next !== 'canvas';
    onViewChange(next);
    if (next === 'canvas') {
      try {
        await ensureCanvas();
        await ensureHydrated(currentMode);
        renderLists();
        updateDocumentMeta();
        requestAnimationFrame(() => renderCanvas());
      } catch (error) {
        entryFor().blocked = true;
        renderCanvasLoading(false);
        setSaveState('error', '画布组件加载失败', formatApiError(error, '请重试进入自由画布'));
        setInteractionDisabled(true);
      }
    } else saveMode(currentMode);
  }

  function setPage(processActive) {
    const switcher = query('#studio-view-switch');
    if (switcher) switcher.hidden = !processActive;
    if (!processActive) saveMode(currentMode);
    else if (currentView === 'canvas') requestAnimationFrame(() => renderCanvas());
  }

  function setMode(mode) {
    if (!mode) return;
    const previous = currentMode;
    if (previous !== mode) saveMode(previous);
    currentMode = mode;
    selectedLayerId = '';
    layerVisibleLimit = CANVAS_PAGE_SIZE;
    assetVisibleLimit = CANVAS_PAGE_SIZE;
    updateDocumentMeta();
    updateExportControl();
    renderLists();
    if (currentView === 'canvas') {
      ensureHydrated(mode);
      renderCanvas();
    }
  }

  function syncAssets() {
    if (!bound) return;
    renderAssetList();
    renderLayerList();
  }

  function bind() {
    if (bound) return;
    bound = true;
    refreshIcons(query('#canvas-workspace'));
    queryAll('[data-studio-view]').forEach((button) => button.addEventListener('click', () => setView(button.dataset.studioView)));
    queryAll('[data-canvas-panel]').forEach((button) => button.addEventListener('click', () => {
      activePanel = button.dataset.canvasPanel;
      queryAll('[data-canvas-panel]').forEach((item) => {
        const active = item.dataset.canvasPanel === activePanel;
        item.classList.toggle('is-active', active);
        item.setAttribute('aria-selected', String(active));
      });
      query('#canvas-panel-layers').hidden = activePanel !== 'layers';
      query('#canvas-panel-assets').hidden = activePanel !== 'assets';
    }));
    queryAll('[data-canvas-tool]').forEach((button) => button.addEventListener('click', () => setTool(button.dataset.canvasTool)));
    query('#canvas-undo').addEventListener('click', undo);
    query('#canvas-redo').addEventListener('click', redo);
    query('#canvas-zoom-out').addEventListener('click', () => setZoom((canvas?.getZoom() || 1) / 1.18));
    query('#canvas-zoom-in').addEventListener('click', () => setZoom((canvas?.getZoom() || 1) * 1.18));
    query('#canvas-fit').addEventListener('click', fitArtboard);
    query('#canvas-export').addEventListener('click', exportArtboard);
    query('#canvas-apply-transform').addEventListener('click', applyTransform);
    query('#canvas-save-retry').addEventListener('click', () => saveMode(currentMode, true));
    query('#canvas-save-reload').addEventListener('click', reloadCurrent);
    query('#canvas-layer-more').addEventListener('click', () => {
      layerVisibleLimit += CANVAS_PAGE_SIZE;
      renderLayerList();
    });
    query('#canvas-asset-more').addEventListener('click', () => {
      assetVisibleLimit += CANVAS_PAGE_SIZE;
      renderAssetList();
    });
    query('#canvas-layer-list').addEventListener('click', (event) => {
      const row = event.target.closest('[data-canvas-layer-id]');
      const action = event.target.closest('[data-canvas-layer-action]')?.dataset.canvasLayerAction;
      if (!row || !action) return;
      const layer = activeDocument()?.layers?.find((item) => item.id === row.dataset.canvasLayerId);
      if (!layer) return;
      if (action === 'select') setSelectedLayer(layer.id);
      if (action === 'visibility') mutateLayer(layer.id, { visible: !layer.visible }, 'command:toggle-layer');
      if (action === 'lock') mutateLayer(layer.id, { locked: !layer.locked }, 'command:toggle-layer-lock');
    });
    query('#canvas-asset-list').addEventListener('click', (event) => {
      const button = event.target.closest('[data-canvas-add-asset]');
      if (button) addAsset(button.dataset.canvasAddAsset);
    });
    query('#canvas-stage').addEventListener('keydown', (event) => {
      if (eventTargetIsEditable(event) || currentView !== 'canvas') return;
      const layer = activeLayer();
      if (event.key === ' ' && !event.repeat) {
        event.preventDefault();
        toolBeforeSpace = activeTool;
        setTool('pan');
        return;
      }
      if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 'z') {
        event.preventDefault();
        if (event.shiftKey) redo(); else undo();
        return;
      }
      if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 'y') {
        event.preventDefault();
        redo();
        return;
      }
      if (!layer || layer.locked || !['ArrowLeft', 'ArrowRight', 'ArrowUp', 'ArrowDown'].includes(event.key)) return;
      event.preventDefault();
      const amount = event.shiftKey ? 10 : 1;
      const transform = { ...layer.transform };
      if (event.key === 'ArrowLeft') transform.x -= amount;
      if (event.key === 'ArrowRight') transform.x += amount;
      if (event.key === 'ArrowUp') transform.y -= amount;
      if (event.key === 'ArrowDown') transform.y += amount;
      mutateLayer(layer.id, { transform }, 'command:transform-layer');
    });
    query('#canvas-stage').addEventListener('keyup', (event) => {
      if (event.key === ' ' && toolBeforeSpace) {
        const restore = toolBeforeSpace;
        toolBeforeSpace = null;
        setTool(restore);
      }
    });
    document.addEventListener('visibilitychange', () => {
      if (document.visibilityState === 'hidden') saveMode(currentMode);
    });
    setPage(true);
    setMode(state.currentMode || 'single');
    updateHistoryControls();
    updateSelectionPanel();
  }

  return {
    bind,
    hydrate,
    setMode,
    setPage,
    setView,
    syncAssets,
    flush: () => saveMode(currentMode),
    get view() { return currentView; },
  };
}
