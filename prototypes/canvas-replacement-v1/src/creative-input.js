import { nextPrototypeId } from './canvas-model.js';
import { mediaIdentity, sceneSignature } from './node-scene-codec.js';

export const DEFAULT_NODE_DRAFT = Object.freeze({ prompt: '', mode: 'image', ratio: '1:1', resolution: '2k', quality: 'auto', count: 1, model: '', references: [], duration_seconds: 5 });
export function nodeDraft(node) { return { ...DEFAULT_NODE_DRAFT, ...node?.data?.draft, references: [...node?.data?.draft?.references || []] }; }

export function createPromptNode(position) {
  return { id: nextPrototypeId('prompt'), type: 'prompt', position, selected: true, style: { width: 300, height: 210 },
    data: { name: '提示词', text: '', separator: '\n---\n', draft: { ...DEFAULT_NODE_DRAFT } } };
}

export function patchNodeDraft(nodes, nodeId, patch) {
  return nodes.map((node) => node.id === nodeId ? { ...node, data: { ...node.data, draft: { ...nodeDraft(node), ...patch } } } : node);
}

// Resolve only the chosen upstream graph. Never consume unrelated history,
// hidden selections, all Canvas images, or an original ancestor of a Result.
export function resolveCreativeInput(nodes, edges, targetId) {
  const byId = new Map(nodes.map((node) => [node.id, node]));
  const target = byId.get(targetId);
  if (!target) throw new Error('创作对象已移除；已提交任务仍可从日志恢复');
  const draft = nodeDraft(target);
  const visited = new Set([targetId]), prompts = [], references = [], referenceIds = new Set();
  const pending = edges.filter((edge) => edge.target === targetId && (edge.kind || 'input') === 'input').map((edge) => edge.source);
  const addReference = (media, sourceNodeId = '') => {
    if (!media?.asset_id || referenceIds.has(media.asset_id)) return;
    referenceIds.add(media.asset_id);
    references.push({ ...mediaIdentity(media), sourceNodeId });
  };
  while (pending.length) {
    const id = pending.shift();
    if (visited.has(id)) continue;
    visited.add(id);
    const node = byId.get(id);
    if (!node) throw new Error('参考连接的来源已移除');
    if (node.type === 'prompt') {
      if (node.data?.text?.trim()) prompts.push({ nodeId: id, text: node.data.text });
      pending.push(...edges.filter((edge) => edge.target === id && (edge.kind || 'input') === 'input').map((edge) => edge.source));
    } else if (node.data?.media) addReference(node.data.media, id);
  }
  for (const media of draft.references) {
    const candidates = nodes.filter((node) => node.data?.media?.asset_id === media.asset_id);
    const matching = media.source_element_id ? candidates.find(node => node.id === media.source_element_id)
      : candidates.length === 1 ? candidates[0] : null;
    addReference(media, matching?.id || '');
  }
  // An existing media with no explicit reference is the exact editing input.
  // It is not an excuse to find an original source through its lineage.
  if (!references.length && target.data?.media && draft.useCurrentMedia !== false) addReference(target.data.media, target.id);
  const text = [...prompts.map((item) => item.text), draft.prompt].filter((value) => value?.trim()).join('\n\n');
  const taskKind = draft.mode === 'video' ? 'image-to-video' : references.length ? 'reference-generate' : 'text-to-image';
  const semantic = { targetId, text, references: references.map((ref) => ({ ...ref })), taskKind,
    model: draft.mode === 'video' ? 'offline-preview-v1' : draft.model, ratio: draft.ratio,
    resolution: draft.mode === 'video' ? 'offline-fixture' : draft.resolution, quality: draft.quality, count: draft.count,
    ...(draft.mode === 'video' ? { duration_seconds: draft.duration_seconds } : {}) };
  return { ...semantic, draft, prompts, signature: sceneSignature(semantic) };
}

export function arrangeSelection(nodes, selectedIds, direction = 'horizontal') {
  const selected = new Set(selectedIds);
  const roots = nodes.filter((node) => selected.has(node.id) && !node.parentId);
  if (roots.length < 2) return nodes;
  let x = Math.min(...roots.map((node) => node.position.x));
  let y = Math.min(...roots.map((node) => node.position.y));
  const positions = new Map();
  for (const node of roots.sort((a, b) => a.position.x - b.position.x || a.position.y - b.position.y)) {
    positions.set(node.id, { x, y });
    if (direction === 'horizontal') x += (node.measured?.width || node.style?.width || 316) + 64;
    else y += (node.measured?.height || node.style?.height || 210) + 64;
  }
  return nodes.map((node) => positions.has(node.id) ? { ...node, position: positions.get(node.id) } : node);
}
