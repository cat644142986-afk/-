import { spatialBusinessKey } from './spatial-canvas-items.js';

function isCanvasTarget(target) {
  return String(target?.tagName || '').toUpperCase() === 'CANVAS';
}

function isFineEditImage(element) {
  return Boolean(
    element
    && !element.isDeleted
    && element.type === 'image'
    && spatialBusinessKey(element),
  );
}

export function installSpatialFineEditGestureRouter({
  host,
  getPointerTarget,
  onOpenFineEdit,
}) {
  let opening = false;

  function captureDoubleClick(event) {
    if (!isCanvasTarget(event.target)) return;
    const element = getPointerTarget?.();
    if (!isFineEditImage(element)) return;

    event.preventDefault();
    event.stopPropagation();
    event.stopImmediatePropagation?.();
    if (opening) return;

    opening = true;
    let result;
    try {
      result = onOpenFineEdit?.(element);
    } catch (error) {
      opening = false;
      throw error;
    }
    Promise.resolve(result).then(
      () => { opening = false; },
      () => { opening = false; },
    );
  }

  host.addEventListener('dblclick', captureDoubleClick, true);
  return () => host.removeEventListener('dblclick', captureDoubleClick, true);
}
