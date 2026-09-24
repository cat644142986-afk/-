import test from 'node:test';
import assert from 'node:assert/strict';

import {
  admittedComposerModel,
  composerAdmissionRequest,
  eligibleComposerModels,
  initialComposerModel,
} from '../../src/js/canvas-model-admission.js';

const selection = {
  status: 'ready',
  default_provider_model_id: 'tt-image-2',
  eligible_provider_model_ids: ['tt-image-2', 'banana-2', 'banana-pro'],
  models: ['tt-image-2', 'banana-2', 'banana-pro'].map((provider_model_id) => ({
    provider_model_id,
    display_name: provider_model_id,
  })),
};

test('Composer requests exact reference-generate parameters and exposes only admitted models', () => {
  assert.deepEqual(composerAdmissionRequest({ ratio: '1:1', resolution: '2K' }), {
    taskKind: 'reference-generate',
    ratio: '1:1',
    resolution: '2k',
  });
  assert.deepEqual(
    eligibleComposerModels({ selection }).map((item) => item.provider_model_id),
    ['tt-image-2', 'banana-2', 'banana-pro'],
  );
  assert.equal(initialComposerModel(selection), 'tt-image-2');
  assert.equal(initialComposerModel(selection, 'banana-pro'), 'banana-pro');
  assert.equal(admittedComposerModel(selection, 'gpt-image-2'), null);
});

test('unsupported parameter response cannot silently substitute a model', () => {
  const unsupported = {
    status: 'unsupported',
    default_provider_model_id: null,
    eligible_provider_model_ids: [],
    models: [],
  };
  assert.deepEqual(eligibleComposerModels(unsupported), []);
  assert.equal(initialComposerModel(unsupported), '');
  assert.equal(initialComposerModel(unsupported, 'banana-2'), 'banana-2');
  assert.equal(admittedComposerModel(unsupported, 'banana-2'), null);
});
