import assert from 'node:assert/strict';
import test from 'node:test';

import {
  SPATIAL_VIDEO_CONTRACT_VERSION,
  SPATIAL_VIDEO_COMMAND_ID,
  SPATIAL_VIDEO_DURATIONS,
  SPATIAL_VIDEO_OFFLINE_PROVIDER,
  SPATIAL_VIDEO_RATIOS,
  confirmSpatialVideoDraft,
  createSpatialVideoDraft,
  createSpatialVideoPlaybackState,
  isSpatialVideoJob,
  normalizeSpatialVideoParameters,
  pauseSpatialVideo,
  playSpatialVideo,
  restoreSpatialVideoPlaybackState,
  selectSpatialVideo,
  spatialVideoRuntimeSnapshot,
  spatialVideoCommandPayload,
  spatialVideoCanvasId,
  spatialVideoJobIsActive,
  spatialVideoJobIsSettled,
  spatialVideoResultAssetIds,
  stopSpatialVideoPlayback,
  switchSpatialVideoCanvas,
  updateSpatialVideoDraft,
} from '../../src/js/spatial-video.js';

test('image-to-video parameters match the frozen offline provider contract', () => {
  assert.equal(SPATIAL_VIDEO_CONTRACT_VERSION, 'image-to-video-v1');
  assert.equal(SPATIAL_VIDEO_OFFLINE_PROVIDER, 'offline-preview-v1');
  assert.deepEqual([...SPATIAL_VIDEO_RATIOS], ['1:1', '16:9', '9:16', '4:3', '3:4']);
  assert.deepEqual([...SPATIAL_VIDEO_DURATIONS], [3, 5, 8, 10]);
  assert.deepEqual(normalizeSpatialVideoParameters({
    prompt: '  商品缓慢   向右旋转  ',
    output_ratio: '16:9',
    duration_seconds: '5',
    motion_intensity: '4',
    first_frame_asset_id: 'ast:first',
    last_frame_asset_id: 'ast:last',
  }, { sourceAssetId: 'ast:first' }), {
    contract_version: 'image-to-video-v1',
    prompt: '商品缓慢 向右旋转',
    output_ratio: '16:9',
    duration_seconds: 5,
    motion_intensity: 4,
    first_frame_asset_id: 'ast:first',
    last_frame_asset_id: 'ast:last',
    provider: 'offline-preview-v1',
    provider_call_confirmed: false,
    automatic_paid_retry: false,
  });
  assert.throws(() => normalizeSpatialVideoParameters({ prompt: 'x' }, { sourceAssetId: 'ast:first' }), /2 to 600/);
  assert.throws(() => normalizeSpatialVideoParameters({ prompt: '有效描述', output_ratio: '21:9' }, { sourceAssetId: 'ast:first' }), /ratio/);
  assert.throws(() => normalizeSpatialVideoParameters({ prompt: '有效描述', duration_seconds: 7 }, { sourceAssetId: 'ast:first' }), /duration/);
  assert.throws(() => normalizeSpatialVideoParameters({ prompt: '有效描述', motion_intensity: 11 }, { sourceAssetId: 'ast:first' }), /motion/);
  assert.throws(() => normalizeSpatialVideoParameters({
    prompt: '有效描述', first_frame_asset_id: 'ast:other',
  }, { sourceAssetId: 'ast:first' }), /first frame/);
  assert.throws(() => normalizeSpatialVideoParameters({
    prompt: '有效描述', last_frame_asset_id: 'job:not-an-asset',
  }, { sourceAssetId: 'ast:first' }), /last_frame_asset_id/);
});

test('billing confirmation owns a stable request id and cost-affecting edits invalidate both', () => {
  let requestCount = 0;
  const createRequestId = () => `video-request:${++requestCount}`;
  const draft = createSpatialVideoDraft({
    canvasId: 'spatial:one',
    sourceAssetId: 'ast:first',
    lineageParentId: 'ast:first',
    prompt: '商品轻微转动',
  });
  const confirmed = confirmSpatialVideoDraft(draft, true, createRequestId);
  assert.equal(confirmed.callConfirmed, true);
  assert.equal(confirmed.requestId, 'video-request:1');
  assert.equal(confirmSpatialVideoDraft(confirmed, true, createRequestId).requestId, 'video-request:1');
  assert.equal(requestCount, 1);

  const unchanged = updateSpatialVideoDraft(confirmed, { prompt: '商品轻微转动' });
  assert.equal(unchanged.callConfirmed, true);
  assert.equal(unchanged.requestId, 'video-request:1');
  const changed = updateSpatialVideoDraft(confirmed, { durationSeconds: 8 });
  assert.equal(changed.callConfirmed, false);
  assert.equal(changed.requestId, '');
  assert.equal(changed.durationSeconds, 8);
  const reconfirmed = confirmSpatialVideoDraft(changed, true, createRequestId);
  assert.equal(reconfirmed.requestId, 'video-request:2');
});

test('confirmed drafts compile to one-source one-attempt immutable command payloads', () => {
  const confirmed = confirmSpatialVideoDraft(createSpatialVideoDraft({
    canvasId: 'spatial:one',
    sourceAssetId: 'ast:first',
    lineageParentId: 'ast:first',
    prompt: '保持包装稳定并缓慢推进镜头',
    outputRatio: '16:9',
    durationSeconds: 5,
    motionIntensity: 3,
  }), true, () => 'video-request:stable');
  const payload = spatialVideoCommandPayload(confirmed);
  assert.equal(SPATIAL_VIDEO_COMMAND_ID, 'command:image-to-video');
  assert.equal(payload.client_request_id, 'video-request:stable');
  assert.deepEqual(payload.source_asset_ids, ['ast:first']);
  assert.equal(payload.spatial_canvas_id, 'spatial:one');
  assert.equal(payload.requested_concurrency, 1);
  assert.equal(payload.max_attempts, 1);
  assert.equal(payload.parameters.automatic_paid_retry, false);
  assert.equal(payload.parameters.provider_call_confirmed, false);
  assert.equal(Object.isFrozen(payload), true);
  assert.equal(Object.isFrozen(payload.parameters), true);
  assert.equal(Object.isFrozen(payload.source_asset_ids), true);
  assert.throws(() => spatialVideoCommandPayload(createSpatialVideoDraft({
    sourceAssetId: 'ast:first', prompt: '保持包装稳定',
  })), /confirmed/);
  const confirmedWithoutCanvas = confirmSpatialVideoDraft(createSpatialVideoDraft({
    sourceAssetId: 'ast:first', prompt: '保持包装稳定',
  }), true, () => 'video-request:no-canvas');
  assert.throws(
    () => spatialVideoCommandPayload(confirmedWithoutCanvas),
    /durable spatial canvas/,
  );
});

test('video job lifecycle is identified by command id instead of the shared single mode', () => {
  const job = {
    id: 'job:video',
    mode: 'single',
    status: 'queued',
    snapshot: { command_id: 'command:image-to-video' },
    items: [{ result_asset_ids: ['ast:video', 'ast:cover'] }],
  };
  assert.equal(isSpatialVideoJob(job), true);
  assert.equal(spatialVideoJobIsActive(job), true);
  assert.equal(spatialVideoJobIsSettled(job), false);
  assert.deepEqual(spatialVideoResultAssetIds(job), ['ast:video', 'ast:cover']);
  assert.equal(isSpatialVideoJob({ ...job, snapshot: {}, command_id: '' }), false);
  assert.equal(spatialVideoJobIsSettled({ ...job, status: 'failed' }), true);
  assert.equal(spatialVideoJobIsActive({ ...job, status: 'interrupted' }), false);
  assert.equal(spatialVideoJobIsSettled({ ...job, status: 'interrupted' }), true);
  assert.equal(spatialVideoCanvasId({
    ...job,
    parameters: { spatial_canvas_id: 'spatial:one' },
  }), 'spatial:one');
  assert.equal(spatialVideoCanvasId({
    ...job,
    snapshot: { ...job.snapshot, parameters: { spatial_canvas_id: 'spatial:restart' } },
  }), 'spatial:restart');
  assert.equal(spatialVideoCanvasId({ ...job, parameters: { spatial_canvas_id: '../outside' } }), '');
});

test('twenty video nodes keep at most one loaded or playing and reset on stop, switch and restart', () => {
  const ids = Array.from({ length: 20 }, (_, index) => `video-${index + 1}`);
  let runtime = createSpatialVideoPlaybackState('spatial:one');
  assert.deepEqual(spatialVideoRuntimeSnapshot(ids, runtime), {
    loadedIds: [], playingIds: [], loadedCount: 0, playingCount: 0,
  });
  runtime = selectSpatialVideo(runtime, ids[4]);
  assert.deepEqual(spatialVideoRuntimeSnapshot(ids, runtime), {
    loadedIds: [ids[4]], playingIds: [], loadedCount: 1, playingCount: 0,
  });
  runtime = playSpatialVideo(runtime, ids[4]);
  assert.deepEqual(spatialVideoRuntimeSnapshot(ids, runtime), {
    loadedIds: [ids[4]], playingIds: [ids[4]], loadedCount: 1, playingCount: 1,
  });
  runtime = pauseSpatialVideo(runtime, ids[4]);
  assert.deepEqual(spatialVideoRuntimeSnapshot(ids, runtime), {
    loadedIds: [ids[4]], playingIds: [], loadedCount: 1, playingCount: 0,
  });
  runtime = playSpatialVideo(runtime, ids[4]);
  runtime = playSpatialVideo(runtime, ids[12]);
  assert.deepEqual(spatialVideoRuntimeSnapshot(ids, runtime), {
    loadedIds: [ids[12]], playingIds: [ids[12]], loadedCount: 1, playingCount: 1,
  });
  runtime = stopSpatialVideoPlayback(runtime);
  assert.equal(spatialVideoRuntimeSnapshot(ids, runtime).loadedCount, 0);
  runtime = playSpatialVideo(runtime, ids[2]);
  runtime = switchSpatialVideoCanvas(runtime, 'spatial:two');
  assert.equal(spatialVideoRuntimeSnapshot(ids, runtime).loadedCount, 0);
  assert.equal(runtime.canvasId, 'spatial:two');
  runtime = restoreSpatialVideoPlaybackState({ canvasId: 'spatial:two', selectedId: ids[2], playingId: ids[2] });
  assert.equal(spatialVideoRuntimeSnapshot(ids, runtime).loadedCount, 0);
  assert.equal(spatialVideoRuntimeSnapshot(ids, runtime).playingCount, 0);
});
