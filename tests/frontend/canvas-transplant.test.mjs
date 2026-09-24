import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { anchoredSurface, selectionFrame, shouldDismissComposerOnEscape, stopComposerKeyboardEvent } from '../../src/js/canvas-transplant-geometry.js';

test('transplanted selection frame follows scene coordinates and host offset', () => {
  const frame = selectionFrame([100, 50, 300, 150], ({ sceneX, sceneY }) => ({
    x: sceneX * 1.5 + 40, y: sceneY * 1.5 + 80,
  }), { left: 20, top: 30 });
  assert.deepEqual(frame, { left: 156, top: 111, width: 328, height: 178 });
});

test('anchored actions flip above and clamp within a compact viewport', () => {
  const placement = anchoredSurface({ left: 440, top: 270, width: 80, height: 60 }, 520, 360, {
    preferredWidth: 330, surfaceHeight: 80,
  });
  assert.deepEqual(placement, { left: 176, top: 178, width: 330 });
  const small = anchoredSurface({ left: -100, top: 0, width: 20, height: 10 }, 320, 190);
  assert.ok(small.left >= 14 && small.top >= 14);
  assert.ok(small.left + small.width <= 320 - 14);
});

test('expanded composer uses an available side in a short viewport', () => {
  const asset = anchoredSurface({ left: 550, top: 170, width: 320, height: 320 }, 946, 550, {
    preferredWidth: 490, surfaceHeight: 170,
  });
  assert.ok(asset.left + asset.width < 550);
  assert.ok(asset.top >= 14 && asset.top + 170 <= 550 - 14);
  const result = anchoredSurface({ left: 170, top: 170, width: 320, height: 320 }, 946, 550, {
    preferredWidth: 490, surfaceHeight: 170,
  });
  assert.ok(result.left > 170 + 320);
  assert.ok(result.width >= 300);
  const narrowSide = anchoredSurface({ left: 235, top: 115, width: 390, height: 355 }, 946, 550, {
    preferredWidth: 490, surfaceHeight: 170,
  });
  assert.ok(narrowSide.left > 235 + 390);
  assert.ok(narrowSide.width >= 275);
});

test('top-anchored selection actions avoid the PA tool and creation strips', () => {
  const placement = anchoredSurface({ left: 240, top: 110, width: 760, height: 360 }, 946, 550, {
    preferredWidth: 330, surfaceHeight: 56,
  });
  assert.ok(placement.top < 72);
  assert.ok(placement.left >= 210);
  assert.ok(placement.left + placement.width <= 946 - 180);
});

test('composer key isolation retains the upstream stopImmediatePropagation guard', () => {
  const calls = [];
  stopComposerKeyboardEvent({
    stopPropagation: () => calls.push('bubble'),
    nativeEvent: { stopImmediatePropagation: () => calls.push('native') },
  });
  assert.deepEqual(calls, ['bubble', 'native']);
});

test('Escape dismisses composer only after IME composition has ended', () => {
  assert.equal(shouldDismissComposerOnEscape({ key: 'Escape', nativeEvent: { isComposing: false } }, false), true);
  assert.equal(shouldDismissComposerOnEscape({ key: 'Escape', nativeEvent: { isComposing: true } }, false), false);
  assert.equal(shouldDismissComposerOnEscape({ key: 'Escape', nativeEvent: { isComposing: false } }, true), false);
});

test('prototype shares the existing Excalidraw host and PA action contract', () => {
  const island = readFileSync(new URL('../../src/js/infinite-canvas-island.jsx', import.meta.url), 'utf8');
  const shell = readFileSync(new URL('../../src/js/canvas-transplant-shell.jsx', import.meta.url), 'utf8');
  const workspace = readFileSync(new URL('../../src/js/infinite-canvas-workspace.js', import.meta.url), 'utf8');
  const retake = readFileSync(new URL('../../src/js/canvas-retake-reference.jsx', import.meta.url), 'utf8');
  const css = readFileSync(new URL('../../src/css/stable-ui.css', import.meta.url), 'utf8');
  assert.equal((island.match(/<StableExcalidraw\b/g) || []).length, 1);
  assert.match(island, /const StableExcalidraw = React\.memo\(Excalidraw\)/);
  assert.match(island, /<CanvasTransplantShell/);
  assert.match(shell, /data-spatial-action=\{imageAction\}/);
  assert.match(shell, /data-spatial-conversation-form/);
  assert.match(shell, /className=\{`pa-canvas-composer/);
  assert.doesNotMatch(shell, /className=\{`pa-transplant__composer/);
  assert.match(shell, /CanvasReferencePicker.*CanvasReferenceTray/);
  assert.match(retake, /Retake Whiteboard v0\.1\.3/);
  assert.match(shell, /onReferenceReview\?\.\(/);
  assert.doesNotMatch(shell, /data-spatial-reference/);
  assert.match(workspace, /openCanvasReferencePreview\(/);
  assert.match(workspace, /inspector\.dataset\.mode = 'composer-review'/);
  assert.doesNotMatch(workspace, /data-spatial-image-ai-field="outputRatio"/);
  assert.match(workspace, /openCanvasConversationPreview\(conversationInput/);
  assert.match(workspace, /shellMode: initialShellMode = 'legacy'/);
  assert.match(css, /\[data-shell="transplant"\].*\.layer-ui__wrapper__footer \{ display: flex !important; \}/);
  assert.match(css, /\[data-shell="transplant"\].*\.layer-ui__wrapper__footer-left \{ transform: none !important; \}/);
  assert.match(shell, /aria-expanded=\{arrangeOpen\}.*>对齐<\/button>/);
  assert.match(css, /\[data-arrange-open="true"\].*\.selected-shape-actions \{ transform: none !important;/);
  assert.doesNotMatch(css, /\.app-shell\.is-spatial-workspace\s*\{\s*grid-template-columns:/);
  assert.match(css, /\.spatial-shell-toggle \{ display: none; \}/);
});

test('Excalidraw upgrade gate keeps native align/distribute projection explicit', () => {
  const manifest = JSON.parse(readFileSync(new URL('../../package.json', import.meta.url), 'utf8'));
  const css = readFileSync(new URL('../../src/css/stable-ui.css', import.meta.url), 'utf8');
  // A version bump must trigger a packaged multi-selection/align/distribute
  // regression check before accepting any changed upstream fieldset order.
  assert.equal(manifest.dependencies['@excalidraw/excalidraw'], '0.18.1');
  assert.match(css, /\[data-arrange-open="true"\].*\.panelColumn > fieldset:not\(:nth-last-of-type\(2\)\)/);
});
