import assert from 'node:assert/strict';
import test from 'node:test';

import { installSpatialFineEditGestureRouter } from '../../src/js/spatial-fine-edit-gesture.js';

function fakeHost() {
  const listeners = new Map();
  return {
    addEventListener(type, listener, capture) {
      listeners.set(type, { listener, capture });
    },
    removeEventListener(type, listener, capture) {
      const entry = listeners.get(type);
      if (entry?.listener === listener && entry.capture === capture) listeners.delete(type);
    },
    dispatch(type, event) {
      listeners.get(type)?.listener(event);
    },
    has(type) {
      return listeners.has(type);
    },
  };
}

function doubleClick(tagName = 'CANVAS') {
  const calls = [];
  return {
    event: {
      target: { tagName },
      preventDefault: () => calls.push('prevent'),
      stopPropagation: () => calls.push('stop'),
      stopImmediatePropagation: () => calls.push('immediate'),
    },
    calls,
  };
}

const image = {
  id: 'asset-image-1',
  type: 'image',
  isDeleted: false,
  customData: { asset_id: 'asset:1' },
};

test('business image double-click opens Fabric once while an async open is pending', async () => {
  const host = fakeHost();
  const opened = [];
  let finish;
  const pending = new Promise((resolve) => { finish = resolve; });
  const remove = installSpatialFineEditGestureRouter({
    host,
    getPointerTarget: () => image,
    onOpenFineEdit: (element) => {
      opened.push(element.id);
      return pending;
    },
  });
  const first = doubleClick();
  const repeated = doubleClick();
  host.dispatch('dblclick', first.event);
  host.dispatch('dblclick', repeated.event);
  assert.deepEqual(opened, ['asset-image-1']);
  assert.deepEqual(first.calls, ['prevent', 'stop', 'immediate']);
  assert.deepEqual(repeated.calls, ['prevent', 'stop', 'immediate']);

  finish();
  await pending;
  await Promise.resolve();
  host.dispatch('dblclick', doubleClick().event);
  assert.deepEqual(opened, ['asset-image-1', 'asset-image-1']);

  remove();
  assert.equal(host.has('dblclick'), false);
});

test('toolbar and non-business double-clicks keep their native behavior', () => {
  const host = fakeHost();
  let pointerTarget = image;
  let opened = 0;
  installSpatialFineEditGestureRouter({
    host,
    getPointerTarget: () => pointerTarget,
    onOpenFineEdit: () => { opened += 1; },
  });

  const toolbar = doubleClick('BUTTON');
  host.dispatch('dblclick', toolbar.event);
  assert.equal(opened, 0);
  assert.deepEqual(toolbar.calls, []);

  pointerTarget = { id: 'rect-1', type: 'rectangle', customData: {} };
  const shape = doubleClick();
  host.dispatch('dblclick', shape.event);
  assert.equal(opened, 0);
  assert.deepEqual(shape.calls, []);
});
