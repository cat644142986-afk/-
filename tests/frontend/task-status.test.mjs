import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import path from 'node:path';

import {
  TASK_STATUS_IDS,
  taskStatusNextAction,
  taskStatusPresentation,
  taskStatusSummary,
} from '../../src/js/task-status.js';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..');

test('every durable task state has one complete presentation contract', () => {
  assert.deepEqual(TASK_STATUS_IDS, [
    'queued', 'running', 'paused', 'completed', 'partial',
    'failed', 'canceling', 'canceled', 'interrupted',
  ]);
  for (const status of TASK_STATUS_IDS) {
    const presentation = taskStatusPresentation(status);
    assert.equal(presentation.status, status);
    for (const field of ['label', 'tone', 'icon', 'family', 'presence', 'nextAction', 'recovery']) {
      assert.ok(String(presentation[field] || '').trim(), `${status}.${field}`);
    }
  }
  assert.equal(taskStatusPresentation('unexpected').label, '状态未知');
});

test('task summaries use the same status families as every visible surface', () => {
  const jobs = [
    { status: 'queued' }, { status: 'running' }, { status: 'canceling' },
    { status: 'paused' }, { status: 'partial' }, { status: 'failed' }, { status: 'interrupted' },
    { status: 'completed' }, { status: 'canceled' },
  ];
  assert.deepEqual(taskStatusSummary(jobs), {
    completed: 1,
    processing: 3,
    attention: 4,
    other: 1,
  });
});

test('the next action matches the recovery path available on each task card', () => {
  assert.equal(taskStatusNextAction('completed', { hasResults: true }), '打开结果');
  assert.equal(taskStatusNextAction('running'), '回到现场');
  assert.equal(taskStatusNextAction('paused'), '继续任务');
  assert.equal(taskStatusNextAction('partial', { retryableCount: 2 }), '只重试失败项');
  assert.equal(taskStatusNextAction('failed'), '查看失败原因');
  assert.equal(taskStatusNextAction('failed', { video: true }), '返回画布重新确认');
  assert.equal(taskStatusNextAction('completed', { video: true }), '打开画布');
});

test('header, rail, lifeform, summary and cards consume the shared task contract', async () => {
  const [app, html, css] = await Promise.all([
    readFile(path.join(root, 'src/js/app.js'), 'utf8'),
    readFile(path.join(root, 'src/index.html'), 'utf8'),
    readFile(path.join(root, 'src/css/stable-ui.css'), 'utf8'),
  ]);
  assert.match(app, /const summary = taskStatusSummary\(jobs\)/);
  assert.match(app, /const presence = taskPresenceModel\(jobs, \{ available: state\.jobsAvailable \}\)/);
  assert.match(app, /job-dock-summary'\)\.textContent = presence\.label/);
  assert.match(app, /renderRailNotice\(presence, summary\)/);
  assert.match(app, /renderTaskPresence\(presence\)/);
  assert.match(app, /const dotClass = state\.jobsAvailable \? \(\{/);
  assert.match(app, /const attention = state\.jobsAvailable \? summary\.attention : 0/);
  assert.match(app, /const status = taskStatusPresentation\(job\.status\)/);
  assert.match(app, /const nextAction = taskStatusNextAction\(job\.status/);
  assert.match(app, /data-task-next-action=/);
  assert.match(app, /data-lucide=/);
  assert.match(html, /id="job-summary-running">0<\/b><small>处理中<\/small>/);
  assert.match(html, /id="job-status-announcer" role="status" aria-live="polite" aria-atomic="true"/);
  assert.match(css, /\.job-dock-dot\.attention/);
  assert.match(css, /\.job-outcome strong/);
});
