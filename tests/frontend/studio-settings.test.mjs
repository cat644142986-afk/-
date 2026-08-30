import test from 'node:test';
import assert from 'node:assert/strict';

import {
  groundingPackStatusCopy,
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

test('optional grounding pack status distinguishes disabled, ready, and verified states', () => {
  assert.deepEqual(groundingPackStatusCopy({
    available: false,
    code: 'RUNTIME_NOT_CONFIGURED',
    message: '尚未选择本地识别运行时',
  }), {
    title: '未启用（当前使用手动框选）',
    detail: '尚未选择本地识别运行时',
    tone: 'idle',
  });
  assert.deepEqual(groundingPackStatusCopy({ available: true, verified: false }), {
    title: '扩展已就绪',
    detail: '首次使用前建议执行一次完整验证',
    tone: 'ready',
  });
  assert.deepEqual(groundingPackStatusCopy({
    available: true,
    verified: true,
    message: '本地智能选物扩展已完整验证，运行环境可用',
  }), {
    title: '完整验证通过',
    detail: '本地智能选物扩展已完整验证，运行环境可用',
    tone: 'ready',
  });
});

test('knowledge status keeps all core meaning in Chinese', () => {
  assert.deepEqual(knowledgeStatusCopy({ available: true, document_count: 61, rule_count: 2094 }), {
    pill: '61 份文档 · 2094 条规则',
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
