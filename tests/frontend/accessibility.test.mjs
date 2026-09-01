import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..');

test('production shell keeps a single active style and script entry point', async () => {
  const html = await readFile(path.join(root, 'src/index.html'), 'utf8');
  assert.deepEqual([...html.matchAll(/<link[^>]+rel="stylesheet"[^>]+href="([^"]+)"/g)].map((match) => match[1]), [
    './css/stable-ui.css',
  ]);
  assert.deepEqual([...html.matchAll(/<script[^>]+type="module"[^>]+src="([^"]+)"/g)].map((match) => match[1]), [
    './js/app.js',
  ]);
  await assert.rejects(readFile(path.join(root, 'src/css/style.css'), 'utf8'), { code: 'ENOENT' });
});

test('tab groups use one tab stop and support arrow, Home, and End navigation', async () => {
  const [html, app, assets, knowledge] = await Promise.all([
    readFile(path.join(root, 'src/index.html'), 'utf8'),
    readFile(path.join(root, 'src/js/app.js'), 'utf8'),
    readFile(path.join(root, 'src/js/studio-assets.js'), 'utf8'),
    readFile(path.join(root, 'src/js/studio-knowledge.js'), 'utf8'),
  ]);
  assert.match(app, /button\.tabIndex = active \? 0 : -1/);
  assert.match(app, /const resultTabs = \$\$\('\.result-tab'\)/);
  assert.match(app, /ArrowRight: index \+ 1, ArrowLeft: index - 1, Home: 0, End: resultTabs\.length - 1/);
  assert.match(app, /resultTabs\[targetIndex\]\?\.click\(\);\s+resultTabs\[targetIndex\]\?\.focus\(\{ preventScroll: true \}\)/);
  assert.match(knowledge, /memoryFilters\[targetIndex\]\?\.click\(\);\s+memoryFilters\[targetIndex\]\?\.focus\(\{ preventScroll: true \}\)/);
  assert.match(assets, /button\.tabIndex = active \? 0 : -1/);
  assert.match(assets, /const viewTabs = queryAll\('\[data-asset-view\]'\)/);
  assert.match(assets, /ArrowRight: index \+ 1, ArrowLeft: index - 1, Home: 0, End: viewTabs\.length - 1/);
  assert.match(assets, /viewTabs\[targetIndex\]\?\.click\(\);\s+viewTabs\[targetIndex\]\?\.focus\(\{ preventScroll: true \}\)/);
  assert.match(html, /id="brief-input"[^>]+aria-label="创作要求"/);
});

test('review help and preview layers expose predictable focus behavior', async () => {
  const [html, app] = await Promise.all([
    readFile(path.join(root, 'src/index.html'), 'utf8'),
    readFile(path.join(root, 'src/js/app.js'), 'utf8'),
  ]);
  assert.match(html, /id="modal-backdrop"[^>]+tabindex="-1"/);
  assert.match(app, /guide\.setAttribute\('role', 'dialog'\)/);
  assert.match(app, /help\.setAttribute\('aria-controls', 'review-guide'\)/);
  assert.match(app, /setReviewGuideOpen\(true, \{ focus: true \}\)/);
  assert.match(app, /setReviewGuideOpen\(false, \{ restore: true \}\)/);
  assert.match(app, /if \(!\$\('#review-guide'\)\.hidden\) \{ setReviewGuideOpen\(false, \{ restore: true \}\); return; \}/);
});

test('key interface tokens preserve readable light and dark surface contrast', async () => {
  const css = await readFile(path.join(root, 'src/css/stable-ui.css'), 'utf8');
  assert.match(css, /--ink-3: #62676d/);
  assert.match(css, /--coral-deep: #c53f20/);
  assert.match(css, /\.task-dock \{ --ink-3: #b7bbc0;/);
  assert.match(css, /:root\[data-contrast="high"\] \.task-dock \{ --ink-3: #e0e2e4;/);
  assert.match(css, /\.primary-button \{[^}]+color: var\(--dark\)/);
  assert.match(css, /\.rail-connection \{[^}]+color: var\(--ink\)/);
  assert.match(css, /textarea:focus-visible \{ outline: 3px solid var\(--coral-deep\)/);
  assert.doesNotMatch(css, /letter-spacing:\s*-/);
  assert.doesNotMatch(css, /font-size:\s*clamp\([^;]*(?:vw|vh|vmin|vmax)/);
});
