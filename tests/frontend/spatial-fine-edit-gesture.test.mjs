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

function keyDown({ key = 'Enter', target = { tagName: 'DIV' } } = {}) {
  const calls = [];
  return {
    event: {
      key,
      target,
      defaultPrevented: false,
      preventDefault() { this.defaultPrevented = true; calls.push('prevent'); },
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
  assert.equal(host.has('keydown'), false);
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

test('business image Enter is reserved from Excalidraw crop without opening Fabric', () => {
  const host = fakeHost();
  let selected = true;
  let opened = 0;
  const remove = installSpatialFineEditGestureRouter({
    host,
    getPointerTarget: () => null,
    isBusinessImageSelected: () => selected,
    onOpenFineEdit: () => { opened += 1; },
  });

  const businessImageEnter = keyDown();
  host.dispatch('keydown', businessImageEnter.event);
  assert.deepEqual(businessImageEnter.calls, ['prevent', 'stop', 'immediate']);
  assert.equal(opened, 0);

  selected = false;
  const ordinaryEnter = keyDown();
  host.dispatch('keydown', ordinaryEnter.event);
  assert.deepEqual(ordinaryEnter.calls, []);

  selected = true;
  const nonEnter = keyDown({ key: 'Escape' });
  host.dispatch('keydown', nonEnter.event);
  assert.deepEqual(nonEnter.calls, []);
  remove();
});

test('business image Enter remains available to editable controls', () => {
  const host = fakeHost();
  const remove = installSpatialFineEditGestureRouter({
    host,
    isBusinessImageSelected: () => true,
  });
  const editableTargets = [
    { tagName: 'INPUT' },
    { tagName: 'TEXTAREA' },
    { tagName: 'SELECT' },
    { tagName: 'BUTTON' },
    { tagName: 'DIV', isContentEditable: true },
    { tagName: 'SPAN', getAttribute: (name) => (name === 'contenteditable' ? '' : null) },
    { tagName: 'SVG', parentElement: { tagName: 'BUTTON' } },
    { tagName: 'A', href: 'https://example.invalid' },
    { tagName: 'SVG', parentElement: { tagName: 'A', href: 'https://example.invalid' } },
  ];

  for (const target of editableTargets) {
    const event = keyDown({ target });
    host.dispatch('keydown', event.event);
    assert.deepEqual(event.calls, []);
  }
  remove();
});

test('a previously prevented Enter still cannot reach Excalidraw crop', () => {
  const host = fakeHost();
  installSpatialFineEditGestureRouter({
    host,
    isBusinessImageSelected: () => true,
  });
  const event = keyDown();
  event.event.defaultPrevented = true;
  host.dispatch('keydown', event.event);
  assert.deepEqual(event.calls, ['prevent', 'stop', 'immediate']);
});
