import test from 'node:test';
import assert from 'node:assert/strict';

import {
  MAX_SPATIAL_IMAGE_BYTES,
  MAX_SPATIAL_VIDEO_BYTES,
  createVideoImportDescriptor,
  isVideoAsset,
  partitionSpatialImportFiles,
} from '../../src/js/spatial-media-import.js';

function source(name, type, size = 128) {
  return { name, type, size, lastModified: 1 };
}

test('spatial import partitions supported images and videos without widening quick-task formats', () => {
  const image = source('product.png', 'image/png');
  const video = source('motion.webm', 'video/webm');
  const untypedVideo = source('motion.mp4', '');
  const invalid = source('notes.txt', 'text/plain');
  const oversizedImage = source('large.jpg', 'image/jpeg', MAX_SPATIAL_IMAGE_BYTES + 1);
  const oversizedVideo = source('large.mp4', 'video/mp4', MAX_SPATIAL_VIDEO_BYTES + 1);
  const result = partitionSpatialImportFiles([
    image, video, untypedVideo, invalid, oversizedImage, oversizedVideo,
  ]);

  assert.deepEqual(result.images, [image]);
  assert.deepEqual(result.videos, [video, untypedVideo]);
  assert.deepEqual(result.rejected.map((entry) => entry.code), [
    'UNSUPPORTED_FORMAT', 'FILE_TOO_LARGE', 'FILE_TOO_LARGE',
  ]);
});

test('video assets are identified for exclusion from image-only task inputs', () => {
  assert.equal(isVideoAsset({ kind: 'video', mime: '' }), true);
  assert.equal(isVideoAsset({ kind: 'image', mime: 'video/mp4' }), true);
  assert.equal(isVideoAsset({ kind: 'image', mime: 'image/png' }), false);
});

test('spatial import rejects the whole oversized batch before decoding media', () => {
  const files = Array.from({ length: 101 }, (_, index) => source(`${index}.png`, 'image/png'));
  const result = partitionSpatialImportFiles(files);
  assert.equal(result.images.length, 0);
  assert.equal(result.videos.length, 0);
  assert.equal(result.rejected.length, 101);
  assert.ok(result.rejected.every((entry) => entry.code === 'TOO_MANY_FILES'));
});

test('video descriptor reads metadata, seeks one frame, emits a bounded JPEG cover and revokes URL', async () => {
  class FakeVideo extends EventTarget {
    constructor() {
      super();
      this.videoWidth = 1920;
      this.videoHeight = 1080;
      this.duration = 10;
      this.readyState = 2;
      this._currentTime = 0;
      this.paused = false;
    }
    load() {
      if (this.src) queueMicrotask(() => this.dispatchEvent(new Event('loadedmetadata')));
    }
    pause() { this.paused = true; }
    removeAttribute() { this.src = ''; }
    get currentTime() { return this._currentTime; }
    set currentTime(value) {
      this._currentTime = value;
      queueMicrotask(() => this.dispatchEvent(new Event('seeked')));
    }
  }
  const video = new FakeVideo();
  const canvas = {
    width: 0,
    height: 0,
    getContext: () => ({ drawImage: () => {} }),
    toBlob: (callback) => callback(new Blob(['cover'], { type: 'image/jpeg' })),
  };
  const documentRef = { createElement: (tag) => (tag === 'video' ? video : canvas) };
  let revoked = '';
  const urlApi = {
    createObjectURL: () => 'blob:video-test',
    revokeObjectURL: (value) => { revoked = value; },
  };
  class FakeFile {
    constructor(parts, name, options) { this.parts = parts; this.name = name; this.type = options.type; }
  }

  const descriptor = await createVideoImportDescriptor(source('product-demo.webm', 'video/webm'), {
    documentRef, urlApi, FileCtor: FakeFile, timeoutMs: 1000,
  });
  assert.equal(descriptor.width, 1920);
  assert.equal(descriptor.height, 1080);
  assert.equal(descriptor.durationSeconds, 10);
  assert.equal(descriptor.cover.name, 'product-demo-cover.jpg');
  assert.equal(descriptor.cover.type, 'image/jpeg');
  assert.equal(canvas.width, 960);
  assert.equal(canvas.height, 540);
  assert.equal(video.currentTime, 0.1);
  assert.equal(video.paused, true);
  assert.equal(revoked, 'blob:video-test');
});
