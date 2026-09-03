const DEFINITIONS = {
  queued: {
    label: '排队中',
    tone: 'queued',
    icon: 'clock-3',
    family: 'processing',
    presence: 'queued',
    nextAction: '查看队列',
    recovery: '任务现场已经保存，等待后台资源即可继续。',
  },
  running: {
    label: '运行中',
    tone: 'running',
    icon: 'loader-circle',
    family: 'processing',
    presence: 'active',
    nextAction: '查看进度',
    recovery: '任务在后台继续，切换页面不会中断。',
  },
  paused: {
    label: '已暂停',
    tone: 'paused',
    icon: 'pause',
    family: 'attention',
    presence: 'paused',
    nextAction: '继续任务',
    recovery: '继续后会从剩余项目接着运行。',
  },
  completed: {
    label: '已完成',
    tone: 'completed',
    icon: 'circle-check',
    family: 'completed',
    presence: 'complete',
    nextAction: '打开结果',
    recovery: '结果已经写入本地任务账本。',
  },
  partial: {
    label: '部分完成',
    tone: 'partial',
    icon: 'triangle-alert',
    family: 'attention',
    presence: 'attention',
    nextAction: '只重试失败项',
    recovery: '成功结果保持不变，只需处理失败项目。',
  },
  failed: {
    label: '失败',
    tone: 'failed',
    icon: 'circle-alert',
    family: 'attention',
    presence: 'error',
    nextAction: '查看失败原因',
    recovery: '按失败说明重试或更换素材，历史结果不会丢失。',
  },
  canceling: {
    label: '正在取消',
    tone: 'canceling',
    icon: 'loader-circle',
    family: 'processing',
    presence: 'active',
    nextAction: '等待取消完成',
    recovery: '已完成的项目仍会保留在任务账本。',
  },
  canceled: {
    label: '已取消',
    tone: 'canceled',
    icon: 'ban',
    family: 'other',
    presence: 'idle',
    nextAction: '回到现场',
    recovery: '任务不会自动重启，可以从原现场重新发起。',
  },
  interrupted: {
    label: '已中断',
    tone: 'interrupted',
    icon: 'triangle-alert',
    family: 'attention',
    presence: 'attention',
    nextAction: '恢复任务',
    recovery: '素材、参数和已成功结果仍保存在本地账本。',
  },
};

const UNKNOWN = Object.freeze({
  label: '状态未知',
  tone: 'unknown',
  icon: 'circle-alert',
  family: 'other',
  presence: 'idle',
  nextAction: '刷新状态',
  recovery: '刷新任务中心；本地任务记录不会因此清空。',
});

export const TASK_STATUS = Object.freeze(Object.fromEntries(
  Object.entries(DEFINITIONS).map(([status, definition]) => [status, Object.freeze({ status, ...definition })]),
));

export function taskStatusPresentation(status) {
  return TASK_STATUS[String(status || '')] || UNKNOWN;
}

export function taskStatusSummary(jobs) {
  const summary = { completed: 0, processing: 0, attention: 0, other: 0 };
  for (const job of Array.isArray(jobs) ? jobs : []) {
    summary[taskStatusPresentation(job?.status).family] += 1;
  }
  return summary;
}

export function taskStatusNextAction(status, options = {}) {
  const normalized = String(status || '');
  const video = Boolean(options.video);
  const hasResults = Boolean(options.hasResults);
  const retryableCount = Math.max(0, Number(options.retryableCount) || 0);

  if (video) {
    if (['failed', 'interrupted', 'canceled'].includes(normalized)) return '返回画布重新确认';
    return '打开画布';
  }
  if (['partial', 'failed', 'interrupted'].includes(normalized) && retryableCount) {
    return '只重试失败项';
  }
  if (normalized === 'paused') return '继续任务';
  if (hasResults) return '打开结果';
  if (['queued', 'running'].includes(normalized)) return '回到现场';
  return taskStatusPresentation(normalized).nextAction;
}

export const TASK_STATUS_IDS = Object.freeze(Object.keys(TASK_STATUS));
