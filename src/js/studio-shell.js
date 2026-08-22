export function workflowDockPresentation(compact, open) {
  if (!compact) {
    return {
      open: false,
      inert: false,
      backdropHidden: true,
      expanded: false,
      role: null,
      modal: null,
    };
  }
  return {
    open: Boolean(open),
    inert: !open,
    backdropHidden: !open,
    expanded: Boolean(open),
    role: 'dialog',
    modal: 'true',
  };
}

export function createWorkflowDockController({
  windowRef = window,
  documentRef = document,
  mediaQuery = '(max-width: 980px)',
  panelSelector = '#settings-panel',
  triggerSelector = '#btn-workflow-drawer',
  backdropSelector = '#task-dock-backdrop',
  initialFocusSelector = '#btn-advanced',
} = {}) {
  const media = windowRef.matchMedia(mediaQuery);
  let returnFocus = null;
  let bound = false;

  const elements = () => ({
    panel: documentRef.querySelector(panelSelector),
    trigger: documentRef.querySelector(triggerSelector),
    backdrop: documentRef.querySelector(backdropSelector),
  });

  function sync() {
    const { panel, trigger, backdrop } = elements();
    if (!panel || !trigger || !backdrop) return;
    const presentation = workflowDockPresentation(media.matches, panel.classList.contains('is-open'));
    panel.classList.toggle('is-open', presentation.open);
    panel.toggleAttribute('inert', presentation.inert);
    backdrop.hidden = presentation.backdropHidden;
    trigger.setAttribute('aria-expanded', String(presentation.expanded));
    if (presentation.role) panel.setAttribute('role', presentation.role);
    else panel.removeAttribute('role');
    if (presentation.modal) panel.setAttribute('aria-modal', presentation.modal);
    else panel.removeAttribute('aria-modal');
    if (!media.matches) returnFocus = null;
  }

  function open() {
    if (!media.matches) return;
    const { panel } = elements();
    if (!panel) return;
    returnFocus = documentRef.activeElement;
    panel.classList.add('is-open');
    sync();
    windowRef.requestAnimationFrame(() => documentRef.querySelector(initialFocusSelector)?.focus());
  }

  function close(restoreFocus = true) {
    const { panel } = elements();
    if (!panel?.classList.contains('is-open')) {
      sync();
      return;
    }
    panel.classList.remove('is-open');
    sync();
    if (restoreFocus && typeof returnFocus?.focus === 'function') returnFocus.focus();
    returnFocus = null;
  }

  function bind() {
    if (bound) return;
    media.addEventListener('change', sync);
    bound = true;
    sync();
  }

  function destroy() {
    if (!bound) return;
    media.removeEventListener('change', sync);
    bound = false;
    returnFocus = null;
  }

  return { bind, close, destroy, open, sync, media };
}
