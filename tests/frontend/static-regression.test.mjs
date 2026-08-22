import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import path from 'node:path';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..');
const [app, api, html, css] = await Promise.all([
  readFile(path.join(root, 'src/js/app.js'), 'utf8'),
  readFile(path.join(root, 'src/js/api.js'), 'utf8'),
  readFile(path.join(root, 'src/index.html'), 'utf8'),
  readFile(path.join(root, 'src/css/stable-ui.css'), 'utf8'),
]);

test('job submission captures an immutable draft before any knowledge await', () => {
  const draft = app.indexOf('const submissionDraft = captureSubmissionDraft()');
  const compile = app.indexOf('payload = await compileSubmissionPayload(submissionDraft)');
  const post = app.indexOf('await API.createJob(payload)');
  assert.ok(draft >= 0 && compile > draft && post > compile);
  assert.match(app, /persistPendingSubmission\(\{ fingerprint, requestId, payload \}\)/);
  assert.match(app, /clearPendingSubmission\(payload\.client_request_id\)/);
});

test('asset import writes selection back to the mode that initiated the async import', () => {
  const capture = app.indexOf('const importMode = state.currentMode');
  const request = app.indexOf('await API.importAssets(valid)');
  const assignment = app.indexOf('state.modeSelections[importMode] = selectionAfterImport');
  assert.ok(capture >= 0 && request > capture && assignment > request);
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
  assert.match(app, /paused: \{ label: '已暂停', tone: 'paused' \}/);
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
  assert.match(app, /openLayer\.id === 'img-modal' \? \$\('\.modal-card', openLayer\) : \$\('\.drawer', openLayer\)/);
});

test('unsupported Folder action is absent and export handles all result roles independently', () => {
  assert.doesNotMatch(html, /btn-open-folder|>Folder</);
  assert.doesNotMatch(app, /openOutputFolder|btn-open-folder/);
  assert.match(app, /function getAllResultItems/);
  assert.match(app, /processResultItems\(items/);
  assert.match(html, /id="btn-save-all"[^>]*>导出全部</);
  assert.match(css, /\.result-actions \{[^}]*repeat\(2,1fr\)/);
});
