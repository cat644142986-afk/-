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
    fidelity: Number(parameters.fidelity ?? fallback.fidelity ?? 40),
    batch: Number(parameters.variations ?? parameters.batch ?? fallback.batch ?? 1),
    platter: parameters.platter || fallback.platter || 'auto',
    refine: parameters.refine ?? fallback.refine ?? true,
    intent_locks: intent,
    active_job_id: job?.id || fallback.active_job_id || null,
  };
}
