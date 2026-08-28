function normalizeLearningReceipt(receipt, review) {
  const source = receipt && typeof receipt === 'object' ? receipt : {};
  const learningAction = String(review?.learning_action || 'none');
  return {
    status: String(source.status || (learningAction === 'none' ? 'reviewed' : 'evidence_recorded')),
    extractedRule: Boolean(source.extracted_rule ?? source.extractedRule),
    independentSessions: Math.max(0, Number(
      source.independent_sessions ?? source.independentSessions ?? 0,
    ) || 0),
    threshold: Math.max(0, Number(source.threshold || 0) || 0),
    suggestionId: String(source.suggestion_id ?? source.suggestionId ?? review?.suggestion_id ?? ''),
    suggestionStatus: String(source.suggestion_status ?? source.suggestionStatus ?? ''),
    nextAction: String(source.next_action ?? source.nextAction ?? ''),
  };
}

const REVIEW_REASON_GROUPS = Object.freeze({
  adopted: Object.freeze([
    { code: 'subject_accurate', label: '主体准确' },
    { code: 'packaging_clean', label: '包装文字清楚' },
    { code: 'composition_ready', label: '构图可直接使用' },
    { code: 'lighting_natural', label: '光影自然' },
    { code: 'color_material_right', label: '色彩与材质准确' },
  ]),
  adjusted: Object.freeze([
    { code: 'subject_scale', label: '主体比例/数量' },
    { code: 'packaging_text', label: '包装文字' },
    { code: 'composition_crop', label: '构图与裁切' },
    { code: 'perspective_shape', label: '透视与形体' },
    { code: 'lighting_shadow', label: '光影与阴影' },
    { code: 'color_material', label: '色彩与材质' },
    { code: 'background_scene', label: '背景与场景' },
    { code: 'detail_artifact', label: '细节瑕疵' },
  ]),
  rejected: Object.freeze([
    { code: 'product_identity_wrong', label: '商品特征不对' },
    { code: 'quantity_wrong', label: '商品数量不对' },
    { code: 'packaging_unusable', label: '包装文字不可用' },
    { code: 'composition_direction_wrong', label: '构图方向不对' },
    { code: 'style_mismatch', label: '风格不符合' },
    { code: 'severe_distortion', label: '严重变形' },
    { code: 'background_wrong', label: '背景方向不对' },
  ]),
});

function clampNumber(value, minimum, maximum, fallback) {
  const parsed = Number(value);
  if (!Number.isFinite(parsed)) return fallback;
  return Math.max(minimum, Math.min(maximum, parsed));
}

export function reviewReasonOptions(decision) {
  return REVIEW_REASON_GROUPS[String(decision || '')] || [];
}

export function normalizeReviewReasonCodes(decision, reasonCodes) {
  const allowed = new Set(reviewReasonOptions(decision).map((item) => item.code));
  return [...new Set(Array.from(reasonCodes || [], (code) => String(code || '').trim()))]
    .filter((code) => allowed.has(code))
    .slice(0, 8);
}

export function reviewReasonLabel(code) {
  const target = String(code || '');
  for (const options of Object.values(REVIEW_REASON_GROUPS)) {
    const match = options.find((item) => item.code === target);
    if (match) return match.label;
  }
  return target;
}

export function normalizeCompareState(compareState) {
  const source = compareState && typeof compareState === 'object' ? compareState : {};
  return {
    divider: clampNumber(source.divider ?? source.position, 3, 97, 50),
    zoom: clampNumber(source.zoom, 1, 4, 1),
    pan_x: clampNumber(source.pan_x ?? source.panX, -100, 100, 0),
    pan_y: clampNumber(source.pan_y ?? source.panY, -100, 100, 0),
    secondary_result_asset_id: String(
      source.secondary_result_asset_id ?? source.secondaryResultAssetId ?? '',
    ),
    guide_dismissed: Boolean(source.guide_dismissed ?? source.guideDismissed),
  };
}

export function comparisonTargetForItems(items, activeResultAssetId, preferredResultAssetId = '') {
  const active = String(activeResultAssetId || '');
  const preferred = String(preferredResultAssetId || '');
  const candidates = Array.from(items || []).filter((item) => (
    String(item?.asset_id || '') && String(item?.asset_id || '') !== active
  ));
  if (preferred) {
    const match = candidates.find((item) => String(item.asset_id) === preferred);
    if (match) return match;
  }
  return null;
}

export function reviewStateForResult(reviews, resultAssetId) {
  const target = String(resultAssetId || '').trim();
  const matching = Array.from(reviews || []).filter((review) => (
    String(review?.result_asset_id || '') === target
    && String(review?.status || 'submitted') !== 'retracted'
  ));
  const review = matching.at(-1) || null;
  if (!review) {
    return {
      reviewed: false,
      showForm: true,
      reviewId: '',
      decision: '',
      learningAction: 'none',
      receipt: normalizeLearningReceipt(null, null),
      review: null,
    };
  }
  return {
    reviewed: true,
    showForm: false,
    reviewId: String(review.id || ''),
    decision: String(review.decision || ''),
    learningAction: String(review.learning_action || 'none'),
    receipt: normalizeLearningReceipt(review.learning_receipt, review),
    review,
  };
}

export function feedbackReceiptCopy(receipt) {
  const status = String(receipt?.status || 'reviewed');
  const count = Math.max(0, Number(receipt?.independentSessions || 0));
  const threshold = Math.max(0, Number(receipt?.threshold || 0));
  if (status === 'no_rule_extracted') return '评审已记录；本次未提取出可复用规则';
  if (status === 'accumulating') return `证据已记录 · 正在累积 ${count}/${threshold}`;
  if (status === 'ready_to_suggest') return '证据已达到阈值，可形成待审核建议';
  if (status === 'pending') return '已形成待审核建议；批准前不会影响未来任务';
  if (status === 'approved') return '相关建议已批准并进入正式执行约束';
  if (status === 'rejected' || status === 'dismissed') return '证据已保留；相关建议当前未采用';
  if (status === 'conflicting_evidence') return '证据已记录；存在相反判断，等待人工核对';
  if (status === 'adjustment_queued') return '调整任务已独立入队；原版本与反馈均已保留';
  if (status === 'adjustment_running') return '正在生成新版本；原版本不会被覆盖';
  if (status === 'adjustment_completed') return '新版本已完成；可在版本对比中查看';
  if (status === 'adjustment_partial') return '新版本已有可用结果，但仍有项目需要处理';
  if (status === 'adjustment_failed') return '调整任务未完成；原版本和失败现场均已保留';
  if (status === 'regenerate_deferred') return '重做请求已记录；自动重做将在后续阶段接通';
  if (status === 'evidence_missing') return '评审已存在，但证据回执不完整；请重试补齐';
  return '结果评审已写入本地任务账本';
}

export function backendDecisionForSignal(signal) {
  return {
    adopted: 'adopt',
    final_artwork: 'adopt',
    rejected: 'reject',
    adjusted: 'adjust',
    note: 'adjust',
  }[String(signal || '')] || 'adjust';
}
