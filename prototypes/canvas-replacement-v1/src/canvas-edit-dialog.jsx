import React, { useEffect, useRef, useState } from 'react';
import { Canvas, FabricImage, PencilBrush, Rect, Textbox } from 'fabric';
import { Button } from 'react-aria-components';
import { stopComposerKeyboardEvent } from '../../../src/js/canvas-transplant-geometry.js';
import { PIXEL_ORIGIN, pixelRegion, hidePixelRegion, expandPixelCanvas } from './fabric-pixel-geometry.js';
import { createAssetPixelSource } from './asset-pixel-source.js';

// Fabric's public selection, transform, free-drawing and serialization APIs.
// The PA adapter only binds the exact source and publishes a new derivative.
export default function CanvasEditDialog({ node, mode, api, canvasAdapter, onCancel, onApplied, onBusy }) {
  const canvasRef = useRef(null), hostRef = useRef(null), editor = useRef(null), region = useRef(null);
  const geometry = useRef(null), frozen = useRef(null), pendingApply = useRef(null);
  const undoStack = useRef([]), redoStack = useRef([]), replaying = useRef(false);
  const [ready, setReady] = useState(false), [busy, setBusy] = useState(false), [error, setError] = useState('');
  const [tool, setTool] = useState(mode || 'fabric'), [color, setColor] = useState('#e74f35'), [brush, setBrush] = useState(12);
  const [padding, setPadding] = useState(100), [layerCount, setLayerCount] = useState(0);
  const [rows, setRows] = useState(2), [columns, setColumns] = useState(2), [pending, setPending] = useState(false);
  const snapshot = () => JSON.stringify({ canvas: editor.current.toObject(['paSource', 'paRegion']), geometry: { ...geometry.current } });
  const checkpoint = () => {
    if (replaying.current || !editor.current || !ready) return;
    const json = snapshot();
    if (undoStack.current.at(-1) === json) return;
    undoStack.current.push(json); undoStack.current = undoStack.current.slice(-30); redoStack.current = [];
    setLayerCount(editor.current.getObjects().filter((object) => !object.paSource && !object.paRegion).length);
  };
  useEffect(() => {
    let live = true, sourceLease;
    const canvas = new Canvas(canvasRef.current, { preserveObjectStacking: true, selection: true, backgroundColor: 'transparent' });
    editor.current = canvas;
    (async () => {
      try {
        await canvasAdapter.flush();
        const record = canvasAdapter.get();
        frozen.current = { canvasId: record.id, version_id: record.current_version_id, element_id: node.id, source_asset_id: node.data.media.asset_id };
        const lease = await createAssetPixelSource(api, node.data.media.asset_id);
        if (!live) { lease.release(); return; }
        sourceLease = lease;
        const image = await FabricImage.fromURL(lease.url);
        if (!live) return;
        const availableWidth = Math.max(260, Math.min(1000, hostRef.current.clientWidth - 24));
        const availableHeight = Math.max(200, Math.min(580, window.innerHeight - 280));
        const scale = Math.min(availableWidth / image.width, availableHeight / image.height, 1);
        geometry.current = { width: image.width, height: image.height, scale, padding: 0 };
        canvas.setDimensions({ width: image.width * scale, height: image.height * scale });
        image.set({ ...PIXEL_ORIGIN, scaleX: scale, scaleY: scale, selectable: false, evented: false, left: 0, top: 0, strokeWidth: 0 }); image.paSource = true;
        canvas.add(image); canvas.renderAll();
        undoStack.current = [snapshot()];
        setReady(true);
      } catch (reason) { sourceLease?.release(); if (live) setError(reason.message); }
    })();
    // Fabric JSON undo reloads its source URL; retain it for the editor's
    // lifetime, never after closing and never in a durable scene.
    return () => { live = false; editor.current = null; canvas.dispose(); sourceLease?.release(); };
  }, [api, canvasAdapter, node.id, node.data.media.asset_id, node.data.src]);

  useEffect(() => {
    const canvas = editor.current; if (!ready || !canvas) return undefined;
    const record = () => checkpoint();
    canvas.on('object:modified', record); canvas.on('object:added', record); canvas.on('object:removed', record);
    return () => { canvas.off('object:modified', record); canvas.off('object:added', record); canvas.off('object:removed', record); };
  }, [ready]);

  useEffect(() => {
    const canvas = editor.current; if (!ready || !canvas) return;
    canvas.isDrawingMode = tool === 'brush';
    if (tool === 'brush') { canvas.freeDrawingBrush = new PencilBrush(canvas); canvas.freeDrawingBrush.color = color; canvas.freeDrawingBrush.width = brush * geometry.current.scale; }
    if (['crop', 'mask'].includes(tool)) {
      if (!region.current || !canvas.getObjects().includes(region.current)) {
        region.current = new Rect({ ...PIXEL_ORIGIN, lockRotation: true, lockScalingFlip: true, minScaleLimit: .02,
          left: canvas.width * .1, top: canvas.height * .1, width: canvas.width * .8, height: canvas.height * .8,
          fill: 'rgba(82,116,158,.12)', stroke: '#52749e', strokeWidth: 1, strokeUniform: true });
        region.current.paRegion = true; canvas.add(region.current);
      }
      region.current.set({ visible: true, selectable: true, evented: true }); canvas.setActiveObject(region.current);
    } else if (region.current) { hidePixelRegion(canvas, region.current); }
    canvas.requestRenderAll();
  }, [ready, tool, brush, color]);

  const restore = async (redo = false) => {
    if (busy || pending || replaying.current) return;
    const canvas = editor.current; if (!canvas) return;
    let serialized;
    if (redo) { serialized = redoStack.current.pop(); if (serialized) undoStack.current.push(serialized); }
    else if (undoStack.current.length > 1) { redoStack.current.push(undoStack.current.pop()); serialized = undoStack.current.at(-1); }
    if (!serialized) return;
    replaying.current = true; setBusy(true); onBusy?.(true);
    try {
      const state = JSON.parse(serialized); geometry.current = state.geometry;
      const g = state.geometry; canvas.setDimensions({ width: (g.width + g.padding * 2) * g.scale, height: (g.height + g.padding * 2) * g.scale });
      await canvas.loadFromJSON(state.canvas); region.current = canvas.getObjects().find((object) => object.paRegion); canvas.requestRenderAll();
    } catch (reason) { setError(`编辑撤销恢复失败：${reason.message}。原图仍保留。`); }
    finally { replaying.current = false; setBusy(false); onBusy?.(false); setLayerCount(canvas.getObjects().filter((object) => !object.paSource && !object.paRegion).length); }
  };
  const removeSelected = () => { const canvas = editor.current; if (!canvas || busy || pending) return; canvas.getActiveObjects().filter((object) => !object.paSource && !object.paRegion).forEach((object) => canvas.remove(object)); canvas.discardActiveObject(); canvas.requestRenderAll(); };
  const expand = () => {
    const canvas = editor.current, g = geometry.current;
    setPadding(expandPixelCanvas(canvas, g, padding)); checkpoint();
  };
  const apply = async () => {
    if (!ready || busy) return;
    setBusy(true); onBusy?.(true); setError('');
    try {
      if (!pendingApply.current) {
        const canvas = editor.current, g = geometry.current;
        // Final application always uses the visible value, even if the user
        // did not separately preview it with “应用范围”. The helper is idempotent.
        if (tool === 'outpaint') expand();
        const selectedRegion = region.current;
        canvas.discardActiveObject();
        if (selectedRegion) selectedRegion.set({ visible: false });
        canvas.renderAll();
        let bitmap = canvas.toCanvasElement(1 / g.scale);
        if (tool === 'crop' || tool === 'mask') {
          if (!selectedRegion) throw new Error('请先选择需要处理的区域');
          const { x, y, width, height } = pixelRegion(selectedRegion, g.scale, bitmap.width, bitmap.height);
          if (tool === 'crop') { const crop = document.createElement('canvas'); crop.width = width; crop.height = height; crop.getContext('2d').drawImage(bitmap, x, y, width, height, 0, 0, width, height); bitmap = crop; }
          else bitmap.getContext('2d').clearRect(x, y, width, height);
        }
        if (selectedRegion) { selectedRegion.set({ visible: ['crop', 'mask'].includes(tool) }); canvas.requestRenderAll(); }
        const bitmaps = tool === 'grid' ? Array.from({ length: rows * columns }, (_, index) => {
          const col = index % columns, row = Math.floor(index / columns);
          const x = Math.floor(bitmap.width * col / columns), y = Math.floor(bitmap.height * row / rows);
          const tile = document.createElement('canvas'); tile.width = Math.floor(bitmap.width * (col + 1) / columns) - x; tile.height = Math.floor(bitmap.height * (row + 1) / rows) - y;
          tile.getContext('2d').drawImage(bitmap, x, y, tile.width, tile.height, 0, 0, tile.width, tile.height); return tile;
        }) : [bitmap];
        pendingApply.current = await Promise.all(bitmaps.map(async (item) => ({
          blob: await new Promise((resolve, reject) => item.toBlob((result) => result ? resolve(result) : reject(new Error('PNG 导出失败')), 'image/png')),
          operation: tool, client_request_id: `canvas-local:${crypto.randomUUID()}`, applied: false,
        })));
        setPending(true); canvas.selection = false; canvas.skipTargetFind = true; canvas.isDrawingMode = false;
      }
      for (const [index, request] of pendingApply.current.entries()) {
        if (request.applied) continue;
        const response = request.response || await api.applySpatialLocalDerivative(frozen.current.canvasId, { ...frozen.current, operation: request.operation, client_request_id: request.client_request_id }, new File([request.blob], 'canvas-local.png', { type: 'image/png' }));
        request.response = response;
        await onApplied(response.asset, node, response.receipt, { keepOpen: true, index }); request.applied = true;
      }
      onCancel();
    } catch (reason) { setError(`${reason.message}。当前编辑和原图均已保留，可重试。`); }
    finally { setBusy(false); onBusy?.(false); }
  };
  return <div className="pa-local-editor" onKeyDown={(event) => {
    stopComposerKeyboardEvent(event);
    if (event.target.matches('input,textarea') || event.target.isContentEditable) return;
    if (['Delete', 'Backspace'].includes(event.key)) { event.preventDefault(); removeSelected(); }
    if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 'z') { event.preventDefault(); restore(event.shiftKey); }
  }}>
    <div className="pa-editor-tools" role="toolbar" aria-label="精修工具">
      {[['fabric', '选择'], ['crop', '裁剪'], ['outpaint', '扩展画幅'], ['mask', '透明遮罩'], ['brush', '画笔'], ['grid', '宫格切分']].map(([value, name]) => <button key={value} type="button" aria-pressed={tool === value} disabled={!ready || busy || pending} onClick={() => setTool(value)}>{name}</button>)}
      <button type="button" disabled={!ready || busy || pending} onClick={() => { const text = new Textbox('文字', { ...PIXEL_ORIGIN, left: 20, top: 20, width: 180, fontFamily: 'Segoe UI', fontSize: 24, fill: color }); editor.current.add(text); editor.current.setActiveObject(text); setTool('fabric'); }}>文字</button>
      <button type="button" disabled={!ready || busy || pending} onClick={removeSelected}>删除选中</button>
      <button type="button" disabled={!ready || busy || pending} onClick={() => restore()}>撤销</button><button type="button" disabled={!ready || busy || pending} onClick={() => restore(true)}>重做</button>
      {tool === 'brush' && <><input aria-label="画笔颜色" type="color" value={color} onChange={(event) => setColor(event.target.value)} /><label>画笔<input aria-label="画笔半径" type="number" min={1} max={200} value={brush} onChange={(event) => setBrush(Number(event.target.value))} /></label></>}
      {tool === 'outpaint' && <><label>四周扩展<input disabled={pending || busy} aria-label="画幅扩展像素" type="number" min={0} max={2048} value={padding} onChange={(event) => setPadding(Number(event.target.value))} />px</label><button type="button" disabled={pending || busy} onClick={expand}>应用范围</button></>}
      {tool === 'grid' && <><label>行<select disabled={pending || busy} value={rows} onChange={(event) => setRows(Number(event.target.value))}>{[1, 2, 3, 4].map((value) => <option key={value}>{value}</option>)}</select></label><label>列<select disabled={pending || busy} value={columns} onChange={(event) => setColumns(Number(event.target.value))}>{[1, 2, 3, 4].map((value) => <option key={value}>{value}</option>)}</select></label><span>{rows * columns} 个独立 PNG · 不生成、不收费</span></>}
    </div>
    <div ref={hostRef} className="pa-fabric-viewport" tabIndex={0}><canvas ref={canvasRef} /></div>
    <div className="pa-editor-info">{node.data.naturalWidth} × {node.data.naturalHeight} · 原图保留 · {layerCount} 个编辑层{tool === 'outpaint' && ' · 扩展区域保留透明，可回画布继续生成'}</div>
    {error && <p role="alert" className="pa-editor-error">{error}</p>}
    <footer><Button isDisabled={busy} onPress={onCancel}>取消</Button><Button className="is-primary" isDisabled={!ready || busy} onPress={apply}>{busy ? '正在保存' : pendingApply.current ? '重试应用' : '应用并返回画布'}</Button></footer>
  </div>;
}
