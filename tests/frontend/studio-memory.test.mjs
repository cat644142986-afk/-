import test from 'node:test';
import assert from 'node:assert/strict';

import { memoryProjectionDetails } from '../../src/js/studio-memory.js';

test('memory projection details keep ledger counts in their correct Chinese contexts', () => {
  const details = memoryProjectionDetails({
    documents: 12,
    sessions: 3,
    feedback: 8,
    pending: 2,
    knowledgeRules: 21,
  });

  assert.equal(details.设计判断, '12 份正式知识、3 个创作现场和 8 条反馈共同构成当前投影。');
  assert.equal(details.正式知识, '唯一主库当前只读加载 12 份文档、21 条规则；正式页面不会被后台修改。');
  assert.equal(details.创作现场, '3 个会话保留各自素材、参数、知识引用与结果版本。');
  assert.equal(details.终稿反馈, '8 条有效反馈作为学习证据，不会直接覆盖正式知识。');
});

test('pending suggestions remain excluded from future generation until approval', () => {
  const details = memoryProjectionDetails({ pending: 4 });

  assert.equal(details.待审核建议, '4 条建议等待人工确认；未批准前不参与未来生成。');
});
