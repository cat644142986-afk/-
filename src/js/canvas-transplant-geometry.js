// Adapted from basketikun/infinite-canvas canvas-selection-toolbar.tsx
// (MIT, e6d0911) and BeatDesign beatcanvas-composer-utils.ts
// (Apache-2.0, 13689dd). See THIRD_PARTY_NOTICES.md.
export function selectionFrame(bounds, viewport, hostRect, padding = 14) {
  if (!bounds || !viewport || !hostRect) return null;
  const [left, top, right, bottom] = bounds;
  const first = viewport({ sceneX: left, sceneY: top });
  const last = viewport({ sceneX: right, sceneY: bottom });
  if (![first.x, first.y, last.x, last.y].every(Number.isFinite)) return null;
  return {
    left: first.x - hostRect.left - padding,
    top: first.y - hostRect.top - padding,
    width: Math.max(0, last.x - first.x) + padding * 2,
    height: Math.max(0, last.y - first.y) + padding * 2,
  };
}

// BeatDesign's anchored composer placement, adapted to a toolbar whose anchor
// is an Excalidraw selection frame in the host's local viewport coordinates.
export function anchoredSurface(frame, viewportWidth, viewportHeight, {
  preferredWidth = 480, surfaceHeight = 48, edgeInset = 14, gap = 12,
  topChromeHeight = 72, topChromeLeft = 210, topChromeRight = 180,
} = {}) {
  const availableWidth = Math.max(0, viewportWidth - edgeInset * 2);
  const width = Math.min(preferredWidth, availableWidth);
  const preferredLeft = frame.left + frame.width / 2 - width / 2;
  const maximumLeft = Math.max(edgeInset, viewportWidth - edgeInset - width);
  const below = frame.top + frame.height + gap;
  const above = frame.top - gap - surfaceHeight;
  const maximumTop = Math.max(edgeInset, viewportHeight - edgeInset - surfaceHeight);
  if (below + surfaceHeight > viewportHeight - edgeInset && above < edgeInset) {
    // A tall composer cannot fit above or below a large image in a short
    // window. Use the free side of the selection instead of covering it.
    const leftRoom = frame.left - gap - edgeInset;
    const rightEdge = frame.left + frame.width;
    const rightRoom = viewportWidth - rightEdge - gap - edgeInset;
    const useLeft = leftRoom >= rightRoom;
    const room = useLeft ? leftRoom : rightRoom;
    if (room >= Math.min(preferredWidth, 275)) {
      const sideWidth = Math.min(preferredWidth, room);
      return {
        left: useLeft ? frame.left - gap - sideWidth : rightEdge + gap,
        top: Math.min(Math.max(frame.top + frame.height / 2 - surfaceHeight / 2, edgeInset), maximumTop),
        width: sideWidth,
      };
    }
  }
  const top = below + surfaceHeight <= viewportHeight - edgeInset
    ? below
    : above >= edgeInset ? above : Math.min(Math.max(below, edgeInset), maximumTop);
  let left = Math.min(Math.max(preferredLeft, edgeInset), maximumLeft);
  // The PA creation/tools strips occupy both top corners. When a large
  // selection forces the toolbar above itself, use the quiet center band.
  if (top < topChromeHeight && viewportWidth - topChromeLeft - topChromeRight >= width) {
    left = Math.min(Math.max(left, topChromeLeft), viewportWidth - topChromeRight - width);
  }
  return { left, top, width };
}

export function stopComposerKeyboardEvent(event) {
  event.stopPropagation();
  event.nativeEvent?.stopImmediatePropagation?.();
}

export function shouldDismissComposerOnEscape(event, compositionActive) {
  return event.key === 'Escape' && !compositionActive && !event.nativeEvent?.isComposing;
}
