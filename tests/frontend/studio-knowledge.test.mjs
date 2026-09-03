import test from 'node:test';
import assert from 'node:assert/strict';

import {
  memoryFilterCounts,
  memoryHistoryLabel,
  memoryQueueNextAction,
  memoryQueueEmptyCopy,
  memorySuggestionCardMarkup,
} from '../../src/js/studio-knowledge.js';

const escapeHtml = (value) => String(value ?? '')
  .replaceAll('&', '&amp;')
  .replaceAll('<', '&lt;')
  .replaceAll('>', '&gt;')
  .replaceAll('"', '&quot;');

test('knowledge queue counts durable filters without hiding historical items from all', () => {
  assert.deepEqual(memoryFilterCounts([
    { status: 'pending' },
    { status: 'approved' },
    { status: 'disabled' },
    { status: 'rejected' },
  ]), { pending: 1, approved: 1, disabled: 1, all: 4 });
  assert.deepEqual(memoryFilterCounts(null), { pending: 0, approved: 0, disabled: 0, all: 0 });
});

test('knowledge queue empty and history copy remains Chinese-first', () => {
  assert.deepEqual(memoryQueueEmptyCopy('approved'), [
    '暂无已采用规则',
    '只有你亲自采用的建议，才会介入之后的匹配任务。',
  ]);
  assert.deepEqual(memoryQueueEmptyCopy('future-filter'), ['暂无内容', '请稍后刷新。']);
  assert.equal(memoryHistoryLabel({ action: 'evidence_refresh' }), '证据更新');
  assert.equal(memoryHistoryLabel({ action: 'future-action' }), '版本变更');
  assert.deepEqual(memoryQueueNextAction(false), { label: '开始创作', value: 'start' });
  assert.deepEqual(memoryQueueNextAction(true), { label: '回到当前结果', value: 'result' });
});

test('knowledge suggestion cards escape evidence and retain exact source cursors', () => {
  const html = memorySuggestionCardMarkup({
    id: 'memory-1',
    status: 'pending',
    confidence: 0.84,
    scope_type: 'brand',
    scope_id: 'PA Tea',
    proposed_value: {
      label: '<script>bad()</script>',
      directive: '保留包装文字',
      distinct_sessions: 2,
      min_support: 2,
      support_count: 3,
      contradiction_examples: ['不要<script>'],
    },
    evidence: [{ result_asset_id: 'asset-1', reason: '<b>文字清楚</b>' }],
    source_results: [{ result_asset_id: 'asset-1', job_id: 'job-1' }],
    governance: {
      revision: 3,
      available_actions: ['edit', 'approve', 'reject'],
      history: [{ revision: 2, action: 'edit', label: '<旧名称>' }],
    },
  }, {
    targetId: 'memory-1',
    expandedIds: new Set(['memory-1']),
    editingIds: new Set(['memory-1']),
    mutationsInFlight: new Set(),
    escapeHtml,
  });

  assert.match(html, /class="memory-item is-target"/);
  assert.match(html, /data-job-id="job-1" data-result-id="asset-1"/);
  assert.match(html, /data-memory-edit-form/);
  assert.match(html, /data-memory-confirm="true"/);
  assert.match(html, /&lt;script&gt;bad\(\)&lt;\/script&gt;/);
  assert.match(html, /&lt;b&gt;文字清楚&lt;\/b&gt;/);
  assert.doesNotMatch(html, /<script>|<b>文字清楚<\/b>/);
});
