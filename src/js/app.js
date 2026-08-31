import * as API from './api.js';
import {
  collectResultItems,
  comparisonPresentation,
  createSubmissionSnapshot,
  itemCompletionProgress,
  jobCompletionProgress,
  jobLifecycleActions,
  jobsRenderSignature,
  knowledgeBundleFromEvidence,
  multiFileOutputPlan,
  normalizeFeedbackSignal,
  processResultItems,
  queueCompletionProgress,
  selectionAfterImport,
  selectionForRestoredResult,
  submissionFingerprint,
} from './workspace-state.js';
import { memoryProjectionState } from './memory-projection.js';
import {
  memoryGovernancePresentation,
  memorySuggestionsForFilter,
} from './memory-governance.js';
import {
  DEFAULT_APPEARANCE,
  appearanceSettingsHtml,
  appearanceStatusCopy,
  explicitThemeAfterToggle,
  readAppearancePreferences,
  resolveAppearancePreferences,
} from './appearance.js';
import {
  backendDecisionForSignal,
  comparisonTargetForItems,
  feedbackReceiptCopy,
  normalizeCompareState,
  normalizeReviewReasonCodes,
  reviewReasonLabel,
  reviewReasonOptions,
  reviewStateForResult,
} from './result-review.js';
import {
  completionRequestKey,
  locateResultVersion,
  selectRestorableResult,
} from './workspace-lifecycle.js';
import { boundedAssetRenderList, createAssetManagerController } from './studio-assets.js';
import { JOB_STATUS, MODE_CONFIG, MODE_IDS, PAGE_CONFIG, STAGE_IDS } from './studio-config.js';
import {
  boundedJobsForDisplay,
  jobFilterCounts,
  jobItemsForDisplay,
  jobsForFilter,
  jobSourceIds,
  jobWorkspaceSnapshot,
} from './studio-jobs.js';
import { createSettingsController } from './studio-settings.js';
import { createWorkflowDockController } from './studio-shell.js';
import { createStudioState, draftPayloadFromSnapshot, snapshotFromDraft } from './studio-state.js';
import { statusPanelHtml } from './status-view.js';
import {
  createSemanticCutoutState,
  semanticCutoutPayload,
  semanticCutoutReadiness,
  semanticCutoutStageCopy,
  semanticGroundingPresentation,
  updateSemanticCutoutState,
} from './semantic-cutout.js';

const MODE_STATE_KEY = 'pa-workspace-ui-v2';
const PENDING_SUBMISSION_KEY = 'pa-pending-job-v1';
const PENDING_REVIEW_KEY = 'pa-pending-result-reviews-v1';
const $ = (selector, root = document) => root.querySelector(selector);
const $$ = (selector, root = document) => Array.from(root.querySelectorAll(selector));
let modalReturnFocus = null;
let drawerReturnFocus = null;
let reviewGuideReturnFocus = null;
let semanticReturnFocus = null;
let workspaceStatusTimer = null;
const semanticCanvasState = {
  image: null,
  assetId: '',
  query: '',
  modelQuery: '',
  modelQueryOverride: '',
  targetCount: 1,
  regions: [],
  suggestedRegions: [],
  maskEdits: [],
  maskImage: null,
  maskStatus: '确认目标框后，可预览绿色蒙版并修正边缘。',
  maskPreviewRevision: 0,
  tool: 'box',
  brushRadius: 0.018,
  stroke: null,
  groundingStatus: 'unavailable',
  groundingTone: 'manual',
  groundingMessage: '请手动框选目标',
  manualRevision: 0,
  previewRevision: 0,
  dragStart: null,
  dragCurrent: null,
};

const state = createStudioState(MODE_IDS);
const workflowDock = createWorkflowDockController();
const settingsController = createSettingsController({
  api: API,
  state,
  query: $,
  toast,
  updateQuickControls,
  compileKnowledgePreview,
});
const assetManager = createAssetManagerController({
  api: API,
  state,
  query: $,
  queryAll: $$,
  modeConfig: MODE_CONFIG,
  escapeHtml,
  assetUrl,
  hydrateAssetUrls,
  loadWorkspace,
  syncLegacySelection,
  renderQueue,
  toggleAssetSelection,
  toast,
  openDrawer,
});
window.ProductAtelier = { state };

function escapeHtml(value) {
  const node = document.createElement('div');
  node.textContent = String(value ?? '');
  return node.innerHTML;
}

function selectedAssetIds(mode = state.currentMode) {
  return state.modeSelections[mode] || [];
}

function selectedAssets(mode = state.currentMode) {
  const ids = new Set(selectedAssetIds(mode));
  return state.assets.filter((asset) => ids.has(asset.id));
}

function cutoutSelectionState() {
  const snapshot = state.modeSnapshots['cutout-batch'];
  const durable = state.workspaceDrafts['cutout-batch'];
  return createSemanticCutoutState(snapshot?.mask_state || durable?.mask_state || {});
}

function setCutoutSelectionState(next, persist = true) {
  const currentSnapshot = state.modeSnapshots['cutout-batch']
    || snapshotFromDraft(state.workspaceDrafts['cutout-batch'] || {}, {});
  state.modeSnapshots['cutout-batch'] = {
    ...currentSnapshot,
    mask_state: createSemanticCutoutState(next),
  };
  renderCutoutControls();
  if (persist) {
    persistWorkspaceState();
    scheduleWorkspaceDraftSave('cutout-batch');
  }
  return state.modeSnapshots['cutout-batch'].mask_state;
}

function folderBatchForMode(mode = state.currentMode, readyOnly = false) {
  const batch = state.folderBatches?.[mode];
  if (!batch || !Array.isArray(batch.asset_ids) || !batch.asset_ids.length) return null;
  if (readyOnly && batch.status && batch.status !== 'ready') return null;
  return batch;
}

function sourceAssetIdsForSubmission(mode = state.currentMode) {
  const folderBatch = mode === 'multi-file' ? folderBatchForMode(mode, true) : null;
  return folderBatch ? [...folderBatch.asset_ids] : selectedAssetIds(mode);
}

function createClientRequestId() {
  if (globalThis.crypto?.randomUUID) return globalThis.crypto.randomUUID();
  return `web-${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

function restorePendingSubmission() {
  try {
    const saved = JSON.parse(localStorage.getItem(PENDING_SUBMISSION_KEY) || 'null');
    if (
      saved && typeof saved === 'object'
      && typeof saved.fingerprint === 'string'
      && typeof saved.requestId === 'string'
      && saved.payload && typeof saved.payload === 'object'
    ) state.pendingSubmission = saved;
  } catch (_) { /* an obsolete pending request should not block startup */ }
}

function persistPendingSubmission(pending) {
  state.pendingSubmission = pending;
  try {
    if (pending) localStorage.setItem(PENDING_SUBMISSION_KEY, JSON.stringify(pending));
    else localStorage.removeItem(PENDING_SUBMISSION_KEY);
  } catch (_) { /* in-memory retry protection remains available */ }
}

function clearPendingSubmission(requestId) {
  if (!state.pendingSubmission || state.pendingSubmission.requestId !== requestId) return;
  persistPendingSubmission(null);
}

function isDefinitiveJobRejection(error) {
  const status = Number(error?.status || 0);
  return status >= 400 && status < 500 && ![408, 429].includes(status);
}

function persistWorkspaceState() {
  try {
    localStorage.setItem(MODE_STATE_KEY, JSON.stringify({
      currentMode: state.currentMode,
    }));
  } catch (_) { /* local preferences are best effort */ }
}

function restorePendingReviewRequests() {
  try {
    const saved = JSON.parse(localStorage.getItem(PENDING_REVIEW_KEY) || '{}');
    if (saved && typeof saved === 'object' && !Array.isArray(saved)) {
      state.pendingReviewRequests = saved;
    }
  } catch (_) { /* obsolete retry data must not block startup */ }
}

function persistPendingReviewRequests() {
  try {
    const keys = Object.keys(state.pendingReviewRequests || {});
    if (keys.length) localStorage.setItem(PENDING_REVIEW_KEY, JSON.stringify(state.pendingReviewRequests));
    else localStorage.removeItem(PENDING_REVIEW_KEY);
  } catch (_) { /* in-memory retry protection remains available */ }
}

function reviewRequestId(resultAssetId, payload) {
  const key = String(resultAssetId || '');
  const fingerprint = JSON.stringify({
    generation_id: payload.generation_id || null,
    decision: payload.decision,
    reason_codes: payload.reason_codes || [],
    note: payload.note || '',
    learning_action: payload.learning_action || 'none',
  });
  const existing = state.pendingReviewRequests[key];
  if (existing?.fingerprint === fingerprint && existing?.requestId) return existing.requestId;
  const requestId = createClientRequestId();
  state.pendingReviewRequests[key] = { fingerprint, requestId };
  persistPendingReviewRequests();
  return requestId;
}

function clearPendingReviewRequest(resultAssetId, requestId) {
  const key = String(resultAssetId || '');
  if (state.pendingReviewRequests[key]?.requestId !== requestId) return;
  delete state.pendingReviewRequests[key];
  persistPendingReviewRequests();
}

function discardPendingReviewRequest(resultAssetId) {
  const key = String(resultAssetId || '');
  if (!state.pendingReviewRequests[key]) return;
  delete state.pendingReviewRequests[key];
  persistPendingReviewRequests();
}

function restoreWorkspaceState() {
  try {
    const saved = JSON.parse(localStorage.getItem(MODE_STATE_KEY) || '{}');
    if (MODE_CONFIG[saved.currentMode]) state.currentMode = saved.currentMode;
  } catch (_) { /* ignore an obsolete local snapshot */ }
}

function syncLegacySelection() {
  state.selectedFiles = selectedAssets();
  state.originalDataUrl = state.selectedFiles[0]?.content_url || '';
}

function formatApiError(error, fallback = '本地服务暂不可用') {
  const message = String(error?.message || error || fallback).replace(/^Error:\s*/, '');
  if (error?.code === 'REQUEST_TIMEOUT') return `${fallback}（请求超时）`;
  if (/HTTP 404/.test(message)) return `${fallback}（接口尚未就绪）`;
  if (/Failed to fetch|NetworkError|Load failed/i.test(message)) return `${fallback}（无法连接后端）`;
  return message.slice(0, 280);
}

function setWorkspaceSyncState(kind = '', options = {}) {
  const host = $('#workspace-sync-state');
  if (!host) return;
  if (workspaceStatusTimer) window.clearTimeout(workspaceStatusTimer);
  workspaceStatusTimer = null;
  if (!kind) {
    host.hidden = true;
    host.replaceChildren();
    delete host.dataset.kind;
    return;
  }
  host.dataset.kind = kind;
  host.innerHTML = statusPanelHtml(kind, { ...options, compact: true, inline: true });
  host.hidden = false;
  const autoHide = Number(options.autoHide || 0);
  if (autoHide > 0) workspaceStatusTimer = window.setTimeout(() => {
    if (host.dataset.kind === kind) setWorkspaceSyncState();
  }, autoHide);
}

function captureModeSnapshot(mode = state.currentMode) {
  if (!MODE_CONFIG[mode] || !$('#brief-input')) return;
  const previous = state.modeSnapshots[mode] || {};
  const next = {
    brief: $('#brief-input').value,
    model: $('#param-model').value,
    angle: $('#param-angle').value,
    output_ratio: $('#param-output-ratio').value,
    output_resolution: $('#param-output-resolution').value,
    fidelity: Number($('#param-fidelity').value),
    batch: Number($('#param-batch').value),
    platter: getPlatter(),
    refine: $('#param-refine').checked,
    intent_locks: getIntentLocks(),
    active_job_id: previous.active_job_id || state.workspaceDrafts[mode]?.active_job_id || null,
    current_generation_id: previous.current_generation_id || state.workspaceDrafts[mode]?.current_generation_id || null,
    current_result_asset_id: previous.current_result_asset_id || state.workspaceDrafts[mode]?.current_result_asset_id || null,
    compare_state: previous.compare_state || state.workspaceDrafts[mode]?.compare_state || {},
    ui_state: {
      ...(previous.ui_state || state.workspaceDrafts[mode]?.ui_state || {}),
      folder_batch: mode === 'multi-file' ? (state.folderBatches[mode] || null) : null,
    },
    mask_state: previous.mask_state || state.workspaceDrafts[mode]?.mask_state || {},
  };
  state.modeSnapshots[mode] = next;
  if (JSON.stringify(previous) !== JSON.stringify(next)) scheduleWorkspaceDraftSave(mode);
}

function modeBrief(mode) {
  if (mode === state.currentMode && $('#brief-input')) return buildBrief(mode);
  const snapshot = state.modeSnapshots[mode] || {};
  return {
    objective: snapshot.brief || '将产品原图转化为可交付的商业图片',
    user_request: snapshot.brief || '',
    mode,
    category: 'general',
    platform: 'ecommerce',
    output_kind: MODE_CONFIG[mode].outputKind,
    angle: snapshot.angle || 'auto',
    output_ratio: snapshot.output_ratio || 'original',
    output_resolution: snapshot.output_resolution || '2k',
    platter: snapshot.platter || 'auto',
    fidelity: Number(snapshot.fidelity ?? 40),
    intent_locks: snapshot.intent_locks || {},
    output_spec: {
      ratio: snapshot.output_ratio || 'original',
      resolution: snapshot.output_resolution || '2k',
      format: mode === 'cutout-batch' ? 'transparent PNG' : 'JPG+transparent PNG',
    },
  };
}

function draftSavePayload(mode) {
  return draftPayloadFromSnapshot({
    revision: state.workspaceRevisions[mode],
    selectedAssetIds: selectedAssetIds(mode),
    snapshot: state.modeSnapshots[mode],
    brief: modeBrief(mode),
  });
}

function scheduleWorkspaceDraftSave(mode = state.currentMode, delay = 320) {
  if (state.hydratingWorkspace || !state.backendReady || !MODE_CONFIG[mode]) return;
  state.draftSaveVersions[mode] += 1;
  const previous = state.draftSaveTimers.get(mode);
  if (previous) window.clearTimeout(previous);
  state.draftSaveTimers.set(mode, window.setTimeout(() => {
    state.draftSaveTimers.delete(mode);
    flushWorkspaceDraft(mode, true);
  }, delay));
}

async function flushWorkspaceDraft(mode = state.currentMode, silent = false) {
  if (!state.backendReady || !MODE_CONFIG[mode]) return null;
  const timer = state.draftSaveTimers.get(mode);
  if (timer) window.clearTimeout(timer);
  state.draftSaveTimers.delete(mode);
  if (state.draftSavesInFlight.has(mode)) {
    state.draftSaveQueued.add(mode);
    return null;
  }
  state.draftSavesInFlight.add(mode);
  const saveVersion = state.draftSaveVersions[mode];
  try {
    const response = await API.saveWorkspaceDraft(mode, draftSavePayload(mode));
    const draft = response?.draft || response;
    state.workspaceDrafts[mode] = draft;
    state.workspaceRevisions[mode] = Number(draft?.revision || state.workspaceRevisions[mode]);
    if (state.draftConflictModes.delete(mode) && mode === state.currentMode) {
      setWorkspaceSyncState('recovered', {
        title: '修改已安全合并',
        detail: `已保存为 revision ${state.workspaceRevisions[mode]}，本次内容没有丢失。`,
        autoHide: 5200,
      });
    }
    return draft;
  } catch (error) {
    if (Number(error?.status) === 409 && error?.detail?.current) {
      const current = error.detail.current;
      state.workspaceDrafts[mode] = current;
      state.workspaceRevisions[mode] = Number(current.revision || 1);
      state.draftConflictModes.add(mode);
      if (mode === state.currentMode) setWorkspaceSyncState('conflict', {
        title: '检测到另一处更新',
        detail: `已读取 revision ${state.workspaceRevisions[mode]}，正在合并本次修改。`,
      });
      state.draftSaveQueued.add(mode);
    } else {
      if (mode === state.currentMode) setWorkspaceSyncState('error', {
        title: '本次修改尚未保存',
        detail: formatApiError(error, '工作区暂不可写'),
        action: { label: '重新保存', attribute: 'data-workspace-status-action', value: 'retry-save' },
      });
      if (!silent) toast(`草稿保存失败：${formatApiError(error, '工作区暂不可写')}`, 'error');
    }
    return null;
  } finally {
    state.draftSavesInFlight.delete(mode);
    if (state.draftSaveQueued.delete(mode) || saveVersion !== state.draftSaveVersions[mode]) {
      scheduleWorkspaceDraftSave(mode, 0);
    }
  }
}

async function settleWorkspaceDraft(mode = state.currentMode) {
  for (let pass = 0; pass < 4; pass += 1) {
    if (state.draftSaveTimers.has(mode) || state.draftSaveQueued.has(mode)) {
      await flushWorkspaceDraft(mode, true);
    }
    const deadline = performance.now() + 5000;
    while (state.draftSavesInFlight.has(mode) && performance.now() < deadline) {
      await new Promise((resolve) => window.setTimeout(resolve, 25));
    }
    if (!state.draftSavesInFlight.has(mode)
      && !state.draftSaveTimers.has(mode)
      && !state.draftSaveQueued.has(mode)) return true;
  }
  return false;
}

function hydrateWorkspace(mode, payload) {
  const draft = payload?.draft || {};
  const collection = payload?.collection || MODE_CONFIG[mode].collection;
  const assets = Array.isArray(payload?.assets) ? payload.assets : [];
  const activeAssetIds = new Set(assets.map((asset) => String(asset.id)));
  state.hydratingWorkspace = true;
  try {
    state.workspaceDrafts[mode] = draft;
    state.workspaceRevisions[mode] = Number(draft.revision || 1);
    state.modeSelections[mode] = Array.from(draft.selected_asset_ids || [], String)
      .filter((assetId) => activeAssetIds.has(assetId))
      .slice(0, MODE_CONFIG[mode].maxFiles);
    state.modeSnapshots[mode] = snapshotFromDraft(draft, state.modeSnapshots[mode] || {});
    state.folderBatches[mode] = mode === 'multi-file'
      ? (state.modeSnapshots[mode]?.ui_state?.folder_batch || null)
      : null;
    state.assetsByCollection[collection] = assets;
    if (mode === state.currentMode) {
      state.resultReviews = Array.isArray(payload?.recent_reviews) ? payload.recent_reviews : [];
    }
    if (MODE_CONFIG[state.currentMode]?.collection === collection) state.assets = assets;
    state.workspaceLoaded.add(mode);
    state.restoredModes.add(mode);
  } finally {
    state.hydratingWorkspace = false;
  }
}

async function loadWorkspace(mode = state.currentMode, silent = false) {
  if (!MODE_CONFIG[mode]) return false;
  const requestVersion = ++state.workspaceRequestVersions[mode];
  const wasLoaded = state.workspaceLoaded.has(mode);
  try {
    const payload = await API.getWorkspace(mode, { timeoutMs: 12000 });
    if (requestVersion !== state.workspaceRequestVersions[mode]) return null;
    hydrateWorkspace(mode, payload);
    await hydrateAssetUrls(payload.assets || []);
    const currentCollectionMatches = MODE_CONFIG[state.currentMode]?.collection === (payload.collection || MODE_CONFIG[mode].collection);
    if (mode === state.currentMode || currentCollectionMatches) {
      state.assets = state.assetsByCollection[MODE_CONFIG[state.currentMode].collection] || [];
      state.assetsAvailable = true;
      state.hydratingWorkspace = true;
      try {
        if (mode === state.currentMode) {
          restoreModeSnapshot(mode);
          renderFolderSource();
        }
        syncLegacySelection();
        renderQueue();
        updateQuickControls();
      } finally {
        state.hydratingWorkspace = false;
      }
      if (mode === state.currentMode) {
        const activeJob = payload.active_jobs?.[0] || null;
        state.currentTaskId = draftActiveJobId(mode) || activeJob?.id || '';
        state.currentSessionId = activeJob?.session_id || '';
        const restored = await restoreWorkspaceResult(mode, payload);
        if (!restored) clearVisibleResultContext(activeJob);
        const restoredContent = Boolean(
          (payload.draft?.selected_asset_ids || []).length
          || payload.draft?.active_job_id
          || payload.draft?.current_result_asset_id
          || activeJob,
        );
        if (!wasLoaded && restoredContent) setWorkspaceSyncState('recovered', {
          title: '上次工作现场已恢复',
          detail: restored ? '素材、参数、任务与当前结果均已恢复。' : '素材、参数与任务进度均已恢复。',
          autoHide: 5200,
        });
        else if ($('#workspace-sync-state')?.dataset.kind === 'offline') setWorkspaceSyncState('recovered', {
          title: '连接已恢复',
          detail: '工作区与本地账本已重新同步。',
          autoHide: 4200,
        });
      }
    }
    return true;
  } catch (error) {
    if (requestVersion !== state.workspaceRequestVersions[mode]) return null;
    if (mode === state.currentMode) state.assetsAvailable = false;
    if (mode === state.currentMode) setWorkspaceSyncState('offline', {
      title: '当前工作区暂时离线',
      detail: '素材与当前选择仍在本机；恢复连接后可继续。',
      action: { label: '重试连接', attribute: 'data-workspace-status-action', value: 'retry' },
    });
    if (!silent) toast(`工作区读取失败：${formatApiError(error, '持久工作区接口不可用')}`, 'error', 6000);
    return false;
  }
}

function clearVisibleResultContext(activeJob = null) {
  state.results = null;
  state.compareData = null;
  state.originalDataUrl = '';
  state.currentGenerationId = '';
  state.knowledgeBundle = null;
  state.feedbackResultKey = '';
  state.feedbackRecorded = false;
  state.feedbackReceipt = '';
  state.feedbackSuggestionId = '';
  state.lastFeedbackSignal = '';
  state.editingFeedbackResultKey = '';
  state.viewerIndex = 0;
  const nextStage = activeJob
    ? 'processing'
    : selectedAssetIds().length ? 'ready' : 'empty';
  setStage(nextStage);
}

function draftActiveJobId(mode = state.currentMode) {
  return state.workspaceDrafts[mode]?.active_job_id || state.modeSnapshots[mode]?.active_job_id || '';
}

async function restoreWorkspaceResult(mode, payload) {
  if (mode !== state.currentMode || !Array.isArray(payload?.jobs)) return false;
  const preferredResultId = payload?.draft?.current_result_asset_id || '';
  const job = selectRestorableResult(payload.jobs, payload?.draft || {});
  if (!job) return false;
  const resultIds = resultIdsForJob(job);
  const adjustment = adjustmentLineage(job);
  const ownVersion = Number(adjustment?.version || 0);
  const generationByResult = new Map(
    (job.items || []).flatMap((jobItem) => (
      (jobItem.result_asset_ids || []).map((assetId) => [String(assetId), jobItem.generation_id || ''])
    )),
  );
  const recentById = new Map((payload.recent_results || []).map((asset) => [asset.id, asset]));
  const assets = resultIds.map((assetId, index) => {
    const asset = recentById.get(assetId) || {};
    return {
      asset_id: assetId,
      name: asset.name || `product-atelier-${index + 1}.${mode === 'cutout-batch' ? 'png' : 'jpg'}`,
      url: asset.content_url || '',
      content_url: asset.content_url || '',
      thumbnail_url: asset.thumbnail_url || '',
      role: asset.role || (mode === 'cutout-batch' ? 'result_cutout' : 'result_main'),
      id: assetId,
      generation_id: generationByResult.get(String(assetId)) || '',
      job_id: job.id,
      width: asset.width || null,
      height: asset.height || null,
      version_label: ownVersion ? `V${ownVersion}` : '',
    };
  });
  const parentResultId = String(adjustment?.parent_result_asset_id || '');
  if (parentResultId && !assets.some((asset) => asset.asset_id === parentResultId)) {
    try {
      const parentItem = await fetchResultItem(parentResultId, null, 0, {
        job_id: String(adjustment?.parent_job_id || ''),
        generation_id: String(adjustment?.parent_generation_id || ''),
        version_label: `V${Math.max(1, ownVersion - 1)}`,
        is_parent_version: true,
      });
      assets.unshift(parentItem);
    } catch (_) { /* the new result remains recoverable even if its parent file moved */ }
  }
  await hydrateAssetUrls(assets);
  assets.forEach((asset) => { asset.url = asset.content_url || asset.url || ''; });
  const rawJobSourceIds = (Array.isArray(job.snapshot?.source_asset_ids)
    ? job.snapshot.source_asset_ids
    : (job.items || []).map((item) => item.source_asset_id));
  const jobSourceIds = selectionForRestoredResult(
    [],
    rawJobSourceIds,
    state.assets.map((asset) => asset.id),
    MODE_CONFIG[mode].maxFiles,
  );
  const restoredSelection = selectionForRestoredResult(
    selectedAssetIds(mode),
    rawJobSourceIds,
    state.assets.map((asset) => asset.id),
    MODE_CONFIG[mode].maxFiles,
  );
  if (!selectedAssetIds(mode).length && restoredSelection.length) {
    state.modeSelections[mode] = restoredSelection;
    syncLegacySelection();
    renderFileMeta();
    updateCtaState();
  }
  const source = findJobSourceAsset(rawJobSourceIds[0])
    || state.assets.find((asset) => asset.id === jobSourceIds[0])
    || selectedAssets(mode)[0];
  state.originalDataUrl = assetUrl(source, 'content');
  state.currentTaskId = job.id;
  state.currentSessionId = job.session_id || '';
  state.currentGenerationId = job.items?.[0]?.generation_id || '';
  state.results = {
    main: assets.filter((item) => item.role !== 'result_cutout'),
    cutout: assets.filter((item) => item.role === 'result_cutout'),
  };
  state.resultTab = mode === 'cutout-batch' || !state.results.main.length ? 'cutout' : 'main';
  const visibleItems = state.results[state.resultTab] || [];
  const preferredIndex = visibleItems.findIndex((asset) => asset.asset_id === preferredResultId);
  const ownIndex = visibleItems.findIndex((asset) => asset.job_id === job.id);
  state.viewerIndex = Math.max(0, preferredIndex >= 0 ? preferredIndex : ownIndex);
  state.currentGenerationId = visibleItems[state.viewerIndex]?.generation_id || state.currentGenerationId;
  try {
    const traceResponse = await API.getJobTraces(job.id);
    applyTaskKnowledgeBundle(knowledgeBundleFromEvidence({
      brief: job.snapshot?.brief || {},
      traces: traceResponse?.traces || [],
      generation: { knowledge_refs: job.snapshot?.knowledge_refs || [] },
    }));
  } catch (_) {
    applyTaskKnowledgeBundle(knowledgeBundleFromEvidence({
      brief: job.snapshot?.brief || {},
      generation: { knowledge_refs: job.snapshot?.knowledge_refs || [] },
    }));
  }
  renderResults();
  setStage('success');
  $('#summary-result').textContent = adjustment
    ? `新版本 V${ownVersion} 已从任务账本恢复`
    : `${assets.length} 个结果已从任务账本恢复`;
  $('#summary-result-note').textContent = adjustment
    ? '上一版本已并入版本对比；原任务、原图和反馈记录保持不变'
    : (jobSourceIds.length
      ? `${jobSourceIds.length} 张源图与当前结果已恢复，可继续调整或开始下一项`
      : '结果已恢复；源素材已移出当前素材域，历史任务快照仍保留');
  return true;
}

function restoreModeSnapshot(mode = state.currentMode) {
  const snapshot = state.modeSnapshots[mode];
  if (!snapshot) return;
  $('#brief-input').value = snapshot.brief || '';
  if (snapshot.model && $(`#param-model option[value="${CSS.escape(snapshot.model)}"]`)) $('#param-model').value = snapshot.model;
  if (snapshot.angle && $(`#param-angle option[value="${CSS.escape(snapshot.angle)}"]`)) $('#param-angle').value = snapshot.angle;
  if (snapshot.output_ratio && $(`#param-output-ratio option[value="${CSS.escape(snapshot.output_ratio)}"]`)) $('#param-output-ratio').value = snapshot.output_ratio;
  if (snapshot.output_resolution && $(`#param-output-resolution option[value="${CSS.escape(snapshot.output_resolution)}"]`)) $('#param-output-resolution').value = snapshot.output_resolution;
  if (Number.isFinite(snapshot.fidelity)) $('#param-fidelity').value = String(snapshot.fidelity);
  if (Number.isFinite(snapshot.batch)) $('#param-batch').value = String(snapshot.batch);
  if (snapshot.platter) {
    const platter = $(`input[name="platter"][value="${CSS.escape(snapshot.platter)}"]`);
    if (platter) platter.checked = true;
  }
  $('#param-refine').checked = snapshot.refine !== false;
  $$('[data-lock]').forEach((input) => {
    input.checked = Boolean(snapshot.intent_locks?.[input.dataset.lock]);
    input.closest('.lock-chip').classList.toggle('active', input.checked);
  });
  if (mode === 'cutout-batch') renderCutoutControls(snapshot.mask_state);
}

function assetUrl(asset, kind = 'thumbnail') {
  if (!asset) return '';
  const relative = kind === 'content' ? asset.content_url : asset.thumbnail_url;
  if (/^https?:\/\//.test(relative || '')) return relative;
  const cached = state.assetUrls.get(`${asset.id}:${kind}`);
  return cached || relative || '';
}

async function hydrateAssetUrls(assets) {
  await Promise.all((assets || []).map(async (asset) => {
    try {
      const [content, thumbnail] = await Promise.all([
        API.getAssetContentUrl(asset.id), API.getAssetThumbnailUrl(asset.id, 360),
      ]);
      state.assetUrls.set(`${asset.id}:content`, content);
      state.assetUrls.set(`${asset.id}:thumbnail`, thumbnail);
      asset.content_url = content;
      asset.thumbnail_url = thumbnail;
    } catch (_) { /* the API response may already contain absolute URLs */ }
  }));
}

function toast(message, type = 'info', duration = 1800, action = null) {
  const wrap = $('#toast-wrap');
  while (wrap.children.length >= 3) wrap.firstElementChild?.remove();
  const item = document.createElement('div');
  item.className = `toast ${type}`;
  const copy = document.createElement('span');
  copy.textContent = message;
  item.appendChild(copy);
  if (action?.label && typeof action.onClick === 'function') {
    const button = document.createElement('button');
    button.type = 'button';
    button.textContent = action.label;
    button.addEventListener('click', async () => {
      if (button.disabled) return;
      button.disabled = true;
      try { await action.onClick(); item.remove(); }
      catch (_) { button.disabled = false; }
    });
    item.appendChild(button);
    item.classList.add('toast--action');
  }
  wrap.appendChild(item);
  window.setTimeout(() => {
    item.style.opacity = '0';
    item.style.transform = 'translateX(12px)';
    window.setTimeout(() => item.remove(), 220);
  }, duration);
}

function setBackendStatus(status, text) {
  state.backendReady = status === 'connected';
  const dot = $('#conn-dot');
  dot.className = `conn-dot ${status}`;
  $('#conn-text').textContent = text;
  $('#conn-status').title = `后端状态：${text}`;
}

const bootStartedAt = performance.now();

function setBootStatus(title, detail, stateName = 'loading') {
  const shell = $('#boot-shell');
  if (!shell || shell.hidden) return;
  shell.dataset.state = stateName;
  shell.setAttribute('aria-busy', stateName === 'loading' ? 'true' : 'false');
  $('#boot-title').textContent = title;
  $('#boot-detail').textContent = detail;
  $('#boot-progress').hidden = stateName !== 'loading';
  $('#boot-actions').hidden = stateName !== 'error';
}

function dismissBootShell() {
  const shell = $('#boot-shell');
  if (!shell || shell.hidden) return;
  const delay = Math.max(0, 320 - (performance.now() - bootStartedAt));
  window.setTimeout(() => {
    shell.classList.add('is-leaving');
    window.setTimeout(() => { shell.hidden = true; }, 190);
  }, delay);
}

function setStage(stage) {
  if (!STAGE_IDS[stage]) return;
  state.stage = stage;
  $('#preview-card').dataset.stage = stage;
  Object.entries(STAGE_IDS).forEach(([name, id]) => { document.getElementById(id).hidden = name !== stage; });
  renderFileMeta();
  updateCtaState();
}

function switchPage(page) {
  if (!PAGE_CONFIG[page]) return;
  state.currentPage = page;
  $$('.app-page').forEach((section) => {
    const active = section.dataset.pageName === page;
    section.hidden = !active;
    section.classList.toggle('active', active);
  });
  $$('.rail-button[data-page]').forEach((button) => {
    const active = button.dataset.page === page;
    button.classList.toggle('active', active);
    if (active) button.setAttribute('aria-current', 'page'); else button.removeAttribute('aria-current');
  });
  const config = PAGE_CONFIG[page];
  $('#page-eyebrow').textContent = config.eyebrow;
  $('#page-title').textContent = config.title;
  $('#page-subtitle').textContent = config.subtitle;
  if (page !== 'process') workflowDock.close(false);
  if (page === 'history') loadSessions();
  if (page === 'memory') loadMemory();
  if (page === 'settings') settingsController.load();
}

function setupAppearance() {
  const groundingCard = $('.settings-card--grounding');
  if (groundingCard && !$('.settings-card--appearance')) {
    groundingCard.insertAdjacentHTML('beforebegin', appearanceSettingsHtml());
    $('.settings-card__heading > span', groundingCard).textContent = '06';
  }
  const themeMedia = window.matchMedia('(prefers-color-scheme: dark)');
  const motionMedia = window.matchMedia('(prefers-reduced-motion: reduce)');
  let preferences = readAppearancePreferences(localStorage);

  const persist = () => {
    localStorage.setItem('pa-theme-preference', preferences.themePreference);
    localStorage.setItem('pa-theme', preferences.themePreference === 'system' ? 'light' : preferences.themePreference);
    localStorage.setItem('pa-text-scale', preferences.textScale);
    localStorage.setItem('pa-contrast', preferences.contrast);
    localStorage.setItem('pa-motion', preferences.motionPreference);
  };
  const apply = (announce = false) => {
    const resolved = resolveAppearancePreferences(preferences, {
      systemDark: themeMedia.matches,
      systemReducedMotion: motionMedia.matches,
    });
    const root = document.documentElement;
    root.dataset.themePreference = resolved.themePreference;
    root.dataset.theme = resolved.theme;
    root.dataset.textScale = resolved.textScale;
    root.dataset.contrast = resolved.contrast;
    root.dataset.motion = resolved.reducedMotion ? 'reduced' : 'full';
    const dark = resolved.theme === 'dark';
    $('#theme-icon-moon').hidden = dark;
    $('#theme-icon-sun').hidden = !dark;
    const toggle = $('#theme-toggle');
    toggle.setAttribute('aria-label', `当前为${dark ? '深色' : '浅色'}主题，切换为${dark ? '浅色' : '深色'}主题`);
    toggle.title = toggle.getAttribute('aria-label');
    $$('input[name="appearance-theme"]').forEach((input) => { input.checked = input.value === resolved.themePreference; });
    $$('input[name="appearance-text-scale"]').forEach((input) => { input.checked = input.value === resolved.textScale; });
    $$('input[name="appearance-contrast"]').forEach((input) => { input.checked = input.value === resolved.contrast; });
    $$('input[name="appearance-motion"]').forEach((input) => { input.checked = input.value === resolved.motionPreference; });
    $('#appearance-status').textContent = appearanceStatusCopy(resolved);
    if (announce) toast('界面外观已更新', 'success');
  };

  $('#theme-toggle').addEventListener('click', () => {
    const resolved = resolveAppearancePreferences(preferences, { systemDark: themeMedia.matches });
    preferences = { ...preferences, themePreference: explicitThemeAfterToggle(resolved.theme) };
    persist();
    apply(true);
  });
  const bindChoice = (name, key) => {
    $$(`input[name="${name}"]`).forEach((input) => input.addEventListener('change', () => {
      if (!input.checked) return;
      preferences = { ...preferences, [key]: input.value };
      persist();
      apply(true);
    }));
  };
  bindChoice('appearance-theme', 'themePreference');
  bindChoice('appearance-text-scale', 'textScale');
  bindChoice('appearance-contrast', 'contrast');
  bindChoice('appearance-motion', 'motionPreference');
  $('#btn-reset-appearance').addEventListener('click', () => {
    preferences = { ...DEFAULT_APPEARANCE };
    persist();
    apply(true);
  });
  themeMedia.addEventListener('change', () => { if (preferences.themePreference === 'system') apply(); });
  motionMedia.addEventListener('change', () => { if (preferences.motionPreference === 'system') apply(); });
  apply();
}

function clearSession(keepMode = true) {
  captureModeSnapshot();
  state.modeSelections[state.currentMode] = [];
  syncLegacySelection();
  state.originalDataUrl = '';
  state.results = null;
  state.compareData = null;
  state.currentTaskId = '';
  state.currentSessionId = '';
  state.currentGenerationId = '';
  state.resultTab = state.currentMode === 'cutout-batch' ? 'cutout' : 'main';
  state.viewerIndex = 0;
  state.knowledgeBundle = null;
  state.feedbackResultKey = '';
  state.feedbackRecorded = false;
  state.feedbackSubmitting = false;
  state.feedbackReceipt = '';
  state.feedbackSuggestionId = '';
  state.lastFeedbackSignal = '';
  state.folderBatches[state.currentMode] = null;
  $('#file-input').value = '';
  $('#folder-path').value = '';
  $('#brief-input').value = '';
  $('#canvas-img-preview').removeAttribute('src');
  $('#summary-result').textContent = '等待选择任务素材';
  $('#summary-result-note').textContent = '共享素材和已有任务不会被清空';
  $('#workspace-progress-percent').textContent = '0%';
  $('#workspace-progress-bar').style.width = '0%';
  $('#knowledge-summary').textContent = '等待知识编译';
  renderKnowledge(null);
  state.modeSnapshots[state.currentMode] = null;
  if (!keepMode) switchMode('single', false);
  renderQueue();
  renderFolderSource();
  persistWorkspaceState();
  scheduleWorkspaceDraftSave(state.currentMode, 0);
  updateCtaState();
}

function applyCompletedWorkspaceDraft(mode, draft) {
  const timer = state.draftSaveTimers.get(mode);
  if (timer) window.clearTimeout(timer);
  state.draftSaveTimers.delete(mode);
  state.draftSaveQueued.delete(mode);
  state.workspaceDrafts[mode] = draft;
  state.workspaceRevisions[mode] = Number(draft?.revision || state.workspaceRevisions[mode]);
  state.modeSelections[mode] = [];
  state.folderBatches[mode] = null;
  state.modeSnapshots[mode] = snapshotFromDraft(draft, state.modeSnapshots[mode] || {});
  if (mode !== state.currentMode) return;

  state.hydratingWorkspace = true;
  try {
    syncLegacySelection();
    state.originalDataUrl = '';
    state.results = null;
    state.compareData = null;
    state.currentTaskId = '';
    state.currentSessionId = '';
    state.currentGenerationId = '';
    state.resultTab = mode === 'cutout-batch' ? 'cutout' : 'main';
    state.viewerIndex = 0;
    state.knowledgeBundle = null;
    state.feedbackResultKey = '';
    state.feedbackRecorded = false;
    state.feedbackReceipt = '';
    state.feedbackSuggestionId = '';
    state.lastFeedbackSignal = '';
    state.editingFeedbackResultKey = '';
    $('#file-input').value = '';
    $('#folder-path').value = '';
    $('#canvas-img-preview').removeAttribute('src');
    $('#summary-result').textContent = '等待选择任务素材';
    $('#summary-result-note').textContent = '历史结果已保留；当前现场已安全完成';
    $('#workspace-progress-percent').textContent = '0%';
    $('#workspace-progress-bar').style.width = '0%';
    $('#knowledge-summary').textContent = '等待知识编译';
    restoreModeSnapshot(mode);
    renderKnowledge(null);
    renderQueue();
    renderFolderSource();
    updateCtaState();
  } finally {
    state.hydratingWorkspace = false;
  }
}

function renderFolderSource() {
  const row = $('#folder-source');
  const input = $('#folder-path');
  const status = $('#folder-source-status');
  if (!row || !input || !status) return;
  const visible = state.currentMode === 'multi-file';
  row.hidden = !visible;
  if (!visible) return;
  const batch = folderBatchForMode('multi-file');
  row.classList.toggle('is-ready', Boolean(batch));
  if (!batch) {
    status.textContent = '自动拆批并发，结果回到原文件夹';
    return;
  }
  input.value = batch.source_folder || input.value;
  const count = Number(batch.imported_count || batch.asset_ids.length || 0);
  const target = String(batch.delivery_root || '').split(/[\\/]/).filter(Boolean).pop() || '已处理文件夹';
  status.textContent = batch.status === 'submitted'
    ? `${count} 张已入队 · 完成后归类到 ${target}`
    : `${count} 张整夹来源已就绪 · 将归类到 ${target}`;
}

function switchMode(mode, preserveCurrent = true, loadDurable = true) {
  if (!MODE_CONFIG[mode]) return;
  const changed = state.currentMode !== mode;
  if (preserveCurrent && changed) captureModeSnapshot();
  if (changed) setWorkspaceSyncState();
  state.currentMode = mode;
  if (changed) {
    state.currentTaskId = '';
    state.currentSessionId = '';
    clearVisibleResultContext();
  }
  state.assets = state.assetsByCollection[MODE_CONFIG[mode].collection] || [];
  const config = MODE_CONFIG[mode];
  $$('.mode-button').forEach((button) => {
    const active = button.dataset.mode === mode;
    button.classList.toggle('active', active);
    button.setAttribute('aria-pressed', String(active));
  });
  const input = $('#file-input');
  input.multiple = config.multiple;
  $('#upload-eyebrow').textContent = config.eyebrow;
  $('#upload-title').textContent = config.title;
  $('#upload-description').textContent = config.description;
  $('#upload-limit').textContent = config.limit;
  $('#canvas-title').textContent = config.label.replace('商业', ' · 商业');
  $('#info-mode-badge').textContent = config.badge;
  $('#summary-mode').textContent = config.label;
  $('#summary-note').textContent = config.note;
  const quickCutout = mode === 'cutout-batch';
  $('#creative-command').hidden = quickCutout;
  $('#cutout-tools').hidden = !quickCutout;
  renderFolderSource();
  $('#field-model').hidden = quickCutout;
  $('#field-composition').hidden = quickCutout;
  $('#field-output-spec').hidden = quickCutout;
  $('#field-intent').hidden = quickCutout;
  $('#btn-knowledge-card').disabled = quickCutout;
  $('#btn-knowledge-card').title = quickCutout
    ? '快速去背景当前不读取知识库或文字描述'
    : '查看本次知识介入';
  $('#field-refine').hidden = mode !== 'group-split';
  $('#batch-field-label').firstChild.textContent = mode === 'multi-file' ? '每图方案数 ' : '生成方案数 ';
  if (mode === 'multi-file' && $('#param-model').value === 'gpt-image-2') $('#model-reason').textContent = '每张独立处理';
  else if (mode === 'cutout-batch') $('#model-reason').textContent = '本地 BiRefNet';
  else $('#model-reason').textContent = '质量优先';
  state.resultTab = mode === 'cutout-batch' ? 'cutout' : 'main';
  state.hydratingWorkspace = true;
  try {
    restoreModeSnapshot(mode);
    syncLegacySelection();
    renderQueue();
    renderFileMeta();
    updateQuickControls();
  } finally {
    state.hydratingWorkspace = false;
  }
  persistWorkspaceState();
  updateCtaState();
  if (state.backendReady && loadDurable) loadWorkspace(mode, true);
  if (quickCutout) renderCutoutCapability();
  else {
    state.knowledgeBundle = null;
    renderKnowledge(null);
  }
  assetManager.sync();
}

function findJobSourceAsset(assetId) {
  const id = String(assetId || '');
  for (const assets of Object.values(state.assetsByCollection)) {
    const asset = assets.find((entry) => entry.id === id);
    if (asset) return asset;
  }
  return state.jobSourceAssets.get(id) || null;
}

async function hydrateJobSourceAssets(jobs) {
  const missing = jobSourceIds(jobs).filter((assetId) => (
    !findJobSourceAsset(assetId) && !state.jobSourceAssets.has(assetId)
  ));
  for (let offset = 0; offset < missing.length; offset += 6) {
    const chunk = missing.slice(offset, offset + 6);
    const resolved = await Promise.all(chunk.map(async (assetId) => {
      try {
        const response = await API.getAsset(assetId);
        const asset = response?.asset || response;
        if (asset?.id) await hydrateAssetUrls([asset]);
        return [assetId, asset?.id ? asset : null];
      } catch (_) {
        return [assetId, null];
      }
    }));
    resolved.forEach(([assetId, asset]) => state.jobSourceAssets.set(assetId, asset));
  }
}

function renderFileMeta() {
  const selection = selectedAssets();
  const count = selection.length;
  const folderBatch = state.currentMode === 'multi-file'
    ? folderBatchForMode('multi-file', true)
    : null;
  $('#asset-count').textContent = `素材库 ${state.assets.length} 张`;
  $('#btn-replace').hidden = false;
  $('#btn-replace').disabled = state.importing;
  $('#btn-clear').hidden = count === 0;
  if (folderBatch) {
    const folderCount = Number(folderBatch.imported_count || folderBatch.asset_ids.length || 0);
    $('#info-filename').textContent = `整夹导入 · ${folderCount} 张图片等待入队`;
    $('#ready-count').textContent = `已选 ${folderCount} 张`;
  } else if (count) {
    $('#info-filename').textContent = count === 1 ? selection[0].name : `${count} 张源图已选中`;
    $('#ready-count').textContent = `已选 ${count} 张`;
  } else {
    $('#info-filename').textContent = '从素材工作台选择任务输入';
    $('#ready-count').textContent = '0 张已选';
  }
}

function renderQueue() {
  const ready = $('#canvas-image');
  const queue = $('#file-queue');
  ready.classList.add('is-queue', 'is-workspace');
  if (!state.assets.length) {
    queue.innerHTML = '';
    setStage('empty');
    renderFileMeta();
    return;
  }
  const selection = new Set(selectedAssetIds());
  const renderedAssets = boundedAssetRenderList(state.assets, selection, 60);
  const items = renderedAssets.map((asset, index) => {
    const selected = selection.has(asset.id);
    const dimensions = asset.width && asset.height ? `${asset.width}×${asset.height}` : '已持久化';
    return `<article class="queue-item asset-card ${selected ? 'selected' : ''}">
      <button class="asset-card__select" type="button" data-asset-id="${escapeHtml(asset.id)}" aria-pressed="${selected}" aria-label="${selected ? '取消选择' : '选择'} ${escapeHtml(asset.name)}">
        <span class="asset-card__visual"><img src="${escapeHtml(assetUrl(asset))}" alt="" loading="lazy" decoding="async" /><span class="asset-card__check" aria-hidden="true">${selected ? '✓' : '+'}</span></span>
        <span class="asset-card__meta"><strong title="${escapeHtml(asset.name)}">${escapeHtml(asset.name || `素材 ${index + 1}`)}</strong><small>${escapeHtml(dimensions)}</small></span>
      </button>
      <button class="asset-card__remove" type="button" data-remove-asset-id="${escapeHtml(asset.id)}" aria-label="将 ${escapeHtml(asset.name)} 移入回收站" title="移入回收站"><svg viewBox="0 0 24 24"><path d="M3 6h18M8 6V4h8v2M19 6l-1 15H6L5 6M10 11v6M14 11v6"/></svg></button>
    </article>`;
  }).join('');
  queue.innerHTML = items;
  if (renderedAssets.length < state.assets.length) {
    queue.innerHTML += `<button class="queue-item queue-manage" type="button" id="btn-queue-manage"><span>+${state.assets.length - renderedAssets.length}</span><strong>管理全部素材</strong><small>搜索、排序或批量处理</small></button>`;
  }
  if (state.currentMode !== 'single') {
    queue.innerHTML += '<button class="queue-item queue-add" type="button" id="btn-queue-add"><span>+</span><strong>添加图片</strong><small>继续导入本工作流素材</small></button>';
  }
  $$('[data-asset-id]', queue).forEach((button) => button.addEventListener('click', () => toggleAssetSelection(button.dataset.assetId)));
  $$('[data-remove-asset-id]', queue).forEach((button) => button.addEventListener('click', () => removeWorkspaceAsset(button.dataset.removeAssetId)));
  $('#btn-queue-add')?.addEventListener('click', () => $('#file-input').click());
  $('#btn-queue-manage')?.addEventListener('click', () => assetManager.open());
  setStage('ready');
  renderFileMeta();
}

async function handleFiles(fileList) {
  const incoming = Array.from(fileList || []);
  if (!incoming.length) return;
  if (state.importing) { toast('上一批素材仍在导入，请稍候', 'error'); return; }
  const importMode = state.currentMode;
  const valid = incoming.filter((file) => {
    if (!/^image\/(png|jpeg|webp)$/i.test(file.type)) { toast(`${file.name} 不是支持的图片格式`, 'error'); return false; }
    if (file.size > 20 * 1024 * 1024) { toast(`${file.name} 超过 20 MB`, 'error'); return false; }
    return true;
  });
  if (!valid.length) return;
  if (valid.length > 100) { toast('一次最多导入 100 张素材', 'error'); return; }
  state.importing = true;
  renderFileMeta();
  $('#btn-browse').disabled = true;
  toast(`正在导入 ${valid.length} 张素材…`);
  try {
    const result = await API.importAssets(valid, MODE_CONFIG[importMode].collection);
    const imported = Array.isArray(result?.assets) ? result.assets : [];
    const errors = Array.isArray(result?.errors) ? result.errors : [];
    await loadWorkspace(importMode, true);
    const config = MODE_CONFIG[importMode];
    state.modeSelections[importMode] = selectionAfterImport(
      selectedAssetIds(importMode),
      imported.map((asset) => asset.id),
      config,
    );
    if (importMode === 'multi-file') {
      state.folderBatches[importMode] = null;
      captureModeSnapshot(importMode);
    }
    scheduleWorkspaceDraftSave(importMode, 0);
    await flushWorkspaceDraft(importMode, true);
    syncLegacySelection();
    if (state.currentMode === importMode) renderQueue();
    renderFolderSource();
    persistWorkspaceState();
    $('#summary-result').textContent = `${imported.length} 张素材已写入工作台`;
    $('#summary-result-note').textContent = '切换模式或重启后仍可继续使用';
    if (errors.length) toast(`${imported.length} 张导入成功，${errors.length} 张失败`, 'error', 5200);
    else toast(`已导入 ${imported.length} 张素材`, 'success');
    updateCtaState();
    await compileKnowledgePreview();
  } catch (error) {
    state.assetsAvailable = false;
    toast(`素材导入失败：${formatApiError(error, '持久素材接口不可用')}`, 'error', 6000);
  } finally {
    state.importing = false;
    $('#btn-browse').disabled = false;
    $('#file-input').value = '';
    renderFileMeta();
  }
}

async function chooseFolderSource() {
  try {
    const selected = await API.selectFolder();
    if (selected) $('#folder-path').value = selected;
  } catch (error) {
    toast(`无法打开文件夹选择器：${formatApiError(error)}`, 'error');
  }
}

async function importFolderSource() {
  if (state.importing) return;
  const folderPath = $('#folder-path').value.trim();
  if (!folderPath) {
    toast('请粘贴文件夹地址，或点击“选择”', 'error');
    $('#folder-path').focus();
    return;
  }
  state.importing = true;
  $('#btn-folder-browse').disabled = true;
  $('#btn-folder-load').disabled = true;
  $('#folder-source-status').textContent = '正在扫描并写入持久素材工作台…';
  try {
    const result = await API.importFolderSources(folderPath);
    const imported = Array.isArray(result?.assets) ? result.assets : [];
    const folderBatch = { ...(result?.folder_batch || {}), status: 'ready' };
    await loadWorkspace('multi-file', true);
    state.folderBatches['multi-file'] = folderBatch;
    state.modeSelections['multi-file'] = imported
      .map((asset) => asset.id)
      .slice(0, MODE_CONFIG['multi-file'].maxFiles);
    captureModeSnapshot('multi-file');
    scheduleWorkspaceDraftSave('multi-file', 0);
    await flushWorkspaceDraft('multi-file', true);
    syncLegacySelection();
    renderQueue();
    renderFolderSource();
    updateCtaState();
    const errorCount = Number(folderBatch.error_count || 0);
    $('#summary-result').textContent = `${folderBatch.imported_count || imported.length} 张整夹图片已就绪`;
    $('#summary-result-note').textContent = '运行后自动拆批并发，完成文件回到原目录';
    toast(
      errorCount
        ? `整夹已载入，${errorCount} 个文件跳过或失败`
        : `整夹已载入：${folderBatch.imported_count || imported.length} 张`,
      errorCount ? 'error' : 'success',
      5200,
    );
    await compileKnowledgePreview();
  } catch (error) {
    $('#folder-source-status').textContent = '载入失败；请检查路径、权限和图片格式';
    toast(`文件夹载入失败：${formatApiError(error, '文件夹接口不可用')}`, 'error', 6500);
  } finally {
    state.importing = false;
    $('#btn-folder-browse').disabled = false;
    $('#btn-folder-load').disabled = false;
    renderFileMeta();
  }
}

function toggleAssetSelection(assetId) {
  const config = MODE_CONFIG[state.currentMode];
  const current = selectedAssetIds();
  if (state.currentMode === 'multi-file' && folderBatchForMode('multi-file', true)) {
    state.folderBatches['multi-file'] = null;
    captureModeSnapshot('multi-file');
    renderFolderSource();
    toast('已退出整夹队列，改为手动选择素材');
  }
  const selected = current.includes(assetId);
  let next;
  if (selected) next = current.filter((id) => id !== assetId);
  else if (!config.multiple) next = [assetId];
  else if (current.length >= config.maxFiles) {
    toast(`当前模式最多选择 ${config.maxFiles} 张`, 'error');
    return;
  } else next = [...current, assetId];
  state.modeSelections[state.currentMode] = next;
  syncLegacySelection();
  renderQueue();
  persistWorkspaceState();
  scheduleWorkspaceDraftSave(state.currentMode);
  if (state.currentMode === 'cutout-batch') renderCutoutControls();
  updateCtaState();
  scheduleKnowledgeCompile();
}

async function loadAssets(silent = false) {
  return loadWorkspace(state.currentMode, silent);
}

async function removeWorkspaceAsset(assetId) {
  return assetManager.remove(assetId);
}

function getIntentLocks() {
  const locks = {};
  $$('[data-lock]').forEach((input) => { locks[input.dataset.lock] = Boolean(input.checked); });
  if ($('#param-angle').value === 'keep') locks.angle = true;
  return locks;
}

function getPlatter() {
  return $('input[name="platter"]:checked')?.value || 'auto';
}

function buildBrief(mode = state.currentMode) {
  const semantic = mode === 'cutout-batch' && cutoutSelectionState().strategy === 'semantic';
  const selection = semantic ? cutoutSelectionState() : null;
  const request = semantic
    ? `只保留 ${selection.target_count} 个${selection.query ? `“${selection.query}”` : '已确认目标'}`
    : $('#brief-input').value.trim();
  return {
    objective: request || '将产品原图转化为可交付的商业图片',
    user_request: request,
    mode,
    category: 'general',
    platform: 'ecommerce',
    output_kind: MODE_CONFIG[mode].outputKind,
    angle: $('#param-angle').value,
    platter: getPlatter(),
    fidelity: Number($('#param-fidelity').value),
    intent_locks: getIntentLocks(),
    output_spec: {
      ratio: mode === 'cutout-batch' ? 'source alpha bounds' : $('#param-output-ratio').value,
      resolution: mode === 'cutout-batch' ? 'source' : $('#param-output-resolution').value,
      format: mode === 'cutout-batch' ? 'transparent PNG' : 'JPG+transparent PNG',
    },
  };
}

let compileTimer = null;
async function compileKnowledgePreview(context = null) {
  const mode = context?.mode || state.currentMode;
  if (mode === 'cutout-batch') {
    if (mode === state.currentMode) renderCutoutCapability();
    return null;
  }
  const brief = context?.brief || buildBrief(mode);
  const hasInput = Boolean(selectedAssetIds(mode).length || brief.user_request || context?.force);
  if (!hasInput) return null;
  const requestVersion = ++state.knowledgeRequestVersion;
  try {
    const bundle = await API.compileKnowledge(brief);
    if (requestVersion === state.knowledgeRequestVersion && mode === state.currentMode) {
      state.knowledgeBundle = bundle;
      renderKnowledge(bundle);
    }
    return bundle;
  } catch (error) {
    if (requestVersion === state.knowledgeRequestVersion && mode === state.currentMode) {
      $('#knowledge-summary').textContent = '知识编译暂不可用，使用安全默认值';
    }
    return null;
  }
}

function scheduleKnowledgeCompile() {
  window.clearTimeout(compileTimer);
  captureModeSnapshot();
  persistWorkspaceState();
  compileTimer = window.setTimeout(compileKnowledgePreview, 480);
}

function applyTaskKnowledgeBundle(bundle) {
  window.clearTimeout(compileTimer);
  state.knowledgeRequestVersion += 1;
  state.knowledgeBundle = bundle;
  renderKnowledge(bundle);
}

function renderKnowledge(bundle) {
  if (state.currentMode === 'cutout-batch') {
    renderCutoutCapability();
    return;
  }
  if (!bundle) {
    $('#knowledge-summary').textContent = '等待知识编译';
    $('#knowledge-rule-count').textContent = '0 条规则';
    $('#knowledge-rule-list').innerHTML = statusPanelHtml('empty', { title: '尚未编译执行规则', detail: '选择素材或输入创作目标后开始编译。', compact: true });
    $('#knowledge-source-count').textContent = '0 条来源';
    $('#knowledge-source-list').innerHTML = statusPanelHtml('empty', { title: '尚未编译知识', detail: '本次引用会在编译后列出。', compact: true });
    $('#knowledge-conflicts').innerHTML = '<div class="conflict-item ok"><span>✓</span><p>当前没有检测到规则冲突</p></div>';
    $('#intelligence-brief').textContent = '等待输入创作意图';
    $('#intelligence-context').textContent = '选择模式与素材后，系统会把目标编译成可检查的创作合同。';
    return;
  }
  const sources = bundle.sources || [];
  const positiveRules = bundle.positive_rules || [];
  const negativeRules = bundle.negative_rules || [];
  const intentLockRules = bundle.intent_lock_rules || [];
  const ruleEntries = [
    ...intentLockRules.map((item) => ({ kind: 'lock', label: '锁定', text: item?.text || item })),
    ...positiveRules.map((item) => ({ kind: 'positive', label: '执行', text: item?.text || item })),
    ...negativeRules.map((item) => ({ kind: 'negative', label: '避坑', text: item?.text || item })),
  ].filter((item) => String(item.text || '').trim());
  const rules = ruleEntries.length;
  $('#knowledge-summary').textContent = `${sources.length} 份知识 · ${rules} 条执行规则`;
  $('#knowledge-rule-count').textContent = `${rules} 条规则`;
  $('#knowledge-rule-list').innerHTML = rules ? ruleEntries.map((item) => `<div class="knowledge-rule-item is-${item.kind}"><span>${item.label}</span><p>${escapeHtml(item.text)}</p></div>`).join('') : statusPanelHtml('empty', { title: '没有额外执行规则', detail: '本次只采用基础安全约束。', compact: true });
  $('#knowledge-source-count').textContent = `${sources.length} 条来源`;
  $('#knowledge-source-list').innerHTML = sources.length ? sources.map((source, index) => {
    const title = typeof source === 'string' ? source : (source.title || source.id || '设计规则');
    const path = typeof source === 'string' ? '' : (source.relative_path || source.path || '');
    return `<div class="source-item"><span>${String(index + 1).padStart(2, '0')}</span><div><strong>${escapeHtml(title)}</strong><small>${escapeHtml(path)}</small></div></div>`;
  }).join('') : statusPanelHtml('empty', { title: '使用安全默认规则', detail: '本次没有引用额外知识来源。', compact: true });
  const brief = bundle.creative_brief || {};
  const memorySources = sources.filter((source) => source?.relative_path === '记忆反馈/已批准');
  $('#intelligence-brief').textContent = brief.objective || '本次商业图片任务';
  $('#intelligence-context').textContent = `${MODE_CONFIG[state.currentMode].label} · ${brief.output_kind || '商业输出'} · ${Object.values(brief.intent_locks || {}).filter(Boolean).length} 项意图锁定${memorySources.length ? ` · ${memorySources.length} 条已批准记忆反馈` : ''}${bundle.trace_bound ? ' · 已绑定该任务执行记录' : ''}`;
  const conflicts = bundle.conflicts || [];
  $('#knowledge-conflicts').innerHTML = conflicts.length ? conflicts.map((item) => `<div class="conflict-item"><span>!</span><p>${escapeHtml(item.message)}</p></div>`).join('') : '<div class="conflict-item ok"><span>✓</span><p>当前没有检测到规则冲突</p></div>';
}

function renderCutoutControls(rawSelection = null) {
  if (!$('#cutout-tools')) return;
  const selection = createSemanticCutoutState(rawSelection || cutoutSelectionState());
  const semantic = selection.strategy === 'semantic';
  $('#settings-panel').classList.toggle(
    'is-semantic-cutout',
    state.currentMode === 'cutout-batch' && semantic,
  );
  $$('[data-cutout-strategy]').forEach((button) => {
    const active = button.dataset.cutoutStrategy === selection.strategy;
    button.classList.toggle('active', active);
    button.setAttribute('aria-pressed', String(active));
  });
  $('#cutout-capability').hidden = state.currentMode !== 'cutout-batch';
  $('#cutout-capability-title').textContent = semantic ? '智能选物 · 确认后执行' : '快速去背景';
  $('#cutout-capability-copy').textContent = semantic
    ? '按名称和数量自动定位候选；你确认选区后才会执行本地抠图。'
    : '分离画面中的全部前景；当前不理解物体名称、数量或“只保留某个物体”等文字要求。';
  $('#semantic-cutout-controls').hidden = !semantic;
  if (document.activeElement !== $('#semantic-query')) $('#semantic-query').value = selection.query;
  if (document.activeElement !== $('#semantic-model-query')) $('#semantic-model-query').value = selection.model_query_override;
  if (document.activeElement !== $('#semantic-count')) $('#semantic-count').value = String(selection.target_count);
  const readiness = semanticCutoutReadiness(selection, selectedAssetIds('cutout-batch'));
  $('#semantic-cutout-status').textContent = readiness.message;
  $('#semantic-cutout-status').classList.toggle('is-confirmed', readiness.ready);
  $('#btn-semantic-preview').disabled = (
    !['confirm', 'submit'].includes(readiness.action)
    || !state.backendReady
    || !state.assetsAvailable
  );
  $('#btn-semantic-preview').textContent = readiness.ready ? '重新识别或调整' : '自动识别并确认';
}

function selectCutoutStrategy(strategy) {
  const next = updateSemanticCutoutState(cutoutSelectionState(), { strategy });
  setCutoutSelectionState(next);
  renderCutoutCapability();
  updateCtaState();
  if (strategy === 'semantic' && selectedAssetIds('cutout-batch').length > 1) {
    toast('智能选物首版每次确认 1 张；快速去背景仍支持批量', 'info', 4200);
  }
}

function updateSemanticCutoutField() {
  const next = updateSemanticCutoutState(cutoutSelectionState(), {
    query: $('#semantic-query').value,
    model_query_override: $('#semantic-model-query').value,
    target_count: Number($('#semantic-count').value),
  });
  setCutoutSelectionState(next);
  renderCutoutCapability();
  updateCtaState();
}

function renderCutoutCapability() {
  const selection = cutoutSelectionState();
  const semantic = selection.strategy === 'semantic';
  $('#knowledge-summary').textContent = semantic ? '名称定位 · 人工确认' : '本地分割 · 不读取文字描述';
  $('#knowledge-rule-count').textContent = '0 条规则';
  $('#knowledge-rule-list').innerHTML = statusPanelHtml('empty', {
    title: semantic ? '先识别候选，再由你确认' : '没有文字执行规则',
    detail: semantic ? '中文名称通过离线词表转换；无法转换或模型失败时保留手动框选。' : '快速去背景只执行本地前景分割。',
    compact: true,
  });
  $('#knowledge-source-count').textContent = '0 条执行知识';
  $('#knowledge-source-list').innerHTML = statusPanelHtml('empty', { title: '不读取知识来源', detail: '此工作流只执行本地前景分割。', compact: true });
  $('#knowledge-conflicts').innerHTML = semantic
    ? '<div class="conflict-item ok"><span>i</span><p>自动候选不会直接入队；未确认、数量不符或源图变化都会阻止执行。</p></div>'
    : '<div class="conflict-item ok"><span>i</span><p>快速去背景只分离全部前景；需要按名称或数量选物时请切换“智能选物”。</p></div>';
  $('#intelligence-brief').textContent = semantic ? '当前能力：按名称和数量定位目标' : '当前能力：分离全部前景';
  $('#intelligence-context').textContent = semantic
    ? '名称进入离线定位模型，候选框经人工确认后进入本地分割链。'
    : '文字描述、物体数量和知识规则不会进入本次执行链。';
  renderCutoutControls(selection);
}

function updateCtaState() {
  const button = $('#btn-generate');
  const folderBatch = state.currentMode === 'multi-file'
    ? folderBatchForMode('multi-file', true)
    : null;
  const count = folderBatch ? folderBatch.asset_ids.length : selectedAssetIds().length;
  const hasFiles = count > 0;
  const batch = Number($('#param-batch').value);
  const plan = multiFileOutputPlan(count, batch);
  const capacityOkay = state.currentMode !== 'multi-file' || Boolean(folderBatch) || plan.valid;
  const cutoutReadiness = state.currentMode === 'cutout-batch'
    ? semanticCutoutReadiness(cutoutSelectionState(), selectedAssetIds())
    : null;
  const semanticCanConfirm = cutoutReadiness?.action === 'confirm';
  const semanticBlocked = cutoutReadiness
    && cutoutSelectionState().strategy === 'semantic'
    && !cutoutReadiness.ready
    && !semanticCanConfirm;
  button.disabled = !hasFiles || state.submitting || !state.assetsAvailable || !capacityOkay || semanticBlocked;
  $('#param-batch').setAttribute('aria-invalid', String(!capacityOkay));
  button.classList.toggle('loading', state.submitting);
  if (state.submitting) $('#generate-text').textContent = '正在加入后台任务';
  else if (!hasFiles) $('#generate-text').textContent = '选择图片开始';
  else if (semanticCanConfirm) $('#generate-text').textContent = '先确认目标';
  else if (cutoutReadiness?.ready && cutoutSelectionState().strategy === 'semantic') $('#generate-text').textContent = '开始智能抠图';
  else if (state.stage === 'success') $('#generate-text').textContent = '基于当前素材再生成';
  else $('#generate-text').textContent = MODE_CONFIG[state.currentMode].action;
  if (!hasFiles) $('#cta-hint').textContent = state.currentMode === 'cutout-batch'
    ? '从抠图素材中选择后可入队'
    : '从当前素材区选择后可入队';
  else if (!capacityOkay) $('#cta-hint').textContent = `${count} 张 × ${batch} 方案 = ${plan.total} 个输出；单批最多 ${plan.maxOutputs}，请改为每图 ${plan.maxVariations} 个`;
  else if (state.currentMode === 'cutout-batch' && cutoutReadiness) $('#cta-hint').textContent = cutoutReadiness.message;
  else if (state.stage === 'success') $('#cta-hint').textContent = '调整创作要求后可继续生成；当前结果不会被覆盖';
  else if (folderBatch) {
    const chunkSize = Math.max(1, Math.min(20, Math.floor(24 / Math.max(1, batch))));
    const partCount = Math.ceil(count / chunkSize);
    $('#cta-hint').textContent = `${count} 张整夹素材 · 自动拆为 ${partCount} 批并发任务`;
  } else $('#cta-hint').textContent = `${count} 张素材 · ${Object.values(getIntentLocks()).filter(Boolean).length} 项锁定`;
}

function updateQuickControls() {
  const angleLabels = { auto: 'Auto', keep: 'Locked', front: 'Front', '45top': '45° Top', '30side': '30° Side', '90top': 'Top' };
  $('#quick-angle').textContent = angleLabels[$('#param-angle').value] || $('#param-angle').value;
  $('#quick-fidelity').textContent = `${$('#param-fidelity').value}%`;
  $('#quick-batch').textContent = state.currentMode === 'multi-file' ? `${$('#param-batch').value} / file` : $('#param-batch').value;
  $('#fid-val').textContent = `${$('#param-fidelity').value}%`;
  $('#batch-val').textContent = $('#param-batch').value;
  const outputRatio = $('#param-output-ratio').value;
  const outputResolution = $('#param-output-resolution').value;
  $('#spec-ratio').textContent = outputRatio === 'original' ? '原图' : outputRatio;
  $('#spec-resolution').textContent = outputResolution.toUpperCase();
  const isGemini = $('#param-model').value.startsWith('gemini-');
  $('#output-spec-note').textContent = outputRatio === 'original' && isGemini
    ? '按每张源图选择最接近的模型原生比例；实际像素会写入结果记录。'
    : outputRatio === 'original'
      ? '按每张源图精确计算画布；实际回传像素会写入结果记录。'
      : `${outputRatio} 画布与拍摄角度独立；实际回传像素会写入结果记录。`;
  captureModeSnapshot();
  persistWorkspaceState();
  updateCtaState();
  scheduleKnowledgeCompile();
}

function captureSubmissionDraft() {
  const mode = state.currentMode;
  const brief = buildBrief(mode);
  const folderBatch = mode === 'multi-file' ? folderBatchForMode(mode, true) : null;
  return createSubmissionSnapshot({
    mode,
    sourceAssetIds: sourceAssetIdsForSubmission(mode),
    parameters: {
      model: $('#param-model').value,
      variations: Number($('#param-batch').value),
      batch: Number($('#param-batch').value),
      platter: getPlatter(),
      fidelity: Number($('#param-fidelity').value),
      angle: $('#param-angle').value,
      output_ratio: $('#param-output-ratio').value,
      output_resolution: $('#param-output-resolution').value,
      refine: $('#param-refine').checked,
      output_root: String(state.settings?.output_root || state.settings?.output_dir || '').trim(),
      brief,
      intent_locks: mode === 'cutout-batch' ? {} : getIntentLocks(),
      category: 'general',
      ...(mode === 'cutout-batch' ? {
        cutout_selection: semanticCutoutPayload(cutoutSelectionState(), sourceAssetIdsForSubmission(mode)),
      } : {}),
      ...(folderBatch ? {
        folder_delivery: {
          batch_id: folderBatch.batch_id,
          source_folder: folderBatch.source_folder,
          delivery_root: folderBatch.delivery_root,
          source_names: folderBatch.source_names || {},
        },
      } : {}),
    },
  });
}

function jobPayloadsForSubmission(payload) {
  const delivery = payload?.parameters?.folder_delivery;
  if (payload?.mode !== 'multi-file' || !delivery) return [payload];
  const variations = Math.max(1, Number(payload.parameters.variations || 1));
  const chunkSize = Math.max(1, Math.min(20, Math.floor(24 / variations)));
  const sourceIds = Array.from(payload.source_asset_ids || []);
  const chunks = [];
  for (let index = 0; index < sourceIds.length; index += chunkSize) {
    chunks.push(sourceIds.slice(index, index + chunkSize));
  }
  return chunks.map((assetIds, index) => ({
    ...payload,
    source_asset_ids: assetIds,
    client_request_id: `${payload.client_request_id}-part-${index + 1}`,
    parameters: {
      ...payload.parameters,
      folder_delivery: {
        ...delivery,
        source_names: Object.fromEntries(
          assetIds.map((assetId) => [assetId, delivery.source_names?.[assetId] || 'image']),
        ),
        part_index: index + 1,
        part_count: chunks.length,
      },
    },
  }));
}

async function compileSubmissionPayload(draft) {
  const fingerprint = submissionFingerprint(draft);
  const pending = state.pendingSubmission;
  if (
    pending?.fingerprint === fingerprint
    && pending.payload?.client_request_id === pending.requestId
  ) return pending.payload;

  const bundle = await compileKnowledgePreview({
    mode: draft.mode,
    brief: draft.parameters.brief,
    force: true,
  });
  const requestId = createClientRequestId();
  const payload = {
    ...createSubmissionSnapshot({
      mode: draft.mode,
      sourceAssetIds: draft.source_asset_ids,
      parameters: {
        ...draft.parameters,
        knowledge_refs: bundle?.sources || [],
      },
    }),
    client_request_id: requestId,
  };
  persistPendingSubmission({ fingerprint, requestId, payload });
  return payload;
}

async function handleGenerate() {
  if (state.submitting) return;
  if (state.currentMode === 'cutout-batch' && cutoutSelectionState().strategy === 'semantic') {
    const readiness = semanticCutoutReadiness(cutoutSelectionState(), selectedAssetIds());
    if (!readiness.ready) {
      if (readiness.action === 'confirm') await openSemanticSelection();
      else toast(readiness.message, 'error', 4200);
      return;
    }
  }
  const submissionDraft = captureSubmissionDraft();
  if (!submissionDraft.source_asset_ids.length) return;
  state.submitting = true;
  updateCtaState();
  let payload = null;
  try {
    captureModeSnapshot(submissionDraft.mode);
    let savedDraft = await flushWorkspaceDraft(submissionDraft.mode, false);
    if (!savedDraft) savedDraft = await flushWorkspaceDraft(submissionDraft.mode, false);
    if (!savedDraft) throw new Error('当前工作草稿未能安全保存，请重试');
    payload = await compileSubmissionPayload(submissionDraft);
    const jobPayloads = jobPayloadsForSubmission(payload);
    const responses = [];
    if (jobPayloads.length === 1) responses.push(await API.createJob(payload));
    else {
      for (const jobPayload of jobPayloads) responses.push(await API.createJob(jobPayload));
    }
    clearPendingSubmission(payload.client_request_id);
    const response = responses[0];
    const job = response?.job || response;
    const jobId = job?.id || response?.job_id || '';
    const sessionId = job?.session_id || response?.session_id || '';
    const modeSnapshot = state.modeSnapshots[submissionDraft.mode] || {};
    state.modeSnapshots[submissionDraft.mode] = { ...modeSnapshot, active_job_id: jobId };
    if (state.workspaceDrafts[submissionDraft.mode]) {
      state.workspaceDrafts[submissionDraft.mode] = {
        ...state.workspaceDrafts[submissionDraft.mode],
        active_job_id: jobId,
      };
    }
    scheduleWorkspaceDraftSave(submissionDraft.mode, 0);
    if (state.currentMode === submissionDraft.mode) {
      state.currentTaskId = jobId;
      state.currentSessionId = sessionId;
      $('#summary-result').textContent = `${submissionDraft.source_asset_ids.length} 项任务已入队`;
      $('#summary-result-note').textContent = '可继续选素材、切换模式或发起新任务';
    }
    if (submissionDraft.parameters.folder_delivery) {
      const currentFolderBatch = state.folderBatches[submissionDraft.mode];
      if (currentFolderBatch) currentFolderBatch.status = 'submitted';
      captureModeSnapshot(submissionDraft.mode);
      renderFolderSource();
    }
    toast(
      responses.length > 1
        ? `${submissionDraft.source_asset_ids.length} 张已拆为 ${responses.length} 批加入后台任务`
        : '任务已加入后台，可继续组织素材',
      'success',
    );
    await loadJobs(true);
    workflowDock.close(false);
    openDrawer('jobs');
  } catch (error) {
    if (payload && !payload.parameters?.folder_delivery && isDefinitiveJobRejection(error)) {
      clearPendingSubmission(payload.client_request_id);
    }
    toast(`任务提交失败：${formatApiError(error, '持久任务接口不可用')}`, 'error', 6500);
  } finally {
    state.submitting = false;
    updateCtaState();
  }
}

function jobProgress(job) {
  return jobCompletionProgress(job);
}

function jobCounts(job) {
  const items = Array.isArray(job?.items) ? job.items : [];
  const count = (status) => items.filter((item) => item.status === status).length;
  const completed = Number(job.completed_items ?? count('completed'));
  const failed = Number(job.failed_items ?? count('failed'));
  const decided = completed + failed;
  return {
    total: Number(job.total_items ?? items.length ?? 0),
    completed,
    failed,
    canceled: Number(job.canceled_items ?? count('canceled')),
    successRate: decided ? Math.round((completed / decided) * 100) : null,
  };
}

function resultIdsForJob(job) {
  return [...new Set((job?.items || []).flatMap((item) => Array.isArray(item.result_asset_ids) ? item.result_asset_ids : []))];
}

function adjustmentLineage(job) {
  const adjustment = job?.parameters?.adjustment;
  return adjustment && typeof adjustment === 'object' && !Array.isArray(adjustment)
    ? adjustment
    : null;
}

async function fetchResultItem(assetId, job, index = 0, overrides = {}) {
  let asset = null;
  try {
    const response = await API.getAsset(assetId);
    asset = response?.asset || response;
  } catch (_) { /* the durable content URL remains usable */ }
  const owner = (job?.items || []).find((jobItem) => (
    (jobItem.result_asset_ids || []).map(String).includes(String(assetId))
  ));
  const contentUrl = await API.getAssetContentUrl(assetId);
  return {
    asset_id: assetId,
    id: assetId,
    name: asset?.name || `product-atelier-${index + 1}.${job?.mode === 'cutout-batch' ? 'png' : 'jpg'}`,
    url: contentUrl,
    content_url: contentUrl,
    thumbnail_url: asset?.thumbnail_url || '',
    role: asset?.role || (job?.mode === 'cutout-batch' ? 'result_cutout' : 'result_main'),
    generation_id: overrides.generation_id || owner?.generation_id || '',
    job_id: overrides.job_id || job?.id || '',
    width: asset?.width || null,
    height: asset?.height || null,
    version_label: overrides.version_label || '',
    is_parent_version: Boolean(overrides.is_parent_version),
  };
}

function renderJobDockSummary() {
  const jobs = state.jobs;
  const ongoing = jobs.filter((job) => ['queued', 'running', 'paused', 'canceling', 'interrupted'].includes(job.status));
  const progressing = ongoing.filter((job) => job.status !== 'paused');
  const paused = ongoing.filter((job) => job.status === 'paused');
  const progressScope = ongoing.length ? ongoing : jobs;
  const items = progressScope.flatMap((job) => Array.isArray(job.items) ? job.items : []);
  const overall = queueCompletionProgress(progressScope);
  const completed = items.filter((item) => item.status === 'completed').length;
  const failed = items.filter((item) => item.status === 'failed').length;
  const decided = completed + failed;
  const outcomeCopy = decided ? ` · 已结束项成功率 ${Math.round((completed / decided) * 100)}%` : '';
  const percent = Math.round(overall * 100);
  $('#job-dock-progress').textContent = `${percent}%`;
  $('#workspace-progress-percent').textContent = `${percent}%`;
  $('#workspace-progress-bar').style.width = `${percent}%`;
  $('#job-dock-summary').textContent = state.jobsAvailable
    ? (ongoing.length
      ? `${ongoing.length} 个未结束${paused.length ? ` · ${paused.length} 个已暂停` : ''} · ${jobs.length} 个记录`
      : (jobs.length ? `${jobs.length} 个任务已恢复` : '暂无任务'))
    : '任务接口未就绪';
  $('#job-dock-dot').className = `job-dock-dot ${progressing.length ? 'active' : ''} ${paused.length && !progressing.length ? 'paused' : ''} ${state.jobsAvailable ? '' : 'error'}`.trim();
  $('#jobs-overall-percent').textContent = `${percent}%`;
  $('#jobs-overall-bar').style.width = `${percent}%`;
  $('#jobs-overall-copy').textContent = progressing.length
    ? `${progressing.length} 个任务正在推进${paused.length ? `，${paused.length} 个已暂停` : ''}，可继续编排新素材${outcomeCopy}`
    : (paused.length
      ? `${paused.length} 个任务已暂停，继续后将领取剩余排队项${outcomeCopy}`
      : (jobs.length ? `任务状态已从本地账本恢复${outcomeCopy}` : '新任务会在这里持续追踪'));
  $('#job-summary-completed').textContent = String(jobs.filter((job) => job.status === 'completed').length);
  $('#job-summary-running').textContent = String(jobs.filter((job) => ['queued', 'running', 'canceling'].includes(job.status)).length);
  $('#job-summary-attention').textContent = String(jobs.filter((job) => ['partial', 'failed', 'interrupted', 'paused'].includes(job.status)).length);
  renderJobRuntime();
  renderRailNotice();
}

function renderRailNotice() {
  const badge = $('.rail-notice-dot', $('#btn-rail-jobs'));
  if (!badge) return;
  const jobs = state.jobs || [];
  const pendingItems = (jobs || []).reduce((sum, job) => {
    if (!['partial', 'failed', 'interrupted', 'paused', 'queued', 'running'].includes(job.status)) return sum;
    const items = Array.isArray(job.items) ? job.items : [];
    return sum + items.filter((item) => ['failed', 'interrupted', 'running'].includes(item.status)).length;
  }, 0);
  const attention = pendingItems
    || jobs.filter((job) => ['partial', 'failed', 'interrupted', 'paused'].includes(job.status)).length;
  badge.textContent = attention > 0 ? String(Math.min(attention, 99)) : '';
  badge.classList.toggle('has-count', attention > 0);
  badge.setAttribute('aria-hidden', attention > 0 ? 'false' : 'true');
}

function renderJobRuntime() {
  const root = $('#job-runtime');
  const title = $('#job-runtime-title');
  const detail = $('#job-runtime-detail');
  const icon = $('.job-runtime__icon', root);
  const runtime = state.jobRuntime;
  root.classList.remove('is-active', 'is-warning');
  if (!runtime) {
    root.classList.add('is-warning');
    icon.textContent = '·';
    title.textContent = '资源状态暂不可读';
    detail.textContent = '任务账本仍可用，等待执行器重新连接';
    return;
  }
  const inFlight = Number(runtime.in_flight || 0);
  const unreconciled = Array.isArray(runtime.unreconciled_workers) ? runtime.unreconciled_workers.length : 0;
  const use = runtime.resource_in_use || {};
  const limits = runtime.resource_limits || {};
  const resourceNames = { 'cloud-image': '云端生图', 'local-cutout': '本地抠图', vlm: '视觉理解' };
  const resourceCopy = Object.entries(limits).map(([name, limit]) => `${resourceNames[name] || name} ${Number(use[name] || 0)}/${Number(limit || 0)}`).join(' · ');
  if (unreconciled) {
    root.classList.add('is-warning');
    icon.textContent = '!';
    title.textContent = '后台正在收口中断现场';
    detail.textContent = `${unreconciled} 个工作项等待账本确认${resourceCopy ? ` · ${resourceCopy}` : ''}`;
  } else if (inFlight || Object.values(use).some((value) => Number(value) > 0)) {
    root.classList.add('is-active');
    icon.textContent = '↻';
    title.textContent = '后台资源正在工作';
    detail.textContent = `${inFlight} 个执行项${resourceCopy ? ` · ${resourceCopy}` : ''}`;
  } else if (runtime.running) {
    icon.textContent = '✓';
    title.textContent = '后台资源已释放';
    detail.textContent = `${resourceCopy || '执行资源 0 占用'} · 没有未归属工作项`;
  } else {
    root.classList.add('is-warning');
    icon.textContent = '·';
    title.textContent = '任务执行器尚未启动';
    detail.textContent = '任务记录安全保存在本地账本中';
  }
}

const PERMANENT_JOB_ERRORS = new Set([
  'INVALID_SOURCE_IMAGE', 'UNSUPPORTED_JOB_MODE', 'INVALID_VARIATION_COUNT',
  'INVALID_PRODUCT_DETECTION', 'NO_PRODUCTS_DETECTED', 'TOO_MANY_PRODUCTS_DETECTED',
  'INVALID_DELIVERY_PATH', 'INVALID_ADJUSTMENT_REFERENCE',
]);

function jobFailureCopy(item) {
  const code = String(item?.error_code || '').trim();
  const raw = String(item?.error_message || item?.error || '').trim();
  if (!code && !raw) return { code: '', permanent: false, message: '', raw: '' };
  const known = {
    PROCESS_RESTARTED: '应用曾在处理中断；该项目可以从安全检查点重新执行。',
    WORKER_INFRASTRUCTURE_FAILURE: '后台工作进程意外停止；素材和已成功结果仍然保留。',
    OUTPUT_ROOT_WRITE_FAILED: '交付目录当前无法写入，请恢复磁盘连接或在设置中重新选择目录。',
    INVALID_OUTPUT_ROOT: '交付目录无效，请在设置中重新选择可写入的位置。',
    INVALID_SOURCE_IMAGE: '源文件已经损坏或不是可读取的图片，重复执行不会修复该文件。',
    UNSUPPORTED_JOB_MODE: '这条历史任务使用了当前版本不支持的工作流，无法继续执行。',
    INVALID_VARIATION_COUNT: '历史任务的方案数量不符合当前规则，需要回到现场重新设置。',
    INVALID_PRODUCT_DETECTION: '合照识别结果结构无效，请更换清晰素材后重新建立任务。',
    NO_PRODUCTS_DETECTED: '图片中没有识别到可拆分产品，请更换更清晰的合照。',
    TOO_MANY_PRODUCTS_DETECTED: '图片中的产品数量超过当前安全拆分上限，请分组后重新导入。',
    INVALID_DELIVERY_PATH: '整夹交付路径未通过安全检查，需要重新载入源文件夹。',
    INVALID_ADJUSTMENT_REFERENCE: '上一版本结果已不可读取；原图和任务记录仍保留，需要从可用版本重新发起调整。',
    USER_CANCELED: '该项目已由你取消。',
    PROCESSOR_ERROR: '处理器未能完成该项目；可以单独重试，若再次失败请查看原始详情。',
  };
  const hasChinese = /[\u3400-\u9fff]/.test(raw);
  return {
    code,
    permanent: PERMANENT_JOB_ERRORS.has(code),
    message: known[code] || (hasChinese ? raw : '处理过程中发生未分类错误；可以单独重试，原始详情已保留。'),
    raw,
  };
}

function retryableJobItems(job) {
  return (job?.items || []).filter((item) => (
    ['failed', 'interrupted'].includes(item.status) && !jobFailureCopy(item).permanent
  ));
}

const MUTATING_JOB_ACTIONS = new Set(['pause', 'resume', 'cancel', 'retry-item', 'retry-failed']);

function jobActionKey(action, jobId = '', itemId = '') {
  return `${action}:${jobId}:${itemId}`;
}

function jobActionDisabled(action, jobId = '', itemId = '') {
  return state.jobActionsInFlight.has(jobActionKey(action, jobId, itemId))
    || (MUTATING_JOB_ACTIONS.has(action) && state.jobMutationsInFlight.has(jobId));
}

function captureJobListView(list) {
  const active = document.activeElement;
  const action = active instanceof HTMLElement ? active.closest('[data-job-action]') : null;
  const card = active instanceof HTMLElement ? active.closest('.job-card') : null;
  return {
    scrollTop: list.scrollTop,
    itemScroll: new Map($$('.job-card', list).map((jobCard) => [
      jobCard.dataset.jobId,
      $('.job-items', jobCard)?.scrollTop || 0,
    ])),
    focus: action ? {
      kind: 'action',
      action: action.dataset.jobAction || '',
      jobId: action.dataset.jobId || '',
      itemId: action.dataset.itemId || '',
    } : (card ? { kind: 'card', jobId: card.dataset.jobId || '' } : null),
  };
}

function restoreJobListView(list, view) {
  list.scrollTop = view.scrollTop;
  $$('.job-card', list).forEach((card) => {
    const items = $('.job-items', card);
    if (items) items.scrollTop = view.itemScroll.get(card.dataset.jobId) || 0;
  });
  if (!view.focus) return;
  let target = null;
  if (view.focus.kind === 'action') {
    target = $$('[data-job-action]', list).find((button) => (
      (button.dataset.jobAction || '') === view.focus.action
      && (button.dataset.jobId || '') === view.focus.jobId
      && (button.dataset.itemId || '') === view.focus.itemId
    ));
    if (target?.disabled) target = null;
  }
  if (!target && view.focus.jobId) {
    target = $$('.job-card', list).find((card) => card.dataset.jobId === view.focus.jobId);
  }
  target?.focus({ preventScroll: true });
}

function renderJobFilters() {
  const counts = jobFilterCounts(state.jobs);
  $$('[data-job-filter]').forEach((button) => {
    const filter = button.dataset.jobFilter || 'all';
    const active = filter === state.jobFilter;
    button.classList.toggle('active', active);
    button.setAttribute('aria-pressed', String(active));
    const count = $('[data-job-filter-count]', button);
    if (count) count.textContent = String(counts[filter] || 0);
  });
}

function renderJobs(force = false) {
  const signature = jobsRenderSignature(
    state.jobs,
    state.jobsAvailable,
    state.jobActionsInFlight,
  );
  renderJobDockSummary();
  if (!force && signature === state.jobsRenderSignature) return;
  renderJobFilters();
  const list = $('#job-list');
  const view = captureJobListView(list);
  const filteredJobs = jobsForFilter(state.jobs, state.jobFilter);
  const visibleJobs = boundedJobsForDisplay(filteredJobs, state.jobVisibleLimit);
  if (!state.jobsAvailable) {
    list.innerHTML = statusPanelHtml('offline', {
      title: '任务账本暂时离线',
      detail: '素材与当前选择不会被清空；恢复连接后继续追踪。',
      fill: true,
      action: { label: '重试连接', attribute: 'data-job-action', value: 'refresh', disabled: jobActionDisabled('refresh'), busy: jobActionDisabled('refresh') },
    });
  } else if (!state.jobs.length) {
    list.innerHTML = statusPanelHtml('empty', { title: '还没有任务', detail: '从工作台选择图片并入队后，进度会在此持续保存。', fill: true });
  } else if (!filteredJobs.length) {
    list.innerHTML = statusPanelHtml('empty', { title: '这个工作流还没有任务', detail: '切换上方筛选，或回到工作台发起新任务。', fill: true });
  } else {
    const partialJobs = visibleJobs.filter((job) => job.status === 'partial');
    const renderedJobIds = new Set(visibleJobs.map((job) => String(job.id)));
    const hiddenJobCount = filteredJobs.filter((job) => !renderedJobIds.has(String(job.id))).length;
    const partialSummary = partialJobs.length ? statusPanelHtml('partial', {
      title: `${partialJobs.length} 个任务部分完成`,
      detail: '成功项目已锁定；打开对应任务，只重试失败项即可。',
      compact: true,
    }) : '';
    list.innerHTML = partialSummary + visibleJobs.map((job) => {
      const status = JOB_STATUS[job.status] || { label: job.status || '未知', tone: 'unknown' };
      const progress = Math.round(jobProgress(job) * 100);
      const counts = jobCounts(job);
      const retryable = retryableJobItems(job);
      const hasResults = resultIdsForJob(job).length > 0;
      const lifecycleActions = jobLifecycleActions(job.status);
      const canPause = lifecycleActions.includes('pause');
      const canResume = lifecycleActions.includes('resume');
      const canCancel = lifecycleActions.includes('cancel');
      const itemsExpanded = state.expandedJobIds.has(job.id);
      const visibleItems = jobItemsForDisplay(job, itemsExpanded, 5);
      const items = visibleItems.map((item) => {
        const source = findJobSourceAsset(item.source_asset_id);
        const sourceIndex = Math.max(0, (job.items || []).findIndex((candidate) => candidate.id === item.id));
        const itemStatus = JOB_STATUS[item.status] || { label: item.status || '未知', tone: 'unknown' };
        const itemProgress = Math.round(itemCompletionProgress(item) * 100);
        const failure = jobFailureCopy(item);
        const canRetryItem = ['failed', 'interrupted'].includes(item.status) && !failure.permanent;
        return `<li class="job-item job-item--${escapeHtml(itemStatus.tone)}">
          <img src="${escapeHtml(assetUrl(source))}" alt="${escapeHtml(source?.name || `任务素材 ${sourceIndex + 1}`)}" loading="lazy" />
          <span class="job-item__copy"><strong>${escapeHtml(source?.name || `任务素材 ${sourceIndex + 1}`)}</strong><span>${escapeHtml(itemStatus.label)} · 完成度 ${itemProgress}%${failure.permanent ? ' · 永久失败' : ''}</span>${failure.message ? `<small title="${escapeHtml(failure.raw || failure.message)}">${escapeHtml(failure.message)}</small>` : ''}</span>
          <span class="job-item__bar"><i style="width:${itemProgress}%"></i></span>
          ${canRetryItem ? `<button type="button" data-job-action="retry-item" data-job-id="${escapeHtml(job.id)}" data-item-id="${escapeHtml(item.id)}" ${jobActionDisabled('retry-item', job.id, item.id) ? 'disabled aria-busy="true"' : ''}>单独重试</button>` : ''}
        </li>`;
      }).join('');
      const issueCount = (job.items || []).filter((item) => ['failed', 'interrupted'].includes(item.status)).length;
      const outcome = status.tone === 'completed'
        ? '成功项目已经锁定，不会因其他项目失败而重复执行。'
        : (issueCount
          ? `${counts.completed} 个成功项目保持不变；${retryable.length} 个可重试，${issueCount - retryable.length} 个需要更换素材或设置。`
          : (['running', 'queued', 'canceling'].includes(status.tone)
            ? '任务在后台继续推进，切换页面不会中断当前工作。'
            : '任务现场、素材选择与参数快照均已保存在本地账本。'));
      const icon = status.tone === 'completed' ? '✓' : (['partial', 'failed', 'interrupted'].includes(status.tone) ? '!' : '↻');
      return `<article class="job-card job-card--${escapeHtml(status.tone)}" data-job-id="${escapeHtml(job.id)}" tabindex="-1">
        <header><span><i class="job-card__icon">${icon}</i><small>${escapeHtml(MODE_CONFIG[job.mode]?.badge || '创作任务')} · ${escapeHtml(status.label)}</small><strong>${escapeHtml(job.title || MODE_CONFIG[job.mode]?.label || '创作任务')}</strong></span><span class="job-status job-status--${escapeHtml(status.tone)}">${counts.completed}/${counts.total}</span></header>
        <div class="job-progress"><span><i style="width:${progress}%"></i></span><strong>${progress}%</strong></div>
        <div class="job-counts"><span>${counts.total} 项</span><span>${counts.completed} 成功</span><span>${counts.failed} 失败</span><span>${counts.canceled} 取消</span><span>成功率 ${counts.successRate === null ? '—' : `${counts.successRate}%`}</span><time>${escapeHtml(formatTime(job.updated_at || job.created_at))}</time></div>
        <p class="job-outcome"><i></i><span>${escapeHtml(outcome)}</span></p>
        ${items ? `<ul class="job-items ${itemsExpanded ? 'is-expanded' : ''}">${items}</ul>` : ''}
        ${(job.items || []).length > Math.max(5, visibleItems.length) || itemsExpanded ? `<button class="job-items-toggle" type="button" data-job-action="toggle-items" data-job-id="${escapeHtml(job.id)}" aria-expanded="${itemsExpanded}">${itemsExpanded ? '收起项目' : `查看全部 ${(job.items || []).length} 项`}</button>` : ''}
        <footer>
          ${hasResults ? `<button type="button" data-job-action="open-results" data-job-id="${escapeHtml(job.id)}" ${jobActionDisabled('open-results', job.id) ? 'disabled aria-busy="true"' : ''}>打开结果</button>` : ''}
          ${!hasResults ? `<button type="button" data-job-action="open-workspace" data-job-id="${escapeHtml(job.id)}" ${jobActionDisabled('open-workspace', job.id) ? 'disabled aria-busy="true"' : ''}>回到现场</button>` : ''}
          ${retryable.length ? `<button class="primary-job-action" type="button" data-job-action="retry-failed" data-job-id="${escapeHtml(job.id)}" ${jobActionDisabled('retry-failed', job.id) ? 'disabled aria-busy="true"' : ''}>只重试失败项</button>` : ''}
          ${canPause ? `<button type="button" data-job-action="pause" data-job-id="${escapeHtml(job.id)}" ${jobActionDisabled('pause', job.id) ? 'disabled aria-busy="true"' : ''}>暂停任务</button>` : ''}
          ${canResume ? `<button type="button" data-job-action="resume" data-job-id="${escapeHtml(job.id)}" ${jobActionDisabled('resume', job.id) ? 'disabled aria-busy="true"' : ''}>继续任务</button>` : ''}
          ${canCancel ? `<button class="danger" type="button" data-job-action="cancel" data-job-id="${escapeHtml(job.id)}" ${jobActionDisabled('cancel', job.id) ? 'disabled aria-busy="true"' : ''}>取消任务</button>` : ''}
        </footer>
      </article>`;
    }).join('') + (hiddenJobCount
      ? `<button class="job-list-more" type="button" data-job-action="show-more-jobs">再显示 ${Math.min(12, hiddenJobCount)} 个任务 <small>剩余 ${hiddenJobCount} 个</small></button>`
      : '');
  }
  $$('[data-job-action]', list).forEach((button) => button.addEventListener('click', () => handleJobAction(button)));
  state.jobsRenderSignature = signature;
  restoreJobListView(list, view);
}

function announceJobChanges(jobs) {
  jobs.forEach((job) => {
    const before = state.knownJobStatuses.get(job.id);
    state.knownJobStatuses.set(job.id, job.status);
    if (!before || before === job.status) return;
    let message = '';
    if (job.status === 'completed') { message = `${MODE_CONFIG[job.mode]?.label || '任务'}已完成`; toast(message, 'success'); }
    else if (job.status === 'partial') { message = `${MODE_CONFIG[job.mode]?.label || '任务'}部分完成，失败项可重试`; toast(message, 'error', 5200); }
    else if (job.status === 'failed') { message = `${MODE_CONFIG[job.mode]?.label || '任务'}失败，详情已保留`; toast(message, 'error', 5200); }
    else if (job.status === 'canceled') { message = `${MODE_CONFIG[job.mode]?.label || '任务'}已取消`; toast(message); }
    if (message) $('#job-status-announcer').textContent = message;
  });
}

function invalidateJobsRead() {
  state.jobsRequestVersion += 1;
  state.jobsAbortController?.abort();
  state.jobsAbortController = null;
}

async function loadJobs(silent = false) {
  const requestVersion = ++state.jobsRequestVersion;
  state.jobsAbortController?.abort();
  const controller = new AbortController();
  state.jobsAbortController = controller;
  try {
    const [result, runtime] = await Promise.all([
      API.getJobs(100, { signal: controller.signal, timeoutMs: 8000 }),
      API.getJobRuntime({ signal: controller.signal, timeoutMs: 8000 }).catch(() => null),
    ]);
    if (requestVersion !== state.jobsRequestVersion) return null;
    const jobs = Array.isArray(result) ? result : (result?.jobs || []);
    await hydrateJobSourceAssets(jobs);
    if (requestVersion !== state.jobsRequestVersion) return null;
    announceJobChanges(jobs);
    state.jobs = jobs;
    state.jobRuntime = runtime;
    state.jobsAvailable = true;
    renderJobs();
    return true;
  } catch (error) {
    if (requestVersion !== state.jobsRequestVersion) return null;
    state.jobsAvailable = false;
    state.jobRuntime = null;
    renderJobs();
    if (!silent) toast(`后台任务读取失败：${formatApiError(error, '持久任务接口不可用')}`, 'error', 6000);
    return false;
  } finally {
    if (requestVersion === state.jobsRequestVersion) state.jobsAbortController = null;
  }
}

function startJobPolling() {
  if (state.jobPollTimer) window.clearTimeout(state.jobPollTimer);
  const tick = async () => {
    const ok = await loadJobs(true);
    if (ok === false) {
      setBackendStatus('connecting', '重连中');
      const recovered = await connectBackend();
      if (recovered) return;
    }
    const hasActive = state.jobs.some((job) => (
      ['queued', 'running', 'canceling', 'interrupted'].includes(job.status)
      || (job.status === 'paused' && (job.items || []).some((item) => item.status === 'running'))
    ));
    state.jobPollTimer = window.setTimeout(tick, ok !== false ? (hasActive ? 1100 : 4200) : 8500);
  };
  tick();
}

async function handleJobAction(button) {
  const action = button.dataset.jobAction;
  const jobId = button.dataset.jobId;
  const itemId = button.dataset.itemId || '';
  if (action === 'toggle-items') {
    if (state.expandedJobIds.has(jobId)) state.expandedJobIds.delete(jobId);
    else state.expandedJobIds.add(jobId);
    renderJobs(true);
    return;
  }
  if (action === 'show-more-jobs') {
    state.jobVisibleLimit += 12;
    renderJobs(true);
    return;
  }
  const actionKey = jobActionKey(action, jobId, itemId);
  const isMutation = MUTATING_JOB_ACTIONS.has(action);
  if (jobActionDisabled(action, jobId, itemId)) return;
  state.jobActionsInFlight.add(actionKey);
  if (isMutation) state.jobMutationsInFlight.add(jobId);
  button.disabled = true;
  if (isMutation) {
    $$('[data-job-action]', button.closest('.job-card')).forEach((control) => {
      if (MUTATING_JOB_ACTIONS.has(control.dataset.jobAction)) control.disabled = true;
    });
  }
  try {
    if (action === 'refresh') await loadJobs(false);
    else if (action === 'pause') { invalidateJobsRead(); await API.pauseJob(jobId); toast('已暂停新任务项领取'); }
    else if (action === 'resume') { invalidateJobsRead(); await API.resumeJob(jobId); toast('任务已继续', 'success'); }
    else if (action === 'cancel') { invalidateJobsRead(); await API.cancelJob(jobId); toast('已发出取消请求'); }
    else if (action === 'retry-item') { invalidateJobsRead(); await API.retryJob(jobId, [itemId]); toast('失败项已重新入队', 'success'); }
    else if (action === 'retry-failed') {
      invalidateJobsRead();
      const job = state.jobs.find((entry) => entry.id === jobId);
      const ids = retryableJobItems(job).map((item) => item.id);
      if (!ids.length) throw new Error('没有可重试项目；永久失败项需要更换素材或设置');
      await API.retryJob(jobId, ids);
      toast(`${ids.length} 个失败项已重新入队`, 'success');
    } else if (action === 'open-results') await openJobResults(jobId);
    else if (action === 'open-workspace') {
      const response = await API.getJob(jobId);
      await openJobWorkspace(response?.job || response);
    }
    await loadJobs(true);
  } catch (error) {
    toast(`任务操作失败：${formatApiError(error, '任务接口不可用')}`, 'error', 6000);
  } finally {
    state.jobActionsInFlight.delete(actionKey);
    if (isMutation) state.jobMutationsInFlight.delete(jobId);
    button.disabled = false;
    renderJobs(true);
  }
}

async function openJobWorkspace(job, announce = true) {
  if (!job?.id || !MODE_CONFIG[job.mode]) throw new Error('任务工作流信息不完整');
  if (state.currentMode !== job.mode) switchMode(job.mode, true, false);
  await loadWorkspace(job.mode, true);

  const sourceIds = Array.isArray(job.snapshot?.source_asset_ids)
    ? job.snapshot.source_asset_ids.map(String)
    : (job.items || []).map((item) => String(item.source_asset_id || '')).filter(Boolean);
  const activeIds = new Set(state.assets.map((asset) => String(asset.id)));
  const restoredIds = sourceIds
    .filter((assetId) => activeIds.has(assetId))
    .slice(0, MODE_CONFIG[job.mode].maxFiles);
  const fallback = state.modeSnapshots[job.mode] || {};

  state.hydratingWorkspace = true;
  try {
    state.modeSelections[job.mode] = restoredIds;
    state.modeSnapshots[job.mode] = jobWorkspaceSnapshot(job, fallback);
    state.currentTaskId = job.id;
    state.currentSessionId = job.session_id || '';
    state.currentGenerationId = job.items?.[0]?.generation_id || '';
    state.results = null;
    state.viewerIndex = 0;
    restoreModeSnapshot(job.mode);
    syncLegacySelection();
    renderQueue();
    renderFileMeta();
    updateQuickControls();
  } finally {
    state.hydratingWorkspace = false;
  }
  assetManager.sync();
  scheduleWorkspaceDraftSave(job.mode, 0);
  switchPage('process');
  closeDrawer('jobs');
  const missing = Math.max(0, sourceIds.length - restoredIds.length);
  $('#summary-result').textContent = `${MODE_CONFIG[job.mode].label} · 已回到任务现场`;
  $('#summary-result-note').textContent = missing
    ? `${restoredIds.length} 张源图已恢复，${missing} 张已移入回收站；任务快照仍保留`
    : `${restoredIds.length} 张源图与提交参数已从任务快照恢复`;
  if (announce) toast('已回到该任务的素材与参数现场', 'success');
  return { restored: restoredIds.length, missing };
}

async function openJobResults(jobId) {
  const response = await API.getJob(jobId);
  const job = response?.job || response;
  const resultIds = resultIdsForJob(job);
  if (!resultIds.length) throw new Error('该任务还没有可打开的结果');
  const adjustment = adjustmentLineage(job);
  const ownVersion = Number(adjustment?.version || 0);
  const parentResultId = String(adjustment?.parent_result_asset_id || '');
  const [ownItems, parentItem, traceResponse, reviewResponse, parentReviewResponse] = await Promise.all([
    Promise.all(resultIds.map((assetId, index) => fetchResultItem(assetId, job, index, {
      job_id: job.id,
      version_label: ownVersion ? `V${ownVersion}` : '',
    }))),
    parentResultId
      ? fetchResultItem(parentResultId, null, 0, {
        job_id: String(adjustment?.parent_job_id || ''),
        generation_id: String(adjustment?.parent_generation_id || ''),
        version_label: `V${Math.max(1, ownVersion - 1)}`,
        is_parent_version: true,
      }).catch(() => null)
      : Promise.resolve(null),
    API.getJobTraces(job.id).catch(() => ({ traces: [] })),
    API.getJobReviews(job.id).catch(() => ({ reviews: [] })),
    adjustment?.parent_job_id
      ? API.getJobReviews(adjustment.parent_job_id).catch(() => ({ reviews: [] }))
      : Promise.resolve({ reviews: [] }),
  ]);
  const items = parentItem ? [parentItem, ...ownItems] : ownItems;
  await openJobWorkspace(job, false);
  const source = findJobSourceAsset(job.items?.[0]?.source_asset_id);
  state.originalDataUrl = assetUrl(source, 'content');
  state.currentTaskId = job.id;
  state.currentSessionId = job.session_id || '';
  state.currentGenerationId = ownItems[0]?.generation_id || job.items?.[0]?.generation_id || '';
  state.resultReviews = [...new Map([
    ...(Array.isArray(parentReviewResponse?.reviews) ? parentReviewResponse.reviews : []),
    ...(Array.isArray(reviewResponse?.reviews) ? reviewResponse.reviews : []),
  ].map((review) => [review.id, review])).values()];
  applyTaskKnowledgeBundle(knowledgeBundleFromEvidence({
    brief: job.snapshot?.brief || {},
    traces: traceResponse?.traces || [],
    generation: { knowledge_refs: job.snapshot?.knowledge_refs || [] },
  }));
  state.results = {
    main: items.filter((item) => item.role !== 'result_cutout'),
    cutout: items.filter((item) => item.role === 'result_cutout'),
  };
  state.resultTab = job.mode === 'cutout-batch' || !state.results.main.length ? 'cutout' : 'main';
  const selectedItems = getResultItems(state.resultTab);
  state.viewerIndex = Math.max(0, selectedItems.findIndex((item) => item.job_id === job.id));
  const selectedItem = selectedItems[state.viewerIndex] || ownItems[0] || items[0];
  state.currentGenerationId = selectedItem?.generation_id || state.currentGenerationId;
  const modeSnapshot = state.modeSnapshots[job.mode] || {};
  state.modeSnapshots[job.mode] = {
    ...modeSnapshot,
    active_job_id: job.id,
    current_generation_id: state.currentGenerationId || null,
    current_result_asset_id: selectedItem?.asset_id || null,
  };
  scheduleWorkspaceDraftSave(job.mode, 0);
  await settleWorkspaceDraft(job.mode);
  renderResults();
  setStage('success');
  switchPage('process');
  closeDrawer('jobs');
  $('#summary-result').textContent = adjustment
    ? `新版本 V${ownVersion} 已从任务账本恢复`
    : `${items.length} 个结果已从任务账本恢复`;
  $('#summary-result-note').textContent = adjustment
    ? '上一版本已并入版本对比；原任务、原图和反馈记录保持不变'
    : '可先检查成功项，再在后台任务中重试失败项';
}

function getResultItems(tab = state.resultTab) {
  return state.results && Array.isArray(state.results[tab]) ? state.results[tab] : [];
}

function getAllResultItems() {
  return collectResultItems(state.results);
}

function selectResultVersion(index, tab = state.resultTab) {
  const items = getResultItems(tab);
  if (!items.length) return;
  state.resultTab = tab;
  state.viewerIndex = Math.max(0, Math.min(Number(index) || 0, items.length - 1));
  const item = items[state.viewerIndex];
  const snapshot = state.modeSnapshots[state.currentMode] || {};
  state.currentGenerationId = item.generation_id || state.currentGenerationId || '';
  state.modeSnapshots[state.currentMode] = {
    ...snapshot,
    active_job_id: state.currentTaskId || snapshot.active_job_id || null,
    current_generation_id: state.currentGenerationId || null,
    current_result_asset_id: item.asset_id || null,
  };
  scheduleWorkspaceDraftSave(state.currentMode);
  renderResults();
}

function resultDataUrl(item, tab = state.resultTab) {
  if (!item) return '';
  if (item.data && String(item.data).startsWith('data:')) return item.data;
  if (item.data) return API.b64ToDataURL(item.data, tab === 'cutout' ? 'image/png' : 'image/jpeg');
  return item.content_url || item.url || '';
}

function activeFeedbackResultKey(item) {
  return [
    item?.job_id || state.currentTaskId || 'session',
    item?.generation_id || state.currentGenerationId || 'generation',
    item?.asset_id || item?.name || state.viewerIndex,
  ].join(':');
}

function renderFeedbackState() {
  const entry = $('#feedback-entry');
  const receipt = $('#feedback-receipt');
  const detail = $('#feedback-detail');
  if (!entry || !receipt || !detail) return;
  entry.hidden = state.feedbackRecorded;
  receipt.hidden = !state.feedbackRecorded;
  detail.hidden = state.feedbackRecorded || !['rejected', 'note'].includes(state.lastFeedbackSignal);
  $('#feedback-receipt-copy').textContent = state.feedbackReceipt || '已记录为下一版证据';
  const suggestionButton = $('#btn-feedback-suggestion');
  suggestionButton.hidden = !state.feedbackRecorded || !state.feedbackSuggestionId;
  suggestionButton.dataset.suggestionId = state.feedbackSuggestionId || '';
  ['#btn-adopt', '#btn-reject', '#btn-feedback'].forEach((selector) => {
    const button = $(selector);
    button.disabled = state.feedbackSubmitting;
    button.setAttribute('aria-busy', String(state.feedbackSubmitting));
  });
  ['#btn-review-adjust', '#btn-review-record', '#btn-review-suggest'].forEach((selector) => {
    const button = $(selector);
    if (!button) return;
    button.disabled = state.feedbackSubmitting;
    button.setAttribute('aria-busy', String(state.feedbackSubmitting));
  });
  $('#btn-feedback').textContent = state.feedbackSubmitting ? '记录中…' : '发送';
}

function prepareFeedbackForResult(item) {
  const key = activeFeedbackResultKey(item);
  const durable = reviewStateForResult(state.resultReviews, item?.asset_id || '');
  if (state.feedbackResultKey !== key) {
    state.feedbackResultKey = key;
    state.feedbackSubmitting = false;
    if (state.editingFeedbackResultKey !== key) state.editingFeedbackResultKey = '';
    $('#feedback-input').value = '';
  }
  const editing = state.editingFeedbackResultKey === key;
  state.feedbackRecorded = durable.reviewed && !editing;
  state.feedbackReceipt = durable.reviewed ? feedbackReceiptCopy(durable.receipt) : '';
  state.feedbackSuggestionId = durable.reviewed ? durable.receipt.suggestionId : '';
  if (durable.reviewed && !editing) {
    discardPendingReviewRequest(item?.asset_id || '');
    const decision = String(durable.decision || '');
    state.lastFeedbackSignal = decision === 'reject'
      ? 'rejected'
      : decision === 'adopt' ? 'adopted' : 'adjusted';
  } else if (!state.lastFeedbackSignal) {
    state.lastFeedbackSignal = 'note';
  }
  renderFeedbackState();
}

function renderResults() {
  $$('.result-tab').forEach((button) => {
    const active = button.dataset.rtab === state.resultTab;
    button.classList.toggle('active', active);
    button.setAttribute('aria-selected', String(active));
    button.tabIndex = active ? 0 : -1;
  });
  const items = getResultItems();
  if (!items.length) {
    const fallback = state.resultTab === 'main' ? 'cutout' : 'main';
    if (getResultItems(fallback).length) { state.resultTab = fallback; renderResults(); }
    return;
  }
  state.viewerIndex = Math.max(0, Math.min(state.viewerIndex, items.length - 1));
  const item = items[state.viewerIndex];
  prepareFeedbackForResult(item);
  const src = resultDataUrl(item);
  const viewerImage = $('#viewer-main-img');
  const showDimensions = () => {
    const width = Number(item.width || viewerImage.naturalWidth || 0);
    const height = Number(item.height || viewerImage.naturalHeight || 0);
    $('#result-dimensions').textContent = width && height ? `${width} × ${height} px` : '像素待读取';
  };
  viewerImage.onload = showDimensions;
  viewerImage.src = src;
  viewerImage.onclick = () => openModal(src);
  if (viewerImage.complete) showDimensions();
  $('#viewer-nav').hidden = items.length < 2;
  $('#viewer-counter').textContent = `${state.viewerIndex + 1} / ${items.length}`;
  $('#viewer-thumbs').innerHTML = items.map((entry, index) => `<button class="viewer-thumb ${index === state.viewerIndex ? 'active' : ''}" type="button" data-index="${index}"><img src="${resultDataUrl(entry)}" alt="结果 ${index + 1}" /></button>`).join('');
  $$('.viewer-thumb').forEach((button) => button.addEventListener('click', () => {
    selectResultVersion(Number(button.dataset.index));
  }));
  if (state.originalDataUrl && src) state.compareData = { original: state.originalDataUrl, result: src };
}

async function saveCurrentResults() {
  if (state.exporting) return;
  const items = getAllResultItems();
  if (!items.length) { toast('当前没有可导出的结果', 'error'); return; }
  const button = $('#btn-save-all');
  const previousLabel = button.textContent;
  state.exporting = true;
  button.disabled = true;
  button.textContent = '正在导出…';
  let outcome = { succeeded: [], failed: [] };
  try {
    outcome = await processResultItems(items, async (item, index) => {
      let data = item.data ? item.data.replace(/^data:[^,]+,/, '') : '';
      if (!data && (item.url || item.asset_id)) {
        const response = await fetch(item.url || await API.getAssetContentUrl(item.asset_id));
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        const bytes = new Uint8Array(await response.arrayBuffer());
        let binary = '';
        for (let offset = 0; offset < bytes.length; offset += 0x8000) binary += String.fromCharCode(...bytes.subarray(offset, offset + 0x8000));
        data = btoa(binary);
      }
      if (!data) throw new Error('结果内容为空');
      const isCutout = item.role === 'result_cutout';
      await API.saveImage(item.name || `product-atelier-${index + 1}.${isCutout ? 'png' : 'jpg'}`, data);
    });
  } finally {
    state.exporting = false;
    button.disabled = false;
    button.textContent = previousLabel;
  }
  const saved = outcome.succeeded.length;
  const failures = outcome.failed;
  if (saved && !failures.length) toast(`已导出全部 ${saved} 个结果`, 'success');
  else if (saved) toast(`已导出 ${saved} 个，${failures.length} 个失败`, 'error', 6000);
  else toast(`导出失败：${String(failures[0]?.error || '未知错误')}`, 'error', 6000);
}

async function recordFeedback(signal, reason = '', learningAction = 'record', reasonCodes = []) {
  const item = getResultItems()[state.viewerIndex];
  const reviewJobId = item?.job_id || state.currentTaskId;
  if (!reviewJobId || !item?.asset_id) { toast('当前没有可评审的结果版本', 'error'); return false; }
  if (state.feedbackSubmitting) return false;
  const normalizedSignal = normalizeFeedbackSignal(signal);
  const decision = backendDecisionForSignal(normalizedSignal);
  const normalizedReasons = normalizeReviewReasonCodes(normalizedSignal, reasonCodes);
  const reviewNote = String(reason || '').trim()
    || normalizedReasons.map((code) => reviewReasonLabel(code)).join('、');
  state.feedbackSubmitting = true;
  renderFeedbackState();
  const reviewPayload = {
    result_asset_id: item.asset_id,
    generation_id: item.generation_id || state.currentGenerationId || null,
    decision,
    reason_codes: normalizedReasons,
    note: reviewNote,
    learning_action: learningAction,
  };
  const requestId = reviewRequestId(item.asset_id, reviewPayload);
  try {
    const response = await API.submitResultReview(reviewJobId, {
      client_request_id: requestId,
      ...reviewPayload,
    });
    const review = response?.review || response;
    state.resultReviews = [...state.resultReviews, review];
    state.editingFeedbackResultKey = '';
    state.feedbackRecorded = true;
    const durable = reviewStateForResult(
      state.resultReviews,
      item.asset_id,
    );
    state.feedbackReceipt = feedbackReceiptCopy(durable.receipt);
    state.feedbackSuggestionId = durable.receipt.suggestionId;
    state.lastFeedbackSignal = 'note';
    clearPendingReviewRequest(item.asset_id, requestId);
    toast(normalizedSignal === 'adopted' ? '已记录采用：这版会成为成功证据' : '结果评审和反馈证据已写入任务账本', 'success');
    $('#feedback-input').value = '';
    return true;
  } catch (error) {
    if (isDefinitiveJobRejection(error)) clearPendingReviewRequest(item.asset_id, requestId);
    toast(`反馈记录失败：${error}`, 'error');
    return false;
  } finally {
    state.feedbackSubmitting = false;
    renderFeedbackState();
  }
}

async function startImmediateAdjustment(reason, reasonCodes = []) {
  const item = getResultItems()[state.viewerIndex];
  const parentJobId = item?.job_id || state.currentTaskId;
  if (!parentJobId || !item?.asset_id || item.role === 'result_cutout') {
    toast('当前版本不是可定向修改的商业主图', 'error');
    return false;
  }
  const note = String(reason || '').trim();
  if (!note) {
    toast('立即修改前，请写清楚这张图需要调整什么');
    return false;
  }
  if (state.feedbackSubmitting) return false;
  const reviewPayload = {
    result_asset_id: item.asset_id,
    generation_id: item.generation_id || state.currentGenerationId || null,
    decision: 'adjust',
    reason_codes: normalizeReviewReasonCodes('adjusted', reasonCodes),
    note,
    learning_action: 'regenerate',
  };
  const requestId = reviewRequestId(item.asset_id, reviewPayload);
  state.feedbackSubmitting = true;
  renderFeedbackState();
  try {
    const response = await API.adjustResult(parentJobId, {
      client_request_id: requestId,
      result_asset_id: item.asset_id,
      generation_id: reviewPayload.generation_id,
      reason_codes: reviewPayload.reason_codes.length ? reviewPayload.reason_codes : ['adjusted'],
      note,
    });
    const review = response?.review;
    if (review) state.resultReviews = [...state.resultReviews, review];
    const job = response?.job || null;
    if (!job?.id) throw new Error('派生任务未返回可追踪编号');
    clearPendingReviewRequest(item.asset_id, requestId);
    state.currentTaskId = job.id;
    state.currentSessionId = job.session_id || '';
    state.currentGenerationId = job.items?.[0]?.generation_id || '';
    const modeSnapshot = state.modeSnapshots[job.mode] || {};
    state.modeSnapshots[job.mode] = {
      ...modeSnapshot,
      active_job_id: job.id,
      current_generation_id: state.currentGenerationId || null,
      current_result_asset_id: null,
    };
    if (state.workspaceDrafts[job.mode]) {
      state.workspaceDrafts[job.mode] = {
        ...state.workspaceDrafts[job.mode],
        active_job_id: job.id,
        current_generation_id: state.currentGenerationId || null,
        current_result_asset_id: null,
      };
    }
    scheduleWorkspaceDraftSave(job.mode, 0);
    setStage('processing');
    switchPage('process');
    $('#summary-result').textContent = `新版本 V${response?.lineage?.version || 2} 已入队`;
    $('#summary-result-note').textContent = '仅执行本次调整，原图、上一版本与反馈记录均不会被覆盖';
    $('#review-reason-input').value = '';
    $('#btn-review-adjust').hidden = true;
    state.reviewDecision = '';
    await loadJobs(true);
    openDrawer('jobs');
    toast(response.created === false ? '调整任务已在后台继续' : '已从本张结果派生独立调整任务', 'success');
    return true;
  } catch (error) {
    if (isDefinitiveJobRejection(error)) clearPendingReviewRequest(item.asset_id, requestId);
    toast(`立即修改失败：${formatApiError(error, '无法建立调整任务')}`, 'error', 6500);
    return false;
  } finally {
    state.feedbackSubmitting = false;
    renderFeedbackState();
  }
}

async function completeCurrentWorkspace() {
  const item = getResultItems()[state.viewerIndex];
  if (!state.currentTaskId || !item?.asset_id) {
    toast('当前没有可完成的结果现场', 'error');
    return false;
  }
  if (state.workspaceCompletionInFlight) return false;
  const mode = state.currentMode;
  const candidateKey = completionRequestKey(mode, state.currentTaskId, item.asset_id);
  if (state.workspaceCompletionRequest?.candidateKey !== candidateKey) {
    state.workspaceCompletionRequest = {
      candidateKey,
      requestId: createClientRequestId(),
    };
  }
  const button = $('#btn-result-next');
  const previousLabel = button.textContent;
  state.workspaceCompletionInFlight = true;
  button.disabled = true;
  button.setAttribute('aria-busy', 'true');
  button.textContent = '正在完成现场…';
  try {
    const settled = await settleWorkspaceDraft(mode);
    if (!settled) throw new Error('当前草稿仍在保存，请稍后重试');
    const payload = {
      expected_revision: state.workspaceRevisions[mode],
      client_request_id: state.workspaceCompletionRequest.requestId,
      job_id: state.currentTaskId,
      result_asset_id: item.asset_id,
    };
    let response;
    try {
      response = await API.completeWorkspace(mode, payload);
    } catch (error) {
      const current = error?.detail?.current;
      if (Number(error?.status) !== 409
        || error?.detail?.code !== 'DRAFT_REVISION_CONFLICT'
        || String(current?.active_job_id || '') !== state.currentTaskId) throw error;
      state.workspaceDrafts[mode] = current;
      state.workspaceRevisions[mode] = Number(current.revision || state.workspaceRevisions[mode]);
      response = await API.completeWorkspace(mode, {
        ...payload,
        expected_revision: state.workspaceRevisions[mode],
      });
    }
    applyCompletedWorkspaceDraft(mode, response?.draft || response);
    state.workspaceCompletionRequest = null;
    switchPage('process');
    toast('当前现场已完成；结果和反馈仍保留在任务账本', 'success', 4200);
    window.setTimeout(() => $('#file-queue [data-asset-id]')?.focus(), 0);
    return true;
  } catch (error) {
    toast(
      `完成现场失败：${formatApiError(error, '本地账本暂不可写')}`,
      'error',
      7000,
      { label: '重试', onClick: completeCurrentWorkspace },
    );
    return false;
  } finally {
    state.workspaceCompletionInFlight = false;
    button.disabled = false;
    button.setAttribute('aria-busy', 'false');
    button.textContent = previousLabel;
  }
}

function openDrawer(name) {
  const drawers = { assets: $('#asset-drawer'), advanced: $('#advanced-drawer'), intelligence: $('#intelligence-drawer'), jobs: $('#job-drawer') };
  const layer = drawers[name];
  if (!layer) return;
  const drawer = $('.drawer', layer);
  const heading = $('.drawer-head h2', layer);
  const close = $('.drawer-close', layer);
  const backdrop = $('.drawer-backdrop', layer);
  if (drawer) {
    drawer.setAttribute('role', 'dialog');
    drawer.setAttribute('aria-modal', 'true');
    if (heading) {
      if (!heading.id) heading.id = `${name}-drawer-title`;
      drawer.setAttribute('aria-labelledby', heading.id);
    }
  }
  if (close && !close.getAttribute('aria-label')) close.setAttribute('aria-label', `关闭${heading?.textContent?.trim() || '抽屉'}`);
  if (backdrop) {
    backdrop.tabIndex = -1;
    if (!backdrop.getAttribute('aria-label')) backdrop.setAttribute('aria-label', `关闭${heading?.textContent?.trim() || '抽屉'}`);
  }
  drawerReturnFocus = document.activeElement;
  layer.hidden = false;
  if (name === 'jobs') {
    state.jobDrawerOpen = true;
    $('#btn-job-dock').setAttribute('aria-expanded', 'true');
    loadJobs(true);
  }
  $('.drawer-close', layer)?.focus();
}

function closeDrawer(name) {
  const drawers = { assets: $('#asset-drawer'), advanced: $('#advanced-drawer'), intelligence: $('#intelligence-drawer'), jobs: $('#job-drawer') };
  const layer = drawers[name];
  if (!layer) return;
  layer.hidden = true;
  if (name === 'jobs') {
    state.jobDrawerOpen = false;
    $('#btn-job-dock').setAttribute('aria-expanded', 'false');
  }
  if (name === 'advanced') updateQuickControls();
  if (drawerReturnFocus instanceof HTMLElement) drawerReturnFocus.focus();
  drawerReturnFocus = null;
}

function openModal(src) {
  if (!src) return;
  modalReturnFocus = document.activeElement;
  $('#modal-img').src = src;
  $('#img-modal').hidden = false;
  $('#modal-close').focus();
}
function closeModal() {
  $('#img-modal').hidden = true;
  $('#modal-img').removeAttribute('src');
  if (modalReturnFocus instanceof HTMLElement) modalReturnFocus.focus();
  modalReturnFocus = null;
}

function semanticCanvasPoint(event) {
  const canvas = $('#semantic-selection-canvas');
  const rect = canvas.getBoundingClientRect();
  return {
    x: Math.max(0, Math.min(1, (event.clientX - rect.left) / Math.max(1, rect.width))),
    y: Math.max(0, Math.min(1, (event.clientY - rect.top) / Math.max(1, rect.height))),
  };
}

function drawSemanticMaskStroke(context, edit, active = false) {
  const canvas = $('#semantic-selection-canvas');
  const points = Array.from(edit?.points || []);
  if (!points.length) return;
  context.save();
  context.lineCap = 'round';
  context.lineJoin = 'round';
  context.lineWidth = Math.max(4, Number(edit.radius || 0.018) * Math.min(canvas.width, canvas.height) * 2);
  context.strokeStyle = edit.mode === 'include'
    ? active ? 'rgba(36,211,138,.86)' : 'rgba(36,190,128,.62)'
    : active ? 'rgba(255,70,70,.9)' : 'rgba(255,82,72,.66)';
  context.fillStyle = context.strokeStyle;
  if (points.length === 1) {
    context.beginPath();
    context.arc(points[0][0] * canvas.width, points[0][1] * canvas.height, context.lineWidth / 2, 0, Math.PI * 2);
    context.fill();
  } else {
    context.beginPath();
    context.moveTo(points[0][0] * canvas.width, points[0][1] * canvas.height);
    points.slice(1).forEach((point) => context.lineTo(point[0] * canvas.width, point[1] * canvas.height));
    context.stroke();
  }
  context.restore();
}

function invalidateSemanticMask(message = '选区或笔画已变化，请重新生成蒙版预览。') {
  semanticCanvasState.maskPreviewRevision += 1;
  semanticCanvasState.maskImage = null;
  semanticCanvasState.maskStatus = message;
}

function drawSemanticCanvas() {
  const canvas = $('#semantic-selection-canvas');
  const context = canvas.getContext('2d');
  context.clearRect(0, 0, canvas.width, canvas.height);
  if (semanticCanvasState.image) context.drawImage(semanticCanvasState.image, 0, 0, canvas.width, canvas.height);
  if (semanticCanvasState.maskImage) {
    context.save();
    context.globalAlpha = 0.48;
    context.drawImage(semanticCanvasState.maskImage, 0, 0, canvas.width, canvas.height);
    context.restore();
  }
  semanticCanvasState.maskEdits.forEach((edit) => drawSemanticMaskStroke(context, edit));
  if (semanticCanvasState.stroke) drawSemanticMaskStroke(context, semanticCanvasState.stroke, true);
  const outlines = [
    ...semanticCanvasState.suggestedRegions.map((region) => ({ ...region, isSuggestion: true })),
    ...semanticCanvasState.regions,
  ];
  if (semanticCanvasState.dragStart && semanticCanvasState.dragCurrent) {
    const start = semanticCanvasState.dragStart;
    const current = semanticCanvasState.dragCurrent;
    outlines.push({
      id: 'preview',
      bbox: [
        Math.min(start.x, current.x),
        Math.min(start.y, current.y),
        Math.abs(current.x - start.x),
        Math.abs(current.y - start.y),
      ],
    });
  }
  context.save();
  context.lineWidth = Math.max(2, Math.round(Math.min(canvas.width, canvas.height) / 280));
  context.font = `700 ${Math.max(14, Math.round(Math.min(canvas.width, canvas.height) / 34))}px "Segoe UI", sans-serif`;
  outlines.forEach((region) => {
    const [x, y, width, height] = region.bbox;
    const left = x * canvas.width;
    const top = y * canvas.height;
    const boxWidth = width * canvas.width;
    const boxHeight = height * canvas.height;
    const suggestion = Boolean(region.isSuggestion);
    context.setLineDash(suggestion ? [10, 7] : []);
    context.strokeStyle = region.id === 'preview' || suggestion ? '#ffd351' : '#ff6b43';
    context.fillStyle = region.id === 'preview' || suggestion
      ? 'rgba(255,211,81,.14)'
      : semanticCanvasState.maskImage
        ? 'rgba(255,107,67,0)'
        : 'rgba(255,107,67,.12)';
    if (region.id === 'preview' || suggestion || !semanticCanvasState.maskImage) {
      context.fillRect(left, top, boxWidth, boxHeight);
    }
    context.strokeRect(left, top, boxWidth, boxHeight);
    if (region.id !== 'preview') {
      const label = suggestion
        ? `待确认 · ${semanticCanvasState.query}`
        : `${semanticCanvasState.regions.indexOf(region) + 1} · ${semanticCanvasState.query}`;
      const labelWidth = context.measureText(label).width + 18;
      const labelHeight = Math.max(24, Math.round(Math.min(canvas.width, canvas.height) / 25));
      const labelTop = Math.max(0, top - labelHeight);
      context.fillStyle = suggestion ? '#c98b00' : '#ff6b43';
      context.fillRect(left, labelTop, labelWidth, labelHeight);
      context.fillStyle = '#fff';
      context.fillText(label, left + 9, labelTop + labelHeight * .7);
    }
  });
  context.setLineDash([]);
  context.restore();
}

function renderSemanticRegions() {
  const list = $('#semantic-region-list');
  const count = semanticCanvasState.regions.length;
  const suggestionCount = semanticCanvasState.suggestedRegions.length;
  const groundingStatus = $('#semantic-grounding-status');
  groundingStatus.dataset.tone = semanticCanvasState.groundingTone;
  groundingStatus.textContent = semanticCanvasState.groundingMessage;
  $('#semantic-region-count').textContent = `${count} / ${semanticCanvasState.targetCount}${suggestionCount ? ` · ${suggestionCount} 个待确认` : ''}`;
  $('#semantic-selection-summary').textContent = `要保留 ${semanticCanvasState.targetCount} 个“${semanticCanvasState.query}”`;
  $('#semantic-mask-status').textContent = semanticCanvasState.maskStatus;
  $('#semantic-brush-size-value').textContent = `${(semanticCanvasState.brushRadius * 100).toFixed(1)}%`;
  $$('[data-semantic-tool]').forEach((button) => {
    const active = button.dataset.semanticTool === semanticCanvasState.tool;
    button.classList.toggle('active', active);
    button.setAttribute('aria-pressed', String(active));
  });
  $('#semantic-canvas-hint').textContent = semanticCanvasState.tool === 'box'
    ? '拖动鼠标框选目标；每个框只包住一个物体并留少量边距'
    : semanticCanvasState.tool === 'include'
      ? '绿色保留画笔：把被误删的产品边缘补回蒙版'
      : '红色删除画笔：清除蒙版中多余背景或相邻物体';
  if (!count && !suggestionCount) {
    list.innerHTML = '<div class="semantic-region-empty">尚未框选。可在左侧拖动鼠标，或用“添加全图范围”后在这里用键盘调整坐标。</div>';
  } else {
    const coordinateLabels = ['X', 'Y', '宽', '高'];
    const selectedMarkup = semanticCanvasState.regions.map((region, index) => `
      <section class="semantic-region-item">
        <div class="semantic-region-item__head"><div><strong>目标 ${index + 1} · ${escapeHtml(semanticCanvasState.query)}</strong><small>${region.origin === 'automatic-review' ? `人工采用建议 · ${Math.round(Number(region.confidence || 0) * 100)}%` : region.origin === 'automatic' ? `可靠候选 · ${Math.round(Number(region.confidence || 0) * 100)}%` : '人工选区'}</small></div><button type="button" data-remove-semantic-region="${index}" aria-label="移除目标 ${index + 1}">移除</button></div>
        <div class="semantic-region-coordinates">
          ${region.bbox.map((value, coordinate) => `<label>${coordinateLabels[coordinate]} %<input type="number" min="0" max="100" step="0.1" value="${Number((value * 100).toFixed(1))}" data-semantic-region-index="${index}" data-semantic-coordinate="${coordinate}" /></label>`).join('')}
        </div>
      </section>
    `).join('');
    const suggestionMarkup = semanticCanvasState.suggestedRegions.map((region, index) => `
      <section class="semantic-region-item is-suggestion">
        <div class="semantic-region-item__head"><div><strong>待确认建议 · ${escapeHtml(semanticCanvasState.query)}</strong><small>模型置信度 ${Math.round(Number(region.confidence || 0) * 100)}% · 尚未选中</small></div><button type="button" data-adopt-semantic-suggestion="${index}" ${count >= semanticCanvasState.targetCount ? 'disabled' : ''}>采用</button></div>
      </section>
    `).join('');
    list.innerHTML = selectedMarkup + suggestionMarkup;
  }
  const exact = count === semanticCanvasState.targetCount;
  $('#semantic-selection-confirm').disabled = !exact;
  $('#semantic-region-error').hidden = exact || count === 0;
  $('#semantic-region-error').textContent = count > semanticCanvasState.targetCount
    ? `多选了 ${count - semanticCanvasState.targetCount} 个目标，请移除多余选区。`
    : `还需框选 ${semanticCanvasState.targetCount - count} 个目标。`;
  $('#semantic-undo').disabled = count === 0;
  $('#semantic-clear').disabled = count === 0 && suggestionCount === 0;
  $('#semantic-add-full').disabled = count >= semanticCanvasState.targetCount;
  $('#semantic-mask-preview').disabled = !exact || semanticCanvasState.maskStatus === '正在生成本地蒙版预览…';
  $('#semantic-mask-undo').disabled = semanticCanvasState.maskEdits.length === 0;
  $('#semantic-mask-clear').disabled = semanticCanvasState.maskEdits.length === 0;
  $('#semantic-mask-point-include').disabled = !exact || semanticCanvasState.maskEdits.length >= 200;
  $('#semantic-mask-point-exclude').disabled = !exact || semanticCanvasState.maskEdits.length >= 200;
  drawSemanticCanvas();
}

function addSemanticRegion(bbox) {
  if (semanticCanvasState.regions.length >= semanticCanvasState.targetCount) {
    $('#semantic-region-error').hidden = false;
    $('#semantic-region-error').textContent = `已经框选 ${semanticCanvasState.targetCount} 个目标；请先移除一个再添加。`;
    return;
  }
  const normalized = bbox.map((value) => Number(Math.max(0, Math.min(1, value)).toFixed(6)));
  if (normalized[2] < 0.01 || normalized[3] < 0.01) {
    $('#semantic-region-error').hidden = false;
    $('#semantic-region-error').textContent = '选区太小，请拖出更大的范围。';
    return;
  }
  semanticCanvasState.regions.push({
    id: `target-${semanticCanvasState.regions.length + 1}`,
    label: semanticCanvasState.query,
    origin: 'manual',
    bbox: normalized,
  });
  semanticCanvasState.groundingStatus = 'manual_regions';
  semanticCanvasState.groundingTone = 'manual';
  semanticCanvasState.groundingMessage = '已进入人工修正；请检查所有选区后确认';
  semanticCanvasState.manualRevision += 1;
  invalidateSemanticMask('目标框已变化；确认数量后可生成蒙版预览。');
  renderSemanticRegions();
}

function updateSemanticRegionCoordinate(input, render = true) {
  const index = Number(input.dataset.semanticRegionIndex);
  const coordinate = Number(input.dataset.semanticCoordinate);
  const region = semanticCanvasState.regions[index];
  if (!region || !Number.isInteger(coordinate) || coordinate < 0 || coordinate > 3) return;
  const next = [...region.bbox];
  next[coordinate] = Math.max(0, Math.min(1, Number(input.value) / 100));
  if (coordinate === 0) next[2] = Math.min(next[2], 1 - next[0]);
  if (coordinate === 1) next[3] = Math.min(next[3], 1 - next[1]);
  if (coordinate === 2) next[2] = Math.max(.01, Math.min(next[2], 1 - next[0]));
  if (coordinate === 3) next[3] = Math.max(.01, Math.min(next[3], 1 - next[1]));
  region.bbox = next.map((value) => Number(value.toFixed(6)));
  region.origin = 'manual';
  delete region.confidence;
  semanticCanvasState.groundingStatus = 'manual_regions';
  semanticCanvasState.groundingTone = 'manual';
  semanticCanvasState.groundingMessage = '已修改自动候选；请检查所有选区后确认';
  semanticCanvasState.manualRevision += 1;
  invalidateSemanticMask();
  if (render) {
    renderSemanticRegions();
  } else {
    const groundingStatus = $('#semantic-grounding-status');
    groundingStatus.dataset.tone = semanticCanvasState.groundingTone;
    groundingStatus.textContent = semanticCanvasState.groundingMessage;
    drawSemanticCanvas();
  }
}

function loadSemanticCanvasImage(url) {
  return new Promise((resolve, reject) => {
    const image = new Image();
    image.crossOrigin = 'anonymous';
    image.onload = () => {
      const scale = Math.min(1, 1600 / image.naturalWidth, 1100 / image.naturalHeight);
      const canvas = $('#semantic-selection-canvas');
      canvas.width = Math.max(1, Math.round(image.naturalWidth * scale));
      canvas.height = Math.max(1, Math.round(image.naturalHeight * scale));
      semanticCanvasState.image = image;
      drawSemanticCanvas();
      resolve();
    };
    image.onerror = () => reject(new Error('源图片预览无法加载'));
    image.src = url;
  });
}

function loadSemanticMaskImage(url) {
  return new Promise((resolve, reject) => {
    const image = new Image();
    image.onload = () => resolve(image);
    image.onerror = () => reject(new Error('蒙版预览无法加载'));
    image.src = url;
  });
}

async function previewSemanticMask() {
  if (semanticCanvasState.regions.length !== semanticCanvasState.targetCount) {
    semanticCanvasState.maskStatus = '请先完成目标框与数量确认。';
    renderSemanticRegions();
    return;
  }
  const revision = semanticCanvasState.maskPreviewRevision + 1;
  semanticCanvasState.maskPreviewRevision = revision;
  semanticCanvasState.maskStatus = '正在生成本地蒙版预览…';
  renderSemanticRegions();
  try {
    const response = await API.previewSemanticCutoutMask({
      asset_id: semanticCanvasState.assetId,
      query: semanticCanvasState.query,
      model_query: semanticCanvasState.modelQueryOverride,
      target_count: semanticCanvasState.targetCount,
      regions: semanticCanvasState.regions,
      mask_edits: semanticCanvasState.maskEdits,
    });
    if (
      semanticCanvasState.maskPreviewRevision !== revision
      || $('#semantic-selection-modal').hidden
    ) return;
    const preview = response.mask_preview || {};
    semanticCanvasState.maskImage = await loadSemanticMaskImage(preview.data_url);
    if (semanticCanvasState.maskPreviewRevision !== revision) return;
    semanticCanvasState.maskStatus = semanticCanvasState.maskEdits.length
      ? `绿色区域为最终保留蒙版 · 已应用 ${semanticCanvasState.maskEdits.length} 笔修正`
      : '绿色区域为当前保留蒙版；发现缺失或多余时可切换画笔修正。';
  } catch (error) {
    if (semanticCanvasState.maskPreviewRevision !== revision) return;
    semanticCanvasState.maskImage = null;
    semanticCanvasState.maskStatus = formatApiError(error, '本地蒙版预览失败；仍可确认后执行');
  }
  renderSemanticRegions();
}

function addSemanticMaskPoint(mode) {
  if (semanticCanvasState.regions.length !== semanticCanvasState.targetCount) {
    semanticCanvasState.maskStatus = '请先完成目标框与数量确认，再添加蒙版修正点。';
    renderSemanticRegions();
    return;
  }
  if (semanticCanvasState.maskEdits.length >= 200) {
    semanticCanvasState.maskStatus = '蒙版修正已达到 200 笔上限；请撤销或清除部分笔画。';
    renderSemanticRegions();
    return;
  }
  const x = Math.max(0, Math.min(100, Number($('#semantic-mask-point-x').value) || 0)) / 100;
  const y = Math.max(0, Math.min(100, Number($('#semantic-mask-point-y').value) || 0)) / 100;
  semanticCanvasState.maskEdits.push({
    mode,
    points: [[Number(x.toFixed(6)), Number(y.toFixed(6))]],
    radius: semanticCanvasState.brushRadius,
  });
  semanticCanvasState.manualRevision += 1;
  invalidateSemanticMask(`已通过坐标添加${mode === 'include' ? '保留' : '删除'}点；请重新生成预览复核。`);
  renderSemanticRegions();
}

async function openSemanticSelection() {
  const selection = cutoutSelectionState();
  const sourceIds = selectedAssetIds('cutout-batch');
  const readiness = semanticCutoutReadiness(selection, sourceIds);
  if (!['confirm', 'submit'].includes(readiness.action)) {
    toast(readiness.message, 'error', 4200);
    return;
  }
  const asset = selectedAssets('cutout-batch').find((item) => item.id === sourceIds[0]);
  if (!asset) {
    toast('源图片尚未恢复，请重新选择', 'error');
    return;
  }
  const button = $('#btn-semantic-preview');
  semanticReturnFocus = document.activeElement;
  button.disabled = true;
  button.setAttribute('aria-busy', 'true');
  const previewRevision = semanticCanvasState.previewRevision + 1;
  semanticCanvasState.previewRevision = previewRevision;
  semanticCanvasState.assetId = asset.id;
  semanticCanvasState.query = selection.query;
  semanticCanvasState.modelQuery = selection.model_query;
  semanticCanvasState.modelQueryOverride = selection.model_query_override;
  semanticCanvasState.targetCount = selection.target_count;
  const restoredRegions = (
    selection.status === 'confirmed'
    && Boolean(selection.digest)
    && selection.source_asset_id === asset.id
    && selection.target_count === semanticCanvasState.targetCount
    && selection.regions.length === selection.target_count
  );
  semanticCanvasState.regions = restoredRegions
    ? selection.regions.map((region) => ({ ...region, bbox: [...region.bbox] }))
    : [];
  semanticCanvasState.maskEdits = restoredRegions
    ? selection.mask_edits.map((edit) => ({
      ...edit,
      points: edit.points.map((point) => [...point]),
    }))
    : [];
  semanticCanvasState.maskImage = null;
  semanticCanvasState.maskStatus = restoredRegions && semanticCanvasState.maskEdits.length
    ? `已恢复 ${semanticCanvasState.maskEdits.length} 笔蒙版修正；可重新生成预览复核。`
    : '确认目标框后，可预览绿色蒙版并修正边缘。';
  semanticCanvasState.maskPreviewRevision += 1;
  semanticCanvasState.tool = 'box';
  semanticCanvasState.brushRadius = 0.018;
  semanticCanvasState.stroke = null;
  semanticCanvasState.suggestedRegions = [];
  semanticCanvasState.manualRevision = 0;
  semanticCanvasState.groundingStatus = restoredRegions ? 'manual_regions' : 'loading';
  semanticCanvasState.groundingTone = 'manual';
  semanticCanvasState.groundingMessage = restoredRegions
    ? '已恢复上次确认选区；可直接复核或继续修改'
    : '正在运行本地目标定位；无需等待，可直接手动框选';
  semanticCanvasState.dragStart = null;
  semanticCanvasState.dragCurrent = null;
  $('#semantic-selection-modal').hidden = false;
  try {
    await loadSemanticCanvasImage(assetUrl(asset, 'content'));
    renderSemanticRegions();
    $('#semantic-selection-close').focus();
    const preview = await API.previewSemanticCutout({
      asset_id: asset.id,
      query: selection.query,
      model_query: selection.model_query_override,
      target_count: selection.target_count,
      regions: restoredRegions ? selection.regions : [],
      mask_edits: restoredRegions ? selection.mask_edits : [],
    });
    if (
      semanticCanvasState.previewRevision !== previewRevision
      || $('#semantic-selection-modal').hidden
    ) return;
    const presentation = semanticGroundingPresentation(preview.preview);
    if (!restoredRegions && semanticCanvasState.manualRevision === 0) {
      semanticCanvasState.query = preview.preview.query;
      semanticCanvasState.modelQuery = preview.preview.model_query || '';
      semanticCanvasState.targetCount = preview.preview.target_count;
      semanticCanvasState.regions = Array.from(preview.preview.regions || [], (region) => ({
        ...region,
        bbox: [...region.bbox],
      }));
      semanticCanvasState.suggestedRegions = Array.from(
        preview.preview.suggested_regions || [],
        (region) => ({ ...region, bbox: [...region.bbox] }),
      );
      semanticCanvasState.groundingStatus = presentation.status;
      semanticCanvasState.groundingTone = presentation.tone;
      semanticCanvasState.groundingMessage = presentation.message;
    } else if (!restoredRegions) {
      semanticCanvasState.groundingStatus = 'manual_regions';
      semanticCanvasState.groundingTone = 'manual';
      semanticCanvasState.groundingMessage = '自动定位已完成，已保留你刚才的手动修改';
    }
    renderSemanticRegions();
  } catch (error) {
    if (
      semanticCanvasState.previewRevision !== previewRevision
      || $('#semantic-selection-modal').hidden
    ) return;
    const stage = error?.detail?.stage;
    semanticCanvasState.groundingStatus = 'failed';
    semanticCanvasState.groundingTone = 'error';
    semanticCanvasState.groundingMessage = stage
      ? semanticCutoutStageCopy(stage)
      : `${formatApiError(error, '自动定位暂不可用')}；仍可手动框选`;
    renderSemanticRegions();
  } finally {
    button.removeAttribute('aria-busy');
    renderCutoutControls();
  }
}

function closeSemanticSelection(restoreFocus = true) {
  const modal = $('#semantic-selection-modal');
  if (!modal) return;
  if (modal.hidden) {
    if (restoreFocus && semanticReturnFocus instanceof HTMLElement) semanticReturnFocus.focus();
    semanticReturnFocus = null;
    return;
  }
  modal.hidden = true;
  semanticCanvasState.previewRevision += 1;
  semanticCanvasState.image = null;
  semanticCanvasState.maskImage = null;
  semanticCanvasState.maskPreviewRevision += 1;
  semanticCanvasState.suggestedRegions = [];
  semanticCanvasState.stroke = null;
  semanticCanvasState.dragStart = null;
  semanticCanvasState.dragCurrent = null;
  if (restoreFocus && semanticReturnFocus instanceof HTMLElement) semanticReturnFocus.focus();
  semanticReturnFocus = null;
}

async function confirmSemanticSelection() {
  if (semanticCanvasState.regions.length !== semanticCanvasState.targetCount) return;
  const button = $('#semantic-selection-confirm');
  button.disabled = true;
  button.setAttribute('aria-busy', 'true');
  const previous = button.textContent;
  button.textContent = '正在确认';
  try {
    const response = await API.confirmSemanticCutout({
      asset_id: semanticCanvasState.assetId,
      query: semanticCanvasState.query,
      model_query: semanticCanvasState.modelQueryOverride,
      target_count: semanticCanvasState.targetCount,
      regions: semanticCanvasState.regions,
      mask_edits: semanticCanvasState.maskEdits,
    });
    const selection = response.selection;
    const sourcePlan = selection.sources[semanticCanvasState.assetId];
    setCutoutSelectionState(createSemanticCutoutState({
      strategy: 'semantic',
      query: selection.query,
      model_query: selection.model_query,
      model_query_override: semanticCanvasState.modelQueryOverride,
      target_count: selection.target_count,
      source_asset_id: semanticCanvasState.assetId,
      status: sourcePlan.status,
      method: sourcePlan.method,
      digest: sourcePlan.digest,
      regions: sourcePlan.regions,
      mask_edits: sourcePlan.mask_edits,
    }));
    closeSemanticSelection();
    updateCtaState();
    toast(`已确认 ${selection.target_count} 个“${selection.query}”${sourcePlan.mask_edits.length ? `及 ${sourcePlan.mask_edits.length} 笔蒙版修正` : ''}，现在可以开始本地抠图`, 'success', 4200);
  } catch (error) {
    const stage = error?.detail?.stage;
    $('#semantic-region-error').hidden = false;
    $('#semantic-region-error').textContent = stage
      ? semanticCutoutStageCopy(stage)
      : formatApiError(error, '目标确认失败，请重试');
  } finally {
    button.removeAttribute('aria-busy');
    button.textContent = previous;
    button.disabled = semanticCanvasState.regions.length !== semanticCanvasState.targetCount;
  }
}

function compareStateForMode(mode = state.currentMode) {
  return normalizeCompareState(
    state.modeSnapshots[mode]?.compare_state
      || state.workspaceDrafts[mode]?.compare_state
      || {},
  );
}

function updateCompareState(patch, persist = true) {
  const mode = state.currentMode;
  const snapshot = state.modeSnapshots[mode]
    || snapshotFromDraft(state.workspaceDrafts[mode] || {}, {});
  const next = normalizeCompareState({
    ...compareStateForMode(mode),
    ...(patch || {}),
  });
  state.modeSnapshots[mode] = { ...snapshot, compare_state: next };
  if (persist) scheduleWorkspaceDraftSave(mode);
  return next;
}

function resultReviewEntries() {
  return [
    ...(state.results?.main || []).map((item, index) => ({ item, index, tab: 'main' })),
    ...(state.results?.cutout || []).map((item, index) => ({ item, index, tab: 'cutout' })),
  ].map((entry, order) => ({
    ...entry,
    label: entry.item.version_label || `结果 ${order + 1}`,
  }));
}

function activeResultEntry(entries = resultReviewEntries()) {
  return entries.find((entry) => (
    entry.tab === state.resultTab && entry.index === state.viewerIndex
  )) || null;
}

function clearReviewForm() {
  state.reviewDecision = '';
  state.reviewReasonCodes = new Set();
  $('#review-reason-input').value = '';
  $('#review-reason').hidden = true;
  $('#btn-review-adjust').hidden = true;
  $$('[data-review-decision]').forEach((button) => button.classList.remove('is-selected'));
}

function renderReviewReasonTags() {
  const wrap = $('#review-reason-tags');
  const options = reviewReasonOptions(state.reviewDecision);
  wrap.innerHTML = options.map((option) => {
    const selected = state.reviewReasonCodes.has(option.code);
    return `<button type="button" data-review-reason="${escapeHtml(option.code)}" aria-pressed="${selected}">${escapeHtml(option.label)}</button>`;
  }).join('');
  $$('[data-review-reason]', wrap).forEach((button) => button.addEventListener('click', () => {
    const code = String(button.dataset.reviewReason || '');
    if (state.reviewReasonCodes.has(code)) state.reviewReasonCodes.delete(code);
    else state.reviewReasonCodes.add(code);
    button.setAttribute('aria-pressed', String(state.reviewReasonCodes.has(code)));
  }));
}

function activateReviewDecision(decision, { reasonCodes = [], note = '' } = {}) {
  const normalized = String(decision || '');
  state.reviewDecision = normalized;
  state.reviewReasonCodes = new Set(normalizeReviewReasonCodes(normalized, reasonCodes));
  $$('[data-review-decision]').forEach((button) => {
    button.classList.toggle('is-selected', button.dataset.reviewDecision === normalized);
  });
  $('#review-reason').hidden = !normalized;
  $('#btn-review-adjust').hidden = normalized !== 'adjusted';
  $('#review-reason-input').value = String(note || '');
  renderReviewReasonTags();
}

function renderReviewPanel(item) {
  const durable = reviewStateForResult(state.resultReviews, item?.asset_id || '');
  const key = activeFeedbackResultKey(item);
  if (state.reviewFormResultKey !== key) {
    clearReviewForm();
    state.reviewFormResultKey = key;
  }
  const editing = state.editingFeedbackResultKey === key;
  const summary = $('#review-summary');
  const options = $('#review-options');
  if (durable.reviewed && !editing) {
    const decisionCopy = {
      adopt: '已确认可以直接使用',
      adjust: '已记录需要调整',
      reject: '已记录整体方向不对',
    }[durable.decision] || '本版本已完成评审';
    summary.hidden = false;
    options.hidden = true;
    $('#review-reason').hidden = true;
    $('#review-summary-title').textContent = decisionCopy;
    $('#review-summary-copy').textContent = feedbackReceiptCopy(durable.receipt);
    const codes = Array.isArray(durable.review?.reason_codes) ? durable.review.reason_codes : [];
    $('#review-summary-tags').innerHTML = codes
      .map((code) => `<span>${escapeHtml(reviewReasonLabel(code))}</span>`)
      .join('');
    const suggestion = $('#btn-review-summary-suggestion');
    suggestion.hidden = !durable.receipt.suggestionId;
    suggestion.dataset.suggestionId = durable.receipt.suggestionId || '';
    clearReviewForm();
  } else {
    summary.hidden = true;
    options.hidden = false;
    if (editing && durable.reviewed && !state.reviewDecision) {
      const signal = durable.decision === 'adopt'
        ? 'adopted' : durable.decision === 'reject' ? 'rejected' : 'adjusted';
      activateReviewDecision(signal, {
        reasonCodes: durable.review?.reason_codes || [],
        note: durable.review?.note || '',
      });
    }
  }
}

function setReviewGuideOpen(open, { focus = false, restore = false } = {}) {
  const guide = $('#review-guide');
  const help = $('#btn-compare-help');
  if (!guide || !help) return;
  guide.hidden = !open;
  help.setAttribute('aria-expanded', String(open));
  if (open && focus) $('#btn-review-guide-done')?.focus();
  if (!open && restore) {
    const target = reviewGuideReturnFocus instanceof HTMLElement ? reviewGuideReturnFocus : help;
    target.focus();
  }
  if (!open) reviewGuideReturnFocus = null;
}

function renderCompare() {
  const rail = $('#review-version-rail');
  const entries = resultReviewEntries();
  const active = activeResultEntry(entries);
  rail.innerHTML = entries.length ? entries.map((entry) => {
    const selected = entry === active;
    const status = selected ? 'A · 当前' : (entry.item.is_parent_version ? '上一版' : '可对比');
    return `<button class="review-version ${selected ? 'is-selected' : ''}" type="button" data-review-tab="${entry.tab}" data-review-index="${entry.index}" aria-pressed="${selected}"><img src="${escapeHtml(resultDataUrl(entry.item, entry.tab))}" alt="${escapeHtml(entry.label)}" /><strong>${escapeHtml(entry.label)}</strong><small>${status}</small></button>`;
  }).join('') : statusPanelHtml('empty', { title: '等待结果版本', detail: '生成完成后可在这里选择 A。', compact: true });
  $$('[data-review-tab]', rail).forEach((button) => button.addEventListener('click', () => {
    state.editingFeedbackResultKey = '';
    selectResultVersion(Number(button.dataset.reviewIndex || 0), button.dataset.reviewTab);
    state.reviewReasonCodes = new Set();
    state.reviewDecision = '';
    renderCompare();
  }));

  const currentCompare = compareStateForMode();
  const activeItem = active?.item || null;
  const otherItems = entries.map((entry) => entry.item);
  let referenceItem = comparisonTargetForItems(
    otherItems,
    activeItem?.asset_id,
    currentCompare.secondary_result_asset_id,
  );
  if (!state.originalDataUrl && !referenceItem) {
    referenceItem = otherItems.find((item) => String(item.asset_id) !== String(activeItem?.asset_id)) || null;
  }
  const referenceUrl = referenceItem
    ? resultDataUrl(referenceItem, entries.find((entry) => entry.item === referenceItem)?.tab)
    : state.originalDataUrl;
  const activeUrl = activeItem ? resultDataUrl(activeItem, active.tab) : '';
  const has = Boolean(referenceUrl && activeUrl);
  state.compareData = has ? { original: referenceUrl, result: activeUrl } : null;

  const target = $('#compare-target');
  const targetOptions = [
    ...(state.originalDataUrl ? [{ value: 'source', label: '原图' }] : []),
    ...entries.filter((entry) => entry !== active).map((entry) => ({
      value: entry.item.asset_id,
      label: entry.label,
    })),
  ];
  target.innerHTML = targetOptions.map((option) => (
    `<option value="${escapeHtml(option.value)}">${escapeHtml(option.label)}</option>`
  )).join('');
  target.disabled = targetOptions.length < 2;
  target.value = referenceItem?.asset_id || 'source';
  $('.review-compare-toolbar').hidden = !has;
  $('#compare-empty').hidden = has;
  $('#compare-view').hidden = !has;
  setReviewGuideOpen(has && !currentCompare.guide_dismissed);
  if (!has) {
    $('#compare-view').classList.remove('is-side-by-side', 'is-zoomed', 'is-panning');
    $('#compare-slider').hidden = false;
    $('#compare-mode-note').hidden = true;
    renderReviewPanel(activeItem);
    return;
  }

  const original = $('#compare-img-original');
  const result = $('#compare-img-result');
  const activeLabel = active?.label || '当前版本';
  const referenceLabel = referenceItem
    ? (entries.find((entry) => entry.item === referenceItem)?.label || '另一版本')
    : '原图';
  $('#compare-label-before').textContent = `B · ${referenceLabel}`;
  $('#compare-label-after').textContent = `A · ${activeLabel}`;
  original.alt = `对比对象 B：${referenceLabel}`;
  result.alt = `当前版本 A：${activeLabel}`;
  const sync = () => syncComparePresentation(original, result);
  original.onload = sync;
  result.onload = sync;
  original.src = referenceUrl;
  result.src = activeUrl;
  if (original.complete && result.complete) sync();
  setComparePosition(currentCompare.divider, false);
  setCompareTransform(currentCompare, false);
  renderReviewPanel(activeItem);
}

function syncComparePresentation(original, result) {
  if (!original?.naturalWidth || !result?.naturalWidth) return;
  const presentation = comparisonPresentation(
    { width: original.naturalWidth, height: original.naturalHeight },
    { width: result.naturalWidth, height: result.naturalHeight },
  );
  const sideBySide = presentation === 'side-by-side';
  $('#compare-view').classList.toggle('is-side-by-side', sideBySide);
  $('#compare-slider').hidden = sideBySide;
  $('#compare-mode-note').hidden = !sideBySide;
  $('#compare-view').dataset.presentation = presentation;
}

function setComparePosition(percent, persist = true) {
  const value = Math.max(3, Math.min(97, percent));
  const view = $('#compare-view');
  view.style.setProperty('--compare-slide', `${value}%`);
  $('#compare-slider').setAttribute('aria-valuenow', String(Math.round(value)));
  if (persist) updateCompareState({ divider: value });
}

function setCompareTransform(compareState, persist = true) {
  const normalized = normalizeCompareState(compareState);
  const view = $('#compare-view');
  view.style.setProperty('--compare-zoom', String(normalized.zoom));
  view.style.setProperty('--compare-pan-x', `${normalized.pan_x}%`);
  view.style.setProperty('--compare-pan-y', `${normalized.pan_y}%`);
  view.classList.toggle('is-zoomed', normalized.zoom > 1);
  $('#compare-zoom-value').textContent = `${Math.round(normalized.zoom * 100)}%`;
  if (persist) updateCompareState(normalized);
}

function setupCompare() {
  const view = $('#compare-view');
  const slider = $('#compare-slider');
  const guide = $('#review-guide');
  const help = $('#btn-compare-help');
  guide.setAttribute('role', 'dialog');
  guide.setAttribute('aria-modal', 'false');
  help.setAttribute('aria-controls', 'review-guide');
  help.setAttribute('aria-expanded', String(!guide.hidden));
  let sliderDragging = false;
  let panDrag = null;
  const moveSlider = (clientX) => {
    if (view.classList.contains('is-side-by-side')) return;
    const rect = view.getBoundingClientRect();
    if (rect.width) setComparePosition(((clientX - rect.left) / rect.width) * 100);
  };
  slider.addEventListener('pointerdown', (event) => {
    sliderDragging = true;
    slider.setPointerCapture(event.pointerId);
    moveSlider(event.clientX);
  });
  slider.addEventListener('pointermove', (event) => { if (sliderDragging) moveSlider(event.clientX); });
  slider.addEventListener('pointerup', () => { sliderDragging = false; });
  slider.addEventListener('keydown', (event) => {
    if (view.classList.contains('is-side-by-side')) return;
    const current = Number(slider.getAttribute('aria-valuenow') || 50);
    const delta = event.shiftKey ? 10 : 2;
    if (event.key === 'ArrowLeft') { event.preventDefault(); setComparePosition(current - delta); }
    if (event.key === 'ArrowRight') { event.preventDefault(); setComparePosition(current + delta); }
    if (event.key === 'Home') { event.preventDefault(); setComparePosition(3); }
    if (event.key === 'End') { event.preventDefault(); setComparePosition(97); }
  });
  view.addEventListener('pointerdown', (event) => {
    if (event.target.closest('#compare-slider') || compareStateForMode().zoom <= 1) return;
    const current = compareStateForMode();
    panDrag = {
      pointerId: event.pointerId,
      x: event.clientX,
      y: event.clientY,
      panX: current.pan_x,
      panY: current.pan_y,
      active: false,
    };
    view.setPointerCapture(event.pointerId);
  });
  view.addEventListener('pointermove', (event) => {
    if (!panDrag || panDrag.pointerId !== event.pointerId) return;
    const rect = view.getBoundingClientRect();
    if (!rect.width || !rect.height) return;
    const deltaX = event.clientX - panDrag.x;
    const deltaY = event.clientY - panDrag.y;
    if (!panDrag.active && Math.hypot(deltaX, deltaY) < 4) return;
    if (!panDrag.active) {
      panDrag.active = true;
      view.classList.add('is-panning');
    }
    setCompareTransform({
      ...compareStateForMode(),
      pan_x: panDrag.panX + (deltaX / rect.width) * 100,
      pan_y: panDrag.panY + (deltaY / rect.height) * 100,
    });
  });
  const endPan = (event) => {
    if (!panDrag || panDrag.pointerId !== event.pointerId) return;
    panDrag = null;
    view.classList.remove('is-panning');
  };
  view.addEventListener('pointerup', endPan);
  view.addEventListener('pointercancel', endPan);
}

function formatTime(value) {
  if (!value) return '—';
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? String(value) : date.toLocaleString('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' });
}

function sessionProjectName(session) {
  return String(session?.project_name || '').trim() || '未归类项目';
}

function sessionStatusCopy(status) {
  return ({ completed: '已完成', partial: '部分完成', processing: '处理中', draft: '草稿', failed: '需要处理', canceled: '已取消' })[status] || '已保存';
}

function sessionActionCopy(session) {
  if (['failed', 'partial'].includes(session.status)) return '处理';
  if (session.status === 'completed') return '查看';
  return '继续';
}

function sessionJob(sessionId) {
  const matches = state.jobs.filter((job) => String(job.session_id || '') === String(sessionId || ''));
  return matches.find((job) => resultIdsForJob(job).length) || matches[0] || null;
}

async function openSessionFromHistory(sessionId) {
  try {
    const session = await API.getSession(sessionId);
    const job = sessionJob(sessionId);
    if (job) {
      if (resultIdsForJob(job).length) await openJobResults(job.id);
      else await openJobWorkspace(job);
      return;
    }
    const generations = session.generations || [];
    const generation = generations[generations.length - 1];
    state.currentSessionId = session.id || '';
    applyTaskKnowledgeBundle(knowledgeBundleFromEvidence({
      brief: session.brief || {},
      generation,
    }));
    openDrawer('intelligence');
    toast('这条旧会话没有任务快照，已打开可追溯证据');
  } catch (error) {
    toast(`无法恢复会话：${formatApiError(error, '本地创作账本暂不可用')}`, 'error');
  }
}

function renderSessionTimeline(session) {
  const steps = $$('#history-timeline span');
  const hasAssets = Number(session?.asset_count || 0) > 0;
  const hasDirection = Boolean(session?.brief?.objective || session?.brief?.user_request);
  const hasReview = Number(session?.feedback_count || 0) > 0;
  const hasKnowledge = state.sessionPendingKnowledgeCount > 0;
  const states = [hasAssets, hasDirection, hasReview, hasKnowledge];
  const current = states.findIndex((done) => !done);
  steps.forEach((step, index) => {
    step.classList.toggle('is-done', states[index]);
    step.classList.toggle('is-current', index === current || (current < 0 && index === steps.length - 1));
  });
  $('#history-memory-copy').textContent = session
    ? `${MODE_CONFIG[session.mode]?.label || '创作工作流'}已保留素材、参数、任务和评审证据。`
    : '素材、参数、任务和评审证据相互独立，随时可以继续。';
}

function renderSessionsDashboard() {
  const grid = $('#history-grid');
  const recentCard = $('#sessions-recent-card');
  const toggle = $('#btn-toggle-history');
  const filter = state.sessionProjectFilter;
  const sessions = filter === 'all'
    ? state.sessions
    : state.sessions.filter((session) => sessionProjectName(session) === filter);
  const projectTitle = filter === 'all' ? '全部创作项目' : filter;
  $('#history-project-title').textContent = projectTitle;
  $('#history-session-count').textContent = String(sessions.length);
  $('#history-result-count').textContent = String(sessions.reduce((sum, session) => sum + Number(session.generation_count || 0), 0));
  $('#history-pending-count').textContent = String(state.sessionPendingKnowledgeCount);
  const completed = sessions.filter((session) => session.status === 'completed').length;
  $('#history-complete-rate').textContent = sessions.length ? `${Math.round((completed / sessions.length) * 100)}%` : '0%';
  renderSessionTimeline(sessions[0] || null);
  const canExpand = sessions.length > 6;
  if (!canExpand) state.sessionShowAll = false;
  recentCard.classList.toggle('is-expanded', state.sessionShowAll);
  toggle.hidden = !canExpand;
  toggle.textContent = state.sessionShowAll ? '收起列表' : `查看全部 ${sessions.length} 个`;
  toggle.setAttribute('aria-expanded', String(state.sessionShowAll));
  if (!sessions.length) {
    grid.innerHTML = statusPanelHtml('empty', { title: '还没有创作现场', detail: '完成第一项任务后，现场会自动保存在这里。', fill: true });
    return;
  }
  const visibleSessions = state.sessionShowAll ? sessions : sessions.slice(0, 6);
  grid.innerHTML = visibleSessions.map((session) => {
    const job = sessionJob(session.id);
    const counts = job ? jobCounts(job) : null;
    const progress = counts ? `${counts.completed}/${counts.total} 项完成` : `${Number(session.generation_count || 0)} 个版本`;
    const summary = `${MODE_CONFIG[session.mode]?.label || '创作任务'} · ${progress} · ${sessionStatusCopy(session.status)}`;
    return `<button class="session-card" type="button" data-session-id="${escapeHtml(session.id)}"><span class="session-card__color" aria-hidden="true"></span><span class="session-card__copy"><strong>${escapeHtml(session.title || sessionProjectName(session))}</strong><small>${escapeHtml(summary)} · ${escapeHtml(formatTime(session.updated_at))}</small></span><span class="session-card__action">${sessionActionCopy(session)}</span><span class="session-card__chevron" aria-hidden="true">›</span></button>`;
  }).join('');
  $$('.session-card', grid).forEach((card) => card.addEventListener('click', () => openSessionFromHistory(card.dataset.sessionId)));
}

async function loadSessions() {
  const grid = $('#history-grid');
  grid.innerHTML = statusPanelHtml('loading', { title: '正在读取创作账本', detail: '正在恢复最近项目与任务现场。', fill: true });
  try {
    const [sessions, suggestions] = await Promise.all([
      API.getSessions(60),
      API.getMemorySuggestions('pending').catch(() => []),
    ]);
    state.sessions = Array.isArray(sessions) ? sessions : [];
    state.sessionPendingKnowledgeCount = Array.isArray(suggestions) ? suggestions.length : 0;
    const projectFilter = $('#history-project-filter');
    const projects = [...new Set(state.sessions.map(sessionProjectName))];
    const available = state.sessionProjectFilter === 'all' || projects.includes(state.sessionProjectFilter);
    if (!available) state.sessionProjectFilter = 'all';
    projectFilter.innerHTML = `<option value="all">全部项目</option>${projects.map((name) => `<option value="${escapeHtml(name)}">${escapeHtml(name)}</option>`).join('')}`;
    projectFilter.value = state.sessionProjectFilter;
    renderSessionsDashboard();
  } catch (error) {
    grid.innerHTML = statusPanelHtml('offline', {
      title: '创作账本暂时离线',
      detail: formatApiError(error, '本地创作账本暂不可用'),
      fill: true,
      action: { label: '重新读取', attribute: 'data-history-status-action', value: 'retry' },
    });
    $('[data-history-status-action="retry"]', grid)?.addEventListener('click', loadSessions);
  }
}

function selectMemoryNode(node) {
  if (!node) return;
  $$('[data-memory-node]').forEach((item) => item.classList.toggle('is-selected', item === node));
  const caption = $('#memory-dna-caption');
  $('strong', caption).textContent = node.dataset.memoryNode || '设计判断';
  $('small', caption).textContent = node.dataset.memoryDetail || '所有关系都来自真实账本和唯一知识库。';
}

function replayMemoryMotion() {
  const panel = $('#memory-dna-panel');
  const trace = $('#memory-trace');
  [panel, trace].forEach((element) => {
    element.classList.remove('is-replaying');
    void element.offsetWidth;
    element.classList.add('is-replaying');
  });
  window.setTimeout(() => {
    panel.classList.remove('is-replaying');
    trace.classList.remove('is-replaying');
  }, window.matchMedia('(prefers-reduced-motion: reduce)').matches ? 0 : 900);
  toast('正在回放：正式知识 → 创作现场 → 终稿反馈 → 待审核建议', 'success');
}

function renderMemoryProjection(ledger, suggestions, knowledgeStatus) {
  const counts = ledger.counts || {};
  const sessions = Number(counts.sessions || 0);
  const feedback = Number(counts.feedback || 0);
  const pending = Number(ledger.pending_memory || suggestions.length || 0);
  const documents = Number(knowledgeStatus?.document_count || 0);
  const knowledgeRules = Number(knowledgeStatus?.rule_count || 0);
  const bundle = state.knowledgeBundle || {};
  const currentItem = getResultItems()[state.viewerIndex] || null;
  const currentProjection = memoryProjectionState({
    currentTaskId: state.currentTaskId,
    knowledgeBundle: bundle,
    reviews: state.resultReviews,
    resultAssetId: currentItem?.asset_id || '',
  });
  const sources = Array.isArray(bundle.sources) ? bundle.sources : [];
  const executionRules = (Array.isArray(bundle.positive_rules) ? bundle.positive_rules.length : 0)
    + (Array.isArray(bundle.negative_rules) ? bundle.negative_rules.length : 0);
  const brief = bundle.creative_brief || {};
  const intent = brief.objective || brief.user_request || $('#brief-input').value.trim();
  const memorySources = sources.filter((source) => source.relative_path === '记忆反馈/已批准');
  const appliedRuleTexts = [
    ...(Array.isArray(bundle.positive_rules) ? bundle.positive_rules : []).map((rule) => rule.text || rule),
    ...(Array.isArray(bundle.negative_rules) ? bundle.negative_rules : []).map((rule) => rule.text || rule),
    ...(Array.isArray(bundle.intent_lock_rules) ? bundle.intent_lock_rules : []),
  ].filter(Boolean);

  $('#memory-session-count').textContent = sessions;
  $('#memory-feedback-count').textContent = feedback;
  $('#memory-pending-count').textContent = pending;
  const pendingNode = $('[data-memory-node="待审核建议"]');
  pendingNode?.classList.toggle('memory-dna-node--pending', pending > 0);
  $('#memory-rule-count').textContent = knowledgeStatus?.available === false
    ? '主库暂不可用'
    : `${documents} 份文档 · ${knowledgeRules} 条规则`;
  $('#memory-core-summary').textContent = `${documents} 份正式知识 · ${sessions} 个现场`;
  $('#memory-trace-title').textContent = currentProjection.title;
  $('#btn-open-memory-trace').disabled = !currentProjection.hasTask;
  $('#memory-trace-intent').textContent = currentProjection.hasTask
    ? intent || '当前任务尚未输入创作目标'
    : '当前未选择任务；全局现场数量不会被伪装成当前步骤';
  $('#memory-trace-knowledge').textContent = currentProjection.hasTask
    ? sources.length
      ? memorySources.length
        ? `本次引用 ${sources.length} 份依据，其中 ${memorySources.length} 条是你已批准的反馈`
        : `本次引用 ${sources.length} 份正式知识`
      : '当前任务尚未编译知识来源'
    : `${documents} 份正式知识可用；选择任务后显示实际引用`;
  $('#memory-trace-rules').textContent = currentProjection.hasTask
    ? executionRules
      ? `已应用 ${executionRules} 条可检查执行规则：${appliedRuleTexts.slice(0, 3).join('；')}${appliedRuleTexts.length > 3 ? '…' : ''}`
      : '只采用已批准规则，不使用待审核建议'
    : '未选择任务，不展示最后一次前端知识包';
  $('#memory-trace-feedback-title').textContent = currentProjection.hasTask
    ? currentProjection.title
    : '当前未选择任务';
  $('#memory-trace-feedback').textContent = currentProjection.detail;

  const traceSteps = $$('#memory-trace li');
  if (!currentProjection.hasTask) {
    traceSteps.forEach((step) => step.classList.remove('is-complete', 'is-current'));
  } else {
    const done = [Boolean(intent), sources.length > 0, executionRules > 0, currentProjection.reviewed];
    const currentIndex = done.findIndex((value) => !value);
    traceSteps.forEach((step, index) => {
      step.classList.toggle('is-complete', done[index]);
      step.classList.toggle('is-current', currentIndex === index);
    });
  }

  const details = {
    设计判断: `${documents} 份正式知识、${sessions} 个创作现场和 ${feedback} 条反馈共同构成当前投影。`,
    正式知识: `唯一主库当前只读加载 ${documents} 份文档、${knowledgeRules} 条规则；正式页面不会被后台修改。`,
    创作现场: `${sessions} 个会话保留各自素材、参数、知识引用与结果版本。`,
    终稿反馈: `${feedback} 条有效反馈作为学习证据，不会直接覆盖正式知识。`,
    待审核建议: `${pending} 条建议等待人工确认；未批准前不参与未来生成。`,
  };
  $$('[data-memory-node]').forEach((node) => { node.dataset.memoryDetail = details[node.dataset.memoryNode] || ''; });
  selectMemoryNode($('.memory-dna-node.is-selected') || $('[data-memory-node]'));
}

async function openMemorySourceResult(source, button) {
  if (!source?.job_id || !source?.result_asset_id || button?.disabled) return;
  if (button) {
    button.disabled = true;
    button.setAttribute('aria-busy', 'true');
  }
  try {
    await openJobResults(source.job_id);
    const location = locateResultVersion(state.results, source.result_asset_id);
    if (!location) throw new Error('来源结果版本已不在该任务中');
    selectResultVersion(location.index, location.tab);
    await settleWorkspaceDraft(state.currentMode);
    toast('已打开形成这条建议的精确结果版本', 'success');
    window.setTimeout(() => $('#btn-open-compare')?.focus(), 0);
  } catch (error) {
    toast(`无法打开来源结果：${formatApiError(error, '历史任务暂不可用')}`, 'error', 6000);
    if (button) {
      button.disabled = false;
      button.setAttribute('aria-busy', 'false');
    }
  }
}

function openMemorySuggestion(suggestionId) {
  const target = String(suggestionId || '').trim();
  if (!target) return;
  state.memoryTargetSuggestionId = target;
  state.memoryExpandedIds.add(target);
  switchPage('memory');
}

function updateMemoryFilterControls() {
  const suggestions = Array.isArray(state.memorySuggestions) ? state.memorySuggestions : [];
  const counts = {
    pending: suggestions.filter((item) => item.status === 'pending').length,
    approved: suggestions.filter((item) => item.status === 'approved').length,
    disabled: suggestions.filter((item) => item.status === 'disabled').length,
    all: suggestions.length,
  };
  $$('[data-memory-filter]').forEach((button) => {
    const active = button.dataset.memoryFilter === state.memoryFilter;
    button.classList.toggle('is-active', active);
    button.setAttribute('aria-selected', active ? 'true' : 'false');
    button.tabIndex = active ? 0 : -1;
  });
  Object.entries(counts).forEach(([filter, count]) => {
    const target = $(`[data-memory-filter-count="${filter}"]`);
    if (target) target.textContent = String(count);
  });
  const pendingNode = $('[data-memory-node="待审核建议"]');
  $('#memory-pending-count').textContent = String(counts.pending);
  pendingNode?.classList.toggle('memory-dna-node--pending', counts.pending > 0);
  if (pendingNode) {
    pendingNode.dataset.memoryDetail = `${counts.pending} 条建议等待人工确认；未批准前不参与未来生成。`;
    if (pendingNode.classList.contains('is-selected')) selectMemoryNode(pendingNode);
  }
}

function memoryQueueEmptyCopy(filter) {
  return {
    pending: ['暂无待审核建议', '继续完成结果评审后，重复模式会在这里出现。'],
    approved: ['暂无已采用规则', '只有你亲自采用的建议，才会介入之后的匹配任务。'],
    disabled: ['暂无已停用规则', '停用后不再介入新任务，历史证据仍然保留。'],
    all: ['暂无知识建议', '终稿反馈会先沉淀成可审核建议，不会直接修改正式知识。'],
  }[filter] || ['暂无内容', '请稍后刷新。'];
}

function memoryHistoryLabel(entry) {
  const actions = {
    created: '建立建议', evidence_refresh: '证据更新', edit: '编辑', approve: '采用',
    reject: '拒绝', postpone: '稍后处理', disable: '停用', enable: '重新启用',
    reopen: '重新审核', undo: '撤销', redo: '恢复撤销', dismiss: '系统撤回',
  };
  return actions[String(entry?.action || '')] || '版本变更';
}

function memoryCardMarkup(item, targetId = '') {
  const id = String(item.id || '');
  const key = id.replace(/[^a-zA-Z0-9_-]/g, '-');
  const proposed = item.proposed_value && typeof item.proposed_value === 'object'
    ? item.proposed_value : {};
  const view = memoryGovernancePresentation(item);
  const governance = item.governance && typeof item.governance === 'object'
    ? item.governance : {};
  const expanded = state.memoryExpandedIds.has(id) || id === targetId;
  const editing = state.memoryEditingIds.has(id);
  const busy = state.memoryMutationsInFlight.has(id);
  const evidence = Array.isArray(item.evidence) ? item.evidence : [];
  const sources = Array.isArray(item.source_results) ? item.source_results : [];
  const sourceByResult = new Map(sources.map((source) => [String(source.result_asset_id || ''), source]));
  const support = Number(proposed.distinct_sessions || evidence.length || 0);
  const threshold = Number(proposed.min_support || (String(item.rule_key || '').startsWith('feedback.recurring.') ? 3 : 2));
  const contradictions = Array.isArray(proposed.contradiction_examples)
    ? proposed.contradiction_examples.filter(Boolean) : [];
  const impact = item.status === 'approved'
    ? `将介入之后的${view.scopeLabel}匹配任务，不改写旧任务。`
    : item.status === 'disabled'
      ? '已停止介入新任务；旧任务与证据保持不变。'
      : '审核前不会介入任何生成任务。';
  const evidenceMarkup = evidence.length
    ? `<ol class="memory-evidence-list">${evidence.map((entry, index) => {
      const source = sourceByResult.get(String(entry.result_asset_id || '')) || {};
      const canOpen = Boolean((source.job_id || entry.job_id) && (source.result_asset_id || entry.result_asset_id));
      return `<li><div><strong>来源评审 ${index + 1}</strong><p>${escapeHtml(entry.reason || '未填写补充说明')}</p></div>${canOpen ? `<button type="button" data-memory-source data-job-id="${escapeHtml(source.job_id || entry.job_id)}" data-result-id="${escapeHtml(source.result_asset_id || entry.result_asset_id)}">打开精确结果</button>` : '<span>旧记录无结果游标</span>'}</li>`;
    }).join('')}</ol>`
    : '<p class="memory-detail-empty">没有可展开的原始评审文本。</p>';
  const contradictionMarkup = contradictions.length
    ? `<ul class="memory-contradiction-list">${contradictions.map((text) => `<li>${escapeHtml(text)}</li>`).join('')}</ul>`
    : '<p class="memory-detail-empty">当前没有相反反馈。</p>';
  const history = Array.isArray(governance.history) ? [...governance.history].reverse().slice(0, 6) : [];
  const historyMarkup = history.length
    ? `<ol class="memory-history-list">${history.map((entry) => `<li><strong>v${escapeHtml(entry.revision || '?')} · ${escapeHtml(memoryHistoryLabel(entry))}</strong><span>${escapeHtml(entry.directive || entry.label || '状态变更')}</span></li>`).join('')}</ol>`
    : '<p class="memory-detail-empty">当前是首个版本。</p>';
  const editMarkup = editing ? `<form class="memory-edit-form" data-memory-edit-form novalidate>
    <label for="memory-label-${key}">建议名称 <span>1–80 字</span></label>
    <input id="memory-label-${key}" name="label" maxlength="80" value="${escapeHtml(proposed.label || '')}" aria-describedby="memory-edit-error-${key}" />
    <label for="memory-directive-${key}">任务指令 <span>1–600 字</span></label>
    <textarea id="memory-directive-${key}" name="directive" maxlength="600" rows="4" aria-describedby="memory-edit-error-${key}">${escapeHtml(proposed.directive || '')}</textarea>
    <p class="memory-edit-error" id="memory-edit-error-${key}" role="alert"></p>
    <div><button class="memory-action memory-action--primary" type="submit">保存新版本</button><button class="memory-action" type="button" data-memory-edit-cancel>取消</button></div>
  </form>` : '';
  const detailMarkup = expanded ? `<div class="memory-item__details">
    ${editMarkup}
    <section><h4>原始反馈与来源结果</h4>${evidenceMarkup}</section>
    <section><h4>反例</h4>${contradictionMarkup}</section>
    <section><h4>版本记录</h4>${historyMarkup}</section>
  </div>` : '';
  const actions = view.actions.map((action) => `<button class="memory-action memory-action--${action.tone}" type="button" data-memory-action="${action.action}"${action.confirm ? ' data-memory-confirm="true"' : ''}${busy ? ' disabled' : ''}>${escapeHtml(action.label)}</button>`).join('');
  return `<article class="memory-item ${id === targetId ? 'is-target' : ''}" data-id="${escapeHtml(id)}" tabindex="-1" aria-busy="${busy ? 'true' : 'false'}">
    <div class="memory-item__head"><div class="memory-item__status"><span data-status="${escapeHtml(view.status)}">${escapeHtml(view.statusLabel)}</span><span>${escapeHtml(view.scopeLabel)}</span><span>v${view.revision}</span>${governance.postponed_at ? '<span>已标记稍后</span>' : ''}</div><strong>${Math.round(Number(item.confidence || 0) * 100)}% 置信度</strong></div>
    <h3>${escapeHtml(proposed.label || item.rule_key || '新偏好建议')}</h3>
    <p class="memory-item__directive">${escapeHtml(proposed.directive || JSON.stringify(proposed))}</p>
    <div class="memory-facts" aria-label="建议证据摘要"><span><b>${support}/${threshold}</b>独立会话 / 阈值</span><span><b>${Number(proposed.support_count || support)}</b>条支持证据</span><span><b>${Number(proposed.contradiction_count || 0)}</b>条反例</span></div>
    <p class="memory-impact"><strong>作用范围：</strong>${escapeHtml(impact)}</p>
    ${detailMarkup}
    <footer class="memory-item__footer"><button class="memory-details-toggle" type="button" data-memory-expand aria-expanded="${expanded ? 'true' : 'false'}">${expanded ? '收起证据' : '查看证据与版本'}</button><div class="memory-actions">${actions || '<span>当前无可用操作</span>'}</div></footer>
  </article>`;
}

function replaceMemorySuggestion(next) {
  const index = state.memorySuggestions.findIndex((item) => String(item.id) === String(next?.id));
  if (index >= 0) state.memorySuggestions.splice(index, 1, next);
  else if (next?.id) state.memorySuggestions.unshift(next);
}

function renderMemoryQueue(targetId = '') {
  const list = $('#memory-list');
  updateMemoryFilterControls();
  const suggestions = memorySuggestionsForFilter(state.memorySuggestions, state.memoryFilter)
    .sort((left, right) => Number(Boolean(left.governance?.postponed_at)) - Number(Boolean(right.governance?.postponed_at)));
  if (!suggestions.length) {
    const [title, detail] = memoryQueueEmptyCopy(state.memoryFilter);
    list.innerHTML = statusPanelHtml('empty', { title, detail, fill: true });
    return;
  }
  list.innerHTML = suggestions.map((item) => memoryCardMarkup(item, targetId)).join('');
  $$('[data-memory-source]', list).forEach((button) => button.addEventListener('click', () => {
    openMemorySourceResult({
      job_id: button.dataset.jobId,
      result_asset_id: button.dataset.resultId,
    }, button);
  }));
  $$('[data-memory-expand]', list).forEach((button) => button.addEventListener('click', () => {
    const id = String(button.closest('.memory-item')?.dataset.id || '');
    if (state.memoryExpandedIds.has(id)) {
      state.memoryExpandedIds.delete(id);
      state.memoryEditingIds.delete(id);
    } else state.memoryExpandedIds.add(id);
    renderMemoryQueue(id);
    window.setTimeout(() => $(`.memory-item[data-id="${CSS.escape(id)}"]`)?.focus({ preventScroll: true }), 0);
  }));
  $$('[data-memory-action]', list).forEach((button) => button.addEventListener('click', () => {
    const id = String(button.closest('.memory-item')?.dataset.id || '');
    if (button.dataset.memoryAction === 'edit') {
      state.memoryExpandedIds.add(id);
      state.memoryEditingIds.add(id);
      renderMemoryQueue(id);
      window.setTimeout(() => $(`#memory-label-${CSS.escape(id.replace(/[^a-zA-Z0-9_-]/g, '-'))}`)?.focus(), 0);
      return;
    }
    performMemoryGovernanceAction(id, button.dataset.memoryAction, button);
  }));
  $$('[data-memory-edit-cancel]', list).forEach((button) => button.addEventListener('click', () => {
    const id = String(button.closest('.memory-item')?.dataset.id || '');
    state.memoryEditingIds.delete(id);
    renderMemoryQueue(id);
  }));
  $$('[data-memory-edit-form]', list).forEach((form) => form.addEventListener('submit', (event) => {
    event.preventDefault();
    const item = form.closest('.memory-item');
    const label = String(form.elements.label?.value || '').trim();
    const directive = String(form.elements.directive?.value || '').trim();
    const error = $('.memory-edit-error', form);
    if (!label || label.length > 80) {
      error.textContent = '建议名称需要 1–80 个字符。';
      form.elements.label?.focus();
      return;
    }
    if (!directive || directive.length > 600) {
      error.textContent = '任务指令需要 1–600 个字符。';
      form.elements.directive?.focus();
      return;
    }
    error.textContent = '';
    performMemoryGovernanceAction(item.dataset.id, 'edit', $('button[type="submit"]', form), { label, directive });
  }));
  if (targetId) {
    window.setTimeout(() => {
      const target = $(`.memory-item[data-id="${CSS.escape(targetId)}"]`, list);
      target?.scrollIntoView({ block: 'nearest', behavior: window.matchMedia('(prefers-reduced-motion: reduce)').matches ? 'auto' : 'smooth' });
      target?.focus({ preventScroll: true });
    }, 0);
  }
}

async function performMemoryGovernanceAction(id, action, button, fields = {}) {
  const item = state.memorySuggestions.find((candidate) => String(candidate.id) === String(id));
  const card = button?.closest('.memory-item');
  if (!item || state.memoryMutationsInFlight.has(id) || card?.getAttribute('aria-busy') === 'true') return;
  if (button?.dataset.memoryConfirm === 'true') {
    const confirmed = window.confirm(action === 'disable'
      ? '停用后，这条规则不再介入新任务，但可重新启用。继续吗？'
      : '拒绝后会保留证据和历史，也可重新审核。继续吗？');
    if (!confirmed) return;
  }
  card?.setAttribute('aria-busy', 'true');
  state.memoryMutationsInFlight.add(id);
  $$('button, input, textarea', card || document.createElement('div')).forEach((control) => { control.disabled = true; });
  try {
    const updated = await API.governMemorySuggestion(id, {
      action,
      expected_revision: Number(item.governance?.revision || 1),
      ...fields,
    });
    replaceMemorySuggestion(updated);
    state.memoryEditingIds.delete(id);
    state.memoryMutationsInFlight.delete(id);
    const messages = {
      edit: '已保存新版本，证据未改变', approve: '已采用；从下一个匹配的新任务开始生效',
      reject: '已拒绝并保留在历史中', postpone: '已标记稍后处理，建议仍保留在待审核队列',
      disable: '已停用；不再介入新任务', enable: '已重新启用', reopen: '已恢复为待审核',
      undo: '已撤销上次变更', redo: '已恢复被撤销的变更',
    };
    renderMemoryQueue('');
    const canUndo = Array.isArray(updated.governance?.available_actions)
      && updated.governance.available_actions.includes('undo') && action !== 'undo';
    toast(messages[action] || '知识建议已更新', 'success', canUndo ? 5200 : 2200, canUndo ? {
      label: '撤销',
      onClick: () => performMemoryGovernanceAction(id, 'undo', null),
    } : null);
  } catch (error) {
    if (Number(error?.status) === 409 && error?.detail?.code === 'MEMORY_REVISION_CONFLICT') {
      state.memoryMutationsInFlight.delete(id);
      if (error.detail.current) replaceMemorySuggestion(error.detail.current);
      renderMemoryQueue(id);
      toast('这条建议已在别处更新，已载入最新版本，请重新确认', 'error', 6200);
    } else {
      state.memoryMutationsInFlight.delete(id);
      card?.setAttribute('aria-busy', 'false');
      $$('button, input, textarea', card || document.createElement('div')).forEach((control) => { control.disabled = false; });
      toast(`知识建议更新失败：${formatApiError(error, '本地账本暂不可写')}`, 'error', 6200);
    }
  }
}

async function loadMemory(targetSuggestionId = state.memoryTargetSuggestionId) {
  const list = $('#memory-list');
  list.innerHTML = statusPanelHtml('loading', { title: '正在读取知识建议', detail: '正在核对证据、版本与当前生效状态。', fill: true });
  try {
    const targetId = String(targetSuggestionId || '').trim();
    const [ledger, allSuggestions, knowledgeStatus] = await Promise.all([
      API.getLedgerStatus(),
      API.getMemorySuggestions('all'),
      API.getKnowledgeStatus().catch(() => state.knowledgeStatus || {}),
    ]);
    const suggestions = [...allSuggestions];
    if (targetId && !suggestions.some((item) => String(item.id) === targetId)) {
      const target = await API.getMemorySuggestion(targetId).catch(() => null);
      if (target) suggestions.unshift(target);
    }
    const target = suggestions.find((item) => String(item.id) === targetId);
    if (target) {
      state.memoryFilter = ['pending', 'approved', 'disabled'].includes(target.status)
        ? target.status : 'all';
      state.memoryExpandedIds.add(targetId);
    }
    state.memorySuggestions = suggestions;
    state.knowledgeStatus = knowledgeStatus;
    const pendingSuggestions = suggestions.filter((item) => item.status === 'pending');
    renderMemoryProjection(ledger, pendingSuggestions, knowledgeStatus);
    renderMemoryQueue(targetId);
    state.memoryTargetSuggestionId = '';
  } catch (error) {
    list.innerHTML = statusPanelHtml('offline', {
      title: '审核队列暂时离线',
      detail: formatApiError(error, '反馈证据与待审核建议暂不可用'),
      fill: true,
      action: { label: '重新读取', attribute: 'data-memory-status-action', value: 'retry' },
    });
    $('[data-memory-status-action="retry"]', list)?.addEventListener('click', () => loadMemory());
  }
}

function bindEvents() {
  $$('.rail-button[data-page]').forEach((button) => button.addEventListener('click', () => switchPage(button.dataset.page)));
  $$('.mode-button').forEach((button) => button.addEventListener('click', () => switchMode(button.dataset.mode)));
  $$('[data-cutout-strategy]').forEach((button) => button.addEventListener('click', () => selectCutoutStrategy(button.dataset.cutoutStrategy)));
  $('#semantic-query').addEventListener('input', updateSemanticCutoutField);
  $('#semantic-model-query').addEventListener('input', updateSemanticCutoutField);
  $('#semantic-count').addEventListener('input', updateSemanticCutoutField);
  $('#btn-semantic-preview').addEventListener('click', openSemanticSelection);
  $('#semantic-selection-backdrop').addEventListener('click', () => closeSemanticSelection());
  $('#semantic-selection-close').addEventListener('click', () => closeSemanticSelection());
  $('#semantic-selection-cancel').addEventListener('click', () => closeSemanticSelection());
  $('#semantic-selection-confirm').addEventListener('click', confirmSemanticSelection);
  $('#semantic-add-full').addEventListener('click', () => addSemanticRegion([0, 0, 1, 1]));
  $('#semantic-mask-preview').addEventListener('click', previewSemanticMask);
  $('#semantic-brush-size').addEventListener('input', (event) => {
    semanticCanvasState.brushRadius = Math.max(0.005, Math.min(0.06, Number(event.target.value) / 100));
    $('#semantic-brush-size-value').textContent = `${(semanticCanvasState.brushRadius * 100).toFixed(1)}%`;
  });
  $('#semantic-mask-point-include').addEventListener('click', () => addSemanticMaskPoint('include'));
  $('#semantic-mask-point-exclude').addEventListener('click', () => addSemanticMaskPoint('exclude'));
  $$('[data-semantic-tool]').forEach((button) => button.addEventListener('click', () => {
    semanticCanvasState.tool = button.dataset.semanticTool;
    semanticCanvasState.dragStart = null;
    semanticCanvasState.dragCurrent = null;
    semanticCanvasState.stroke = null;
    renderSemanticRegions();
  }));
  $('#semantic-mask-undo').addEventListener('click', () => {
    semanticCanvasState.maskEdits.pop();
    invalidateSemanticMask(semanticCanvasState.maskEdits.length
      ? `已撤销一笔修正，当前保留 ${semanticCanvasState.maskEdits.length} 笔；请重新生成预览。`
      : '已撤销全部蒙版修正；可重新生成预览。');
    renderSemanticRegions();
  });
  $('#semantic-mask-clear').addEventListener('click', () => {
    semanticCanvasState.maskEdits = [];
    invalidateSemanticMask('已清除全部蒙版修正；可重新生成预览。');
    renderSemanticRegions();
  });
  $('#semantic-undo').addEventListener('click', () => {
    semanticCanvasState.regions.pop();
    semanticCanvasState.groundingStatus = 'manual_regions';
    semanticCanvasState.groundingTone = 'manual';
    semanticCanvasState.groundingMessage = '已撤销一个选区；请补充并检查后确认';
    semanticCanvasState.manualRevision += 1;
    invalidateSemanticMask('目标框已变化；补齐数量后可重新生成蒙版预览。');
    renderSemanticRegions();
  });
  $('#semantic-clear').addEventListener('click', () => {
    semanticCanvasState.regions = [];
    semanticCanvasState.suggestedRegions = [];
    semanticCanvasState.maskEdits = [];
    semanticCanvasState.groundingStatus = 'manual_regions';
    semanticCanvasState.groundingTone = 'manual';
    semanticCanvasState.groundingMessage = '已清空候选与建议，请重新手动框选目标';
    semanticCanvasState.manualRevision += 1;
    invalidateSemanticMask('已清空目标框和蒙版笔画，请重新框选目标。');
    renderSemanticRegions();
  });
  $('#semantic-region-list').addEventListener('click', (event) => {
    const suggestionButton = event.target.closest('[data-adopt-semantic-suggestion]');
    if (suggestionButton) {
      if (semanticCanvasState.regions.length >= semanticCanvasState.targetCount) return;
      const index = Number(suggestionButton.dataset.adoptSemanticSuggestion);
      const [suggestion] = semanticCanvasState.suggestedRegions.splice(index, 1);
      if (!suggestion) return;
      semanticCanvasState.regions.push({
        ...suggestion,
        id: `target-${semanticCanvasState.regions.length + 1}`,
        origin: 'automatic-review',
        bbox: [...suggestion.bbox],
      });
      semanticCanvasState.manualRevision += 1;
      semanticCanvasState.groundingStatus = 'review_adopted';
      semanticCanvasState.groundingTone = 'warning';
      semanticCanvasState.groundingMessage = '已采用一个橙色建议；它仍需你检查位置和目标是否正确';
      invalidateSemanticMask('目标框已变化；确认数量后可生成蒙版预览。');
      renderSemanticRegions();
      return;
    }
    const button = event.target.closest('[data-remove-semantic-region]');
    if (!button) return;
    semanticCanvasState.regions.splice(Number(button.dataset.removeSemanticRegion), 1);
    semanticCanvasState.regions.forEach((region, index) => { region.id = `target-${index + 1}`; });
    semanticCanvasState.groundingStatus = 'manual_regions';
    semanticCanvasState.groundingTone = 'manual';
    semanticCanvasState.groundingMessage = '已移除一个候选；请补充并检查后确认';
    semanticCanvasState.manualRevision += 1;
    invalidateSemanticMask('目标框已变化；补齐数量后可重新生成蒙版预览。');
    renderSemanticRegions();
  });
  $('#semantic-region-list').addEventListener('change', (event) => {
    const input = event.target.closest('[data-semantic-region-index]');
    if (input) updateSemanticRegionCoordinate(input);
  });
  $('#semantic-region-list').addEventListener('input', (event) => {
    const input = event.target.closest('[data-semantic-region-index]');
    if (input) updateSemanticRegionCoordinate(input, false);
  });
  const semanticCanvas = $('#semantic-selection-canvas');
  semanticCanvas.addEventListener('pointerdown', (event) => {
    if (event.button !== 0) return;
    if (semanticCanvasState.tool !== 'box') {
      if (semanticCanvasState.regions.length !== semanticCanvasState.targetCount) {
        semanticCanvasState.maskStatus = '请先完成目标框与数量确认，再使用蒙版画笔。';
        renderSemanticRegions();
        return;
      }
      if (semanticCanvasState.maskEdits.length >= 200) {
        semanticCanvasState.maskStatus = '蒙版修正已达到 200 笔上限；请撤销或清除部分笔画。';
        renderSemanticRegions();
        return;
      }
      semanticCanvas.setPointerCapture(event.pointerId);
      const point = semanticCanvasPoint(event);
      semanticCanvasState.stroke = {
        mode: semanticCanvasState.tool,
        points: [[point.x, point.y]],
        radius: semanticCanvasState.brushRadius,
      };
      drawSemanticCanvas();
      return;
    }
    if (semanticCanvasState.regions.length >= semanticCanvasState.targetCount) {
      addSemanticRegion([0, 0, 0, 0]);
      return;
    }
    semanticCanvas.setPointerCapture(event.pointerId);
    semanticCanvasState.manualRevision += 1;
    semanticCanvasState.groundingStatus = 'manual_regions';
    semanticCanvasState.groundingTone = 'manual';
    semanticCanvasState.groundingMessage = '正在手动框选；自动结果不会覆盖你的修改';
    semanticCanvasState.dragStart = semanticCanvasPoint(event);
    semanticCanvasState.dragCurrent = semanticCanvasState.dragStart;
    drawSemanticCanvas();
  });
  semanticCanvas.addEventListener('pointermove', (event) => {
    if (semanticCanvasState.stroke) {
      const point = semanticCanvasPoint(event);
      const last = semanticCanvasState.stroke.points.at(-1);
      if (
        semanticCanvasState.stroke.points.length < 1024
        && Math.hypot(point.x - last[0], point.y - last[1]) >= 0.002
      ) {
        semanticCanvasState.stroke.points.push([point.x, point.y]);
        drawSemanticCanvas();
      }
      return;
    }
    if (!semanticCanvasState.dragStart) return;
    semanticCanvasState.dragCurrent = semanticCanvasPoint(event);
    drawSemanticCanvas();
  });
  semanticCanvas.addEventListener('pointerup', (event) => {
    if (semanticCanvasState.stroke) {
      const stroke = semanticCanvasState.stroke;
      const point = semanticCanvasPoint(event);
      const last = stroke.points.at(-1);
      if (
        stroke.points.length < 1024
        && Math.hypot(point.x - last[0], point.y - last[1]) >= 0.001
      ) {
        stroke.points.push([point.x, point.y]);
      }
      semanticCanvasState.maskEdits.push({
        ...stroke,
        points: stroke.points.map(([x, y]) => [
          Number(x.toFixed(6)),
          Number(y.toFixed(6)),
        ]),
      });
      semanticCanvasState.stroke = null;
      semanticCanvasState.manualRevision += 1;
      invalidateSemanticMask(`已记录 ${semanticCanvasState.maskEdits.length} 笔蒙版修正；请重新生成预览复核。`);
      renderSemanticRegions();
      return;
    }
    if (!semanticCanvasState.dragStart) return;
    const start = semanticCanvasState.dragStart;
    const end = semanticCanvasPoint(event);
    semanticCanvasState.dragStart = null;
    semanticCanvasState.dragCurrent = null;
    addSemanticRegion([
      Math.min(start.x, end.x),
      Math.min(start.y, end.y),
      Math.abs(end.x - start.x),
      Math.abs(end.y - start.y),
    ]);
  });
  semanticCanvas.addEventListener('pointercancel', () => {
    semanticCanvasState.stroke = null;
    semanticCanvasState.dragStart = null;
    semanticCanvasState.dragCurrent = null;
    drawSemanticCanvas();
  });
  const input = $('#file-input');
  $('#btn-browse').addEventListener('click', () => input.click());
  $('#btn-replace').addEventListener('click', () => input.click());
  $('#btn-folder-browse').addEventListener('click', chooseFolderSource);
  $('#btn-folder-load').addEventListener('click', importFolderSource);
  $('#folder-path').addEventListener('keydown', (event) => {
    if (event.key === 'Enter') importFolderSource();
  });
  input.addEventListener('change', () => handleFiles(input.files));
  $('#btn-clear').addEventListener('click', () => clearSession(true));
  $('#btn-new-session').addEventListener('click', () => { clearSession(true); switchPage('process'); });
  $('#btn-generate').addEventListener('click', handleGenerate);
  $('#btn-retry').addEventListener('click', handleGenerate);
  $('#btn-error-reset').addEventListener('click', () => { setStage(state.selectedFiles.length ? 'ready' : 'empty'); updateCtaState(); });
  $('#brief-input').addEventListener('input', scheduleKnowledgeCompile);
  $('#param-model').addEventListener('change', updateQuickControls);
  $('#param-angle').addEventListener('change', updateQuickControls);
  $('#param-output-ratio').addEventListener('change', updateQuickControls);
  $('#param-output-resolution').addEventListener('change', updateQuickControls);
  $('#param-fidelity').addEventListener('input', updateQuickControls);
  $('#param-batch').addEventListener('input', updateQuickControls);
  $$('input[name="platter"]').forEach((radio) => radio.addEventListener('change', updateQuickControls));
  $('#param-refine').addEventListener('change', updateQuickControls);
  $$('[data-lock]').forEach((input) => input.addEventListener('change', () => { input.closest('.lock-chip').classList.toggle('active', input.checked); updateCtaState(); scheduleKnowledgeCompile(); }));
  $('#btn-advanced').addEventListener('click', () => openDrawer('advanced'));
  $('#btn-workflow-drawer').addEventListener('click', workflowDock.open);
  $('#task-dock-backdrop').addEventListener('click', () => workflowDock.close());
  $$('[data-open-advanced]').forEach((button) => button.addEventListener('click', () => openDrawer('advanced')));
  $('#btn-open-intelligence').addEventListener('click', () => openDrawer('intelligence'));
  $('#btn-job-dock').addEventListener('click', () => openDrawer('jobs'));
  $('#btn-rail-jobs').addEventListener('click', () => openDrawer('jobs'));
  $('#btn-refresh-jobs').addEventListener('click', async () => {
    const button = $('#btn-refresh-jobs');
    if (button.disabled) return;
    button.disabled = true;
    try { await loadJobs(false); } finally { button.disabled = false; }
  });
  $$('[data-job-filter]').forEach((button) => button.addEventListener('click', () => {
    state.jobFilter = button.dataset.jobFilter || 'all';
    state.jobVisibleLimit = 12;
    renderJobs(true);
  }));
  $('#btn-knowledge-card').addEventListener('click', () => openDrawer('intelligence'));
  $$('[data-close-drawer]').forEach((button) => button.addEventListener('click', () => closeDrawer(button.dataset.closeDrawer)));
  const resultTabs = $$('.result-tab');
  resultTabs.forEach((button, index) => {
    button.addEventListener('click', () => {
      selectResultVersion(0, button.dataset.rtab);
    });
    button.addEventListener('keydown', (event) => {
      const targets = { ArrowRight: index + 1, ArrowLeft: index - 1, Home: 0, End: resultTabs.length - 1 };
      if (!(event.key in targets)) return;
      event.preventDefault();
      const targetIndex = event.key === 'ArrowRight' || event.key === 'ArrowLeft'
        ? (targets[event.key] + resultTabs.length) % resultTabs.length
        : targets[event.key];
      resultTabs[targetIndex]?.focus();
      resultTabs[targetIndex]?.click();
    });
  });
  $('#viewer-prev').addEventListener('click', () => {
    const items = getResultItems();
    if (items.length) selectResultVersion((state.viewerIndex - 1 + items.length) % items.length);
  });
  $('#viewer-next').addEventListener('click', () => {
    const items = getResultItems();
    if (items.length) selectResultVersion((state.viewerIndex + 1) % items.length);
  });
  $('#btn-open-compare').addEventListener('click', () => { renderCompare(); switchPage('compare'); });
  $('#btn-compare-back').addEventListener('click', () => switchPage('process'));
  $('#btn-review-why').addEventListener('click', () => openDrawer('intelligence'));
  $('#compare-target').addEventListener('change', (event) => {
    const value = String(event.target.value || 'source');
    updateCompareState({ secondary_result_asset_id: value === 'source' ? '' : value });
    renderCompare();
  });
  $('#btn-compare-zoom-out').addEventListener('click', () => {
    const current = compareStateForMode();
    const zoom = Math.max(1, Math.round((current.zoom - 0.25) * 100) / 100);
    setCompareTransform({
      ...current,
      zoom,
      pan_x: zoom === 1 ? 0 : current.pan_x,
      pan_y: zoom === 1 ? 0 : current.pan_y,
    });
  });
  $('#btn-compare-zoom-in').addEventListener('click', () => {
    const current = compareStateForMode();
    setCompareTransform({ ...current, zoom: Math.min(4, Math.round((current.zoom + 0.25) * 100) / 100) });
  });
  $('#btn-compare-reset').addEventListener('click', () => {
    const reset = updateCompareState({ divider: 50, zoom: 1, pan_x: 0, pan_y: 0 });
    setComparePosition(reset.divider, false);
    setCompareTransform(reset, false);
    toast('对比位置与缩放已复位');
  });
  $('#btn-compare-help').addEventListener('click', () => {
    reviewGuideReturnFocus = document.activeElement;
    setReviewGuideOpen(true, { focus: true });
  });
  $('#btn-review-guide-done').addEventListener('click', () => {
    updateCompareState({ guide_dismissed: true });
    setReviewGuideOpen(false, { restore: true });
  });
  $('#btn-review-edit').addEventListener('click', () => {
    const item = getResultItems()[state.viewerIndex];
    state.editingFeedbackResultKey = activeFeedbackResultKey(item);
    state.reviewDecision = '';
    renderCompare();
    $('#review-options [data-review-decision]')?.focus();
  });
  $('#btn-review-summary-suggestion').addEventListener('click', (event) => {
    openMemorySuggestion(event.currentTarget.dataset.suggestionId || '');
  });
  $$('[data-review-decision]').forEach((button) => button.addEventListener('click', () => {
    activateReviewDecision(button.dataset.reviewDecision || '');
    $('#review-reason-tags button')?.focus();
  }));
  $('#btn-review-adjust').addEventListener('click', async () => {
    if (state.reviewDecision !== 'adjusted') { toast('请先选择“需要小幅调整”'); return; }
    await startImmediateAdjustment(
      $('#review-reason-input').value.trim(),
      [...state.reviewReasonCodes],
    );
  });
  $('#btn-review-record').addEventListener('click', async () => {
    if (!state.reviewDecision) { toast('请先选择一个结果判断'); return; }
    const reason = $('#review-reason-input').value.trim();
    const saved = await recordFeedback(
      state.reviewDecision,
      reason,
      'record',
      [...state.reviewReasonCodes],
    );
    if (saved) {
      clearReviewForm();
      switchPage('process');
      renderResults();
    }
  });
  $('#btn-review-suggest').addEventListener('click', async () => {
    if (!state.reviewDecision) { toast('请先选择一个结果判断'); return; }
    const reason = $('#review-reason-input').value.trim();
    if (!reason && !state.reviewReasonCodes.size) { toast('形成知识建议前，请选择原因或写下具体判断依据'); return; }
    const saved = await recordFeedback(
      state.reviewDecision,
      reason,
      'suggest',
      [...state.reviewReasonCodes],
    );
    if (!saved) return;
    clearReviewForm();
    switchPage('process');
    renderResults();
  });
  $('#btn-save-all').addEventListener('click', saveCurrentResults);
  $('#btn-adopt').addEventListener('click', async () => {
    state.lastFeedbackSignal = 'adopted';
    await recordFeedback('adopted', $('#feedback-input').value.trim());
  });
  $('#btn-reject').addEventListener('click', () => {
    state.lastFeedbackSignal = 'rejected';
    renderFeedbackState();
    $('#feedback-input').focus();
    toast('请说出具体原因，它会成为下一版证据');
  });
  $('#btn-feedback').addEventListener('click', async () => {
    const reason = $('#feedback-input').value.trim();
    if (!reason) { toast('请先写下具体判断'); return; }
    const ok = await recordFeedback(state.lastFeedbackSignal === 'rejected' ? 'rejected' : 'note', reason);
    if (ok) state.lastFeedbackSignal = 'note';
  });
  $('#btn-feedback-edit').addEventListener('click', () => {
    state.editingFeedbackResultKey = state.feedbackResultKey;
    state.feedbackRecorded = false;
    state.feedbackReceipt = '';
    state.lastFeedbackSignal = 'note';
    renderFeedbackState();
    $('#feedback-input').focus();
  });
  $('#btn-feedback-suggestion').addEventListener('click', () => {
    openMemorySuggestion(state.feedbackSuggestionId || $('#btn-feedback-suggestion').dataset.suggestionId);
  });
  $('#btn-result-next').addEventListener('click', completeCurrentWorkspace);
  $('#btn-refresh-history').addEventListener('click', loadSessions);
  $('#history-project-filter').addEventListener('change', (event) => {
    state.sessionProjectFilter = event.target.value || 'all';
    state.sessionShowAll = false;
    renderSessionsDashboard();
  });
  $('#btn-toggle-history').addEventListener('click', () => {
    state.sessionShowAll = !state.sessionShowAll;
    renderSessionsDashboard();
  });
  $('#btn-refresh-memory').addEventListener('click', () => loadMemory());
  const memoryFilters = $$('[data-memory-filter]');
  memoryFilters.forEach((button, index) => {
    button.addEventListener('click', () => {
      state.memoryFilter = button.dataset.memoryFilter || 'pending';
      state.memoryTargetSuggestionId = '';
      renderMemoryQueue('');
    });
    button.addEventListener('keydown', (event) => {
      const targets = { ArrowRight: index + 1, ArrowLeft: index - 1, Home: 0, End: memoryFilters.length - 1 };
      if (!(event.key in targets)) return;
      event.preventDefault();
      const targetIndex = event.key === 'ArrowRight' || event.key === 'ArrowLeft'
        ? (targets[event.key] + memoryFilters.length) % memoryFilters.length
        : targets[event.key];
      memoryFilters[targetIndex]?.focus();
      memoryFilters[targetIndex]?.click();
    });
  });
  $('#workspace-sync-state').addEventListener('click', (event) => {
    const button = event.target.closest('[data-workspace-status-action="retry"]');
    const saveButton = event.target.closest('[data-workspace-status-action="retry-save"]');
    const target = button || saveButton;
    if (!target || target.disabled) return;
    target.disabled = true;
    target.setAttribute('aria-busy', 'true');
    if (saveButton) flushWorkspaceDraft(state.currentMode, false);
    else if (state.backendReady) loadWorkspace(state.currentMode, false);
    else connectBackend();
  });
  $('#btn-replay-memory').addEventListener('click', replayMemoryMotion);
  $$('[data-memory-node]').forEach((node) => node.addEventListener('click', () => selectMemoryNode(node)));
  $('#btn-open-memory-evidence').addEventListener('click', () => openDrawer('intelligence'));
  $('#btn-open-memory-trace').addEventListener('click', () => openDrawer('intelligence'));
  assetManager.bind();
  settingsController.bind();
  $('#modal-backdrop').addEventListener('click', closeModal);
  $('#modal-close').addEventListener('click', closeModal);
  $('#btn-min-dot').addEventListener('click', () => API.minimizeWindow().catch(() => {}));
  $('#btn-max-dot').addEventListener('click', () => API.toggleMaximize().catch(() => {}));
  $('#btn-close-dot').addEventListener('click', () => API.closeApp().catch(() => window.close()));
  const canvas = $('#preview-canvas');
  canvas.addEventListener('dragover', (event) => { event.preventDefault(); canvas.style.outline = '2px solid var(--coral)'; });
  canvas.addEventListener('dragleave', () => { canvas.style.outline = ''; });
  canvas.addEventListener('drop', (event) => { event.preventDefault(); canvas.style.outline = ''; handleFiles(event.dataTransfer.files); });
  document.addEventListener('keydown', (event) => {
    const workflowLayer = $('#settings-panel').classList.contains('is-open') ? $('#settings-panel') : null;
    const openLayer = [$('#semantic-selection-modal'), $('#img-modal'), $('#job-drawer'), $('#asset-drawer'), $('#advanced-drawer'), $('#intelligence-drawer'), workflowLayer].find((layer) => layer && !layer.hidden);
    if (event.key === 'Tab' && openLayer) {
      const focusRoot = openLayer.id === 'semantic-selection-modal'
        ? $('.semantic-modal-card', openLayer)
        : openLayer.id === 'img-modal'
          ? $('.modal-card', openLayer)
          : openLayer.id === 'settings-panel'
            ? openLayer
            : $('.drawer', openLayer);
      const focusable = $$('button:not(:disabled), input:not(:disabled), select:not(:disabled), textarea:not(:disabled), [contenteditable="true"], [tabindex]:not([tabindex="-1"])', focusRoot).filter((element) => element.offsetParent !== null);
      const first = focusable[0];
      const last = focusable[focusable.length - 1];
      if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last?.focus(); }
      else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first?.focus(); }
    }
    if (event.key !== 'Escape') return;
    if (!$('#review-guide').hidden) { setReviewGuideOpen(false, { restore: true }); return; }
    if (!$('#semantic-selection-modal').hidden) { closeSemanticSelection(); return; }
    if (!$('#img-modal').hidden) closeModal();
    if (!$('#advanced-drawer').hidden) closeDrawer('advanced');
    if (!$('#intelligence-drawer').hidden) closeDrawer('intelligence');
    if (!$('#job-drawer').hidden) closeDrawer('jobs');
    if (!$('#asset-drawer').hidden) closeDrawer('assets');
    if ($('#settings-panel').classList.contains('is-open')) workflowDock.close();
  });
  workflowDock.bind();
  window.addEventListener('beforeunload', () => {
    workflowDock.destroy();
    if (state.jobPollTimer) window.clearTimeout(state.jobPollTimer);
    state.draftSaveTimers.forEach((timer) => window.clearTimeout(timer));
    state.draftSaveTimers.clear();
    state.assetsRequestVersion += 1;
    state.assetsAbortController?.abort();
    invalidateJobsRead();
  });
  document.addEventListener('visibilitychange', () => {
    if (document.visibilityState === 'hidden' && state.backendReady) flushWorkspaceDraft(state.currentMode, true);
  });
}

async function connectBackend() {
  if (state.backendConnecting) return false;
  state.backendConnecting = true;
  setBackendStatus('connecting', '连接中');
  setBootStatus('正在启动本地服务', '界面已经就绪，正在连接任务与素材账本…');
  API.reportStartupMilestone('backend-connecting');
  const deadline = performance.now() + 45000;
  try {
    while (performance.now() < deadline) {
      const health = await API.checkHealth();
      if (health.ok) {
        setBootStatus('正在恢复工作现场', '同步素材、任务和上次保存的创作状态…');
        try { const status = await API.getKnowledgeStatus(); settingsController.renderKnowledgeStatus(status); } catch (_) { /* keep app usable */ }
        const [assetsLoaded, jobsLoaded] = await Promise.all([loadAssets(false), loadJobs(true)]);
        if (assetsLoaded !== true || jobsLoaded !== true) {
          setBackendStatus('connecting', '重连中');
          continue;
        }
        setBackendStatus('connected', '已连接');
        API.reportStartupMilestone('backend-ready');
        startJobPolling();
        API.reportStartupMilestone('workspace-ready');
        setBootStatus('工作台已就绪', '可以继续上次任务或开始新的创作。', 'ready');
        dismissBootShell();
        return true;
      }
      const remaining = deadline - performance.now();
      if (remaining > 0) await new Promise((resolve) => window.setTimeout(resolve, Math.min(700, remaining)));
    }
    setBackendStatus('disconnected', '未连接');
    state.assetsAvailable = false;
    state.jobsAvailable = false;
    renderJobs();
    updateCtaState();
    API.reportStartupMilestone('backend-unavailable');
    setBootStatus('本地服务暂未就绪', '可以重试连接，或先进入界面检查设置。', 'error');
    toast('本地服务尚未就绪，可以重试连接', 'error');
    return false;
  } finally {
    state.backendConnecting = false;
  }
}

async function init() {
  API.reportStartupMilestone('dom-ready');
  window.requestAnimationFrame(() => window.requestAnimationFrame(() => {
    API.reportStartupMilestone('first-paint');
  }));
  setupAppearance();
  bindEvents();
  workflowDock.sync();
  setupCompare();
  restoreWorkspaceState();
  restorePendingSubmission();
  restorePendingReviewRequests();
  switchMode(state.currentMode, false);
  setStage('empty');
  renderJobs();
  updateQuickControls();
  $('#boot-retry')?.addEventListener('click', () => connectBackend());
  $('#boot-enter')?.addEventListener('click', dismissBootShell);
  connectBackend();
  settingsController.load();
}

document.addEventListener('DOMContentLoaded', init);
