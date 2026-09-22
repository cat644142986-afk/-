// Excalidraw is the sole scene/selection source. This projection only supplies
// Retake's reference picker with PA's durable business identities.
export function canvasReferenceOptions(elements, files = {}, selectedElementId = '', selectedAsset = null) {
  return Array.from(elements || [])
    .filter((element) => element?.type === 'image' && !element.isDeleted
      && element.customData?.asset_id
      && (!element.customData.result_id
        || element.customData.result_id === element.customData.asset_id))
    .map((element) => {
      const refs = element.customData;
      const name = element.id === selectedElementId && selectedAsset?.id === refs.asset_id
        ? selectedAsset.name : '';
      return {
        elementId: element.id,
        assetId: refs.asset_id,
        resultId: refs.result_id || '',
        previewUrl: files[element.fileId]?.dataURL || '',
        title: name || `${refs.result_id ? 'Result' : '素材'} · ${String(refs.asset_id).slice(0, 12)}`,
      };
    });
}

export function activeCanvasReference(reference, selectedElementId, options) {
  if (!reference?.elementId || !selectedElementId
    || reference.elementId !== selectedElementId) return null;
  return Array.from(options || []).find((image) => image.elementId === reference.elementId) || reference;
}
