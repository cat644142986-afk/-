import { decodeNodeScene, encodeNodeScene, hydrateNodeMedia, mediaIdentity, sceneSignature } from './node-scene-codec.js';
import { createVideoImportDescriptor, partitionSpatialImportFiles } from '../../../src/js/spatial-media-import.js';

export const LEGACY_SCENE_KEY = 'product-atelier:canvas-replacement:v1';
export const CANVAS_POINTER_KEY = 'product-atelier:node-canvas:pointer:v2';

function newId(prefix) { return `${prefix}:${globalThis.crypto?.randomUUID?.() || `${Date.now()}-${Math.random()}`}`; }

// Thin adapter to the existing PA Asset + spatial Canvas API. No second DB.
export function createNodeCanvasAdapter({ api, storage, onStatus = () => {}, startupTimeoutMs = 30000, pause = (ms) => new Promise((resolve) => setTimeout(resolve, ms)) }) {
  let record = null;
  let chain = Promise.resolve();
  let pendingSave = null;
  let lastSignature = '';
  let loadPromise;

  const remember = (value) => {
    record = value;
    lastSignature = sceneSignature(value.scene);
    storage.setItem(CANVAS_POINTER_KEY, JSON.stringify({ canvasId: value.id }));
    return value;
  };

  async function recoverLegacy(value) {
    if (!value || value.schema !== 1 || !Array.isArray(value.nodes)) throw new Error('旧原型恢复点无效；旧副本未改动');
    const nodes = [];
    for (const node of value.nodes) {
      const { src, naturalWidth, naturalHeight, ...data } = node.data || {};
      let media;
      if (src) {
        if (!String(src).startsWith('data:image/')) throw new Error('旧媒体不是受支持的本地恢复格式');
        const blob = await (await fetch(src)).blob();
        const file = new File([blob], data.name || `${node.id}.png`, { type: blob.type });
        const response = await api.importAssets([file], 'product');
        if (response.errors?.length || response.assets?.length !== 1) throw new Error('旧媒体导入失败；旧副本未改动');
        media = mediaIdentity(response.assets[0]);
      }
      nodes.push({ ...node, data: { name: data.name || '对象', ...(media ? { media } : {}) } });
    }
    return encodeNodeScene(nodes, value.edges || [], value.viewport);
  }

  async function load() {
    if (loadPromise) return loadPromise;
    loadPromise = (async () => {
      onStatus({ phase: 'loading', message: '正在打开画布' });
      // A packaged WebView can mount before the sidecar finishes starting.
      // Only retry the read-only health handshake, never create/execute calls.
      if (api.checkHealth) {
        const deadline = Date.now() + startupTimeoutMs;
        while (!(await api.checkHealth()).ok) {
          if (Date.now() >= deadline) throw new Error('本地服务尚未就绪；请重新打开，已有内容保留');
          await pause(300);
        }
      }
      const pointer = JSON.parse(storage.getItem(CANVAS_POINTER_KEY) || 'null');
      if (pointer?.canvasId) {
        remember(await api.openSpatialCanvas(pointer.canvasId));
      } else {
        const legacy = storage.getItem(LEGACY_SCENE_KEY);
        const scene = legacy ? await recoverLegacy(JSON.parse(legacy)) : encodeNodeScene([], [], { x: 0, y: 0, zoom: 1 });
        // Persist only a request identity before create. A dropped response can
        // be replayed without creating a second document. Keep legacy intact.
        const createRequestId = pointer?.createRequestId || newId('node-create');
        storage.setItem(CANVAS_POINTER_KEY, JSON.stringify({ createRequestId }));
        const created = await api.createSpatialCanvas({ name: '未命名画布', client_request_id: createRequestId, scene });
        const readBack = await api.openSpatialCanvas(created.id);
        if (sceneSignature(readBack.scene) !== sceneSignature(scene)) throw new Error('画布读回不一致；旧副本未改动');
        remember(readBack);
      }
      const document = decodeNodeScene(record.scene);
      document.nodes = await hydrateNodeMedia(document.nodes, api);
      onStatus({ phase: 'saved', message: `已保存 · 版本 ${record.current_revision}` });
      return { ...document, record };
    })().catch((error) => { loadPromise = null; onStatus({ phase: 'error', message: error.message }); throw error; });
    return loadPromise;
  }

  async function saveDocument(scene) {
    if (!record) throw new Error('画布尚未打开；修改仍保留在当前窗口');
    const signature = sceneSignature(scene);
    if (!pendingSave && signature === lastSignature) return record;
    // Complete/replay an uncertain write with its ORIGINAL request + revision.
    if (pendingSave) {
      const completed = await api.saveSpatialCanvasScene(record.id, pendingSave);
      remember(completed);
      pendingSave = null;
      if (signature === lastSignature) return record;
    }
    pendingSave = { expected_revision: record.current_revision, client_request_id: newId('node-save'), scene };
    const saved = await api.saveSpatialCanvasScene(record.id, pendingSave);
    remember(saved);
    pendingSave = null;
    return saved;
  }

  function save(nodes, edges, viewport, metadata = record?.scene?.metadata) {
    const scene = encodeNodeScene(nodes, edges, viewport, metadata);
    const next = chain.catch(() => {}).then(async () => {
      onStatus({ phase: 'saving', message: '正在保存' });
      try {
        const saved = await saveDocument(scene);
        onStatus({ phase: 'saved', message: `已保存 · 版本 ${saved.current_revision}` });
        return saved;
      } catch (error) {
        onStatus({ phase: 'error', message: error.status === 409 ? '画布存在另一份修改；当前内容已保留，未覆盖远端' : `保存失败 · ${error.message}` });
        throw error;
      }
    });
    chain = next;
    return next;
  }

  async function importMedia(files) {
    const audio = Array.from(files).filter((file) => /\.(mp3|wav|ogg|opus|flac)$/i.test(file.name));
    const partition = partitionSpatialImportFiles(Array.from(files).filter((file) => !audio.includes(file)));
    const response = partition.images.length ? await api.importAssets(partition.images, 'product') : { assets: [], errors: [] };
    for (const file of partition.videos) {
      try {
        const descriptor = await createVideoImportDescriptor(file);
        const imported = await api.importVideoAsset(file, descriptor.cover, descriptor, 'product');
        response.assets.push(imported.asset || imported);
      } catch (error) { response.errors.push({ name: file.name, message: error.message }); }
    }
    for (const file of audio) {
      try { const imported = await api.importAudioAsset(file, 'product'); response.assets.push(imported.asset || imported); }
      catch (error) { response.errors.push({ name: file.name, message: error.message }); }
    }
    const assets = await Promise.all((response.assets || []).map(async (asset) => ({
      name: asset.name, kind: asset.kind || 'image', src: await api.getAssetContentUrl(asset.id),
      naturalWidth: asset.width, naturalHeight: asset.height, media: mediaIdentity(asset),
    })));
    return { assets, errors: [...(response.errors || []), ...(partition.rejected || []).map((item) => ({ name: item.file?.name, message: item.message || item.reason }))] };
  }

  async function open(canvasId) {
    await chain;
    const next = await api.openSpatialCanvas(canvasId);
    const document = decodeNodeScene(next.scene); // Validate BEFORE changing the pointer.
    document.nodes = await hydrateNodeMedia(document.nodes, api);
    remember(next); loadPromise = Promise.resolve({ ...document, record });
    onStatus({ phase: 'saved', message: `已保存 · 版本 ${record.current_revision}` });
    return { ...document, record };
  }
  async function create(name = '未命名画布') {
    await chain;
    const next = await api.createSpatialCanvas({ name, client_request_id: newId('node-create'), scene: encodeNodeScene([], [], { x: 0, y: 0, zoom: 1 }) });
    return open(next.id);
  }

  return { load, open, create, save, importMedia, flush: () => chain, get: () => record };
}
