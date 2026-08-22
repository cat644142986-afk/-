import test from 'node:test';
import assert from 'node:assert/strict';

import {
  jobFilterCounts,
  jobsForFilter,
  jobSourceIds,
  jobWorkspaceSnapshot,
} from '../../src/js/studio-jobs.js';

const jobs = [
  { id: 'j1', mode: 'single', snapshot: { source_asset_ids: ['a'] }, items: [{ source_asset_id: 'a' }] },
  { id: 'j2', mode: 'multi-file', snapshot: { source_asset_ids: ['b', 'c'] }, items: [{ source_asset_id: 'b' }] },
  { id: 'j3', mode: 'cutout-batch', items: [{ source_asset_id: 'd' }, { source_asset_id: 'a' }] },
];

test('task dock filters one global queue without changing global counts', () => {
  assert.equal(jobsForFilter(jobs, 'all').length, 3);
  assert.deepEqual(jobsForFilter(jobs, 'multi-file').map((job) => job.id), ['j2']);
  assert.deepEqual(jobsForFilter(jobs, 'unknown'), jobs);
  assert.deepEqual(jobFilterCounts(jobs), {
    all: 3,
    single: 1,
    'multi-file': 1,
    'group-split': 0,
    'cutout-batch': 1,
  });
});

test('job source hydration uses immutable snapshot order and removes duplicates', () => {
  assert.deepEqual(jobSourceIds(jobs), ['a', 'b', 'c', 'd']);
  assert.deepEqual(jobSourceIds(jobs, 2), ['a', 'b']);
  assert.deepEqual(jobSourceIds(jobs, 0), []);
});

test('returning to a job restores its immutable brief and controls', () => {
  const snapshot = jobWorkspaceSnapshot({
    id: 'job-immutable',
    parameters: { model: 'old-model', fidelity: 10 },
    snapshot: {
      brief: { user_request: '只保留两个汉堡' },
      intent: { product_quantity: true },
      parameters: {
        model: 'gpt-image-2', angle: '45top', fidelity: 82,
        variations: 3, platter: 'remove', refine: false,
      },
    },
  }, { compare_state: { divider: 42 } });
  assert.deepEqual(snapshot, {
    compare_state: { divider: 42 },
    brief: '只保留两个汉堡',
    model: 'gpt-image-2',
    angle: '45top',
    fidelity: 82,
    batch: 3,
    platter: 'remove',
    refine: false,
    intent_locks: { product_quantity: true },
    active_job_id: 'job-immutable',
  });
});
