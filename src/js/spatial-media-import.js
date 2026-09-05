export const MAX_SPATIAL_IMPORT_FILES = 100;
export const MAX_SPATIAL_IMAGE_BYTES = 20 * 1024 * 1024;
export const MAX_SPATIAL_VIDEO_BYTES = 512 * 1024 * 1024;

export function isVideoAsset(value) {
  return String(value?.kind || '').toLowerCase() === 'video'
    || /^video\//i.test(String(value?.mime || value?.media_type || ''));
}

function fileKind(file) {
  const mime = String(file?.type || '').trim().toLowerCase();
  const name = String(file?.name || '');
  if (/^image\/(png|jpeg|webp)$/.test(mime) || (!mime && /\.(png|jpe?g|webp)$/i.test(name))) {
    return 'image';
  }
  if (/^video\/(mp4|webm)$/.test(mime) || (!mime && /\.(mp4|webm)$/i.test(name))) {
    return 'video';
  }
  return '';
}

export function partitionSpatialImportFiles(fileList) {
  const incoming = Array.from(fileList || []);
  if (incoming.length > MAX_SPATIAL_IMPORT_FILES) {
    return {
      images: [],
      videos: [],
      rejected: incoming.map((file) => ({
        file,
        code: 'TOO_MANY_FILES',
        message: `一次最多导入 ${MAX_SPATIAL_IMPORT_FILES} 项素材`,
      })),
    };
  }
  const result = { images: [], videos: [], rejected: [] };
  incoming.forEach((file) => {
    const kind = fileKind(file);
    if (!kind) {
      result.rejected.push({ file, code: 'UNSUPPORTED_FORMAT', message: '仅支持 JPG、PNG、WebP、MP4 和 WebM' });
      return;
    }
    const limit = kind === 'video' ? MAX_SPATIAL_VIDEO_BYTES : MAX_SPATIAL_IMAGE_BYTES;
    if (Number(file?.size || 0) > limit) {
      result.rejected.push({
        file,
        code: 'FILE_TOO_LARGE',
        message: `${kind === 'video' ? '视频' : '图片'}超过 ${kind === 'video' ? '512' : '20'} MB`,
      });
      return;
    }
    result[kind === 'video' ? 'videos' : 'images'].push(file);
  });
  return result;
}

function waitForMediaEvent(target, eventName, timeoutMs, errorMessage) {
  return new Promise((resolve, reject) => {
    let timer = null;
    const cleanup = () => {
      if (timer !== null) globalThis.clearTimeout(timer);
      target.removeEventListener(eventName, onReady);
      target.removeEventListener('error', onError);
    };
    const onReady = () => { cleanup(); resolve(); };
    const onError = () => { cleanup(); reject(new Error(errorMessage)); };
    target.addEventListener(eventName, onReady, { once: true });
    target.addEventListener('error', onError, { once: true });
    timer = globalThis.setTimeout(() => {
      cleanup();
      reject(new Error(`${errorMessage}（读取超时）`));
    }, timeoutMs);
  });
}

function canvasBlob(canvas, type, quality) {
  return new Promise((resolve, reject) => canvas.toBlob(
    (blob) => (blob ? resolve(blob) : reject(new Error('无法生成视频封面'))),
    type,
    quality,
  ));
}

export async function createVideoImportDescriptor(file, options = {}) {
  const documentRef = options.documentRef || globalThis.document;
  const urlApi = options.urlApi || globalThis.URL;
  const FileCtor = options.FileCtor || globalThis.File;
  const timeoutMs = Math.max(1000, Number(options.timeoutMs || 20000));
  if (!documentRef?.createElement || !urlApi?.createObjectURL || typeof FileCtor !== 'function') {
    throw new Error('当前环境无法读取本地视频');
  }
  const video = documentRef.createElement('video');
  const objectUrl = urlApi.createObjectURL(file);
  try {
    video.preload = 'auto';
    video.muted = true;
    video.playsInline = true;
    const metadataReady = waitForMediaEvent(video, 'loadedmetadata', timeoutMs, '无法读取视频元数据');
    video.src = objectUrl;
    video.load?.();
    await metadataReady;
    const width = Math.round(Number(video.videoWidth || 0));
    const height = Math.round(Number(video.videoHeight || 0));
    const durationSeconds = Number(video.duration || 0);
    if (!width || !height || !Number.isFinite(durationSeconds) || durationSeconds <= 0 || durationSeconds > 3600) {
      throw new Error('视频尺寸或时长无效（最长 60 分钟）');
    }
    if (video.readyState < 2) {
      await waitForMediaEvent(video, 'loadeddata', timeoutMs, '无法解码视频首帧');
    }
    const captureTime = Math.min(0.1, durationSeconds / 2);
    if (captureTime > 0.001 && Math.abs(Number(video.currentTime || 0) - captureTime) > 0.001) {
      const seeked = waitForMediaEvent(video, 'seeked', timeoutMs, '无法定位视频封面帧');
      video.currentTime = captureTime;
      await seeked;
    }
    const maxEdge = Math.max(64, Number(options.maxCoverEdge || 960));
    const scale = Math.min(1, maxEdge / Math.max(width, height));
    const canvas = documentRef.createElement('canvas');
    canvas.width = Math.max(1, Math.round(width * scale));
    canvas.height = Math.max(1, Math.round(height * scale));
    const context = canvas.getContext('2d');
    if (!context) throw new Error('当前环境无法生成视频封面');
    context.drawImage(video, 0, 0, canvas.width, canvas.height);
    const coverBlob = await canvasBlob(canvas, 'image/jpeg', 0.86);
    const baseName = String(file?.name || 'video').replace(/\.[^.]+$/, '') || 'video';
    const cover = new FileCtor([coverBlob], `${baseName}-cover.jpg`, {
      type: 'image/jpeg',
      lastModified: Number(file?.lastModified || Date.now()),
    });
    return { file, cover, width, height, durationSeconds };
  } finally {
    video.pause?.();
    video.removeAttribute?.('src');
    video.load?.();
    urlApi.revokeObjectURL(objectUrl);
  }
}
