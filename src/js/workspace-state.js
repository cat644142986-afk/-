export { memoryProjectionState } from './memory-projection.js';
export { reviewStateForResult } from './result-review.js';
export { selectRestorableResult } from './workspace-lifecycle.js';

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

export function createSubmissionSnapshot({
  mode,
  sourceAssetIds,
  parameters,
  productProfileId = null,
  expectedProductProfileRevision = null,
}) {
  return {
    mode: String(mode),
    source_asset_ids: Array.from(sourceAssetIds || [], String),
    parameters: cloneJson(parameters || {}),
    product_profile_id: productProfileId ? String(productProfileId) : null,
    expected_product_profile_revision: productProfileId
      ? Number(expectedProductProfileRevision)
      : null,
  };
}

export function submissionFingerprint(submission) {
  return JSON.stringify(stableValue({
    mode: submission?.mode || '',
    source_asset_ids: submission?.source_asset_ids || [],
    parameters: submission?.parameters || {},
    product_profile_id: submission?.product_profile_id || null,
    expected_product_profile_revision: submission?.expected_product_profile_revision ?? null,
  }));
}

export function selectionAfterImport(currentIds, importedIds, config) {
  const current = config?.multiple ? Array.from(currentIds || [], String) : [];
  const imported = Array.from(importedIds || [], String);
  const maxFiles = Math.max(1, Number(config?.maxFiles) || 1);
  return [...new Set([...current, ...imported])].slice(0, maxFiles);
}

export function selectionForRestoredResult(currentIds, sourceIds, activeIds, maxFiles) {
  const current = Array.from(currentIds || [], String);
  if (current.length) return current;
  const active = new Set(Array.from(activeIds || [], String));
  return Array.from(sourceIds || [], String)
    .filter((assetId) => active.has(assetId))
    .slice(0, Math.max(1, Number(maxFiles) || 1));
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

const SUPPORTED_FEEDBACK_SIGNALS = new Set([
  'adopted', 'rejected', 'adjusted', 'final_artwork', 'note',
]);

export function normalizeFeedbackSignal(signal) {
  const aliases = { adopt: 'adopted', reject: 'rejected', adjust: 'adjusted' };
  const candidate = aliases[String(signal || '').trim()] || String(signal || '').trim();
  return SUPPORTED_FEEDBACK_SIGNALS.has(candidate) ? candidate : 'note';
}

function uniqueByText(items) {
  const seen = new Set();
  return items.filter((item) => {
    const key = String(item?.text || item || '').trim();
    if (!key || seen.has(key)) return false;
    seen.add(key);
    return true;
  });
}

function uniqueSources(items) {
  const seen = new Set();
  return items.filter((item) => {
    if (!item || typeof item !== 'object') return false;
    const key = String(item.id || item.relative_path || item.path || item.title || '').trim();
    if (!key || seen.has(key)) return false;
    seen.add(key);
    return true;
  });
}

function promptRules(prompt, marker, stopMarker = '') {
  const text = String(prompt || '');
  const start = text.indexOf(marker);
  if (start < 0) return [];
  const contentStart = start + marker.length;
  const stop = stopMarker ? text.indexOf(stopMarker, contentStart) : -1;
  return text.slice(contentStart, stop >= 0 ? stop : undefined)
    .split('；')
    .map((rule) => rule.trim())
    .filter(Boolean)
    .map((rule) => ({ text: rule, recovered_from_prompt: true }));
}

export function knowledgeBundleFromEvidence({ brief = {}, traces = [], generation = null } = {}) {
  const sources = [];
  const positiveRules = [];
  const negativeRules = [];
  const intentLockRules = [];
  const promptTraces = Array.from(traces || []).filter((trace) => (
    String(trace?.stage || '').startsWith('prompt.') || trace?.compiled_prompt
  ));

  for (const source of generation?.knowledge_refs || []) sources.push(source);
  for (const trace of promptTraces) {
    for (const evidence of trace.applied_knowledge || []) {
      if (!evidence || typeof evidence !== 'object') continue;
      if (evidence.kind === 'positive_rule') positiveRules.push({ text: evidence.text, source: evidence.source || null });
      else if (evidence.kind === 'negative_rule') negativeRules.push({ text: evidence.text, source: evidence.source || null });
      else if (evidence.kind === 'intent_lock') intentLockRules.push(evidence.text);
      else if (evidence.kind === 'source') sources.push(evidence.source || evidence);
      else if (evidence.title || evidence.id || evidence.relative_path || evidence.path) sources.push(evidence);
    }
    intentLockRules.push(...promptRules(
      trace.compiled_prompt,
      '。不可破坏约束（最高优先级）：',
      '。知识库设计约束：',
    ).map((rule) => rule.text));
    positiveRules.push(...promptRules(trace.compiled_prompt, '。知识库设计约束：'));
  }

  if (!promptTraces.length && generation?.prompt) {
    intentLockRules.push(...promptRules(
      generation.prompt,
      '。不可破坏约束（最高优先级）：',
      '。知识库设计约束：',
    ).map((rule) => rule.text));
    positiveRules.push(...promptRules(generation.prompt, '。知识库设计约束：'));
  }

  const compiledNegativePrompt = [...promptTraces].reverse()
    .map((trace) => String(trace?.parameters?.negative_prompt || '').trim())
    .find(Boolean) || String(generation?.negative_prompt || '').trim();
  if (!negativeRules.length && compiledNegativePrompt) {
    negativeRules.push({ text: compiledNegativePrompt, recovered_from_prompt: true });
  }

  const traceBrief = [...promptTraces].reverse()
    .map((trace) => trace?.user_input?.brief)
    .find((item) => item && typeof item === 'object');
  const compiledPrompt = [...promptTraces].reverse()
    .map((trace) => String(trace?.compiled_prompt || '').trim())
    .find(Boolean) || String(generation?.prompt || '').trim();

  return {
    creative_brief: traceBrief || brief || {},
    sources: uniqueSources(sources),
    positive_rules: uniqueByText(positiveRules),
    negative_rules: uniqueByText(negativeRules),
    intent_lock_rules: uniqueByText(intentLockRules.map((text) => ({ text }))).map((rule) => rule.text),
    conflicts: [],
    compiled_prompt: compiledPrompt,
    compiled_negative_prompt: compiledNegativePrompt,
    trace_bound: Boolean(promptTraces.length || generation?.id || generation?.prompt),
    trace_count: promptTraces.length,
  };
}

export function comparisonPresentation(original, result, tolerance = 0.03) {
  const originalWidth = Number(original?.width || 0);
  const originalHeight = Number(original?.height || 0);
  const resultWidth = Number(result?.width || 0);
  const resultHeight = Number(result?.height || 0);
  if (!originalWidth || !originalHeight || !resultWidth || !resultHeight) return 'overlay';
  const originalRatio = originalWidth / originalHeight;
  const resultRatio = resultWidth / resultHeight;
  const delta = Math.abs(originalRatio - resultRatio) / Math.max(originalRatio, resultRatio);
  return delta <= Math.max(0, Number(tolerance) || 0) ? 'overlay' : 'side-by-side';
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
