import { reviewStateForResult } from './result-review.js';

export function memoryProjectionState({
  currentTaskId = '',
  knowledgeBundle = null,
  reviews = [],
  resultAssetId = '',
} = {}) {
  const hasTask = Boolean(String(currentTaskId || '').trim() && knowledgeBundle?.trace_bound);
  if (!hasTask) {
    return {
      status: 'disabled',
      hasTask: false,
      reviewed: false,
      title: '当前未选择任务',
      detail: '这里先展示全局事实；从结果或历史任务进入后，才显示任务 trace。',
    };
  }
  const reviewState = reviewStateForResult(reviews, resultAssetId);
  if (!reviewState.reviewed) {
    return {
      status: 'waiting_review',
      hasTask: true,
      reviewed: false,
      title: '等待结果评审',
      detail: '完成当前结果判断后，证据状态会在这里更新。',
      reviewState,
    };
  }
  const receiptStatus = String(reviewState.receipt.status || 'reviewed');
  const status = ['pending', 'approved', 'rejected', 'dismissed', 'accumulating'].includes(receiptStatus)
    ? receiptStatus
    : receiptStatus === 'no_rule_extracted' ? 'reviewed' : receiptStatus;
  const count = reviewState.receipt.independentSessions;
  const threshold = reviewState.receipt.threshold;
  const copy = {
    accumulating: `证据已记录，正在累积 ${count}/${threshold}`,
    pending: '已形成待审核建议；批准前不会影响未来任务。',
    approved: '相关建议已经人工批准并进入执行约束。',
    rejected: '相关建议已拒绝，原始评审证据仍保留。',
    dismissed: '相关建议已停用，原始评审证据仍保留。',
    reviewed: '结果评审已记录；本次没有提取出可复用规则。',
  };
  return {
    status,
    hasTask: true,
    reviewed: true,
    title: status === 'pending' ? '待审核建议已形成' : '结果评审已记录',
    detail: copy[status] || '结果评审与学习动作已写入任务账本。',
    reviewState,
  };
}
