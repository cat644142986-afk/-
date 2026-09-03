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

function isEditableTarget(target) {
  let node = target;
  while (node) {
    const tagName = String(node.tagName || '').toUpperCase();
    if (['INPUT', 'TEXTAREA', 'SELECT', 'BUTTON'].includes(tagName)) return true;
    if (tagName === 'A' && (node.hasAttribute?.('href') || node.href)) return true;
    if (node.isContentEditable) return true;
    const contentEditable = node.getAttribute?.('contenteditable');
    if (contentEditable !== undefined && contentEditable !== null && contentEditable !== 'false') {
      return true;
    }
    node = node.parentElement;
  }
  return false;
}

export function installSpatialFineEditGestureRouter({
  host,
  getPointerTarget,
  isBusinessImageSelected,
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

  function captureKeyDown(event) {
    if (event.key !== 'Enter') return;
    if (isEditableTarget(event.target) || !isBusinessImageSelected?.()) return;

    event.preventDefault();
    event.stopPropagation();
    event.stopImmediatePropagation?.();
  }

  host.addEventListener('dblclick', captureDoubleClick, true);
  host.addEventListener('keydown', captureKeyDown, true);
  return () => {
    host.removeEventListener('dblclick', captureDoubleClick, true);
    host.removeEventListener('keydown', captureKeyDown, true);
  };
}
