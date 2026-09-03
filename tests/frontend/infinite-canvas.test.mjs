import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';

import {
  createApiSpatialCanvasAdapter,
  createMemorySpatialCanvasAdapter,
} from '../../src/js/infinite-canvas-adapter.js';

const packageJson = JSON.parse(readFileSync(new URL('../../package.json', import.meta.url), 'utf8'));
const packageLock = JSON.parse(readFileSync(new URL('../../package-lock.json', import.meta.url), 'utf8'));
const html = readFileSync(new URL('../../src/index.html', import.meta.url), 'utf8');
const app = readFileSync(new URL('../../src/js/app.js', import.meta.url), 'utf8');
const workspace = readFileSync(new URL('../../src/js/infinite-canvas-workspace.js', import.meta.url), 'utf8');
const island = readFileSync(new URL('../../src/js/infinite-canvas-island.jsx', import.meta.url), 'utf8');
const itemsSource = readFileSync(new URL('../../src/js/spatial-canvas-items.js', import.meta.url), 'utf8');
const adapterSource = readFileSync(new URL('../../src/js/infinite-canvas-adapter.js', import.meta.url), 'utf8');
const apiSource = readFileSync(new URL('../../src/js/api.js', import.meta.url), 'utf8');
const stableUiCss = readFileSync(new URL('../../src/css/stable-ui.css', import.meta.url), 'utf8');
const tauriConfig = JSON.parse(readFileSync(new URL('../../src-tauri/tauri.conf.json', import.meta.url), 'utf8'));
const viteConfig = readFileSync(new URL('../../vite.config.js', import.meta.url), 'utf8');
const bundleVerifier = readFileSync(new URL('../../tools/verify-infinite-canvas-bundle.mjs', import.meta.url), 'utf8');

test('production dependencies pin the approved Excalidraw and React versions', () => {
  assert.equal(packageJson.dependencies['@excalidraw/excalidraw'], '0.18.1');
  assert.equal(packageJson.dependencies.react, '18.3.1');
  assert.equal(packageJson.dependencies['react-dom'], '18.3.1');
  assert.equal(packageLock.packages['node_modules/@excalidraw/excalidraw'].version, '0.18.1');
  assert.equal(packageLock.packages['node_modules/react'].version, '18.3.1');
  assert.equal(packageLock.packages['node_modules/react-dom'].version, '18.3.1');
});

test('spatial workspace is a primary route and the old Studio switch is gone', () => {
  assert.match(html, /data-page="canvas"[^>]*aria-label="无限画布"/);
  assert.match(html, /id="page-canvas"[^>]*data-page-name="canvas"/);
  assert.match(html, /id="spatial-canvas-list"/);
  assert.match(html, /id="btn-spatial-new"/);
  assert.match(html, /id="btn-spatial-rename"[^>]*aria-label="重命名当前画布"/);
  assert.match(html, /id="spatial-inspector"[^>]*hidden/);
  assert.match(html, /class="spatial-workspace" data-adapter="sqlite-v8"/);
  assert.doesNotMatch(html, /data-studio-view=/);
  assert.doesNotMatch(html, />自由画布</);
  assert.match(app, /infiniteCanvasWorkspace\.setPage\(page === 'canvas'\)/);
  assert.match(workspace, /function thumbnailElements\(scene\)/);
});

test('Excalidraw is isolated behind a user-triggered dynamic import', () => {
  assert.match(workspace, /runtimeLoader = \(\) => import\('\.\/infinite-canvas-island\.jsx'\)/);
  assert.doesNotMatch(workspace, /from ['"]@excalidraw\/excalidraw/);
  assert.doesNotMatch(app, /from ['"]@excalidraw\/excalidraw/);
  assert.match(island, /from '@excalidraw\/excalidraw'/);
  assert.match(island, /@excalidraw\/excalidraw\/index\.css/);
  assert.match(adapterSource, /currentItemRoughness:\s*0/);
  assert.match(island, /dark: '#cfd3d7'/);
  assert.match(island, /initialData=\{runtimeSceneForTheme\(canvasDocument\.scene, theme\)\}/);
  assert.match(island, /appState: persistentAppState\(appState\)/);
  assert.match(island, /api\.updateScene\(\{ appState: \{ viewBackgroundColor \} \}\)/);
  assert.doesNotMatch(itemsSource, /#fffdf9|#3f3b37/);
  assert.match(island, /changeViewBackgroundColor: false/);
  assert.match(island, /saveAsImage: false/);
  assert.match(island, /aiEnabled=\{false\}/);
  assert.match(stableUiCss, /\.spatial-canvas-host \.App-toolbar__extra-tools-dropdown \[data-testid="toolbar-embeddable"\]/);
  assert.match(stableUiCss, /\[data-testid="toolbar-laser"\]/);
  assert.match(stableUiCss, /@media \(max-width: 1080px\)[\s\S]*?\.spatial-inspector \{ top: 72px;/);
  assert.match(viteConfig, /manifest:\s*true/);
  assert.match(bundleVerifier, /isDynamicEntry/);
  assert.match(bundleVerifier, /modulepreload/);
});

test('IC2 memory adapter provides list, recent, rename and scene continuity without a ledger', () => {
  let tick = 0;
  const adapter = createMemorySpatialCanvasAdapter({
    now: () => new Date(Date.UTC(2026, 8, 2, 12, tick++)),
    idFactory: () => `canvas-${tick}`,
  });
  const first = adapter.create({ name: '  商品   主图方案  ' });
  const second = adapter.create({ name: '候选分支' });
  assert.equal(first.name, '商品 主图方案');
  assert.equal(adapter.list()[0].id, second.id);

  const renamed = adapter.rename(first.id, '白底图对比');
  assert.equal(renamed.name, '白底图对比');
  adapter.open(first.id);
  assert.equal(adapter.list()[0].id, first.id);

  const updated = adapter.updateScene(first.id, {
    elements: [{ id: 'frame-1', type: 'frame', isDeleted: false }],
    appState: { scrollX: 42, zoom: { value: 0.8 } },
    files: { forbidden: { dataURL: 'data:image/png;base64,AAAA' } },
  });
  assert.equal(updated.summary.frame_count, 1);
  assert.equal(updated.scene.appState.scrollX, 42);
  assert.deepEqual(updated.scene.files, {});
  assert.doesNotMatch(adapterSource, /localStorage|sessionStorage|fetch\(|\.saveCanvas\(/);
});

test('IC3 production adapter uses schema v8 APIs and strips scene file bytes', async () => {
  const calls = [];
  const base = {
    id: 'spatial:api',
    name: 'API 画布',
    current_revision: 1,
    current_version_id: 'spatialver:1',
    created_at: '2026-09-02T00:00:00Z',
    updated_at: '2026-09-02T00:00:00Z',
    last_opened_at: '2026-09-02T00:00:00Z',
    summary: { element_count: 0, image_count: 0, video_count: 0, frame_count: 0 },
    thumbnail: { element_count: 0, image_count: 0, video_count: 0, frame_count: 0, elements: [] },
  };
  const api = {
    async listSpatialCanvases() { calls.push(['list']); return { canvases: [base] }; },
    async createSpatialCanvas(payload) {
      calls.push(['create', payload]);
      return { ...base, scene: { elements: [], app_state: { zoom: { value: 1 } }, files: {} } };
    },
    async openSpatialCanvas(id) {
      calls.push(['open', id]);
      return { ...base, scene: { elements: [], app_state: { zoom: { value: 0.8 }, scrollX: 42 }, files: {} } };
    },
    async renameSpatialCanvas(id, payload) {
      calls.push(['rename', id, payload]);
      return { ...base, name: payload.name };
    },
    async saveSpatialCanvasScene(id, payload) {
      calls.push(['save', id, payload]);
      return {
        ...base,
        current_revision: 2,
        current_version_id: 'spatialver:2',
        scene: payload.scene,
      };
    },
  };
  const adapter = createApiSpatialCanvasAdapter({ api });
  await adapter.load();
  assert.equal(adapter.kind, 'sqlite-v8');
  assert.equal(adapter.list()[0].id, base.id);
  const opened = await adapter.open(base.id);
  assert.equal(opened.scene.appState.zoom.value, 0.8);
  assert.equal(opened.scene.appState.scrollX, 42);
  const saved = await adapter.updateScene(base.id, {
    elements: [{ id: 'rect-1', type: 'rectangle', x: 1, y: 2, width: 3, height: 4 }],
    appState: { zoom: { value: 0.7 }, scrollX: 9 },
    files: { forbidden: { dataURL: 'data:image/png;base64,AAAA' } },
  });
  assert.equal(saved.current_revision, 2);
  const savePayload = calls.find(([kind]) => kind === 'save')[2];
  assert.equal(savePayload.expected_revision, 1);
  assert.equal(savePayload.scene.schema_version, 1);
  assert.deepEqual(savePayload.scene.files, {});
  assert.equal(savePayload.scene.app_state.zoom.value, 0.7);
  assert.match(apiSource, /export async function listSpatialCanvases/);
  assert.match(apiSource, /export async function saveSpatialCanvasScene/);
  assert.match(workspace, /api = API,[\s\S]*createApiSpatialCanvasAdapter\(\{ api \}\)/);
});

test('IC3 production adapter invalidates queued old-base saves after a conflict', async () => {
  const canvas = {
    id: 'spatial:serial',
    name: '串行保存',
    current_revision: 1,
    current_version_id: 'spatialver:1',
    created_at: '2026-09-02T00:00:00Z',
    updated_at: '2026-09-02T00:00:00Z',
    last_opened_at: '2026-09-02T00:00:00Z',
    summary: { element_count: 0, image_count: 0, video_count: 0, frame_count: 0 },
    thumbnail: { element_count: 0, image_count: 0, video_count: 0, frame_count: 0, elements: [] },
  };
  const expectedRevisions = [];
  let saveCount = 0;
  const api = {
    async listSpatialCanvases() { return { canvases: [canvas] }; },
    async openSpatialCanvas() {
      return {
        ...canvas,
        current_revision: 4,
        current_version_id: 'spatialver:4',
        scene: { elements: [], app_state: {}, files: {} },
      };
    },
    async saveSpatialCanvasScene(id, payload) {
      assert.equal(id, canvas.id);
      expectedRevisions.push(payload.expected_revision);
      saveCount += 1;
      if (saveCount === 1) {
        const conflict = new Error('revision conflict');
        conflict.status = 409;
        throw conflict;
      }
      return {
        ...canvas,
        current_revision: payload.expected_revision + 1,
        current_version_id: `spatialver:${payload.expected_revision + 1}`,
        scene: payload.scene,
      };
    },
  };
  const adapter = createApiSpatialCanvasAdapter({ api });
  await adapter.load();
  const first = adapter.updateScene(canvas.id, { elements: [], appState: {}, files: {} });
  const second = adapter.updateScene(canvas.id, {
    elements: [{ id: 'frame-2', type: 'frame', x: 0, y: 0, width: 100, height: 100 }],
    appState: {},
    files: {},
  });
  await assert.rejects(first, /revision conflict/);
  await assert.rejects(second, (error) => {
    assert.equal(error.status, 409);
    assert.equal(error.code, 'SPATIAL_CANVAS_STALE_QUEUED_SAVE');
    assert.equal(error.current.current_revision, 4);
    return true;
  });
  const saved = await adapter.updateScene(canvas.id, {
    elements: [{ id: 'frame-2', type: 'frame', x: 0, y: 0, width: 100, height: 100 }],
    appState: {},
    files: {},
  });
  assert.deepEqual(expectedRevisions, [1, 4]);
  assert.equal(saved.current_revision, 5);
  assert.equal(adapter.get(canvas.id).current_revision, 5);
  assert.equal((await first.catch((error) => error)).current.current_revision, 4);
  assert.match(workspace, /savePromise = savePromise/);
  assert.match(workspace, /preserveSceneConflict\(saveId, sceneConflictState/);
  assert.match(island, /canvasApi\?\.updateScene/);
});

test('scene adapter keeps clean Excalidraw defaults and no embedded file bytes', () => {
  const adapter = createMemorySpatialCanvasAdapter({ idFactory: () => 'clean' });
  const record = adapter.create();
  assert.equal(record.scene.appState.currentItemRoughness, 0);
  assert.equal(record.scene.appState.currentItemStrokeStyle, 'solid');
  assert.deepEqual(record.scene.files, {});
  assert.doesNotMatch(JSON.stringify(record.scene), /data:image|;base64,/i);
});

test('IC4 connects durable assets, tasks, results and Fabric without automatic paid execution', () => {
  assert.match(html, /id="btn-spatial-assets"[^>]*aria-label="打开素材库"/);
  assert.match(html, /id="btn-spatial-jobs"[^>]*aria-label="打开任务中心"/);
  assert.match(html, /id="btn-send-result-canvas"/);
  assert.match(app, /onSendToCanvas: sendAssetToCanvas/);
  assert.match(app, /onSpatialResult: handleSpatialFineEditResult/);
  assert.match(app, /onAction: handleSpatialAction/);
  assert.match(app, /onFineEdit: handleSpatialFineEdit/);
  assert.match(app, /writeSpatialDragData\(event, spatialItemFromJob\(job,/);
  assert.match(app, /lineage_parent_id: asset\.lineage_parent_id \|\| ''/);
  assert.match(app, /已打开并预填快捷处理；尚未发起生成调用/);
  assert.doesNotMatch(app, /prepareSpatialQuickAction[\s\S]{0,2500}handleGenerate\(/);
  assert.match(workspace, /documentRef\.addEventListener\('drop', onDrop\)/);
  assert.equal(tauriConfig.app.windows[0].dragDropEnabled, false);
  assert.match(workspace, /addBusinessItems/);
  assert.match(workspace, /onOpenFineEdit/);
  assert.match(island, /convertToExcalidrawElements/);
  assert.match(island, /api\.addFiles/);
  assert.match(island, /api\.getAppState\(\)\?\.isLoading/);
  assert.match(island, /appState: \{ selectedElementIds \}/);
  assert.match(island, /await synchronizeScene\?\.\(nextElements, nextAppState\)/);
  assert.match(island, /waitForVisibleCanvasViewport\(canvasApi, host\)/);
  assert.match(island, /readyFrames >= 2/);
  assert.match(island, /mergeSpatialNodeBatch\(existing, additions, batch\.lineageBindings\)/);
  assert.match(island, /captureUpdate: CaptureUpdateAction\.IMMEDIATELY/);
  assert.match(island, /captureUpdate: CaptureUpdateAction\.NEVER/);
  assert.match(island, /spatialLineageFocusElements\(normalized, boundExisting, boundAdditions\)/);
  assert.match(island, /fitToContent: focusElements\.length > inserted\.length/);
  assert.match(island, /canvasApi\.getAppState\(\),\s*\);/);
  assert.match(island, /synchronizeScene\?\.\(elements, appState, \{ persist: false \}\)/);
  assert.match(workspace, /const session = await ensureCanvasForImport\(\)[\s\S]{0,220}session\.island\.addBusinessItems/);
});

test('IC5 production island renders only Product Atelier video embeddables on demand', () => {
  assert.match(island, /renderEmbeddable=\{renderVideoEmbeddable\}/);
  assert.match(island, /validateEmbeddable=\{validateVideoEmbeddable\}/);
  assert.equal(island.includes('product-atelier-video:\\/\\/'), true);
  assert.match(island, /createSpatialVideoPlaybackState/);
  assert.match(island, /stopSpatialVideoPlayback/);
  assert.match(island, /loaded \? presentation\.streamUrl : ''/);
  assert.match(island, /controlsList="nodownload noremoteplayback"/);
  assert.match(island, /onPlay=\{onPlaybackStart\}/);
  assert.match(island, /onPause=\{onPlaybackPause\}/);
  assert.match(island, /const playbackErrorRef = useRef\(onPlaybackError\)/);
  assert.match(island, /playbackErrorRef\.current = onPlaybackError/);
  assert.match(island, /const failPlayback = useCallback\(\(\) => \{/);
  assert.match(island, /setPresentation\(\(current\) => \(\{ \.\.\.current, streamUrl: '' \}\)\)/);
  assert.match(island, /if \(loaded && !nextPresentation\.streamUrl\) failPlayback\(\)/);
  assert.match(island, /if \(!canceled && loaded\) failPlayback\(\)/);
  assert.match(island, /const playbackActive = playing && Boolean\(videoSrc\)/);
  assert.match(island, /video\.play\(\)\.catch\(\(\) => \{ if \(!canceled\) failPlayback\(\); \}\)/);
  assert.match(island, /onError=\{failPlayback\}/);
  assert.match(island, /value\?\.streamUrl/);
  assert.match(island, /value\?\.durationSeconds/);
  assert.match(island, /data-video-playing=\{playbackActive \? 'true' : 'false'\}/);
  assert.match(island, /<i>\{playbackActive \? '播放中' : '视频'\}<\/i>/);
  assert.match(island, /onPlaybackStart=\{\(\) => videoControls\.play\(element\.id\)\}/);
  assert.match(island, /onPlaybackPause=\{\(\) => videoControls\.pause\(element\.id\)\}/);
  assert.match(island, /onPlaybackError=\{\(\) => videoControls\.stop\(\)\}/);
  assert.doesNotMatch(island, /video\.play\(\)\.catch\(\(\) => \{\}\)/);
  assert.doesNotMatch(island, /const statusCopy = playing \?/);
  assert.match(stableUiCss, /\.spatial-video-node\.is-loaded \.spatial-video-node__cover > i \{ top: 8px; bottom: auto; \}/);
  assert.doesNotMatch(island, /removeAttribute\('src'\)/);
  assert.doesNotMatch(island, /autoPlay(?:=|\s)/);
});

test('IC5 video jobs use the durable queue, idempotent result backfill and canvas-only recovery', () => {
  assert.match(workspace, /api\.executeCommand\(SPATIAL_VIDEO_COMMAND_ID, payload/);
  assert.match(workspace, /persistVideoJobAssociation\(job, session\)/);
  assert.match(workspace, /spatialVideoCanvasId\(job\)/);
  assert.match(workspace, /api\.getJobs\(200/);
  assert.match(workspace, /for \(let attempt = 0; attempt < 3; attempt \+= 1\)/);
  assert.match(workspace, /session\.island\.updateTask\?\.\(videoTaskItem\(job, session\)\)/);
  assert.match(island, /updateSpatialTaskElements\(current, item/);
  assert.match(workspace, /session\.island\.addBusinessItemsOnce\(\[videoTaskItem\(job, session\)\]\)[\s\S]{0,700}await flushScene\(ownerId\)/);
  assert.match(workspace, /assets\.filter\(\(asset\) => asset\?\.role === 'result_video'\)/);
  assert.match(app, /!videoJob && retryable\.length/);
  assert.match(workspace, /VIDEO_POLL_INTERVAL_MS/);
  assert.match(workspace, /VIDEO_RECOVERY_MAX_ATTEMPTS = 8/);
  assert.match(workspace, /恢复已暂停，重新进入画布或任务中心重试/);
  assert.match(workspace, /permanentVideoRecoveryError/);
  assert.match(workspace, /if \(epoch !== inspectorEpoch\) return/);
  assert.match(workspace, /videoDraft !== submittedDraft/);
  assert.match(workspace, /runtimePromise = null/);
  assert.doesNotMatch(workspace, /startPolling\(/);
  assert.match(island, /addBusinessItemsOnce: \(items\) => insertBusinessItems\(items, \{ once: true \}\)/);
  assert.match(island, /element\?\.type === 'image' && spatialBusinessKey\(element\)/);
  assert.match(app, /if \(isSpatialVideoJob\(job\)\) return \[\];/);
  assert.match(app, /isSpatialVideoJob\(job\) && \['retry-item', 'retry-failed'\]\.includes\(action\)/);
  assert.match(app, /if \(isSpatialVideoJob\(job\)\) return openVideoJobCanvas\(job\);/);
  assert.match(app, /onVideoJobSubmitted: \(\) => loadJobs\(true\)/);
  assert.match(app, /onVideoJobSettled: \(\) => loadJobs\(true\)/);
});

test('IC5 video export keeps original binary bytes out of the base64 image path', () => {
  const exportFunction = app.match(/async function exportSpatialResult\(context\) \{[\s\S]*?\n\}/)?.[0] || '';
  const downloadStart = apiSource.indexOf('export async function downloadAsset');
  const downloadEnd = apiSource.indexOf('export async function saveImage', downloadStart);
  const downloadFunction = apiSource.slice(downloadStart, downloadEnd);
  assert.match(exportFunction, /asset\?\.kind === 'video'/);
  assert.match(exportFunction, /API\.downloadAsset\(assetId, suggestedName\)/);
  assert.match(downloadFunction, /content\?download=true/);
  assert.match(downloadFunction, /invoke\('save_binary_asset'/);
  assert.doesNotMatch(downloadFunction, /saveImage|save_base64_image|btoa|base64/i);
});
