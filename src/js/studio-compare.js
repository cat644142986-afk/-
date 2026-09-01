import { comparisonPresentation } from './workspace-state.js';
import { comparisonTargetForItems, normalizeCompareState } from './result-review.js';

export function clampCompareDivider(value) {
  const parsed = Number(value);
  return Math.max(3, Math.min(97, Number.isFinite(parsed) ? parsed : 50));
}

export function steppedCompareTransform(compareState, delta) {
  const current = normalizeCompareState(compareState);
  const zoom = Math.max(1, Math.min(4, Math.round((current.zoom + Number(delta || 0)) * 100) / 100));
  return normalizeCompareState({
    ...current,
    zoom,
    pan_x: zoom === 1 ? 0 : current.pan_x,
    pan_y: zoom === 1 ? 0 : current.pan_y,
  });
}

export function pannedCompareTransform(compareState, {
  deltaX = 0,
  deltaY = 0,
  width = 0,
  height = 0,
} = {}) {
  const current = normalizeCompareState(compareState);
  if (!(Number(width) > 0) || !(Number(height) > 0)) return current;
  return normalizeCompareState({
    ...current,
    pan_x: current.pan_x + (Number(deltaX) / Number(width)) * 100,
    pan_y: current.pan_y + (Number(deltaY) / Number(height)) * 100,
  });
}

export function resultReviewEntries(results) {
  return [
    ...(results?.main || []).map((item, index) => ({ item, index, tab: 'main' })),
    ...(results?.cutout || []).map((item, index) => ({ item, index, tab: 'cutout' })),
  ].map((entry, order) => ({
    ...entry,
    label: entry.item.version_label || `结果 ${order + 1}`,
  }));
}

export function activeResultEntry(entries, resultTab, viewerIndex) {
  return Array.from(entries || []).find((entry) => (
    entry.tab === resultTab && entry.index === viewerIndex
  )) || null;
}

export function createCompareController({
  state,
  query,
  queryAll,
  escapeHtml,
  resultDataUrl,
  statusPanelHtml,
  selectResultVersion,
  reviewController,
  snapshotFromDraft,
  scheduleWorkspaceDraftSave,
  switchPage,
  toast,
  windowRef,
  documentRef,
}) {
  let bound = false;
  let guideReturnFocus = null;

  function stateForMode(mode = state.currentMode) {
    return normalizeCompareState(
      state.modeSnapshots[mode]?.compare_state
        || state.workspaceDrafts[mode]?.compare_state
        || {},
    );
  }

  function updateState(patch, persist = true) {
    const mode = state.currentMode;
    const snapshot = state.modeSnapshots[mode]
      || snapshotFromDraft(state.workspaceDrafts[mode] || {}, {});
    const next = normalizeCompareState({
      ...stateForMode(mode),
      ...(patch || {}),
    });
    state.modeSnapshots[mode] = { ...snapshot, compare_state: next };
    if (persist) scheduleWorkspaceDraftSave(mode);
    return next;
  }

  function setGuideOpen(open, { focus = false, restore = false } = {}) {
    const guide = query('#review-guide');
    const help = query('#btn-compare-help');
    if (!guide || !help) return;
    guide.hidden = !open;
    help.setAttribute('aria-expanded', String(open));
    if (open && focus) query('#btn-review-guide-done')?.focus();
    if (!open && restore) {
      const target = guideReturnFocus instanceof windowRef.HTMLElement ? guideReturnFocus : help;
      target.focus();
    }
    if (!open) guideReturnFocus = null;
  }

  function syncPresentation(original, result) {
    if (!original?.naturalWidth || !result?.naturalWidth) return;
    const presentation = comparisonPresentation(
      { width: original.naturalWidth, height: original.naturalHeight },
      { width: result.naturalWidth, height: result.naturalHeight },
    );
    const sideBySide = presentation === 'side-by-side';
    query('#compare-view').classList.toggle('is-side-by-side', sideBySide);
    query('#compare-slider').hidden = sideBySide;
    query('#compare-mode-note').hidden = !sideBySide;
    query('#compare-view').dataset.presentation = presentation;
  }

  function setPosition(percent, persist = true) {
    const value = clampCompareDivider(percent);
    const view = query('#compare-view');
    view.style.setProperty('--compare-slide', `${value}%`);
    query('#compare-slider').setAttribute('aria-valuenow', String(Math.round(value)));
    if (persist) updateState({ divider: value });
  }

  function setTransform(compareState, persist = true) {
    const normalized = normalizeCompareState(compareState);
    const view = query('#compare-view');
    view.style.setProperty('--compare-zoom', String(normalized.zoom));
    view.style.setProperty('--compare-pan-x', `${normalized.pan_x}%`);
    view.style.setProperty('--compare-pan-y', `${normalized.pan_y}%`);
    view.classList.toggle('is-zoomed', normalized.zoom > 1);
    query('#compare-zoom-value').textContent = `${Math.round(normalized.zoom * 100)}%`;
    if (persist) updateState(normalized);
  }

  function render() {
    const rail = query('#review-version-rail');
    const entries = resultReviewEntries(state.results);
    const active = activeResultEntry(entries, state.resultTab, state.viewerIndex);
    rail.innerHTML = entries.length ? entries.map((entry) => {
      const selected = entry === active;
      const status = selected ? 'A · 当前' : (entry.item.is_parent_version ? '上一版' : '可对比');
      return `<button class="review-version ${selected ? 'is-selected' : ''}" type="button" data-review-tab="${entry.tab}" data-review-index="${entry.index}" aria-pressed="${selected}"><img src="${escapeHtml(resultDataUrl(entry.item, entry.tab))}" alt="${escapeHtml(entry.label)}" /><strong>${escapeHtml(entry.label)}</strong><small>${status}</small></button>`;
    }).join('') : statusPanelHtml('empty', { title: '等待结果版本', detail: '生成完成后可在这里选择 A。', compact: true });
    queryAll('[data-review-tab]', rail).forEach((button) => button.addEventListener('click', () => {
      state.editingFeedbackResultKey = '';
      selectResultVersion(Number(button.dataset.reviewIndex || 0), button.dataset.reviewTab);
      state.reviewReasonCodes = new Set();
      state.reviewDecision = '';
      render();
    }));

    const currentCompare = stateForMode();
    const activeItem = active?.item || null;
    const otherItems = entries.map((entry) => entry.item);
    let referenceItem = comparisonTargetForItems(
      otherItems,
      activeItem?.asset_id,
      currentCompare.secondary_result_asset_id,
    );
    if (!state.originalDataUrl && !referenceItem) {
      referenceItem = otherItems.find((item) => String(item.asset_id) !== String(activeItem?.asset_id)) || null;
    }
    const referenceUrl = referenceItem
      ? resultDataUrl(referenceItem, entries.find((entry) => entry.item === referenceItem)?.tab)
      : state.originalDataUrl;
    const activeUrl = activeItem ? resultDataUrl(activeItem, active.tab) : '';
    const has = Boolean(referenceUrl && activeUrl);
    state.compareData = has ? { original: referenceUrl, result: activeUrl } : null;

    const target = query('#compare-target');
    const targetOptions = [
      ...(state.originalDataUrl ? [{ value: 'source', label: '原图' }] : []),
      ...entries.filter((entry) => entry !== active).map((entry) => ({
        value: entry.item.asset_id,
        label: entry.label,
      })),
    ];
    target.innerHTML = targetOptions.map((option) => (
      `<option value="${escapeHtml(option.value)}">${escapeHtml(option.label)}</option>`
    )).join('');
    target.disabled = targetOptions.length < 2;
    target.value = referenceItem?.asset_id || 'source';
    query('.review-compare-toolbar').hidden = !has;
    query('#compare-empty').hidden = has;
    query('#compare-view').hidden = !has;
    setGuideOpen(has && !currentCompare.guide_dismissed);
    if (!has) {
      query('#compare-view').classList.remove('is-side-by-side', 'is-zoomed', 'is-panning');
      query('#compare-slider').hidden = false;
      query('#compare-mode-note').hidden = true;
      reviewController.renderPanel(activeItem);
      return;
    }

    const original = query('#compare-img-original');
    const result = query('#compare-img-result');
    const activeLabel = active?.label || '当前版本';
    const referenceLabel = referenceItem
      ? (entries.find((entry) => entry.item === referenceItem)?.label || '另一版本')
      : '原图';
    query('#compare-label-before').textContent = `B · ${referenceLabel}`;
    query('#compare-label-after').textContent = `A · ${activeLabel}`;
    original.alt = `对比对象 B：${referenceLabel}`;
    result.alt = `当前版本 A：${activeLabel}`;
    const sync = () => syncPresentation(original, result);
    original.onload = sync;
    result.onload = sync;
    original.src = referenceUrl;
    result.src = activeUrl;
    if (original.complete && result.complete) sync();
    setPosition(currentCompare.divider, false);
    setTransform(currentCompare, false);
    reviewController.renderPanel(activeItem);
  }

  function bindGestures() {
    const view = query('#compare-view');
    const slider = query('#compare-slider');
    let sliderDragging = false;
    let panDrag = null;
    const moveSlider = (clientX) => {
      if (view.classList.contains('is-side-by-side')) return;
      const rect = view.getBoundingClientRect();
      if (rect.width) setPosition(((clientX - rect.left) / rect.width) * 100);
    };
    slider.addEventListener('pointerdown', (event) => {
      sliderDragging = true;
      slider.setPointerCapture(event.pointerId);
      moveSlider(event.clientX);
    });
    slider.addEventListener('pointermove', (event) => { if (sliderDragging) moveSlider(event.clientX); });
    slider.addEventListener('pointerup', () => { sliderDragging = false; });
    slider.addEventListener('pointercancel', () => { sliderDragging = false; });
    slider.addEventListener('keydown', (event) => {
      if (view.classList.contains('is-side-by-side')) return;
      const current = Number(slider.getAttribute('aria-valuenow') || 50);
      const delta = event.shiftKey ? 10 : 2;
      if (event.key === 'ArrowLeft') { event.preventDefault(); setPosition(current - delta); }
      if (event.key === 'ArrowRight') { event.preventDefault(); setPosition(current + delta); }
      if (event.key === 'Home') { event.preventDefault(); setPosition(3); }
      if (event.key === 'End') { event.preventDefault(); setPosition(97); }
    });
    view.addEventListener('pointerdown', (event) => {
      if (event.target.closest('#compare-slider') || stateForMode().zoom <= 1) return;
      const current = stateForMode();
      panDrag = {
        pointerId: event.pointerId,
        x: event.clientX,
        y: event.clientY,
        panX: current.pan_x,
        panY: current.pan_y,
        active: false,
      };
      view.setPointerCapture(event.pointerId);
    });
    view.addEventListener('pointermove', (event) => {
      if (!panDrag || panDrag.pointerId !== event.pointerId) return;
      const rect = view.getBoundingClientRect();
      if (!rect.width || !rect.height) return;
      const deltaX = event.clientX - panDrag.x;
      const deltaY = event.clientY - panDrag.y;
      if (!panDrag.active && Math.hypot(deltaX, deltaY) < 4) return;
      if (!panDrag.active) {
        panDrag.active = true;
        view.classList.add('is-panning');
      }
      setTransform(pannedCompareTransform({
        ...stateForMode(),
        pan_x: panDrag.panX,
        pan_y: panDrag.panY,
      }, {
        deltaX,
        deltaY,
        width: rect.width,
        height: rect.height,
      }));
    });
    const endPan = (event) => {
      if (!panDrag || panDrag.pointerId !== event.pointerId) return;
      panDrag = null;
      view.classList.remove('is-panning');
    };
    view.addEventListener('pointerup', endPan);
    view.addEventListener('pointercancel', endPan);
  }

  function bind() {
    if (bound) return;
    bound = true;
    const guide = query('#review-guide');
    const help = query('#btn-compare-help');
    guide.setAttribute('role', 'dialog');
    guide.setAttribute('aria-modal', 'false');
    help.setAttribute('aria-controls', 'review-guide');
    help.setAttribute('aria-expanded', String(!guide.hidden));
    bindGestures();
    query('#btn-open-compare').addEventListener('click', () => { render(); switchPage('compare'); });
    query('#btn-compare-back').addEventListener('click', () => switchPage('process'));
    query('#compare-target').addEventListener('change', (event) => {
      const value = String(event.target.value || 'source');
      updateState({ secondary_result_asset_id: value === 'source' ? '' : value });
      render();
    });
    query('#btn-compare-zoom-out').addEventListener('click', () => {
      setTransform(steppedCompareTransform(stateForMode(), -0.25));
    });
    query('#btn-compare-zoom-in').addEventListener('click', () => {
      setTransform(steppedCompareTransform(stateForMode(), 0.25));
    });
    query('#btn-compare-reset').addEventListener('click', () => {
      const reset = updateState({ divider: 50, zoom: 1, pan_x: 0, pan_y: 0 });
      setPosition(reset.divider, false);
      setTransform(reset, false);
      toast('对比位置与缩放已复位');
    });
    help.addEventListener('click', () => {
      guideReturnFocus = documentRef.activeElement;
      setGuideOpen(true, { focus: true });
    });
    query('#btn-review-guide-done').addEventListener('click', () => {
      updateState({ guide_dismissed: true });
      setGuideOpen(false, { restore: true });
    });
  }

  return {
    bind,
    render,
    setGuideOpen,
    setPosition,
    setTransform,
    stateForMode,
    updateState,
  };
}
