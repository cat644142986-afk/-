import React, { useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react';
import { getCommonBounds, sceneCoordsToViewportCoords } from '@excalidraw/excalidraw';
import { anchoredSurface, selectionFrame, shouldDismissComposerOnEscape, stopComposerKeyboardEvent } from './canvas-transplant-geometry.js';
import { CanvasReferenceControls, CanvasReferencePicker, CanvasReferenceTray } from './canvas-retake-reference.jsx';
import { activeCanvasReference, canvasReferenceOptions } from './canvas-reference-adapter.js';
import { CanvasModelSelector } from './canvas-model-selector.jsx';
import {
  admittedComposerModel,
  composerAdmissionRequest,
  composerSelection,
  initialComposerModel,
} from './canvas-model-admission.js';

// Interaction transplant: selection geometry follows basketikun's contextual
// toolbar; Excalidraw's live onChange provides Loomic-style viewport sync.
// Composer keyboard/IME containment follows BeatDesign; the transient popover
// dismissal follows Retake. PA owns all business actions and execution.
export function CanvasTransplantShell({
  api, view, business, onTool, onReferenceReview, onReferenceAdmission,
}) {
  const [composerOpen, setComposerOpen] = useState(false);
  const [moreOpen, setMoreOpen] = useState(false);
  const [annotateOpen, setAnnotateOpen] = useState(false);
  const [arrangeOpen, setArrangeOpen] = useState(false);
  const [compositionActive, setCompositionActive] = useState(false);
  const [reference, setReference] = useState(null);
  const [pickerAnchor, setPickerAnchor] = useState(null);
  const [pickerSelectedId, setPickerSelectedId] = useState('');
  const [referencePrompt, setReferencePrompt] = useState('');
  const [referenceRatio, setReferenceRatio] = useState('1:1');
  const [referenceResolution, setReferenceResolution] = useState('2k');
  const [referenceError, setReferenceError] = useState('');
  const [modelAdmission, setModelAdmission] = useState(null);
  const [modelAdmissionLoading, setModelAdmissionLoading] = useState(false);
  const [modelAdmissionError, setModelAdmissionError] = useState('');
  const [selectedModel, setSelectedModel] = useState('');
  const [, setHostLayoutVersion] = useState(0);
  const composerRef = useRef(null);
  const referenceAddRef = useRef(null);
  const moreRef = useRef(null);
  const annotateRef = useRef(null);
  const arrangeRef = useRef(null);
  const selected = view.selected || [];
  const one = selected.length === 1 ? selected[0] : null;
  const refs = one?.customData || {};
  const kind = refs.result_id ? 'Result' : refs.asset_id ? '素材' : '对象';
  const exactAsset = business.elementId === one?.id && business.asset?.id === refs.asset_id;
  const eligible = Boolean(exactAsset && one?.type === 'image'
    && business.asset?.mime?.startsWith('image/')
    && (refs.result_id === refs.asset_id
      ? business.asset?.role?.startsWith('result_')
      : !refs.result_id && business.asset?.role === 'workspace_source'));
  const reviewing = Boolean(business.reviewing);

  useLayoutEffect(() => {
    const host = document.getElementById('spatial-canvas-host');
    if (!host) return undefined;
    let lastWidth = 0;
    let lastHeight = 0;
    const refresh = () => {
      const rect = host.getBoundingClientRect();
      if (rect.width === lastWidth && rect.height === lastHeight) return;
      lastWidth = rect.width;
      lastHeight = rect.height;
      setHostLayoutVersion((version) => version + 1);
    };
    const observer = new ResizeObserver(refresh);
    observer.observe(host);
    const frame = requestAnimationFrame(refresh);
    return () => { cancelAnimationFrame(frame); observer.disconnect(); };
  }, [api]);

  useEffect(() => {
    const editor = document.getElementById('spatial-editor');
    if (!editor) return undefined;
    editor.dataset.arrangeOpen = String(arrangeOpen);
    return () => { delete editor.dataset.arrangeOpen; };
  }, [arrangeOpen]);

  useEffect(() => {
    if (reference?.elementId !== one?.id) {
      setReference(null);
      setComposerOpen(false);
    }
    setPickerAnchor(null);
    setMoreOpen(false);
    setAnnotateOpen(false);
    setArrangeOpen(false);
  }, [one?.id, selected.length]);

  const closePicker = useCallback((restoreFocus = true) => {
    setPickerAnchor(null);
    setPickerSelectedId('');
    if (restoreFocus) requestAnimationFrame(() => referenceAddRef.current?.focus());
  }, []);

  // Retake's outside-pointer/Escape pattern, scoped to the transplant's
  // lightweight popovers. The Governor preview retains its own focus rules.
  useEffect(() => {
    if (!moreOpen && !annotateOpen && !arrangeOpen) return undefined;
    const onPointer = (event) => {
      if (moreOpen && !moreRef.current?.contains(event.target)) setMoreOpen(false);
      if (annotateOpen && !annotateRef.current?.contains(event.target)) setAnnotateOpen(false);
      if (arrangeOpen && !arrangeRef.current?.contains(event.target)
        && !document.querySelector('#spatial-editor .selected-shape-actions')?.contains(event.target)) setArrangeOpen(false);
    };
    const onEscape = (event) => {
      if (event.key !== 'Escape') return;
      if (moreOpen) { setMoreOpen(false); moreRef.current?.querySelector('button')?.focus(); }
      if (annotateOpen) { setAnnotateOpen(false); annotateRef.current?.querySelector('button')?.focus(); }
      if (arrangeOpen) { setArrangeOpen(false); arrangeRef.current?.focus(); }
    };
    document.addEventListener('pointerdown', onPointer, true);
    document.addEventListener('keydown', onEscape, true);
    return () => {
      document.removeEventListener('pointerdown', onPointer, true);
      document.removeEventListener('keydown', onEscape, true);
    };
  }, [moreOpen, annotateOpen, arrangeOpen]);

  const host = document.getElementById('spatial-canvas-host');
  const hostRect = host?.getBoundingClientRect();
  const appState = api?.getAppState?.() || view.appState;
  const frame = selected.length && api && appState && hostRect
    ? selectionFrame(getCommonBounds(selected), (point) => (
      sceneCoordsToViewportCoords(point, appState)
    ), hostRect)
    : null;
  const placement = frame && hostRect
    ? anchoredSurface(frame, hostRect.width, hostRect.height, {
      preferredWidth: selected.length > 1 ? 330 : 490,
      surfaceHeight: 56,
    })
    : null;
  const imageAction = refs.result_id === refs.asset_id ? 'generate-image' : 'white-background';
  const imageLabel = refs.result_id === refs.asset_id ? '生成变体' : '白底图';
  const files = api?.getFiles?.() || {};
  const referenceOptions = canvasReferenceOptions(
    api?.getSceneElementsIncludingDeleted?.(), files, business.elementId, business.asset,
  );
  const selectedPickerImage = referenceOptions.find((image) => image.elementId === pickerSelectedId);
  const activeReference = activeCanvasReference(reference, one?.id, referenceOptions);
  const selectedModelEvidence = admittedComposerModel(modelAdmission, selectedModel);
  const tool = view.appState?.activeTool?.type || 'selection';
  const preventCanvasPointer = (event) => event.stopPropagation();
  const setTool = (type) => { onTool(type); setAnnotateOpen(false); };

  useEffect(() => {
    if (!activeReference?.elementId || typeof onReferenceAdmission !== 'function') {
      setModelAdmission(null);
      setModelAdmissionLoading(false);
      setModelAdmissionError('');
      return undefined;
    }
    let canceled = false;
    setModelAdmissionLoading(true);
    setModelAdmissionError('');
    const request = composerAdmissionRequest({
      ratio: referenceRatio,
      resolution: referenceResolution,
    });
    Promise.resolve(onReferenceAdmission(request)).then((payload) => {
      if (canceled) return;
      const selection = composerSelection(payload);
      setModelAdmission(selection);
      setSelectedModel((current) => initialComposerModel(selection, current));
      if (selection?.status !== 'ready') {
        setModelAdmissionError('当前任务与参数组合暂无已验证模型；不会自动切换或降级');
      }
    }).catch((error) => {
      if (canceled) return;
      setModelAdmission(null);
      setModelAdmissionError(String(error?.detail?.message || error?.message || error));
    }).finally(() => {
      if (!canceled) setModelAdmissionLoading(false);
    });
    return () => { canceled = true; };
  }, [activeReference?.elementId, onReferenceAdmission, referenceRatio, referenceResolution]);

  function openReferencePicker(event) {
    const rect = event.currentTarget.getBoundingClientRect();
    setPickerAnchor({ x: rect.left, y: rect.bottom + 8 });
    setPickerSelectedId('');
    setReferencePrompt(composerRef.current?.querySelector('textarea')?.value || referencePrompt);
  }

  function confirmReference() {
    if (!selectedPickerImage) return;
    const image = selectedPickerImage;
    api?.setActiveTool?.({ type: 'selection' });
    if (one?.id !== image.elementId) {
      api?.updateScene?.({ appState: { selectedElementIds: { [image.elementId]: true } } });
    }
    setReference(image);
    setComposerOpen(true);
    setReferenceError('');
    closePicker(false);
    requestAnimationFrame(() => composerRef.current?.querySelector('textarea')?.focus());
  }

  function reviewReference(event) {
    event.preventDefault();
    const message = String(composerRef.current?.querySelector('textarea')?.value || '').trim();
    if (!activeReference || activeReference.elementId !== one?.id) {
      setReferenceError('参考图已变化，请重新选择');
      return;
    }
    if (message.length < 2) {
      setReferenceError('请先描述希望生成的新方案');
      return;
    }
    if (!selectedModelEvidence) {
      setReferenceError('请选择当前任务与参数支持的模型');
      return;
    }
    setReferenceError('');
    onReferenceReview?.({
      sourceElementId: activeReference.elementId,
      userRequest: message,
      outputRatio: referenceRatio,
      outputResolution: referenceResolution,
      model: selectedModelEvidence.provider_model_id,
      modelAdmission,
    });
  }

  return (
    <div className="pa-transplant" aria-label="Product Atelier Canvas 工作台">
      <div className="pa-transplant__tools" onPointerDown={preventCanvasPointer}>
        <button type="button" aria-label="选择" title="选择 · V" aria-pressed={tool === 'selection'} onClick={() => setTool('selection')}>选择</button>
        <button type="button" aria-label="移动画布" title="移动画布 · H" aria-pressed={tool === 'hand'} onClick={() => setTool('hand')}>移动</button>
        <span aria-hidden="true" />
        <button type="button" aria-label="添加文字" title="文字 · T" aria-pressed={tool === 'text'} onClick={() => setTool('text')}>文字</button>
        <div className="pa-transplant__popover-anchor" ref={annotateRef}>
          <button type="button" aria-expanded={annotateOpen} aria-label="标注工具" onClick={() => setAnnotateOpen(!annotateOpen)}>标注</button>
          {annotateOpen && <div className="pa-transplant__popover" role="menu" aria-label="标注工具">
            {['rectangle', 'ellipse', 'arrow'].map((type) => <button type="button" role="menuitem" key={type} onClick={() => setTool(type)}>{({ rectangle: '矩形', ellipse: '椭圆', arrow: '箭头' })[type]}</button>)}
          </div>}
        </div>
      </div>
      <div className="pa-transplant__create" onPointerDown={preventCanvasPointer}>
        <button type="button" data-spatial-zero-open>生成图片</button>
        <button type="button" data-spatial-shell-import>导入</button>
        <button type="button" data-spatial-shell-assets>素材</button>
      </div>
      {placement && !reviewing && <section className="pa-transplant__selection" style={{ left: placement.left, top: placement.top, width: placement.width }} onPointerDown={preventCanvasPointer} aria-label="当前选区操作">
        <div className="pa-transplant__selection-row">
          <strong>{selected.length > 1 ? `${selected.length} 个对象` : kind}</strong>
          {selected.length > 1 ? <><span className="pa-transplant__hint">Ctrl+G 组合 · Delete 删除</span><button type="button" ref={arrangeRef} aria-expanded={arrangeOpen} onClick={() => setArrangeOpen(!arrangeOpen)}>对齐</button></> : <>
            {one?.type === 'image' && refs.asset_id && <button type="button" data-spatial-action={imageAction} className="is-primary">{imageLabel}</button>}
            {refs.asset_id && <button type="button" data-spatial-action="fine-edit">Fabric 精修</button>}
            <div className="pa-transplant__popover-anchor" ref={moreRef}>
              <button type="button" aria-expanded={moreOpen} onClick={() => setMoreOpen(!moreOpen)}>更多</button>
              {moreOpen && <div className="pa-transplant__popover" role="menu">{refs.asset_id && <button type="button" role="menuitem" data-spatial-action="export">导出</button>}<button type="button" role="menuitem" data-spatial-details>对象详情</button></div>}
            </div>
          </>}
        </div>
      </section>}
      {eligible && !reviewing && <form className={`pa-canvas-composer${composerOpen ? ' is-open' : ''}`} ref={composerRef} data-spatial-conversation-form={activeReference ? undefined : ''} onSubmit={activeReference ? reviewReference : undefined} onPointerDown={preventCanvasPointer} noValidate>
          <div className="pa-transplant__composer-main">
          <CanvasReferenceTray image={activeReference} onAdd={openReferencePicker} onRemove={() => { setReference(null); setReferenceError(''); }} addButtonRef={referenceAddRef} />
          <label className="sr-only" htmlFor="pa-transplant-message">{activeReference ? '描述参考生成的新方案' : '告诉 AI 下一步怎么改'}</label>
          <textarea id="pa-transplant-message" data-spatial-conversation-field={activeReference ? undefined : ''} maxLength="1200" rows={composerOpen ? 3 : 1} placeholder={activeReference ? '描述希望参考这张图片生成的新方案' : '告诉 AI 下一步怎么改'} defaultValue={activeReference ? referencePrompt : business.conversationInput || ''} key={`${one.id}:${reviewing}:${activeReference ? 'reference' : 'conversation'}`} onChange={activeReference ? (event) => setReferencePrompt(event.target.value) : undefined} onFocus={() => setComposerOpen(true)} onCompositionStart={() => setCompositionActive(true)} onCompositionEnd={() => setCompositionActive(false)} onKeyDownCapture={(event) => {
            // Workspace's document capture listener handles Ctrl+Enter; IME
            // composition must not submit, while all other keys stay in text.
            if (event.key === 'Enter' && (event.ctrlKey || event.metaKey) && !compositionActive && !event.nativeEvent?.isComposing) {
              if (activeReference) {
                stopComposerKeyboardEvent(event);
                reviewReference(event);
              }
              return;
            }
            if (shouldDismissComposerOnEscape(event, compositionActive)) {
              event.preventDefault();
              stopComposerKeyboardEvent(event);
              setComposerOpen(false);
              document.querySelector('#spatial-canvas-host .excalidraw')?.focus();
              return;
            }
            stopComposerKeyboardEvent(event);
          }} />
          </div>
          {composerOpen && <div className="pa-transplant__composer-footer"><small data-spatial-conversation-status aria-live="polite">{referenceError || modelAdmissionError || business.conversationError || '当前选区 · 核对不会调用 Provider'}</small>{activeReference && <><CanvasModelSelector admission={modelAdmission} value={selectedModel} loading={modelAdmissionLoading} error="" onChange={(model) => { setSelectedModel(model); setReferenceError(''); }} /><CanvasReferenceControls ratio={referenceRatio} resolution={referenceResolution} onChange={(patch) => { if (patch.ratio) setReferenceRatio(patch.ratio); if (patch.resolution) setReferenceResolution(patch.resolution); setReferenceError(''); }} /></>}<button type="submit" disabled={reviewing || (activeReference && (modelAdmissionLoading || !selectedModelEvidence))}>{activeReference ? '核对生成' : '核对修改'}</button></div>}
        </form>}
      {pickerAnchor && <CanvasReferencePicker anchor={pickerAnchor} images={referenceOptions} selectedImage={selectedPickerImage} onSelectImage={setPickerSelectedId} onConfirm={confirmReference} onCancel={closePicker} />}
    </div>
  );
}
