import test from 'node:test';
import assert from 'node:assert/strict';
import { composerPlacement, composerOwnsEscape, selectionToolbarSide, branchRevealOptions } from '../src/overlay-layout.js';

test('new results reveal their exact branch without fitting unrelated nodes or increasing zoom', () => {
  const viewport = { x: 0, y: 0, zoom: .8 };
  assert.equal(branchRevealOptions({ x: 200, y: 200, width: 500, height: 300 }, viewport, 1360, 860, ['source', 'result']), null);
  const options = branchRevealOptions({ x: 1100, y: 200, width: 800, height: 300 }, viewport, 1360, 860, ['source', 'result', 'result']);
  assert.deepEqual(options.nodes, [{ id: 'source' }, { id: 'result' }]);
  assert.equal(options.maxZoom, .8);
});

test('flipped composer does not occupy the selection toolbar side', () => {
  const frame = { left: 789, top: 325, width: 460, height: 306 };
  const placement = composerPlacement(frame, 1360, 860, 250);
  assert.equal(selectionToolbarSide(frame, { ...placement, height: 250 }), 'bottom');
  assert.ok(placement.left + placement.width <= 1360 - 14);
});
test('escape belongs to the innermost public control rather than closing the composer', () => {
  const event = { key: 'Escape', nativeEvent: {}, target: { closest: () => ({}) } };
  assert.equal(composerOwnsEscape(event, false), false);
  event.target.closest = () => null;
  assert.equal(composerOwnsEscape(event, false), true);
  assert.equal(composerOwnsEscape(event, true), false);
  event.defaultPrevented = true;
  assert.equal(composerOwnsEscape(event, false), false);
});
