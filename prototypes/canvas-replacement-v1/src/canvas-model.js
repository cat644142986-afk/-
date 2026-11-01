const IMAGE_MAX_EDGE = 460;
const GROUP_PADDING = 40;

let sequence = 0;

// Media controls isolate pointer events from the canvas, but selection still
// belongs to React Flow's single nodes state, not a player-specific store.
export function selectMediaNode(nodes, id, event = {}) {
  if (!nodes.some(node => node.id === id)) return nodes;
  const additive = event.ctrlKey || event.metaKey || event.shiftKey;
  return nodes.map(node => ({ ...node, selected: node.id === id || Boolean(additive && node.selected) }));
}

export function nextPrototypeId(prefix = 'node') {
  sequence += 1;
  return `${prefix}_${Date.now().toString(36)}_${sequence.toString(36)}`;
}

function finite(value, fallback = 0) {
  const number = Number(value);
  return Number.isFinite(number) ? number : fallback;
}

function nodeWidth(node) {
  return finite(node?.width ?? node?.measured?.width ?? node?.style?.width, 240);
}

function nodeHeight(node) {
  return finite(node?.height ?? node?.measured?.height ?? node?.style?.height, 180);
}

export function fittedImageSize(naturalWidth, naturalHeight, maxEdge = IMAGE_MAX_EDGE) {
  const width = Math.max(1, finite(naturalWidth, 1));
  const height = Math.max(1, finite(naturalHeight, 1));
  const scale = Math.min(1, maxEdge / Math.max(width, height));
  return {
    width: Math.max(1, width * scale),
    height: Math.max(1, height * scale),
    aspectRatio: width / height,
  };
}

export function createImageNode({
  id = nextPrototypeId('image'),
  name = '图片',
  src,
  media,
  naturalWidth,
  naturalHeight,
  position = { x: 0, y: 0 },
} = {}) {
  const size = fittedImageSize(naturalWidth, naturalHeight);
  return {
    id,
    type: 'image',
    position: { x: finite(position.x), y: finite(position.y) },
    selected: true,
    style: { width: size.width, height: size.height },
    data: {
      name,
      src,
      naturalWidth: Math.max(1, finite(naturalWidth, 1)),
      naturalHeight: Math.max(1, finite(naturalHeight, 1)),
      aspectRatio: size.aspectRatio,
      kind: media?.result_id ? 'result' : 'asset',
      ...(media ? { media } : {}),
    },
  };
}

export function selectOnly(nodes, ids = []) {
  const selected = new Set(ids);
  return nodes.map((node) => ({ ...node, selected: selected.has(node.id) }));
}

export function worldPosition(node, nodes) {
  const byId = new Map(nodes.map((item) => [item.id, item]));
  const position = { x: finite(node?.position?.x), y: finite(node?.position?.y) };
  const visited = new Set([node?.id]); let parent = byId.get(node?.parentId);
  while (parent && !visited.has(parent.id)) {
    visited.add(parent.id); position.x += finite(parent.position?.x); position.y += finite(parent.position?.y);
    parent = byId.get(parent.parentId);
  }
  return position;
}

export function duplicateSelection(nodes, edges, offset = { x: 36, y: 36 }) {
  const selected = new Set(nodes.filter((node) => node.selected).map((node) => node.id));
  const roots = nodes.filter((node) => node.selected && !selected.has(node.parentId));
  if (!roots.length) return { nodes, edges, selectedIds: [] };
  const copiedIds = new Set();
  const collect = (id) => {
    copiedIds.add(id);
    nodes.filter((node) => node.parentId === id).forEach((node) => collect(node.id));
  };
  roots.forEach((node) => collect(node.id));
  const idMap = new Map(Array.from(copiedIds).map((id) => [id, nextPrototypeId('copy')]));
  const additions = nodes.filter((node) => copiedIds.has(node.id)).map((node) => {
    const root = roots.some((candidate) => candidate.id === node.id);
    return {
      ...node,
      id: idMap.get(node.id),
      parentId: !root && node.parentId ? idMap.get(node.parentId) : undefined,
      position: root
        ? { x: worldPosition(node, nodes).x + finite(offset.x, 36), y: worldPosition(node, nodes).y + finite(offset.y, 36) }
        : { ...node.position },
      selected: root,
      data: { ...node.data, execution: undefined, name: root ? `${node.data?.name || '对象'} 副本` : node.data?.name },
    };
  });
  const edgeAdditions = edges.filter((edge) => copiedIds.has(edge.source) && copiedIds.has(edge.target)).map((edge) => ({
    ...edge,
    id: nextPrototypeId('edge'),
    source: idMap.get(edge.source),
    target: idMap.get(edge.target),
    selected: false,
  }));
  const selectedIds = roots.map((node) => idMap.get(node.id));
  return {
    nodes: [...nodes.map((node) => ({ ...node, selected: false })), ...additions],
    edges: [...edges.map((edge) => ({ ...edge, selected: false })), ...edgeAdditions],
    selectedIds,
  };
}

export function groupSelection(nodes) {
  const selected = nodes.filter((node) => node.selected && !node.parentId && node.type !== 'group');
  if (selected.length < 2) return { nodes, groupId: '' };
  const left = Math.min(...selected.map((node) => finite(node.position?.x)));
  const top = Math.min(...selected.map((node) => finite(node.position?.y)));
  const right = Math.max(...selected.map((node) => finite(node.position?.x) + nodeWidth(node)));
  const bottom = Math.max(...selected.map((node) => finite(node.position?.y) + nodeHeight(node)));
  const groupId = nextPrototypeId('group');
  const group = {
    id: groupId,
    type: 'group',
    position: { x: left - GROUP_PADDING, y: top - GROUP_PADDING },
    selected: true,
    style: {
      width: right - left + GROUP_PADDING * 2,
      height: bottom - top + GROUP_PADDING * 2,
    },
    data: { name: `组合 · ${selected.length} 个对象`, childCount: selected.length },
  };
  const selectedIds = new Set(selected.map((node) => node.id));
  const children = nodes.map((node) => {
    if (!selectedIds.has(node.id)) return { ...node, selected: false };
    return {
      ...node,
      parentId: groupId,
      extent: undefined,
      position: {
        x: finite(node.position?.x) - group.position.x,
        y: finite(node.position?.y) - group.position.y,
      },
      selected: false,
    };
  });
  return { nodes: [group, ...children], groupId };
}

export function ungroupSelection(nodes) {
  const groups = nodes.filter((node) => node.selected && node.type === 'group');
  if (!groups.length) return { nodes, selectedIds: [] };
  const groupIds = new Set(groups.map((node) => node.id));
  const groupById = new Map(groups.map((node) => [node.id, node]));
  const selectedIds = [];
  const next = nodes.flatMap((node) => {
    if (groupIds.has(node.id)) return [];
    if (!node.parentId || !groupIds.has(node.parentId)) return [{ ...node, selected: false }];
    const parent = groupById.get(node.parentId);
    selectedIds.push(node.id);
    return [{
      ...node,
      parentId: undefined,
      extent: undefined,
      position: {
        x: finite(parent.position?.x) + finite(node.position?.x),
        y: finite(parent.position?.y) + finite(node.position?.y),
      },
      selected: true,
    }];
  });
  return { nodes: next, selectedIds };
}

export function deleteSelection(nodes, edges) {
  const removed = new Set(nodes.filter((node) => node.selected).map((node) => node.id));
  let changed = true;
  while (changed) {
    changed = false;
    nodes.forEach((node) => {
      if (node.parentId && removed.has(node.parentId) && !removed.has(node.id)) { removed.add(node.id); changed = true; }
    });
  }
  return {
    nodes: nodes.filter((node) => !removed.has(node.id)),
    edges: edges.filter((edge) => !removed.has(edge.source) && !removed.has(edge.target) && !edge.selected),
  };
}

function serializableNode(node) {
  const data = Object.fromEntries(Object.entries(node.data || {}).filter(([key, value]) => typeof value !== 'function' && key !== 'referenceViews'));
  return {
    id: node.id,
    type: node.type,
    position: { x: finite(node.position?.x), y: finite(node.position?.y) },
    parentId: node.parentId || undefined,
    extent: node.extent,
    style: {
      width: nodeWidth(node),
      height: nodeHeight(node),
    },
    data,
  };
}

export function serializePrototype(nodes, edges, viewport) {
  return {
    schema: 1,
    nodes: nodes.map(serializableNode),
    edges: edges.map((edge) => ({
      id: edge.id,
      source: edge.source,
      target: edge.target,
      sourceHandle: edge.sourceHandle || undefined,
      targetHandle: edge.targetHandle || undefined,
      type: edge.type || 'smoothstep',
      kind: edge.kind || 'input',
    })),
    viewport: {
      x: finite(viewport?.x),
      y: finite(viewport?.y),
      zoom: Math.min(4, Math.max(0.1, finite(viewport?.zoom, 1))),
    },
  };
}

export function restorePrototype(value) {
  if (!value || value.schema !== 1 || !Array.isArray(value.nodes) || !Array.isArray(value.edges)) return null;
  return {
    nodes: value.nodes.map((node) => ({ ...node, selected: false, data: { ...node.data } })),
    edges: value.edges.map((edge) => ({ ...edge, selected: false })),
    viewport: {
      x: finite(value.viewport?.x),
      y: finite(value.viewport?.y),
      zoom: Math.min(4, Math.max(0.1, finite(value.viewport?.zoom, 1))),
    },
  };
}
