const COMPLETION_WINDOW_MS = 45_000;

const STATE_COPY = Object.freeze({
  error: {
    label: (count) => `${count} 个任务失败`,
    detail: '打开任务中心查看原因与可重试项',
  },
  attention: {
    label: (count) => `${count} 个任务需要处理`,
    detail: '部分完成或中断，成功结果仍然保留',
  },
  active: {
    label: (count) => `${count} 个任务正在运行`,
    detail: '任务在后台继续，切换页面不会中断',
  },
  paused: {
    label: (count) => `${count} 个任务已暂停`,
    detail: '打开任务中心可以继续运行',
  },
  queued: {
    label: (count) => `${count} 个任务正在排队`,
    detail: '等待后台资源，任务现场已经保存',
  },
  complete: {
    label: (count) => `${count} 个任务刚刚完成`,
    detail: '结果已经写入本地任务账本',
  },
  idle: {
    label: () => '任务空闲',
    detail: '没有需要处理的后台任务',
  },
});

function eventTime(job) {
  for (const value of [job?.completed_at, job?.updated_at, job?.created_at]) {
    const parsed = Date.parse(String(value || ''));
    if (Number.isFinite(parsed)) return parsed;
  }
  return 0;
}

function presenceResult(state, count, overrides = {}) {
  const copy = STATE_COPY[state];
  const label = overrides.label || copy.label(count);
  const detail = overrides.detail || copy.detail;
  return {
    state,
    count,
    label,
    detail,
    ariaLabel: `任务状态：${label}。${detail}。打开任务中心`,
  };
}

export function taskPresenceModel(jobs, options = {}) {
  const items = Array.isArray(jobs) ? jobs : [];
  const now = Number.isFinite(Number(options.now)) ? Number(options.now) : Date.now();
  const completionWindowMs = Math.max(
    0,
    Number.isFinite(Number(options.completionWindowMs))
      ? Number(options.completionWindowMs)
      : COMPLETION_WINDOW_MS,
  );
  const count = (...statuses) => items.filter((job) => statuses.includes(String(job?.status || ''))).length;

  const failed = count('failed');
  if (failed) return presenceResult('error', failed);

  const attention = count('partial', 'interrupted');
  if (attention) return presenceResult('attention', attention);

  const active = count('running', 'canceling');
  if (active) return presenceResult('active', active);

  const paused = count('paused');
  if (paused) return presenceResult('paused', paused);

  const queued = count('queued');
  if (queued) return presenceResult('queued', queued);

  const recentlyCompleted = items.filter((job) => {
    const timestamp = eventTime(job);
    return job?.status === 'completed'
      && timestamp > 0
      && now - timestamp >= 0
      && now - timestamp <= completionWindowMs;
  }).length;
  if (recentlyCompleted) return presenceResult('complete', recentlyCompleted);

  if (options.available === false) {
    return presenceResult('idle', 0, {
      label: '任务状态暂不可读',
      detail: '本地账本仍然保留，等待任务接口恢复',
    });
  }
  return presenceResult('idle', 0);
}

export const TASK_PRESENCE_COMPLETION_WINDOW_MS = COMPLETION_WINDOW_MS;
