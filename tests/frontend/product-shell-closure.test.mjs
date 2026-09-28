import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';

const read = (path) => fs.readFileSync(new URL(`../../${path}`, import.meta.url), 'utf8');

test('desktop shell delegates window chrome to Windows', () => {
  const config = read('src-tauri/tauri.conf.json');
  const html = read('src/index.html');
  const app = read('src/js/app.js');

  assert.match(config, /"decorations": true/);
  assert.match(config, /"shadow": true/);
  assert.doesNotMatch(html, /traffic-lights|btn-(?:spatial-)?(?:close|min|max)(?:-dot)?/);
  assert.doesNotMatch(app, /requestAppClose|btn-(?:spatial-)?(?:close|min|max)(?:-dot)?/);
  assert.match(app, /API\.onAppCloseRequested\(appCloseCoordinator\.handleCloseRequested\)/);
});

test('primary navigation names product work instead of implementation surfaces', () => {
  const html = read('src/index.html');
  const config = read('src/js/studio-config.js');

  assert.match(html, /data-page="process" aria-label="生产"/);
  assert.match(html, /data-page="canvas" aria-label="画布"/);
  assert.match(html, /data-page="history" aria-label="历史"/);
  assert.match(config, /title: '生产', subtitle: '批量与结构化任务'/);
  assert.match(config, /title: '画布', subtitle: '视觉创作现场'/);
  assert.match(config, /title: '历史', subtitle: '任务、结果与恢复'/);
});

test('shell removes decorative hierarchy and keeps a non-error focus token', () => {
  const css = read('src/css/stable-ui.css');

  assert.match(css, /--focus-ring: #2563eb/);
  assert.match(css, /button:focus-visible,[^}]+outline: 2px solid var\(--focus-ring\)/);
  assert.match(css, /\.eyebrow,[\s\S]*\.status-panel__eyebrow \{ display: none; \}/);
  assert.match(css, /\.mode-button > span \{ display: none; \}/);
  assert.match(css, /\.mode-button small \{ display: none; \}/);
  assert.match(css, /\.workspace-context-meta span:nth-child\(2\),[\s\S]*display: none;/);
});
