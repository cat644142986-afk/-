import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import path from 'node:path';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..');
const [app, api, assets, config, jobs, settings, shell, studioState, html, css] = await Promise.all([
  readFile(path.join(root, 'src/js/app.js'), 'utf8'),
  readFile(path.join(root, 'src/js/api.js'), 'utf8'),
  readFile(path.join(root, 'src/js/studio-assets.js'), 'utf8'),
  readFile(path.join(root, 'src/js/studio-config.js'), 'utf8'),
  readFile(path.join(root, 'src/js/studio-jobs.js'), 'utf8'),
  readFile(path.join(root, 'src/js/studio-settings.js'), 'utf8'),
  readFile(path.join(root, 'src/js/studio-shell.js'), 'utf8'),
  readFile(path.join(root, 'src/js/studio-state.js'), 'utf8'),
  readFile(path.join(root, 'src/index.html'), 'utf8'),
  readFile(path.join(root, 'src/css/stable-ui.css'), 'utf8'),
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

test('asset management exposes discoverable safe removal, undo, recycle, restore, and references', () => {
  assert.match(html, /id="btn-asset-manager"/);
  assert.match(html, /id="asset-drawer"/);
  assert.match(html, /data-asset-view="active"/);
  assert.match(html, /data-asset-view="trash"/);
  assert.match(app, /createAssetManagerController/);
  assert.match(assets, /removeAssetFromCollectionSelections/);
  assert.match(assets, /api\.restoreAssetToCollection/);
  assert.match(assets, /api\.getAssetReferences/);
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
  assert.match(app, /完成度 \$\{itemProgress\}%/);
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
  assert.match(app, /openLayer\.id === 'img-modal' \? \$\('\.modal-card', openLayer\) : openLayer\.id === 'settings-panel' \? openLayer : \$\('\.drawer', openLayer\)/);
});

test('unsupported Folder action is absent and export handles all result roles independently', () => {
  assert.doesNotMatch(html, /btn-open-folder|>Folder</);
  assert.doesNotMatch(app, /openOutputFolder|btn-open-folder/);
  assert.match(app, /function getAllResultItems/);
  assert.match(app, /processResultItems\(items/);
  assert.match(html, /id="btn-save-all"[^>]*>导出全部</);
  assert.match(css, /\.result-actions \{[^}]*repeat\(2,1fr\)/);
});

test('production shell keeps review contextual and removes the redundant bottom dashboard', () => {
  assert.doesNotMatch(html, /class="rail-button"[^>]*data-page="compare"/);
  assert.match(html, /id="btn-open-compare"/);
  assert.doesNotMatch(html, /class="studio-meta-grid"/);
  assert.match(html, /class="workspace-announcer"/);
  assert.match(css, /\.studio-grid \{[^}]*grid-template-rows: minmax\(0, 1fr\)/);
  assert.match(css, /\.traffic-light \{ width: 12px; height: 12px/);
});

test('narrow windows turn workflow controls into an accessible drawer instead of squeezing the stage', () => {
  assert.match(html, /id="btn-workflow-drawer"[^>]*aria-controls="settings-panel"[^>]*aria-expanded="false"/);
  assert.match(html, /id="task-dock-backdrop"[^>]*hidden/);
  assert.match(css, /@media \(max-width: 980px\)[\s\S]*?\.studio-grid \{ grid-template-columns: minmax\(0,1fr\); \}/);
  assert.match(css, /\.task-dock\.is-open \{ opacity: 1; pointer-events: auto; transform: translateX\(0\); \}/);
  assert.match(shell, /panel\.toggleAttribute\('inert', presentation\.inert\)/);
  assert.match(shell, /panel\.setAttribute\('role', presentation\.role\)/);
  assert.match(shell, /media\.addEventListener\('change', sync\)/);
  assert.match(app, /if \(\$\('#settings-panel'\)\.classList\.contains\('is-open'\)\) workflowDock\.close\(\)/);
  assert.doesNotMatch(app, /closeWorkflowDock|openWorkflowDock|syncWorkflowDockLayout|compactWorkflowDock/);
});

test('primary studio copy uses readable type tokens instead of shrinking every label', () => {
  assert.match(css, /--type-caption: 10px/);
  assert.match(css, /--type-control: 12px/);
  assert.match(css, /--type-body: 13px/);
  assert.match(css, /\.creative-command input \{[^}]*font-size: var\(--type-body\)/);
  assert.match(css, /\.mode-button strong \{[^}]*font-size: var\(--type-control\)/);
  assert.match(css, /\.dock-field select,[^}]*font-size: var\(--type-control\)/);
  assert.match(css, /\.canvas-empty p \{[^}]*font-size: var\(--type-body\)/);
});

test('result, settings, memory, and job controls keep readable core copy', () => {
  assert.match(css, /\.result-tab \{[^}]*font-size: var\(--type-caption\)/);
  assert.match(css, /\.feedback-choice, \.feedback-send \{[^}]*font-size: var\(--type-caption\)/);
  assert.match(css, /\.settings-card > label,[^}]*font-size: var\(--type-caption\)/);
  assert.match(css, /\.memory-item p \{[^}]*font-size: var\(--type-caption\)/);
  assert.match(css, /\.job-card > footer button \{[^}]*font-size: var\(--type-caption\)/);
  assert.match(css, /@media \(max-width: 980px\)[\s\S]*?\.settings-layout \{[^}]*overflow-y: auto/);
});

test('workspace restores its latest result and exposes recoverable asset removal', () => {
  assert.match(app, /await restoreWorkspaceResult\(mode, payload\)/);
  assert.match(app, /payload\.jobs\.find\(\(entry\) => entry\.id === preferredId/);
  assert.match(app, /current_result_asset_id: items\[0\]\?\.asset_id \|\| null/);
  assert.match(api, /export async function removeAssetFromCollection\(collection, assetId\)/);
  assert.match(app, /data-remove-asset-id=/);
  assert.match(assets, /await api\.removeAssetFromCollection\(targetCollection, assetId\)/);
  assert.match(assets, /已移入当前域回收站/);
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

test('knowledge summary stays content-sized instead of stretching into an empty dark panel', () => {
  assert.match(css, /\.task-dock \{[^}]*grid-template-rows: auto auto auto auto auto auto minmax\(0,1fr\) auto/);
  assert.match(css, /\.task-dock__footer \{ grid-row: -2 \/ -1/);
});
