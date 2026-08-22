import test from 'node:test';
import assert from 'node:assert/strict';

import {
  collectResultItems,
  createSubmissionSnapshot,
  itemCompletionProgress,
  jobCompletionProgress,
  jobLifecycleActions,
  jobsRenderSignature,
  multiFileOutputPlan,
  processResultItems,
  queueCompletionProgress,
  selectionAfterImport,
  submissionFingerprint,
} from '../../src/js/workspace-state.js';

test('submission snapshot freezes mode, twenty source IDs, order, and nested parameters', () => {
  const sourceAssetIds = Array.from({ length: 20 }, (_, index) => `asset-${index + 1}`);
  const parameters = {
    model: 'gpt-image-2',
    brief: { mode: 'multi-file', intent_locks: { packaging_text: true } },
  };
  const snapshot = createSubmissionSnapshot({ mode: 'multi-file', sourceAssetIds, parameters });

  sourceAssetIds.reverse();
  parameters.model = 'changed-after-click';
  parameters.brief.intent_locks.packaging_text = false;

  assert.equal(snapshot.mode, 'multi-file');
  assert.deepEqual(
    snapshot.source_asset_ids,
    Array.from({ length: 20 }, (_, index) => `asset-${index + 1}`),
  );
  assert.equal(snapshot.parameters.model, 'gpt-image-2');
  assert.equal(snapshot.parameters.brief.intent_locks.packaging_text, true);
});

test('import selection is applied to the initiating mode and preserves deterministic order', () => {
  const current = ['asset-2', 'asset-1'];
  const imported = ['asset-3', 'asset-2', ...Array.from({ length: 20 }, (_, index) => `new-${index}`)];
  assert.deepEqual(
    selectionAfterImport(current, imported, { multiple: true, maxFiles: 20 }),
    ['asset-2', 'asset-1', 'asset-3', ...Array.from({ length: 17 }, (_, index) => `new-${index}`)],
  );
  assert.deepEqual(
    selectionAfterImport(['old-selection'], ['new-source', 'extra'], { multiple: false, maxFiles: 1 }),
    ['new-source'],
  );
});

test('twenty-file batches stay usable while the 24-output safety limit remains explicit', () => {
  assert.deepEqual(multiFileOutputPlan(20, 1), {
    sources: 20, variations: 1, total: 20, maxOutputs: 24, maxVariations: 1, valid: true,
  });
  assert.equal(multiFileOutputPlan(20, 2).valid, false);
  assert.equal(multiFileOutputPlan(12, 2).valid, true);
  assert.equal(multiFileOutputPlan(8, 4).valid, false);
});

test('completion rendering follows durable DB progress for every item status', () => {
  assert.equal(itemCompletionProgress({ status: 'failed', progress: 0.15 }), 0.15);
  assert.equal(itemCompletionProgress({ status: 'canceled', progress: 1 }), 1);
  assert.equal(itemCompletionProgress({ status: 'running', progress: 0.35 }), 0.35);
  assert.equal(jobCompletionProgress({
    status: 'partial',
    progress: 1,
    items: [
      { status: 'completed', progress: 1 },
      { status: 'failed', progress: 1 },
      { status: 'canceled', progress: 1 },
    ],
  }), 1);
  assert.equal(jobCompletionProgress({
    status: 'running',
    progress: 0.7,
    items: [{ status: 'completed', progress: 1 }, { status: 'running', progress: 0.4 }],
  }), 0.7);
  assert.equal(jobCompletionProgress({
    status: 'running',
    items: [{ status: 'completed', progress: 1 }, { status: 'running', progress: 0.4 }],
  }), 0.7);
  assert.equal(queueCompletionProgress([{
    progress: 0.4,
    total_items: 2,
    items: [{ progress: 1 }, { progress: 1 }],
  }]), 0.4);
  assert.equal(queueCompletionProgress([
    { progress: 0.25, total_items: 2 },
    { progress: 1, total_items: 1 },
  ]), 0.5);
});

test('job lifecycle controls expose pause, resume, and cancel only for valid durable states', () => {
  assert.deepEqual(jobLifecycleActions('queued'), ['pause', 'cancel']);
  assert.deepEqual(jobLifecycleActions('running'), ['pause', 'cancel']);
  assert.deepEqual(jobLifecycleActions('paused'), ['resume', 'cancel']);
  assert.deepEqual(jobLifecycleActions('canceling'), []);
  assert.deepEqual(jobLifecycleActions('completed'), []);
});

test('submission fingerprints are stable for object key order but retain source order', () => {
  const first = {
    mode: 'multi-file',
    source_asset_ids: ['asset-a', 'asset-b'],
    parameters: { brief: { angle: 'front', mode: 'multi-file' }, batch: 2 },
  };
  const reorderedKeys = {
    parameters: { batch: 2, brief: { mode: 'multi-file', angle: 'front' } },
    source_asset_ids: ['asset-a', 'asset-b'],
    mode: 'multi-file',
  };
  assert.equal(submissionFingerprint(first), submissionFingerprint(reorderedKeys));
  assert.notEqual(
    submissionFingerprint(first),
    submissionFingerprint({ ...first, source_asset_ids: ['asset-b', 'asset-a'] }),
  );
});

test('Dock render signature ignores object key order and changes for real state or action changes', () => {
  const jobs = [{ id: 'job-1', status: 'running', items: [{ id: 'item-1', progress: 0.2 }] }];
  const sameJobs = [{ items: [{ progress: 0.2, id: 'item-1' }], status: 'running', id: 'job-1' }];
  assert.equal(jobsRenderSignature(jobs, true), jobsRenderSignature(sameJobs, true));
  assert.notEqual(jobsRenderSignature(jobs, true), jobsRenderSignature(jobs, false));
  assert.notEqual(
    jobsRenderSignature(jobs, true),
    jobsRenderSignature(jobs, true, ['cancel:job-1:']),
  );
});

test('partial result export collects both roles and continues after an individual failure', async () => {
  const main = { asset_id: 'main-1', role: 'result_main' };
  const broken = { asset_id: 'cutout-broken', role: 'result_cutout' };
  const last = { asset_id: 'cutout-2', role: 'result_cutout' };
  const items = collectResultItems({ main: [main], cutout: [broken, last, main] });
  assert.deepEqual(items, [main, broken, last]);
  const visited = [];
  const outcome = await processResultItems(items, async (item) => {
    visited.push(item.asset_id);
    if (item === broken) throw new Error('missing blob');
  });
  assert.deepEqual(visited, ['main-1', 'cutout-broken', 'cutout-2']);
  assert.deepEqual(outcome.succeeded, [main, last]);
  assert.equal(outcome.failed.length, 1);
  assert.equal(outcome.failed[0].item, broken);
});
