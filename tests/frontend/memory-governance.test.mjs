import assert from 'node:assert/strict';
import test from 'node:test';

import {
  memoryGovernancePresentation,
  memoryScopeLabel,
  memorySuggestionsForFilter,
  memoryStatusLabel,
} from '../../src/js/memory-governance.js';

test('memory governance keeps status and scope meaning visible in text', () => {
  assert.equal(memoryStatusLabel('disabled'), '已停用');
  assert.equal(memoryScopeLabel({ scope_type: 'brand', scope_id: 'PA Tea' }), '品牌 · PA Tea');
  assert.equal(memoryScopeLabel({ scope_type: 'category', scope_id: 'food' }), '品类 · food');
  assert.equal(memoryScopeLabel({ scope_type: 'designer', scope_id: 'default' }), '个人通用');
});

test('memory filters preserve rejected history in all while keeping queues focused', () => {
  const items = [
    { id: 'one', status: 'pending' },
    { id: 'two', status: 'approved' },
    { id: 'three', status: 'rejected' },
  ];
  assert.deepEqual(memorySuggestionsForFilter(items, 'pending').map((item) => item.id), ['one']);
  assert.deepEqual(memorySuggestionsForFilter(items, 'all').map((item) => item.id), ['one', 'two', 'three']);
});

test('available governance actions come from the durable backend contract', () => {
  const view = memoryGovernancePresentation({
    status: 'pending',
    scope_type: 'designer',
    governance: {
      revision: 4,
      history_count: 3,
      available_actions: ['edit', 'postpone', 'approve', 'reject', 'undo'],
    },
  });
  assert.equal(view.revision, 4);
  assert.equal(view.historyCount, 3);
  assert.deepEqual(view.actions.map((item) => item.action), ['edit', 'postpone', 'approve', 'reject', 'undo']);
  assert.equal(view.actions.find((item) => item.action === 'reject').confirm, true);
  assert.equal(view.actions.find((item) => item.action === 'postpone').label, '稍后处理');
});
