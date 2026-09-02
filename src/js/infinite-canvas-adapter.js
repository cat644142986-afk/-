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

function clone(value) {
  if (typeof structuredClone === 'function') return structuredClone(value);
  return JSON.parse(JSON.stringify(value));
}

function cleanName(value, fallback = '未命名画布') {
  const name = String(value || '').trim().replace(/\s+/g, ' ');
  return name.slice(0, 60) || fallback;
}

function sceneSummary(scene) {
  const elements = Array.from(scene?.elements || []).filter((element) => !element?.isDeleted);
  return {
    element_count: elements.length,
    image_count: elements.filter((element) => element.type === 'image').length,
    frame_count: elements.filter((element) => element.type === 'frame').length,
  };
}

export function createMemorySpatialCanvasAdapter({
  now = () => new Date(),
  idFactory = () => globalThis.crypto?.randomUUID?.() || `${Date.now()}-${Math.random().toString(16).slice(2)}`,
} = {}) {
  const records = new Map();

  function snapshot(record) {
    return record ? clone({ ...record, summary: sceneSummary(record.scene) }) : null;
  }

  function create({ name } = {}) {
    const timestamp = now().toISOString();
    const record = {
      id: `spatial:${String(idFactory()).toLowerCase()}`,
      name: cleanName(name),
      created_at: timestamp,
      updated_at: timestamp,
      last_opened_at: timestamp,
      scene: { elements: [], appState: clone(DEFAULT_APP_STATE), files: {} },
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
    record.scene = {
      elements: clone(Array.from(scene?.elements || [])),
      appState: clone({ ...DEFAULT_APP_STATE, ...(scene?.appState || {}) }),
      files: {},
    };
    record.updated_at = now().toISOString();
    return snapshot(record);
  }

  return { create, get, list, open, rename, updateScene, kind: 'memory' };
}

export { cleanName as normalizeSpatialCanvasName, sceneSummary as spatialSceneSummary };
