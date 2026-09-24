export const REFERENCE_GENERATE_TASK_KIND = 'reference-generate';

export function composerAdmissionRequest({ ratio, resolution } = {}) {
  return {
    taskKind: REFERENCE_GENERATE_TASK_KIND,
    ratio: String(ratio || '').trim().toLowerCase(),
    resolution: String(resolution || '').trim().toLowerCase(),
  };
}

export function composerSelection(payload) {
  const selection = payload?.selection || payload || null;
  return selection && typeof selection === 'object' ? selection : null;
}

export function eligibleComposerModels(payload) {
  const selection = composerSelection(payload);
  return Array.isArray(selection?.models) ? selection.models.filter((model) => (
    model && selection.eligible_provider_model_ids?.includes(model.provider_model_id)
  )) : [];
}

export function admittedComposerModel(payload, providerModelId) {
  const exactId = String(providerModelId || '');
  return eligibleComposerModels(payload).find((model) => (
    String(model.provider_model_id || '') === exactId
  )) || null;
}

export function composerRecommendation(payload) {
  const routing = composerSelection(payload)?.routing;
  if (!routing || routing.status !== 'ready') return null;
  const providerModelId = String(routing.recommended_provider_model_id || '');
  const model = admittedComposerModel(payload, providerModelId);
  return model ? { ...routing, model } : null;
}

export function initialComposerModel(payload, currentModel = '') {
  const models = eligibleComposerModels(payload);
  const current = String(currentModel || '');
  if (models.some((model) => model.provider_model_id === current)) return current;
  if (current) return current;
  const recommendedId = String(composerRecommendation(payload)?.recommended_provider_model_id || '');
  if (models.some((model) => model.provider_model_id === recommendedId)) return recommendedId;
  const defaultId = String(composerSelection(payload)?.default_provider_model_id || '');
  return models.some((model) => model.provider_model_id === defaultId)
    ? defaultId
    : String(models[0]?.provider_model_id || '');
}
