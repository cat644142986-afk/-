function cloneJson(value) {
  return value === undefined ? undefined : JSON.parse(JSON.stringify(value));
}

function clampProgress(value) {
  const number = Number(value);
  if (!Number.isFinite(number)) return 0;
  return Math.max(0, Math.min(1, number));
}

function stableValue(value) {
  if (Array.isArray(value)) return value.map(stableValue);
  if (value && typeof value === 'object') {
    return Object.fromEntries(
      Object.keys(value).sort().map((key) => [key, stableValue(value[key])]),
    );
  }
  return value;
}

export function createSubmissionSnapshot({ mode, sourceAssetIds, parameters }) {
  return {
    mode: String(mode),
    source_asset_ids: Array.from(sourceAssetIds || [], String),
    parameters: cloneJson(parameters || {}),
  };
}

export function submissionFingerprint(submission) {
  return JSON.stringify(stableValue({
    mode: submission?.mode || '',
    source_asset_ids: submission?.source_asset_ids || [],
    parameters: submission?.parameters || {},
  }));
}

export function selectionAfterImport(currentIds, importedIds, config) {
  const current = config?.multiple ? Array.from(currentIds || [], String) : [];
  const imported = Array.from(importedIds || [], String);
  const maxFiles = Math.max(1, Number(config?.maxFiles) || 1);
  return [...new Set([...current, ...imported])].slice(0, maxFiles);
}

export function multiFileOutputPlan(sourceCount, variations, maxOutputs = 24) {
  const sources = Math.max(0, Number(sourceCount) || 0);
  const perSource = Math.max(1, Number(variations) || 1);
  const outputLimit = Math.max(1, Number(maxOutputs) || 24);
  const maxVariations = sources ? Math.max(1, Math.min(4, Math.floor(outputLimit / sources))) : 4;
  const total = sources * perSource;
  return {
    sources,
    variations: perSource,
    total,
    maxOutputs: outputLimit,
    maxVariations,
    valid: total <= outputLimit,
  };
}

export function itemCompletionProgress(item) {
  return clampProgress(item?.progress);
}

export function jobCompletionProgress(job) {
  if (job?.progress !== undefined && job?.progress !== null) {
    return clampProgress(job.progress);
  }
  const items = Array.isArray(job?.items) ? job.items : [];
  if (items.length) {
    return items.reduce((sum, item) => sum + itemCompletionProgress(item), 0) / items.length;
  }
  return 0;
}

export function queueCompletionProgress(jobs) {
  const weighted = Array.from(jobs || []).map((job) => ({
    progress: jobCompletionProgress(job),
    weight: Math.max(1, Number(job?.total_items) || (Array.isArray(job?.items) ? job.items.length : 0)),
  }));
  const totalWeight = weighted.reduce((sum, entry) => sum + entry.weight, 0);
  if (!totalWeight) return 0;
  return weighted.reduce((sum, entry) => sum + (entry.progress * entry.weight), 0) / totalWeight;
}

export function jobLifecycleActions(status) {
  if (status === 'queued' || status === 'running') return ['pause', 'cancel'];
  if (status === 'paused') return ['resume', 'cancel'];
  return [];
}

export function jobsRenderSignature(jobs, available, inFlightActions = []) {
  const renderState = Array.from(jobs || []).map((job) => ({
    id: job?.id,
    mode: job?.mode,
    title: job?.title,
    status: job?.status,
    progress: job?.progress,
    total_items: job?.total_items,
    completed_items: job?.completed_items,
    failed_items: job?.failed_items,
    canceled_items: job?.canceled_items,
    created_at: job?.created_at,
    updated_at: job?.updated_at,
    items: Array.from(job?.items || []).map((item) => ({
      id: item?.id,
      source_asset_id: item?.source_asset_id,
      status: item?.status,
      progress: item?.progress,
      error_code: item?.error_code,
      error_message: item?.error_message,
      result_asset_ids: item?.result_asset_ids,
    })),
  }));
  return JSON.stringify(stableValue({
    available: Boolean(available),
    in_flight_actions: Array.from(inFlightActions).sort(),
    jobs: renderState,
  }));
}

export function collectResultItems(results) {
  const seen = new Set();
  return ['main', 'cutout'].flatMap((role) => (
    Array.isArray(results?.[role]) ? results[role] : []
  )).filter((item) => {
    const key = item?.asset_id || item?.url || `${item?.role || ''}:${item?.name || ''}`;
    if (seen.has(key)) return false;
    seen.add(key);
    return true;
  });
}

export async function processResultItems(items, processor) {
  const succeeded = [];
  const failed = [];
  for (const [index, item] of Array.from(items || []).entries()) {
    try {
      await processor(item, index);
      succeeded.push(item);
    } catch (error) {
      failed.push({ item, error });
    }
  }
  return { succeeded, failed };
}
