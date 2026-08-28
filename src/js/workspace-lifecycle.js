function resultIdsForWorkspaceJob(job) {
  return Array.from(job?.items || []).flatMap((item) => (
    Array.isArray(item?.result_asset_ids) ? item.result_asset_ids : []
  )).map(String);
}

export function selectRestorableResult(jobs, draft) {
  const candidates = Array.from(jobs || []);
  const activeJobId = String(draft?.active_job_id || '').trim();
  const resultAssetId = String(draft?.current_result_asset_id || '').trim();
  if (!activeJobId && !resultAssetId) return null;

  if (activeJobId && resultAssetId) {
    return candidates.find((job) => (
      String(job?.id || '') === activeJobId
      && resultIdsForWorkspaceJob(job).includes(resultAssetId)
    )) || null;
  }
  if (activeJobId) {
    return candidates.find((job) => (
      String(job?.id || '') === activeJobId
      && resultIdsForWorkspaceJob(job).length > 0
    )) || null;
  }
  return candidates.find((job) => resultIdsForWorkspaceJob(job).includes(resultAssetId)) || null;
}

export function completionRequestKey(mode, jobId, resultAssetId) {
  return [mode, jobId, resultAssetId].map((value) => String(value || '').trim()).join(':');
}

export function locateResultVersion(results, resultAssetId) {
  const target = String(resultAssetId || '').trim();
  if (!target) return null;
  for (const tab of ['main', 'cutout']) {
    const items = Array.isArray(results?.[tab]) ? results[tab] : [];
    const index = items.findIndex((item) => (
      String(item?.asset_id || item?.id || '') === target
    ));
    if (index >= 0) return { tab, index };
  }
  return null;
}
