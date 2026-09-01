import test from 'node:test';
import assert from 'node:assert/strict';

import { reviewDecisionSummaryCopy } from '../../src/js/studio-review.js';

test('review summaries keep every durable decision Chinese-first', () => {
  assert.equal(reviewDecisionSummaryCopy('adopt'), '已确认可以直接使用');
  assert.equal(reviewDecisionSummaryCopy('adjust'), '已记录需要调整');
  assert.equal(reviewDecisionSummaryCopy('reject'), '已记录整体方向不对');
  assert.equal(reviewDecisionSummaryCopy('future-decision'), '本版本已完成评审');
});
