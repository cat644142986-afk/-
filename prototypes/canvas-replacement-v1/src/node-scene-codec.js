// The scene contains PA identities, not media bytes or runtime view state.
export const NODE_SCENE_KIND = 'pa-node-canvas';
export const NODE_SCENE_VERSION = 2;
const DATA_FIELDS = ['name', 'media', 'outputs', 'active_output', 'draft', 'text', 'separator', 'llm', 'plan', 'clips', 'execution'];
const MEDIA_FIELDS = ['asset_id', 'result_id', 'task_id', 'product_profile_version_id', 'lineage_parent_id', 'role', 'kind', 'name', 'width', 'height', 'mime', 'sha256', 'duration_seconds', 'source_element_id'];
const clone = (value) => JSON.parse(JSON.stringify(value));
const pick = (value, keys) => Object.fromEntries(keys.filter((key) => value?.[key] !== undefined).map((key) => [key, clone(value[key])]));

export function mediaIdentity(asset) {
  return pick({ ...asset, asset_id: asset.asset_id || asset.id,
    result_id: asset.result_id || (String(asset.role || '').startsWith('result_') ? asset.id : undefined),
  }, MEDIA_FIELDS);
}

export function encodeNodeScene(nodes, edges, viewport, metadata) {
  return {
    schema_version: NODE_SCENE_VERSION, kind: NODE_SCENE_KIND,
    nodes: nodes.map((node) => {
      const data = pick(node.data, DATA_FIELDS);
      if (data.media) data.media = mediaIdentity(data.media);
      if (data.outputs) data.outputs = data.outputs.map(mediaIdentity);
      if (data.draft?.references) data.draft.references = data.draft.references.map(mediaIdentity);
      return {
        id: node.id, type: node.type, position: { x: node.position.x, y: node.position.y },
        ...(node.parentId ? { parentId: node.parentId } : {}),
        style: { width: node.width || node.measured?.width || node.style?.width || 316, height: node.height || node.measured?.height || node.style?.height || 210 },
        data,
      };
    }),
    edges: edges.map((edge) => ({ ...pick(edge, ['id', 'source', 'target', 'sourceHandle', 'targetHandle']), type: 'reference', kind: edge.kind || 'input' })),
    viewport: { x: viewport?.x || 0, y: viewport?.y || 0, zoom: viewport?.zoom || 1 },
    ...(metadata ? { metadata: clone(metadata) } : {}),
  };
}

export function decodeNodeScene(scene) {
  if (scene?.schema_version !== NODE_SCENE_VERSION || scene.kind !== NODE_SCENE_KIND || !Array.isArray(scene.nodes) || !Array.isArray(scene.edges)) {
    throw new Error('这不是兼容的节点画布；原画布没有被覆盖');
  }
  return { nodes: clone(scene.nodes).map((node) => ({ ...node, selected: false })), edges: clone(scene.edges), viewport: { ...scene.viewport }, metadata: clone(scene.metadata || {}) };
}

export function sceneSignature(scene) {
  return JSON.stringify(scene, (_, value) => value && typeof value === 'object' && !Array.isArray(value)
    ? Object.fromEntries(Object.keys(value).sort().map((key) => [key, value[key]])) : value);
}

export async function hydrateNodeMedia(nodes, api) {
  const ids = [...new Set(nodes.flatMap((node) => [node.data.media, ...node.data.outputs || [], ...node.data.draft?.references || []]).filter(Boolean).map((media) => media.asset_id))];
  const entries = await Promise.all(ids.map(async (id) => {
    try {
      const response = await api.getAsset(id);
      const asset = response.asset || response;
      return [id, { asset, src: await api.getAssetContentUrl(id), previewUrl: await api.getAssetThumbnailUrl(id, 512) }];
    } catch (error) { return [id, { error: error.message }]; }
  }));
  const media = new Map(entries);
  const referenceViews = Object.fromEntries(entries);
  return nodes.map((node) => {
    const identity = node.data.media;
    const resolved = media.get(identity?.asset_id);
    return { ...node, data: { ...node.data, src: resolved?.src || '', previewUrl: resolved?.previewUrl || '',
      mediaError: resolved?.error || '', naturalWidth: identity?.width, naturalHeight: identity?.height,
      aspectRatio: identity?.width && identity?.height ? identity.width / identity.height : undefined,
      kind: identity?.result_id ? 'result' : 'asset',
      referenceViews,
    } };
  });
}
