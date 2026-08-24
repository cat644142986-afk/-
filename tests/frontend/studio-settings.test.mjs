import test from 'node:test';
import assert from 'node:assert/strict';

import {
  knowledgeStatusCopy,
  normalizeSettingsPayload,
  outputRootStatusCopy,
} from '../../src/js/studio-settings.js';

test('settings payload trims paths and only sends a non-empty key on explicit save', () => {
  const values = {
    defaultModel: 'gemini-3.1-flash-image-preview',
    defaultPlatter: 'keep',
    defaultAngle: '45top',
    defaultFidelity: '65',
    knowledgeBasePath: '  D:\\知识库  ',
    apiKey: '  secret-value  ',
  };
  assert.deepEqual(normalizeSettingsPayload(values, false), {
    default_model: 'gemini-3.1-flash-image-preview',
    default_platter: 'keep',
    default_angle: '45top',
    default_fidelity: 65,
    knowledge_base_path: 'D:\\知识库',
  });
  assert.equal(normalizeSettingsPayload(values, true).api_key, 'secret-value');
  assert.equal('api_key' in normalizeSettingsPayload({ ...values, apiKey: '   ' }, true), false);
});

test('knowledge status keeps English decorative and Chinese explanatory', () => {
  assert.deepEqual(knowledgeStatusCopy({ available: true, document_count: 61, rule_count: 2094 }), {
    pill: '61 docs · 2094 rules',
    title: '只读连接正常',
    detail: '61 份文档 · 2094 条规则',
  });
  assert.deepEqual(knowledgeStatusCopy({ available: false, design_path: 'D:\\知识库' }), {
    pill: '知识库未连接',
    title: '未找到知识库',
    detail: 'D:\\知识库',
  });
});

test('output root status keeps path failures explanatory in Chinese', () => {
  assert.deepEqual(outputRootStatusCopy({
    available: true,
    message: '新任务将保存到这里；运行中任务保持原目录',
  }), {
    text: '新任务将保存到这里；运行中任务保持原目录',
    error: false,
  });
  assert.deepEqual(outputRootStatusCopy({
    available: false,
    message: '交付目录不存在，或所在磁盘当前不可用',
  }), {
    text: '交付目录不存在，或所在磁盘当前不可用',
    error: true,
  });
});
