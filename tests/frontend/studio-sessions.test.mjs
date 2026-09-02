import test from 'node:test';
import assert from 'node:assert/strict';

import {
  formatStudioTime,
  sessionActionCopy,
  sessionProjectName,
  sessionStatusCopy,
} from '../../src/js/studio-sessions.js';

test('session presentation keeps durable fallbacks Chinese-first', () => {
  assert.equal(sessionProjectName({ project_name: '  秋季上新  ' }), '秋季上新');
  assert.equal(sessionProjectName({}), '未归类项目');
  assert.equal(sessionStatusCopy('completed'), '已完成');
  assert.equal(sessionStatusCopy('failed'), '需要处理');
  assert.equal(sessionStatusCopy('error'), '需要处理');
  assert.equal(sessionStatusCopy('future-state'), '已保存');
  assert.equal(sessionActionCopy({ status: 'partial' }), '处理');
  assert.equal(sessionActionCopy({ status: 'completed' }), '查看');
  assert.equal(sessionActionCopy({ status: 'draft' }), '继续');
});

test('session timestamps keep empty and invalid values readable', () => {
  assert.equal(formatStudioTime(''), '—');
  assert.equal(formatStudioTime('not-a-date'), 'not-a-date');
  assert.match(formatStudioTime('2026-09-01T10:00:00Z'), /\d{2}.*\d{2}/);
});
