const DEFAULT_APP_STATE = Object.freeze({
  viewBackgroundColor: '#d4d0cb',
  currentItemRoughness: 0,
  currentItemStrokeStyle: 'solid',
  currentItemFillStyle: 'solid',
  gridSize: 20,
  gridStep: 5,
  gridModeEnabled: false,
  zoom: { value: 1 },
  scrollX: 0,
  scrollY: 0,
});

const STABLE_APP_STATE_FIELDS = Object.freeze([
  'viewBackgroundColor',
  'currentItemRoughness',
  'currentItemStrokeStyle',
  'currentItemFillStyle',
  'gridSize',
  'gridStep',
  'gridModeEnabled',
  'zoom',
  'scrollX',
  'scrollY',
]);

function clone(value) {
  if (typeof structuredClone === 'function') return structuredClone(value);
  return JSON.parse(JSON.stringify(value));
}

function cleanName(value, fallback = '未命名画布') {
  const name = String(value || '').trim().replace(/\s+/g, ' ');
  return name.slice(0, 60) || fallback;
}

function requestId(prefix) {
  const suffix = globalThis.crypto?.randomUUID?.()
    || `${Date.now()}-${Math.random().toString(16).slice(2)}`;
  return `${prefix}:${String(suffix).toLowerCase()}`;
}

function stableAppState(value) {
  const source = value && typeof value === 'object' ? value : {};
  const state = clone(DEFAULT_APP_STATE);
  STABLE_APP_STATE_FIELDS.forEach((field) => {
    if (Object.hasOwn(source, field)) state[field] = clone(source[field]);
  });
  state.zoom = { value: Number(state.zoom?.value) || 1 };
  return state;
}

function runtimeScene(scene) {
  const source = scene && typeof scene === 'object' ? scene : {};
  return {
    elements: clone(Array.from(source.elements || [])),
    appState: stableAppState(source.appState || source.app_state),
    files: {},
  };
}

function apiScene(scene) {
  const runtime = runtimeScene(scene);
  return {
    schema_version: 1,
    elements: runtime.elements,
    app_state: runtime.appState,
    files: {},
  };
}

function sceneSummary(scene) {
  const elements = Array.from(scene?.elements || []).filter((element) => !element?.isDeleted);
  return {
    element_count: elements.length,
    image_count: elements.filter((element) => element.type === 'image').length,
    video_count: elements.filter((element) => element.type === 'embeddable').length,
    frame_count: elements.filter((element) => element.type === 'frame').length,
  };
}

function normalizeRecord(value) {
  if (!value) return null;
  const record = clone(value);
  if (record.scene) record.scene = runtimeScene(record.scene);
  record.summary = record.summary || sceneSummary(record.scene);
  record.thumbnail = record.thumbnail || {
    ...record.summary,
    elements: Array.from(record.scene?.elements || []).slice(-12).map((element) => ({
      id: element.id,
      type: element.type,
      x: element.x,
      y: element.y,
      width: element.width,
      height: element.height,
    })),
  };
  return record;
}

export function createMemorySpatialCanvasAdapter({
  now = () => new Date(),
  idFactory = () => globalThis.crypto?.randomUUID?.() || `${Date.now()}-${Math.random().toString(16).slice(2)}`,
} = {}) {
  const records = new Map();

  function snapshot(record) {
    return normalizeRecord(record);
  }

  function create({ name } = {}) {
    const timestamp = now().toISOString();
    const record = {
      id: `spatial:${String(idFactory()).toLowerCase()}`,
      name: cleanName(name),
      current_revision: 1,
      current_version_id: `spatial-version:${String(idFactory()).toLowerCase()}`,
      created_at: timestamp,
      updated_at: timestamp,
      last_opened_at: timestamp,
      scene: runtimeScene({ elements: [], appState: DEFAULT_APP_STATE }),
    };
    records.set(record.id, record);
    return snapshot(record);
  }

  function list() {
    return [...records.values()]
      .sort((left, right) => right.last_opened_at.localeCompare(left.last_opened_at))
      .map(snapshot);
  }

  function get(id) {
    return snapshot(records.get(String(id)));
  }

  function open(id) {
    const record = records.get(String(id));
    if (!record) return null;
    record.last_opened_at = now().toISOString();
    return snapshot(record);
  }

  function rename(id, name) {
    const record = records.get(String(id));
    if (!record) return null;
    record.name = cleanName(name, record.name);
    record.updated_at = now().toISOString();
    return snapshot(record);
  }

  function updateScene(id, scene) {
    const record = records.get(String(id));
    if (!record) return null;
    record.scene = runtimeScene(scene);
    record.current_revision += 1;
    record.current_version_id = `${record.id}:version:${record.current_revision}`;
    record.updated_at = now().toISOString();
    return snapshot(record);
  }

  return {
    create,
    get,
    list,
    load: async () => list(),
    open,
    rename,
    updateScene,
    kind: 'memory',
  };
}

export function createApiSpatialCanvasAdapter({ api } = {}) {
  if (!api) throw new TypeError('createApiSpatialCanvasAdapter requires an API client');
  const records = new Map();
  const saveChains = new Map();
  let loaded = false;

  function remember(value) {
    const record = normalizeRecord(value);
    if (record) records.set(record.id, record);
    return normalizeRecord(record);
  }

  function list() {
    return [...records.values()]
      .sort((left, right) => right.last_opened_at.localeCompare(left.last_opened_at))
      .map(normalizeRecord);
  }

  function get(id) {
    return normalizeRecord(records.get(String(id)));
  }

  async function load({ force = false } = {}) {
    if (loaded && !force) return list();
    const response = await api.listSpatialCanvases(100, { timeoutMs: 12000 });
    records.clear();
    Array.from(response?.canvases || []).forEach(remember);
    loaded = true;
    return list();
  }

  async function create({ name } = {}) {
    const record = await api.createSpatialCanvas({
      name: cleanName(name),
      client_request_id: requestId('spatial-create'),
    }, { timeoutMs: 12000 });
    loaded = true;
    return remember(record);
  }

  async function open(id) {
    return remember(await api.openSpatialCanvas(String(id), { timeoutMs: 12000 }));
  }

  async function rename(id, name) {
    const record = await api.renameSpatialCanvas(String(id), {
      name: cleanName(name, get(id)?.name),
    }, { timeoutMs: 12000 });
    return remember(record);
  }

  function updateScene(id, scene) {
    const canvasId = String(id);
    const serializedScene = apiScene(scene);
    const previous = saveChains.get(canvasId) || Promise.resolve();
    const operation = previous.catch(() => {}).then(async () => {
      const current = get(canvasId);
      if (!current) throw new Error(`Unknown spatial canvas: ${canvasId}`);
      try {
        const record = await api.saveSpatialCanvasScene(canvasId, {
          expected_revision: current.current_revision,
          client_request_id: requestId('spatial-scene-save'),
          scene: serializedScene,
        }, { timeoutMs: 15000 });
        return remember(record);
      } catch (error) {
        if (error?.status === 409) {
          try {
            error.current = remember(
              await api.openSpatialCanvas(canvasId, { timeoutMs: 12000 }),
            );
          } catch (_) { /* retain the original conflict */ }
        }
        throw error;
      }
    });
    saveChains.set(canvasId, operation);
    operation.then(
      () => { if (saveChains.get(canvasId) === operation) saveChains.delete(canvasId); },
      () => { if (saveChains.get(canvasId) === operation) saveChains.delete(canvasId); },
    );
    return operation;
  }

  return {
    create,
    get,
    list,
    load,
    open,
    rename,
    updateScene,
    kind: 'sqlite-v8',
  };
}

export {
  apiScene as spatialSceneForApi,
  cleanName as normalizeSpatialCanvasName,
  runtimeScene as spatialSceneForRuntime,
  sceneSummary as spatialSceneSummary,
};
