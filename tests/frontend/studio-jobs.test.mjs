import test from 'node:test';
import assert from 'node:assert/strict';

import {
  boundedJobsForDisplay,
  jobAnnouncementCopy,
  jobFilterCounts,
  jobItemsForDisplay,
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

test('large task lists stay segmented without hiding an older active task', () => {
  const many = Array.from({ length: 31 }, (_, index) => ({
    id: `job-${index + 1}`,
    status: index === 30 ? 'running' : 'completed',
  }));
  const visible = boundedJobsForDisplay(many, 12);
  assert.equal(visible.length, 13);
  assert.equal(visible[0].id, 'job-1');
  assert.equal(visible.at(-1).id, 'job-31');
});

test('job announcements prefer the durable task title over a workflow fallback', () => {
  assert.deepEqual(jobAnnouncementCopy({
    title: '生成视频预览', mode: 'single', status: 'completed',
  }, '商业主图'), {
    message: '生成视频预览已完成', tone: 'success',
  });
  assert.equal(jobAnnouncementCopy({ status: 'running' }, '商业主图').message, '');
});

test('twenty-item jobs show actionable work first and expand without reordering', () => {
  const items = Array.from({ length: 20 }, (_, index) => ({
    id: `item-${index + 1}`,
    status: index === 3 ? 'failed' : (index === 7 ? 'running' : 'queued'),
  }));
  const job = { status: 'running', items };
  assert.deepEqual(jobItemsForDisplay(job, false, 5).map((item) => item.id), [
    'item-4', 'item-8', 'item-1', 'item-2', 'item-3',
  ]);
  assert.deepEqual(jobItemsForDisplay(job, true).map((item) => item.id), items.map((item) => item.id));
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
      brief: { user_request: '只保留两个汉堡', material_profile: 'opaque' },
      intent: { product_quantity: true },
      parameters: {
        model: 'gpt-image-2', angle: '45top', fidelity: 82,
        output_ratio: '16:9', output_resolution: '4k',
        variations: 3, platter: 'remove', refine: false,
        generation_strategy: 'single_pass',
        prompt_version: 'prompt_v3', prompt_version_source: 'user',
      },
    },
  }, { compare_state: { divider: 42 } });
  assert.deepEqual(snapshot, {
    compare_state: { divider: 42 },
    brief: '只保留两个汉堡',
    model: 'gpt-image-2',
    angle: '45top',
    output_ratio: '16:9',
    output_resolution: '4k',
    generation_strategy: 'single_pass',
    material_profile: 'opaque',
    compact_prompt_enabled: true,
    fidelity: 82,
    batch: 3,
    platter: 'remove',
    refine: false,
    intent_locks: { product_quantity: true },
    active_job_id: 'job-immutable',
  });
});
