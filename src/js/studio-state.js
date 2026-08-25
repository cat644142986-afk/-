function modeMap(modeIds, factory) {
  return Object.fromEntries(modeIds.map((mode) => [mode, factory(mode)]));
}

export function createStudioState(modeIds) {
  return {
    currentPage: 'process',
    currentMode: 'single',
    stage: 'empty',
    selectedFiles: [],
    originalDataUrl: '',
    results: null,
    resultTab: 'main',
    viewerIndex: 0,
    compareData: null,
    currentTaskId: '',
    currentSessionId: '',
    currentGenerationId: '',
    knowledgeStatus: null,
    knowledgeBundle: null,
    settings: null,
    lastFeedbackSignal: '',
    assets: [],
    assetsByCollection: { product: [], group: [], cutout: [] },
    assetUrls: new Map(),
    modeSelections: modeMap(modeIds, () => []),
    folderBatches: modeMap(modeIds, () => null),
    modeSnapshots: modeMap(modeIds, () => null),
    workspaceDrafts: modeMap(modeIds, () => null),
    workspaceRevisions: modeMap(modeIds, () => 1),
    workspaceLoaded: new Set(),
    workspaceRequestVersions: modeMap(modeIds, () => 0),
    draftSaveTimers: new Map(),
    draftSaveVersions: modeMap(modeIds, () => 0),
    draftSavesInFlight: new Set(),
    draftSaveQueued: new Set(),
    hydratingWorkspace: false,
    backendReady: false,
    jobs: [],
    jobRuntime: null,
    jobFilter: 'all',
    jobSourceAssets: new Map(),
    knownJobStatuses: new Map(),
    jobsAvailable: true,
    assetsAvailable: true,
    submitting: false,
    importing: false,
    jobPollTimer: null,
    jobDrawerOpen: false,
    exporting: false,
    restoredModes: new Set(),
    knowledgeRequestVersion: 0,
    assetsRequestVersion: 0,
    assetsAbortController: null,
    jobsRequestVersion: 0,
    jobsAbortController: null,
    jobsRenderSignature: '',
    jobActionsInFlight: new Set(),
    jobMutationsInFlight: new Set(),
    pendingSubmission: null,
    sessions: [],
  sessionProjectFilter: 'all',
  sessionShowAll: false,
  sessionPendingKnowledgeCount: 0,
    reviewDecision: '',
  };
}

export function snapshotFromDraft(draft, fallback = {}) {
  const parameters = draft?.parameters || {};
  const brief = draft?.brief || {};
  return {
    brief: brief.user_request || brief.objective || fallback.brief || '',
    model: parameters.model || fallback.model || 'gpt-image-2',
    angle: parameters.angle || fallback.angle || 'auto',
    fidelity: Number(parameters.fidelity ?? fallback.fidelity ?? 40),
    batch: Number(parameters.variations ?? parameters.batch ?? fallback.batch ?? 1),
    platter: parameters.platter || fallback.platter || 'auto',
    refine: parameters.refine ?? fallback.refine ?? true,
    intent_locks: draft?.intent || parameters.intent_locks || fallback.intent_locks || {},
    active_job_id: draft?.active_job_id || null,
    current_generation_id: draft?.current_generation_id || null,
    current_result_asset_id: draft?.current_result_asset_id || null,
    compare_state: draft?.compare_state || {},
    ui_state: draft?.ui_state || {},
    mask_state: draft?.mask_state || {},
  };
}

export function draftPayloadFromSnapshot({ revision, selectedAssetIds, snapshot, brief }) {
  const safe = snapshot || {};
  return {
    expected_revision: Number(revision || 1),
    selected_asset_ids: Array.from(selectedAssetIds || [], String),
    brief: brief || {
      objective: safe.brief || '将产品原图转化为可交付的商业图片',
      user_request: safe.brief || '',
    },
    intent: safe.intent_locks || {},
    parameters: {
      model: safe.model || 'gpt-image-2',
      angle: safe.angle || 'auto',
      fidelity: Number(safe.fidelity ?? 40),
      batch: Number(safe.batch ?? 1),
      variations: Number(safe.batch ?? 1),
      platter: safe.platter || 'auto',
      refine: safe.refine !== false,
      intent_locks: safe.intent_locks || {},
    },
    active_job_id: safe.active_job_id || null,
    current_generation_id: safe.current_generation_id || null,
    current_result_asset_id: safe.current_result_asset_id || null,
    compare_state: safe.compare_state || {},
    ui_state: safe.ui_state || {},
    mask_state: safe.mask_state || {},
  };
}
