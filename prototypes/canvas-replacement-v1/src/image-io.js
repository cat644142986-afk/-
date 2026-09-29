export function imageFile(file) {
  return Boolean(file && /^image\/(png|jpeg|webp|gif)$/i.test(file.type || ''));
}

export function readImageFile(file) {
  return new Promise((resolve, reject) => {
    if (!imageFile(file)) {
      reject(new Error(`不支持的文件：${file?.name || '未知文件'}`));
      return;
    }
    const reader = new FileReader();
    reader.onerror = () => reject(new Error(`无法读取 ${file.name}`));
    reader.onload = () => {
      const src = String(reader.result || '');
      const image = new Image();
      image.onload = () => resolve({
        name: file.name,
        src,
        naturalWidth: image.naturalWidth,
        naturalHeight: image.naturalHeight,
      });
      image.onerror = () => reject(new Error(`无法解析 ${file.name}`));
      image.src = src;
    };
    reader.readAsDataURL(file);
  });
}
