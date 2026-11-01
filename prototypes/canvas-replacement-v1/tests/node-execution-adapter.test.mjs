import test from 'node:test';
import assert from 'node:assert/strict';
import { createNodeExecutionAdapter, bindTaskProjection, taskProjection, mergeTaskProjection, activeTaskIds } from '../src/node-execution-adapter.js';
import { createImageNode } from '../src/canvas-model.js';
import { patchNodeDraft, resolveCreativeInput } from '../src/creative-input.js';

function fixture() {
  const calls = [];
  const api = {
    async compileKnowledge(payload) { calls.push(['preview', payload]); return { execution_context: { binding: 'preview', context_sha256: 'a'.repeat(64), summary: {} }, spatial_context: { action: payload.spatial_action, spatial_canvas_id: payload.spatial_canvas_id, source_element_id: payload.spatial_source_element_id, source_asset_id: payload.source_asset_ids[0], input_surface: 'canvas-reference' } }; },
    async executeCommand(id, payload) { calls.push(['submit', id, payload]); return { job: { id: 'job_fixture', status: 'queued' } }; },
    async getAsset(id) { return { id, role: 'result_main', kind: 'image', name: 'Result.png', width: 1536, height: 1024, lineage_parent_id: 'ast_exact' }; },
    async getAssetContentUrl(id) { return `http://localhost/${id}`; },
  };
  const canvasAdapter = { get() { return { id: 'scv_fixture', current_version_id: 'scvv_fixture' }; } };
  const nodes = patchNodeDraft([createImageNode({ id: 'result', media: { asset_id: 'ast_exact', result_id: 'ast_exact', role: 'result_main', kind: 'image' }, src: 'http://localhost/ast_exact', naturalWidth: 1536, naturalHeight: 1024 })], 'result', { prompt: '只改数字', model: 'tt-image-2' });
  return { calls, api, nodes, adapter: createNodeExecutionAdapter({ api, canvasAdapter }), input: resolveCreativeInput(nodes, [], 'result') };
}

test('preview uses the existing Governor and exact Result; no execution until explicit confirmation', async () => {
  const f = fixture(); const review = await f.adapter.preview(f.input);
  assert.deepEqual(f.calls[0][1].source_asset_ids, ['ast_exact']);
  assert.equal(f.calls[0][1].output_kind, 'canvas-visual-design'); assert.deepEqual(f.calls[0][1].intent_locks, {});
  await assert.rejects(f.adapter.submit(review), /未确认/); assert.equal(f.calls.length, 1);
  await f.adapter.submit(review, { confirmed: true });
  const payload = f.calls[1][2]; assert.equal(payload.max_attempts, 1); assert.equal(payload.parameters.automatic_paid_retry, false);
  assert.equal(payload.parameters.node_target_id, 'result'); assert.equal(payload.parameters.node_input_signature, f.input.signature);
  assert.equal(payload.parameters.brief.user_request, f.calls[0][1].user_request);
});

test('response-lost submit reuses the same request identity, not a new Provider attempt', async () => {
  const f = fixture(); const review = await f.adapter.preview(f.input); const original = f.api.executeCommand;
  let fail = true; f.api.executeCommand = async (...args) => { const result = await original(...args); if (fail) { fail = false; throw new Error('response lost'); } return result; };
  await assert.rejects(f.adapter.submit(review, { confirmed: true }), /lost/); await f.adapter.submit(review, { confirmed: true });
  assert.equal(f.calls[1][2].client_request_id, f.calls[2][2].client_request_id);
});

test('Task projection creates a result branch once; recovery is read-only and deleted applied results do not resurrect', async () => {
  const f = fixture(); const job = { id: 'job_fixture', command_id: 'command:existing-generate-single', status: 'completed', parameters: { node_target_id: 'result', spatial_action: 'generate-image' }, items: [{ result_asset_ids: ['ast_new'] }] };
  const first = await f.adapter.project(job, f.nodes, []); assert.equal(first.nodes.length, 2); assert.equal(first.edges[0].kind, 'flow');
  assert.equal(first.nodes[1].data.media.lineage_parent_id, 'ast_exact');
  const replay = await f.adapter.project(job, first.nodes, first.edges); assert.equal(replay.nodes.length, 2);
  const deleted = await f.adapter.project(job, first.nodes.slice(0, 1), []); assert.equal(deleted.nodes.length, 1);
  const explicitRestore = await f.adapter.project(job, deleted.nodes, [], { restoreApplied: true });
  assert.equal(explicitRestore.nodes.length, 2); assert.equal(explicitRestore.nodes[1].data.media.asset_id, 'ast_new');
  assert.equal(explicitRestore.nodes[1].data.media.task_id, job.id);
  assert.equal(f.calls.length, 0);
});

test('video uses only the existing offline contract, exact source and explicit confirmation', async () => {
  const f = fixture(); const nodes = patchNodeDraft(f.nodes, 'result', { mode: 'video', duration_seconds: 5 });
  const input = resolveCreativeInput(nodes, [], 'result'); const review = await f.adapter.preview(input);
  assert.equal(review.localOnly, true); assert.equal(f.calls.length, 0);
  await assert.rejects(f.adapter.submit(review), /未确认/);
  await f.adapter.submit(review, { confirmed: true });
  assert.equal(f.calls[0][1], 'command:image-to-video');
  assert.equal(f.calls[0][2].parameters.provider, 'offline-preview-v1');
  assert.equal(f.calls[0][2].parameters.provider_call_confirmed, false);
  assert.equal(f.calls[0][2].source_asset_ids[0], 'ast_exact');
  assert.equal(f.calls[0][2].max_attempts, 1);
});

test('two runs on one node retain independent outputs and latest status across out-of-order recovery', async () => {
  const f = fixture();
  const firstJob = { id: 'job_first', command_id: 'command:existing-generate-single', status: 'running', parameters: { node_target_id: 'result', spatial_action: 'generate-image' } };
  const secondJob = { ...firstJob, id: 'job_second', status: 'queued' };
  f.nodes[0].data.execution = bindTaskProjection({}, firstJob, { activate: true });
  f.nodes[0].data.execution = bindTaskProjection(f.nodes[0].data.execution, secondJob, { activate: true });
  const first = await f.adapter.project({ ...firstJob, status: 'completed', items: [{ result_asset_ids: ['ast_first'] }] }, f.nodes, []);
  assert.equal(first.nodes[0].data.execution.task_id, 'job_second');
  assert.equal(first.nodes[0].data.execution.status, 'queued');
  assert.equal(taskProjection(first.nodes[0].data.execution, 'job_first').status, 'completed');
  const second = await f.adapter.project({ ...secondJob, status: 'completed', items: [{ result_asset_ids: ['ast_second'] }] }, first.nodes, first.edges);
  assert.deepEqual(second.nodes.slice(1).map(n => n.data.media.asset_id), ['ast_first', 'ast_second']);
  assert.ok(second.nodes[2].position.y >= second.nodes[1].position.y + second.nodes[1].style.height + 64);
  const deletedFirst = second.nodes.filter(n => n.data.media.asset_id !== 'ast_first');
  const restored = await f.adapter.project({ ...firstJob, status: 'completed', items: [{ result_asset_ids: ['ast_first'] }] }, deletedFirst, []);
  assert.equal(restored.nodes.length, 2); assert.equal(f.calls.length, 0);
});

test('async backfill merges into live drafts, geometry and graph without resurrecting deleted nodes', async () => {
  const f = fixture();
  const job = { id: 'job_first', command_id: 'command:existing-generate-single', status: 'completed', parameters: { node_target_id: 'result', spatial_action: 'generate-image' }, items: [{ result_asset_ids: ['ast_first'] }] };
  const unrelated = createImageNode({ id: 'unrelated', naturalWidth: 100, naturalHeight: 100 });
  const before = [...f.nodes, unrelated];
  const projected = await f.adapter.project(job, before, []);
  const latest = patchNodeDraft(f.nodes, 'result', { prompt: '正在输入下一轮，不能丢失' }).map(node => ({ ...node, position: { x: 800, y: 900 } }));
  latest[0].data.execution = bindTaskProjection({}, { id: 'job_newer', status: 'queued' }, { activate: true });
  const merged = mergeTaskProjection(job, before, projected, latest, []);
  assert.equal(merged.nodes[0].data.draft.prompt, '正在输入下一轮，不能丢失');
  assert.deepEqual(merged.nodes[0].position, { x: 800, y: 900 });
  assert.equal(merged.nodes[0].data.execution.task_id, 'job_newer');
  assert.equal(taskProjection(merged.nodes[0].data.execution, job.id).status, 'completed');
  assert.equal(merged.nodes.some(node => node.id === 'unrelated'), false);
  assert.equal(merged.nodes.length, 2); assert.equal(merged.edges.length, 1);
  const deleted = mergeTaskProjection(job, before, projected, [], []);
  assert.equal(deleted.nodes.length, 0);
  const explicitRestore = mergeTaskProjection(job, before, projected, [], [], { allowMissingSource: true });
  assert.equal(explicitRestore.nodes.length, 1); assert.equal(explicitRestore.edges.length, 0);
  assert.equal(f.calls.length, 0);
});

test('active task lookup does not lose older running tasks outside the latest jobs page', () => {
  let execution = bindTaskProjection({}, { id: 'old_running', status: 'running' }, { activate: true });
  execution = bindTaskProjection(execution, { id: 'new_completed', status: 'completed' }, { activate: true });
  assert.deepEqual(activeTaskIds([{ data: { execution } }]), ['old_running']);
});
