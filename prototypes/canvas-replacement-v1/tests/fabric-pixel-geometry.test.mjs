import assert from 'node:assert/strict';
import test from 'node:test';
import { Rect } from 'fabric';
import { PIXEL_ORIGIN, pixelRegion, hidePixelRegion, expandPixelCanvas } from '../src/fabric-pixel-geometry.js';

test('outpaint applies visible padding once, preserves source pixels and supports shrinking', () => {
  const source = new Rect({ ...PIXEL_ORIGIN, left: 0, top: 0, width: 2458, height: 1638, scaleX: .25, scaleY: .25, strokeWidth: 0 });
  const overlay = new Rect({ left: 30, top: 40 });
  const geometry = { width: 2458, height: 1638, scale: .25, padding: 0 };
  let dimensions;
  const canvas = { getObjects: () => [source, overlay], setDimensions(value) { dimensions = value; }, requestRenderAll() {} };
  assert.equal(expandPixelCanvas(canvas, geometry, 100), 100);
  assert.deepEqual(dimensions, { width: 664.5, height: 459.5 });
  assert.equal(source.left, 25); assert.equal(source.top, 25);
  assert.equal(overlay.left, 55); assert.equal(source.scaleX, .25);
  expandPixelCanvas(canvas, geometry, 100);
  assert.equal(source.left, 25); assert.equal(overlay.left, 55);
  expandPixelCanvas(canvas, geometry, 0);
  assert.equal(source.left, 0); assert.equal(overlay.left, 30);
  assert.deepEqual(dimensions, { width: 614.5, height: 409.5 });
  assert.equal(expandPixelCanvas(canvas, geometry, 10000), 2048);
});

test('leaving crop hides its region without deselecting a newly added overlay', () => {
  const region = new Rect(), text = new Rect();
  let active = text;
  const canvas = { getActiveObject: () => active, discardActiveObject() { active = null; } };
  hidePixelRegion(canvas, region);
  assert.equal(active, text);
  assert.equal(region.visible, false);
  assert.equal(region.selectable, false);
  assert.equal(region.evented, false);
  active = region;
  hidePixelRegion(canvas, region);
  assert.equal(active, null);
});

test('Fabric 7 explicit pixel origin starts at zero, not half an image outside the viewport', () => {
  const image = new Rect({ ...PIXEL_ORIGIN, left: 0, top: 0, width: 3072, height: 2048, scaleX: .25, scaleY: .25, strokeWidth: 0 });
  assert.deepEqual(image.getBoundingRect(), { left: 0, top: 0, width: 768, height: 512 });
});
test('crop maps display coordinates to original pixels and clips partial outside selections', () => {
  assert.deepEqual(pixelRegion({ left: 20, top: 10, width: 100, height: 50, scaleX: 1, scaleY: 1 }, .25, 800, 400), { x: 80, y: 40, width: 400, height: 200 });
  assert.deepEqual(pixelRegion({ left: -20, top: -10, width: 100, height: 50, scaleX: 1, scaleY: 1 }, .25, 800, 400), { x: 0, y: 0, width: 320, height: 160 });
  assert.throws(() => pixelRegion({ left: 1000, top: 0, width: 100, height: 50, scaleX: 1, scaleY: 1 }, 1, 800, 400), /选区/);
});
