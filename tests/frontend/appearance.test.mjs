import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

import {
  DEFAULT_APPEARANCE,
  appearanceSettingsHtml,
  appearanceStatusCopy,
  explicitThemeAfterToggle,
  normalizeAppearancePreferences,
  readAppearancePreferences,
  resolveAppearancePreferences,
} from '../../src/js/appearance.js';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..');

test('appearance preferences reject unknown persisted values', () => {
  assert.deepEqual(normalizeAppearancePreferences({
    themePreference: 'neon', textScale: 'tiny', contrast: 'maximum', motion: 'spin',
  }), DEFAULT_APPEARANCE);
});

test('appearance settings expose labeled native choices and a live summary', () => {
  const html = appearanceSettingsHtml();
  assert.match(html, /<fieldset><legend>界面主题<\/legend>/);
  assert.match(html, /name="appearance-theme" value="system"/);
  assert.match(html, /<fieldset><legend>界面配色<\/legend>/);
  assert.match(html, /name="appearance-colorway" value="mono"/);
  assert.match(html, /name="appearance-text-scale" value="large"/);
  assert.match(html, /name="appearance-contrast" value="high"/);
  assert.match(html, /name="appearance-motion" value="reduced"/);
  assert.match(html, /id="appearance-status" role="status" aria-live="polite"/);
});

test('system theme and reduced motion resolve without changing the stored choice', () => {
  const resolved = resolveAppearancePreferences({
    themePreference: 'system', colorway: 'warm', motionPreference: 'system', textScale: 'large', contrast: 'high',
  }, { systemDark: true, systemReducedMotion: true });
  assert.equal(resolved.themePreference, 'system');
  assert.equal(resolved.theme, 'dark');
  assert.equal(resolved.reducedMotion, true);
  assert.match(appearanceStatusCopy(resolved), /跟随系统（当前深色）.*暖色工作台.*舒适字号.*高对比.*减少动效/);
});

test('classic black and white remains a light workspace even after a dark preference', () => {
  const normalized = normalizeAppearancePreferences({ themePreference: 'dark', colorway: 'mono' });
  const resolved = resolveAppearancePreferences(normalized, { systemDark: true });
  assert.equal(normalized.themePreference, 'light');
  assert.equal(resolved.theme, 'light');
  assert.equal(resolved.colorway, 'mono');
});

test('legacy pa-theme remains readable while the new preference takes priority', () => {
  const values = new Map([['pa-theme', 'dark']]);
  const storage = { getItem: (key) => values.get(key) || null };
  assert.equal(readAppearancePreferences(storage).themePreference, 'dark');
  values.set('pa-theme-preference', 'system');
  assert.equal(readAppearancePreferences(storage).themePreference, 'system');
  values.set('pa-colorway', 'mono');
  assert.equal(readAppearancePreferences(storage).colorway, 'mono');
  assert.equal(explicitThemeAfterToggle('dark'), 'light');
  assert.equal(explicitThemeAfterToggle('light'), 'dark');
});

test('appearance and large-library optimizations remain wired into the production shell', async () => {
  const [app, css, assets] = await Promise.all([
    readFile(path.join(root, 'src/js/app.js'), 'utf8'),
    readFile(path.join(root, 'src/css/stable-ui.css'), 'utf8'),
    readFile(path.join(root, 'src/js/studio-assets.js'), 'utf8'),
  ]);
  assert.match(app, /function setupAppearance\(\)/);
  assert.match(app, /button\.tabIndex = active \? 0 : -1/);
  assert.match(app, /ArrowRight:[\s\S]*ArrowLeft:[\s\S]*Home:[\s\S]*End:/);
  assert.match(app, /drawer\.setAttribute\('role', 'dialog'\)/);
  assert.match(app, /textarea:not\(:disabled\)/);
  assert.match(app, /decoding="async"/);
  assert.match(assets, /decoding="async"/);
  assert.match(css, /content-visibility: auto/);
  assert.match(css, /contain-intrinsic-block-size: 76px/);
  assert.match(css, /:root\[data-motion="reduced"\]/);
  assert.match(css, /:root\[data-theme="dark"\] \{[\s\S]*?--stage-surface: #cfd3d7;/);
  assert.match(css, /:root\[data-theme="dark"\] \{[\s\S]*?--stage-surface-soft: #dfe2e5;/);
  assert.match(css, /:root\[data-theme="dark"\] \{[\s\S]*?--stage-surface-checker: #f2f3f4;/);
  assert.match(css, /\.preview-canvas \{[^}]*background: var\(--stage-surface\);/);
  assert.match(css, /\.canvas-ready \{[^}]*background: var\(--stage-surface-soft\);/);
  assert.match(css, /\.result-stage \{[^}]*background-color: var\(--stage-surface-checker\);/);
  assert.match(css, /\.canvas-stage \{[^}]*background: var\(--stage-surface\);/);
  assert.match(css, /\.review-compare-canvas \{[^}]*background: var\(--stage-surface\);/);
  assert.match(css, /\.spatial-thumbnail \{[^}]*background-color: var\(--stage-surface\);/);
  assert.match(css, /:root\[data-theme="dark"\] \.brand-mark \{ --presence-ink: #f7f7f4; --presence-paper: var\(--shell\); \}/);
});

test('settings cards grow with content and appearance choices reflow without overlap', async () => {
  const css = await readFile(path.join(root, 'src/css/stable-ui.css'), 'utf8');
  assert.match(css, /\.settings-layout \{[^}]*grid-auto-rows: max-content;[^}]*align-items: stretch;/);
  assert.match(css, /\.settings-card \{[^}]*min-width: 0;[^}]*min-height: 250px;[^}]*overflow: hidden;/);
  assert.doesNotMatch(css, /\.settings-card \{[^}]*overflow: visible;/);
  assert.match(css, /\.appearance-settings \{[^}]*grid-template-columns: repeat\(5,minmax\(0,1fr\)\)/);
  assert.match(css, /@media \(max-width: 1100px\)[\s\S]*?\.appearance-settings \{[^}]*grid-template-columns: repeat\(3,minmax\(0,1fr\)\)/);
  assert.match(css, /@media \(max-width: 760px\)[\s\S]*?\.appearance-settings \{[^}]*grid-template-columns: 1fr 1fr/);
  assert.match(css, /@media \(max-width: 520px\)[\s\S]*?\.appearance-settings \{[^}]*grid-template-columns: 1fr/);
});
