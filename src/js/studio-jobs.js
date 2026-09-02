export const JOB_FILTER_IDS = Object.freeze([
  'all',
  'single',
  'multi-file',
  'group-split',
  'cutout-batch',
]);

export function jobsForFilter(jobs, filter = 'all') {
  const items = Array.isArray(jobs) ? jobs : [];
  if (filter === 'all' || !JOB_FILTER_IDS.includes(filter)) return items;
  return items.filter((job) => job?.mode === filter);
}

export function jobFilterCounts(jobs) {
  const counts = Object.fromEntries(JOB_FILTER_IDS.map((filter) => [filter, 0]));
  for (const job of Array.isArray(jobs) ? jobs : []) {
    counts.all += 1;
    if (Object.hasOwn(counts, job?.mode)) counts[job.mode] += 1;
  }
  return counts;
}

export function boundedJobsForDisplay(jobs, limit = 12) {
  const all = Array.from(jobs || []);
  const visible = all.slice(0, Math.max(1, Number(limit) || 12));
  const included = new Set(visible.map((job) => String(job?.id || '')));
  const activeStatuses = new Set(['queued', 'running', 'paused', 'canceling', 'interrupted']);
  all.forEach((job) => {
    const jobId = String(job?.id || '');
    if (jobId && activeStatuses.has(job?.status) && !included.has(jobId)) {
      visible.push(job);
      included.add(jobId);
    }
  });
  return visible;
}

export function jobAnnouncementCopy(job, fallbackLabel = '任务') {
  const label = String(job?.title || fallbackLabel || '任务').trim() || '任务';
  const messages = {
    completed: { message: `${label}已完成`, tone: 'success' },
    partial: { message: `${label}部分完成，失败项可重试`, tone: 'error', duration: 5200 },
    failed: { message: `${label}失败，详情已保留`, tone: 'error', duration: 5200 },
    canceled: { message: `${label}已取消`, tone: undefined },
  };
  return messages[String(job?.status || '')] || { message: '', tone: undefined };
}

export function jobItemsForDisplay(job, expanded = false, limit = 5) {
  const items = Array.from(job?.items || []);
  if (expanded) return items;
  const important = items.filter((item) => ['failed', 'interrupted', 'running'].includes(item?.status));
  if (!['queued', 'running', 'paused', 'canceling', 'interrupted'].includes(job?.status)) {
    return important.slice(0, Math.max(1, Number(limit) || 5));
  }
  const queued = items.filter((item) => item?.status === 'queued');
  return [...important, ...queued].slice(0, Math.max(1, Number(limit) || 5));
}

export function jobSourceIds(jobs, limit = 120) {
  const maximum = Math.max(0, Number(limit) || 0);
  if (maximum === 0) return [];
  const ids = [];
  const seen = new Set();
  for (const job of Array.isArray(jobs) ? jobs : []) {
    const snapshotIds = Array.isArray(job?.snapshot?.source_asset_ids)
      ? job.snapshot.source_asset_ids
      : [];
    const itemIds = (Array.isArray(job?.items) ? job.items : [])
      .map((item) => item?.source_asset_id);
    for (const value of [...snapshotIds, ...itemIds]) {
      const id = String(value || '').trim();
      if (!id || seen.has(id)) continue;
      seen.add(id);
      ids.push(id);
      if (ids.length >= maximum) return ids;
    }
  }
  return ids;
}

export function jobWorkspaceSnapshot(job, fallback = {}) {
  const immutable = job?.snapshot || {};
  const parameters = {
    ...(job?.parameters || {}),
    ...(immutable.parameters || {}),
  };
  const brief = immutable.brief && typeof immutable.brief === 'object'
    ? immutable.brief
    : (parameters.brief && typeof parameters.brief === 'object' ? parameters.brief : {});
  const intent = immutable.intent && typeof immutable.intent === 'object'
    ? immutable.intent
    : (parameters.intent_locks && typeof parameters.intent_locks === 'object'
      ? parameters.intent_locks
      : {});
  return {
    ...fallback,
    brief: brief.user_request || brief.objective || brief.goal || fallback.brief || '',
    model: parameters.model || fallback.model || 'gpt-image-2',
    angle: parameters.angle || fallback.angle || 'auto',
    // Historical jobs were created with the hard-coded square provider size.
    // Preserve that immutable meaning when their snapshot predates this field.
    output_ratio: parameters.output_ratio || fallback.output_ratio || '1:1',
    output_resolution: parameters.output_resolution || fallback.output_resolution || '2k',
    generation_strategy: parameters.generation_strategy || fallback.generation_strategy || 'legacy_double_pass',
    material_profile: brief.material_profile || fallback.material_profile || 'unknown',
    compact_prompt_enabled: parameters.prompt_version === 'prompt_v3',
    fidelity: Number(parameters.fidelity ?? fallback.fidelity ?? 40),
    batch: Number(parameters.variations ?? parameters.batch ?? fallback.batch ?? 1),
    platter: parameters.platter || fallback.platter || 'auto',
    refine: parameters.refine ?? fallback.refine ?? true,
    intent_locks: intent,
    active_job_id: job?.id || fallback.active_job_id || null,
  };
}
