import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import path from 'node:path';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..');
const [app, api, assets, config, jobs, knowledge, memory, review, sessions, settings, shell, studioState, html, css, mainRust, tauriConfig] = await Promise.all([
  readFile(path.join(root, 'src/js/app.js'), 'utf8'),
  readFile(path.join(root, 'src/js/api.js'), 'utf8'),
  readFile(path.join(root, 'src/js/studio-assets.js'), 'utf8'),
  readFile(path.join(root, 'src/js/studio-config.js'), 'utf8'),
  readFile(path.join(root, 'src/js/studio-jobs.js'), 'utf8'),
  readFile(path.join(root, 'src/js/studio-knowledge.js'), 'utf8'),
  readFile(path.join(root, 'src/js/studio-memory.js'), 'utf8'),
  readFile(path.join(root, 'src/js/studio-review.js'), 'utf8'),
  readFile(path.join(root, 'src/js/studio-sessions.js'), 'utf8'),
  readFile(path.join(root, 'src/js/studio-settings.js'), 'utf8'),
  readFile(path.join(root, 'src/js/studio-shell.js'), 'utf8'),
  readFile(path.join(root, 'src/js/studio-state.js'), 'utf8'),
  readFile(path.join(root, 'src/index.html'), 'utf8'),
  readFile(path.join(root, 'src/css/stable-ui.css'), 'utf8'),
  readFile(path.join(root, 'src-tauri/src/main.rs'), 'utf8'),
  readFile(path.join(root, 'src-tauri/tauri.conf.json'), 'utf8'),
]);

test('task dock filters all workflows and returns to immutable task context', () => {
  assert.match(html, /data-job-filter="all"/);
  assert.match(html, /data-job-filter="multi-file"/);
  assert.match(html, /data-job-filter="group-split"/);
  assert.match(html, /data-job-filter="cutout-batch"/);
  assert.match(jobs, /jobWorkspaceSnapshot/);
  assert.match(jobs, /jobSourceIds/);
  assert.match(app, /hydrateJobSourceAssets/);
  assert.match(app, /data-job-action="open-workspace"/);
  assert.match(app, /async function openJobWorkspace/);
  assert.match(app, /await openJobWorkspace\(job, false\)/);
  assert.match(css, /\.job-filters/);
});

test('multi-file restores twenty sources and blocks plans above twenty-four outputs', () => {
  assert.match(config, /'multi-file':[\s\S]*?maxFiles: 20/);
  assert.match(app, /multiFileOutputPlan\(count, batch\)/);
  assert.match(app, /单批最多 \$\{plan\.maxOutputs\}/);
  assert.match(app, /aria-invalid/);
});

test('multi-file accepts a durable folder queue and splits it into safe concurrent jobs', () => {
  assert.match(html, /id="folder-source"[^>]*hidden/);
  assert.match(html, /id="folder-path"/);
  assert.match(html, /id="btn-folder-browse"/);
  assert.match(html, /id="btn-folder-load"/);
  assert.match(api, /export async function importFolderSources\(folderPath\)/);
  assert.match(app, /await API\.importFolderSources\(folderPath\)/);
  assert.match(app, /Math\.floor\(24 \/ variations\)/);
  assert.match(app, /part_index: index \+ 1/);
  assert.match(app, /responses\.length > 1/);
  assert.match(studioState, /folderBatches: modeMap\(modeIds, \(\) => null\)/);
  assert.match(css, /\.folder-source \{/);
});

test('asset management exposes discoverable safe removal, undo, recycle, restore, and references', () => {
  assert.match(html, /id="btn-asset-manager"/);
  assert.match(html, /id="asset-drawer"/);
  assert.match(html, /data-asset-view="active"/);
  assert.match(html, /data-asset-view="trash"/);
  assert.match(app, /createAssetManagerController/);
  assert.match(assets, /removeAssetFromCollectionSelections/);
  assert.match(assets, /api\.restoreAssetToCollection/);
  assert.match(assets, /api\.getAssetReferences/);
  assert.match(assets, /filterAndSortAssets/);
  assert.match(assets, /visibleLimit \+= ASSET_PAGE_SIZE/);
  assert.match(assets, /api\.reorderCollectionAssets/);
  assert.match(assets, /api\.purgeAsset/);
  assert.match(html, /id="asset-manager-search"/);
  assert.match(html, /id="asset-bulk-action"/);
  assert.match(assets, /label: '撤销'/);
  assert.match(css, /\.drawer--assets/);
});

test('settings and knowledge connection behavior lives outside the page orchestrator', () => {
  assert.match(app, /createSettingsController/);
  assert.match(app, /settingsController\.bind\(\)/);
  assert.match(app, /settingsController\.renderKnowledgeStatus\(status\)/);
  assert.doesNotMatch(app, /function (loadSettings|renderKnowledgeStatus|saveSettings|reloadKnowledge|checkBalance)/);
  assert.match(settings, /normalizeSettingsPayload/);
  assert.match(settings, /knowledgeStatusCopy/);
  assert.match(settings, /if \(bound\) return/);
  assert.match(html, /<h2>唯一知识库<\/h2>/);
  assert.match(html, /<label for="setting-knowledge-path">唯一知识库主路径<\/label>/);
  assert.match(html, /id="setting-knowledge-path"[^>]*readonly[^>]*aria-readonly="true"/);
  assert.match(html, /<label for="setting-output-root">交付文件目录<\/label>/);
  assert.match(html, /id="setting-output-root"[^>]*readonly[^>]*aria-readonly="true"/);
  assert.match(html, /id="btn-select-output-root"/);
  assert.match(html, /id="output-root-status"[^>]*aria-live="polite"/);
  assert.match(settings, /api\.selectFolder\(\)/);
  assert.match(settings, /api\.saveSettings\(\{ output_root: selected \}\)/);
  assert.match(css, /\.output-root-field/);
  assert.match(html, /<h2>本地智能选物（可选）<\/h2>/);
  assert.match(html, /id="setting-grounding-runtime-root"[^>]*readonly/);
  assert.match(html, /id="setting-grounding-model-root"[^>]*readonly/);
  assert.match(html, /id="btn-verify-grounding-pack"/);
  assert.match(settings, /groundingPackStatusCopy/);
  assert.match(settings, /api\.verifyGroundingPack\(\)/);
  assert.match(css, /\.grounding-pack-status\.is-error/);
});

test('job submission captures an immutable draft before any knowledge await', () => {
  const draft = app.indexOf('const submissionDraft = captureSubmissionDraft()');
  const compile = app.indexOf('payload = await compileSubmissionPayload(submissionDraft)');
  const post = app.indexOf('await API.createJob(payload)');
  assert.ok(draft >= 0 && compile > draft && post > compile);
  assert.match(app, /persistPendingSubmission\(\{ fingerprint, requestId, payload \}\)/);
  assert.match(app, /clearPendingSubmission\(payload\.client_request_id\)/);
  assert.match(app, /if \(!savedDraft\) savedDraft = await flushWorkspaceDraft\(submissionDraft\.mode, false\)/);
  assert.match(app, /当前工作草稿未能安全保存/);
  assert.match(app, /output_root: String\(state\.settings\?\.output_root/);
});

test('asset import writes selection back to the mode that initiated the async import', () => {
  const capture = app.indexOf('const importMode = state.currentMode');
  const request = app.indexOf('await API.importAssets(valid, MODE_CONFIG[importMode].collection)');
  const assignment = app.indexOf('state.modeSelections[importMode] = selectionAfterImport');
  assert.ok(capture >= 0 && request > capture && assignment > request);
  assert.match(app, /await loadWorkspace\(importMode, true\)/);
  assert.match(app, /MODE_CONFIG\[state\.currentMode\]\?\.collection === collection/);
});

test('workspace drafts are restored and saved through durable scoped APIs', () => {
  assert.match(api, /export async function getWorkspace\(mode/);
  assert.match(api, /export async function saveWorkspaceDraft\(mode, payload/);
  assert.match(app, /await API\.getWorkspace\(mode/);
  assert.match(app, /await API\.saveWorkspaceDraft\(mode, draftSavePayload\(mode\)\)/);
  assert.match(app, /state\.modeSnapshots\[submissionDraft\.mode\] = \{ \.\.\.modeSnapshot, active_job_id: jobId \}/);
  assert.match(studioState, /assetsByCollection: \{ product: \[\], group: \[\], cutout: \[\] \}/);
  assert.doesNotMatch(app, /selectedAssetIds:\s*state\.modeSelections/);
  assert.match(app, /document\.visibilityState === 'hidden'/);
  assert.match(app, /\$\('#param-model'\)\.addEventListener\('change', updateQuickControls\)/);
  assert.match(app, /state\.hydratingWorkspace = true;[\s\S]*?restoreModeSnapshot\(mode\);[\s\S]*?updateQuickControls\(\);[\s\S]*?state\.hydratingWorkspace = false;/);
  assert.match(app, /if \(JSON\.stringify\(previous\) !== JSON\.stringify\(next\)\) scheduleWorkspaceDraftSave\(mode\)/);
});

test('durable job polling has timeout, cancellation, stale-response guard, and stable rendering', () => {
  assert.match(api, /DEFAULT_TIMEOUT_MS/);
  assert.match(api, /fetchWithTimeout/);
  assert.match(app, /jobsAbortController/);
  assert.match(app, /requestVersion !== state\.jobsRequestVersion/);
  assert.match(app, /jobsRenderSignature/);
  assert.match(app, /restoreJobListView/);
  assert.doesNotMatch(app, /API\.startPolling/);
  assert.doesNotMatch(app, /state\.generating/);
  assert.match(html, /QUEUE COMPLETION/);
  assert.match(app, /成功率/);
  assert.match(app, /const itemProgressCopy = \['failed', 'interrupted', 'canceled'\]\.includes\(item\.status\)/);
  assert.match(app, /const progressCopy = \['failed', 'partial', 'interrupted', 'canceled'\]\.includes\(job\.status\)/);
  assert.match(app, /queueCompletionProgress\(progressScope\)/);
});

test('durable jobs expose pause and resume controls with per-job mutation locking', () => {
  assert.match(api, /export async function pauseJob\(jobId\)/);
  assert.match(api, /export async function resumeJob\(jobId\)/);
  assert.match(config, /paused: \{ label: '已暂停', tone: 'paused' \}/);
  assert.match(app, /jobLifecycleActions\(job\.status\)/);
  assert.match(app, /data-job-action="pause"/);
  assert.match(app, /data-job-action="resume"/);
  assert.match(app, /state\.jobMutationsInFlight\.has\(jobId\)/);
  assert.match(app, /await API\.pauseJob\(jobId\)/);
  assert.match(app, /await API\.resumeJob\(jobId\)/);
  assert.match(app, /job\.status === 'paused' && \(job\.items \|\| \[\]\)\.some\(\(item\) => item\.status === 'running'\)/);
  assert.match(css, /\.job-status--paused/);
  assert.match(css, /\.job-dock-dot\.paused/);
});

test('job dialog does not repeatedly announce or focus its backdrop', () => {
  assert.match(html, /id="job-drawer"[\s\S]*?class="drawer-backdrop"[^>]*tabindex="-1"/);
  assert.match(html, /id="job-list" aria-live="off"/);
  assert.match(html, /id="job-status-announcer" role="status" aria-live="polite"/);
  assert.match(app, /openLayer\.id === 'semantic-selection-modal'/);
  assert.match(app, /\$\('\.semantic-modal-card', openLayer\)/);
  assert.match(app, /openLayer\.id === 'img-modal'/);
  assert.match(app, /\$\('\.modal-card', openLayer\)/);
});

test('unsupported Folder action is absent and export handles all result roles independently', () => {
  assert.doesNotMatch(html, /btn-open-folder|>Folder</);
  assert.doesNotMatch(app, /openOutputFolder|btn-open-folder/);
  assert.match(app, /function getAllResultItems/);
  assert.match(app, /processResultItems\(items/);
  assert.match(html, /id="btn-save-all"[^>]*>导出全部</);
  assert.match(css, /\.result-actions \{[^}]*repeat\(2,1fr\)/);
});

test('production shell uses DWM system corners without a hard-clipped resize region', () => {
  assert.doesNotMatch(html, /class="rail-button"[^>]*data-page="compare"/);
  assert.match(html, /id="btn-open-compare"/);
  assert.doesNotMatch(html, /class="studio-meta-grid"/);
  assert.match(html, /class="studio-context-panel workspace-announcer"/);
  assert.doesNotMatch(html, /studio-review-panel|终稿候选已准备/);
  assert.match(css, /\.studio-grid \{[^}]*grid-template-columns: minmax\(0, 1\.55fr\) minmax\(310px, \.9fr\)[^}]*grid-template-rows: minmax\(330px, 1fr\) 160px/);
  assert.match(css, /--radius-panel: 27px/);
  assert.match(css, /--radius-card: 19px/);
  assert.match(css, /--radius-control: 14px/);
  assert.doesNotMatch(css, /--radius-shell/);
  assert.match(css, /\.app-shell \{[^}]*border-radius: 0/);
  assert.match(mainRust, /DWMWA_WINDOW_CORNER_PREFERENCE/);
  assert.match(mainRust, /DWMWA_BORDER_COLOR/);
  assert.match(mainRust, /let border_color = 0xFFFF_FFFEu32/);
  assert.match(mainRust, /DWMWCP_ROUND/);
  assert.doesNotMatch(mainRust, /WINDOW_CORNER_RADIUS_LOGICAL/);
  assert.doesNotMatch(mainRust, /CreateRoundRectRgn/);
  assert.match(mainRust, /SetWindowRgn/);
  assert.match(tauriConfig, /"shadow": false/);
  assert.match(tauriConfig, /"backgroundColor": "#F4F1EB"/);
  assert.match(css, /\.canvas-card,[^}]*box-shadow: none/);
  assert.match(css, /\.task-dock \{[^}]*grid-row: 1 \/ 3/);
  assert.match(css, /\.rail-cluster \{[^}]*border-radius: 29px;[^}]*background: var\(--paper\)/);
  assert.match(css, /\.traffic-light \{[^}]*width: 28px; height: 28px/);
  assert.match(css, /\.traffic-light::before \{[^}]*width: 12px; height: 12px/);
  assert.match(html, /<strong>设计依据<\/strong>/);
  assert.match(html, /<strong>任务中心<\/strong>/);
  assert.doesNotMatch(html, />Studio<\/span>/);
  assert.match(css, /\.canvas-empty::before, \.canvas-empty::after \{ display: none; \}/);
});

test('desktop startup paints an embedded shell before waiting for the sidecar', () => {
  assert.match(html, /id="boot-shell"[^>]*aria-busy="true"/);
  assert.match(html, /id="boot-retry"/);
  assert.match(app, /reportStartupMilestone\('first-paint'\)/);
  assert.match(app, /const deadline = performance\.now\(\) \+ 45000/);
  const setupIndex = mainRust.indexOf('.setup(move |app|');
  const spawnIndex = mainRust.indexOf('std::thread::spawn', setupIndex);
  const waitIndex = mainRust.indexOf('wait_for_server(port, 45)', spawnIndex);
  assert.ok(setupIndex >= 0 && spawnIndex > setupIndex && waitIndex > spawnIndex);
});

test('desktop detects an exited sidecar and reconnects through a single native restart gate', () => {
  assert.match(mainRust, /sidecar_starting: AtomicBool/);
  assert.match(mainRust, /fn ensure_python_sidecar\(/);
  assert.match(mainRust, /child\.try_wait\(\)/);
  assert.match(mainRust, /compare_exchange\(false, true/);
  assert.match(mainRust, /get_api_port, ensure_python_sidecar, report_startup_milestone/);
  assert.match(api, /invoke\('ensure_python_sidecar'\)/);
  assert.match(api, /API_BASE = 'http:\/\/127\.0\.0\.1:' \+ port/);
  assert.match(app, /setBackendStatus\('connecting', '重连中'\)/);
  assert.match(app, /const recovered = await connectBackend\(\)/);
  assert.match(app, /assetsLoaded !== true \|\| jobsLoaded !== true/);
});

test('workspace completion refreshes one stale revision without changing the idempotency key', () => {
  assert.match(app, /async function completeCurrentWorkspace\(\)/);
  assert.match(app, /error\?\.detail\?\.code !== 'DRAFT_REVISION_CONFLICT'/);
  assert.match(app, /state\.workspaceRevisions\[mode\] = Number\(current\.revision/);
  assert.match(app, /response = await API\.completeWorkspace\(mode, \{[\s\S]*?\.\.\.payload,[\s\S]*?expected_revision: state\.workspaceRevisions\[mode\]/);
  assert.match(app, /requestId: createClientRequestId\(\)/);
});

test('growth page projects real knowledge, evidence, review, and motion without demo business content', () => {
  assert.match(html, /id="memory-dna-map"/);
  assert.match(html, /id="memory-rule-count"/);
  assert.match(html, /id="memory-trace"/);
  assert.match(html, /正式知识、创作现场与终稿反馈各自保留来源/);
  assert.doesNotMatch(html, /牛油果|AVOCADO/);
  assert.match(app, /createMemoryProjectionController/);
  assert.match(app, /createKnowledgeController/);
  assert.match(app, /knowledgeController\.load\(\)/);
  assert.match(app, /knowledgeController\.bind\(\)/);
  assert.match(knowledge, /memoryProjectionController\.render\(ledger, pendingSuggestions, knowledgeStatus\)/);
  assert.match(app, /memoryProjectionController\.bind\(\)/);
  assert.doesNotMatch(app, /function (selectMemoryNode|replayMemoryMotion|renderMemoryProjection)/);
  assert.match(memory, /function render\(ledger, suggestions, knowledgeStatus\)/);
  assert.match(app, /API\.getKnowledgeStatus\(\)/);
  assert.match(memory, /function replayMotion\(\)/);
  assert.match(memory, /未批准前不参与未来生成/);
  assert.match(html, /id="btn-feedback-suggestion"[^>]*hidden/);
  assert.match(api, /export async function getMemorySuggestion\(id\)/);
  assert.match(app, /knowledgeController\.openSuggestion/);
  assert.doesNotMatch(app, /function (openMemorySuggestion|updateMemoryFilterControls|memoryCardMarkup|renderMemoryQueue|performMemoryGovernanceAction|loadMemory)/);
  assert.match(knowledge, /data-memory-source/);
  assert.match(app, /locateResultVersion\(state\.results, source\.result_asset_id\)/);
  assert.match(css, /@keyframes memoryDrawLine/);
  assert.match(css, /@keyframes memoryNodeReveal/);
  assert.match(css, /@keyframes memoryTraceReveal/);
  assert.match(css, /@media \(prefers-reduced-motion: reduce\)/);
});

test('narrow windows turn workflow controls into an accessible drawer instead of squeezing the stage', () => {
  assert.match(html, /id="btn-workflow-drawer"[^>]*aria-controls="settings-panel"[^>]*aria-expanded="false"/);
  assert.match(html, /id="task-dock-backdrop"[^>]*hidden/);
  assert.match(css, /@media \(max-width: 980px\)[\s\S]*?\.studio-grid \{[^}]*grid-template-columns: minmax\(0,1fr\)/);
  assert.match(css, /\.task-dock\.is-open \{ opacity: 1; pointer-events: auto; transform: translateX\(0\); \}/);
  assert.match(shell, /panel\.toggleAttribute\('inert', presentation\.inert\)/);
  assert.match(shell, /panel\.setAttribute\('role', presentation\.role\)/);
  assert.match(shell, /media\.addEventListener\('change', sync\)/);
  assert.match(app, /if \(\$\('#settings-panel'\)\.classList\.contains\('is-open'\)\) workflowDock\.close\(\)/);
  assert.doesNotMatch(app, /closeWorkflowDock|openWorkflowDock|syncWorkflowDockLayout|compactWorkflowDock/);
});

test('narrow growth filters use a stable two-column grid without horizontal scrolling', () => {
  assert.match(html, /class="memory-filter-tabs"[^>]*role="tablist"/);
  assert.match(css, /@media \(max-width: 980px\)[\s\S]*?\.memory-filter-tabs \{[^}]*display: grid;[^}]*grid-template-columns: repeat\(2,minmax\(0,1fr\)\);[^}]*overflow: visible/);
  assert.match(css, /@media \(max-width: 980px\)[\s\S]*?\.memory-filter-tabs button \{[^}]*width: 100%;[^}]*min-width: 0/);
});

test('primary studio copy uses readable type tokens instead of shrinking every label', () => {
  assert.match(css, /--type-caption: 11px/);
  assert.match(css, /--type-control: 13px/);
  assert.match(css, /--type-body: 14px/);
  assert.match(css, /\.creative-command input \{[^}]*font-size: var\(--type-body\)/);
  assert.match(css, /\.mode-button strong \{[^}]*font-size: var\(--type-control\)/);
  assert.match(css, /\.dock-field select,[^}]*font-size: var\(--type-control\)/);
  assert.match(css, /\.canvas-empty p \{[^}]*font-size: var\(--type-body\)/);
});

test('production sessions, task center, and result review use the approved information architecture', () => {
  assert.match(html, /class="sessions-layout"/);
  assert.match(html, /id="history-project-filter"/);
  assert.match(html, /id="history-session-count"/);
  assert.match(html, /id="history-timeline"/);
  assert.match(html, /DURABLE TASK CENTER/);
  assert.match(html, /id="job-summary-completed"/);
  assert.match(html, /id="job-runtime"/);
  assert.match(html, /class="review-workspace"/);
  assert.match(html, /data-review-decision="adopted"/);
  assert.match(html, /id="compare-target"/);
  assert.match(html, /id="review-reason-tags"/);
  assert.match(html, /id="review-guide"/);
  assert.match(app, /createSessionsController/);
  assert.match(app, /sessionsController\.bind\(\)/);
  assert.match(app, /sessionsController\.load\(\)/);
  assert.doesNotMatch(app, /function (renderSessionsDashboard|openSessionFromHistory|loadSessions)/);
  assert.match(sessions, /function render\(\)/);
  assert.match(sessions, /async function open\(sessionId\)/);
  assert.match(app, /function jobFailureCopy\(item\)/);
  assert.match(app, /PRODUCT_DETECTION_FAILED: '商品识别返回格式异常，尚未开始生图/);
  assert.match(app, /PERMANENT_JOB_ERRORS/);
  assert.match(app, /API\.getJobRuntime/);
  assert.match(api, /export async function getJobRuntime/);
  assert.match(css, /\.sessions-project-visual/);
  assert.match(css, /\.job-runtime/);
  assert.match(css, /\.review-decision-panel/);
});

test('core production status copy remains Chinese-first', () => {
  assert.match(sessions, /completed: '已完成'/);
  assert.match(app, /INVALID_SOURCE_IMAGE: '源文件已经损坏/);
  assert.match(app, /只重试失败项/);
  assert.doesNotMatch(app, /\$\{session\.status \|\| 'draft'\}/);
  assert.doesNotMatch(html, />Compare</);
});

test('result, settings, memory, and job controls keep readable core copy', () => {
  assert.match(css, /\.result-tab \{[^}]*font-size: var\(--type-caption\)/);
  assert.match(css, /\.feedback-choice, \.feedback-send \{[^}]*font-size: var\(--type-caption\)/);
  assert.match(css, /\.settings-card > label,[^}]*font-size: var\(--type-caption\)/);
  assert.match(css, /\.memory-item p \{[^}]*font-size: var\(--type-caption\)/);
  assert.match(css, /\.job-card > footer button \{[^}]*font-size: var\(--type-caption\)/);
  assert.match(css, /@media \(max-width: 980px\)[\s\S]*?\.settings-layout \{[^}]*overflow-y: auto/);
});

test('workspace restores only an explicit durable result cursor and exposes recoverable asset removal', () => {
  assert.match(app, /await restoreWorkspaceResult\(mode, payload\)/);
  assert.match(app, /selectRestorableResult\(payload\.jobs, payload\?\.draft \|\| \{\}\)/);
  assert.match(app, /current_result_asset_id: selectedItem\?\.asset_id \|\| null/);
  assert.match(api, /export async function removeAssetFromCollection\(collection, assetId\)/);
  assert.match(app, /data-remove-asset-id=/);
  assert.match(assets, /await api\.removeAssetFromCollection\(targetCollection, assetId\)/);
  assert.match(assets, /已移入当前域回收站/);
  assert.match(app, /asset\.url = asset\.content_url \|\| asset\.url \|\| ''/);
  assert.match(app, /return item\.content_url \|\| item\.url \|\| ''/);
});

test('quick cutout does not pretend to understand a semantic brief or knowledge rules', () => {
  assert.match(html, /id="cutout-capability"[^>]*hidden/);
  assert.match(html, /当前不理解物体名称、数量/);
  assert.match(app, /\$\('#creative-command'\)\.hidden = quickCutout/);
  assert.match(app, /\$\('#field-intent'\)\.hidden = quickCutout/);
  assert.match(app, /if \(mode === 'cutout-batch'\) \{[\s\S]*?renderCutoutCapability\(\);[\s\S]*?return null/);
  assert.match(app, /本地分割 · 不读取文字描述/);
  assert.match(app, /else \{[\s\S]*?state\.knowledgeBundle = null;[\s\S]*?renderKnowledge\(null\);/);
  assert.match(app, /if \(!bundle\) \{[\s\S]*?knowledge-summary'\)\.textContent = '等待知识编译'/);
});

test('semantic cutout requires visible name, count, region confirmation, and keeps a keyboard alternative', () => {
  assert.match(html, /data-cutout-strategy="semantic"[^>]*aria-pressed="false">智能选物/);
  assert.match(html, /<label for="semantic-query">/);
  assert.match(html, /id="semantic-model-query"[^>]*placeholder="例如：hamburger"/);
  assert.match(html, /id="semantic-count"[^>]*min="1"[^>]*max="8"/);
  assert.match(html, /id="semantic-selection-modal"[^>]*role="dialog"[^>]*aria-modal="true"/);
  assert.match(html, /id="semantic-region-list"/);
  assert.match(html, /id="semantic-grounding-status"[^>]*role="status"/);
  assert.match(html, /id="semantic-add-full"/);
  assert.match(html, /data-semantic-tool="include"[^>]*>保留画笔/);
  assert.match(html, /data-semantic-tool="exclude"[^>]*>删除画笔/);
  assert.match(html, /id="semantic-mask-preview"[^>]*>生成蒙版预览/);
  assert.match(html, /id="semantic-brush-size"[^>]*type="range"/);
  assert.match(html, /id="semantic-mask-point-include"[^>]*>添加保留点/);
  assert.match(html, /id="semantic-mask-point-exclude"[^>]*>添加删除点/);
  assert.match(app, /API\.previewSemanticCutout/);
  assert.match(app, /API\.previewSemanticCutoutMask/);
  assert.match(app, /model_query: selection\.model_query_override/);
  assert.match(app, /model_query_override: semanticCanvasState\.modelQueryOverride/);
  assert.match(app, /API\.confirmSemanticCutout/);
  assert.match(app, /cutout_selection: semanticCutoutPayload/);
  assert.match(api, /export async function previewSemanticCutout/);
  assert.match(api, /SEMANTIC_GROUNDING_TIMEOUT_MS = 180000/);
  assert.match(api, /previewSemanticCutout[\s\S]*timeoutMs: SEMANTIC_GROUNDING_TIMEOUT_MS/);
  assert.match(api, /export async function confirmSemanticCutout/);
  assert.match(api, /export async function previewSemanticCutoutMask/);
  assert.match(api, /semantic-cutout\/mask-preview/);
  assert.match(css, /\.semantic-region-coordinates/);
  assert.match(css, /\.semantic-mask-tools/);
  assert.match(app, /previewSemanticCutoutMask\([\s\S]{0,500}mask_edits: semanticCanvasState\.maskEdits/);
  assert.match(app, /confirmSemanticCutout\([\s\S]{0,500}mask_edits: semanticCanvasState\.maskEdits/);
  assert.match(app, /正在运行本地目标定位；无需等待，可直接手动框选/);
  assert.match(app, /自动结果不会覆盖你的修改/);
  assert.match(app, /semantic-region-list'\)\.addEventListener\('input'/);
});

test('output canvas ratio and resolution are real durable controls instead of static square copy', () => {
  assert.match(html, /id="param-output-ratio"[\s\S]*?value="original" selected/);
  assert.match(html, /id="param-output-resolution"[\s\S]*?value="4k"/);
  assert.match(html, /id="result-dimensions"/);
  assert.doesNotMatch(html, /<small>SIZE<\/small><strong>2048²<\/strong>/);
  assert.match(app, /output_ratio: \$\('#param-output-ratio'\)\.value/);
  assert.match(app, /output_resolution: \$\('#param-output-resolution'\)\.value/);
  assert.match(app, /snapshot\.output_ratio/);
  assert.match(studioState, /output_ratio: parameters\.output_ratio/);
  assert.match(jobs, /output_resolution: parameters\.output_resolution/);
  assert.match(app, /actual回传像素|实际回传像素/);
});

test('generation flow exposes an honest per-task single or double pass choice', () => {
  assert.match(html, /<label for="param-generation-strategy">/);
  assert.match(html, /id="param-generation-strategy"[^>]*aria-describedby="strategy-help"/);
  assert.match(html, /value="legacy_double_pass"[^>]*>完整双阶段/);
  assert.match(html, /value="single_pass"[^>]*>快速单次/);
  assert.match(html, /id="strategy-reason">2 次调用/);
  assert.match(app, /generation_strategy: getGenerationStrategy\(mode\)/);
  assert.match(app, /generation_strategy_source: 'user'/);
  assert.match(app, /strategy-reason/);
  assert.match(studioState, /generation_strategy: parameters\.generation_strategy/);
  assert.match(css, /\.dock-field-row \{[^}]*grid-template-columns:/);
});

test('material routing is an explicit durable choice with safe defaults', () => {
  assert.match(html, /<label for="param-material-profile">主要材质/);
  assert.match(html, /id="param-material-profile"[^>]*aria-describedby="material-profile-help"/);
  assert.match(html, /value="unknown" selected>未指定 · 稳定基线/);
  assert.match(html, /id="param-compact-prompt"[^>]*aria-describedby="material-route-status"[^>]*disabled/);
  assert.match(app, /material_profile: getMaterialProfile\(mode\)/);
  assert.match(app, /prompt_version: getCompactPromptEnabled\(mode\) \? 'prompt_v3' : 'prompt_v1'/);
  assert.match(app, /prompt_version_source: 'user'/);
  assert.match(app, /toggle\.disabled = profile !== 'opaque'/);
  assert.match(studioState, /material_profile: brief\.material_profile/);
  assert.match(jobs, /compact_prompt_enabled: parameters\.prompt_version === 'prompt_v3'/);
  assert.match(css, /\.material-route-status/);
});

test('result adjustment is a durable derived version instead of overwriting the reviewed image', () => {
  assert.match(html, /id="btn-review-adjust"[^>]*hidden>立即修改本张/);
  assert.match(api, /export async function adjustResult\(jobId, payload\)/);
  assert.match(app, /async function startImmediateAdjustment\(reason, reasonCodes = \[\]\)/);
  assert.match(app, /API\.adjustResult\(parentJobId/);
  assert.match(app, /parent_result_asset_id/);
  assert.match(app, /上一版本已并入版本对比/);
  assert.match(css, /\.review-reason \.review-adjust-button/);
});

test('result review persists A/B target, divider, zoom, pan, and first-use guidance', () => {
  assert.match(app, /function updateCompareState\(patch, persist = true\)/);
  assert.match(app, /secondary_result_asset_id/);
  assert.match(app, /guide_dismissed/);
  assert.match(app, /pan_x/);
  assert.match(app, /setCompareTransform/);
  assert.match(app, /scheduleWorkspaceDraftSave\(mode\)/);
  assert.match(css, /--compare-zoom/);
  assert.match(css, /\.review-guide/);
});

test('result review form and feedback receipt behavior live outside the page orchestrator', () => {
  assert.match(app, /createReviewController/);
  assert.match(app, /reviewController\.prepareFeedback\(item\)/);
  assert.match(app, /reviewController\.renderPanel\(activeItem\)/);
  assert.match(app, /reviewController\.activateDecision\(button\.dataset\.reviewDecision \|\| ''\)/);
  assert.doesNotMatch(app, /function (renderFeedbackState|prepareFeedbackForResult|clearReviewForm|renderReviewReasonTags|activateReviewDecision|renderReviewPanel)/);
  assert.match(review, /function prepareFeedback\(item\)/);
  assert.match(review, /function renderPanel\(item\)/);
  assert.match(review, /已记录需要调整/);
  assert.match(review, /feedbackReceiptCopy\(durable\.receipt\)/);
});

test('real workflow controls share the dark production dock without stretching empty space', () => {
  assert.match(html, /<aside class="task-dock"[\s\S]*?class="task-dock__body"[\s\S]*?class="mode-grid"[\s\S]*?id="folder-source"/);
  assert.match(css, /\.task-dock \{[^}]*display: grid;[^}]*grid-template-rows: auto minmax\(0,1fr\) auto/);
  assert.match(css, /\.task-dock__body \{[^}]*overflow: hidden/);
  assert.doesNotMatch(css, /\.task-dock__body \{[^}]*overflow: hidden auto/);
  assert.match(css, /\.task-dock \.folder-source \{[^}]*border-radius: 17px/);
});

test('approved feedback is shown as an applied rule, not only a count', () => {
  assert.match(html, /id="memory-trace-knowledge"/);
  assert.match(html, /id="memory-trace-rules"/);
  assert.match(memory, /记忆反馈\/已批准/);
  assert.match(memory, /appliedRuleTexts/);
  assert.match(memory, /positive_rules[\s\S]*?negative_rules[\s\S]*?intent_lock_rules/);
  assert.match(memory, /其中 \$\{memorySources\.length\} 条是你已批准的反馈/);
  assert.match(memory, /已应用 \$\{executionRules\} 条可检查执行规则/);
});

test('knowledge suggestions expose durable governance instead of one-way approval', () => {
  assert.match(html, /data-memory-filter="pending"/);
  assert.match(html, /data-memory-filter="approved"/);
  assert.match(html, /data-memory-filter="disabled"/);
  assert.match(knowledge, /api\.governMemorySuggestion/);
  assert.match(knowledge, /expected_revision: Number\(item\.governance\?\.revision/);
  assert.match(knowledge, /memoryMutationsInFlight\.has\(id\)/);
  assert.match(knowledge, /MEMORY_REVISION_CONFLICT/);
  assert.match(knowledge, /memory-pending-count'\)\.textContent = String\(counts\.pending\)/);
  assert.match(knowledge, /data-memory-edit-form/);
  assert.match(knowledge, /data-memory-confirm="true"/);
  assert.match(css, /\.memory-history-list/);
  assert.match(css, /\.memory-edit-error/);
});

test('rendering a workspace queue must commit items before appending the add slot', () => {
  // Regression guard: the asset-card list must be written to #file-queue, otherwise
  // uploaded assets never render (no preview thumbnails) and the add-slot accumulates.
  assert.match(app, /queue\.innerHTML = items;/);
  assert.match(app, /queue\.innerHTML = items;[\s\S]*?queue\.innerHTML \+= '<button class="queue-item queue-add"/);
  assert.match(app, /const renderedAssets = boundedAssetRenderList\(state\.assets, selection, 60\);/);
  assert.match(app, /const items = renderedAssets\.map[\s\S]*?\.join\(''\);/);
});
