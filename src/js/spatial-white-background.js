import {
  SPATIAL_IMAGE_AI_COMMAND_ID,
  SPATIAL_IMAGE_AI_SKILL_ID,
  SPATIAL_WHITE_BACKGROUND_ACTION,
  applySpatialImageAiPreview,
  createSpatialImageAiDraft,
  isSpatialImageAiJob,
  spatialImageAiAction,
  spatialImageAiCanvasId,
  spatialImageAiCommandPayload,
  spatialImageAiJobIsActive,
  spatialImageAiJobIsSettled,
  spatialImageAiPreviewPayload,
  spatialImageAiResultAssetIds,
  spatialImageAiSourceElementId,
  updateSpatialImageAiDraft,
} from './spatial-native-image-ai.js';

export const SPATIAL_WHITE_BACKGROUND_COMMAND_ID = SPATIAL_IMAGE_AI_COMMAND_ID;
export { SPATIAL_WHITE_BACKGROUND_ACTION };
export const SPATIAL_WHITE_BACKGROUND_SKILL_ID = SPATIAL_IMAGE_AI_SKILL_ID;

export function createSpatialWhiteBackgroundDraft(options = {}) {
  return createSpatialImageAiDraft(SPATIAL_WHITE_BACKGROUND_ACTION, options);
}

export function updateSpatialWhiteBackgroundDraft(draft, patch = {}) {
  return updateSpatialImageAiDraft(draft, patch);
}

export function spatialWhiteBackgroundPreviewPayload(draft) {
  return spatialImageAiPreviewPayload(draft);
}

export function applySpatialWhiteBackgroundPreview(draft, bundle) {
  return applySpatialImageAiPreview(draft, bundle);
}

export function spatialWhiteBackgroundCommandPayload(draft, idFactory = null) {
  return spatialImageAiCommandPayload(draft, idFactory);
}

export function isSpatialWhiteBackgroundJob(job) {
  return isSpatialImageAiJob(job)
    && spatialImageAiAction(job) === SPATIAL_WHITE_BACKGROUND_ACTION;
}

export function spatialWhiteBackgroundCanvasId(job) {
  return isSpatialWhiteBackgroundJob(job) ? spatialImageAiCanvasId(job) : '';
}

export function spatialWhiteBackgroundSourceElementId(job) {
  return isSpatialWhiteBackgroundJob(job) ? spatialImageAiSourceElementId(job) : '';
}

export function spatialWhiteBackgroundJobIsActive(job) {
  return isSpatialWhiteBackgroundJob(job) && spatialImageAiJobIsActive(job);
}

export function spatialWhiteBackgroundJobIsSettled(job) {
  return isSpatialWhiteBackgroundJob(job) && spatialImageAiJobIsSettled(job);
}

export function spatialWhiteBackgroundResultAssetIds(job) {
  return isSpatialWhiteBackgroundJob(job) ? spatialImageAiResultAssetIds(job) : [];
}
