import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import test from 'node:test';
import { fileURLToPath } from 'node:url';
import {
  POC_FRAME_COUNT,
  POC_IMAGE_COUNT,
  POC_VIDEO_COUNT,
  REQUIRED_BUSINESS_KEYS,
  createPocSceneSkeletons,
  restorePocScene,
  sceneContentFingerprint,
  sceneMetrics,
  serializePocScene,
} from '../src/scene-model.js';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');

test('runtime dependencies are exact and locked', () => {
  const manifest = JSON.parse(fs.readFileSync(path.join(root, 'package.json'), 'utf8'));
  const lock = JSON.parse(fs.readFileSync(path.join(root, 'package-lock.json'), 'utf8'));
  assert.equal(manifest.dependencies['@excalidraw/excalidraw'], '0.18.1');
  assert.equal(manifest.dependencies.react, '18.3.1');
  assert.equal(manifest.dependencies['react-dom'], '18.3.1');
  assert.equal(manifest.devDependencies.vite, '6.4.3');
  assert.equal(manifest.overrides['@excalidraw/excalidraw'].nanoid, '3.3.18');
  assert.equal(manifest.overrides['lodash-es'], '4.18.1');
  assert.equal(lock.packages['node_modules/@excalidraw/excalidraw'].version, '0.18.1');
  assert.equal(lock.packages['node_modules/react'].version, '18.3.1');
  assert.equal(lock.packages['node_modules/react-dom'].version, '18.3.1');
  assert.equal(lock.packages['node_modules/vite'].version, '6.4.3');
  assert.equal(lock.packages['node_modules/nanoid'].version, '3.3.18');
  assert.equal(lock.packages['node_modules/lodash-es'].version, '4.18.1');
});

test('scene contains 200 proxy images, 20 videos, frames and lineage branches', () => {
  const { skeletons, files } = createPocSceneSkeletons();
  const metrics = sceneMetrics(skeletons, files);
  assert.deepEqual(metrics, {
    elements: 270,
    images: POC_IMAGE_COUNT,
    videos: POC_VIDEO_COUNT,
    frames: POC_FRAME_COUNT,
    lineage: 40,
    files: POC_IMAGE_COUNT,
  });
});

test('business nodes preserve the complete Product Atelier reference envelope', () => {
  const { skeletons } = createPocSceneSkeletons();
  const nodes = skeletons.filter((element) => ['image', 'embeddable'].includes(element.type));
  assert.equal(nodes.length, POC_IMAGE_COUNT + POC_VIDEO_COUNT);
  for (const node of nodes) {
    for (const key of REQUIRED_BUSINESS_KEYS) {
      assert.equal(Object.hasOwn(node.customData, key), true, `${node.id} missing ${key}`);
    }
    assert.equal(node.roughness, 0);
  }
});

test('persisted scene excludes proxy bytes, originals and machine paths', () => {
  const { skeletons, files } = createPocSceneSkeletons();
  assert.match(files['proxy-file-001'].dataURL, /^data:image\/svg\+xml;base64,/);
  const payload = serializePocScene(skeletons, {
    scrollX: 42,
    scrollY: -31,
    zoom: { value: 0.6 },
    viewBackgroundColor: '#d4d0cb',
  });
  const serialized = JSON.stringify(payload);
  assert.doesNotMatch(serialized, /data:/i);
  assert.doesNotMatch(serialized, /[A-Za-z]:\\\\/);
  assert.equal(Object.hasOwn(payload, 'files'), false);
  assert.ok(serialized.length < 400_000, `scene JSON unexpectedly large: ${serialized.length}`);
});

test('restoring rebuilds proxies while preserving viewport and video metadata', () => {
  const { skeletons } = createPocSceneSkeletons();
  const moved = skeletons.find((element) => element.id === 'image-poc-006');
  moved.x += 320;
  moved.y += 180;
  moved.locked = true;
  const stored = serializePocScene(skeletons, {
    scrollX: 120,
    scrollY: 80,
    zoom: { value: 0.45 },
    viewBackgroundColor: '#d4d0cb',
  });
  const restored = restorePocScene(JSON.stringify(stored));
  assert.equal(Object.keys(restored.files).length, POC_IMAGE_COUNT);
  assert.equal(restored.appState.zoom.value, 0.45);
  assert.equal(restored.appState.scrollX, 120);
  const restoredImage = restored.elements.find((item) => item.id === 'image-poc-006');
  assert.equal(restoredImage.x, moved.x);
  assert.equal(restoredImage.y, moved.y);
  assert.equal(restoredImage.locked, true);
  assert.equal(restoredImage.frameId, moved.frameId);
  assert.deepEqual(restoredImage.groupIds, moved.groupIds);
  assert.equal(restored.elements.find((item) => item.id === 'video-poc-001').customData.duration_seconds, 6);
  assert.equal(restored.elements.find((item) => item.id === 'lineage-arrow-005').customData.lineage_parent_id, 'asset-poc-004');
});

test('video nodes are custom embeddables and never persist playable media bytes', () => {
  const { skeletons } = createPocSceneSkeletons();
  const videos = skeletons.filter((element) => element.type === 'embeddable');
  assert.equal(videos.length, POC_VIDEO_COUNT);
  for (const video of videos) {
    assert.match(video.link, /^product-atelier-video:\/\//);
    assert.match(video.customData.cover_ref, /^synthetic:\/\/video-cover\//);
    assert.equal(Object.hasOwn(video.customData, 'duration_seconds'), true);
    assert.equal(Object.hasOwn(video.customData, 'pixel_width'), true);
    assert.equal(Object.hasOwn(video.customData, 'pixel_height'), true);
  }
});

test('content fingerprint ignores transient selection but changes for persisted edits', () => {
  const { skeletons } = createPocSceneSkeletons();
  const baseState = {
    scrollX: 80,
    scrollY: 80,
    zoom: { value: 0.2 },
    viewBackgroundColor: '#d4d0cb',
    selectedElementIds: {},
  };
  const initial = sceneContentFingerprint(skeletons, baseState);
  assert.equal(sceneContentFingerprint(skeletons, {
    ...baseState,
    selectedElementIds: { 'video-poc-001': true },
  }), initial);

  const moved = structuredClone(skeletons);
  moved.find((element) => element.id === 'image-poc-001').x += 10;
  assert.notEqual(sceneContentFingerprint(moved, baseState), initial);
  assert.notEqual(sceneContentFingerprint(skeletons, {
    ...baseState,
    zoom: { value: 0.4 },
  }), initial);
});
