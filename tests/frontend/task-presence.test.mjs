import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile, readdir } from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

import { taskPresenceModel } from '../../src/js/task-presence.js';
import { taskPresencePresentation } from '../../src/js/task-presence-view.js';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..');

const now = Date.parse('2026-09-03T02:00:00.000Z');
const job = (status, updatedAt = '2026-09-03T01:59:50.000Z') => ({ status, updated_at: updatedAt });

test('task presence follows the frozen cross-job priority', () => {
  const cases = [
    [['completed', 'queued', 'paused', 'running', 'partial', 'failed'], 'error'],
    [['completed', 'queued', 'paused', 'running', 'interrupted'], 'attention'],
    [['completed', 'queued', 'paused', 'canceling'], 'active'],
    [['completed', 'queued', 'paused'], 'paused'],
    [['completed', 'queued'], 'queued'],
    [['completed'], 'complete'],
    [['canceled'], 'idle'],
  ];
  for (const [statuses, expected] of cases) {
    assert.equal(taskPresenceModel(statuses.map((status) => job(status)), { now }).state, expected);
  }
});

test('completed presence expires and unavailable task data stays honest', () => {
  assert.equal(taskPresenceModel([job('completed', '2026-09-03T01:58:00.000Z')], { now }).state, 'idle');
  const unavailable = taskPresenceModel([], { now, available: false });
  assert.equal(unavailable.state, 'idle');
  assert.match(unavailable.ariaLabel, /暂不可读.*本地账本仍然保留.*打开任务中心/);
});

test('task presence reports the highest-priority count and keeps its action', () => {
  const model = taskPresenceModel([job('partial'), job('interrupted'), job('queued')], { now });
  assert.equal(model.count, 2);
  assert.match(model.label, /2 个任务需要处理/);
  assert.match(model.ariaLabel, /打开任务中心$/);
});

test('task presence turns every durable task state into a distinct motion phrase', () => {
  const cases = [
    ['idle', 0, 'rest', true, { pose: 'idle', expression: 'attentif' }],
    ['queued', 0, 'rest', true, { pose: 'orbit', expression: 'curieux' }],
    ['active', 0, 'rest', true, { pose: 'thinking', expression: 'attentif' }],
    ['paused', 0, 'rest', true, { pose: 'sleep', expression: 'somnolent' }],
    ['attention', 0, 'rest', true, { pose: 'alert', expression: 'surpris' }],
    ['attention', 1, 'rest', true, { pose: 'idle', expression: 'mefiant' }],
    ['error', 0, 'rest', true, { pose: 'exclaim', expression: 'effraye' }],
    ['error', 1, 'rest', true, { pose: 'idle', expression: 'triste' }],
    ['complete', 0, 'rest', true, { pose: 'burst', expression: 'excite' }],
    ['complete', 0.7, 'rest', true, { pose: 'wink', expression: 'heureux' }],
    ['complete', 1.4, 'rest', true, { pose: 'idle', expression: 'heureux' }],
    ['idle', 0, 'rest', false, { pose: 'sleep', expression: 'somnolent' }],
  ];
  for (const [state, age, interaction, available, expected] of cases) {
    assert.deepEqual(taskPresencePresentation(state, { age, interaction, available }), expected);
  }
});

test('idle presence responds to hover, keyboard press, and file drag', () => {
  assert.deepEqual(taskPresencePresentation('idle', { interaction: 'hover' }), { pose: 'idle', expression: 'curieux' });
  assert.deepEqual(taskPresencePresentation('idle', { interaction: 'focus' }), { pose: 'idle', expression: 'curieux' });
  assert.deepEqual(taskPresencePresentation('idle', { interaction: 'pressed' }), { pose: 'wide', expression: 'surpris' });
  assert.deepEqual(taskPresencePresentation('idle', { interaction: 'drag' }), { pose: 'wide', expression: 'excite' });
});

test('production shell exposes one accessible event-driven SVG task presence', async () => {
  const [html, css, app] = await Promise.all([
    readFile(path.join(root, 'src/index.html'), 'utf8'),
    readFile(path.join(root, 'src/css/stable-ui.css'), 'utf8'),
    readFile(path.join(root, 'src/js/app.js'), 'utf8'),
  ]);
  assert.match(html, /<button class="brand-mark task-presence" id="sidebar-logo"[^>]+aria-label="任务状态：任务空闲。打开任务中心"/);
  assert.doesNotMatch(html, /class="brand-word"/);
  assert.doesNotMatch(html, />\s*PA\s*</);
  assert.match(html, /data-presence-scene/);
  assert.match(html, /data-presence-mask-body/);
  assert.equal((html.match(/data-presence-eye/g) || []).length, 2);
  assert.doesNotMatch(html, /task-presence__face|task-presence__satellite|task-presence__symbol/);
  assert.match(app, /taskPresenceModel\(state\.jobs, \{ available: state\.jobsAvailable \}\)/);
  assert.match(app, /createTaskPresenceController\(\$\('#sidebar-logo'\)\)/);
  assert.match(app, /taskPresenceController\.setTaskState\(model\.state, \{ available: state\.jobsAvailable \}\)/);
  assert.match(app, /taskPresenceController\.setMotion\(document\.visibilityState === 'hidden' \? 'paused' : 'running'\)/);
  assert.match(app, /taskPresenceController\.destroy\(\)/);
  assert.match(app, /\$\('#sidebar-logo'\)\.addEventListener\('click', \(\) => openDrawer\('jobs'\)\)/);
  assert.match(css, /\.brand-mark \{[\s\S]*?background: transparent;/);
  assert.match(css, /\.task-presence__scene \{[^}]*width: 44px;[^}]*height: 44px;/);
  assert.match(css, /\.task-presence__ink \{[^}]*fill: var\(--presence-ink\)/);
  assert.match(css, /\.task-presence\[data-task-state="queued"\] \{ --presence-signal: var\(--coral\); \}/);
  assert.doesNotMatch(css, /@keyframes taskPresence|taskPresencePupil|task-presence__face/);
  assert.doesNotMatch(html, /boot-mark__face i::after|bootPupil/);
});

test('vendored Bloub core stays framework-free and preserves its MIT origin', async () => {
  const vendorRoot = path.join(root, 'src/vendor/bloub');
  const files = (await readdir(vendorRoot)).filter((name) => name.endsWith('.js'));
  const sources = await Promise.all(files.map((name) => readFile(path.join(vendorRoot, name), 'utf8')));
  assert.ok(files.length >= 10);
  assert.doesNotMatch(sources.join('\n'), /(?:from|import)\s+['"](?:vue|mediabunny|lottie|@rive)/i);
  const [origin, license, notices] = await Promise.all([
    readFile(path.join(vendorRoot, 'ORIGIN.md'), 'utf8'),
    readFile(path.join(vendorRoot, 'LICENSE'), 'utf8'),
    readFile(path.join(root, 'THIRD_PARTY_NOTICES.md'), 'utf8'),
  ]);
  assert.match(origin, /jeremy-prt\/bloub[\s\S]*b4bb3c1[\s\S]*MIT/);
  assert.match(license, /MIT License[\s\S]*Jérémy Perret/);
  assert.match(notices, /Bloub animation engine[\s\S]*0\.1\.1[\s\S]*MIT/);
});
