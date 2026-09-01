import test from 'node:test';
import assert from 'node:assert/strict';

import {
  collectResultItems,
  comparisonPresentation,
  createSubmissionSnapshot,
  itemCompletionProgress,
  jobCompletionProgress,
  jobLifecycleActions,
  jobsRenderSignature,
  knowledgeBundleFromEvidence,
  multiFileOutputPlan,
  normalizeFeedbackSignal,
  processResultItems,
  queueCompletionProgress,
  selectionAfterImport,
  selectionForRestoredResult,
  submissionFingerprint,
} from '../../src/js/workspace-state.js';

test('submission snapshot freezes mode, twenty source IDs, order, and nested parameters', () => {
  const sourceAssetIds = Array.from({ length: 20 }, (_, index) => `asset-${index + 1}`);
  const parameters = {
    model: 'gpt-image-2',
    brief: { mode: 'multi-file', intent_locks: { packaging_text: true } },
  };
  const snapshot = createSubmissionSnapshot({
    mode: 'multi-file',
    sourceAssetIds,
    parameters,
    productProfileId: 'profile:sku-001',
    expectedProductProfileRevision: 7,
  });

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
  assert.equal(snapshot.product_profile_id, 'profile:sku-001');
  assert.equal(snapshot.expected_product_profile_revision, 7);
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

test('restored results recover active source selection without replacing a newer draft choice', () => {
  assert.deepEqual(
    selectionForRestoredResult([], ['source-a', 'source-missing'], ['source-a'], 20),
    ['source-a'],
  );
  assert.deepEqual(
    selectionForRestoredResult(['new-draft'], ['old-source'], ['new-draft', 'old-source'], 20),
    ['new-draft'],
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
  assert.notEqual(
    submissionFingerprint(first),
    submissionFingerprint({ ...first, product_profile_id: 'profile:sku-001', expected_product_profile_revision: 1 }),
  );
  assert.notEqual(
    submissionFingerprint({ ...first, product_profile_id: 'profile:sku-001', expected_product_profile_revision: 1 }),
    submissionFingerprint({ ...first, product_profile_id: 'profile:sku-001', expected_product_profile_revision: 2 }),
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

test('result review decisions normalize to backend-supported feedback signals', () => {
  assert.equal(normalizeFeedbackSignal('adjust'), 'adjusted');
  assert.equal(normalizeFeedbackSignal('adjusted'), 'adjusted');
  assert.equal(normalizeFeedbackSignal('adopt'), 'adopted');
  assert.equal(normalizeFeedbackSignal('rejected'), 'rejected');
  assert.equal(normalizeFeedbackSignal('unexpected-value'), 'note');
});

test('completed jobs restore exact task-bound rules instead of recompiling live knowledge', () => {
  const bundle = knowledgeBundleFromEvidence({
    brief: { objective: '做一张白底主图' },
    traces: [{
      stage: 'prompt.primary',
      compiled_prompt: '基础提示。不可破坏约束（最高优先级）：保持包装文字；保持产品数量。知识库设计约束：阴影克制；已批准记忆反馈：保留杯身',
      user_input: { brief: { objective: '保留玻璃杯身' } },
      applied_knowledge: [
        { kind: 'intent_lock', text: '保持包装文字' },
        { kind: 'positive_rule', text: '阴影克制', source: { id: 'K-1', title: '食品主图规则' } },
        { kind: 'negative_rule', text: '不要改变品牌色' },
        { kind: 'source', source: { id: 'K-1', title: '食品主图规则' } },
      ],
    }],
  });
  assert.equal(bundle.trace_bound, true);
  assert.equal(bundle.creative_brief.objective, '保留玻璃杯身');
  assert.deepEqual(bundle.intent_lock_rules, ['保持包装文字', '保持产品数量']);
  assert.deepEqual(bundle.positive_rules.map((rule) => rule.text), ['阴影克制', '已批准记忆反馈：保留杯身']);
  assert.deepEqual(bundle.negative_rules.map((rule) => rule.text), ['不要改变品牌色']);
  assert.equal(bundle.sources[0].id, 'K-1');
});

test('image comparison switches to honest side-by-side mode when aspect ratios differ', () => {
  assert.equal(
    comparisonPresentation({ width: 1200, height: 1200 }, { width: 2048, height: 2048 }),
    'overlay',
  );
  assert.equal(
    comparisonPresentation({ width: 900, height: 1400 }, { width: 2048, height: 2048 }),
    'side-by-side',
  );
  assert.equal(comparisonPresentation({}, {}), 'overlay');
});
