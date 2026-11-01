import { anchoredSurface } from '../../../src/js/canvas-transplant-geometry.js';

export function composerPlacement(frame, width, height, composerHeight = 250) {
  return anchoredSurface(frame, width, height, { preferredWidth: 560, surfaceHeight: composerHeight, topChromeHeight: 80 });
}

export function selectionToolbarSide(frame, composer) {
  const above = frame.top > 126;
  if (!composer) return above ? 'top' : 'bottom';
  const intersectsHorizontally = composer.left < frame.left + frame.width + 200 && composer.left + composer.width > frame.left - 200;
  if (intersectsHorizontally && composer.top + composer.height <= frame.top + 12) return 'bottom';
  if (intersectsHorizontally && composer.top >= frame.top + frame.height - 12 && above) return 'top';
  return above ? 'top' : 'bottom';
}

export function composerOwnsEscape(event, composing) {
  return event.key === 'Escape' && !composing && !event.nativeEvent?.isComposing && !event.defaultPrevented
    && !event.target?.closest?.('[role="combobox"], [role="listbox"], [role="dialog"]');
}

// Keep the current view when the result already fits. Reveal only the new
// branch and its source, never zoom the entire unrelated document.
export function branchRevealOptions(bounds, viewport, width, height, ids) {
  const left = bounds.x * viewport.zoom + viewport.x;
  const top = bounds.y * viewport.zoom + viewport.y;
  if (left >= 24 && top >= 90 && left + bounds.width * viewport.zoom <= width - 24
    && top + bounds.height * viewport.zoom <= height - 70) return null;
  return { nodes: [...new Set(ids)].map(id => ({ id })), padding: .18, duration: 180, maxZoom: Math.min(viewport.zoom, 1) };
}
