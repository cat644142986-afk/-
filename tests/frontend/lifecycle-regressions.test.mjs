import test from 'node:test';
import assert from 'node:assert/strict';

import { memoryProjectionState } from '../../src/js/memory-projection.js';
import {
  comparisonTargetForItems,
  feedbackReceiptCopy,
  normalizeCompareState,
  normalizeReviewReasonCodes,
  reviewReasonLabel,
  reviewReasonOptions,
  reviewStateForResult,
} from '../../src/js/result-review.js';
import {
  locateResultVersion,
  selectRestorableResult,
} from '../../src/js/workspace-lifecycle.js';

const completedJobs = [{
  id: 'job-old',
  status: 'completed',
  items: [{ result_asset_ids: ['result-old'] }],
}, {
  id: 'job-current',
  status: 'completed',
  items: [{ result_asset_ids: ['result-current'] }],
}];

test('an empty durable cursor never restores an arbitrary historical result', () => {
  assert.equal(selectRestorableResult(completedJobs, {}), null);
  assert.equal(selectRestorableResult(completedJobs, {
    active_job_id: '',
    current_result_asset_id: '',
  }), null);
});

test('only an explicit job or result cursor restores completed output', () => {
  assert.equal(
    selectRestorableResult(completedJobs, { active_job_id: 'job-current' })?.id,
    'job-current',
  );
  assert.equal(
    selectRestorableResult(completedJobs, { current_result_asset_id: 'result-old' })?.id,
    'job-old',
  );
  assert.equal(
    selectRestorableResult(completedJobs, { current_result_asset_id: 'missing-result' }),
    null,
  );
});

test('knowledge evidence opens the exact result version across result roles', () => {
  const results = {
    main: [{ asset_id: 'result-main-1' }, { asset_id: 'result-main-2' }],
    cutout: [{ asset_id: 'result-cutout-1' }],
  };
  assert.deepEqual(locateResultVersion(results, 'result-main-2'), { tab: 'main', index: 1 });
  assert.deepEqual(locateResultVersion(results, 'result-cutout-1'), { tab: 'cutout', index: 0 });
  assert.equal(locateResultVersion(results, 'missing-result'), null);
});

test('a persisted result review hydrates a stable receipt instead of reopening the form', () => {
  const state = reviewStateForResult([{
    id: 'review-1',
    result_asset_id: 'result-current',
    decision: 'adopted',
    learning_action: 'suggest',
    feedback_id: 'feedback-1',
    learning_receipt: {
      status: 'accumulating',
      independent_sessions: 1,
      threshold: 2,
    },
  }], 'result-current');

  assert.equal(state.reviewed, true);
  assert.equal(state.showForm, false);
  assert.equal(state.reviewId, 'review-1');
  assert.equal(state.receipt.status, 'accumulating');
  assert.equal(state.receipt.independentSessions, 1);
  assert.equal(state.receipt.threshold, 2);
});

test('review state remains result-specific when switching versions', () => {
  const reviews = [{
    id: 'review-a',
    result_asset_id: 'result-a',
    decision: 'adjusted',
    learning_action: 'record',
  }];
  assert.equal(reviewStateForResult(reviews, 'result-a').reviewed, true);
  assert.equal(reviewStateForResult(reviews, 'result-b').reviewed, false);
  assert.equal(reviewStateForResult(reviews, 'result-b').showForm, true);
});

test('review reason tags are decision-specific, stable, and reject unknown codes', () => {
  assert.deepEqual(
    reviewReasonOptions('adjusted').slice(0, 3).map((item) => item.code),
    ['subject_scale', 'packaging_text', 'composition_crop'],
  );
  assert.deepEqual(
    normalizeReviewReasonCodes('adjusted', ['packaging_text', 'unknown', 'packaging_text']),
    ['packaging_text'],
  );
  assert.equal(reviewReasonLabel('packaging_text'), '包装文字');
});

test('comparison state clamps durable divider, zoom, and pan values', () => {
  assert.deepEqual(normalizeCompareState({
    position: 120,
    zoom: 9,
    panX: -140,
    panY: 36,
    secondaryResultAssetId: 'result-b',
    guideDismissed: true,
  }), {
    divider: 97,
    zoom: 4,
    pan_x: -100,
    pan_y: 36,
    secondary_result_asset_id: 'result-b',
    guide_dismissed: true,
  });
  assert.deepEqual(normalizeCompareState({}), {
    divider: 50,
    zoom: 1,
    pan_x: 0,
    pan_y: 0,
    secondary_result_asset_id: '',
    guide_dismissed: false,
  });
});

test('version B selection never points at the active result and falls back to source', () => {
  const items = [{ asset_id: 'result-a' }, { asset_id: 'result-b' }];
  assert.equal(comparisonTargetForItems(items, 'result-a', 'result-b')?.asset_id, 'result-b');
  assert.equal(comparisonTargetForItems(items, 'result-b', 'result-b'), null);
  assert.equal(comparisonTargetForItems(items, 'result-a', 'missing'), null);
});

test('an immediate adjustment receipt reports a real derived task instead of a deferred promise', () => {
  assert.equal(
    feedbackReceiptCopy({ status: 'adjustment_queued' }),
    '调整任务已独立入队；原版本与反馈均已保留',
  );
  assert.equal(
    feedbackReceiptCopy({ status: 'adjustment_completed' }),
    '新版本已完成；可在版本对比中查看',
  );
});

test('Growth disables task trace unless a task-bound evidence bundle is selected', () => {
  const projection = memoryProjectionState({
    currentTaskId: '',
    knowledgeBundle: { trace_bound: true },
    reviews: [],
    resultAssetId: '',
  });
  assert.equal(projection.status, 'disabled');
  assert.equal(projection.hasTask, false);
  assert.equal(projection.title, '当前未选择任务');
});

test('Growth projects accumulating, pending, approved, and rejected review states', () => {
  const base = {
    currentTaskId: 'job-current',
    knowledgeBundle: { trace_bound: true },
    resultAssetId: 'result-current',
  };
  for (const status of ['accumulating', 'pending', 'approved', 'rejected']) {
    const projection = memoryProjectionState({
      ...base,
      reviews: [{
        id: `review-${status}`,
        result_asset_id: 'result-current',
        decision: 'adjust',
        learning_action: 'record',
        learning_receipt: {
          status,
          independent_sessions: 1,
          threshold: 2,
        },
      }],
    });
    assert.equal(projection.status, status);
    assert.equal(projection.reviewed, true);
  }
});
