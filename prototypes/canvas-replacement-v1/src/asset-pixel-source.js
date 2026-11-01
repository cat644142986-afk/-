// Runtime URLs are ephemeral, never written into the scene. A checked binary
// request also reports missing assets explicitly rather than a generic image
// decode error, and avoids WebView's shared non-CORS <img> cache.
export async function createAssetMediaSource(api, assetId, kind, urls = URL) {
  const blob = await api.getAssetBlob(assetId);
  if (!blob.type.startsWith(`${kind}/`)) throw new Error('这个素材不是所需的媒体类型；原素材保留');
  const url = urls.createObjectURL(blob);
  let released = false;
  return { url, release() { if (!released) { released = true; urls.revokeObjectURL(url); } } };
}

export function createAssetPixelSource(api, assetId, urls = URL) {
  return createAssetMediaSource(api, assetId, 'image', urls);
}

export async function withAssetMediaSource(api, assetId, kind, consume, urls = URL) {
  const source = await createAssetMediaSource(api, assetId, kind, urls);
  try { return await consume(source.url); }
  finally { source.release(); }
}

export async function withAssetPixelSource(api, assetId, consume, urls = URL) {
  const source = await createAssetPixelSource(api, assetId, urls);
  try { return await consume(source.url); }
  finally { source.release(); }
}
