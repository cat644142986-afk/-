export const SPATIAL_DRAG_MIME = 'application/x-product-atelier-spatial-item';

export const SPATIAL_REFERENCE_FIELDS = Object.freeze([
  'asset_id',
  'result_id',
  'task_id',
  'product_profile_version_id',
  'lineage_parent_id',
]);

const BUSINESS_ACTIONS = Object.freeze({
  asset: ['cutout', 'white-background', 'outpaint', 'local-edit', 'generate-image', 'generate-video', 'fine-edit'],
  result: ['outpaint', 'local-edit', 'generate-image', 'generate-video', 'compare', 'export', 'fine-edit'],
  task: ['open-task'],
  video: ['toggle-video', 'export'],
});

const TASK_STATUS_COPY = Object.freeze({
  queued: '排队中',
  running: '处理中',
  paused: '已暂停',
  canceling: '正在取消',
  completed: '已完成',
  partial: '部分完成',
  failed: '失败',
  interrupted: '已中断',
  canceled: '已取消',
});

function cleanText(value, fallback = '') {
  return String(value || '').trim().replace(/\s+/g, ' ') || fallback;
}

function positiveInteger(value) {
  const number = Math.round(Number(value));
  return Number.isFinite(number) && number > 0 ? number : null;
}

function reference(value) {
  const candidate = cleanText(value);
  return candidate || null;
}

function roleIsResult(role) {
  return String(role || '').startsWith('result_');
}

function mediaKind(asset) {
  const mime = String(asset?.mime || asset?.media_type || '').toLowerCase();
  return asset?.kind === 'video' || mime.startsWith('video/') ? 'video' : 'image';
}

function runtimeId(prefix = 'spatial') {
  const token = globalThis.crypto?.randomUUID?.()
    || `${Date.now()}-${Math.random().toString(16).slice(2)}`;
  return `${prefix}_${String(token).replace(/[^A-Za-z0-9_-]/g, '_')}`;
}

function normalizedReferences(value = {}) {
  const result = {};
  SPATIAL_REFERENCE_FIELDS.forEach((field) => { result[field] = reference(value[field]); });
  return result;
}

export function spatialItemFromAsset(asset, overrides = {}) {
  const assetId = reference(asset?.id || asset?.asset_id || overrides.asset_id);
  if (!assetId) throw new Error('空间画布素材缺少 asset_id');
  const role = cleanText(overrides.role || asset?.role, 'workspace_source');
  const result = overrides.kind === 'result' || roleIsResult(role);
  const metadata = asset?.metadata && typeof asset.metadata === 'object' ? asset.metadata : {};
  return {
    kind: mediaKind(asset),
    business_kind: result ? 'result' : 'asset',
    name: cleanText(overrides.name || asset?.name, result ? '生成结果' : '素材'),
    role,
    mime: cleanText(asset?.mime || overrides.mime, 'image/jpeg'),
    width: positiveInteger(overrides.width || asset?.width),
    height: positiveInteger(overrides.height || asset?.height),
    references: normalizedReferences({
      asset_id: assetId,
      result_id: result ? reference(overrides.result_id || assetId) : null,
      task_id: overrides.task_id || asset?.task_id || asset?.job_id,
      product_profile_version_id: overrides.product_profile_version_id
        || asset?.product_profile_version_id,
      lineage_parent_id: overrides.lineage_parent_id
        || asset?.lineage_parent_id
        || metadata.lineage_parent_id,
    }),
  };
}

export function spatialItemFromJob(job, overrides = {}) {
  const taskId = reference(job?.id || job?.task_id);
  if (!taskId) throw new Error('空间画布任务缺少 task_id');
  const items = Array.from(job?.items || []);
  return {
    kind: 'task',
    business_kind: 'task',
    name: cleanText(job?.title, '创作任务'),
    status: cleanText(job?.status, 'queued'),
    total_items: positiveInteger(job?.total_items) || items.length,
    completed_items: Math.max(0, Number(job?.completed_items || 0)),
    references: normalizedReferences({
      task_id: taskId,
      product_profile_version_id: overrides.product_profile_version_id
        || job?.snapshot?.product_profile_version_id,
      lineage_parent_id: overrides.lineage_parent_id,
    }),
  };
}

export function spatialTaskLabel(item) {
  const status = cleanText(item?.status, 'queued');
  return `${cleanText(item?.name, '创作任务')}\n${TASK_STATUS_COPY[status] || status} · ${Number(item?.completed_items || 0)}/${Number(item?.total_items || 0)} 项`;
}

export function updateSpatialTaskElements(elements, item, updateElement = (element, updates) => ({
  ...element,
  ...updates,
})) {
  const taskId = reference(item?.references?.task_id);
  const current = Array.from(elements || []);
  if (!taskId) return { changed: false, elements: current, taskElement: null };
  const taskElement = current.find((element) => (
    !element?.isDeleted
    && element?.type === 'rectangle'
    && reference(element?.customData?.task_id) === taskId
  ));
  if (!taskElement) return { changed: false, elements: current, taskElement: null };
  const nextLabel = spatialTaskLabel(item);
  const boundTextIds = new Set(Array.from(taskElement.boundElements || [])
    .filter((binding) => binding?.type === 'text' && binding?.id)
    .map((binding) => binding.id));
  let changed = false;
  const next = current.map((element) => {
    if (element?.id === taskElement.id && element?.label?.text !== undefined) {
      if (element.label.text === nextLabel) return element;
      changed = true;
      return updateElement(element, { label: { ...element.label, text: nextLabel } });
    }
    const boundText = element?.type === 'text'
      && (element.containerId === taskElement.id || boundTextIds.has(element.id));
    if (!boundText || (element.text === nextLabel && element.originalText === nextLabel)) return element;
    changed = true;
    return updateElement(element, { text: nextLabel, originalText: nextLabel });
  });
  return {
    changed,
    elements: next,
    taskElement: next.find((element) => element?.id === taskElement.id) || taskElement,
  };
}

export function spatialCustomData(item) {
  return normalizedReferences(item?.references || item || {});
}

export function spatialBusinessKey(value) {
  const refs = normalizedReferences(value?.references || value?.customData || value || {});
  if (refs.result_id) return `result:${refs.result_id}`;
  if (refs.asset_id) return `asset:${refs.asset_id}`;
  if (refs.task_id) return `task:${refs.task_id}`;
  return '';
}

export function spatialContextActions(value) {
  const refs = normalizedReferences(value?.references || value?.customData || value || {});
  if (value?.kind === 'video' || value?.type === 'embeddable') return [...BUSINESS_ACTIONS.video];
  if (refs.result_id) return [...BUSINESS_ACTIONS.result];
  if (refs.asset_id) return [...BUSINESS_ACTIONS.asset];
  if (refs.task_id) return [...BUSINESS_ACTIONS.task];
  return [];
}

export function serializeSpatialDragItem(item) {
  const normalized = item?.kind === 'task'
    ? spatialItemFromJob({
      id: item.references?.task_id,
      title: item.name,
      status: item.status,
      total_items: item.total_items,
      completed_items: item.completed_items,
      snapshot: { product_profile_version_id: item.references?.product_profile_version_id },
    }, {
      lineage_parent_id: item.references?.lineage_parent_id,
    })
    : spatialItemFromAsset({
      id: item?.references?.asset_id,
      name: item?.name,
      role: item?.role,
      mime: item?.mime,
      width: item?.width,
      height: item?.height,
    }, {
      kind: item?.business_kind,
      result_id: item?.references?.result_id,
      task_id: item?.references?.task_id,
      product_profile_version_id: item?.references?.product_profile_version_id,
      lineage_parent_id: item?.references?.lineage_parent_id,
    });
  return JSON.stringify(normalized);
}

export function parseSpatialDragItem(value) {
  const parsed = JSON.parse(String(value || ''));
  if (parsed?.kind === 'task') return spatialItemFromJob({
    id: parsed.references?.task_id,
    title: parsed.name,
    status: parsed.status,
    total_items: parsed.total_items,
    completed_items: parsed.completed_items,
    snapshot: { product_profile_version_id: parsed.references?.product_profile_version_id },
  }, {
    lineage_parent_id: parsed.references?.lineage_parent_id,
  });
  return spatialItemFromAsset({
    id: parsed?.references?.asset_id,
    name: parsed?.name,
    role: parsed?.role,
    mime: parsed?.mime,
    width: parsed?.width,
    height: parsed?.height,
  }, {
    kind: parsed?.business_kind,
    result_id: parsed?.references?.result_id,
    task_id: parsed?.references?.task_id,
    product_profile_version_id: parsed?.references?.product_profile_version_id,
    lineage_parent_id: parsed?.references?.lineage_parent_id,
  });
}

function imageSize(item) {
  const sourceWidth = positiveInteger(item?.width) || 1200;
  const sourceHeight = positiveInteger(item?.height) || 900;
  const scale = Math.min(420 / sourceWidth, 320 / sourceHeight, 1);
  return {
    width: Math.max(120, Math.round(sourceWidth * scale)),
    height: Math.max(90, Math.round(sourceHeight * scale)),
  };
}

function viewportOrigin(appState = {}) {
  const zoom = Math.max(0.01, Number(appState.zoom?.value || 1));
  const width = Math.max(0, Number(appState.width || 1200));
  const height = Math.max(0, Number(appState.height || 760));
  return {
    x: -Number(appState.scrollX || 0) + width / (2 * zoom),
    y: -Number(appState.scrollY || 0) + height / (2 * zoom),
  };
}

function liveElements(elements) {
  return Array.from(elements || []).filter((element) => !element?.isDeleted);
}

export function uniqueSpatialBusinessItems(items, elements = []) {
  const seen = new Set(liveElements(elements).map(spatialBusinessKey).filter(Boolean));
  return Array.from(items || []).filter((item) => {
    const key = spatialBusinessKey(item);
    if (!key || seen.has(key)) return false;
    seen.add(key);
    return true;
  });
}

function appendBoundArrow(element, arrowId) {
  const boundElements = Array.from(element?.boundElements || []);
  if (boundElements.some((bound) => bound?.id === arrowId && bound?.type === 'arrow')) {
    return element;
  }
  return { ...element, boundElements: [...boundElements, { id: arrowId, type: 'arrow' }] };
}

export function mergeSpatialNodeBatch(elements, additions, lineageBindings = []) {
  const bindingsByArrow = new Map();
  const arrowsByEndpoint = new Map();
  Array.from(lineageBindings || []).forEach((binding) => {
    if (!binding?.arrow_id || !binding?.parent_element_id || !binding?.child_element_id) return;
    bindingsByArrow.set(binding.arrow_id, binding);
    [binding.parent_element_id, binding.child_element_id].forEach((elementId) => {
      const arrowIds = arrowsByEndpoint.get(elementId) || [];
      arrowIds.push(binding.arrow_id);
      arrowsByEndpoint.set(elementId, arrowIds);
    });
  });
  return [...Array.from(elements || []), ...Array.from(additions || [])].map((element) => {
    const binding = bindingsByArrow.get(element?.id);
    if (binding) {
      return {
        ...element,
        startBinding: { elementId: binding.parent_element_id, focus: 0, gap: 12 },
        endBinding: { elementId: binding.child_element_id, focus: 0, gap: 12 },
      };
    }
    return (arrowsByEndpoint.get(element?.id) || []).reduce(appendBoundArrow, element);
  });
}

function parentElementFor(item, elements) {
  const parentId = reference(item?.references?.lineage_parent_id);
  if (!parentId) return null;
  return liveElements(elements).find((element) => {
    const refs = normalizedReferences(element.customData || {});
    return refs.result_id === parentId || refs.asset_id === parentId;
  }) || null;
}

function placementFor(item, elements, appState, batchIndex) {
  const size = item.kind === 'task' ? { width: 320, height: 176 } : imageSize(item);
  const parent = parentElementFor(item, elements);
  if (parent) {
    const parentReference = reference(item?.references?.lineage_parent_id);
    const siblings = liveElements(elements).filter((element) => {
      const refs = normalizedReferences(element?.customData || {});
      const sameKind = item.kind === 'task' ? Boolean(refs.task_id) : Boolean(refs.asset_id);
      return sameKind && refs.lineage_parent_id === parentReference;
    });
    if (item.kind === 'task') {
      return {
        ...size,
        x: Number(parent.x),
        y: Number(parent.y) + Number(parent.height) + 120 + siblings.length * (size.height + 56),
        parent,
        placement: 'below',
      };
    }
    const lane = siblings.length === 0
      ? 0
      : (siblings.length % 2 ? Math.ceil(siblings.length / 2) : -Math.ceil(siblings.length / 2));
    return {
      ...size,
      x: Number(parent.x) + Number(parent.width) + 160,
      y: Number(parent.y) + (Number(parent.height) - size.height) / 2 + lane * (size.height + 56),
      parent,
      placement: 'right',
    };
  }
  const origin = viewportOrigin(appState);
  const column = batchIndex % 3;
  const row = Math.floor(batchIndex / 3);
  return {
    ...size,
    x: origin.x - size.width / 2 + column * (size.width + 72),
    y: origin.y - size.height / 2 + row * (size.height + 72),
      parent: null,
      placement: 'viewport',
  };
}

export function buildSpatialNodeBatch(items, {
  elements = [],
  appState = {},
  idFactory = runtimeId,
} = {}) {
  const skeletons = [];
  const proxyRequests = [];
  const nodeIds = [];
  const lineageBindings = [];
  const working = liveElements(elements);
  Array.from(items || []).forEach((item, index) => {
    const placement = placementFor(item, working, appState, index);
    const nodeId = idFactory('spatial_node');
    const refs = spatialCustomData(item);
    let node;
    if (item.kind === 'task') {
      node = {
        id: nodeId,
        type: 'rectangle',
        x: placement.x,
        y: placement.y,
        width: placement.width,
        height: placement.height,
        strokeColor: '#3f3b37',
        backgroundColor: '#fffdf9',
        fillStyle: 'solid',
        strokeWidth: 1,
        strokeStyle: 'solid',
        roughness: 0,
        roundness: { type: 3 },
        customData: refs,
        label: {
          text: spatialTaskLabel(item),
          fontSize: 22,
          textAlign: 'left',
          verticalAlign: 'middle',
        },
      };
    } else if (item.kind === 'video') {
      node = {
        id: nodeId,
        type: 'embeddable',
        x: placement.x,
        y: placement.y,
        width: placement.width,
        height: placement.height,
        link: `product-atelier-video://${refs.asset_id}`,
        strokeColor: '#20242a',
        backgroundColor: '#181c20',
        fillStyle: 'solid',
        strokeWidth: 1,
        strokeStyle: 'solid',
        roughness: 0,
        roundness: { type: 3 },
        customData: refs,
      };
    } else {
      const fileId = `proxy_${String(refs.asset_id).replace(/[^A-Za-z0-9_-]/g, '_')}`;
      node = {
        id: nodeId,
        type: 'image',
        x: placement.x,
        y: placement.y,
        width: placement.width,
        height: placement.height,
        fileId,
        status: 'saved',
        scale: [1, 1],
        crop: null,
        strokeColor: '#3f3b37',
        backgroundColor: 'transparent',
        fillStyle: 'solid',
        strokeWidth: 1,
        strokeStyle: 'solid',
        roughness: 0,
        roundness: { type: 3 },
        customData: refs,
      };
      proxyRequests.push({ elementId: nodeId, fileId, assetId: refs.asset_id });
    }
    if (placement.parent) {
      const arrowId = idFactory('spatial_lineage');
      const below = placement.placement === 'below';
      const parentX = Number(placement.parent.x);
      const parentY = Number(placement.parent.y);
      const parentWidth = Number(placement.parent.width);
      const parentHeight = Number(placement.parent.height);
      skeletons.push({
        id: arrowId,
        type: 'arrow',
        x: below ? parentX + parentWidth / 2 : parentX + parentWidth + 12,
        y: below ? parentY + parentHeight + 12 : parentY + parentHeight / 2,
        points: below
          ? [[0, 0], [0, Math.max(40, placement.y - parentY - parentHeight - 24)]]
          : [[0, 0], [Math.max(40, placement.x - parentX - parentWidth - 24), 0]],
        strokeColor: '#c85f3b',
        strokeWidth: 2,
        strokeStyle: 'solid',
        roughness: 0,
        startArrowhead: null,
        endArrowhead: 'arrow',
      });
      lineageBindings.push({
        arrow_id: arrowId,
        parent_element_id: placement.parent.id,
        child_element_id: nodeId,
      });
    }
    skeletons.push(node);
    nodeIds.push(nodeId);
    working.push(node);
  });
  return {
    skeletons, proxyRequests, nodeIds, lineageBindings,
  };
}

export function selectedSpatialBusinessElement(elements, appState) {
  const selected = appState?.selectedElementIds || {};
  const matches = liveElements(elements).filter((element) => (
    selected[element.id] && spatialBusinessKey(element)
  ));
  return matches.length === 1 ? matches[0] : null;
}

export function spatialLineageFocusElements(items, elements, additions) {
  const parentIds = new Set(Array.from(items || [])
    .map((item) => reference(item?.references?.lineage_parent_id))
    .filter(Boolean));
  const parents = liveElements(elements).filter((element) => {
    const refs = normalizedReferences(element.customData || {});
    return parentIds.has(refs.result_id) || parentIds.has(refs.asset_id);
  });
  return [...parents, ...liveElements(additions)];
}
