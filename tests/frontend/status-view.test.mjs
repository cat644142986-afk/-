import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import path from 'node:path';

import { STATUS_KINDS, statusPanelHtml, statusViewModel } from '../../src/js/status-view.js';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..');

test('canonical status vocabulary covers every recovery state', () => {
  assert.deepEqual(STATUS_KINDS, ['loading', 'empty', 'offline', 'conflict', 'partial', 'recovered', 'error']);
  assert.equal(statusViewModel('loading').busy, true);
  assert.equal(statusViewModel('offline').role, 'alert');
  assert.equal(statusViewModel('conflict').busy, true);
  assert.match(statusViewModel('partial').detail, /成功项目已保留/);
  assert.match(statusViewModel('recovered').title, /已恢复/);
});

test('status markup is accessible, actionable, and safely escaped', () => {
  const html = statusPanelHtml('offline', {
    title: '<script>bad()</script>',
    detail: '连接 <失败>',
    fill: true,
    action: { label: '重试', attribute: 'data-job-action', value: 'refresh', busy: true },
  });
  assert.match(html, /role="alert"/);
  assert.match(html, /aria-live="assertive"/);
  assert.match(html, /data-job-action="refresh"/);
  assert.match(html, /aria-busy="true"/);
  assert.match(html, /&lt;script&gt;bad\(\)&lt;\/script&gt;/);
  assert.doesNotMatch(html, /<script>/);
});

test('major durable surfaces use shared states and visible conflict recovery', async () => {
  const [app, assets, state, html, css] = await Promise.all([
    readFile(path.join(root, 'src/js/app.js'), 'utf8'),
    readFile(path.join(root, 'src/js/studio-assets.js'), 'utf8'),
    readFile(path.join(root, 'src/js/studio-state.js'), 'utf8'),
    readFile(path.join(root, 'src/index.html'), 'utf8'),
    readFile(path.join(root, 'src/css/stable-ui.css'), 'utf8'),
  ]);
  assert.match(app, /statusPanelHtml\('offline'/);
  assert.match(app, /statusPanelHtml\('loading'/);
  assert.match(app, /statusPanelHtml\('partial'/);
  assert.match(app, /setWorkspaceSyncState\('conflict'/);
  assert.match(app, /setWorkspaceSyncState\('recovered'/);
  assert.match(app, /state\.draftConflictModes\.add\(mode\)/);
  assert.match(assets, /statusPanelHtml\('empty'/);
  assert.match(assets, /data-asset-status-action/);
  assert.match(state, /draftConflictModes: new Set\(\)/);
  assert.match(html, /id="workspace-sync-state"/);
  assert.match(css, /\.status-panel--offline/);
  assert.match(css, /\.status-panel--conflict/);
  assert.match(css, /\.status-panel--recovered/);
});
