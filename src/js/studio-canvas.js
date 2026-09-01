import {
  Brush,
  Download,
  Eraser,
  Eye,
  EyeOff,
  FlipHorizontal2,
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
  Scan,
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

import {
  appendMaskStroke,
  buildFreeLocalEditContract,
  buildPaidOutpaintContract,
  cloneMaskDefinition,
  compatibleLocalEditCandidates,
  createMaskDefinition,
  defaultOutpaintConfig,
  invertMaskDefinition,
  maskHasWritablePixels,
  normalizeOutpaintConfig,
  normalizeSourceRoi,
  roiFromSceneDrag,
  sceneRectFromSourceRoi,
  setMaskBase,
  setMaskFeather,
  sourcePointFromScene,
} from './local-edit-model.js';

const CANVAS_COMMAND_CONTRACT = 'canvas-command-v1';
const REQUIRED_MUTATION_COMMANDS = new Set([
  'command:transform-layer',
  'command:toggle-layer',
  'command:toggle-layer-lock',
  'command:local-edit-compose',
]);
const EMPTY_ARTBOARD = Object.freeze({
  id: 'artboard:main',
  name: '主画板',
  rect: { x: 0, y: 0, width: 1600, height: 1200 },
  export: { pixel_width: 1600, pixel_height: 1200, color_space: 'srgb' },
});
const ICONS = {
  Brush,
  Download,
  Eraser,
  Eye,
  EyeOff,
  FlipHorizontal2,
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
  Scan,
  Undo2,
  ZoomIn,
  ZoomOut,
};
let fabricRuntimePromise = null;

function loadFabricRuntime() {
  if (!fabricRuntimePromise) {
    fabricRuntimePromise = import('fabric').then(({ Canvas, Circle, FabricImage, Point, Polyline, Rect }) => ({
      Canvas,
      Circle,
      FabricImage,
      Point,
      Polyline,
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
    localEdits: new Map(),
    localEditLoadToken: 0,
  };
}

function createRequestId(prefix = 'canvas-save') {
  if (globalThis.crypto?.randomUUID) return `${prefix}:${globalThis.crypto.randomUUID().toLowerCase()}`;
  return `${prefix}:${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

function blankLocalEditState(mode = 'inpaint') {
  return {
    mode,
    hydrated: false,
    loading: false,
    saving: false,
    roi: null,
    draftRect: null,
    mask: null,
    definition: null,
    savedDefinition: null,
    dirtyMask: false,
    spec: null,
    specRequest: null,
    roiRequest: null,
    outpaint: null,
    outpaintConfirmed: false,
    recentCandidates: [],
    candidatesHydrated: false,
    candidateLoading: false,
    candidateId: '',
    composeRequest: null,
    error: '',
  };
}

function sameRect(left, right) {
  return ['x', 'y', 'width', 'height']
    .every((key) => Number(left?.[key]) === Number(right?.[key]));
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
  const assetDetails = new Map();
  let currentMode = 'single';
  let currentView = 'quick';
  let activePanel = 'layers';
  let activeTool = 'select';
  let localEditMode = 'inpaint';
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
  let localPointer = null;
  let historySyncing = false;
  let suppressSelectionCleared = false;
  let bound = false;

  function entryFor(mode = currentMode) {
    if (!entries.has(mode)) entries.set(mode, blankEntry());
    return entries.get(mode);
  }

  function assets() {
    return Array.from(state.assets || []);
  }

  function assetById(assetId) {
    const target = String(assetId || '');
    const currentResult = Object.values(state.results || {})
      .flatMap((items) => Array.from(items || []))
      .find((asset) => String(asset.id || asset.asset_id || '') === target);
    return assets().find((asset) => String(asset.id) === target)
      || currentResult
      || assetDetails.get(target)
      || null;
  }

  async function ensureLayerSourceAsset(layer) {
    const assetId = String(layer?.source?.id || '');
    const existing = assetById(assetId);
    if (!assetId || existing?.sha256 || existing?.blob?.sha256) return existing;
    const asset = await api.getAsset(assetId, { timeoutMs: 10000 });
    assetDetails.set(assetId, asset);
    return asset;
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

  function localEditKey(entry = entryFor(), layerId = selectedLayerId, mode = localEditMode) {
    if (!entry.currentVersionId || !layerId) return '';
    return `${entry.currentVersionId}|${layerId}|${mode}`;
  }

  function localEditState({ create = true } = {}) {
    const entry = entryFor();
    const key = localEditKey(entry);
    if (!key) return null;
    if (!entry.localEdits.has(key) && create) entry.localEdits.set(key, blankLocalEditState(localEditMode));
    return entry.localEdits.get(key) || null;
  }

  function setLocalEditStatus(kind, title, detail = '') {
    const host = query('#local-edit-state');
    if (!host) return;
    host.dataset.kind = kind;
    query('#local-edit-state-title').textContent = title;
    query('#local-edit-state-detail').textContent = detail;
  }

  function localEditLayerReady(layer = activeLayer()) {
    return Boolean(
      layer
      && !layer.locked
      && (
        localEditMode === 'outpaint'
        || Math.abs(Number(layer.transform.rotation_degrees || 0)) < 0.0001
      )
      && entryFor().document
      && entryFor().currentVersionId
      && !entryFor().dirty
      && !entryFor().saving
      && !entryFor().blocked
    );
  }

  function writeRoiInputs(rect) {
    const values = rect || { x: '', y: '', width: '', height: '' };
    query('#local-edit-roi-x').value = String(values.x ?? '');
    query('#local-edit-roi-y').value = String(values.y ?? '');
    query('#local-edit-roi-width').value = String(values.width ?? '');
    query('#local-edit-roi-height').value = String(values.height ?? '');
  }

  function roiFromInputs() {
    return normalizeSourceRoi(activeLayer(), {
      x: Number(query('#local-edit-roi-x').value),
      y: Number(query('#local-edit-roi-y').value),
      width: Number(query('#local-edit-roi-width').value),
      height: Number(query('#local-edit-roi-height').value),
    });
  }

  function ensureOutpaintDraft(local = localEditState(), layer = activeLayer()) {
    if (local && layer && !local.outpaint) local.outpaint = defaultOutpaintConfig(layer);
    return local?.outpaint || null;
  }

  function writeOutpaintInputs(config) {
    const values = config || {
      output_width: '',
      output_height: '',
      source_x: '',
      source_y: '',
      transition_width: 0,
    };
    query('#local-edit-outpaint-width').value = String(values.output_width ?? '');
    query('#local-edit-outpaint-height').value = String(values.output_height ?? '');
    query('#local-edit-outpaint-x').value = String(values.source_x ?? '');
    query('#local-edit-outpaint-y').value = String(values.source_y ?? '');
    query('#local-edit-outpaint-transition').value = String(values.transition_width ?? 0);
  }

  function outpaintFromInputs(layer = activeLayer()) {
    return normalizeOutpaintConfig(layer, {
      output_width: Number(query('#local-edit-outpaint-width').value),
      output_height: Number(query('#local-edit-outpaint-height').value),
      source_x: Number(query('#local-edit-outpaint-x').value),
      source_y: Number(query('#local-edit-outpaint-y').value),
      transition_width: Number(query('#local-edit-outpaint-transition').value),
    });
  }

  function localEditCandidateOptions(local = localEditState({ create: false }), layer = activeLayer()) {
    if (!local?.spec || !layer) return [];
    try {
      return compatibleLocalEditCandidates(
        state.results,
        local.recentCandidates,
        local.spec.contract,
        layer,
      );
    } catch (_) {
      return [];
    }
  }

  function updateLocalEditCandidates(local, layer, busy) {
    const select = query('#local-edit-candidate');
    const preview = query('#local-edit-candidate-preview');
    const candidates = localEditCandidateOptions(local, layer);
    const selected = candidates.some((item) => item.id === local?.candidateId)
      ? local.candidateId
      : candidates[0]?.id || '';
    if (local) local.candidateId = selected;
    select.innerHTML = candidates.length
      ? candidates.map((candidate) => (
        `<option value="${escapeHtml(candidate.id)}">${escapeHtml(candidate.name)} · ${candidate.width} × ${candidate.height}</option>`
      )).join('')
      : '<option value="">暂无兼容候选</option>';
    select.value = selected;
    select.disabled = !local?.spec || !candidates.length || busy;
    query('#local-edit-candidate-count').textContent = `${candidates.length} 个`;
    query('#local-edit-refresh-candidates').disabled = !local?.spec || busy;
    query('#local-edit-apply').disabled = !local?.spec || !selected || busy;
    const candidate = candidates.find((item) => item.id === selected) || null;
    preview.hidden = !candidate;
    if (candidate) {
      const image = query('#local-edit-candidate-image');
      const previewUrl = candidate.preview_url || candidate.thumbnail_url || '';
      if (previewUrl) image.src = previewUrl;
      else image.removeAttribute('src');
      query('#local-edit-candidate-name').textContent = candidate.name;
      query('#local-edit-candidate-detail').textContent = `${candidate.width} × ${candidate.height} px · ${candidate.role}`;
    }
  }

  function updateLocalEditPanel() {
    const entry = entryFor();
    const layer = activeLayer();
    const local = localEditState({ create: false });
    const isOutpaint = localEditMode === 'outpaint';
    const layerReady = localEditLayerReady(layer);
    const roiReady = Boolean(local?.roi && sameRect(local.roi.rect, local.draftRect));
    const maskSaved = Boolean(local?.mask?.version?.id && !local.dirtyMask);
    const writableMask = Boolean(local?.definition && maskHasWritablePixels(local.definition));
    const busy = Boolean(local?.loading || local?.saving || local?.candidateLoading);
    let outpaint = null;
    let outpaintValid = false;
    if (isOutpaint && layer && local) {
      outpaint = ensureOutpaintDraft(local, layer);
      try {
        outpaint = normalizeOutpaintConfig(layer, outpaint);
        outpaintValid = true;
      } catch (_) { /* the inline status below reports the stored validation error */ }
    }

    queryAll('[data-local-edit-mode]').forEach((button) => {
      const active = button.dataset.localEditMode === localEditMode;
      button.classList.toggle('is-active', active);
      button.setAttribute('aria-pressed', String(active));
    });
    queryAll('[data-local-edit-section]').forEach((section) => {
      section.hidden = section.dataset.localEditSection !== localEditMode;
    });

    query('#local-edit-roi-fields').disabled = isOutpaint || !layerReady || busy;
    query('#local-edit-mask-fields').disabled = isOutpaint || !layerReady || !roiReady || busy;
    query('#local-edit-outpaint-fields').disabled = !isOutpaint || !layerReady || busy;
    queryAll('[data-local-edit-requires-layer]').forEach((button) => {
      button.disabled = isOutpaint || !layerReady || busy;
    });
    queryAll('[data-local-edit-requires-roi]').forEach((button) => {
      button.disabled = isOutpaint || !layerReady || !roiReady || busy;
    });
    query('#local-edit-prepare').disabled = isOutpaint || !maskSaved || !writableMask || busy || Boolean(local?.spec);
    query('#local-edit-prepare-outpaint').disabled = !isOutpaint
      || !outpaintValid
      || !local?.outpaintConfirmed
      || busy
      || Boolean(local?.spec);
    query('#local-edit-compose-fields').disabled = !local?.spec || busy;

    if (local?.draftRect) writeRoiInputs(local.draftRect);
    else if (!layer) writeRoiInputs(null);
    if (isOutpaint) {
      writeOutpaintInputs(outpaint);
      const sourceWidth = Number(layer?.source?.original_pixel_width || 0);
      const sourceHeight = Number(layer?.source?.original_pixel_height || 0);
      const maxTransition = Math.max(0, Math.floor(Math.min(sourceWidth, sourceHeight) / 2));
      query('#local-edit-outpaint-transition').max = String(maxTransition);
      query('#local-edit-outpaint-transition-output').textContent = `${Number(outpaint?.transition_width || 0)} px`;
      query('#local-edit-outpaint-confirm').checked = Boolean(local?.outpaintConfirmed);
      query('#local-edit-outpaint-output-summary').textContent = outpaintValid
        ? `${outpaint.output_width} × ${outpaint.output_height} px · 原图位于 ${outpaint.source_x}, ${outpaint.source_y}`
        : '扩图规格需要修正';
      query('#local-edit-outpaint-write-summary').textContent = outpaintValid
        ? `允许写入新增区域与 ${outpaint.transition_width} px 过渡带；原画板保持 ${activeArtboard().export.pixel_width} × ${activeArtboard().export.pixel_height} px`
        : '原图必须完整位于输出范围内，且输出需包含新增区域';
      query('#local-edit-outpaint-cost-summary').textContent = '扩图生成预算：1 次调用 · 冻结规格不会发起调用';
    }
    const brushRadius = Number(query('#local-edit-brush-radius').value || 24);
    query('#local-edit-brush-radius-output').textContent = `${brushRadius} px`;
    const feather = Number(local?.definition?.feather_radius ?? query('#local-edit-feather').value ?? 0);
    query('#local-edit-feather').value = String(feather);
    query('#local-edit-feather-output').textContent = `${feather} px`;
    queryAll('[data-local-edit-base]').forEach((button) => {
      button.classList.toggle('is-active', button.dataset.localEditBase === local?.definition?.base);
    });
    updateLocalEditCandidates(local, layer, busy);

    if (!layer) setLocalEditStatus('idle', '请选择一个图层', isOutpaint ? '扩图规格尚未开始' : '局部编辑尚未开始');
    else if (!entry.currentVersionId) setLocalEditStatus('dirty', '请先保存画布', '处理规格必须绑定不可变画布版本');
    else if (entry.dirty) setLocalEditStatus('dirty', '正在保存画布修改', '新画布版本生成后再继续处理');
    else if (layer.locked) setLocalEditStatus('idle', '图层已锁定', '解锁后才能创建处理规格');
    else if (!isOutpaint && Math.abs(Number(layer.transform.rotation_degrees || 0)) >= 0.0001) {
      setLocalEditStatus('error', '暂不支持旋转图层', '将旋转恢复为 0° 后再框选');
    } else if (local?.loading) setLocalEditStatus('saving', '正在恢复处理规格', isOutpaint ? '读取当前版本的扩图范围' : '读取当前版本的最新选区与蒙版');
    else if (local?.candidateLoading) setLocalEditStatus('saving', '正在读取候选结果', '合并当前结果与本模式的近期任务结果');
    else if (local?.saving) setLocalEditStatus('saving', '正在写入本地账本', '操作完成前不会覆盖已有版本');
    else if (local?.error) setLocalEditStatus('error', isOutpaint ? '扩图规格未完成' : '局部编辑未完成', local.error);
    else if (isOutpaint && local?.spec) {
      const count = localEditCandidateOptions(local, layer).length;
      setLocalEditStatus('saved', '扩图规格已冻结', `1 次调用已确认 · ${count} 个兼容候选`);
    }
    else if (isOutpaint && !local?.outpaintConfirmed) setLocalEditStatus('ready', '核对扩图影响', '确认输出规格、写入范围与 1 次调用');
    else if (isOutpaint) setLocalEditStatus('saved', '扩图规格可以冻结', '冻结只写入本地账本，不会发起模型调用');
    else if (!local?.draftRect) setLocalEditStatus('idle', '框选局部区域', '选择框选工具后在图层上拖动');
    else if (!roiReady) setLocalEditStatus('dirty', '选区尚未保存', '核对原图像素坐标后保存不可变 ROI');
    else if (!local.mask) setLocalEditStatus('ready', '选区已保存', '填满选区或使用保留画笔创建蒙版');
    else if (local.dirtyMask) setLocalEditStatus('dirty', '蒙版有未保存修改', '保存后才可冻结本地编辑规格');
    else if (local.spec) {
      const count = localEditCandidateOptions(local, layer).length;
      setLocalEditStatus('saved', '本地编辑规格已冻结', `零调用 · ${count} 个兼容候选`);
    }
    else setLocalEditStatus('saved', '蒙版版本已保存', `revision ${local.mask.current_revision} · 可冻结零费用规格`);
  }

  async function ensureLocalEditCandidates(force = false) {
    const local = localEditState({ create: false });
    if (!local?.spec || local.candidateLoading || (!force && local.candidatesHydrated)) {
      updateLocalEditPanel();
      return;
    }
    local.candidateLoading = true;
    local.error = '';
    updateLocalEditPanel();
    try {
      const response = await api.getWorkspace(currentMode, { timeoutMs: 12000 });
      const candidates = Array.from(response?.recent_results || []);
      await Promise.all(candidates.map(async (candidate) => {
        try {
          candidate.preview_url = await api.getAssetThumbnailUrl(candidate.id, 160);
        } catch (_) { /* candidate remains selectable without a preview */ }
      }));
      local.recentCandidates = candidates;
      local.candidatesHydrated = true;
      const available = localEditCandidateOptions(local);
      if (!available.some((item) => item.id === local.candidateId)) {
        local.candidateId = available[0]?.id || '';
        local.composeRequest = null;
      }
    } catch (error) {
      if (!localEditCandidateOptions(local).length) {
        local.error = formatApiError(error, '无法读取本模式的近期候选结果');
      }
    } finally {
      local.candidateLoading = false;
      updateLocalEditPanel();
    }
  }

  async function ensureLocalEditHydrated(force = false) {
    const entry = entryFor();
    const layer = activeLayer();
    const mode = localEditMode;
    const key = localEditKey(entry);
    if (!layer || !key) {
      updateLocalEditPanel();
      return;
    }
    const local = localEditState();
    if ((!force && local.hydrated) || local.loading) {
      updateLocalEditPanel();
      return;
    }
    const token = ++entry.localEditLoadToken;
    local.loading = true;
    local.error = '';
    updateLocalEditPanel();
    try {
      await ensureLayerSourceAsset(layer);
      if (token !== entry.localEditLoadToken || key !== localEditKey(entry)) return;
      const response = await api.getCanvasRois(entry.currentVersionId, layer.id, { timeoutMs: 10000 });
      if (token !== entry.localEditLoadToken || key !== localEditKey(entry)) return;
      const roi = Array.from(response?.rois || [])
        .find((item) => item.purpose === mode) || null;
      local.roi = roi;
      local.draftRect = roi ? { ...roi.rect } : null;
      local.mask = null;
      local.definition = mode === 'inpaint' && roi ? createMaskDefinition(layer) : null;
      local.savedDefinition = null;
      local.dirtyMask = false;
      local.spec = null;
      if (mode === 'inpaint' && roi) {
        try {
          local.mask = await api.getCanvasMask(roi.id, { timeoutMs: 10000 });
          local.definition = cloneMaskDefinition(local.mask.version.definition);
          local.savedDefinition = cloneMaskDefinition(local.mask.version.definition);
        } catch (error) {
          if (error?.status !== 404) throw error;
        }
      }
      if (roi && (mode === 'outpaint' || local.mask?.version?.id)) {
        const latest = await api.getLatestLocalEditSpec({
          canvasVersionId: entry.currentVersionId,
          sourceLayerId: layer.id,
          roiId: roi.id,
          mode,
          maskVersionId: local.mask?.version?.id || '',
        }, { timeoutMs: 10000 });
        local.spec = latest?.spec || null;
        if (mode === 'outpaint' && local.spec?.contract?.outpaint) {
          local.outpaint = normalizeOutpaintConfig(layer, local.spec.contract.outpaint);
          local.outpaintConfirmed = Boolean(local.spec.contract.cost?.user_confirmed);
        }
      }
      if (mode === 'outpaint') ensureOutpaintDraft(local, layer);
      local.hydrated = true;
      renderLayerList();
    } catch (error) {
      local.error = formatApiError(error, mode === 'outpaint'
        ? '无法恢复当前图层的扩图规格'
        : '无法恢复当前图层的局部编辑记录');
    } finally {
      local.loading = false;
      if (token === entry.localEditLoadToken && key === localEditKey(entry)) {
        updateLocalEditPanel();
        renderLocalEditOverlay();
        if (local.spec) ensureLocalEditCandidates();
      }
    }
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
      canvas.skipTargetFind = disabled || activeTool !== 'select';
      canvas.forEachObject((object) => {
        const role = String(object.get('objectRole') || '');
        if (role === 'artboard' || role.startsWith('local-edit')) {
          object.set({ selectable: false, evented: false });
          return;
        }
        const layer = entry.document?.layers?.find((item) => item.id === object.get('layerId'));
        object.set({ selectable: !disabled && !layer?.locked, evented: !disabled && !layer?.locked });
      });
      canvas.requestRenderAll();
    }
    updateExportControl();
    updateLocalEditPanel();
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
      if (selectedLayerId) ensureLocalEditHydrated();
      if (currentView === 'canvas') {
        if (rebuildCanvas) renderCanvas();
        else {
          entry.document?.layers?.forEach((layer) => syncObjectFromLayer(layer.id));
          renderLocalEditOverlay();
        }
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
    undo.disabled = !canUndo || entryFor().saving || entryFor().blocked || historySyncing;
    redo.disabled = !canRedo || entryFor().saving || entryFor().blocked || historySyncing;
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

  function setActivePanel(panel) {
    activePanel = ['layers', 'assets', 'local-edit'].includes(panel) ? panel : 'layers';
    queryAll('[data-canvas-panel]').forEach((item) => {
      const active = item.dataset.canvasPanel === activePanel;
      item.classList.toggle('is-active', active);
      item.setAttribute('aria-selected', String(active));
    });
    query('#canvas-panel-layers').hidden = activePanel !== 'layers';
    query('#canvas-panel-assets').hidden = activePanel !== 'assets';
    query('#canvas-panel-local-edit').hidden = activePanel !== 'local-edit';
    if (activePanel === 'local-edit') {
      updateLocalEditPanel();
      ensureLocalEditHydrated();
    }
  }

  function setLocalEditMode(mode) {
    const next = mode === 'outpaint' ? 'outpaint' : 'inpaint';
    if (next === localEditMode) return;
    localEditMode = next;
    localPointer = null;
    setTool('select');
    updateLocalEditPanel();
    renderLocalEditOverlay();
    ensureLocalEditHydrated();
  }

  function syncObjectFromLayer(layerId) {
    const layer = activeDocument()?.layers?.find((item) => item.id === layerId);
    const object = objectByLayer.get(layerId);
    if (!layer || !object || !canvas) return;
    if (String(object.get('sourceAssetId') || '') !== String(layer.source.id)) {
      reloadObjectFromLayer(layerId);
      return;
    }
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

  async function reloadObjectFromLayer(layerId) {
    const layer = activeDocument()?.layers?.find((item) => item.id === layerId);
    if (!layer || !canvas) return;
    const requestedSourceId = String(layer.source.id);
    const replacement = await fabricObjectForLayer(layer);
    const currentLayer = activeDocument()?.layers?.find((item) => item.id === layerId);
    if (!currentLayer || String(currentLayer.source.id) !== requestedSourceId || !canvas) return;
    const previous = objectByLayer.get(layerId);
    const selectedBeforeReload = selectedLayerId === layerId;
    suppressSelectionCleared = true;
    try {
      if (previous) canvas.remove(previous);
      objectByLayer.set(layerId, replacement);
      canvas.add(replacement);
      canvas.moveObjectTo(replacement, currentLayer.z_index + 1);
      if (selectedBeforeReload && !currentLayer.locked) canvas.setActiveObject(replacement);
    } finally {
      suppressSelectionCleared = false;
    }
    renderLocalEditOverlay();
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
    ensureLocalEditHydrated();
    renderLocalEditOverlay();
    if (focusRow) {
      queryAll('[data-canvas-layer-id]')
        .find((row) => row.dataset.canvasLayerId === layerId)
        ?.scrollIntoView({ block: 'nearest' });
    }
  }

  function setTool(tool) {
    const allowed = new Set(['select', 'pan', 'roi', 'brush-include', 'brush-exclude']);
    const requested = allowed.has(tool) ? tool : 'select';
    if (localEditMode === 'outpaint' && (requested === 'roi' || requested.startsWith('brush-'))) {
      updateLocalEditPanel();
      return;
    }
    if (requested === 'roi' && !localEditLayerReady()) {
      updateLocalEditPanel();
      return;
    }
    if (requested.startsWith('brush-')) {
      const local = localEditState({ create: false });
      if (!localEditLayerReady() || !local?.roi || !sameRect(local.roi.rect, local.draftRect)) {
        updateLocalEditPanel();
        return;
      }
    }
    activeTool = requested;
    if (activeTool === 'roi' || activeTool.startsWith('brush-')) setActivePanel('local-edit');
    queryAll('[data-canvas-tool]').forEach((button) => {
      const active = button.dataset.canvasTool === activeTool;
      button.classList.toggle('is-active', active);
      button.setAttribute('aria-pressed', String(active));
    });
    if (!canvas) {
      updateLocalEditPanel();
      return;
    }
    const disabled = entryFor().saving || entryFor().blocked;
    canvas.selection = !disabled && activeTool === 'select';
    canvas.skipTargetFind = disabled || activeTool !== 'select';
    canvas.defaultCursor = activeTool === 'pan'
      ? 'grab'
      : activeTool === 'roi' || activeTool.startsWith('brush-') ? 'crosshair' : 'default';
    canvas.requestRenderAll();
    updateLocalEditPanel();
  }

  function removeLocalEditObjects() {
    if (!canvas) return;
    canvas.getObjects()
      .filter((object) => String(object.get('objectRole') || '').startsWith('local-edit'))
      .forEach((object) => canvas.remove(object));
  }

  function localClipPath(sceneRect) {
    return new fabricRuntime.Rect({
      left: sceneRect.x,
      top: sceneRect.y,
      width: sceneRect.width,
      height: sceneRect.height,
      originX: 'left',
      originY: 'top',
      absolutePositioned: true,
    });
  }

  function renderLocalEditOverlay() {
    if (!canvas || !fabricRuntime) return;
    removeLocalEditObjects();
    if (localEditMode !== 'inpaint') {
      canvas.requestRenderAll();
      return;
    }
    const layer = activeLayer();
    const local = localEditState({ create: false });
    if (!layer || !local?.draftRect) {
      canvas.requestRenderAll();
      return;
    }
    let sceneRect;
    try {
      sceneRect = sceneRectFromSourceRoi(layer, local.draftRect);
    } catch (_) {
      canvas.requestRenderAll();
      return;
    }
    const { Circle, Polyline, Rect } = fabricRuntime;
    const roiReady = Boolean(local.roi && sameRect(local.roi.rect, local.draftRect));
    if (roiReady && local.definition?.base === 'full') {
      const fill = new Rect({
        left: sceneRect.x,
        top: sceneRect.y,
        width: sceneRect.width,
        height: sceneRect.height,
        originX: 'left',
        originY: 'top',
        fill: 'rgba(45, 164, 112, 0.22)',
        strokeWidth: 0,
        selectable: false,
        evented: false,
        objectCaching: false,
        excludeFromExport: true,
      });
      fill.set('objectRole', 'local-edit-mask-base');
      canvas.add(fill);
    }
    if (roiReady) {
      Array.from(local.definition?.strokes || []).forEach((stroke) => {
        const color = stroke.mode === 'exclude' ? 'rgba(213, 70, 70, 0.72)' : 'rgba(37, 164, 109, 0.72)';
        const points = stroke.points.map((point) => ({
          x: Number(layer.transform.x) + point.x * Number(layer.transform.scale_x),
          y: Number(layer.transform.y) + point.y * Number(layer.transform.scale_y),
        }));
        const width = Math.max(
          1,
          Number(stroke.radius) * 2
            * ((Number(layer.transform.scale_x) + Number(layer.transform.scale_y)) / 2),
        );
        if (points.length === 1) {
          const dot = new Circle({
            left: points[0].x,
            top: points[0].y,
            radius: width / 2,
            originX: 'center',
            originY: 'center',
            fill: color,
            selectable: false,
            evented: false,
            objectCaching: false,
            excludeFromExport: true,
            clipPath: localClipPath(sceneRect),
          });
          dot.set('objectRole', 'local-edit-mask-stroke');
          canvas.add(dot);
          return;
        }
        const path = new Polyline(points, {
          fill: '',
          stroke: color,
          strokeWidth: width,
          strokeLineCap: 'round',
          strokeLineJoin: 'round',
          selectable: false,
          evented: false,
          objectCaching: false,
          excludeFromExport: true,
          clipPath: localClipPath(sceneRect),
        });
        path.set('objectRole', 'local-edit-mask-stroke');
        canvas.add(path);
      });
    }
    const outline = new Rect({
      left: sceneRect.x,
      top: sceneRect.y,
      width: sceneRect.width,
      height: sceneRect.height,
      originX: 'left',
      originY: 'top',
      fill: 'rgba(0, 0, 0, 0)',
      stroke: roiReady ? '#f16745' : '#bf6b22',
      strokeWidth: 2,
      strokeDashArray: roiReady ? null : [8, 6],
      strokeUniform: true,
      selectable: false,
      evented: false,
      objectCaching: false,
      excludeFromExport: true,
    });
    outline.set('objectRole', 'local-edit-roi');
    canvas.add(outline);
    canvas.requestRenderAll();
  }

  function scenePointFromEvent(options) {
    const point = options?.scenePoint || canvas?.getScenePoint?.(options?.e);
    return point ? { x: Number(point.x), y: Number(point.y) } : null;
  }

  function pointInsideRect(point, rect) {
    return Boolean(
      point
      && point.x >= rect.x
      && point.y >= rect.y
      && point.x < rect.x + rect.width
      && point.y < rect.y + rect.height
    );
  }

  function beginLocalPointer(options) {
    if (!['roi', 'brush-include', 'brush-exclude'].includes(activeTool)) return false;
    const layer = activeLayer();
    const local = localEditState();
    const scenePoint = scenePointFromEvent(options);
    if (!localEditLayerReady(layer) || !local || !scenePoint) return true;
    local.error = '';
    if (activeTool === 'roi') {
      localPointer = {
        kind: 'roi',
        start: scenePoint,
        priorRect: local.draftRect ? { ...local.draftRect } : null,
      };
      local.draftRect = roiFromSceneDrag(layer, scenePoint, scenePoint);
      local.spec = null;
      writeRoiInputs(local.draftRect);
      updateLocalEditPanel();
      renderLocalEditOverlay();
      return true;
    }
    if (!local.roi || !sameRect(local.roi.rect, local.draftRect)) return true;
    const sourcePoint = sourcePointFromScene(layer, scenePoint);
    if (!pointInsideRect(sourcePoint, local.roi.rect)) {
      local.error = '画笔必须从当前选区内部开始';
      updateLocalEditPanel();
      return true;
    }
    localPointer = {
      kind: 'stroke',
      mode: activeTool === 'brush-exclude' ? 'exclude' : 'include',
      points: [sourcePoint],
    };
    return true;
  }

  function moveLocalPointer(options) {
    if (!localPointer) return false;
    const layer = activeLayer();
    const local = localEditState();
    const scenePoint = scenePointFromEvent(options);
    if (!layer || !local || !scenePoint) return true;
    if (localPointer.kind === 'roi') {
      local.draftRect = roiFromSceneDrag(layer, localPointer.start, scenePoint);
      local.spec = null;
      writeRoiInputs(local.draftRect);
      updateLocalEditPanel();
      renderLocalEditOverlay();
      return true;
    }
    const sourcePoint = sourcePointFromScene(layer, scenePoint);
    const previous = localPointer.points.at(-1);
    if (!previous || Math.hypot(sourcePoint.x - previous.x, sourcePoint.y - previous.y) >= 1) {
      localPointer.points.push(sourcePoint);
    }
    return true;
  }

  function endLocalPointer() {
    if (!localPointer) return false;
    const pointer = localPointer;
    localPointer = null;
    if (pointer.kind === 'roi') {
      updateLocalEditPanel();
      renderLocalEditOverlay();
      return true;
    }
    const layer = activeLayer();
    const local = localEditState();
    if (!layer || !local?.roi) return true;
    try {
      const definition = local.definition || createMaskDefinition(layer);
      local.definition = appendMaskStroke(
        definition,
        pointer.mode,
        Number(query('#local-edit-brush-radius').value),
        pointer.points,
        local.roi.rect,
      );
      local.dirtyMask = true;
      local.spec = null;
      local.error = '';
    } catch (error) {
      local.error = error.message || '无法记录当前笔触';
    }
    updateLocalEditPanel();
    renderLocalEditOverlay();
    return true;
  }

  function updateRoiDraftFromInputs() {
    const local = localEditState();
    if (!local || !activeLayer()) return false;
    try {
      local.draftRect = roiFromInputs();
      local.spec = null;
      local.specRequest = null;
      local.roiRequest = null;
      local.error = '';
      updateLocalEditPanel();
      renderLocalEditOverlay();
      return true;
    } catch (error) {
      local.error = error.message || '选区像素坐标无效';
      updateLocalEditPanel();
      return false;
    }
  }

  async function saveLocalRoi() {
    const entry = entryFor();
    const layerId = selectedLayerId;
    let rect;
    try {
      rect = roiFromInputs();
    } catch (error) {
      const local = localEditState();
      if (local) local.error = error.message || '选区像素坐标无效';
      updateLocalEditPanel();
      return;
    }
    if (entry.dirty && !await saveMode(currentMode)) return;
    entry.localEditLoadToken += 1;
    const layer = activeDocument()?.layers?.find((item) => item.id === layerId);
    const local = localEditState();
    if (!layer || !local || !entry.currentVersionId) {
      updateLocalEditPanel();
      return;
    }
    local.loading = false;
    local.saving = true;
    local.error = '';
    updateLocalEditPanel();
    try {
      local.roiRequest = local.roiRequest || {
        canvas_document_id: entry.document.id,
        expected_canvas_revision: entry.currentRevision,
        source_layer_id: layer.id,
        coordinate_space: 'source-pixel',
        rect: normalizeSourceRoi(layer, rect),
        purpose: 'inpaint',
        client_request_id: createRequestId('roi-create'),
      };
      const roi = await api.createCanvasRoi(local.roiRequest, { timeoutMs: 12000 });
      local.roi = roi;
      local.draftRect = { ...roi.rect };
      local.mask = null;
      local.definition = createMaskDefinition(layer);
      local.savedDefinition = null;
      local.dirtyMask = false;
      local.spec = null;
      local.specRequest = null;
      local.hydrated = true;
      setTool('brush-include');
    } catch (error) {
      local.error = formatApiError(error, '选区未能写入本地账本');
    } finally {
      local.saving = false;
      updateLocalEditPanel();
      renderLocalEditOverlay();
    }
  }

  function mutateLocalMask(mutation) {
    const layer = activeLayer();
    const local = localEditState();
    if (!layer || !local?.roi || !sameRect(local.roi.rect, local.draftRect)) return;
    try {
      const current = local.definition || createMaskDefinition(layer);
      local.definition = mutation(current);
      local.dirtyMask = true;
      local.spec = null;
      local.specRequest = null;
      local.error = '';
    } catch (error) {
      local.error = error.message || '蒙版修改失败';
    }
    updateLocalEditPanel();
    renderLocalEditOverlay();
  }

  function restoreLocalMask() {
    const layer = activeLayer();
    const local = localEditState();
    if (!layer || !local?.roi) return;
    local.definition = local.savedDefinition
      ? cloneMaskDefinition(local.savedDefinition)
      : createMaskDefinition(layer);
    local.dirtyMask = false;
    local.error = '';
    updateLocalEditPanel();
    renderLocalEditOverlay();
  }

  async function saveLocalMask() {
    const local = localEditState();
    if (!local?.roi || !local.definition || local.saving) return;
    local.saving = true;
    local.error = '';
    updateLocalEditPanel();
    try {
      local.mask = await api.saveCanvasMask(local.roi.id, {
        expected_revision: Number(local.mask?.current_revision || 0),
        client_request_id: createRequestId('mask-save'),
        definition: cloneMaskDefinition(local.definition),
      }, { timeoutMs: 20000 });
      local.definition = cloneMaskDefinition(local.mask.version.definition);
      local.savedDefinition = cloneMaskDefinition(local.mask.version.definition);
      local.dirtyMask = false;
      local.spec = null;
      local.specRequest = null;
    } catch (error) {
      local.error = formatApiError(error, '蒙版版本未能写入本地账本');
      if (error?.detail?.code === 'CANVAS_MASK_REVISION_CONFLICT') {
        local.hydrated = false;
      }
    } finally {
      local.saving = false;
      updateLocalEditPanel();
      renderLocalEditOverlay();
    }
  }

  async function prepareLocalEditSpec() {
    const entry = entryFor();
    const layer = activeLayer();
    const local = localEditState();
    const source = assetById(layer?.source?.id);
    const sourceSha256 = source?.sha256 || source?.blob?.sha256 || '';
    if (
      !layer
      || !local?.roi
      || !local.mask?.version?.id
      || local.dirtyMask
      || !maskHasWritablePixels(local.definition)
    ) return;
    local.saving = true;
    local.error = '';
    let prepared = false;
    updateLocalEditPanel();
    try {
      local.specRequest = local.specRequest || {
        client_request_id: createRequestId('local-edit-spec'),
        contract: buildFreeLocalEditContract({
          operationId: createRequestId('operation-local-edit'),
          canvasVersionId: entry.currentVersionId,
          layer,
          sourceSha256,
          sourcePixelSha256: '',
          roi: local.roi,
          mask: local.mask,
        }),
      };
      local.spec = await api.createLocalEditSpec(local.specRequest, { timeoutMs: 12000 });
      local.candidatesHydrated = false;
      local.composeRequest = null;
      prepared = true;
      toast('本地编辑规格已冻结，不会产生模型调用费用', 'success');
    } catch (error) {
      local.error = formatApiError(error, '本地编辑规格冻结失败');
    } finally {
      local.saving = false;
      updateLocalEditPanel();
    }
    if (prepared) await ensureLocalEditCandidates(true);
  }

  function invalidateOutpaintSpec(local) {
    local.roi = null;
    local.draftRect = null;
    local.spec = null;
    local.roiRequest = null;
    local.specRequest = null;
    local.composeRequest = null;
    local.candidatesHydrated = false;
    local.candidateId = '';
    local.outpaintConfirmed = false;
  }

  function updateOutpaintDraftFromInputs() {
    const local = localEditState();
    const layer = activeLayer();
    if (!local || !layer || localEditMode !== 'outpaint') return false;
    try {
      local.outpaint = outpaintFromInputs(layer);
      invalidateOutpaintSpec(local);
      local.error = '';
      updateLocalEditPanel();
      return true;
    } catch (error) {
      local.error = error.message || '扩图规格无效';
      updateLocalEditPanel();
      return false;
    }
  }

  function confirmOutpaintSpec(confirmed) {
    const local = localEditState();
    if (!local || localEditMode !== 'outpaint') return;
    local.outpaintConfirmed = Boolean(confirmed);
    local.error = '';
    updateLocalEditPanel();
  }

  async function prepareOutpaintSpec() {
    const entry = entryFor();
    const layerId = selectedLayerId;
    const local = localEditState();
    if (!local || !local.outpaintConfirmed || local.spec || local.saving) return;
    let outpaint;
    try {
      outpaint = outpaintFromInputs(activeLayer());
    } catch (error) {
      local.error = error.message || '扩图规格无效';
      updateLocalEditPanel();
      return;
    }
    if (entry.dirty && !await saveMode(currentMode)) return;
    entry.localEditLoadToken += 1;
    const layer = activeDocument()?.layers?.find((item) => item.id === layerId);
    const source = assetById(layer?.source?.id);
    const sourceSha256 = source?.sha256 || source?.blob?.sha256 || '';
    if (!layer || !sourceSha256 || !entry.currentVersionId) return;
    local.saving = true;
    local.error = '';
    let prepared = false;
    updateLocalEditPanel();
    try {
      const rect = {
        x: 0,
        y: 0,
        width: outpaint.output_width,
        height: outpaint.output_height,
      };
      if (!local.roi || !sameRect(local.roi.rect, rect)) {
        local.roiRequest = local.roiRequest || {
          canvas_document_id: entry.document.id,
          expected_canvas_revision: entry.currentRevision,
          source_layer_id: layer.id,
          coordinate_space: 'output-pixel',
          rect,
          purpose: 'outpaint',
          client_request_id: createRequestId('outpaint-roi-create'),
        };
        local.roi = await api.createCanvasRoi(local.roiRequest, { timeoutMs: 12000 });
        local.draftRect = { ...local.roi.rect };
      }
      local.specRequest = local.specRequest || {
        client_request_id: createRequestId('outpaint-spec'),
        contract: buildPaidOutpaintContract({
          operationId: createRequestId('operation-outpaint'),
          canvasVersionId: entry.currentVersionId,
          layer,
          sourceSha256,
          sourcePixelSha256: '',
          roi: local.roi,
          outpaint,
          confirmed: true,
        }),
      };
      local.spec = await api.createLocalEditSpec(local.specRequest, { timeoutMs: 12000 });
      local.outpaint = normalizeOutpaintConfig(layer, local.spec.contract.outpaint);
      local.candidatesHydrated = false;
      local.composeRequest = null;
      prepared = true;
      toast('扩图规格已冻结；当前没有发起模型调用', 'success');
    } catch (error) {
      local.error = formatApiError(error, '扩图规格冻结失败');
    } finally {
      local.saving = false;
      updateLocalEditPanel();
    }
    if (prepared) await ensureLocalEditCandidates(true);
  }

  function selectLocalEditCandidate(candidateId) {
    const local = localEditState({ create: false });
    if (!local?.spec) return;
    local.candidateId = String(candidateId || '');
    local.composeRequest = null;
    local.error = '';
    updateLocalEditPanel();
  }

  async function applyLocalEditCandidate() {
    const entry = entryFor();
    const local = localEditState({ create: false });
    const candidate = localEditCandidateOptions(local)
      .find((item) => item.id === local?.candidateId);
    if (!local?.spec || !candidate || local.saving || local.candidateLoading) return;
    if (entry.dirty && !await saveMode(currentMode)) return;
    const request = local.composeRequest || {
      local_edit_spec_id: local.spec.id,
      candidate_asset_id: candidate.id,
      expected_canvas_revision: entry.currentRevision,
      client_request_id: createRequestId('local-edit-compose'),
    };
    local.composeRequest = request;
    local.saving = true;
    local.error = '';
    updateLocalEditPanel();
    try {
      const response = await api.composeLocalEdit(currentMode, request, { timeoutMs: 120000 });
      entry.localEditLoadToken += 1;
      applyCanvasResponse(currentMode, response.canvas, { rebuildCanvas: true });
      local.composeRequest = null;
      setTool('select');
      setSaveState('saved', '画布已保存', `revision ${response.canvas.current_revision}`);
      toast(response.replayed ? '局部编辑结果已从账本恢复' : '局部编辑结果已应用到画布', 'success');
    } catch (error) {
      local.error = formatApiError(error, '候选结果未能应用到画布');
      if (error?.detail?.code === 'CANVAS_REVISION_CONFLICT') entry.blocked = true;
    } finally {
      local.saving = false;
      updateLocalEditPanel();
      updateHistoryControls();
    }
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
      image.set('sourceAssetId', layer.source.id);
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
      placeholder.set('sourceAssetId', layer.source.id);
      return placeholder;
    }
  }

  async function renderCanvas() {
    if (!canvas || currentView !== 'canvas') return;
    const { Rect } = fabricRuntime;
    const token = ++buildToken;
    const selectedBeforeBuild = selectedLayerId;
    renderCanvasLoading(true);
    setCanvasDimensions();
    suppressSelectionCleared = true;
    try {
      canvas.discardActiveObject();
      canvas.clear();
    } finally {
      suppressSelectionCleared = false;
    }
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
    renderLocalEditOverlay();
    canvas.requestRenderAll();
    fitArtboard();
    if (selectedBeforeBuild) setSelectedLayer(selectedBeforeBuild);
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
      if (suppressSelectionCleared || activeTool !== 'select') return;
      selectedLayerId = '';
      renderLayerList();
      updateSelectionPanel();
      renderLocalEditOverlay();
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
    canvas.on('mouse:down', (options) => {
      const { e } = options;
      if (activeTool !== 'pan' && !toolBeforeSpace) return;
      isPanning = true;
      lastPanPoint = { x: e.clientX, y: e.clientY };
      canvas.defaultCursor = 'grabbing';
    });
    canvas.on('mouse:down', (options) => {
      if (activeTool === 'pan' || toolBeforeSpace) return;
      beginLocalPointer(options);
    });
    canvas.on('mouse:move', (options) => {
      const { e } = options;
      if (isPanning && lastPanPoint) {
        const viewport = canvas.viewportTransform;
        viewport[4] += e.clientX - lastPanPoint.x;
        viewport[5] += e.clientY - lastPanPoint.y;
        lastPanPoint = { x: e.clientX, y: e.clientY };
        canvas.requestRenderAll();
        return;
      }
      moveLocalPointer(options);
    });
    canvas.on('mouse:up', () => {
      if (isPanning) {
        isPanning = false;
        lastPanPoint = null;
        canvas.defaultCursor = activeTool === 'pan' ? 'grab' : 'default';
        return;
      }
      endLocalPointer();
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

  function operationReplacesLayerSource(operation) {
    return String(operation?.mutation?.before?.source?.id || '')
      !== String(operation?.mutation?.after?.source?.id || '');
  }

  async function syncHistoryMutation(operation) {
    if (operationReplacesLayerSource(operation)) {
      await reloadObjectFromLayer(operation.mutation.target_layer_id);
    } else syncObjectFromLayer(operation.mutation.target_layer_id);
  }

  async function undo() {
    const document = activeDocument();
    if (!document || entryFor().saving || entryFor().blocked || historySyncing) return;
    const operation = undoCanvas(document);
    if (!operation) return;
    historySyncing = true;
    renderLists();
    updateHistoryControls();
    scheduleSave();
    try {
      await syncHistoryMutation(operation);
    } finally {
      historySyncing = false;
      updateHistoryControls();
    }
  }

  async function redo() {
    const document = activeDocument();
    if (!document || entryFor().saving || entryFor().blocked || historySyncing) return;
    const operation = redoCanvas(document);
    if (!operation) return;
    historySyncing = true;
    renderLists();
    updateHistoryControls();
    scheduleSave();
    try {
      await syncHistoryMutation(operation);
    } finally {
      historySyncing = false;
      updateHistoryControls();
    }
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
    updateLocalEditPanel();
  }

  function bind() {
    if (bound) return;
    bound = true;
    refreshIcons(query('#canvas-workspace'));
    queryAll('[data-studio-view]').forEach((button) => button.addEventListener('click', () => setView(button.dataset.studioView)));
    queryAll('[data-canvas-panel]').forEach((button) => button.addEventListener('click', () => {
      setActivePanel(button.dataset.canvasPanel);
    }));
    queryAll('[data-local-edit-mode]').forEach((button) => button.addEventListener('click', () => {
      setLocalEditMode(button.dataset.localEditMode);
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
    query('#local-edit-save-roi').addEventListener('click', saveLocalRoi);
    queryAll('#local-edit-roi-fields input').forEach((input) => {
      input.addEventListener('change', updateRoiDraftFromInputs);
    });
    queryAll('[data-local-edit-base]').forEach((button) => {
      button.addEventListener('click', () => mutateLocalMask(
        (definition) => setMaskBase(definition, button.dataset.localEditBase),
      ));
    });
    query('#local-edit-brush-radius').addEventListener('input', updateLocalEditPanel);
    query('#local-edit-feather').addEventListener('input', (event) => {
      mutateLocalMask((definition) => setMaskFeather(definition, Number(event.target.value)));
    });
    query('#local-edit-invert').addEventListener('click', () => {
      mutateLocalMask(invertMaskDefinition);
    });
    query('#local-edit-restore').addEventListener('click', restoreLocalMask);
    query('#local-edit-save-mask').addEventListener('click', saveLocalMask);
    query('#local-edit-prepare').addEventListener('click', prepareLocalEditSpec);
    queryAll('#local-edit-outpaint-width, #local-edit-outpaint-height, #local-edit-outpaint-x, #local-edit-outpaint-y').forEach((input) => {
      input.addEventListener('change', updateOutpaintDraftFromInputs);
    });
    query('#local-edit-outpaint-transition').addEventListener('input', updateOutpaintDraftFromInputs);
    query('#local-edit-outpaint-confirm').addEventListener('change', (event) => {
      confirmOutpaintSpec(event.currentTarget.checked);
    });
    query('#local-edit-prepare-outpaint').addEventListener('click', prepareOutpaintSpec);
    query('#local-edit-candidate').addEventListener('change', (event) => {
      selectLocalEditCandidate(event.target.value);
    });
    query('#local-edit-refresh-candidates').addEventListener('click', () => {
      ensureLocalEditCandidates(true);
    });
    query('#local-edit-apply').addEventListener('click', applyLocalEditCandidate);
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
      if (event.key === 'Escape') {
        event.preventDefault();
        const local = localEditState({ create: false });
        if (localPointer?.kind === 'roi' && local) local.draftRect = localPointer.priorRect;
        localPointer = null;
        setTool('select');
        updateLocalEditPanel();
        renderLocalEditOverlay();
        return;
      }
      if (event.key === ' ' && !event.repeat) {
        event.preventDefault();
        toolBeforeSpace = activeTool;
        setTool('pan');
        return;
      }
      if (!event.ctrlKey && !event.metaKey && !event.altKey) {
        const key = event.key.toLowerCase();
        const tool = key === 'r' ? 'roi' : key === 'b' ? 'brush-include' : key === 'e' ? 'brush-exclude' : '';
        if (tool) {
          event.preventDefault();
          setTool(tool);
          return;
        }
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
    setActivePanel('layers');
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
