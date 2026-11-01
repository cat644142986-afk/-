import test from 'node:test';
import assert from 'node:assert/strict';
import { createPromptNode, patchNodeDraft, resolveCreativeInput } from '../src/creative-input.js';
import { createImageNode } from '../src/canvas-model.js';

const media = (id, result = false) => ({ asset_id: id, role: result ? 'result_main' : 'workspace_source', kind: 'image', ...(result ? { result_id: id, lineage_parent_id: 'old_original' } : {}) });
const image = (id, identity) => createImageNode({ id, media: identity, name: id, src: `http://localhost/${id}`, naturalWidth: 1536, naturalHeight: 1024 });

test('current Result remains exact, flow lineage is NOT an input reference', () => {
  const nodes = [image('original', media('ast_original')), image('result', media('ast_exact', true))];
  const next = patchNodeDraft(nodes, 'result', { prompt: '只把 14.9 改成 10，其余不变', model: 'tt-image-2' });
  const input = resolveCreativeInput(next, [{ source: 'original', target: 'result', kind: 'flow' }], 'result');
  assert.equal(input.references.length, 1); assert.equal(input.references[0].asset_id, 'ast_exact');
  assert.equal(input.references[0].sourceNodeId, 'result'); assert.ok(!input.text.includes('original'));
});

test('only selected upstream input text and references enter the prompt', () => {
  const prompt = createPromptNode({ x: 0, y: 0 }); prompt.id = 'prompt'; prompt.data.text = '保护包装文字和 Logo';
  const unrelated = createPromptNode({ x: 0, y: 0 }); unrelated.data.text = '无关历史';
  const nodes = patchNodeDraft([prompt, unrelated, image('reference', media('ast_ref')), image('target', undefined)], 'target', { prompt: '新的方案' });
  const edges = [{ source: 'prompt', target: 'target', kind: 'input' }, { source: 'reference', target: 'target', kind: 'input' }];
  const input = resolveCreativeInput(nodes, edges, 'target');
  assert.equal(input.text, '保护包装文字和 Logo\n\n新的方案'); assert.equal(input.references[0].asset_id, 'ast_ref');
});

test('semantic stale ignores viewport/selection/geometry but catches input, model and reference changes', () => {
  const nodes = patchNodeDraft([image('target', media('ast_ref'))], 'target', { prompt: '测试创作', model: 'tt-image-2' });
  const original = resolveCreativeInput(nodes, [], 'target').signature;
  assert.equal(resolveCreativeInput(nodes.map((node) => ({ ...node, selected: false, position: { x: 200, y: 300 } })), [], 'target').signature, original);
  for (const patch of [{ prompt: '另一个要求' }, { model: 'banana-2' }, { ratio: '16:9' }, { useCurrentMedia: false }]) {
    assert.notEqual(resolveCreativeInput(patchNodeDraft(nodes, 'target', patch), [], 'target').signature, original);
  }
});

test('node drafts persist independently through object switches and deselect', () => {
  const nodes = patchNodeDraft(patchNodeDraft([image('a', media('ast_a')), image('b', media('ast_b'))], 'a', { prompt: 'A 的要求' }), 'b', { prompt: 'B 的要求' });
  assert.equal(resolveCreativeInput(nodes, [], 'a').text, 'A 的要求');
  assert.equal(resolveCreativeInput(nodes, [], 'b').text, 'B 的要求');
});

test('manual references bind the exact duplicate node; missing/ambiguous references never fall back', () => {
  const identity = media('ast_shared', true);
  const nodes = patchNodeDraft([image('original-view', identity), image('chosen-copy', identity), image('target', undefined)], 'target', {
    prompt: '以这个副本继续', references: [{ ...identity, source_element_id: 'chosen-copy' }], useCurrentMedia: false,
  });
  assert.equal(resolveCreativeInput(nodes, [], 'target').references[0].sourceNodeId, 'chosen-copy');
  assert.equal(resolveCreativeInput(nodes.filter(n => n.id !== 'chosen-copy'), [], 'target').references[0].sourceNodeId, '');
  assert.equal(resolveCreativeInput(patchNodeDraft(nodes, 'target', { references: [identity] }), [], 'target').references[0].sourceNodeId, '');
});
