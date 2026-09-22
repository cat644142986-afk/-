import React, { useEffect, useRef, useState } from 'react';

// Adapted from Retake Whiteboard v0.1.3 (a24d0684):
// InputReferencePicker, ImageComposerReferenceTray and ImageComposerControls.
// Apache-2.0; see THIRD_PARTY_NOTICES.md. PA supplies its own canvas images,
// exact Asset/Result binding and execution callback; no Retake Board or runtime.

export const CANVAS_REFERENCE_RATIOS = Object.freeze([
  'original', '1:1', '2:3', '3:2', '3:4', '4:3', '4:5', '5:4', '9:16', '16:9',
]);
export const CANVAS_REFERENCE_RESOLUTIONS = Object.freeze(['2k', '4k']);

export function CanvasReferencePicker({ anchor, images, onCancel, onConfirm, selectedImage, onSelectImage }) {
  const pickerRef = useRef(null);
  const left = Math.max(16, Math.min(anchor.x, window.innerWidth - 376));
  const top = Math.max(16, Math.min(anchor.y, window.innerHeight - 350));

  useEffect(() => {
    const onPointerDown = (event) => {
      if (event.target instanceof Node && pickerRef.current?.contains(event.target)) return;
      onCancel();
    };
    const onKeyDown = (event) => {
      if (event.key !== 'Escape') return;
      event.preventDefault();
      event.stopPropagation();
      onCancel();
    };
    document.addEventListener('pointerdown', onPointerDown);
    document.addEventListener('keydown', onKeyDown, true);
    pickerRef.current?.querySelector('button')?.focus({ preventScroll: true });
    return () => {
      document.removeEventListener('pointerdown', onPointerDown);
      document.removeEventListener('keydown', onKeyDown, true);
    };
  }, [onCancel]);

  return <div ref={pickerRef} className="pa-reference-picker" role="dialog" aria-label="选择参考图" style={{ left, top }} onPointerDown={(event) => event.stopPropagation()}>
    <header><span><strong>{selectedImage ? '确认参考图' : '选择参考图'}</strong><small>从当前画布选择一张图片；不会因此发起生成。</small></span><button type="button" aria-label="关闭参考图选择" onClick={onCancel}>×</button></header>
    {selectedImage ? <>
      <div className="pa-reference-picker__selected">{selectedImage.previewUrl ? <img src={selectedImage.previewUrl} alt="" /> : <span className="pa-reference-picker__fallback" aria-hidden="true">图</span>}<strong>{selectedImage.title}</strong><small>{selectedImage.resultId ? 'Result' : '素材'}</small></div>
      <button type="button" className="pa-reference-picker__confirm" onClick={onConfirm}>加入本次创作</button>
    </> : images.length ? <div className="pa-reference-picker__list">
      {images.map((image) => <button key={image.elementId} type="button" onClick={() => onSelectImage(image.elementId)}>
        {image.previewUrl ? <img src={image.previewUrl} alt="" /> : <span className="pa-reference-picker__fallback" aria-hidden="true">图</span>}
        <span>{image.title}</span><small>{image.resultId ? 'Result' : '素材'}</small>
      </button>)}
    </div> : <div className="pa-reference-picker__empty">当前画布还没有可用图片。</div>}
  </div>;
}

export function CanvasReferenceTray({ image, onAdd, onRemove, addButtonRef }) {
  const [hovered, setHovered] = useState(false);
  const [pinned, setPinned] = useState(false);
  const rootRef = useRef(null);
  useEffect(() => {
    if (!pinned) return undefined;
    const onPointerDown = (event) => {
      if (event.target instanceof Node && rootRef.current?.contains(event.target)) return;
      setPinned(false);
    };
    const onKeyDown = (event) => {
      if (event.key !== 'Escape') return;
      event.preventDefault();
      event.stopPropagation();
      setPinned(false);
    };
    document.addEventListener('pointerdown', onPointerDown, true);
    document.addEventListener('keydown', onKeyDown, true);
    return () => {
      document.removeEventListener('pointerdown', onPointerDown, true);
      document.removeEventListener('keydown', onKeyDown, true);
    };
  }, [pinned]);
  return <div ref={rootRef} className="pa-reference-tray" aria-label="本次参考图">
    <div className="pa-reference-tray__list">
      {image && <div className="pa-reference-tray__item" onPointerEnter={() => setHovered(true)} onPointerLeave={() => setHovered(false)} onFocusCapture={() => setHovered(true)} onBlurCapture={(event) => { if (!event.currentTarget.contains(event.relatedTarget)) setHovered(false); }}>
        <button type="button" className="pa-reference-tray__thumbnail" aria-expanded={pinned} aria-pressed={pinned} aria-label={`查看参考图：${image.title}`} onClick={() => setPinned((value) => !value)}>
          {image.previewUrl ? <img src={image.previewUrl} alt="" /> : <span aria-hidden="true">图</span>}
        </button>
        <span className="pa-reference-tray__badge">参考</span>
        <button type="button" className="pa-reference-tray__remove" aria-label={`移除参考图：${image.title}`} onClick={onRemove}>×</button>
      </div>}
      <button ref={addButtonRef} type="button" className="pa-reference-tray__add" aria-label={image ? '更换参考图' : '添加参考图'} title={image ? '更换参考图' : '添加参考图'} onClick={onAdd}>+ 参考图</button>
    </div>
    {image && (pinned || hovered) && <figure className="pa-reference-tray__preview" aria-label="参考图预览">
      <div>{image.previewUrl ? <img src={image.previewUrl} alt={image.title} /> : <span aria-hidden="true">图</span>}</div>
      <figcaption><strong>{image.title}</strong><span>{image.resultId ? '精确 Result' : '原始素材'} · 仅供本次参考生成</span></figcaption>
    </figure>}
  </div>;
}

function ParameterOptions({ label, options, selected, onSelect }) {
  return <section className="pa-reference-controls__group"><strong>{label}</strong><div>
    {options.map((option) => <button key={option} type="button" className={option === selected ? 'is-selected' : ''} aria-pressed={option === selected} onClick={() => onSelect(option)}>{option === '2k' ? '2K' : option === '4k' ? '4K' : option}</button>)}
  </div></section>;
}

export function CanvasReferenceControls({ ratio, resolution, onChange }) {
  const [open, setOpen] = useState(false);
  const rootRef = useRef(null);
  const buttonRef = useRef(null);
  useEffect(() => {
    if (!open) return undefined;
    const onPointerDown = (event) => {
      if (event.target instanceof Node && rootRef.current?.contains(event.target)) return;
      setOpen(false);
    };
    const onKeyDown = (event) => {
      if (event.key === 'Escape') {
        event.preventDefault();
        event.stopPropagation();
        setOpen(false);
        requestAnimationFrame(() => buttonRef.current?.focus());
      }
    };
    document.addEventListener('pointerdown', onPointerDown, true);
    document.addEventListener('keydown', onKeyDown, true);
    return () => {
      document.removeEventListener('pointerdown', onPointerDown, true);
      document.removeEventListener('keydown', onKeyDown, true);
    };
  }, [open]);
  return <div ref={rootRef} className="pa-reference-controls" aria-label="图像参数">
    <button ref={buttonRef} type="button" className="pa-reference-controls__button" aria-expanded={open} onClick={() => setOpen((value) => !value)}><span>画幅 {ratio} · {resolution.toUpperCase()}</span><span aria-hidden="true">⌄</span></button>
    {open && <div className="pa-reference-controls__popover" role="dialog" aria-label="图像参数">
      <ParameterOptions label="画幅" options={CANVAS_REFERENCE_RATIOS} selected={ratio} onSelect={(value) => onChange({ ratio: value })} />
      <ParameterOptions label="质量" options={CANVAS_REFERENCE_RESOLUTIONS} selected={resolution} onSelect={(value) => onChange({ resolution: value })} />
    </div>}
  </div>;
}
