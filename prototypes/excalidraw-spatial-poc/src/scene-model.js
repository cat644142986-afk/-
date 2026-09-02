export const POC_STORAGE_KEY = 'product-atelier:excalidraw-spatial-poc:v1';
export const POC_IMAGE_COUNT = 200;
export const POC_VIDEO_COUNT = 20;
export const POC_FRAME_COUNT = 5;

export const REQUIRED_BUSINESS_KEYS = Object.freeze([
  'asset_id',
  'result_id',
  'task_id',
  'product_profile_version_id',
  'lineage_parent_id',
]);

const FRAME_WIDTH = 1040;
const FRAME_HEIGHT = 1540;
const FRAME_GAP = 160;
const CREATED_AT = 1788314400000;
const PROXY_COLORS = Object.freeze([
  ['#fff0e9', '#ff6b43'],
  ['#eaf4f0', '#2f7d63'],
  ['#eef2f7', '#58718d'],
  ['#f7f1df', '#9a7725'],
  ['#f1ecf6', '#735b87'],
]);

function pad(value, width = 3) {
  return String(value).padStart(width, '0');
}

function businessData(index, kind, overrides = {}) {
  const branchParent = kind === 'image' && index % 5 === 0
    ? `asset-poc-${pad(index - 1)}`
    : null;
  return {
    asset_id: `${kind === 'video' ? 'video' : 'asset'}-poc-${pad(index)}`,
    result_id: kind === 'image' && index % 5 === 0 ? `result-poc-${pad(index)}` : null,
    task_id: `task-poc-${pad(Math.ceil(index / 4), 2)}`,
    product_profile_version_id: 'product-profile-poc-v1',
    lineage_parent_id: branchParent,
    node_kind: kind,
    ...overrides,
  };
}

function proxySvg(index) {
  const [surface, accent] = PROXY_COLORS[index % PROXY_COLORS.length];
  const label = pad(index);
  return `<svg xmlns="http://www.w3.org/2000/svg" width="320" height="210" viewBox="0 0 320 210"><rect width="320" height="210" fill="${surface}"/><rect x="22" y="22" width="276" height="166" rx="12" fill="#fffefd" stroke="${accent}" stroke-width="3"/><circle cx="160" cy="88" r="42" fill="${accent}" opacity=".18"/><rect x="126" y="54" width="68" height="74" rx="18" fill="${accent}"/><text x="160" y="166" text-anchor="middle" font-family="Segoe UI, sans-serif" font-size="22" font-weight="700" fill="#20242a">SKU ${label}</text></svg>`;
}

function utf8ToBase64(value) {
  const bytes = new TextEncoder().encode(value);
  let binary = '';
  for (let index = 0; index < bytes.length; index += 1) binary += String.fromCharCode(bytes[index]);
  return btoa(binary);
}

export function createProxyBinaryFile(index, fileId = `proxy-file-${pad(index)}`) {
  return {
    id: fileId,
    dataURL: `data:image/svg+xml;base64,${utf8ToBase64(proxySvg(index))}`,
    mimeType: 'image/svg+xml',
    created: CREATED_AT,
    lastRetrieved: CREATED_AT,
  };
}

export function createPocSceneSkeletons({
  imageCount = POC_IMAGE_COUNT,
  videoCount = POC_VIDEO_COUNT,
} = {}) {
  const skeletons = [];
  const files = {};
  const frameChildren = Array.from({ length: POC_FRAME_COUNT }, () => []);
  const imagesPerFrame = Math.ceil(imageCount / POC_FRAME_COUNT);
  const videosPerFrame = Math.ceil(videoCount / POC_FRAME_COUNT);

  for (let frameIndex = 0; frameIndex < POC_FRAME_COUNT; frameIndex += 1) {
    const frameId = `frame-poc-${frameIndex + 1}`;
    const frameX = frameIndex * (FRAME_WIDTH + FRAME_GAP);
    const titleId = `frame-title-${frameIndex + 1}`;
    frameChildren[frameIndex].push(titleId);
    skeletons.push({
      id: titleId,
      type: 'text',
      text: `方案 ${String.fromCharCode(65 + frameIndex)} · 商品视觉方向`,
      x: frameX + 36,
      y: 34,
      fontSize: 28,
      fontFamily: 2,
      strokeColor: '#20242a',
      roughness: 0,
      frameId,
      customData: { node_kind: 'label', frame_id: frameId },
    });

    for (let localIndex = 0; localIndex < imagesPerFrame; localIndex += 1) {
      const index = frameIndex * imagesPerFrame + localIndex + 1;
      if (index > imageCount) break;
      const elementId = `image-poc-${pad(index)}`;
      const fileId = `proxy-file-${pad(index)}`;
      const column = localIndex % 5;
      const row = Math.floor(localIndex / 5);
      const x = frameX + 38 + column * 198;
      const y = 100 + row * 140;
      frameChildren[frameIndex].push(elementId);
      files[fileId] = createProxyBinaryFile(index, fileId);
      skeletons.push({
        id: elementId,
        type: 'image',
        fileId,
        status: 'saved',
        x,
        y,
        width: 160,
        height: 105,
        angle: 0,
        roughness: 0,
        strokeColor: '#d1c8b5',
        backgroundColor: 'transparent',
        fillStyle: 'solid',
        strokeStyle: 'solid',
        groupIds: localIndex % 8 < 2 ? [`comparison-group-${frameIndex + 1}-${Math.floor(localIndex / 8) + 1}`] : [],
        frameId,
        customData: businessData(index, 'image', {
          proxy_ref: `synthetic://proxy/asset-poc-${pad(index)}`,
          original_pixel_width: 4096,
          original_pixel_height: 4096,
        }),
      });

      if (index % 5 === 0) {
        const arrowId = `lineage-arrow-${pad(index)}`;
        frameChildren[frameIndex].push(arrowId);
        skeletons.push({
          id: arrowId,
          type: 'arrow',
          x: x - 30,
          y: y + 52,
          width: 24,
          height: 0,
          points: [[0, 0], [24, 0]],
          strokeColor: '#ff6b43',
          strokeWidth: 2,
          strokeStyle: 'solid',
          roughness: 0,
          endArrowhead: 'arrow',
          frameId,
          customData: {
            node_kind: 'lineage',
            lineage_parent_id: `asset-poc-${pad(index - 1)}`,
            lineage_child_id: `asset-poc-${pad(index)}`,
          },
        });
      }
    }

    for (let localIndex = 0; localIndex < videosPerFrame; localIndex += 1) {
      const index = frameIndex * videosPerFrame + localIndex + 1;
      if (index > videoCount) break;
      const elementId = `video-poc-${pad(index)}`;
      const column = localIndex % 4;
      frameChildren[frameIndex].push(elementId);
      skeletons.push({
        id: elementId,
        type: 'embeddable',
        x: frameX + 38 + column * 246,
        y: 1290,
        width: 220,
        height: 160,
        link: `product-atelier-video://video-poc-${pad(index)}`,
        strokeColor: '#20242a',
        backgroundColor: '#181c20',
        fillStyle: 'solid',
        strokeStyle: 'solid',
        roughness: 0,
        frameId,
        customData: businessData(index, 'video', {
          cover_ref: `synthetic://video-cover/video-poc-${pad(index)}`,
          duration_seconds: 5 + (index % 4),
          pixel_width: index % 2 ? 1080 : 1920,
          pixel_height: index % 2 ? 1920 : 1080,
          status: index % 5 === 0 ? 'queued' : 'ready',
        }),
      });
    }
  }

  for (let frameIndex = 0; frameIndex < POC_FRAME_COUNT; frameIndex += 1) {
    skeletons.push({
      id: `frame-poc-${frameIndex + 1}`,
      type: 'frame',
      name: `方案 ${String.fromCharCode(65 + frameIndex)}`,
      x: frameIndex * (FRAME_WIDTH + FRAME_GAP),
      y: 0,
      width: FRAME_WIDTH,
      height: FRAME_HEIGHT,
      children: frameChildren[frameIndex],
      strokeColor: '#b9b1a4',
      backgroundColor: 'transparent',
      strokeWidth: 1,
      strokeStyle: 'solid',
      roughness: 0,
      locked: false,
      customData: {
        node_kind: 'frame',
        frame_index: frameIndex,
      },
    });
  }

  return { skeletons, files };
}

function persistedAppState(appState = {}) {
  return {
    scrollX: Number(appState.scrollX || 0),
    scrollY: Number(appState.scrollY || 0),
    zoom: Number(appState.zoom?.value || appState.zoom || 1),
    viewBackgroundColor: String(appState.viewBackgroundColor || '#d4d0cb'),
  };
}

export function validatePersistedScene(payload) {
  if (!payload || payload.schema_version !== 1 || !Array.isArray(payload.elements)) {
    throw new Error('Invalid Product Atelier spatial scene');
  }
  const serialized = JSON.stringify(payload);
  if (/data:/i.test(serialized)) throw new Error('Scene must not contain Data URLs');
  if (/[A-Za-z]:\\\\/.test(serialized)) throw new Error('Scene must not contain absolute Windows paths');

  for (const element of payload.elements) {
    if (!['image', 'embeddable'].includes(element.type)) continue;
    for (const key of REQUIRED_BUSINESS_KEYS) {
      if (!Object.prototype.hasOwnProperty.call(element.customData || {}, key)) {
        throw new Error(`Business reference ${key} is missing from ${element.id}`);
      }
    }
  }
  return payload;
}

export function serializePocScene(elements, appState) {
  const payload = {
    schema_version: 1,
    scene_kind: 'product-atelier-spatial-layout',
    saved_at: new Date().toISOString(),
    app_state: persistedAppState(appState),
    elements: JSON.parse(JSON.stringify(elements.filter((element) => !element.isDeleted))),
  };
  return validatePersistedScene(payload);
}

export function sceneContentFingerprint(elements, appState) {
  return JSON.stringify({
    app_state: persistedAppState(appState),
    elements: elements.filter((element) => !element.isDeleted),
  });
}

export function restorePocScene(raw) {
  const payload = validatePersistedScene(typeof raw === 'string' ? JSON.parse(raw) : raw);
  const files = {};
  for (const element of payload.elements) {
    if (element.type !== 'image' || !element.fileId) continue;
    const match = String(element.customData?.asset_id || '').match(/(\d+)$/);
    const index = Number(match?.[1] || 1);
    files[element.fileId] = createProxyBinaryFile(index, element.fileId);
  }
  return {
    elements: payload.elements,
    files,
    appState: {
      scrollX: payload.app_state.scrollX,
      scrollY: payload.app_state.scrollY,
      zoom: { value: payload.app_state.zoom },
      viewBackgroundColor: payload.app_state.viewBackgroundColor,
      currentItemRoughness: 0,
      currentItemStrokeStyle: 'solid',
      currentItemFillStyle: 'solid',
    },
  };
}

export function sceneMetrics(elements, files = {}) {
  const active = elements.filter((element) => !element.isDeleted);
  return {
    elements: active.length,
    images: active.filter((element) => element.type === 'image').length,
    videos: active.filter((element) => element.type === 'embeddable' && element.customData?.node_kind === 'video').length,
    frames: active.filter((element) => element.type === 'frame').length,
    lineage: active.filter((element) => element.customData?.node_kind === 'lineage').length,
    files: Object.keys(files).length,
  };
}
