export const APP_CLOSE_SAVE_TIMEOUT = 'APP_CLOSE_SAVE_TIMEOUT';

function timeoutError(timeoutMs) {
  const error = new Error(`Timed out after ${timeoutMs}ms while saving before close`);
  error.code = APP_CLOSE_SAVE_TIMEOUT;
  error.timeoutMs = timeoutMs;
  return error;
}

function runWithTimeout(operation, timeoutMs, timers) {
  return new Promise((resolve, reject) => {
    let settled = false;
    const timer = timers.setTimeout(() => {
      if (settled) return;
      settled = true;
      reject(timeoutError(timeoutMs));
    }, timeoutMs);

    Promise.resolve()
      .then(operation)
      .then((value) => {
        if (settled) return;
        settled = true;
        timers.clearTimeout(timer);
        resolve(value);
      }, (error) => {
        if (settled) return;
        settled = true;
        timers.clearTimeout(timer);
        reject(error);
      });
  });
}

export function createAppCloseCoordinator(options = {}) {
  const prepareForClose = options.prepareForClose;
  const completeClose = options.completeClose;
  const onStart = typeof options.onStart === 'function' ? options.onStart : () => {};
  const onFailure = typeof options.onFailure === 'function' ? options.onFailure : () => {};
  const timeoutMs = Math.max(1, Number(options.timeoutMs) || 30000);
  const timers = {
    setTimeout: options.setTimeout || globalThis.setTimeout,
    clearTimeout: options.clearTimeout || globalThis.clearTimeout,
  };
  if (typeof prepareForClose !== 'function') throw new TypeError('prepareForClose is required');
  if (typeof completeClose !== 'function') throw new TypeError('completeClose is required');

  let closeAttempt = null;
  let closeCommitted = false;

  async function attemptClose() {
    try {
      onStart();
      const prepared = await runWithTimeout(prepareForClose, timeoutMs, timers);
      if (prepared !== true) {
        const error = new Error('Workspace did not confirm that it is safe to close');
        error.code = 'APP_CLOSE_SAVE_NOT_CONFIRMED';
        throw error;
      }
      closeCommitted = true;
      await completeClose();
      return true;
    } catch (error) {
      closeCommitted = false;
      try { await onFailure(error); } catch (_) { /* reporting cannot block a retry */ }
      return false;
    } finally {
      if (!closeCommitted) closeAttempt = null;
    }
  }

  function handleCloseRequested(event) {
    event?.preventDefault?.();
    if (closeAttempt) return closeAttempt;
    if (closeCommitted) return Promise.resolve(true);
    closeAttempt = attemptClose();
    return closeAttempt;
  }

  return {
    handleCloseRequested,
    get pending() { return Boolean(closeAttempt) && !closeCommitted; },
  };
}
