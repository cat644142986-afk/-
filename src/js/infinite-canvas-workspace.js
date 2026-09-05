import * as API from './api.js';
import { createApiSpatialCanvasAdapter, spatialSceneSignature } from './infinite-canvas-adapter.js';
import {
  SPATIAL_DRAG_MIME,
  parseSpatialDragItem,
  spatialContextActions,
  spatialItemFromAsset,
  spatialItemFromJob,
} from './spatial-canvas-items.js';
import {
  SPATIAL_VIDEO_COMMAND_ID,
  SPATIAL_VIDEO_DURATIONS,
  SPATIAL_VIDEO_RATIOS,
  confirmSpatialVideoDraft,
  createSpatialVideoDraft,
  isSpatialVideoJob,
  spatialVideoCanvasId,
  spatialVideoCommandPayload,
  spatialVideoJobIsActive,
  spatialVideoJobIsSettled,
  spatialVideoResultAssetIds,
  updateSpatialVideoDraft,
} from './spatial-video.js';

const ACTION_COPY = Object.freeze({
  cutout: '抠图',
  'white-background': '白底图',
  outpaint: '扩图',
  'local-edit': '局部修改',
  'generate-image': '生图',
  'generate-video': '生视频',
  compare: '对比',
  export: '导出',
  'fine-edit': '精细修改',
  'open-task': '打开任务',
  'toggle-video': '播放 / 暂停',
});

const VIDEO_POLL_INTERVAL_MS = 1200;
const VIDEO_RECOVERY_MAX_INTERVAL_MS = 30000;
const VIDEO_RECOVERY_MAX_ATTEMPTS = 8;

function workspaceRequestId(prefix) {
  const suffix = globalThis.crypto?.randomUUID?.()
    || `${Date.now()}-${Math.random().toString(16).slice(2)}`;
  return `${prefix}:${String(suffix).toLowerCase()}`;
}

function videoRecoveryError(message, { permanent = false, code = '' } = {}) {
  const error = new Error(String(message || '视频恢复失败'));
  error.videoRecoveryPermanent = Boolean(permanent);
  if (code) error.code = String(code);
  return error;
}

function permanentVideoRecoveryError(error) {
  return Boolean(error?.videoRecoveryPermanent || Number(error?.status || 0) === 404);
}

function permanentVideoRecoveryMessage(error) {
  const detail = String(error?.message || '').trim();
  return detail || '视频任务或结果已不存在';
}

async function defaultVideoAssetResolver(api, assetId, { loadStream = false } = {}) {
  const response = await api.getAsset(assetId, { timeoutMs: 10000 });
  const asset = response?.asset || response || {};
  const coverAssetId = asset.cover_asset_id || assetId;
  return {
    ...asset,
    cover_url: await api.getAssetThumbnailUrl(coverAssetId, 960),
    stream_url: loadStream ? await api.getAssetContentUrl(assetId) : '',
  };
}

function escapeHtml(value) {
  return String(value || '')
    .replaceAll('&', '&amp;')
    .replaceAll('<', '&lt;')
    .replaceAll('>', '&gt;')
    .replaceAll('"', '&quot;')
    .replaceAll("'", '&#039;');
}

function formatRecent(iso) {
  const date = new Date(iso);
  if (!Number.isFinite(date.getTime())) return '刚刚';
  return new Intl.DateTimeFormat('zh-CN', {
    month: 'numeric', day: 'numeric', hour: '2-digit', minute: '2-digit', hour12: false,
  }).format(date);
}

function closestVideoRatio(asset = {}) {
  const ratio = Number(asset.width || 0) / Math.max(1, Number(asset.height || 0));
  if (!Number.isFinite(ratio) || ratio <= 0) return '1:1';
  return [...SPATIAL_VIDEO_RATIOS]
    .map((value) => {
      const [width, height] = value.split(':').map(Number);
      return { value, distance: Math.abs((width / height) - ratio) };
    })
    .sort((left, right) => left.distance - right.distance)[0].value;
}

function videoDraftHtml(draft, asset, { submitting = false, error = '' } = {}) {
  if (!draft) return '';
  const durationOptions = SPATIAL_VIDEO_DURATIONS.map((value) => (
    `<option value="${value}"${Number(draft.durationSeconds) === value ? ' selected' : ''}>${value} 秒</option>`
  )).join('');
  const ratioOptions = SPATIAL_VIDEO_RATIOS.map((value) => (
    `<option value="${value}"${draft.outputRatio === value ? ' selected' : ''}>${value}</option>`
  )).join('');
  return `
    <form class="spatial-video-form" data-spatial-video-form novalidate>
      <label class="spatial-video-form__wide"><span>提示词</span><textarea data-spatial-video-field="prompt" maxlength="600" rows="3" ${submitting ? 'disabled' : ''}>${escapeHtml(draft.prompt)}</textarea></label>
      <label><span>比例</span><select data-spatial-video-field="outputRatio" ${submitting ? 'disabled' : ''}>${ratioOptions}</select></label>
      <label><span>时长</span><select data-spatial-video-field="durationSeconds" ${submitting ? 'disabled' : ''}>${durationOptions}</select></label>
      <label class="spatial-video-form__wide"><span>首帧</span><input value="${escapeHtml(asset?.name || draft.sourceAssetId)}" readonly /></label>
      <label class="spatial-video-form__wide"><span>尾帧（可选素材 ID）</span><input data-spatial-video-field="lastFrameAssetId" value="${escapeHtml(draft.lastFrameAssetId)}" placeholder="ast_..." ${submitting ? 'disabled' : ''} /></label>
      <label class="spatial-video-form__wide spatial-video-form__range"><span>运动强度 <b data-spatial-video-motion>${Number(draft.motionIntensity)}</b></span><input type="range" min="1" max="10" step="1" data-spatial-video-field="motionIntensity" value="${Number(draft.motionIntensity)}" ${submitting ? 'disabled' : ''} /></label>
      <label class="spatial-video-form__confirm"><input type="checkbox" data-spatial-video-confirm ${draft.callConfirmed ? 'checked' : ''} ${submitting ? 'disabled' : ''} /><span>确认参数；失败后不自动重试</span></label>
      <p class="spatial-video-form__status" data-spatial-video-status${error ? ' data-error="true"' : ''}>${escapeHtml(error || (draft.callConfirmed ? '参数已确认' : '等待确认'))}</p>
      <div class="spatial-video-form__actions">
        <button type="button" data-spatial-video-cancel ${submitting ? 'disabled' : ''}>取消</button>
        <button type="submit" class="is-primary" ${draft.callConfirmed && !submitting ? '' : 'disabled'}>${submitting ? '正在创建任务' : '创建视频任务'}</button>
      </div>
    </form>
  `;
}

function thumbnailElements(scene) {
  const elements = Array.from(scene?.elements || [])
    .filter((element) => !element?.isDeleted && Number.isFinite(element?.x) && Number.isFinite(element?.y))
    .slice(-12);
  if (!elements.length) return '';
  const geometry = elements.map((element) => {
    const width = Math.max(2, Math.abs(Number(element.width) || 2));
    const height = Math.max(2, Math.abs(Number(element.height) || 2));
    return { element, x: Number(element.x), y: Number(element.y), width, height };
  });
  const minX = Math.min(...geometry.map((item) => item.x));
  const minY = Math.min(...geometry.map((item) => item.y));
  const maxX = Math.max(...geometry.map((item) => item.x + item.width));
  const maxY = Math.max(...geometry.map((item) => item.y + item.height));
  const spanX = Math.max(40, maxX - minX);
  const spanY = Math.max(40, maxY - minY);
  const allowed = new Set(['arrow', 'diamond', 'ellipse', 'frame', 'freedraw', 'image', 'line', 'rectangle', 'text']);
  return geometry.map(({ element, x, y, width, height }) => {
    const kind = allowed.has(element.type) ? element.type : 'rectangle';
    const left = 8 + ((x - minX) / spanX) * 76;
    const top = 8 + ((y - minY) / spanY) * 76;
    const scaledWidth = Math.max(3, Math.min(80 - left, (width / spanX) * 76));
    const scaledHeight = Math.max(3, Math.min(80 - top, (height / spanY) * 76));
    return `<i class="is-${kind}" style="left:${left.toFixed(2)}%;top:${top.toFixed(2)}%;width:${scaledWidth.toFixed(2)}%;height:${scaledHeight.toFixed(2)}%"></i>`;
  }).join('');
}

export function createInfiniteCanvasWorkspaceController({
  documentRef = document,
  windowRef = window,
  api = API,
  adapter = createApiSpatialCanvasAdapter({ api }),
  runtimeLoader = () => import('./infinite-canvas-island.jsx'),
  onAction = () => {},
  onFineEdit = () => {},
  onImportFiles = async () => [],
  onVideoJobSubmitted = () => {},
  onVideoJobSettled = () => {},
  resolveProxyUrl = (assetId) => api.getAssetThumbnailUrl(assetId, 960),
  resolveVideoAsset = (assetId, options) => defaultVideoAssetResolver(api, assetId, options),
} = {}) {
  const query = (selector) => documentRef.querySelector(selector);
  let bound = false;
  let active = false;
  let currentId = '';
  let mountedIsland = null;
  let currentCanvasSession = null;
  let runtimePromise = null;
  let recordsPromise = null;
  const pendingScenes = new Map();
  const savingScenes = new Map();
  const sceneConflicts = new Map();
  let sceneSequence = 0;
  let sceneTimer = null;
  let sceneTimerCanvasId = '';
  let savePromise = Promise.resolve();
  let openEpoch = 0;
  let renameReturnFocus = null;
  let islandReadyPromise = null;
  let resolveIslandReady = null;
  let selectedElement = null;
  let inspectorEpoch = 0;
  let selectedAsset = null;
  let videoDraft = null;
  let videoSubmitting = false;
  let videoDraftError = '';
  let videoPollTimer = null;
  let videoPollEpoch = 0;
  let videoRecoveryAttempt = 0;
  let videoRecoveryPending = false;
  let emptySceneRecoveryPromise = null;
  const activeVideoJobIds = new Set();
  const notifiedVideoJobs = new Set();

  function canvasSessionIsCurrent(session) {
    const islandMatches = session?.island
      ? mountedIsland === session.island
      : mountedIsland === null;
    return Boolean(
      session
      && currentCanvasSession === session
      && currentId === session.canvasId
      && islandMatches,
    );
  }

  function captureCanvasSession() {
    return canvasSessionIsCurrent(currentCanvasSession) ? currentCanvasSession : null;
  }

  function recordsHtml(records) {
    return records.map((record, index) => `
      <article class="spatial-canvas-card" data-spatial-record="${escapeHtml(record.id)}">
        <button class="spatial-canvas-card__open" type="button" data-spatial-open="${escapeHtml(record.id)}" aria-label="打开画布 ${escapeHtml(record.name)}">
          <span class="spatial-thumbnail" data-empty="${record.summary.element_count ? 'false' : 'true'}" aria-hidden="true">${thumbnailElements(record.thumbnail || record.scene)}</span>
          <span class="spatial-canvas-card__copy"><strong>${escapeHtml(record.name)}</strong><small>${index === 0 ? '最近打开' : '本次会话'} · ${formatRecent(record.last_opened_at)}</small></span>
        </button>
        <button class="spatial-card-action" type="button" data-spatial-rename="${escapeHtml(record.id)}" aria-label="重命名 ${escapeHtml(record.name)}" title="重命名">
          <svg viewBox="0 0 24 24" aria-hidden="true"><path d="m4 20 4.5-1 10-10a2.1 2.1 0 0 0-3-3l-10 10Z"/><path d="m13.5 7.5 3 3"/></svg>
        </button>
      </article>
    `).join('');
  }

  function renderLibrary() {
    const list = query('#spatial-canvas-list');
    const empty = query('#spatial-library-empty');
    if (!list || !empty) return;
    const records = adapter.list();
    list.innerHTML = recordsHtml(records);
    list.hidden = records.length === 0;
    empty.hidden = records.length !== 0;
    query('#spatial-canvas-count').textContent = `${records.length} 个画布`;
  }

  function syncEditorHeading() {
    const record = currentId ? adapter.get(currentId) : null;
    query('#spatial-current-name').textContent = record?.name || '无限画布';
    query('#btn-spatial-rename').hidden = !record;
  }

  async function renderInspector(element) {
    const inspector = query('#spatial-inspector');
    if (!inspector) return;
    const epoch = ++inspectorEpoch;
    const selectionChanged = String(element?.id || '') !== String(selectedElement?.id || '');
    if (selectionChanged) {
      videoDraft = null;
      videoDraftError = '';
      selectedAsset = null;
    }
    selectedElement = element || null;
    const actions = spatialContextActions(element || {});
    if (!element || !actions.length) {
      inspector.hidden = true;
      inspector.innerHTML = '';
      return;
    }
    const refs = element.customData || {};
    const videoElement = element.type === 'embeddable';
    let name = videoElement ? '视频结果' : refs.result_id ? '生成结果' : refs.task_id ? '创作任务' : '素材图片';
    let detail = refs.result_id || refs.task_id || refs.asset_id || '';
    if (refs.asset_id) {
      try {
        const response = await api.getAsset(refs.asset_id, { timeoutMs: 10000 });
        if (epoch !== inspectorEpoch) return;
        const asset = response?.asset || response || {};
        selectedAsset = asset;
        name = asset.name || name;
        detail = asset.width && asset.height
          ? `${asset.width} × ${asset.height}${videoElement && asset.duration_seconds ? ` · ${asset.duration_seconds} 秒` : ' px'}`
          : refs.result_id ? '结果版本' : '原始素材';
      } catch (_) { /* the immutable reference is still enough for actions */ }
    }
    if (epoch !== inspectorEpoch) return;
    const headerKind = videoElement ? 'VIDEO' : refs.result_id ? 'RESULT' : refs.task_id ? 'TASK' : 'ASSET';
    inspector.innerHTML = `
      <header><span>${headerKind}</span><button type="button" data-spatial-inspector-close aria-label="收起对象操作">×</button></header>
      <div class="spatial-inspector__copy"><strong>${escapeHtml(name)}</strong><small>${escapeHtml(detail)}</small></div>
      <div class="spatial-inspector__actions">${actions.map((action) => `<button type="button" data-spatial-action="${action}"${action === 'fine-edit' ? ' class="is-primary"' : ''}>${ACTION_COPY[action]}</button>`).join('')}</div>
      ${videoDraft?.sourceAssetId === refs.asset_id ? videoDraftHtml(videoDraft, selectedAsset, { submitting: videoSubmitting, error: videoDraftError }) : ''}
    `;
    inspector.hidden = false;
  }

  function stopVideoPolling({ clearJobs = true, resetRecovery = true } = {}) {
    videoPollEpoch += 1;
    windowRef.clearTimeout(videoPollTimer);
    videoPollTimer = null;
    if (clearJobs) activeVideoJobIds.clear();
    if (resetRecovery) {
      videoRecoveryAttempt = 0;
      videoRecoveryPending = false;
    }
  }

  function currentCanvasElements(session = captureCanvasSession()) {
    if (session && !canvasSessionIsCurrent(session)) return [];
    const canvasId = session?.canvasId || currentId;
    const island = session?.island || mountedIsland;
    return Array.from(island?.getScene?.().elements || adapter.get(canvasId)?.scene?.elements || []);
  }

  function taskContext(taskId, elements = currentCanvasElements()) {
    const taskElement = elements.find((element) => (
      !element?.isDeleted && String(element?.customData?.task_id || '') === String(taskId || '')
    ));
    return {
      taskElement: taskElement || null,
      lineageParentId: String(taskElement?.customData?.lineage_parent_id || ''),
      productProfileVersionId: String(taskElement?.customData?.product_profile_version_id || ''),
    };
  }

  function sceneHasVideoTask(scene, taskId) {
    return Array.from(scene?.elements || []).some((element) => (
      !element?.isDeleted && String(element?.customData?.task_id || '') === String(taskId || '')
    ));
  }

  function sceneHasVideoResults(scene, taskId, items) {
    const expected = new Set(Array.from(items || []).map((item) => (
      String(item?.references?.result_id || item?.result_id || '')
    )).filter(Boolean));
    if (!expected.size) return false;
    Array.from(scene?.elements || []).forEach((element) => {
      if (
        !element?.isDeleted
        && String(element?.customData?.task_id || '') === String(taskId || '')
      ) expected.delete(String(element?.customData?.result_id || ''));
    });
    return expected.size === 0;
  }

  function videoTaskItem(job, session = captureCanvasSession()) {
    const parameters = job?.parameters || job?.snapshot?.parameters || {};
    const sourceAssetId = String(parameters.first_frame_asset_id || job?.snapshot?.source_asset_ids?.[0] || '');
    const sourceElement = currentCanvasElements(session).find((element) => (
      !element?.isDeleted && String(element?.customData?.asset_id || '') === sourceAssetId
    ));
    return spatialItemFromJob(job, {
      product_profile_version_id: sourceElement?.customData?.product_profile_version_id,
      lineage_parent_id: sourceAssetId,
    });
  }

  async function persistVideoJobAssociation(job, session = captureCanvasSession()) {
    const ownerId = spatialVideoCanvasId(job);
    if (!session || !ownerId || ownerId !== session.canvasId) {
      throw new Error('视频任务与当前画布的持久关联不一致');
    }
    for (let attempt = 0; attempt < 3; attempt += 1) {
      if (!canvasSessionIsCurrent(session)) return { persisted: false, deferred: true };
      const durableScene = adapter.get(ownerId)?.scene;
      if (sceneHasVideoTask(durableScene, job.id) && sceneHasVideoTask(session.island.getScene?.(), job.id)) {
        return { persisted: true, replayed: attempt > 0 };
      }
      await session.island.addBusinessItemsOnce([videoTaskItem(job, session)]);
      if (!canvasSessionIsCurrent(session)) return { persisted: false, deferred: true };
      if (!pendingScenes.has(ownerId) && !sceneHasVideoTask(durableScene, job.id)) {
        queueScene(session.island.getScene?.(), session);
      }
      const saved = await flushScene(ownerId);
      if (!canvasSessionIsCurrent(session)) return { persisted: false, deferred: true };
      const latestScene = saved?.scene || adapter.get(ownerId)?.scene;
      if (sceneHasVideoTask(latestScene, job.id)) {
        return { persisted: true, replayed: attempt > 0 };
      }
    }
    throw new Error('视频任务已创建，但画布关联保存失败；再次提交只会恢复关联，不会重复创建任务');
  }

  async function videoResultItems(job, session = captureCanvasSession()) {
    if (!session || !canvasSessionIsCurrent(session)) return [];
    const context = taskContext(job?.id, currentCanvasElements(session));
    const resultAssetIds = spatialVideoResultAssetIds(job);
    if (!resultAssetIds.length) {
      throw videoRecoveryError('视频任务缺少结果合同', {
        permanent: true,
        code: 'VIDEO_RESULT_CONTRACT_MISSING',
      });
    }
    const assetResults = await Promise.all(resultAssetIds.map(async (assetId) => {
      try {
        const response = await api.getAsset(assetId, { timeoutMs: 10000 });
        return { asset: response?.asset || response || null, error: null };
      } catch (error) {
        return { asset: null, error };
      }
    }));
    const missingAsset = assetResults.find((entry) => !entry.asset);
    if (missingAsset) {
      if (permanentVideoRecoveryError(missingAsset.error)) {
        throw videoRecoveryError('视频结果素材已不存在', {
          permanent: true,
          code: 'VIDEO_RESULT_ASSET_MISSING',
        });
      }
      throw new Error('视频结果素材暂不可用，正在自动恢复');
    }
    const assets = assetResults.map((entry) => entry.asset);
    const items = assets.filter((asset) => asset?.role === 'result_video').map((asset) => spatialItemFromAsset(asset, {
      kind: 'result',
      result_id: asset.id,
      task_id: job.id,
      product_profile_version_id: context.productProfileVersionId,
      lineage_parent_id: asset.lineage_parent_id
        || context.lineageParentId
        || job?.parameters?.first_frame_asset_id,
    }));
    if (!items.length) {
      throw videoRecoveryError('视频任务返回的结果合同无效', {
        permanent: true,
        code: 'VIDEO_RESULT_CONTRACT_INVALID',
      });
    }
    return items;
  }

  function showPermanentVideoRecovery(error) {
    videoRecoveryPending = false;
    query('#spatial-save-state').textContent = `${permanentVideoRecoveryMessage(error)} · 请在任务中心处理`;
  }

  function scheduleVideoRecovery(session = captureCanvasSession()) {
    windowRef.clearTimeout(videoPollTimer);
    videoPollTimer = null;
    if (!active || !session || !canvasSessionIsCurrent(session)) return;
    if (videoRecoveryAttempt >= VIDEO_RECOVERY_MAX_ATTEMPTS) {
      videoRecoveryPending = false;
      query('#spatial-save-state').textContent = '恢复已暂停，重新进入画布或任务中心重试';
      return;
    }
    videoRecoveryPending = true;
    const delay = Math.min(
      VIDEO_RECOVERY_MAX_INTERVAL_MS,
      VIDEO_POLL_INTERVAL_MS * (2 ** Math.min(videoRecoveryAttempt, 5)),
    );
    videoRecoveryAttempt += 1;
    const epoch = videoPollEpoch;
    videoPollTimer = windowRef.setTimeout(() => {
      if (epoch !== videoPollEpoch || !active || !canvasSessionIsCurrent(session)) return;
      scanCurrentCanvasVideoJobs(session).catch((error) => {
        console.error('Infinite canvas video recovery failed', error);
        if (active && canvasSessionIsCurrent(session)) scheduleVideoRecovery(session);
      });
    }, delay);
  }

  function scheduleVideoPolling(session = captureCanvasSession()) {
    windowRef.clearTimeout(videoPollTimer);
    videoPollTimer = null;
    if (!active || !session || !canvasSessionIsCurrent(session) || !activeVideoJobIds.size) return;
    videoRecoveryAttempt = 0;
    videoRecoveryPending = false;
    const epoch = videoPollEpoch;
    videoPollTimer = windowRef.setTimeout(async () => {
      if (epoch !== videoPollEpoch || !active || !canvasSessionIsCurrent(session)) return;
      let recoveryNeeded = false;
      let permanentError = null;
      for (const jobId of [...activeVideoJobIds]) {
        let job = null;
        try {
          const response = await api.getJob(jobId);
          job = response?.job || response;
          await reconcileVideoJob(job, { schedule: false, session });
        } catch (error) {
          if (permanentVideoRecoveryError(error)) {
            activeVideoJobIds.delete(String(jobId));
            permanentError ||= error;
            if (job && spatialVideoJobIsSettled(job)) await notifyVideoJobSettled(job);
          } else {
            recoveryNeeded = true;
          }
        }
      }
      if (epoch !== videoPollEpoch || !canvasSessionIsCurrent(session)) return;
      if (permanentError) showPermanentVideoRecovery(permanentError);
      if (recoveryNeeded) scheduleVideoRecovery(session);
      else scheduleVideoPolling(session);
    }, VIDEO_POLL_INTERVAL_MS);
  }

  async function reconcileVideoJob(job, {
    schedule = true,
    session = captureCanvasSession(),
  } = {}) {
    if (!isSpatialVideoJob(job) || !job?.id) return null;
    if (!session || !canvasSessionIsCurrent(session)) return null;
    const ownerId = spatialVideoCanvasId(job);
    if (ownerId && ownerId !== session.canvasId) return null;
    if (spatialVideoJobIsActive(job)) {
      activeVideoJobIds.add(String(job.id));
    } else {
      activeVideoJobIds.delete(String(job.id));
    }
    const association = ownerId
      ? await persistVideoJobAssociation(job, session)
      : null;
    if (!canvasSessionIsCurrent(session) || association?.deferred) return null;
    const taskUpdate = await session.island.updateTask?.(videoTaskItem(job, session));
    if (!canvasSessionIsCurrent(session)) return null;
    let inserted = null;
    let resultItems = [];
    if (['completed', 'partial'].includes(String(job.status || ''))) {
      resultItems = await videoResultItems(job, session);
      if (!canvasSessionIsCurrent(session)) return null;
      if (resultItems.length) {
        inserted = await session.island.addBusinessItemsOnce(resultItems);
      }
    }
    if (!canvasSessionIsCurrent(session)) return null;
    if (association?.persisted || taskUpdate?.changed || (inserted && !inserted.skipped)) {
      await flushScene(session.canvasId);
    }
    if (!canvasSessionIsCurrent(session)) return null;
    if (resultItems.length) {
      const durableScene = adapter.get(session.canvasId)?.scene;
      if (!sceneHasVideoResults(durableScene, job.id, resultItems)) {
        throw new Error('视频结果画布关联尚未保存，正在自动恢复');
      }
    }
    if (spatialVideoJobIsSettled(job)) await notifyVideoJobSettled(job);
    if (schedule) scheduleVideoPolling(session);
    return inserted;
  }

  async function notifyVideoJobSettled(job) {
    if (!job?.id || notifiedVideoJobs.has(String(job.id))) return;
    notifiedVideoJobs.add(String(job.id));
    await Promise.resolve(onVideoJobSettled(job)).catch(() => {});
  }

  async function scanCurrentCanvasVideoJobs(session = captureCanvasSession()) {
    if (!session || !canvasSessionIsCurrent(session)) return;
    stopVideoPolling({ resetRecovery: false });
    const jobs = new Map();
    let recoveryNeeded = false;
    let permanentError = null;
    const taskIds = [...new Set(currentCanvasElements(session)
      .map((element) => String(element?.customData?.task_id || ''))
      .filter(Boolean))];
    for (const taskId of taskIds) {
      try {
        const response = await api.getJob(taskId);
        if (!canvasSessionIsCurrent(session)) return;
        const job = response?.job || response;
        if (
          isSpatialVideoJob(job)
          && (!spatialVideoCanvasId(job) || spatialVideoCanvasId(job) === session.canvasId)
        ) jobs.set(String(job.id), job);
      } catch (error) {
        if (permanentVideoRecoveryError(error)) permanentError ||= error;
        else recoveryNeeded = true;
      }
    }
    try {
      const response = await api.getJobs(200, { timeoutMs: 12000 });
      if (!canvasSessionIsCurrent(session)) return;
      Array.from(response?.jobs || []).forEach((job) => {
        if (isSpatialVideoJob(job) && spatialVideoCanvasId(job) === session.canvasId) {
          jobs.set(String(job.id), job);
        }
      });
    } catch (error) {
      if (permanentVideoRecoveryError(error)) permanentError ||= error;
      else recoveryNeeded = true;
    }
    for (const job of jobs.values()) {
      if (!canvasSessionIsCurrent(session)) return;
      try {
        await reconcileVideoJob(job, { schedule: false, session });
      } catch (error) {
        if (permanentVideoRecoveryError(error)) {
          permanentError ||= error;
          if (spatialVideoJobIsSettled(job)) await notifyVideoJobSettled(job);
        } else {
          recoveryNeeded = true;
        }
        console.error('Infinite canvas video job reconciliation failed', error);
      }
    }
    if (!canvasSessionIsCurrent(session)) return;
    if (permanentError) showPermanentVideoRecovery(permanentError);
    if (recoveryNeeded) scheduleVideoRecovery(session);
    else {
      videoRecoveryAttempt = 0;
      videoRecoveryPending = false;
      scheduleVideoPolling(session);
    }
  }

  async function openVideoComposer(context = {}, seed = {}) {
    const session = captureCanvasSession();
    const requestedCanvasId = String(context?.canvasId || session?.canvasId || '');
    if (!session || requestedCanvasId !== session.canvasId) {
      throw new Error('当前画布已切换，请重新打开视频设置');
    }
    const element = context?.element || selectedElement;
    const refs = element?.customData || {};
    if (element?.type !== 'image' || !refs.asset_id) {
      throw new Error('图生视频需要选择一张图片');
    }
    let asset = selectedAsset;
    if (!asset || String(asset.id) !== String(refs.asset_id)) {
      const response = await api.getAsset(refs.asset_id, { timeoutMs: 10000 });
      if (!canvasSessionIsCurrent(session)) {
        throw new Error('当前画布已切换，请重新打开视频设置');
      }
      asset = response?.asset || response || {};
    }
    if (!canvasSessionIsCurrent(session)) {
      throw new Error('当前画布已切换，请重新打开视频设置');
    }
    selectedAsset = asset;
    videoDraft = createSpatialVideoDraft({
      canvasId: session.canvasId,
      sourceAssetId: refs.asset_id,
      lineageParentId: refs.result_id || refs.asset_id,
      productProfileVersionId: refs.product_profile_version_id,
      prompt: seed.prompt || '镜头缓慢推进，保持商品包装、文字与颜色稳定',
      outputRatio: seed.output_ratio || closestVideoRatio(asset),
      durationSeconds: seed.duration_seconds || 5,
      motionIntensity: seed.motion_intensity || 3,
      lastFrameAssetId: seed.last_frame_asset_id || '',
      provider: seed.provider,
    });
    videoDraftError = '';
    await renderInspector(element);
    if (!canvasSessionIsCurrent(session)) {
      throw new Error('当前画布已切换，请重新打开视频设置');
    }
    query('[data-spatial-video-field="prompt"]')?.focus();
    return videoDraft;
  }

  function syncVideoDraftControls(error = '') {
    const form = query('[data-spatial-video-form]');
    if (!form || !videoDraft) return;
    const confirmation = form.querySelector('[data-spatial-video-confirm]');
    const submit = form.querySelector('button[type="submit"]');
    const status = form.querySelector('[data-spatial-video-status]');
    if (confirmation) confirmation.checked = videoDraft.callConfirmed;
    if (submit) submit.disabled = !videoDraft.callConfirmed || videoSubmitting;
    if (status) {
      status.textContent = error || (videoDraft.callConfirmed ? '参数已确认' : '参数已变化，请重新确认');
      if (error) status.dataset.error = 'true';
      else delete status.dataset.error;
    }
  }

  function updateVideoDraftFromField(control) {
    if (!videoDraft) return;
    const field = control?.dataset?.spatialVideoField;
    if (!field) return;
    const numeric = ['durationSeconds', 'motionIntensity'].includes(field);
    videoDraft = updateSpatialVideoDraft(videoDraft, {
      [field]: numeric ? Number(control.value) : control.value,
    });
    videoDraftError = '';
    const motion = query('[data-spatial-video-motion]');
    if (motion) motion.textContent = String(videoDraft.motionIntensity);
    syncVideoDraftControls();
  }

  function confirmVideoDraft(control) {
    if (!videoDraft) return;
    try {
      videoDraft = confirmSpatialVideoDraft(videoDraft, Boolean(control.checked));
      videoDraftError = '';
      syncVideoDraftControls();
    } catch (error) {
      control.checked = false;
      videoDraft = confirmSpatialVideoDraft(videoDraft, false);
      videoDraftError = String(error?.message || error);
      syncVideoDraftControls(videoDraftError);
    }
  }

  async function submitVideoDraft(event) {
    event.preventDefault();
    if (!videoDraft || videoSubmitting) return;
    const sourceElement = selectedElement;
    const sourceElementId = String(sourceElement?.id || '');
    const submittedDraft = videoDraft;
    const session = captureCanvasSession();
    if (!session || submittedDraft.canvasId !== session.canvasId) {
      videoDraftError = '当前画布已切换，请重新确认视频参数';
      syncVideoDraftControls(videoDraftError);
      return null;
    }
    try {
      const payload = spatialVideoCommandPayload(submittedDraft);
      videoSubmitting = true;
      videoDraftError = '';
      await renderInspector(sourceElement);
      if (!canvasSessionIsCurrent(session)) return null;
      if (videoDraft !== submittedDraft || String(selectedElement?.id || '') !== sourceElementId) return null;
      const response = await api.executeCommand(SPATIAL_VIDEO_COMMAND_ID, payload, { timeoutMs: 15000 });
      const job = response?.job || response;
      if (!job?.id || !isSpatialVideoJob(job)) throw new Error('视频任务返回内容不完整');
      await Promise.resolve(onVideoJobSubmitted(job)).catch(() => {});
      if (!canvasSessionIsCurrent(session)) return job;
      await persistVideoJobAssociation(job, session);
      if (!canvasSessionIsCurrent(session)) return job;
      if (videoDraft === submittedDraft) videoDraft = null;
      await reconcileVideoJob(job, { session });
      if (!canvasSessionIsCurrent(session)) return job;
      const taskElement = await session.island.selectBusinessReference({ task_id: job.id });
      if (taskElement) await renderInspector(taskElement);
      return job;
    } catch (error) {
      if (canvasSessionIsCurrent(session) && videoDraft === submittedDraft) {
        videoDraftError = String(error?.message || error);
        videoSubmitting = false;
        await renderInspector(sourceElement);
      }
      return null;
    } finally {
      videoSubmitting = false;
      if (canvasSessionIsCurrent(session) && videoDraft === submittedDraft) {
        syncVideoDraftControls(videoDraftError);
      } else if (videoDraft && captureCanvasSession()) {
        await renderInspector(selectedElement);
      }
    }
  }

  async function canvasForVideoJob(job) {
    await ensureRecords();
    const taskId = String(job?.id || '');
    const ownerId = spatialVideoCanvasId(job);
    if (ownerId) {
      let record = adapter.get(ownerId);
      if (!record) {
        await ensureRecords(true);
        record = adapter.get(ownerId);
      }
      if (record && !record.scene) record = await adapter.open(ownerId);
      return record || null;
    }
    const ordered = currentId
      ? [adapter.get(currentId), ...adapter.list().filter((record) => record.id !== currentId)]
      : adapter.list();
    for (const candidate of ordered.filter(Boolean)) {
      const record = candidate.scene ? candidate : await adapter.open(candidate.id);
      if (Array.from(record?.scene?.elements || []).some((element) => (
        !element?.isDeleted && String(element?.customData?.task_id || '') === taskId
      ))) return record;
      if (!candidate.scene) continue;
      if (candidate.id !== currentId && adapter.kind !== 'memory') {
        const opened = await adapter.open(candidate.id);
        if (Array.from(opened?.scene?.elements || []).some((element) => (
          !element?.isDeleted && String(element?.customData?.task_id || '') === taskId
        ))) return opened;
      }
    }
    return null;
  }

  async function openVideoJob(jobOrId) {
    let job = typeof jobOrId === 'object' ? jobOrId : null;
    if (!job?.id) {
      const response = await api.getJob(String(jobOrId || ''));
      job = response?.job || response;
    }
    if (!isSpatialVideoJob(job)) throw new Error('当前任务不是视频任务');
    const record = await canvasForVideoJob(job);
    if (!record) throw new Error('没有找到创建该任务的无限画布');
    if (!mountedIsland || currentId !== record.id) await openCanvas(record.id);
    const session = captureCanvasSession();
    if (!session || session.canvasId !== record.id) throw new Error('画布已切换，请重新打开视频任务');
    await islandReadyPromise;
    if (!canvasSessionIsCurrent(session)) throw new Error('画布已切换，请重新打开视频任务');
    await reconcileVideoJob(job, { session });
    if (!canvasSessionIsCurrent(session)) throw new Error('画布已切换，请重新打开视频任务');
    const resultId = String(currentCanvasElements(session).find((element) => (
      !element?.isDeleted
      && element.type === 'embeddable'
      && String(element?.customData?.task_id || '') === String(job.id)
      && element?.customData?.result_id
    ))?.customData?.result_id || '');
    if (['completed', 'partial'].includes(job.status) && resultId) {
      const target = await session.island.selectBusinessReference({ result_id: resultId });
      if (target) await renderInspector(target);
      return target;
    }
    if (spatialVideoJobIsActive(job)) {
      const target = await session.island.selectBusinessReference({ task_id: job.id });
      if (target) await renderInspector(target);
      return target;
    }
    const sourceId = String(job?.parameters?.first_frame_asset_id || job?.snapshot?.source_asset_ids?.[0] || '');
    const source = await session.island.selectBusinessReference({ asset_id: sourceId });
    if (!source) throw new Error('视频任务的首帧已不在该画布');
    return openVideoComposer({ canvasId: record.id, element: source }, job.parameters || {});
  }

  function showLibrary({ restoreFocus = false } = {}) {
    const leavingId = currentCanvasSession?.canvasId || currentId;
    openEpoch += 1;
    stopVideoPolling();
    flushScene(leavingId);
    currentCanvasSession = null;
    mountedIsland?.unmount?.();
    mountedIsland = null;
    islandReadyPromise = null;
    resolveIslandReady = null;
    renderInspector(null);
    query('#spatial-library').hidden = false;
    query('#spatial-editor').hidden = true;
    query('#btn-spatial-home').hidden = true;
    query('#btn-spatial-rename').hidden = true;
    query('#spatial-current-name').textContent = '画布空间';
    renderLibrary();
    if (restoreFocus) windowRef.requestAnimationFrame(() => query('#btn-spatial-new')?.focus());
  }

  function recoverUnexpectedEmptyScene(scene, session) {
    if (!session || !canvasSessionIsCurrent(session)) return false;
    if (Array.from(scene?.elements || []).length) return false;
    const pendingScene = pendingScenes.get(session.canvasId)?.scene;
    const durableScene = adapter.get(session.canvasId)?.scene;
    const fallback = [pendingScene, durableScene].find((candidate) => (
      Array.from(candidate?.elements || []).length > 0
    ));
    if (!fallback) return false;
    query('#spatial-save-state').textContent = '检测到异常空场景 · 正在恢复上一版本';
    if (!emptySceneRecoveryPromise) {
      emptySceneRecoveryPromise = Promise.resolve()
        .then(() => {
          if (!canvasSessionIsCurrent(session)) return null;
          return session.island.updateScene(fallback);
        })
        .then(() => {
          if (canvasSessionIsCurrent(session)) {
            query('#spatial-save-state').textContent = '已阻止空场景覆盖 · 上一版本已恢复';
          }
        })
        .catch((error) => {
          if (canvasSessionIsCurrent(session)) {
            query('#spatial-save-state').textContent = '已阻止空场景覆盖 · 自动恢复失败，请重新打开画布';
          }
          console.error('Infinite canvas empty scene recovery failed', error);
        })
        .finally(() => { emptySceneRecoveryPromise = null; });
    }
    return true;
  }

  function queueScene(scene, session = captureCanvasSession()) {
    if (!session || !canvasSessionIsCurrent(session)) return false;
    if (recoverUnexpectedEmptyScene(scene, session)) return false;
    const signature = spatialSceneSignature(scene);
    const pending = pendingScenes.get(session.canvasId);
    const saving = savingScenes.get(session.canvasId);
    const latest = pending || saving;
    if (latest?.signature === signature) {
      // Preserve the first deadline. A failed save has no timer and must remain
      // retryable, even when the next callback carries exactly the same scene.
      if (!pending || (sceneTimer !== null && sceneTimerCanvasId === session.canvasId)) return false;
    } else if (!latest) {
      const durable = adapter.get(session.canvasId)?.scene;
      if (durable && spatialSceneSignature(durable) === signature) return false;
    }
    const entry = {
      canvasId: session.canvasId,
      scene,
      signature,
      sequence: ++sceneSequence,
    };
    pendingScenes.set(session.canvasId, entry);
    query('#spatial-save-state').textContent = '正在保存画布';
    windowRef.clearTimeout(sceneTimer);
    sceneTimerCanvasId = session.canvasId;
    sceneTimer = windowRef.setTimeout(() => flushScene(session.canvasId), 240);
    return true;
  }

  function clearSceneTimer(canvasId) {
    if (sceneTimerCanvasId !== String(canvasId || '')) return;
    windowRef.clearTimeout(sceneTimer);
    sceneTimer = null;
    sceneTimerCanvasId = '';
  }

  function freezeSceneConflict(message, { allowDiscard = false } = {}) {
    query('#spatial-canvas-host').hidden = true;
    const loading = query('#spatial-editor-loading');
    loading.hidden = false;
    loading.innerHTML = `<strong>${escapeHtml(message)}</strong><button type="button" data-spatial-retry>重试保存副本</button>${allowDiscard ? '<button type="button" data-spatial-conflict-discard>放弃本地并载入远端</button>' : ''}`;
  }

  function sceneConflictState(saveId, error, initialEntry) {
    const existing = sceneConflicts.get(saveId);
    if (existing) return existing;
    const sourceName = adapter.get(saveId)?.name || '未命名画布';
    const state = {
      copyName: `${sourceName} · 冲突副本`,
      copyRecord: null,
      copyRequestId: workspaceRequestId('spatial-conflict-copy'),
      initialEntry,
      preservedSequence: 0,
      remote: error.current,
    };
    sceneConflicts.set(saveId, state);
    return state;
  }

  function permanentSceneConflictError(error) {
    return [400, 404, 422].includes(Number(error?.status || 0));
  }

  async function preserveSceneConflict(saveId, state) {
    clearSceneTimer(saveId);
    if (currentId === saveId) freezeSceneConflict('检测到其他窗口的新版本，正在保护本地修改');
    try {
      if (!state.copyRecord) {
        state.copyRecord = await adapter.create({
          name: state.copyName,
          scene: state.initialEntry.scene,
          clientRequestId: state.copyRequestId,
        });
        state.preservedSequence = state.initialEntry.sequence;
      }
      while (true) {
        const latest = pendingScenes.get(saveId);
        if (latest && latest.sequence > state.preservedSequence) {
          state.copyRecord = await adapter.updateScene(state.copyRecord.id, latest.scene);
          state.preservedSequence = latest.sequence;
          continue;
        }
        const remote = await adapter.open(saveId);
        const afterRefresh = pendingScenes.get(saveId);
        if (afterRefresh && afterRefresh.sequence > state.preservedSequence) continue;
        if (afterRefresh && afterRefresh.sequence === state.preservedSequence) {
          clearSceneTimer(saveId);
          pendingScenes.delete(saveId);
        }
        if (!pendingScenes.has(saveId) && sceneTimerCanvasId === saveId) {
          windowRef.clearTimeout(sceneTimer);
          sceneTimer = null;
          sceneTimerCanvasId = '';
        }
        state.remote = remote;
        break;
      }
    } catch (error) {
      if (currentId === saveId) {
        query('#spatial-save-state').textContent = '保存冲突 · 副本保存失败，已阻止切换和退出';
        freezeSceneConflict('本地修改仍在内存中，副本保存失败', {
          allowDiscard: permanentSceneConflictError(error),
        });
      }
      console.error('Infinite canvas conflict copy failed', error);
      return null;
    }
    sceneConflicts.delete(saveId);
    renderLibrary();
    if (currentId === saveId) {
      mountedIsland?.updateScene?.(state.remote.scene);
      query('#spatial-editor-loading').hidden = true;
      query('#spatial-canvas-host').hidden = false;
      query('#spatial-save-state').textContent = `保存冲突 · 本地修改已另存为「${state.copyRecord.name}」`;
    }
    return state.remote;
  }

  async function discardSceneConflict(saveId = currentId) {
    const conflict = sceneConflicts.get(String(saveId || ''));
    if (!conflict) return false;
    const confirmed = windowRef.confirm?.('这会放弃当前未保存的本地画布修改，并载入其他窗口的最新版本。确定继续吗？');
    if (!confirmed) return false;
    try {
      const remote = await adapter.open(saveId);
      pendingScenes.delete(saveId);
      sceneConflicts.delete(saveId);
      if (sceneTimerCanvasId === saveId) {
        windowRef.clearTimeout(sceneTimer);
        sceneTimer = null;
        sceneTimerCanvasId = '';
      }
      if (currentId === saveId) {
        mountedIsland?.updateScene?.(remote.scene);
        query('#spatial-editor-loading').hidden = true;
        query('#spatial-canvas-host').hidden = false;
        query('#spatial-save-state').textContent = '已放弃本地冲突修改 · 已载入远端最新版本';
      }
      return true;
    } catch (error) {
      freezeSceneConflict('远端版本暂时无法载入，本地修改仍保留', { allowDiscard: true });
      console.error('Infinite canvas conflict discard failed', error);
      return false;
    }
  }

  function flushScene(canvasId = currentId) {
    const requestedId = String(canvasId || '');
    clearSceneTimer(requestedId);
    const entry = pendingScenes.get(requestedId);
    if (!requestedId || !entry) return savePromise;
    const existingConflict = sceneConflicts.get(requestedId);
    if (existingConflict) {
      savePromise = savePromise
        .catch(() => {})
        .then(() => preserveSceneConflict(requestedId, existingConflict));
      return savePromise;
    }
    const saveId = entry.canvasId;
    const scene = entry.scene;
    if (pendingScenes.get(saveId) === entry) pendingScenes.delete(saveId);
    savingScenes.set(saveId, entry);
    savePromise = savePromise
      .catch(() => {})
      .then(() => adapter.updateScene(saveId, scene))
      .then((record) => {
        if (record && currentId === saveId) {
          query('#spatial-save-state').textContent = record.unchanged
            ? '画布无变化'
            : `已保存 · 版本 ${record.current_revision}`;
          syncEditorHeading();
        }
        return record;
      })
      .catch((error) => {
        const resolvedConflict = error?.status === 409 && Boolean(error.current?.scene);
        const newerPending = pendingScenes.get(saveId);
        if (resolvedConflict) {
          const localEntry = newerPending && newerPending.sequence > entry.sequence
            ? newerPending
            : entry;
          pendingScenes.set(saveId, localEntry);
          return preserveSceneConflict(saveId, sceneConflictState(saveId, error, localEntry));
        }
        if (currentId === saveId) {
          if (!newerPending || newerPending.sequence < entry.sequence) {
            pendingScenes.set(saveId, entry);
          }
          query('#spatial-save-state').textContent = '保存失败 · 等待下次修改重试';
        } else if (!newerPending || newerPending.sequence < entry.sequence) {
          pendingScenes.set(saveId, entry);
        }
        console.error('Infinite canvas scene save failed', error);
        return null;
      })
      .finally(() => {
        if (savingScenes.get(saveId) === entry) savingScenes.delete(saveId);
      });
    return savePromise;
  }

  async function waitForSaveQueue() {
    while (true) {
      const observed = savePromise;
      await observed;
      if (observed === savePromise) return;
    }
  }

  async function flushCanvasForTransition(canvasId) {
    const requestedId = String(canvasId || '');
    if (!requestedId) {
      await waitForSaveQueue();
      return true;
    }
    while (true) {
      const queuedBefore = pendingScenes.get(requestedId);
      await flushScene(requestedId);
      await waitForSaveQueue();
      const queuedAfter = pendingScenes.get(requestedId);
      if (!queuedAfter) return true;
      if (queuedBefore && queuedAfter === queuedBefore) return false;
    }
  }

  async function prepareForClose() {
    const failedCanvasIds = new Set();
    while (true) {
      await waitForSaveQueue();
      const candidates = Array.from(pendingScenes.keys())
        .filter((canvasId) => !failedCanvasIds.has(canvasId));
      if (!candidates.length) break;
      for (const canvasId of candidates) {
        if (!(await flushCanvasForTransition(canvasId))) failedCanvasIds.add(canvasId);
      }
    }
    if (pendingScenes.size) {
      const error = new Error('画布仍有未保存的修改，已阻止退出');
      error.code = 'SPATIAL_CANVAS_SAVE_PENDING';
      error.canvasIds = Array.from(pendingScenes.keys());
      query('#spatial-save-state').textContent = '保存失败 · 已阻止退出，请重试';
      throw error;
    }
    return true;
  }

  function ensureRecords(force = false) {
    if (!adapter.load) return Promise.resolve(adapter.list());
    if (!recordsPromise || force) {
      query('#spatial-save-state').textContent = '正在读取画布列表';
      recordsPromise = Promise.resolve(adapter.load({ force }))
        .then((records) => {
          renderLibrary();
          query('#spatial-save-state').textContent = `${records.length} 个画布 · 已同步`;
          return records;
        })
        .catch((error) => {
          recordsPromise = null;
          query('#spatial-save-state').textContent = '画布列表读取失败';
          console.error('Infinite canvas list failed to load', error);
          return [];
        });
    }
    return recordsPromise;
  }

  async function ensureRuntime() {
    if (!runtimePromise) {
      runtimePromise = Promise.resolve()
        .then(() => runtimeLoader())
        .catch((error) => {
          runtimePromise = null;
          throw error;
        });
    }
    return runtimePromise;
  }

  async function openCanvas(id) {
    const leavingId = currentCanvasSession?.canvasId || currentId;
    if (!(await flushCanvasForTransition(leavingId))) {
      query('#spatial-save-state').textContent = '保存失败 · 已留在当前画布，请重试';
      return false;
    }
    stopVideoPolling();
    const epoch = ++openEpoch;
    currentCanvasSession = null;
    mountedIsland?.unmount?.();
    mountedIsland = null;
    islandReadyPromise = new Promise((resolve) => { resolveIslandReady = resolve; });
    renderInspector(null);
    query('#spatial-library').hidden = true;
    query('#spatial-editor').hidden = false;
    query('#btn-spatial-home').hidden = false;
    query('#spatial-editor-loading').hidden = false;
    query('#spatial-editor-loading').innerHTML = '<span></span><strong>正在载入画布</strong>';
    query('#spatial-canvas-host').hidden = true;
    query('#spatial-save-state').textContent = '正在载入画布';
    try {
      const record = await adapter.open(id);
      if (!record || epoch !== openEpoch) return;
      currentId = record.id;
      syncEditorHeading();
      const runtime = await ensureRuntime();
      const host = query('#spatial-canvas-host');
      if (!host || currentId !== record.id || epoch !== openEpoch) return;
      const pendingEntry = pendingScenes.get(record.id);
      const canvasDocument = adapter.get(record.id);
      if (pendingEntry) canvasDocument.scene = pendingEntry.scene;
      const session = { canvasId: record.id, epoch, island: null };
      currentCanvasSession = session;
      const island = runtime.mountInfiniteCanvas(host, {
        canvasDocument,
        onChange: (scene) => queueScene(scene, session),
        onOpenFineEdit: (element) => {
          if (!canvasSessionIsCurrent(session)) return undefined;
          return onFineEdit({ canvasId: session.canvasId, element });
        },
        onSelectionChange: (element) => {
          if (canvasSessionIsCurrent(session)) renderInspector(element);
        },
        resolveProxyUrl,
        resolveVideoAsset,
        onReady: () => {
          if (!canvasSessionIsCurrent(session)) return;
          documentRef.documentElement.dataset.spatialRuntime = 'loaded';
          query('#spatial-editor-loading').hidden = true;
          host.hidden = false;
          query('#spatial-save-state').textContent = '本次会话 · 已打开';
          if (pendingEntry) queueScene(pendingEntry.scene, session);
          resolveIslandReady?.();
          resolveIslandReady = null;
          scanCurrentCanvasVideoJobs(session).catch((error) => {
            console.error('Infinite canvas video recovery failed', error);
          });
        },
      });
      session.island = island;
      mountedIsland = island;
    } catch (error) {
      if (epoch !== openEpoch) return;
      resolveIslandReady?.();
      resolveIslandReady = null;
      query('#spatial-editor-loading').hidden = false;
      query('#spatial-editor-loading').innerHTML = '<strong>画布加载失败</strong><button type="button" data-spatial-retry>重试</button>';
      query('#spatial-save-state').textContent = '画布暂不可用';
      console.error('Infinite canvas runtime failed to load', error);
    }
  }

  async function ensureCanvasForImport() {
    await ensureRecords();
    let targetId = currentId || adapter.list()[0]?.id || '';
    if (!targetId) {
      const record = await adapter.create({ name: `未命名画布 ${adapter.list().length + 1}` });
      renderLibrary();
      targetId = record.id;
    }
    if (!mountedIsland || currentId !== targetId) await openCanvas(targetId);
    await islandReadyPromise;
    const session = captureCanvasSession();
    if (!session || session.canvasId !== targetId) throw new Error('画布已切换，请重新加入内容');
    return session;
  }

  async function addBusinessItems(items, targetSession = null) {
    const normalized = Array.from(items || []).filter(Boolean);
    if (!normalized.length) return null;
    try {
      const session = targetSession || await ensureCanvasForImport();
      if (!canvasSessionIsCurrent(session)) {
        query('#spatial-save-state').textContent = '素材已导入；画布已切换，未加入节点';
        return { skipped: true, reason: 'canvas-switched' };
      }
      query('#spatial-save-state').textContent = `${normalized.length} 项已加入 · 正在保存`;
      const result = await session.island.addBusinessItems(normalized);
      return canvasSessionIsCurrent(session) ? result : null;
    } catch (error) {
      query('#spatial-save-state').textContent = '内容未能加入画布';
      console.error('Infinite canvas business import failed', error);
      return null;
    }
  }

  async function addBusinessItemsOnce(items) {
    const normalized = Array.from(items || []).filter(Boolean);
    if (!normalized.length) return null;
    try {
      const session = await ensureCanvasForImport();
      query('#spatial-save-state').textContent = `${normalized.length} 项正在核对并保存`;
      const result = await session.island.addBusinessItemsOnce(normalized);
      return canvasSessionIsCurrent(session) ? result : null;
    } catch (error) {
      query('#spatial-save-state').textContent = '内容未能加入画布';
      console.error('Infinite canvas idempotent import failed', error);
      return null;
    }
  }

  function setFileDropActive(value) {
    const host = query('#spatial-canvas-host');
    if (host) host.dataset.fileDropActive = value ? 'true' : 'false';
    const status = query('#spatial-save-state');
    if (!status) return;
    if (value) status.textContent = '正在接收图片';
    else if (status.textContent === '正在接收图片') status.textContent = '本次会话 · 已打开';
  }

  async function onDrop(event) {
    if (!active) return;
    const transfer = event.dataTransfer;
    const transferTypes = Array.from(transfer?.types || []);
    const files = Array.from(transfer?.files || []);
    const hasFileTransfer = files.length > 0 || transferTypes.includes('Files');
    const payload = transfer?.getData(SPATIAL_DRAG_MIME);
    if (!payload && !hasFileTransfer) return;
    event.preventDefault();
    event.stopPropagation();
    if (hasFileTransfer) {
      setFileDropActive(false);
      if (!files.length) {
        query('#spatial-save-state').textContent = '没有读取到可导入的图片';
        return;
      }
      try {
        const targetSession = await ensureCanvasForImport();
        query('#spatial-save-state').textContent = `正在导入 ${files.length} 张图片`;
        const items = Array.from(await onImportFiles(files) || []).filter(Boolean);
        if (!items.length) {
          query('#spatial-save-state').textContent = '没有可加入画布的图片';
          return;
        }
        await addBusinessItems(items, targetSession);
      } catch (error) {
        query('#spatial-save-state').textContent = '图片导入失败，请重试';
        console.error('Infinite canvas file import failed', error);
      }
      return;
    }
    setFileDropActive(false);
    try { await addBusinessItems([parseSpatialDragItem(payload)]); }
    catch (error) { console.error('Infinite canvas drop payload was rejected', error); }
  }

  function onDragOver(event) {
    if (!active) return;
    const transferTypes = Array.from(event.dataTransfer?.types || []);
    if (!transferTypes.includes(SPATIAL_DRAG_MIME) && !transferTypes.includes('Files')) return;
    event.preventDefault();
    if (event.dataTransfer) event.dataTransfer.dropEffect = 'copy';
    if (transferTypes.includes('Files')) setFileDropActive(true);
  }

  function onDragLeave(event) {
    if (active && !event.relatedTarget) setFileDropActive(false);
  }

  async function createCanvas() {
    const ordinal = adapter.list().length + 1;
    const buttons = [query('#btn-spatial-new'), query('#btn-spatial-empty-new')];
    buttons.forEach((button) => { button.disabled = true; });
    query('#spatial-save-state').textContent = '正在新建画布';
    try {
      const record = await adapter.create({ name: `未命名画布 ${ordinal}` });
      renderLibrary();
      return await openCanvas(record.id);
    } catch (error) {
      query('#spatial-save-state').textContent = '新建画布失败';
      console.error('Infinite canvas creation failed', error);
      return null;
    } finally {
      buttons.forEach((button) => { button.disabled = false; });
    }
  }

  function beginRename(id, returnFocus) {
    const record = adapter.get(id);
    if (!record) return;
    renameReturnFocus = returnFocus || documentRef.activeElement;
    const form = query('#spatial-rename-form');
    const input = query('#spatial-rename-input');
    form.dataset.canvasId = record.id;
    input.value = record.name;
    form.hidden = false;
    windowRef.requestAnimationFrame(() => { input.focus(); input.select(); });
  }

  function closeRename(restoreFocus = true) {
    query('#spatial-rename-form').hidden = true;
    query('#spatial-rename-form').dataset.canvasId = '';
    if (restoreFocus) renameReturnFocus?.focus?.();
    renameReturnFocus = null;
  }

  async function submitRename(event) {
    event.preventDefault();
    const form = query('#spatial-rename-form');
    const buttons = form.querySelectorAll('button');
    buttons.forEach((button) => { button.disabled = true; });
    try {
      const record = await adapter.rename(
        form.dataset.canvasId,
        query('#spatial-rename-input').value,
      );
      if (!record) return closeRename();
      renderLibrary();
      syncEditorHeading();
      query('#spatial-save-state').textContent = '画布已重命名';
      closeRename();
    } catch (error) {
      query('#spatial-save-state').textContent = '重命名失败';
      console.error('Infinite canvas rename failed', error);
    } finally {
      buttons.forEach((button) => { button.disabled = false; });
    }
  }

  function onClick(event) {
    if (event.target.closest('[data-spatial-video-cancel]')) {
      videoDraft = null;
      videoDraftError = '';
      return renderInspector(selectedElement);
    }
    const inspectorAction = event.target.closest('[data-spatial-action]');
    if (inspectorAction && selectedElement) {
      if (inspectorAction.dataset.spatialAction === 'toggle-video') {
        return mountedIsland?.toggleVideo?.(selectedElement.id);
      }
      return onAction(inspectorAction.dataset.spatialAction, {
        canvasId: currentId,
        element: selectedElement,
      });
    }
    if (event.target.closest('[data-spatial-inspector-close]')) return renderInspector(null);
    const openButton = event.target.closest('[data-spatial-open]');
    if (openButton) return openCanvas(openButton.dataset.spatialOpen);
    const renameButton = event.target.closest('[data-spatial-rename]');
    if (renameButton) return beginRename(renameButton.dataset.spatialRename, renameButton);
    if (event.target.closest('[data-spatial-conflict-discard]') && currentId) {
      return discardSceneConflict(currentId);
    }
    if (event.target.closest('[data-spatial-retry]') && currentId) return openCanvas(currentId);
  }

  function onInput(event) {
    const control = event.target.closest('[data-spatial-video-field]');
    if (control) updateVideoDraftFromField(control);
  }

  function onChange(event) {
    const confirmation = event.target.closest('[data-spatial-video-confirm]');
    if (confirmation) return confirmVideoDraft(confirmation);
    const control = event.target.closest('[data-spatial-video-field]');
    if (control) updateVideoDraftFromField(control);
  }

  function onSubmit(event) {
    if (event.target.matches('[data-spatial-video-form]')) submitVideoDraft(event);
  }

  function bind() {
    if (bound) return;
    bound = true;
    query('#btn-spatial-new').addEventListener('click', createCanvas);
    query('#btn-spatial-empty-new').addEventListener('click', createCanvas);
    query('#btn-spatial-home').addEventListener('click', () => showLibrary({ restoreFocus: true }));
    query('#btn-spatial-rename').addEventListener('click', (event) => beginRename(currentId, event.currentTarget));
    query('#spatial-rename-form').addEventListener('submit', submitRename);
    query('#spatial-rename-cancel').addEventListener('click', () => closeRename());
    query('#spatial-rename-input').addEventListener('keydown', (event) => {
      if (event.key === 'Escape') { event.preventDefault(); closeRename(); }
    });
    query('#page-canvas').addEventListener('click', onClick);
    query('#page-canvas').addEventListener('input', onInput);
    query('#page-canvas').addEventListener('change', onChange);
    query('#page-canvas').addEventListener('submit', onSubmit);
    documentRef.addEventListener('dragover', onDragOver);
    documentRef.addEventListener('dragleave', onDragLeave);
    documentRef.addEventListener('drop', onDrop);
    renderLibrary();
  }

  function setPage(isActive) {
    active = Boolean(isActive);
    if (!active) {
      stopVideoPolling();
      mountedIsland?.stopVideo?.();
      flushScene(currentCanvasSession?.canvasId || currentId);
      closeRename(false);
      return;
    }
    ensureRecords().then(() => {
      if (!active) return;
      if (!currentId) showLibrary();
      else if (mountedIsland) {
        const session = captureCanvasSession();
        if (session) scanCurrentCanvasVideoJobs(session).catch((error) => {
          console.error('Infinite canvas video recovery failed', error);
        });
      }
      windowRef.requestAnimationFrame(() => {
        const target = query('#spatial-editor').hidden
          ? query('#btn-spatial-new')
          : query('#spatial-canvas-host');
        target?.focus?.({ preventScroll: true });
      });
    });
  }

  function destroy() {
    stopVideoPolling();
    flushScene(currentCanvasSession?.canvasId || currentId);
    documentRef.removeEventListener('dragover', onDragOver);
    documentRef.removeEventListener('dragleave', onDragLeave);
    documentRef.removeEventListener('drop', onDrop);
    currentCanvasSession = null;
    mountedIsland?.unmount?.();
    mountedIsland = null;
  }

  return {
    adapter,
    addBusinessItems,
    addBusinessItemsOnce,
    bind,
    createCanvas,
    destroy,
    flush: flushScene,
    openCanvas,
    openVideoComposer,
    openVideoJob,
    prepareForClose,
    setPage,
    showLibrary,
    get active() { return active; },
    get videoRecoveryPending() { return videoRecoveryPending; },
    get currentId() { return currentId; },
    get runtimeLoaded() { return Boolean(runtimePromise); },
  };
}
