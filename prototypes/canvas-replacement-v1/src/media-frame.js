// HTMLMediaElement provides decode and seek; no copied codec or Provider.
export async function extractVideoFrame(url, position = 'current', currentTime = 0) {
  const video = document.createElement('video'); video.crossOrigin = 'anonymous'; video.preload = 'auto'; video.muted = true;
  const wait = (event) => new Promise((resolve, reject) => {
    const timer = setTimeout(() => finish(new Error('视频帧读取超时，原素材保留')), 15000);
    const done = () => finish(); const failed = () => finish(new Error('浏览器无法解码这个视频'));
    function finish(error) { clearTimeout(timer); video.removeEventListener(event, done); video.removeEventListener('error', failed); error ? reject(error) : resolve(); }
    video.addEventListener(event, done, { once: true }); video.addEventListener('error', failed, { once: true });
  });
  try {
    const loaded = wait('loadeddata'); video.src = url; await loaded;
    const seconds = position === 'first' ? 0 : position === 'last' ? Math.max(0, video.duration - .05) : Math.max(0, Math.min(currentTime, video.duration - .05));
    if (Math.abs(video.currentTime - seconds) > .005) { const seeked = wait('seeked'); video.currentTime = seconds; await seeked; }
    const canvas = document.createElement('canvas'); canvas.width = video.videoWidth; canvas.height = video.videoHeight;
    canvas.getContext('2d').drawImage(video, 0, 0);
    const blob = await new Promise((resolve, reject) => canvas.toBlob((value) => value ? resolve(value) : reject(new Error('PNG 导出失败')), 'image/png'));
    return { blob, seconds, width: canvas.width, height: canvas.height };
  } finally { video.pause(); video.removeAttribute('src'); video.load(); }
}
