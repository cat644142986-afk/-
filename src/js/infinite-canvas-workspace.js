import * as API from './api.js';
import { createApiSpatialCanvasAdapter, spatialSceneSignature } from './infinite-canvas-adapter.js';
import { recoveryViewModel } from './status-view.js';
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
import {
  SPATIAL_CANVAS_CONVERSATION_SURFACE,
  SPATIAL_IMAGE_AI_COMMAND_ID,
  SPATIAL_IMAGE_AI_SKILL_ID,
  SPATIAL_RESULT_VARIATION_ACTION,
  SPATIAL_WHITE_BACKGROUND_ACTION,
  applySpatialImageAiPreview,
  createSpatialImageAiDraft,
  isSpatialImageAiJob,
  spatialImageAiAction,
  spatialImageAiCanvasId,
  spatialImageAiCommandPayload,
  spatialImageAiDefinition,
  spatialImageAiJobIsActive,
  spatialImageAiJobIsSettled,
  spatialImageAiPreviewPayload,
  spatialImageAiResultAssetIds,
  spatialImageAiSourceElementId,
  updateSpatialImageAiDraft,
} from './spatial-native-image-ai.js';

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

export function spatialClipboardImageFiles(clipboardData) {
  const types = Array.from(clipboardData?.types || []).map((value) => String(value).toLowerCase());
  if (types.some((value) => value.includes('excalidraw'))) return [];
  const direct = Array.from(clipboardData?.files || []);
  const files = direct.length ? direct : Array.from(clipboardData?.items || [])
    .filter((item) => item?.kind === 'file')
    .map((item) => item.getAsFile?.())
    .filter(Boolean);
  return files.filter((file) => /^image\//i.test(String(file?.type || '')));
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

function shortHash(value) {
  const hash = String(value || '');
  return hash ? hash.slice(0, 12) : '—';
}

function imageAiDraftHtml(draft, asset, {
  previewing = false,
  submitting = false,
  error = '',
} = {}) {
  if (!draft) return '';
  const preview = draft.preview;
  const context = preview?.executionContext || null;
  const skill = preview?.skillSnapshot || null;
  const summary = context?.summary || {};
  const definition = spatialImageAiDefinition(draft.action, draft);
  const providerCalls = draft.productProfileVersionId ? 1 : 2;
  const busy = previewing || submitting;
  const primaryLabel = submitting
    ? '正在创建任务'
    : previewing
      ? '正在核对上下文'
      : preview
        ? `确认并创建任务 · ${providerCalls === 1 ? '1 次调用' : '最多 2 次调用'}`
        : '重新核对执行上下文';
  const skillOptions = `
    <option value=""${draft.designSkillId ? '' : ' selected'}>默认方法 · 不额外引用</option>
    <option value="${SPATIAL_IMAGE_AI_SKILL_ID}"${draft.designSkillId === SPATIAL_IMAGE_AI_SKILL_ID ? ' selected' : ''}>食品饮料白底主图</option>
  `;
  return `
    <form class="spatial-white-form" data-spatial-image-ai-form novalidate>
      <div class="spatial-white-form__heading"><span>CANVAS NATIVE AI</span><strong>${escapeHtml(definition.title)}</strong><small>提交前核对本次真正生效的上下文</small></div>
      <label class="spatial-white-form__wide"><span>本次要求</span><textarea data-spatial-image-ai-field="userRequest" maxlength="1200" rows="4" ${busy ? 'disabled' : ''}>${escapeHtml(draft.userRequest)}</textarea><small>主体结构、数量、包装文字与 Logo 始终锁定。</small></label>
      <label class="spatial-white-form__wide"><span>设计方法</span><select data-spatial-image-ai-field="designSkillId" ${busy ? 'disabled' : ''}>${skillOptions}</select><small>只读贡献设计规则，不执行脚本或 Provider。</small></label>
      <section class="spatial-context-preview" data-spatial-image-ai-preview aria-live="polite">
        ${preview ? `
          <dl>
            <div><dt>用户意图</dt><dd>${escapeHtml(context?.user_intent?.user_request || draft.userRequest)}</dd></div>
            <div><dt>Canvas Context</dt><dd>${escapeHtml(definition.sourceLabel)} · ${escapeHtml(shortHash(preview.spatialContext?.fingerprint))}</dd></div>
            <div><dt>Product Profile</dt><dd>${draft.productProfileVersionId ? `已冻结 · ${escapeHtml(shortHash(draft.productProfileVersionId))}` : '未绑定 · 将先识别素材'}</dd></div>
            <div><dt>Approved Knowledge</dt><dd>${Number(summary.source_count || 0)} 条来源 · ${Number(summary.positive_rule_count || 0) + Number(summary.negative_rule_count || 0)} 条规则</dd></div>
            <div><dt>设计方法</dt><dd>${skill ? `${escapeHtml(skill.title || '食品饮料白底主图')} · ${escapeHtml(skill.version || '')}<br><small>hash ${escapeHtml(shortHash(skill.content_sha256))} · ${escapeHtml(skill.adapter_version || '')}</small>` : '默认方法'}</dd></div>
            <div><dt>Provider</dt><dd>${escapeHtml(context?.provider_adapter?.model || draft.model)} · ${providerCalls === 1 ? '1 次调用' : '最多 2 次（含素材识别）'}</dd></div>
          </dl>
        ` : `<p>${previewing ? '正在从账本编译执行上下文…' : '要求已变化，请重新核对后再提交。'}</p>`}
      </section>
      <p class="spatial-white-form__status" data-spatial-image-ai-status aria-live="polite"${error ? ' data-error="true" tabindex="-1"' : ''}>${escapeHtml(error || (preview ? '上下文已冻结；点击确认后才会创建正式任务。' : '尚未发起 Provider 调用。'))}</p>
      <div class="spatial-white-form__actions">
        ${draft.inputSurface === SPATIAL_CANVAS_CONVERSATION_SURFACE ? '' : `<button type="button" data-spatial-image-ai-classic ${busy ? 'disabled' : ''}>到经典页调整</button>`}
        <button type="button" data-spatial-image-ai-cancel ${busy ? 'disabled' : ''}>取消</button>
        <button type="submit" class="is-primary" ${busy ? 'disabled' : ''}>${primaryLabel}</button>
      </div>
      <p class="spatial-white-form__source">来源：${escapeHtml(asset?.name || draft.sourceAssetId)} · 失败后不自动付费重试</p>
    </form>
  `;
}

function canvasConversationEligible(element, asset) {
  const refs = element?.customData || {};
  const assetId = String(refs.asset_id || '');
  const resultId = String(refs.result_id || '');
  const exactResult = Boolean(resultId) && resultId === assetId;
  const sourceRole = !resultId && asset?.role === 'workspace_source';
  const resultRole = exactResult && String(asset?.role || '').startsWith('result_');
  return Boolean(
    element?.type === 'image'
    && assetId
    && asset?.id === assetId
    && asset?.kind !== 'video'
    && String(asset?.mime || '').startsWith('image/')
    && (sourceRole || resultRole)
  );
}

function canvasConversationHtml(value, asset, { reviewing = false, error = '' } = {}) {
  return `
    <form class="spatial-conversation" data-spatial-conversation-form novalidate>
      <label><span>告诉 AI 下一步怎么改</span><textarea data-spatial-conversation-field maxlength="1200" rows="2" ${reviewing ? 'disabled' : ''}>${escapeHtml(value)}</textarea></label>
      <div class="spatial-conversation__meta"><small>只针对当前所选图片 · 核对修改不会调用 Provider</small><kbd>Ctrl+Enter</kbd></div>
      <p class="spatial-conversation__status" data-spatial-conversation-status aria-live="polite"${error ? ' data-error="true" tabindex="-1"' : ''}>${escapeHtml(error || (reviewing ? '正在编译本次执行上下文…' : `当前输入：${asset?.role === 'workspace_source' ? '原始素材' : '精确 Result'}`))}</p>
      <div class="spatial-conversation__actions"><button type="submit" class="is-primary" ${reviewing ? 'disabled aria-busy="true"' : ''}>核对修改</button></div>
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
  onWhiteBackgroundJobSubmitted = () => {},
  onWhiteBackgroundJobSettled = () => {},
  onWhiteBackgroundClassic = () => {},
  getWhiteBackgroundDefaults = () => ({}),
  onImageAiJobSubmitted = onWhiteBackgroundJobSubmitted,
  onImageAiJobSettled = onWhiteBackgroundJobSettled,
  onImageAiClassic = (action, context) => (
    action === SPATIAL_WHITE_BACKGROUND_ACTION
      ? onWhiteBackgroundClassic(context)
      : onAction(action, context)
  ),
  getImageAiDefaults = getWhiteBackgroundDefaults,
  onRecoveryAction = () => {},
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
  let recordsFailure = null;
  let recoveryContext = null;
  let pendingBusinessImport = null;
  let pendingFileImport = null;
  const pendingScenes = new Map();
  const savingScenes = new Map();
  const sceneConflicts = new Map();
  let sceneSequence = 0;
  let sceneTimer = null;
  let sceneTimerCanvasId = '';
  let savePromise = Promise.resolve();
  let openEpoch = 0;
  let renameReturnFocus = null;
  let deleteReturnFocus = null;
  let deleteTargetId = '';
  let deleteSubmitting = false;
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
  let imageAiDraft = null;
  let imageAiPreviewing = false;
  let imageAiSubmitting = false;
  let imageAiDraftError = '';
  let conversationInput = '';
  let conversationError = '';
  let conversationReviewing = false;
  let commandMenuReturnFocus = null;
  let emptySceneRecoveryPromise = null;
  const activeVideoJobIds = new Set();
  const notifiedVideoJobs = new Set();
  const activeImageAiJobIds = new Set();
  const notifiedImageAiJobs = new Set();

  function setSpatialStatus(text, { kind = '', action = '', actionLabel = '' } = {}) {
    const status = query('#spatial-save-state');
    const button = query('#spatial-recovery-action');
    if (!status) return;
    status.textContent = String(text || '');
    status.dataset.kind = kind;
    status.setAttribute?.('role', kind === 'error' ? 'alert' : 'status');
    status.setAttribute?.('aria-live', kind === 'error' ? 'assertive' : 'polite');
    if (button) {
      button.hidden = !action;
      button.dataset.spatialRecovery = action;
      button.textContent = actionLabel || '重试';
    }
  }

  function setSpatialRecovery(recoveryId, error, overrides = {}, context = {}) {
    const model = recoveryViewModel(recoveryId, error, overrides);
    recoveryContext = { recoveryId, ...context };
    setSpatialStatus(`${model.title} · ${model.cause} · ${model.preservation}`, {
      kind: 'error',
      action: model.action?.value,
      actionLabel: model.action?.label,
    });
    return model;
  }

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
        <div class="spatial-card-actions">
          <button class="spatial-card-action" type="button" data-spatial-rename="${escapeHtml(record.id)}" aria-label="重命名 ${escapeHtml(record.name)}" title="重命名">
            <svg viewBox="0 0 24 24" aria-hidden="true"><path d="m4 20 4.5-1 10-10a2.1 2.1 0 0 0-3-3l-10 10Z"/><path d="m13.5 7.5 3 3"/></svg>
          </button>
          <button class="spatial-card-action is-destructive" type="button" data-spatial-delete="${escapeHtml(record.id)}" aria-label="删除 ${escapeHtml(record.name)}" title="删除">
            <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 7h16M9 7V4h6v3M7 7l1 13h8l1-13"/><path d="M10 11v5M14 11v5"/></svg>
          </button>
        </div>
      </article>
    `).join('');
  }

  function renderLibrary() {
    const list = query('#spatial-canvas-list');
    const empty = query('#spatial-library-empty');
    if (!list || !empty) return;
    const title = query('#spatial-library-empty-title');
    const detail = query('#spatial-library-empty-detail');
    const action = query('#btn-spatial-empty-new');
    if (recordsFailure) {
      const model = recoveryViewModel('spatial-list-read', recordsFailure);
      list.innerHTML = '';
      list.hidden = true;
      empty.hidden = false;
      empty.dataset.kind = 'error';
      if (title) title.textContent = model.title;
      if (detail) {
        detail.hidden = false;
        detail.textContent = `${model.cause}；${model.preservation}`;
      }
      if (action) {
        action.dataset.spatialEmptyAction = model.action.value;
        action.textContent = model.action.label;
      }
      query('#spatial-canvas-count').textContent = '读取失败';
      return;
    }
    const records = adapter.list();
    list.innerHTML = recordsHtml(records);
    list.hidden = records.length === 0;
    empty.hidden = records.length !== 0;
    empty.dataset.kind = records.length ? '' : 'empty';
    if (title) title.textContent = '创建第一张画布';
    if (detail) {
      detail.hidden = true;
      detail.textContent = '';
    }
    if (action) {
      action.dataset.spatialEmptyAction = 'create';
      action.textContent = '新建画布';
    }
    query('#spatial-canvas-count').textContent = `${records.length} 个画布`;
  }

  function syncEditorHeading() {
    const record = currentId ? adapter.get(currentId) : null;
    query('#spatial-current-name').textContent = record?.name || '无限画布';
    query('#btn-spatial-import').hidden = !record;
    query('#btn-spatial-rename').hidden = !record;
    query('#btn-spatial-delete').hidden = !record;
  }

  function openDeleteDialog(id, returnFocus = null) {
    const record = adapter.get(id);
    const dialog = query('#spatial-delete-dialog');
    if (!record || !dialog || deleteSubmitting) return false;
    closeNativeAiCommandMenu({ restoreFocus: false });
    closeRename(false);
    deleteTargetId = record.id;
    deleteReturnFocus = returnFocus || documentRef.activeElement;
    query('#spatial-delete-title').textContent = `删除「${record.name}」？`;
    query('#spatial-delete-detail').textContent = '画布会从当前工作区移除；已有版本、任务、结果与来源证据仍会保留。';
    const error = query('#spatial-delete-error');
    error.hidden = true;
    error.textContent = '';
    dialog.hidden = false;
    windowRef.requestAnimationFrame(() => query('#spatial-delete-cancel')?.focus?.());
    return true;
  }

  function closeDeleteDialog({ restoreFocus = true } = {}) {
    if (deleteSubmitting) return false;
    const dialog = query('#spatial-delete-dialog');
    if (dialog) dialog.hidden = true;
    const target = deleteReturnFocus;
    deleteTargetId = '';
    deleteReturnFocus = null;
    if (restoreFocus) windowRef.requestAnimationFrame(() => target?.focus?.());
    return true;
  }

  function trapDeleteDialogFocus(event) {
    const dialog = query('#spatial-delete-dialog');
    const focusable = Array.from(dialog?.querySelectorAll?.('button:not(:disabled):not([tabindex="-1"])') || []);
    if (!focusable.length) return;
    const first = focusable[0];
    const last = focusable[focusable.length - 1];
    if (event.shiftKey && documentRef.activeElement === first) {
      event.preventDefault();
      last.focus?.();
    } else if (!event.shiftKey && documentRef.activeElement === last) {
      event.preventDefault();
      first.focus?.();
    }
  }

  async function confirmDeleteCanvas() {
    const canvasId = String(deleteTargetId || '');
    const dialog = query('#spatial-delete-dialog');
    if (!canvasId || !dialog || deleteSubmitting) return false;
    const buttons = Array.from(dialog.querySelectorAll?.('button') || []);
    const error = query('#spatial-delete-error');
    deleteSubmitting = true;
    buttons.forEach((button) => {
      button.disabled = true;
      button.setAttribute?.('aria-busy', 'true');
    });
    error.hidden = true;
    error.textContent = '';
    try {
      if (!(await flushCanvasForTransition(canvasId))) {
        throw new Error('画布仍有未保存的修改；已停止删除，请重试');
      }
      const receipt = await adapter.remove(canvasId);
      if (!receipt) throw new Error('画布已不存在；请刷新画布列表');
      clearSceneTimer(canvasId);
      pendingScenes.delete(canvasId);
      savingScenes.delete(canvasId);
      sceneConflicts.delete(canvasId);
      if (currentId === canvasId) {
        openEpoch += 1;
        stopVideoPolling();
        currentCanvasSession = null;
        mountedIsland?.unmount?.();
        mountedIsland = null;
        islandReadyPromise = null;
        resolveIslandReady = null;
        selectedElement = null;
        currentId = '';
        query('#spatial-library').hidden = false;
        query('#spatial-editor').hidden = true;
        query('#btn-spatial-home').hidden = true;
        query('#btn-spatial-import').hidden = true;
        query('#btn-spatial-rename').hidden = true;
        query('#btn-spatial-delete').hidden = true;
        query('#spatial-current-name').textContent = '画布空间';
      }
      renderLibrary();
      deleteSubmitting = false;
      closeDeleteDialog({ restoreFocus: false });
      setSpatialStatus('画布已删除 · 历史任务与结果证据已保留');
      windowRef.requestAnimationFrame(() => (
        query('[data-spatial-open]') || query('#btn-spatial-new')
      )?.focus?.());
      return true;
    } catch (deleteError) {
      error.hidden = false;
      error.textContent = `删除失败：${String(deleteError?.detail?.message || deleteError?.message || deleteError)}`;
      setSpatialStatus('画布删除失败 · 原画布仍保留', { kind: 'error' });
      return false;
    } finally {
      deleteSubmitting = false;
      buttons.forEach((button) => {
        button.disabled = false;
        button.removeAttribute?.('aria-busy');
      });
    }
  }

  async function renderInspector(element) {
    const inspector = query('#spatial-inspector');
    if (!inspector) return;
    const epoch = ++inspectorEpoch;
    const selectionChanged = String(element?.id || '') !== String(selectedElement?.id || '');
    if (selectionChanged) {
      closeNativeAiCommandMenu({ restoreFocus: false });
      videoDraft = null;
      videoDraftError = '';
      imageAiDraft = null;
      imageAiDraftError = '';
      conversationInput = '';
      conversationError = '';
      conversationReviewing = false;
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
      ${!imageAiDraft && !videoDraft && canvasConversationEligible(element, selectedAsset)
        ? canvasConversationHtml(conversationInput, selectedAsset, {
          reviewing: conversationReviewing,
          error: conversationError,
        })
        : ''}
      ${videoDraft?.sourceAssetId === refs.asset_id ? videoDraftHtml(videoDraft, selectedAsset, { submitting: videoSubmitting, error: videoDraftError }) : ''}
      ${imageAiDraft?.sourceAssetId === refs.asset_id ? imageAiDraftHtml(imageAiDraft, selectedAsset, {
        previewing: imageAiPreviewing,
        submitting: imageAiSubmitting,
        error: imageAiDraftError,
      }) : ''}
    `;
    inspector.hidden = false;
  }

  function getCurrentNativeAiActions() {
    const session = captureCanvasSession();
    const editor = query('#spatial-editor');
    if (!active || !session || editor?.hidden || selectedElement?.type !== 'image') return [];
    const refs = selectedElement.customData || {};
    const assetId = String(refs.asset_id || '');
    if (!assetId) return [];
    const action = refs.result_id
      ? (String(refs.result_id) === assetId ? SPATIAL_RESULT_VARIATION_ACTION : '')
      : SPATIAL_WHITE_BACKGROUND_ACTION;
    return action ? [spatialImageAiDefinition(action)] : [];
  }

  function closeNativeAiCommandMenu({ restoreFocus = true } = {}) {
    const menu = query('#spatial-command-menu');
    if (!menu || menu.hidden) return false;
    menu.hidden = true;
    menu.innerHTML = '';
    menu.setAttribute?.('aria-hidden', 'true');
    const returnFocus = commandMenuReturnFocus;
    commandMenuReturnFocus = null;
    if (restoreFocus) {
      windowRef.requestAnimationFrame(() => {
        if (returnFocus?.isConnected !== false) returnFocus?.focus?.({ preventScroll: true });
      });
    }
    return true;
  }

  function openNativeAiCommandMenu() {
    const actions = getCurrentNativeAiActions();
    const menu = query('#spatial-command-menu');
    if (!menu || !actions.length) return false;
    commandMenuReturnFocus = documentRef.activeElement || query('#spatial-canvas-host');
    menu.innerHTML = `
      <div class="spatial-command-menu__scrim" data-spatial-command-close aria-hidden="true"></div>
      <section class="spatial-command-menu__surface">
        <header class="spatial-command-menu__header">
          <div class="spatial-command-menu__heading"><span>CANVAS AI</span><strong id="spatial-command-menu-title">当前选区可用</strong></div>
          <button class="spatial-command-menu__close" type="button" data-spatial-command-close aria-label="关闭 Canvas AI 命令">×</button>
        </header>
        <div class="spatial-command-menu__actions">${actions.map((definition) => `
          <button class="spatial-command-menu__action" type="button" data-spatial-command-action="${escapeHtml(definition.action)}">
            <strong>${escapeHtml(definition.title)}</strong>
            <small>${escapeHtml(definition.sourceKind === 'result' ? '精确使用当前 Result' : '使用当前原始素材')}</small>
            <kbd>Enter</kbd>
          </button>
        `).join('')}</div>
        <footer class="spatial-command-menu__footer"><span>Esc 关闭</span></footer>
      </section>
    `;
    menu.hidden = false;
    menu.setAttribute?.('aria-hidden', 'false');
    windowRef.requestAnimationFrame(() => {
      query('[data-spatial-command-action]')?.focus?.({ preventScroll: true });
    });
    return true;
  }

  async function invokeCurrentNativeAiAction(action) {
    try {
      const definition = getCurrentNativeAiActions().find((item) => item.action === String(action || ''));
      if (!definition) throw new Error('当前选区不支持这个 Canvas Native AI 动作');
      const context = { canvasId: currentId, element: selectedElement };
      closeNativeAiCommandMenu({ restoreFocus: false });
      return await openCanvasAiPreview(definition.action, context);
    } catch (error) {
      closeNativeAiCommandMenu({ restoreFocus: false });
      imageAiDraftError = String(error?.detail?.message || error?.message || error);
      if (imageAiDraft) await renderInspector(selectedElement);
      setSpatialStatus(`Canvas Native AI 上下文未建立 · ${imageAiDraftError}`, { kind: 'error' });
      return null;
    }
  }

  function editableShortcutTarget(target) {
    const tagName = String(target?.tagName || '').toUpperCase();
    return ['INPUT', 'TEXTAREA', 'SELECT'].includes(tagName)
      || Boolean(target?.isContentEditable)
      || Boolean(target?.closest?.('[contenteditable="true"], [contenteditable=""], [role="textbox"]'));
  }

  function blockingShortcutLayer(target) {
    return Boolean(target?.closest?.('[aria-modal="true"], dialog[open], .modal-card, .drawer, #settings-panel.is-open'));
  }

  function trapCommandMenuFocus(event) {
    const menu = query('#spatial-command-menu');
    const focusable = Array.from(menu?.querySelectorAll?.('button:not(:disabled)') || []);
    if (!focusable.length) return;
    const first = focusable[0];
    const last = focusable[focusable.length - 1];
    if (event.shiftKey && documentRef.activeElement === first) {
      event.preventDefault();
      last.focus?.();
    } else if (!event.shiftKey && documentRef.activeElement === last) {
      event.preventDefault();
      first.focus?.();
    }
  }

  function onWorkspaceKeyDown(event) {
    const deleteDialog = query('#spatial-delete-dialog');
    if (!deleteDialog?.hidden) {
      if (event.key === 'Escape' && !deleteSubmitting) {
        event.preventDefault();
        event.stopPropagation?.();
        closeDeleteDialog();
      } else if (event.key === 'Tab') trapDeleteDialogFocus(event);
      return;
    }
    const menu = query('#spatial-command-menu');
    if (!menu?.hidden) {
      if (event.key === 'Escape') {
        event.preventDefault();
        event.stopPropagation?.();
        closeNativeAiCommandMenu();
      } else if (event.key === 'Tab') trapCommandMenuFocus(event);
      else if ((event.ctrlKey || event.metaKey) && !event.altKey && String(event.key).toLowerCase() === 'k') {
        event.preventDefault();
        event.stopPropagation?.();
        closeNativeAiCommandMenu();
      }
      return;
    }
    const conversationField = event.target?.closest?.('[data-spatial-conversation-field]');
    if (
      conversationField
      && String(event.key) === 'Enter'
      && (event.ctrlKey || event.metaKey)
      && !event.altKey
    ) {
      event.preventDefault();
      event.stopPropagation?.();
      submitConversationDraft({ preventDefault() {} });
      return;
    }
    if (
      imageAiDraft
      && event.key === 'Escape'
      && event.target?.closest?.('[data-spatial-image-ai-form]')
      && !imageAiPreviewing
      && !imageAiSubmitting
    ) {
      event.preventDefault();
      event.stopPropagation?.();
      if (imageAiDraft.inputSurface === SPATIAL_CANVAS_CONVERSATION_SURFACE) {
        conversationInput = imageAiDraft.userRequest;
      }
      imageAiDraft = null;
      imageAiDraftError = '';
      renderInspector(selectedElement).then(() => {
        query('[data-spatial-conversation-field]')?.focus?.({ preventScroll: true });
      });
      return;
    }
    if (
      event.defaultPrevented
      || event.repeat
      || !(event.ctrlKey || event.metaKey)
      || event.altKey
      || String(event.key).toLowerCase() !== 'k'
      || editableShortcutTarget(event.target)
      || editableShortcutTarget(documentRef.activeElement)
      || blockingShortcutLayer(event.target)
      || blockingShortcutLayer(documentRef.activeElement)
      || imageAiDraft
      || videoDraft
      || !query('#spatial-rename-form')?.hidden
    ) return;
    if (!getCurrentNativeAiActions().length) return;
    event.preventDefault();
    event.stopPropagation?.();
    openNativeAiCommandMenu();
  }

  function stopVideoPolling({ clearJobs = true, resetRecovery = true } = {}) {
    videoPollEpoch += 1;
    windowRef.clearTimeout(videoPollTimer);
    videoPollTimer = null;
    if (clearJobs) {
      activeVideoJobIds.clear();
      activeImageAiJobIds.clear();
    }
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
    setSpatialStatus(`${permanentVideoRecoveryMessage(error)} · 请在任务中心处理`);
  }

  function scheduleVideoRecovery(session = captureCanvasSession()) {
    windowRef.clearTimeout(videoPollTimer);
    videoPollTimer = null;
    if (!active || !session || !canvasSessionIsCurrent(session)) return;
    if (videoRecoveryAttempt >= VIDEO_RECOVERY_MAX_ATTEMPTS) {
      videoRecoveryPending = false;
      setSpatialStatus('恢复已暂停，重新进入画布或任务中心重试');
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
    if (
      !active
      || !session
      || !canvasSessionIsCurrent(session)
      || (!activeVideoJobIds.size && !activeImageAiJobIds.size)
    ) return;
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
      for (const jobId of [...activeImageAiJobIds]) {
        let job = null;
        try {
          const response = await api.getJob(jobId);
          job = response?.job || response;
          await reconcileImageAiJob(job, { schedule: false, session });
        } catch (error) {
          if (permanentVideoRecoveryError(error)) {
            activeImageAiJobIds.delete(String(jobId));
            permanentError ||= error;
            if (job && spatialImageAiJobIsSettled(job)) {
              await notifyImageAiJobSettled(job);
            }
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

  function imageAiTaskItem(job, session = captureCanvasSession()) {
    const parameters = job?.parameters || job?.snapshot?.parameters || {};
    const sourceAssetId = String(job?.snapshot?.source_asset_ids?.[0] || '');
    const sourceElementId = spatialImageAiSourceElementId(job);
    const sourceElement = currentCanvasElements(session).find((element) => (
      !element?.isDeleted
      && (
        (sourceElementId && String(element?.id || '') === sourceElementId)
        || (!sourceElementId && String(element?.customData?.asset_id || '') === sourceAssetId)
      )
    ));
    return spatialItemFromJob(job, {
      product_profile_version_id: job?.snapshot?.product_profile_version_id
        || sourceElement?.customData?.product_profile_version_id,
      lineage_parent_id: sourceAssetId || parameters.source_asset_id,
    });
  }

  async function persistImageAiJobAssociation(job, session = captureCanvasSession()) {
    const ownerId = spatialImageAiCanvasId(job);
    if (!session || !ownerId || ownerId !== session.canvasId) {
      throw new Error('Canvas Native AI 任务与当前画布的持久关联不一致');
    }
    for (let attempt = 0; attempt < 3; attempt += 1) {
      if (!canvasSessionIsCurrent(session)) return { persisted: false, deferred: true };
      const durableScene = adapter.get(ownerId)?.scene;
      if (sceneHasVideoTask(durableScene, job.id) && sceneHasVideoTask(session.island.getScene?.(), job.id)) {
        return { persisted: true, replayed: attempt > 0 };
      }
      await session.island.addBusinessItemsOnce([imageAiTaskItem(job, session)]);
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
    throw new Error('Canvas Native AI 任务已创建，但画布关联保存失败；返回画布会自动恢复且不会重复调用');
  }

  async function imageAiResultItems(job, session = captureCanvasSession()) {
    if (!session || !canvasSessionIsCurrent(session)) return [];
    const context = taskContext(job?.id, currentCanvasElements(session));
    const resultAssetIds = spatialImageAiResultAssetIds(job);
    if (!resultAssetIds.length) {
      throw videoRecoveryError('Canvas Native AI 任务缺少结果合同', {
        permanent: true,
        code: 'SPATIAL_IMAGE_RESULT_CONTRACT_MISSING',
      });
    }
    const assets = await Promise.all(resultAssetIds.map(async (assetId) => {
      try {
        const response = await api.getAsset(assetId, { timeoutMs: 10000 });
        return response?.asset || response || null;
      } catch (error) {
        if (permanentVideoRecoveryError(error)) {
          throw videoRecoveryError('Canvas Native AI 结果素材已不存在', {
            permanent: true,
            code: 'SPATIAL_IMAGE_RESULT_ASSET_MISSING',
          });
        }
        throw new Error('Canvas Native AI 结果暂不可用，正在自动恢复');
      }
    }));
    return assets.map((asset) => spatialItemFromAsset(asset, {
      kind: 'result',
      result_id: asset.id,
      task_id: job.id,
      product_profile_version_id: job?.snapshot?.product_profile_version_id
        || context.productProfileVersionId,
      lineage_parent_id: asset.lineage_parent_id
        || context.lineageParentId
        || job?.snapshot?.source_asset_ids?.[0],
    }));
  }

  async function reconcileImageAiJob(job, {
    schedule = true,
    session = captureCanvasSession(),
  } = {}) {
    if (!isSpatialImageAiJob(job) || !job?.id) return null;
    if (!session || !canvasSessionIsCurrent(session)) return null;
    const ownerId = spatialImageAiCanvasId(job);
    if (ownerId && ownerId !== session.canvasId) return null;
    if (spatialImageAiJobIsActive(job)) {
      activeImageAiJobIds.add(String(job.id));
    } else {
      activeImageAiJobIds.delete(String(job.id));
    }
    const association = ownerId
      ? await persistImageAiJobAssociation(job, session)
      : null;
    if (!canvasSessionIsCurrent(session) || association?.deferred) return null;
    const taskUpdate = await session.island.updateTask?.(imageAiTaskItem(job, session));
    if (!canvasSessionIsCurrent(session)) return null;
    let inserted = null;
    let resultItems = [];
    if (['completed', 'partial'].includes(String(job.status || ''))) {
      resultItems = await imageAiResultItems(job, session);
      if (!canvasSessionIsCurrent(session)) return null;
      if (resultItems.length) inserted = await session.island.addBusinessItemsOnce(resultItems);
    }
    if (!canvasSessionIsCurrent(session)) return null;
    if (association?.persisted || taskUpdate?.changed || (inserted && !inserted.skipped)) {
      await flushScene(session.canvasId);
    }
    if (!canvasSessionIsCurrent(session)) return null;
    if (resultItems.length) {
      const durableScene = adapter.get(session.canvasId)?.scene;
      if (!sceneHasVideoResults(durableScene, job.id, resultItems)) {
        throw new Error('Canvas Native AI 结果画布关联尚未保存，正在自动恢复');
      }
    }
    if (spatialImageAiJobIsSettled(job)) {
      await notifyImageAiJobSettled(job);
    }
    if (schedule) scheduleVideoPolling(session);
    return inserted;
  }

  async function notifyImageAiJobSettled(job) {
    if (!job?.id || notifiedImageAiJobs.has(String(job.id))) return;
    notifiedImageAiJobs.add(String(job.id));
    await Promise.resolve(onImageAiJobSettled(job)).catch(() => {});
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
          (
            isSpatialVideoJob(job)
            && (!spatialVideoCanvasId(job) || spatialVideoCanvasId(job) === session.canvasId)
          )
          || (
            isSpatialImageAiJob(job)
            && spatialImageAiCanvasId(job) === session.canvasId
          )
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
        if (
          isSpatialImageAiJob(job)
          && spatialImageAiCanvasId(job) === session.canvasId
        ) jobs.set(String(job.id), job);
      });
    } catch (error) {
      if (permanentVideoRecoveryError(error)) permanentError ||= error;
      else recoveryNeeded = true;
    }
    for (const job of jobs.values()) {
      if (!canvasSessionIsCurrent(session)) return;
      try {
        if (isSpatialImageAiJob(job)) {
          await reconcileImageAiJob(job, { schedule: false, session });
        } else {
          await reconcileVideoJob(job, { schedule: false, session });
        }
      } catch (error) {
        if (permanentVideoRecoveryError(error)) {
          permanentError ||= error;
          if (spatialImageAiJobIsSettled(job)) {
            await notifyImageAiJobSettled(job);
          } else if (spatialVideoJobIsSettled(job)) {
            await notifyVideoJobSettled(job);
          }
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

  async function compileImageAiPreview() {
    if (!imageAiDraft || imageAiPreviewing || imageAiSubmitting) return null;
    const sourceElement = selectedElement;
    const submittedDraft = imageAiDraft;
    const session = captureCanvasSession();
    if (!session || submittedDraft.canvasId !== session.canvasId) {
      imageAiDraftError = '当前画布已切换，请重新选择素材';
      await renderInspector(sourceElement);
      return null;
    }
    try {
      imageAiPreviewing = true;
      imageAiDraftError = '';
      await renderInspector(sourceElement);
      const bundle = await api.compileKnowledge(
        spatialImageAiPreviewPayload(submittedDraft),
      );
      if (
        !canvasSessionIsCurrent(session)
        || imageAiDraft !== submittedDraft
        || String(selectedElement?.id || '') !== submittedDraft.sourceElementId
      ) return null;
      imageAiDraft = applySpatialImageAiPreview(submittedDraft, bundle);
      await renderInspector(sourceElement);
      return imageAiDraft.preview;
    } catch (error) {
      if (canvasSessionIsCurrent(session) && imageAiDraft === submittedDraft) {
        imageAiDraftError = String(error?.detail?.message || error?.message || error);
        await renderInspector(sourceElement);
        query('[data-spatial-image-ai-status]')?.focus?.({ preventScroll: true });
      }
      return null;
    } finally {
      imageAiPreviewing = false;
      if (canvasSessionIsCurrent(session) && imageAiDraft) {
        await renderInspector(selectedElement);
      }
    }
  }

  async function openCanvasAiPreview(action, context = {}, seed = {}) {
    const session = captureCanvasSession();
    const requestedCanvasId = String(context?.canvasId || session?.canvasId || '');
    if (!session || requestedCanvasId !== session.canvasId) {
      throw new Error('当前画布已切换，请重新选择素材');
    }
    const element = context?.element || selectedElement;
    const refs = element?.customData || {};
    const sourceElementId = String(element?.id || '');
    const selectionIsCurrent = () => (
      canvasSessionIsCurrent(session)
      && String(selectedElement?.id || '') === sourceElementId
    );
    const exactResult = String(refs.result_id || '') === String(refs.asset_id || '');
    const inputSurface = String(seed?.inputSurface || '');
    const conversation = inputSurface === SPATIAL_CANVAS_CONVERSATION_SURFACE;
    const definition = spatialImageAiDefinition(action, {
      inputSurface,
      sourceResultId: exactResult ? String(refs.result_id || '') : '',
    });
    const sourceMatchesAction = conversation
      ? (!refs.result_id || exactResult)
      : definition.sourceKind === 'source'
        ? !refs.result_id
        : exactResult;
    if (element?.type !== 'image' || !refs.asset_id || !sourceMatchesAction) {
      throw new Error(`${definition.title}当前只接受无限画布中的${definition.sourceLabel}`);
    }
    let asset = selectedAsset;
    if (!asset || String(asset.id) !== String(refs.asset_id)) {
      const response = await api.getAsset(refs.asset_id, { timeoutMs: 10000 });
      asset = response?.asset || response || {};
    }
    if (!selectionIsCurrent()) throw new Error('当前选区已变化，请重新核对修改');
    const roleMatches = conversation
      ? (
        exactResult
          ? String(asset?.role || '').startsWith('result_')
          : asset?.role === 'workspace_source'
      )
      : definition.sourceKind === 'source'
        ? asset?.role === 'workspace_source'
        : String(asset?.role || '').startsWith('result_');
    if (!roleMatches || asset?.kind === 'video' || !String(asset?.mime || '').startsWith('image/')) {
      throw new Error(`${definition.title}当前只接受${definition.sourceLabel}`);
    }
    if (!selectionIsCurrent()) throw new Error('当前选区已变化，请重新核对修改');
    await flushScene(session.canvasId);
    const durableCanvas = adapter.get(session.canvasId);
    if (!durableCanvas?.current_version_id || !selectionIsCurrent()) {
      throw new Error('请等待当前画布保存完成后再创建任务');
    }
    const defaults = await Promise.resolve(getImageAiDefaults({
      action: definition.action,
      canvasId: session.canvasId,
      element,
      asset,
    }));
    if (!selectionIsCurrent()) throw new Error('当前选区已变化，请重新核对修改');
    selectedAsset = asset;
    imageAiDraft = createSpatialImageAiDraft(definition.action, {
      ...defaults,
      canvasId: session.canvasId,
      sourceElementId: String(element.id || ''),
      sourceAssetId: String(refs.asset_id),
      sourceResultId: exactResult ? String(refs.result_id || '') : '',
      productProfileVersionId: String(refs.product_profile_version_id || ''),
      userRequest: String(seed?.userRequest || defaults?.userRequest || ''),
      inputSurface,
    });
    imageAiDraftError = '';
    await renderInspector(element);
    await compileImageAiPreview();
    query('[data-spatial-image-ai-field="userRequest"]')?.focus?.({ preventScroll: true });
    return imageAiDraft;
  }

  async function openCanvasConversationPreview(userRequest = conversationInput, context = {}) {
    const message = String(userRequest || '').trim().replace(/\s+/g, ' ').slice(0, 1200);
    const element = context?.element || selectedElement;
    if (message.length < 2) {
      conversationError = '请写明希望 AI 如何修改当前图片';
      await renderInspector(element);
      query('[data-spatial-conversation-status]')?.focus?.({ preventScroll: true });
      return null;
    }
    if (!canvasConversationEligible(element, selectedAsset)) {
      conversationError = '当前选区已变化，请重新选择一张图片';
      await renderInspector(element);
      return null;
    }
    conversationInput = message;
    conversationError = '';
    conversationReviewing = true;
    await renderInspector(element);
    try {
      return await openCanvasAiPreview(SPATIAL_RESULT_VARIATION_ACTION, {
        canvasId: String(context?.canvasId || currentId),
        element,
      }, {
        inputSurface: SPATIAL_CANVAS_CONVERSATION_SURFACE,
        userRequest: message,
      });
    } catch (error) {
      conversationError = String(error?.detail?.message || error?.message || error);
      if (String(selectedElement?.id || '') === String(element?.id || '')) {
        await renderInspector(element);
        query('[data-spatial-conversation-status]')?.focus?.({ preventScroll: true });
      }
      return null;
    } finally {
      conversationReviewing = false;
      if (!imageAiDraft && String(selectedElement?.id || '') === String(element?.id || '')) {
        await renderInspector(element);
      }
    }
  }

  async function submitConversationDraft(event) {
    event?.preventDefault?.();
    if (conversationReviewing || imageAiDraft || videoDraft) return null;
    return openCanvasConversationPreview(conversationInput, {
      canvasId: currentId,
      element: selectedElement,
    });
  }

  function syncImageAiControls(error = '') {
    const form = query('[data-spatial-image-ai-form]');
    if (!form || !imageAiDraft) return;
    const submit = form.querySelector('button[type="submit"]');
    const status = form.querySelector('[data-spatial-image-ai-status]');
    if (submit) {
      submit.disabled = imageAiPreviewing || imageAiSubmitting;
      submit.textContent = imageAiDraft.preview
        ? `确认并创建任务 · ${imageAiDraft.productProfileVersionId ? '1 次调用' : '最多 2 次调用'}`
        : '重新核对执行上下文';
    }
    if (status) {
      status.textContent = error || (imageAiDraft.preview
        ? '上下文已冻结；点击确认后才会创建正式任务。'
        : '要求已变化，尚未发起 Provider 调用。');
      if (error) status.dataset.error = 'true';
      else delete status.dataset.error;
    }
    const preview = form.querySelector('[data-spatial-image-ai-preview]');
    if (preview && !imageAiDraft.preview) {
      preview.innerHTML = '<p>要求已变化，请重新核对后再提交。</p>';
    }
  }

  function updateImageAiDraftFromField(control) {
    if (!imageAiDraft) return;
    const field = control?.dataset?.spatialImageAiField;
    if (!field) return;
    imageAiDraft = updateSpatialImageAiDraft(imageAiDraft, {
      [field]: control.value,
    });
    imageAiDraftError = '';
    syncImageAiControls();
  }

  async function submitImageAiDraft(event) {
    event.preventDefault();
    if (!imageAiDraft || imageAiPreviewing || imageAiSubmitting) return;
    if (!imageAiDraft.preview) {
      await compileImageAiPreview();
      return;
    }
    const sourceElement = selectedElement;
    const submittedDraft = imageAiDraft;
    const session = captureCanvasSession();
    if (!session || submittedDraft.canvasId !== session.canvasId) {
      imageAiDraftError = '当前画布已切换，请重新核对执行上下文';
      syncImageAiControls(imageAiDraftError);
      return;
    }
    try {
      const submission = spatialImageAiCommandPayload(submittedDraft);
      imageAiDraft = submission.draft;
      imageAiSubmitting = true;
      imageAiDraftError = '';
      await renderInspector(sourceElement);
      const response = await api.executeCommand(
        SPATIAL_IMAGE_AI_COMMAND_ID,
        submission.payload,
        { timeoutMs: 15000 },
      );
      const job = response?.job || response;
      if (
        !job?.id
        || !isSpatialImageAiJob(job)
        || spatialImageAiAction(job) !== submittedDraft.action
      ) {
        throw new Error(`${spatialImageAiDefinition(submittedDraft.action).title}任务返回内容不完整`);
      }
      await Promise.resolve(onImageAiJobSubmitted(job)).catch(() => {});
      if (!canvasSessionIsCurrent(session)) return job;
      await persistImageAiJobAssociation(job, session);
      if (!canvasSessionIsCurrent(session)) return job;
      imageAiDraft = null;
      if (submittedDraft.inputSurface === SPATIAL_CANVAS_CONVERSATION_SURFACE) {
        conversationInput = '';
        conversationError = '';
      }
      await reconcileImageAiJob(job, { session });
      if (!canvasSessionIsCurrent(session)) return job;
      const taskElement = await session.island.selectBusinessReference({ task_id: job.id });
      if (taskElement) await renderInspector(taskElement);
      return job;
    } catch (error) {
      if (canvasSessionIsCurrent(session) && imageAiDraft) {
        const stale = ['EXECUTION_CONTEXT_STALE', 'SPATIAL_EXECUTION_CONTEXT_STALE']
          .includes(String(error?.detail?.code || ''));
        if (stale) {
          imageAiDraft = {
            ...imageAiDraft,
            preview: null,
            requestId: '',
          };
        }
        imageAiDraftError = String(error?.detail?.message || error?.message || error);
        await renderInspector(sourceElement);
        query('[data-spatial-image-ai-status]')?.focus?.({ preventScroll: true });
      }
      return null;
    } finally {
      imageAiSubmitting = false;
      if (canvasSessionIsCurrent(session) && imageAiDraft) {
        syncImageAiControls(imageAiDraftError);
      }
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
    closeNativeAiCommandMenu({ restoreFocus: false });
    renderInspector(null);
    query('#spatial-library').hidden = false;
    query('#spatial-editor').hidden = true;
    query('#btn-spatial-home').hidden = true;
    query('#btn-spatial-import').hidden = true;
    query('#btn-spatial-rename').hidden = true;
    query('#btn-spatial-delete').hidden = true;
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
    setSpatialStatus('检测到异常空场景 · 正在恢复上一版本');
    if (!emptySceneRecoveryPromise) {
      emptySceneRecoveryPromise = Promise.resolve()
        .then(() => {
          if (!canvasSessionIsCurrent(session)) return null;
          return session.island.updateScene(fallback);
        })
        .then(() => {
          if (canvasSessionIsCurrent(session)) {
            setSpatialStatus('已阻止空场景覆盖 · 上一版本已恢复');
          }
        })
        .catch((error) => {
          if (canvasSessionIsCurrent(session)) {
            setSpatialRecovery('spatial-open', error, {
              title: '异常空场景恢复失败',
              cause: String(error?.message || error),
            }, { openId: session.canvasId });
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
    setSpatialStatus('正在保存画布');
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

  function freezeSceneConflict(message, { allowDiscard = false, recoveryAction = '' } = {}) {
    query('#spatial-canvas-host').hidden = true;
    const loading = query('#spatial-editor-loading');
    loading.hidden = false;
    loading.innerHTML = `<strong>${escapeHtml(message)}</strong>${recoveryAction ? `<button type="button" data-spatial-recovery="${escapeHtml(recoveryAction)}">重试保存副本</button>` : ''}${allowDiscard ? '<button type="button" data-spatial-conflict-discard>放弃本地并载入远端</button>' : ''}`;
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
        const model = setSpatialRecovery('spatial-conflict-copy', error, {}, { canvasId: saveId });
        freezeSceneConflict(`${model.title}：${model.cause}。${model.preservation}`, {
          allowDiscard: permanentSceneConflictError(error),
          recoveryAction: model.action.value,
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
      setSpatialStatus(`保存冲突 · 本地修改已另存为「${state.copyRecord.name}」`);
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
        setSpatialStatus('已放弃本地冲突修改 · 已载入远端最新版本');
      }
      return true;
    } catch (error) {
      const model = setSpatialRecovery('spatial-conflict-copy', error, {
        title: '远端版本暂时无法载入',
        action: { label: '重试保存副本', value: 'retry-conflict-copy' },
      }, { canvasId: saveId });
      freezeSceneConflict(`${model.title}：${model.cause}。${model.preservation}`, {
        allowDiscard: true,
        recoveryAction: model.action.value,
      });
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
          setSpatialStatus(record.unchanged
            ? '画布无变化'
            : `已保存 · 版本 ${record.current_revision}`);
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
          setSpatialRecovery('spatial-save', error, {}, { canvasId: saveId });
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
      setSpatialRecovery('spatial-save', error, {
        title: '仍有未保存修改，已阻止退出',
      }, { canvasId: error.canvasIds[0] || currentId });
      throw error;
    }
    return true;
  }

  function ensureRecords(force = false) {
    if (!adapter.load) return Promise.resolve(adapter.list());
    if (!recordsPromise || force) {
      setSpatialStatus('正在读取画布列表');
      recordsPromise = Promise.resolve(adapter.load({ force }))
        .then((records) => {
          recordsFailure = null;
          recoveryContext = null;
          renderLibrary();
          setSpatialStatus(`${records.length} 个画布 · 已同步`);
          return records;
        })
        .catch((error) => {
          recordsPromise = null;
          recordsFailure = error;
          setSpatialRecovery('spatial-list-read', error);
          renderLibrary();
          console.error('Infinite canvas list failed to load', error);
          throw error;
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
      const pending = pendingScenes.get(String(leavingId || ''));
      setSpatialRecovery('spatial-save', new Error('本地修改尚未写入版本账本'), {
        title: '保存失败，已留在当前画布',
      }, { canvasId: pending?.canvasId || leavingId });
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
    setSpatialStatus('正在载入画布');
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
          setSpatialStatus('本次会话 · 已打开');
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
      return true;
    } catch (error) {
      if (epoch !== openEpoch) return;
      resolveIslandReady?.();
      resolveIslandReady = null;
      query('#spatial-editor-loading').hidden = false;
      const model = setSpatialRecovery('spatial-open', error, {}, { openId: id });
      query('#spatial-editor-loading').innerHTML = `<strong>${escapeHtml(model.title)}</strong><p>${escapeHtml(`${model.cause}。${model.preservation}`)}</p><button type="button" data-spatial-recovery="${escapeHtml(model.action.value)}">${escapeHtml(model.action.label)}</button>`;
      console.error('Infinite canvas runtime failed to load', error);
      return false;
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

  async function addBusinessItems(
    items,
    targetSession = null,
    recoveryOptions = {},
    insertionOptions = {},
  ) {
    const normalized = Array.from(items || []).filter(Boolean);
    if (!normalized.length) return null;
    try {
      const session = targetSession || await ensureCanvasForImport();
      if (!canvasSessionIsCurrent(session)) {
        setSpatialStatus('素材已导入；画布已切换，未加入节点');
        return { skipped: true, reason: 'canvas-switched' };
      }
      setSpatialStatus(`${normalized.length} 项已加入 · 正在保存`);
      const result = await session.island.addBusinessItems(normalized, insertionOptions);
      pendingBusinessImport = null;
      return canvasSessionIsCurrent(session) ? result : null;
    } catch (error) {
      const recoveryId = recoveryOptions.recoveryId || 'spatial-import';
      if (!recoveryOptions.external) {
        pendingBusinessImport = { items: normalized, recoveryOptions, insertionOptions };
      }
      setSpatialRecovery(recoveryId, error, recoveryOptions.action
        ? { action: recoveryOptions.action }
        : {}, { canvasId: currentId });
      console.error('Infinite canvas business import failed', error);
      return null;
    }
  }

  async function addBusinessItemsOnce(items) {
    const normalized = Array.from(items || []).filter(Boolean);
    if (!normalized.length) return null;
    try {
      const session = await ensureCanvasForImport();
      setSpatialStatus(`${normalized.length} 项正在核对并保存`);
      const result = await session.island.addBusinessItemsOnce(normalized);
      return canvasSessionIsCurrent(session) ? result : null;
    } catch (error) {
      setSpatialRecovery('spatial-import', error, {
        title: '内容核对失败',
        action: { label: '重新核对', value: 'retry-import-once' },
      });
      pendingBusinessImport = { items: normalized, once: true };
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

  function setImportControlBusy(value) {
    const button = query('#btn-spatial-import');
    if (!button) return;
    button.disabled = Boolean(value);
    if (value) button.setAttribute?.('aria-busy', 'true');
    else button.removeAttribute?.('aria-busy');
  }

  function canvasClientPoint(event) {
    const clientX = Number(event?.clientX);
    const clientY = Number(event?.clientY);
    const host = query('#spatial-canvas-host');
    if (!Number.isFinite(clientX) || !Number.isFinite(clientY) || !host || host.hidden) return null;
    const rect = host.getBoundingClientRect?.();
    if (rect && Number(rect.width) > 0 && Number(rect.height) > 0) {
      if (clientX < rect.left || clientX > rect.right || clientY < rect.top || clientY > rect.bottom) {
        return null;
      }
    }
    return { clientX, clientY };
  }

  function insertionOptionsFor(session, clientPoint) {
    const insertionPoint = clientPoint
      ? session?.island?.scenePointFromClient?.(clientPoint)
      : null;
    return insertionPoint ? { insertionPoint } : {};
  }

  async function importFilesIntoCanvas(fileList, {
    clientPoint = null,
    source = 'button',
  } = {}) {
    const files = Array.from(fileList || []).filter(Boolean);
    if (!files.length) return null;
    setImportControlBusy(true);
    try {
      const targetSession = await ensureCanvasForImport();
      const sourceCopy = source === 'paste' ? '正在粘贴' : source === 'drop' ? '正在导入' : '正在选择';
      setSpatialStatus(`${sourceCopy} ${files.length} 项素材`);
      const items = Array.from(await onImportFiles(files) || []).filter(Boolean);
      if (!items.length) {
        setSpatialStatus('没有可加入画布的图片或视频');
        return null;
      }
      const result = await addBusinessItems(
        items,
        targetSession,
        {},
        insertionOptionsFor(targetSession, clientPoint),
      );
      if (!result?.skipped) pendingFileImport = null;
      return result;
    } catch (error) {
      pendingFileImport = { files, clientPoint, source };
      setSpatialRecovery('spatial-import', error, { title: '素材导入失败' });
      console.error('Infinite canvas file import failed', error);
      return null;
    } finally {
      setImportControlBusy(false);
    }
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
    event.stopImmediatePropagation?.();
    if (hasFileTransfer) {
      setFileDropActive(false);
      if (!files.length) {
        setSpatialStatus('没有读取到可导入的图片或视频');
        return;
      }
      await importFilesIntoCanvas(files, { clientPoint: canvasClientPoint(event), source: 'drop' });
      return;
    }
    setFileDropActive(false);
    try {
      const session = await ensureCanvasForImport();
      await addBusinessItems(
        [parseSpatialDragItem(payload)],
        session,
        {},
        insertionOptionsFor(session, canvasClientPoint(event)),
      );
    }
    catch (error) { console.error('Infinite canvas drop payload was rejected', error); }
  }

  async function onPaste(event) {
    if (!active || event.defaultPrevented) return;
    if (
      editableShortcutTarget(event.target)
      || editableShortcutTarget(documentRef.activeElement)
      || blockingShortcutLayer(event.target)
      || blockingShortcutLayer(documentRef.activeElement)
      || imageAiDraft
      || videoDraft
      || !query('#spatial-rename-form')?.hidden
    ) return;
    const files = spatialClipboardImageFiles(event.clipboardData);
    if (!files.length) return;
    event.preventDefault();
    event.stopPropagation?.();
    event.stopImmediatePropagation?.();
    await importFilesIntoCanvas(files, { source: 'paste' });
  }

  function onDragOver(event) {
    if (!active) return;
    const transferTypes = Array.from(event.dataTransfer?.types || []);
    if (!transferTypes.includes(SPATIAL_DRAG_MIME) && !transferTypes.includes('Files')) return;
    event.preventDefault();
    event.stopPropagation?.();
    event.stopImmediatePropagation?.();
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
    setSpatialStatus('正在新建画布');
    try {
      const record = await adapter.create({ name: `未命名画布 ${ordinal}` });
      renderLibrary();
      return await openCanvas(record.id);
    } catch (error) {
      setSpatialRecovery('spatial-list-read', error, {
        title: '新建画布失败',
        action: { label: '重试新建', value: 'retry-create' },
      });
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
      setSpatialStatus('画布已重命名');
      closeRename();
    } catch (error) {
      setSpatialRecovery('spatial-save', error, {
        title: '画布重命名失败',
        action: { label: '重新保存名称', value: 'retry-rename' },
      });
      console.error('Infinite canvas rename failed', error);
    } finally {
      buttons.forEach((button) => { button.disabled = false; });
    }
  }

  async function retryFileImport() {
    const pending = Array.isArray(pendingFileImport)
      ? { files: pendingFileImport, source: 'button', clientPoint: null }
      : pendingFileImport;
    const files = pending?.files;
    if (!files?.length) return false;
    const result = await importFilesIntoCanvas(files, pending);
    if (!result || result.skipped) return false;
    pendingFileImport = null;
    return true;
  }

  async function runSpatialRecovery(action, button = null) {
    const recoveryAction = String(action || '');
    if (!recoveryAction) return false;
    if (button) {
      button.disabled = true;
      button.setAttribute?.('aria-busy', 'true');
    }
    try {
      if (recoveryAction === 'retry-list') {
        await ensureRecords(true);
        if (active) showLibrary();
        return true;
      }
      if (recoveryAction === 'retry-open') {
        return Boolean(await openCanvas(recoveryContext?.openId || currentId));
      }
      if (recoveryAction === 'retry-save') {
        const canvasId = recoveryContext?.canvasId || currentId;
        await flushScene(canvasId);
        return !pendingScenes.has(String(canvasId || ''));
      }
      if (recoveryAction === 'retry-conflict-copy') {
        const canvasId = String(recoveryContext?.canvasId || currentId || '');
        const conflict = sceneConflicts.get(canvasId);
        return Boolean(conflict && await preserveSceneConflict(canvasId, conflict));
      }
      if (recoveryAction === 'retry-import') {
        if (pendingFileImport) return retryFileImport();
        if (!pendingBusinessImport) return false;
        return Boolean(await addBusinessItems(
          pendingBusinessImport.items,
          null,
          pendingBusinessImport.recoveryOptions,
          pendingBusinessImport.insertionOptions,
        ));
      }
      if (recoveryAction === 'retry-import-once') {
        if (!pendingBusinessImport) return false;
        return Boolean(await addBusinessItemsOnce(pendingBusinessImport.items));
      }
      if (recoveryAction === 'retry-create') return Boolean(await createCanvas());
      if (recoveryAction === 'retry-rename') {
        await submitRename({ preventDefault() {} });
        return true;
      }
      if (recoveryAction === 'retry-spatial-return') {
        return Boolean(await onRecoveryAction(recoveryAction));
      }
      return false;
    } catch (error) {
      console.error('Infinite canvas recovery action failed', recoveryAction, error);
      return false;
    } finally {
      if (button) {
        button.disabled = false;
        button.removeAttribute?.('aria-busy');
      }
    }
  }

  function onClick(event) {
    if (event.target.closest('[data-spatial-delete-cancel]')) return closeDeleteDialog();
    if (event.target.closest('[data-spatial-delete-confirm]')) return confirmDeleteCanvas();
    if (event.target.closest('[data-spatial-command-close]')) {
      return closeNativeAiCommandMenu();
    }
    const commandAction = event.target.closest('[data-spatial-command-action]');
    if (commandAction) {
      return invokeCurrentNativeAiAction(commandAction.dataset.spatialCommandAction);
    }
    if (event.target.closest('[data-spatial-image-ai-cancel]')) {
      const conversation = imageAiDraft?.inputSurface === SPATIAL_CANVAS_CONVERSATION_SURFACE;
      if (conversation) conversationInput = imageAiDraft.userRequest;
      imageAiDraft = null;
      imageAiDraftError = '';
      return renderInspector(selectedElement).then(() => {
        if (conversation) query('[data-spatial-conversation-field]')?.focus?.({ preventScroll: true });
      });
    }
    if (event.target.closest('[data-spatial-image-ai-classic]')) {
      const context = { canvasId: currentId, element: selectedElement };
      const action = imageAiDraft?.action || '';
      imageAiDraft = null;
      imageAiDraftError = '';
      return onImageAiClassic(action, context);
    }
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
      const action = String(inspectorAction.dataset.spatialAction || '');
      const nativeImageAiAction = getCurrentNativeAiActions().some((item) => item.action === action);
      if (nativeImageAiAction) {
        return invokeCurrentNativeAiAction(action);
      }
      return onAction(action, {
        canvasId: currentId,
        element: selectedElement,
      });
    }
    if (event.target.closest('[data-spatial-inspector-close]')) return renderInspector(null);
    const openButton = event.target.closest('[data-spatial-open]');
    if (openButton) return openCanvas(openButton.dataset.spatialOpen);
    const renameButton = event.target.closest('[data-spatial-rename]');
    if (renameButton) return beginRename(renameButton.dataset.spatialRename, renameButton);
    const deleteButton = event.target.closest('[data-spatial-delete]');
    if (deleteButton) return openDeleteDialog(deleteButton.dataset.spatialDelete, deleteButton);
    if (event.target.closest('[data-spatial-conflict-discard]') && currentId) {
      return discardSceneConflict(currentId);
    }
    const recoveryButton = event.target.closest('[data-spatial-recovery]');
    if (recoveryButton) return runSpatialRecovery(recoveryButton.dataset.spatialRecovery, recoveryButton);
  }

  function onInput(event) {
    const conversationField = event.target.closest('[data-spatial-conversation-field]');
    if (conversationField) {
      conversationInput = String(conversationField.value || '').slice(0, 1200);
      conversationError = '';
      return;
    }
    const imageAiControl = event.target.closest('[data-spatial-image-ai-field]');
    if (imageAiControl) return updateImageAiDraftFromField(imageAiControl);
    const control = event.target.closest('[data-spatial-video-field]');
    if (control) updateVideoDraftFromField(control);
  }

  function onChange(event) {
    const imageAiControl = event.target.closest('[data-spatial-image-ai-field]');
    if (imageAiControl) return updateImageAiDraftFromField(imageAiControl);
    const confirmation = event.target.closest('[data-spatial-video-confirm]');
    if (confirmation) return confirmVideoDraft(confirmation);
    const control = event.target.closest('[data-spatial-video-field]');
    if (control) updateVideoDraftFromField(control);
  }

  function onSubmit(event) {
    if (event.target.matches('[data-spatial-conversation-form]')) {
      submitConversationDraft(event);
      return;
    }
    if (event.target.matches('[data-spatial-image-ai-form]')) {
      submitImageAiDraft(event);
      return;
    }
    if (event.target.matches('[data-spatial-video-form]')) submitVideoDraft(event);
  }

  function bind() {
    if (bound) return;
    bound = true;
    query('#btn-spatial-new').addEventListener('click', createCanvas);
    query('#btn-spatial-import').addEventListener('click', () => query('#spatial-file-input').click());
    query('#spatial-file-input').addEventListener('change', async (event) => {
      const input = event.currentTarget;
      const files = Array.from(input.files || []);
      input.value = '';
      await importFilesIntoCanvas(files, { source: 'button' });
    });
    query('#btn-spatial-empty-new').addEventListener('click', (event) => (
      event.currentTarget.dataset.spatialEmptyAction === 'retry-list'
        ? runSpatialRecovery('retry-list', event.currentTarget)
        : createCanvas()
    ));
    query('#btn-spatial-home').addEventListener('click', () => showLibrary({ restoreFocus: true }));
    query('#btn-spatial-rename').addEventListener('click', (event) => beginRename(currentId, event.currentTarget));
    query('#btn-spatial-delete').addEventListener('click', (event) => openDeleteDialog(currentId, event.currentTarget));
    query('#spatial-rename-form').addEventListener('submit', submitRename);
    query('#spatial-rename-cancel').addEventListener('click', () => closeRename());
    query('#spatial-rename-input').addEventListener('keydown', (event) => {
      if (event.key === 'Escape') { event.preventDefault(); closeRename(); }
    });
    query('#page-canvas').addEventListener('click', onClick);
    query('#page-canvas').addEventListener('input', onInput);
    query('#page-canvas').addEventListener('change', onChange);
    query('#page-canvas').addEventListener('submit', onSubmit);
    documentRef.addEventListener('dragover', onDragOver, true);
    documentRef.addEventListener('dragleave', onDragLeave);
    documentRef.addEventListener('drop', onDrop, true);
    documentRef.addEventListener('paste', onPaste, true);
    documentRef.addEventListener('keydown', onWorkspaceKeyDown, true);
    renderLibrary();
  }

  function setPage(isActive) {
    active = Boolean(isActive);
    if (!active) {
      stopVideoPolling();
      mountedIsland?.stopVideo?.();
      flushScene(currentCanvasSession?.canvasId || currentId);
      closeNativeAiCommandMenu({ restoreFocus: false });
      closeDeleteDialog({ restoreFocus: false });
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
    }).catch(() => {
      if (active) showLibrary();
    });
  }

  function destroy() {
    stopVideoPolling();
    flushScene(currentCanvasSession?.canvasId || currentId);
    documentRef.removeEventListener('dragover', onDragOver, true);
    documentRef.removeEventListener('dragleave', onDragLeave);
    documentRef.removeEventListener('drop', onDrop, true);
    documentRef.removeEventListener('paste', onPaste, true);
    documentRef.removeEventListener('keydown', onWorkspaceKeyDown, true);
    closeNativeAiCommandMenu({ restoreFocus: false });
    closeDeleteDialog({ restoreFocus: false });
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
    getCurrentNativeAiActions,
    invokeCurrentNativeAiAction,
    openCanvas,
    openDeleteDialog,
    openCanvasAiPreview,
    openCanvasConversationPreview,
    openVideoComposer,
    openVideoJob,
    prepareForClose,
    setPage,
    showLibrary,
    get active() { return active; },
    get commandMenuOpen() { return !query('#spatial-command-menu')?.hidden; },
    get deleteDialogOpen() { return !query('#spatial-delete-dialog')?.hidden; },
    get videoRecoveryPending() { return videoRecoveryPending; },
    get currentId() { return currentId; },
    get currentRecord() { return currentId ? adapter.get(currentId) : null; },
    get runtimeLoaded() { return Boolean(runtimePromise); },
  };
}
