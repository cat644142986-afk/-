// Fabric 7 defaults to CENTER origins. Pixel coordinates in this adapter are
// explicitly top-left; do not modify global Fabric defaults or user documents.
export const PIXEL_ORIGIN = Object.freeze({ originX: 'left', originY: 'top' });

export function hidePixelRegion(canvas, region) {
  region.set({ visible: false, selectable: false, evented: false });
  // Changing tools must not discard a newly selected text/brush overlay.
  if (canvas.getActiveObject() === region) canvas.discardActiveObject();
}

export function expandPixelCanvas(canvas, geometry, padding) {
  const next = Math.max(0, Math.min(2048, Math.round(Number(padding) || 0)));
  const delta = (next - geometry.padding) * geometry.scale;
  canvas.getObjects().forEach(object => {
    object.set({ left: object.left + delta, top: object.top + delta });
    object.setCoords();
  });
  canvas.setDimensions({ width: (geometry.width + next * 2) * geometry.scale, height: (geometry.height + next * 2) * geometry.scale });
  geometry.padding = next;
  canvas.requestRenderAll();
  return next;
}

export function pixelRegion(region, scale, width, height) {
  const left = region.left / scale, top = region.top / scale;
  const right = left + region.width * region.scaleX / scale;
  const bottom = top + region.height * region.scaleY / scale;
  const x = Math.max(0, Math.min(width, Math.round(left)));
  const y = Math.max(0, Math.min(height, Math.round(top)));
  const w = Math.max(0, Math.min(width, Math.round(right)) - x);
  const h = Math.max(0, Math.min(height, Math.round(bottom)) - y);
  if (w < 1 || h < 1) throw new Error('选区在图片之外；请将选区移动回图片');
  return { x, y, width: w, height: h };
}
