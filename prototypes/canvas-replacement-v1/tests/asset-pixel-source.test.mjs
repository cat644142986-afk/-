import test from 'node:test';
import assert from 'node:assert/strict';
import { withAssetPixelSource, createAssetPixelSource, withAssetMediaSource } from '../src/asset-pixel-source.js';

test('pixel source uses the exact durable asset and releases runtime URL on success/failure', async () => {
  const calls = [];
  const api = { async getAssetBlob(id) { calls.push(id); return new Blob(['png'], { type: 'image/png' }); } };
  const urls = { createObjectURL() { calls.push('create'); return 'blob:temporary'; }, revokeObjectURL(url) { calls.push(['revoke', url]); } };
  assert.equal(await withAssetPixelSource(api, 'ast_result', async (url) => url, urls), 'blob:temporary');
  assert.deepEqual(calls, ['ast_result', 'create', ['revoke', 'blob:temporary']]);
  calls.length = 0;
  await assert.rejects(withAssetPixelSource(api, 'ast_result', async () => { throw new Error('decode'); }, urls), /decode/);
  assert.deepEqual(calls, ['ast_result', 'create', ['revoke', 'blob:temporary']]);
});

test('wrong media and missing asset errors do not leak URLs or replace the source', async () => {
  const urls = { createObjectURL() { throw new Error('must not create'); } };
  await assert.rejects(withAssetPixelSource({ getAssetBlob: async () => new Blob(['wav'], { type: 'audio/wav' }) }, 'ast_audio', () => {}, urls), /不是/);
  await assert.rejects(withAssetPixelSource({ getAssetBlob: async () => { throw new Error('ASSET_NOT_FOUND'); } }, 'ast_missing', () => {}, urls), /ASSET_NOT_FOUND/);
});

test('video frame decoding uses the exact durable video and always releases the runtime URL', async () => {
  const ids = [], releases = [];
  const api = { async getAssetBlob(id) { ids.push(id); return new Blob(['webm'], { type: 'video/webm' }); } };
  const urls = { createObjectURL: () => 'blob:video', revokeObjectURL: url => releases.push(url) };
  const frame = await withAssetMediaSource(api, 'ast_exact_video', 'video', async url => ({ url, seconds: 4.95 }), urls);
  assert.deepEqual(frame, { url: 'blob:video', seconds: 4.95 });
  assert.deepEqual(ids, ['ast_exact_video']); assert.deepEqual(releases, ['blob:video']);
});

test('Fabric undo may reload the leased source until editor closes; release is idempotent', async () => {
  const released = [];
  const source = await createAssetPixelSource({ getAssetBlob: async () => new Blob(['pixels'], { type: 'image/png' }) }, 'ast_exact', {
    createObjectURL: () => 'blob:editor-session', revokeObjectURL: url => released.push(url),
  });
  assert.equal(source.url, 'blob:editor-session'); assert.deepEqual(released, []);
  source.release(); source.release(); assert.deepEqual(released, ['blob:editor-session']);
});
