import test from 'node:test';
import assert from 'node:assert/strict';

import {
  groundingPackStatusCopy,
  knowledgeStatusCopy,
  normalizeSettingsPayload,
  outputRootStatusCopy,
  providerConnectionCopy,
} from '../../src/js/studio-settings.js';

test('ordinary settings payload never includes provider credentials', () => {
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
  assert.equal('api_key' in normalizeSettingsPayload(values, true), false);
  assert.equal('apiKey' in normalizeSettingsPayload(values, true), false);
});

test('provider connection copy distinguishes fresh and stale catalog state', () => {
  assert.deepEqual(providerConnectionCopy({ credential_configured: false }), {
    tone: 'idle',
    title: '尚未连接 LK / AI模型中心',
    detail: '输入账户创建的 API Key 后执行只读目录同步。',
    summary: '尚无目录快照',
    balance: '余额尚未同步',
  });
  const fresh = providerConnectionCopy({
    credential_configured: true,
    credential_fingerprint: 'sha256:abc123',
    catalog_status: 'fresh',
    fetched_at: '2026-09-23T00:00:00Z',
    counts: {
      catalog_models: 173,
      media_models_total: 100,
      media_models: { image: 19, video: 74, audio: 7 },
      skill_categories: 9,
    },
    balance: { balance: 12.5, unit: 'CNY' },
  });
  assert.equal(fresh.tone, 'ready');
  assert.match(fresh.summary, /173 模型/);
  assert.match(fresh.summary, /图片 19/);
  assert.equal(fresh.balance, '余额：12.5 CNY');
  const stale = providerConnectionCopy({
    credential_configured: true,
    catalog_status: 'stale',
    last_error_message: 'Provider catalog endpoint returned HTTP 503',
  });
  assert.equal(stale.tone, 'stale');
  assert.match(stale.title, /上次目录/);
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
