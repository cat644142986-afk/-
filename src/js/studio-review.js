import {
  feedbackReceiptCopy,
  normalizeReviewReasonCodes,
  reviewReasonLabel,
  reviewReasonOptions,
  reviewStateForResult,
} from './result-review.js';

export function reviewDecisionSummaryCopy(decision) {
  return ({
    adopt: '已确认可以直接使用',
    adjust: '已记录需要调整',
    reject: '已记录整体方向不对',
  })[String(decision || '')] || '本版本已完成评审';
}

export function createReviewController({
  state,
  query,
  queryAll,
  escapeHtml,
  activeFeedbackResultKey,
  discardPendingReviewRequest,
}) {
  function renderFeedback() {
    const entry = query('#feedback-entry');
    const receipt = query('#feedback-receipt');
    const detail = query('#feedback-detail');
    if (!entry || !receipt || !detail) return;
    entry.hidden = state.feedbackRecorded;
    receipt.hidden = !state.feedbackRecorded;
    detail.hidden = state.feedbackRecorded
      || !['rejected', 'note'].includes(state.lastFeedbackSignal);
    query('#feedback-receipt-copy').textContent = state.feedbackReceipt
      || '已记录为下一版证据';
    const suggestionButton = query('#btn-feedback-suggestion');
    suggestionButton.hidden = !state.feedbackRecorded || !state.feedbackSuggestionId;
    suggestionButton.dataset.suggestionId = state.feedbackSuggestionId || '';
    ['#btn-adopt', '#btn-reject', '#btn-feedback'].forEach((selector) => {
      const button = query(selector);
      button.disabled = state.feedbackSubmitting;
      button.setAttribute('aria-busy', String(state.feedbackSubmitting));
    });
    ['#btn-review-adjust', '#btn-review-record', '#btn-review-suggest'].forEach((selector) => {
      const button = query(selector);
      if (!button) return;
      button.disabled = state.feedbackSubmitting;
      button.setAttribute('aria-busy', String(state.feedbackSubmitting));
    });
    query('#btn-feedback').textContent = state.feedbackSubmitting ? '记录中…' : '发送';
  }

  function prepareFeedback(item) {
    const key = activeFeedbackResultKey(item);
    const durable = reviewStateForResult(state.resultReviews, item?.asset_id || '');
    if (state.feedbackResultKey !== key) {
      state.feedbackResultKey = key;
      state.feedbackSubmitting = false;
      if (state.editingFeedbackResultKey !== key) state.editingFeedbackResultKey = '';
      query('#feedback-input').value = '';
    }
    const editing = state.editingFeedbackResultKey === key;
    state.feedbackRecorded = durable.reviewed && !editing;
    state.feedbackReceipt = durable.reviewed ? feedbackReceiptCopy(durable.receipt) : '';
    state.feedbackSuggestionId = durable.reviewed ? durable.receipt.suggestionId : '';
    if (durable.reviewed && !editing) {
      discardPendingReviewRequest(item?.asset_id || '');
      const decision = String(durable.decision || '');
      state.lastFeedbackSignal = decision === 'reject'
        ? 'rejected'
        : decision === 'adopt' ? 'adopted' : 'adjusted';
    } else if (!state.lastFeedbackSignal) {
      state.lastFeedbackSignal = 'note';
    }
    renderFeedback();
  }

  function clearForm() {
    state.reviewDecision = '';
    state.reviewReasonCodes = new Set();
    query('#review-reason-input').value = '';
    query('#review-reason').hidden = true;
    query('#btn-review-adjust').hidden = true;
    queryAll('[data-review-decision]').forEach((button) => {
      button.classList.remove('is-selected');
    });
  }

  function renderReasonTags() {
    const wrap = query('#review-reason-tags');
    const options = reviewReasonOptions(state.reviewDecision);
    wrap.innerHTML = options.map((option) => {
      const selected = state.reviewReasonCodes.has(option.code);
      return `<button type="button" data-review-reason="${escapeHtml(option.code)}" aria-pressed="${selected}">${escapeHtml(option.label)}</button>`;
    }).join('');
    queryAll('[data-review-reason]', wrap).forEach((button) => {
      button.addEventListener('click', () => {
        const code = String(button.dataset.reviewReason || '');
        if (state.reviewReasonCodes.has(code)) state.reviewReasonCodes.delete(code);
        else state.reviewReasonCodes.add(code);
        button.setAttribute('aria-pressed', String(state.reviewReasonCodes.has(code)));
      });
    });
  }

  function activateDecision(decision, { reasonCodes = [], note = '' } = {}) {
    const normalized = String(decision || '');
    state.reviewDecision = normalized;
    state.reviewReasonCodes = new Set(normalizeReviewReasonCodes(normalized, reasonCodes));
    queryAll('[data-review-decision]').forEach((button) => {
      button.classList.toggle('is-selected', button.dataset.reviewDecision === normalized);
    });
    query('#review-reason').hidden = !normalized;
    query('#btn-review-adjust').hidden = normalized !== 'adjusted';
    query('#review-reason-input').value = String(note || '');
    renderReasonTags();
  }

  function renderPanel(item) {
    const durable = reviewStateForResult(state.resultReviews, item?.asset_id || '');
    const key = activeFeedbackResultKey(item);
    if (state.reviewFormResultKey !== key) {
      clearForm();
      state.reviewFormResultKey = key;
    }
    const editing = state.editingFeedbackResultKey === key;
    const summary = query('#review-summary');
    const options = query('#review-options');
    if (durable.reviewed && !editing) {
      summary.hidden = false;
      options.hidden = true;
      query('#review-reason').hidden = true;
      query('#review-summary-title').textContent = reviewDecisionSummaryCopy(durable.decision);
      query('#review-summary-copy').textContent = feedbackReceiptCopy(durable.receipt);
      const codes = Array.isArray(durable.review?.reason_codes)
        ? durable.review.reason_codes
        : [];
      query('#review-summary-tags').innerHTML = codes
        .map((code) => `<span>${escapeHtml(reviewReasonLabel(code))}</span>`)
        .join('');
      const suggestion = query('#btn-review-summary-suggestion');
      suggestion.hidden = !durable.receipt.suggestionId;
      suggestion.dataset.suggestionId = durable.receipt.suggestionId || '';
      clearForm();
      return;
    }
    summary.hidden = true;
    options.hidden = false;
    if (editing && durable.reviewed && !state.reviewDecision) {
      const signal = durable.decision === 'adopt'
        ? 'adopted'
        : durable.decision === 'reject' ? 'rejected' : 'adjusted';
      activateDecision(signal, {
        reasonCodes: durable.review?.reason_codes || [],
        note: durable.review?.note || '',
      });
    }
  }

  return {
    activateDecision,
    clearForm,
    prepareFeedback,
    renderFeedback,
    renderPanel,
  };
}
