import { Unzip, UnzipInflate, strFromU8, strToU8, zip } from 'fflate';
import { encodeNodeScene, mediaIdentity } from './node-scene-codec.js';
import { nextPrototypeId, worldPosition } from './canvas-model.js';

export const WORKFLOW_FORMAT = 'pa-node-workflow-v1';
const MAX_BYTES = 128 * 1024 * 1024, MAX_JSON = 8 * 1024 * 1024, MAX_ENTRIES = 201;
const entryName = name => name === 'workflow.json' || /^assets\/[A-Za-z0-9_-]{1,128}\.(png|jpg|webp|mp4|webm|mp3|wav|flac|ogg|opus)$/.test(name);
const hash = async bytes => Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256', bytes)), b => b.toString(16).padStart(2, '0')).join('');
const identities = scene => [...new Map(scene.nodes.flatMap(node => [node.data.media, ...(node.data.outputs || []), ...(node.data.draft?.references || [])]).filter(Boolean).map(media => [media.asset_id, media])).values()];

export function workflowScene(nodes, edges, viewport, metadata, selectedIds = []) {
  const selected = new Set(selectedIds);
  if (!selected.size) nodes.forEach(node => selected.add(node.id));
  for (let previous = -1; previous !== selected.size;) {
    previous = selected.size; nodes.filter(node => selected.has(node.parentId)).forEach(node => selected.add(node.id));
    // Carry the explicit inputs needed to use the exported draft. Flow edges
    // are history/organization, never a reason to copy unrelated ancestors.
    edges.filter(edge => selected.has(edge.target) && (edge.kind || 'input') === 'input').forEach(edge => selected.add(edge.source));
    for (const node of nodes.filter(node => selected.has(node.id))) {
      for (const ref of node.data.draft?.references || []) {
        const source = ref.source_element_id ? nodes.find(candidate => candidate.id === ref.source_element_id) : nodes.find(candidate => candidate.data.media?.asset_id === ref.asset_id);
        if (source) selected.add(source.id);
      }
    }
  }
  const exported = nodes.filter(node => selected.has(node.id)).map(node => {
    const parentSelected = node.parentId && selected.has(node.parentId);
    return { ...node, parentId: parentSelected ? node.parentId : undefined,
      position: parentSelected ? node.position : worldPosition(node, nodes), data: { ...node.data, execution: undefined } };
  });
  return encodeNodeScene(exported, edges.filter(edge => selected.has(edge.source) && selected.has(edge.target)), viewport, metadata);
}

export async function exportWorkflow({ scene, api, includeResources = false }) {
  const canonical = await api.validateNodeWorkflow(scene);
  const manifest = { format: WORKFLOW_FORMAT, scene: canonical, assets: [] };
  const files = {}; let total = 0;
  if (includeResources) {
    const refs = identities(canonical);
    if (refs.length > 200) throw new Error('资源包最多包含 200 个素材；可导出 JSON 引用或分组选中导出');
    const extensions = { 'image/png': 'png', 'image/jpeg': 'jpg', 'image/webp': 'webp', 'video/mp4': 'mp4', 'video/webm': 'webm', 'audio/mpeg': 'mp3', 'audio/wav': 'wav', 'audio/x-wav': 'wav', 'audio/flac': 'flac', 'audio/ogg': 'ogg', 'audio/opus': 'opus' };
    for (const ref of refs) {
      const blob = await api.getAssetBlob(ref.asset_id); total += blob.size;
      if (total > MAX_BYTES - MAX_JSON) throw new Error('资源包超过 120 MiB；原素材保留，请分组导出或使用 JSON 引用');
      const extension = extensions[blob.type.split(';')[0]];
      if (!extension) throw new Error(`不支持打包这个媒体类型：${blob.type}`);
      const path = `assets/${ref.asset_id}.${extension}`;
      if (!entryName(path)) throw new Error('素材身份无法安全导出');
      const bytes = new Uint8Array(await blob.arrayBuffer()); files[path] = [bytes, { level: 0 }];
      manifest.assets.push({ asset_id: ref.asset_id, path, mime: blob.type, sha256: await hash(bytes), name: ref.name || `${ref.asset_id}.${extension}` });
    }
  }
  const json = strToU8(JSON.stringify(manifest, null, 2));
  if (json.length > MAX_JSON) throw new Error('工作流元数据超过 8 MiB');
  if (!includeResources) return new Blob([json], { type: 'application/json' });
  files['workflow.json'] = [json, { level: 0 }];
  const bytes = await new Promise((resolve, reject) => zip(files, { level: 0 }, (error, value) => error ? reject(error) : resolve(value)));
  return new Blob([bytes], { type: 'application/zip' });
}

// Stream small compressed chunks and enforce ACTUAL expanded bytes as well as
// announced sizes. Never extract archive paths to disk; no traversal/symlinks.
export function readWorkflowZip(bytes, limits = {}) {
  const maxBytes = limits.maxBytes || MAX_BYTES, maxEntries = limits.maxEntries || MAX_ENTRIES;
  if (bytes.length > maxBytes) throw new Error('资源包超过大小限制');
  if (bytes.length < 22 || bytes[0] !== 80 || bytes[1] !== 75) throw new Error('不是有效的 ZIP 工作流');
  let hasEnd = false;
  for (let index = bytes.length - 22; index >= Math.max(0, bytes.length - 65557); index--) {
    if (bytes[index] === 80 && bytes[index + 1] === 75 && bytes[index + 2] === 5 && bytes[index + 3] === 6) { hasEnd = true; break; }
  }
  if (!hasEnd) throw new Error('资源包不完整，当前画布未改动');
  const files = new Map(); let count = 0, total = 0, failure;
  const reader = new Unzip(file => {
    if (!entryName(file.name) || files.has(file.name) || ++count > maxEntries || (file.originalSize || 0) > maxBytes || ![0, 8].includes(file.compression)) {
      failure = new Error('资源包含不安全路径、重复条目或超限媒体'); file.terminate(); return;
    }
    const chunks = []; let size = 0; files.set(file.name, null);
    file.ondata = (error, data, final) => {
      if (failure) return;
      if (error) { failure = error; return; }
      total += data.length; size += data.length;
      if (total > maxBytes || file.name === 'workflow.json' && size > MAX_JSON) { failure = new Error('资源包解压超过限制'); file.terminate(); return; }
      chunks.push(data);
      if (final) { const joined = new Uint8Array(size); let offset = 0; for (const chunk of chunks) { joined.set(chunk, offset); offset += chunk.length; } files.set(file.name, joined); }
    };
    file.start();
  });
  reader.register(UnzipInflate);
  for (let offset = 0; offset < bytes.length && !failure; offset += 1024) reader.push(bytes.subarray(offset, offset + 1024), offset + 1024 >= bytes.length);
  if (failure) throw failure;
  if (!files.get('workflow.json') || [...files.values()].some(value => !value)) throw new Error('资源包不完整');
  return files;
}

export async function importWorkflow(file, { api, canvasAdapter, position = { x: 0, y: 0 } }) {
  if (file.size > MAX_BYTES) throw new Error('工作流文件超过 128 MiB');
  const bytes = new Uint8Array(await file.arrayBuffer());
  const zipped = file.name.toLowerCase().endsWith('.zip');
  const files = zipped ? readWorkflowZip(bytes) : new Map();
  if (!zipped && bytes.length > MAX_JSON) throw new Error('工作流 JSON 超过 8 MiB');
  const manifest = JSON.parse(strFromU8(zipped ? files.get('workflow.json') : bytes));
  if (manifest.format !== WORKFLOW_FORMAT || !Array.isArray(manifest.assets) || manifest.assets.length > 200) throw new Error('不是兼容的 PA 工作流；当前画布未改动');
  // Validate before importing any asset or rendering anything from the file.
  const scene = await api.validateNodeWorkflow(manifest.scene);
  const mapped = new Map(), resources = new Map();
  for (const item of manifest.assets) {
    if (!item || !entryName(item.path) || item.path === 'workflow.json' || resources.has(item.asset_id) || !/^[a-f0-9]{64}$/.test(item.sha256)) throw new Error('工作流资源清单无效');
    const data = files.get(item.path);
    if (!data || await hash(data) !== item.sha256) throw new Error('工作流资源缺失或 hash 不匹配');
    resources.set(item.asset_id, { item, data });
  }
  for (const ref of identities(scene)) {
    const resource = resources.get(ref.asset_id);
    if (resource) {
      const imported = await canvasAdapter.importMedia([new File([resource.data], resource.item.name, { type: resource.item.mime })]);
      if (imported.errors.length || imported.assets.length !== 1) throw new Error(`资源导入未完成：${imported.errors[0]?.message || ref.asset_id}；已导入素材保留，当前画布未改动`);
      // Portable resources are imported as sources. Never forge the old Task,
      // Result/receipt or lineage identity in another ledger.
      mapped.set(ref.asset_id, imported.assets[0].media);
    } else {
      const response = await api.getAsset(ref.asset_id).catch(() => { throw new Error(`缺少资源 ${ref.name || ref.asset_id}；请使用包含资源的 ZIP，当前画布未改动`); });
      mapped.set(ref.asset_id, mediaIdentity(response.asset || response));
    }
  }
  const ids = new Map(scene.nodes.map(node => [node.id, nextPrototypeId('import')]));
  const roots = scene.nodes.filter(node => !node.parentId);
  const minX = Math.min(0, ...roots.map(node => node.position.x)), minY = Math.min(0, ...roots.map(node => node.position.y));
  const nodes = scene.nodes.map(node => ({ ...node, id: ids.get(node.id), selected: !node.parentId,
    ...(node.parentId ? { parentId: ids.get(node.parentId) } : {}),
    position: node.parentId ? node.position : { x: node.position.x - minX + position.x, y: node.position.y - minY + position.y },
    data: { ...node.data, execution: undefined,
      ...(node.data.media ? { media: mapped.get(node.data.media.asset_id) } : {}),
      ...(node.data.outputs ? { outputs: node.data.outputs.map(media => mapped.get(media.asset_id)) } : {}),
      ...(node.data.draft ? { draft: { ...node.data.draft, references: (node.data.draft.references || []).map(media => ({ ...mapped.get(media.asset_id), ...(media.source_element_id ? { source_element_id: ids.get(media.source_element_id) } : {}) })) } } : {}),
    },
  }));
  const edges = scene.edges.map(edge => ({ ...edge, id: nextPrototypeId('edge'), source: ids.get(edge.source), target: ids.get(edge.target) }));
  const templates = (scene.metadata?.templates || []).map(template => ({ ...template, id: nextPrototypeId('template') }));
  return { nodes, edges, templates };
}
