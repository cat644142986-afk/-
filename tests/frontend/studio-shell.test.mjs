import test from 'node:test';
import assert from 'node:assert/strict';

import { workflowDockPresentation } from '../../src/js/studio-shell.js';

test('desktop task controls remain inline and never inert', () => {
  assert.deepEqual(workflowDockPresentation(false, true), {
    open: false,
    inert: false,
    backdropHidden: true,
    expanded: false,
    role: null,
    modal: null,
  });
});

test('compact task controls expose a modal drawer only while open', () => {
  assert.deepEqual(workflowDockPresentation(true, false), {
    open: false,
    inert: true,
    backdropHidden: true,
    expanded: false,
    role: 'dialog',
    modal: 'true',
  });
  assert.deepEqual(workflowDockPresentation(true, true), {
    open: true,
    inert: false,
    backdropHidden: false,
    expanded: true,
    role: 'dialog',
    modal: 'true',
  });
});
