import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  addEdge,
  applyEdgeChanges,
  applyNodeChanges,
  Background,
  BackgroundVariant,
  Controls,
  ConnectionLineType,
  MiniMap,
  NodeToolbar,
  Panel,
  Position,
  ReactFlow,
  SelectionMode,
  useReactFlow,
  useViewport,
  reconnectEdge,
} from '@xyflow/react';
import { Copy, Group, Ungroup, Trash2, ImagePlus, Scan, Undo2, Redo2, MoreHorizontal, Plus, Download, Check, AlertCircle, LoaderCircle, MousePointer2, Type, Sparkles, AlignHorizontalJustifyStart, Eye, Library, ScrollText, Keyboard, Moon, Sun, Workflow } from 'lucide-react';
import * as paApi from '../../../src/js/api.js';
import { createNodeCanvasAdapter } from './node-canvas-adapter.js';
import { hydrateNodeMedia } from './node-scene-codec.js';
import { mediaIdentity } from './node-scene-codec.js';
import { createNodeExecutionAdapter, bindTaskProjection, taskProjection, freeBranchPosition, mergeTaskProjection, activeTaskIds } from './node-execution-adapter.js';
import { arrangeSelection, createPromptNode, patchNodeDraft, resolveCreativeInput } from './creative-input.js';
import { GovernorDialog, NodeComposer } from './node-composer.jsx';
import { WorkbenchDialog } from './workbench-dialog.jsx';
import { extractVideoFrame } from './media-frame.js';
import { withAssetMediaSource } from './asset-pixel-source.js';
import { composerPlacement, selectionToolbarSide, branchRevealOptions } from './overlay-layout.js';
import './workbench.css';
import {
  createImageNode,
  deleteSelection,
  duplicateSelection,
  groupSelection,
  restorePrototype,
  serializePrototype,
  ungroupSelection,
  selectMediaNode,
} from './canvas-model.js';
import { imageFile } from './image-io.js';
import { nodeTypes } from './canvas-nodes.jsx';
import { edgeTypes } from './reference-edge.jsx';
import { createEmptyImageNode, referenceConnectionAllowed, referenceEdge } from './reference-model.js';

function editableTarget(target) {
  const tag = String(target?.tagName || '').toLowerCase();
  return target?.isContentEditable || ['input', 'textarea', 'select'].includes(tag);
}

function initialDocument() {
  return { nodes: [], edges: [], viewport: { x: 0, y: 0, zoom: 1 } };
}

function SelectionTools({ ids, hasGroup, duplicate, group, ungroup, removeSelected, create, preview, exportMedia, edit, arrange, media, mediaType, frame, composerId, composerHeight }) {
  const flow = useReactFlow();
  const view = useViewport();
  const bounds = flow.getNodesBounds(ids);
  const top = bounds.y * view.zoom + view.y;
  const center = (bounds.x + bounds.width / 2) * view.zoom + view.x;
  const selectionFrame = { left: bounds.x * view.zoom + view.x, top, width: bounds.width * view.zoom, height: bounds.height * view.zoom };
  const composer = ids.length === 1 && ids[0] === composerId
    ? { ...composerPlacement(selectionFrame, window.innerWidth, window.innerHeight, composerHeight), height: composerHeight } : null;
  const side = selectionToolbarSide(selectionFrame, composer);
  const align = center < 175 ? 'start' : center > window.innerWidth - 175 ? 'end' : 'center';
  return <NodeToolbar nodeId={ids} isVisible position={side === 'top' ? Position.Top : Position.Bottom} offset={36} align={align}
    className="pa-selection-toolbar nodrag nopan" role="toolbar" aria-label="选区操作">
    {ids.length > 1 && <span className="pa-selection-count">{ids.length} 个对象</span>}
    {ids.length === 1 && media && <>
      <button type="button" onClick={preview}><Eye size={15} /><span>预览</span></button>
      {mediaType === 'video' ? <><button type="button" onClick={() => frame('first')}>首帧</button><button type="button" onClick={() => frame('current')}>当前帧</button><button type="button" onClick={() => frame('last')}>尾帧</button></> : mediaType === 'image' && <>
      <button type="button" onClick={() => edit('crop')}>裁剪</button>
      <button type="button" onClick={() => edit('outpaint')}>扩图</button>
      <button type="button" onClick={() => edit('mask')}>遮罩</button>
      <button type="button" onClick={() => edit('brush')}>画笔</button>
      <button type="button" onClick={() => edit('grid')}>宫格</button>
      </>}
      <button type="button" onClick={exportMedia} title="下载原始文件"><Download size={15} /></button>
      {mediaType === 'image' && <><i aria-hidden="true" /><button type="button" onClick={create} title="继续创作"><Sparkles size={15} /></button></>}
    </>}
    {ids.length > 1 && <button type="button" onClick={arrange} title="整理选中对象"><AlignHorizontalJustifyStart size={15} /><span>整理</span></button>}
    <button type="button" onClick={duplicate} title="复制对象"><Copy size={15} aria-hidden="true" /><span>复制</span></button>
    {(ids.length > 1 || hasGroup) && <button type="button" onClick={hasGroup ? ungroup : group}>
      {hasGroup ? <Ungroup size={15} aria-hidden="true" /> : <Group size={15} aria-hidden="true" />}<span>{hasGroup ? '取消组合' : '组合'}</span>
    </button>}
    <i aria-hidden="true" />
    <button type="button" onClick={removeSelected} title="删除 · Delete" aria-label="删除选中对象"><Trash2 size={15} aria-hidden="true" /></button>
  </NodeToolbar>;
}

function historySnapshot(nodes, edges) {
  return JSON.stringify(serializePrototype(nodes, edges, { x: 0, y: 0, zoom: 1 }));
}

export function CanvasFoundationPrototype() {
  const initial = useMemo(initialDocument, []);
  const [nodes, setNodes] = useState(initial.nodes);
  const [edges, setEdges] = useState(initial.edges);
  const [viewport, setViewport] = useState(initial.viewport);
  const [metadata, setMetadata] = useState({});
  const metadataRef = useRef(metadata);
  const [message, setMessage] = useState('画布已就绪');
  const [saveStatus, setSaveStatus] = useState({ phase: 'loading', message: '正在打开画布' });
  const [ready, setReady] = useState(false);
  const [loadEpoch, setLoadEpoch] = useState(0);
  const canvasAdapter = useMemo(() => createNodeCanvasAdapter({ api: paApi, storage: localStorage, onStatus: setSaveStatus }), []);
  const executionAdapter = useMemo(() => createNodeExecutionAdapter({ api: paApi, canvasAdapter }), [canvasAdapter]);
  const [activeComposerId, setActiveComposerId] = useState('');
  const [composerHeight, setComposerHeight] = useState(250);
  const [review, setReview] = useState(null), [submitting, setSubmitting] = useState(false), [reviewError, setReviewError] = useState('');
  const [dialog, setDialog] = useState(null);
  const [jobs, setJobs] = useState([]);
  const [theme, setTheme] = useState('light');
  const [interacting, setInteracting] = useState(false);
  const [clipboard, setClipboard] = useState(null);
  const [historyVersion, setHistoryVersion] = useState(0);
  const [menu, setMenu] = useState(null);
  const menuRef = useRef(null);
  const returnFocusRef = useRef(null);
  const importTargetRef = useRef('');
  const reconnectingEdgeRef = useRef('');
  const fileInputRef = useRef(null);
  const persistenceTimer = useRef(0);
  const historyTimer = useRef(0);
  const nodesRef = useRef(nodes);
  const edgesRef = useRef(edges);
  const viewportRef = useRef(viewport);
  const mediaTimes = useRef(new Map()), frameBusy = useRef(false);
  const operationBusy = useRef(0), dialogBusy = useRef(false), dialogRef = useRef(null);
  const setDialogBusy = useCallback(busy => { dialogBusy.current = busy; }, []);
  const [branchToReveal, setBranchToReveal] = useState(null);
  const applyingHistory = useRef(false);
  const history = useRef({
    past: [],
    present: historySnapshot(initial.nodes, initial.edges),
    future: [],
  });
  const flow = useReactFlow();
  const selectedIds = nodes.filter((node) => node.selected).map((node) => node.id);
  const selectedGroups = nodes.filter((node) => node.selected && node.type === 'group');
  const selectedNode = selectedIds.length === 1 ? nodes.find((node) => node.id === selectedIds[0]) : null;

  useEffect(() => {
    if (!branchToReveal || dialog) return undefined;
    if (canvasAdapter.get()?.id !== branchToReveal.owner) { setBranchToReveal(null); return undefined; }
    const ids = branchToReveal.ids.filter(id => nodes.some(node => node.id === id));
    if (!ids.length) { setBranchToReveal(null); return undefined; }
    const frame = requestAnimationFrame(() => {
      const options = branchRevealOptions(flow.getNodesBounds(ids), flow.getViewport(), window.innerWidth, window.innerHeight, ids);
      if (options) flow.fitView(options).catch(error => setMessage(`结果已保存；可用适合视图查看。${error.message}`));
      setBranchToReveal(null);
    });
    return () => cancelAnimationFrame(frame);
  }, [branchToReveal, dialog, canvasAdapter, flow, nodes]);

  useEffect(() => { document.documentElement.dataset.theme = theme; }, [theme]);

  useEffect(() => {
    if (!ready) return undefined;
    let disposed = false, unlisten;
    paApi.onAppCloseRequested(async (event) => {
      event.preventDefault(); clearTimeout(persistenceTimer.current);
      try {
        if (operationBusy.current || dialogBusy.current || frameBusy.current) {
          setMessage('正在保存或导入，请完成后再关闭；当前操作没有被取消'); return;
        }
        if (dialogRef.current?.type === 'edit' && !window.confirm('放弃尚未应用的精修编辑并关闭？原图及已保存结果不受影响。')) return;
        await canvasAdapter.save(nodesRef.current, edgesRef.current, viewportRef.current, metadataRef.current);
        await canvasAdapter.flush(); await paApi.completeAppClose();
      } catch (error) { setMessage(`未关闭：保存尚未成功。${error.message}`); }
    }).then((cleanup) => { if (disposed) cleanup(); else unlisten = cleanup; });
    return () => { disposed = true; unlisten?.(); };
  }, [ready, canvasAdapter]);

  const updateDraft = useCallback((id, patch) => setNodes((current) => patchNodeDraft(current, id, patch)), []);
  const removeNode = useCallback((id) => {
    const result = deleteSelection(nodesRef.current.map((node) => ({ ...node, selected: node.id === id })), edgesRef.current.map((edge) => ({ ...edge, selected: false })));
    setNodes(result.nodes); setEdges(result.edges); setMessage('已删除对象 · 可撤销');
  }, []);

  useEffect(() => {
    let active = true;
    canvasAdapter.load().then((document) => {
      if (!active) return;
      setNodes(document.nodes); setEdges(document.edges); setViewport(document.viewport);
      metadataRef.current = document.metadata; setMetadata(document.metadata);
      flow.setViewport(document.viewport);
      history.current = { past: [], present: historySnapshot(document.nodes, document.edges), future: [] };
      setReady(true);
    }).catch(() => {});
    return () => { active = false; };
  }, [canvasAdapter, flow, loadEpoch]);

  const decorateNodes = useCallback((items) => items.map((node) => ({
    ...node,
    data: {
      ...node.data,
      onImport: (id) => {
        importTargetRef.current = id;
        fileInputRef.current?.click();
      },
      onResizeEnd: (id, params) => {
        setInteracting(false);
        setNodes((current) => current.map((candidate) => candidate.id === id ? {
          ...candidate,
          style: { ...candidate.style, width: params.width, height: params.height },
        } : candidate));
      },
      onTransformStart: () => setInteracting(true),
      onDelete: removeNode,
      onText: (id, text) => setNodes((current) => current.map((candidate) => candidate.id === id ? { ...candidate, data: { ...candidate.data, text } } : candidate)),
      onTemplates: (id) => setDialog({ type: 'templates', nodeId: id }),
      onSplitPrompt: (id) => setDialog({ type: 'split-prompt', nodeId: id }),
      onRecoverMedia: async () => setNodes(await hydrateNodeMedia(nodesRef.current, paApi)),
      onMediaTime: (id, seconds) => mediaTimes.current.set(id, seconds),
      onSelectMedia: (id, event) => setNodes(current => selectMediaNode(current, id, event)),
      onCancelTask: async (id) => {
        const task = nodesRef.current.find((node) => node.id === id)?.data.execution?.task_id;
        if (task) { try { await paApi.cancelJob(task); setMessage('已请求取消 · 远端请求可能仍会完成，历史结果保留'); } catch (error) { setMessage(error.message); } }
      },
    },
  })), [removeNode]);

  useEffect(() => { nodesRef.current = nodes; }, [nodes]);
  useEffect(() => { edgesRef.current = edges; }, [edges]);
  useEffect(() => { viewportRef.current = viewport; }, [viewport]);
  useEffect(() => { metadataRef.current = metadata; }, [metadata]);
  useEffect(() => { dialogRef.current = dialog; }, [dialog]);

  // Selection can reveal a different node draft, but deselect does NOT destroy
  // any draft or hide active work. Prompt editing is its own node surface.
  useEffect(() => {
    if (selectedNode?.type === 'image') setActiveComposerId(selectedNode.id);
  }, [selectedNode?.id]);

  useEffect(() => {
    if (!ready) return undefined;
    let canceled = false, timer;
    const recover = async () => {
      const owner = canvasAdapter.get()?.id;
      try {
        const response = await paApi.getJobs(100);
        const recent = response.jobs || [];
        const missing = activeTaskIds(nodesRef.current).filter(id => !recent.some(job => job.id === id));
        const older = await Promise.all(missing.map(async id => { const response = await paApi.getJob(id); return response.job || response; }));
        const relevant = [...recent, ...older].filter((job) => (job.parameters || job.snapshot?.parameters || {}).spatial_canvas_id === owner);
        if (canceled || canvasAdapter.get()?.id !== owner) return;
        setJobs(relevant);
        for (const job of relevant) {
          const targetId = (job.parameters || job.snapshot?.parameters || {}).node_target_id;
          const source = nodesRef.current.find((node) => taskProjection(node.data.execution, job.id))
            || nodesRef.current.find((node) => node.id === targetId);
          if (!source) continue; // Deleted views remain accessible in Task logs.
          const projection = taskProjection(source.data.execution, job.id);
          if (projection?.status === job.status && projection?.updated_at === (job.updated_at || '') && ['completed', 'partial', 'failed', 'canceled', 'interrupted'].includes(job.status)) continue;
          const beforeNodes = nodesRef.current;
          const projected = await executionAdapter.project(job, beforeNodes, edgesRef.current);
          if (canceled || canvasAdapter.get()?.id !== owner) return;
          const result = mergeTaskProjection(job, beforeNodes, projected, nodesRef.current, edgesRef.current);
          nodesRef.current = result.nodes; edgesRef.current = result.edges;
          setNodes(result.nodes); setEdges(result.edges);
        }
      } catch (error) { if (!canceled) setMessage(`任务读取暂不可用 · ${error.message}`); }
      finally { if (!canceled) timer = window.setTimeout(recover, 3000); }
    };
    recover();
    return () => { canceled = true; clearTimeout(timer); };
  }, [ready, canvasAdapter, executionAdapter]);

  const openReview = useCallback(async (input) => {
    if (Object.keys(nodesRef.current.find(node => node.id === input.targetId)?.data.execution?.tasks || {}).length >= 500) {
      throw new Error('这个对象已保留 500 次任务记录；请在新的结果分支继续创作，历史不会被清理');
    }
    clearTimeout(persistenceTimer.current);
    await canvasAdapter.save(nodesRef.current, edgesRef.current, viewportRef.current, metadataRef.current);
    const prepared = await executionAdapter.preview(input);
    if (resolveCreativeInput(nodesRef.current, edgesRef.current, input.targetId).signature !== input.signature) throw new Error('输入已变化；请核对当前草稿');
    setReviewError(''); setReview(prepared);
  }, [canvasAdapter, executionAdapter]);

  const submitReview = useCallback(async () => {
    if (!review || submitting) return;
    setSubmitting(true); setReviewError('');
    try {
      if (resolveCreativeInput(nodesRef.current, edgesRef.current, review.input.targetId).signature !== review.input.signature) throw new Error('输入已变化；不会提交旧预览');
      await canvasAdapter.save(nodesRef.current, edgesRef.current, viewportRef.current, metadataRef.current);
      const job = await executionAdapter.submit(review, { confirmed: true });
      const next = nodesRef.current.map((node) => node.id === review.input.targetId ? { ...node, data: { ...node.data,
        draft: { ...node.data.draft, prompt: '' }, execution: bindTaskProjection(node.data.execution, job, { activate: true }),
      } } : node);
      nodesRef.current = next; setNodes(next);
      await canvasAdapter.save(next, edgesRef.current, viewportRef.current, metadataRef.current);
      setReview(null); setMessage('任务已提交 · 结果会回到当前画布');
    } catch (error) { setReviewError(error.message); }
    finally { setSubmitting(false); }
  }, [review, submitting, canvasAdapter, executionAdapter]);

  useEffect(() => {
    clearTimeout(historyTimer.current);
    if (applyingHistory.current) {
      applyingHistory.current = false;
      return undefined;
    }
    historyTimer.current = window.setTimeout(() => {
      const next = historySnapshot(nodesRef.current, edgesRef.current);
      if (next === history.current.present) return;
      history.current.past = [...history.current.past.slice(-49), history.current.present];
      history.current.present = next;
      history.current.future = [];
      setHistoryVersion((value) => value + 1);
    }, 180);
    return () => clearTimeout(historyTimer.current);
  }, [nodes, edges]);

  useEffect(() => {
    clearTimeout(persistenceTimer.current);
    if (!ready) return undefined;
    persistenceTimer.current = window.setTimeout(() => {
      canvasAdapter.save(nodesRef.current, edgesRef.current, viewportRef.current, metadataRef.current).catch(() => {});
    }, 450);
    return () => clearTimeout(persistenceTimer.current);
  }, [canvasAdapter, ready, nodes, edges, viewport, metadata]);

  const replaceDocumentFromHistory = useCallback((serialized) => {
    const restored = restorePrototype(JSON.parse(serialized));
    if (!restored) return;
    applyingHistory.current = true;
    const executions = new Map(nodesRef.current.map((node) => [node.id, node.data.execution]));
    setNodes(restored.nodes.map((node) => executions.get(node.id) ? { ...node, data: { ...node.data, execution: executions.get(node.id) } } : node));
    setEdges(restored.edges);
  }, []);

  const undo = useCallback(() => {
    clearTimeout(historyTimer.current);
    const state = history.current;
    if (!state.past.length) return;
    const previous = state.past[state.past.length - 1];
    state.past = state.past.slice(0, -1);
    state.future = [state.present, ...state.future.slice(0, 49)];
    state.present = previous;
    replaceDocumentFromHistory(previous);
    setHistoryVersion((value) => value + 1);
    setMessage('已撤销');
  }, [replaceDocumentFromHistory]);

  const redo = useCallback(() => {
    clearTimeout(historyTimer.current);
    const state = history.current;
    if (!state.future.length) return;
    const next = state.future[0];
    state.future = state.future.slice(1);
    state.past = [...state.past.slice(-49), state.present];
    state.present = next;
    replaceDocumentFromHistory(next);
    setHistoryVersion((value) => value + 1);
    setMessage('已重做');
  }, [replaceDocumentFromHistory]);

  const importImages = useCallback(async (files, origin) => {
    const accepted = Array.from(files || []);
    if (!accepted.length) {
      setMessage('请选择图片、视频或音频');
      return;
    }
    const owner = canvasAdapter.get().id; operationBusy.current += 1;
    try {
      const targetId = importTargetRef.current;
      importTargetRef.current = '';
      const imported = await canvasAdapter.importMedia(accepted);
      if (canvasAdapter.get().id !== owner) throw new Error('画布已切换，导入素材保留在资产库，未加入其他画布');
      const images = imported.assets;
      if (!images.length) throw new Error(imported.errors[0]?.message || '没有素材导入成功');
      const additions = images.map((image, index) => ({ ...createImageNode({
        ...image,
        position: { x: origin.x + index * 36, y: origin.y + index * 36 },
      }), type: ['video', 'audio'].includes(image.kind) ? image.kind : 'image', ...(image.kind === 'audio' ? { style: { width: 320, height: 112 } } : {}) }));
      const [first, ...rest] = additions;
      setNodes((current) => {
        const target = current.find((node) => node.id === targetId && !node.data.src);
        if (!target) return [...current.map((node) => ({ ...node, selected: false })), ...decorateNodes(additions)];
        return decorateNodes([
          ...current.map((node) => node.id === targetId
            ? { ...first, id: node.id, parentId: node.parentId, position: node.position, data: { ...node.data, ...first.data } }
            : { ...node, selected: false }),
          ...rest,
        ]);
      });
      setMessage(`已导入 ${additions.length} 项素材${imported.errors.length ? ` · ${imported.errors.length} 项失败，成功项已保留` : ''}`);
    } catch (error) {
      setMessage(error?.message || String(error));
    } finally { operationBusy.current -= 1; }
  }, [canvasAdapter, decorateNodes]);

  const onDrop = useCallback((event) => {
    event.preventDefault();
    importTargetRef.current = '';
    const origin = flow.screenToFlowPosition({ x: event.clientX, y: event.clientY });
    importImages(event.dataTransfer.files, origin);
  }, [flow, importImages]);

  const duplicate = useCallback(() => {
    const result = duplicateSelection(nodes, edges);
    if (!result.selectedIds.length) {
      setMessage('请选择要复制的对象');
      return;
    }
    setNodes(decorateNodes(result.nodes));
    setEdges(result.edges);
    setMessage(`已复制 ${result.selectedIds.length} 个对象`);
  }, [decorateNodes, edges, nodes]);

  const group = useCallback(() => {
    const result = groupSelection(nodes);
    if (!result.groupId) return;
    setNodes(decorateNodes(result.nodes));
    setMessage('已组合选中对象');
  }, [decorateNodes, nodes]);

  const ungroup = useCallback(() => {
    const result = ungroupSelection(nodes);
    if (!result.selectedIds.length) return;
    setNodes(decorateNodes(result.nodes));
    setMessage('已取消组合');
  }, [decorateNodes, nodes]);

  const removeSelected = useCallback(() => {
    const result = deleteSelection(nodes, edges);
    setNodes(result.nodes);
    setEdges(result.edges);
    setMessage('已删除选中内容 · 可撤销');
  }, [edges, nodes]);

  const closeMenu = useCallback(() => {
    setMenu(null);
    returnFocusRef.current?.focus?.();
  }, []);

  const openMenu = useCallback((event, nodeId = '') => {
    event.preventDefault();
    returnFocusRef.current = event.currentTarget;
    if (nodeId && !nodesRef.current.find((node) => node.id === nodeId)?.selected) {
      setNodes((current) => current.map((node) => ({ ...node, selected: node.id === nodeId })));
    }
    setMenu({ nodeId, x: Math.min(Math.max(12, event.clientX), window.innerWidth - 224), y: Math.min(Math.max(72, event.clientY), window.innerHeight - 230) });
  }, []);

  useEffect(() => {
    if (!menu) return undefined;
    menuRef.current?.querySelector('button')?.focus();
    const outside = (event) => { if (!menuRef.current?.contains(event.target)) closeMenu(); };
    window.addEventListener('pointerdown', outside);
    return () => window.removeEventListener('pointerdown', outside);
  }, [closeMenu, menu]);

  const addEmptyImage = useCallback((point) => {
    const origin = point || flow.screenToFlowPosition({ x: window.innerWidth / 2 - 158, y: window.innerHeight / 2 - 105 });
    const node = createEmptyImageNode(origin);
    setNodes((current) => decorateNodes([...current.map((candidate) => ({ ...candidate, selected: false })), node]));
    closeMenu();
    return node;
  }, [closeMenu, decorateNodes, flow]);

  const addPrompt = useCallback((point) => {
    const node = createPromptNode(point || flow.screenToFlowPosition({ x: window.innerWidth / 2 - 150, y: window.innerHeight / 2 - 105 }));
    setNodes((current) => [...current.map((candidate) => ({ ...candidate, selected: false })), node]); closeMenu(); return node;
  }, [closeMenu, flow]);

  useEffect(() => {
    const onKeyDown = (event) => {
      if (review || dialog) return;
      if (menu) {
        if (event.key === 'Escape') { event.preventDefault(); closeMenu(); }
        if (['ArrowDown', 'ArrowUp', 'Home', 'End'].includes(event.key)) {
          event.preventDefault();
          const items = [...(menuRef.current?.querySelectorAll('button:not(:disabled)') || [])];
          const index = items.indexOf(document.activeElement);
          const next = event.key === 'Home' ? 0 : event.key === 'End' ? items.length - 1 : (index + (event.key === 'ArrowDown' ? 1 : -1) + items.length) % items.length;
          items[next]?.focus();
        }
        return;
      }
      if (editableTarget(event.target)) return;
      const command = event.ctrlKey || event.metaKey;
      if (command && event.key.toLowerCase() === 'z') {
        event.preventDefault();
        if (event.shiftKey) redo(); else undo();
        return;
      }
      if (command && event.key.toLowerCase() === 'y') {
        event.preventDefault();
        redo();
        return;
      }
      if (command && event.key.toLowerCase() === 'c' && selectedIds.length) {
        event.preventDefault();
        setClipboard({ ...serializePrototype(nodesRef.current, edgesRef.current, viewportRef.current), selectedIds: nodesRef.current.filter((node) => node.selected).map((node) => node.id) });
        setMessage(`已复制 ${selectedIds.length} 个对象`);
        return;
      }
      if (command && event.key.toLowerCase() === 'a' && nodes.length) {
        event.preventDefault();
        setNodes((current) => current.map((node) => ({ ...node, selected: true })));
        setMessage(`已选择 ${nodes.length} 个对象`);
        return;
      }
      if ((event.key === 'Delete' || event.key === 'Backspace') && (selectedIds.length || edges.some((edge) => edge.selected))) {
        event.preventDefault();
        removeSelected();
        return;
      }
      if (event.key === 'Escape') {
        setNodes((current) => current.map((node) => ({ ...node, selected: false })));
        setEdges((current) => current.map((edge) => ({ ...edge, selected: false })));
      }
      if (command && event.key.toLowerCase() === 'g' && !event.shiftKey) {
        event.preventDefault();
        group();
        return;
      }
      if (command && event.shiftKey && event.key.toLowerCase() === 'g') {
        event.preventDefault();
        ungroup();
      }
    };
    const onPaste = (event) => {
      if (editableTarget(event.target)) return;
      const files = Array.from(event.clipboardData?.files || []).filter(imageFile);
      if (files.length) {
        event.preventDefault();
        importTargetRef.current = '';
        const center = flow.screenToFlowPosition({ x: window.innerWidth / 2, y: window.innerHeight / 2 });
        importImages(files, center);
        return;
      }
      if (clipboard) {
        event.preventDefault();
        const copied = restorePrototype(clipboard);
        if (!copied) return;
        // Selection at COPY time, not whatever is selected at PASTE time.
        copied.nodes = clipboard.nodes.map((node) => ({ ...node, selected: clipboard.selectedIds?.includes(node.id) }));
        const result = duplicateSelection(copied.nodes, copied.edges);
        const oldIds = new Set(copied.nodes.map((node) => node.id));
        setNodes((current) => [...current.map((node) => ({ ...node, selected: false })), ...result.nodes.filter((node) => !oldIds.has(node.id))]);
        const oldEdges = new Set(copied.edges.map((edge) => edge.id));
        setEdges((current) => [...current, ...result.edges.filter((edge) => !oldEdges.has(edge.id))]);
        setMessage(`已粘贴 ${result.selectedIds.length} 个对象`);
      }
    };
    window.addEventListener('keydown', onKeyDown, true);
    window.addEventListener('paste', onPaste, true);
    return () => {
      window.removeEventListener('keydown', onKeyDown, true);
      window.removeEventListener('paste', onPaste, true);
    };
  }, [clipboard, closeMenu, duplicate, edges, flow, group, importImages, menu, nodes.length, redo, removeSelected, selectedIds.length, undo, ungroup, review, dialog]);

  const onNodesChange = useCallback((changes) => {
    setNodes((current) => decorateNodes(applyNodeChanges(changes, current)));
  }, [decorateNodes]);
  const onEdgesChange = useCallback((changes) => setEdges((current) => applyEdgeChanges(changes, current)), []);
  const onConnect = useCallback((connection) => {
    if (!referenceConnectionAllowed(connection, nodesRef.current, edgesRef.current)) { setMessage('不能连接：输入需兼容，且不能重复、自连或形成循环'); return; }
    setEdges((current) => addEdge(referenceEdge(connection), current)); setMessage('已建立输入连接 · 连线不会自动执行');
  }, []);

  const onConnectEnd = useCallback((event, state) => {
    if (state.isValid || !state.fromNode || !event.target?.closest?.('.react-flow__pane')) return;
    const point = event.changedTouches?.[0] || event;
    const source = state.fromHandle?.type === 'source';
    const position = flow.screenToFlowPosition({ x: point.clientX + (source ? 24 : -340), y: point.clientY - 105 });
    const node = addEmptyImage(position);
    const connection = source ? { source: state.fromNode.id, target: node.id } : { source: node.id, target: state.fromNode.id };
    setEdges((current) => addEdge(referenceEdge(connection), current));
    setMessage('已创建相连图片节点 · 导入图片后继续');
  }, [addEmptyImage, flow]);

  const onReconnect = useCallback((edge, connection) => {
    setEdges((current) => referenceConnectionAllowed(connection, nodesRef.current, current, edge.id)
      ? reconnectEdge(edge, referenceEdge(connection), current) : current);
  }, []);

  const clearPrototype = useCallback(() => {
    if (!window.confirm('清空这个隔离原型中的全部测试对象？')) return;
    setNodes([]);
    setEdges([]);
    setMessage('原型已清空');
  }, []);

  const flowNodes = useMemo(() => decorateNodes(nodes), [decorateNodes, nodes]);
  const flowEdges = useMemo(() => edges.map((edge) => ({
    ...edge, type: 'reference', data: { kind: edge.kind, onRemove: (id) => setEdges((current) => current.filter((candidate) => candidate.id !== id)) },
  })), [edges]);
  const canUndo = history.current.past.length > 0;
  const canRedo = history.current.future.length > 0;
  void historyVersion;

  const exportNode = (node) => { if (!node?.data.media) return; paApi.downloadAsset(node.data.media.asset_id, node.data.name).catch((error) => setMessage(error.message)); };
  const restoreJobResult = async (job) => {
    if ((job.parameters || job.snapshot?.parameters || {}).spatial_canvas_id !== canvasAdapter.get().id) throw new Error('Task 不属于当前画布');
    const owner = canvasAdapter.get().id, beforeNodes = nodesRef.current;
    const projected = await executionAdapter.project(job, beforeNodes, edgesRef.current, { restoreApplied: true });
    if (canvasAdapter.get().id !== owner) throw new Error('来源画布已切换；未把结果加入其他画布');
    const result = mergeTaskProjection(job, beforeNodes, projected, nodesRef.current, edgesRef.current, { allowMissingSource: true });
    nodesRef.current = result.nodes; edgesRef.current = result.edges; setNodes(result.nodes); setEdges(result.edges);
    await canvasAdapter.save(result.nodes, result.edges, viewportRef.current, metadataRef.current);
    const ids = result.nodes.filter(node => node.data.media?.task_id === job.id).map(node => node.id);
    if (!ids.length) throw new Error('这个 Task 暂无可回填的主结果；没有创建新 Task 或 Provider attempt');
    setNodes(current => current.map(node => ({ ...node, selected: node.id === ids.at(-1) })));
    setDialog(null); setBranchToReveal({ owner, ids }); setMessage('已找回现有 Result · 未创建 Task 或 Provider attempt');
  };
  const switchCanvas = async (canvasId, name = '') => {
    if (operationBusy.current || dialogBusy.current || frameBusy.current) throw new Error('当前操作尚未完成，完成后可以切换画布');
    await canvasAdapter.save(nodesRef.current, edgesRef.current, viewportRef.current, metadataRef.current);
    const document = canvasId ? await canvasAdapter.open(canvasId) : await canvasAdapter.create(name);
    nodesRef.current = document.nodes; edgesRef.current = document.edges; viewportRef.current = document.viewport;
    metadataRef.current = document.metadata; setMetadata(document.metadata);
    history.current = { past: [], present: historySnapshot(document.nodes, document.edges), future: [] };
    setNodes(document.nodes); setEdges(document.edges); setViewport(document.viewport); flow.setViewport(document.viewport);
    setActiveComposerId(''); setReview(null); setDialog(null); setJobs([]); setHistoryVersion((value) => value + 1); setMessage('画布已就绪');
  };
  const openEditor = async (mode) => {
    if (!selectedNode?.data.media || selectedNode.type !== 'image') return;
    try { await canvasAdapter.save(nodesRef.current, edgesRef.current, viewportRef.current, metadataRef.current); setDialog({ type: 'edit', nodeId: selectedNode.id, mode }); }
    catch (error) { setMessage(`先完成来源保存再精修 · ${error.message}`); }
  };
  const applyEdited = async (asset, source, receipt, options = {}) => {
    const existing = nodesRef.current.find((node) => node.data.media?.asset_id === asset.id);
    if (!existing) {
      const node = createImageNode({ name: asset.name, media: mediaIdentity(asset), src: await paApi.getAssetContentUrl(asset.id), naturalWidth: asset.width, naturalHeight: asset.height,
        position: { x: 0, y: 0 } });
      node.position = freeBranchPosition(source, nodesRef.current, node.style.width, node.style.height);
      const next = [...nodesRef.current.map((item) => ({ ...item, selected: false })), node];
      const nextEdges = [...edgesRef.current, { ...referenceEdge({ source: source.id, target: node.id }), kind: 'flow' }];
      nodesRef.current = next; edgesRef.current = nextEdges; setNodes(next); setEdges(nextEdges);
      await canvasAdapter.save(next, nextEdges, viewportRef.current, metadataRef.current);
      setBranchToReveal(current => ({ owner: canvasAdapter.get().id,
        ids: [...new Set([source.id, ...(current?.owner === canvasAdapter.get().id ? current.ids : []), node.id])] }));
    } else await canvasAdapter.save(nodesRef.current, edgesRef.current, viewportRef.current, metadataRef.current);
    if (!options.keepOpen) setDialog(null); setMessage('本地编辑已保存 · 原图保留');
  };
  const extractFrame = async (position) => {
    if (frameBusy.current || selectedNode?.type !== 'video') return;
    const source = selectedNode; frameBusy.current = true; setMessage('正在读取视频帧…');
    try {
      await canvasAdapter.save(nodesRef.current, edgesRef.current, viewportRef.current, metadataRef.current);
      const record = canvasAdapter.get(); const frame = await withAssetMediaSource(paApi, source.data.media.asset_id, 'video',
        url => extractVideoFrame(url, position, mediaTimes.current.get(source.id) || 0));
      const result = await paApi.applySpatialLocalDerivative(record.id, { version_id: record.current_version_id, element_id: source.id, source_asset_id: source.data.media.asset_id,
        operation: 'video-frame', client_request_id: `video-frame:${frame.seconds}:${crypto.randomUUID()}` }, new File([frame.blob], 'frame.png', { type: 'image/png' }));
      await applyEdited(result.asset, source, result.receipt); setMessage(`视频 ${frame.seconds.toFixed(2)} 秒帧已保存 · 可作为参考图`);
    } catch (error) { setMessage(`抽帧未完成 · ${error.message} · 原视频保留`); }
    finally { frameBusy.current = false; }
  };
  let reviewStale = false;
  if (review) { try { reviewStale = resolveCreativeInput(nodes, edges, review.input.targetId).signature !== review.input.signature; } catch (_) { reviewStale = true; } }
  return <main className={`pa-foundation-shell ${interacting ? 'pa-is-interacting' : ''}`} data-theme={theme}>
    {!ready && <div className="pa-loading-workspace" role="status">
      {saveStatus.phase === 'error' ? <><AlertCircle size={22} /><p>{saveStatus.message}</p><button type="button" onClick={() => setLoadEpoch((value) => value + 1)}>重新打开 · 旧副本保留</button></> : <><LoaderCircle size={22} /><p>{saveStatus.message}</p></>}
    </div>}
    <ReactFlow
      nodes={flowNodes}
      edges={flowEdges}
      nodeTypes={nodeTypes}
      edgeTypes={edgeTypes}
      connectionLineType={ConnectionLineType.Bezier}
      connectionLineStyle={{ stroke: '#64748b', strokeWidth: 1.8, strokeDasharray: '5 4' }}
      isValidConnection={(connection) => referenceConnectionAllowed(connection, nodes, edges, reconnectingEdgeRef.current)}
      defaultViewport={initial.viewport}
      minZoom={0.12}
      maxZoom={4}
      selectionMode={SelectionMode.Partial}
      selectionOnDrag
      panOnDrag={[1, 2]}
      panOnScroll
      zoomOnScroll={false}
      zoomActivationKeyCode="Control"
      multiSelectionKeyCode={["Control", "Shift"]}
      deleteKeyCode={null}
      onNodesChange={onNodesChange}
      onNodeDragStart={() => setInteracting(true)}
      onNodeDragStop={() => setInteracting(false)}
      onEdgesChange={onEdgesChange}
      onConnect={onConnect}
      onConnectStart={() => setInteracting(true)}
      onConnectEnd={(...args) => { setInteracting(false); onConnectEnd(...args); }}
      onReconnect={onReconnect}
      onReconnectStart={(_, edge) => { reconnectingEdgeRef.current = edge.id; }}
      onReconnectEnd={() => { reconnectingEdgeRef.current = ''; }}
      onNodeContextMenu={(event, node) => openMenu(event, node.id)}
      onPaneContextMenu={(event) => openMenu(event)}
      onDoubleClick={(event) => { if (event.target.closest('.react-flow__pane')) openMenu(event); }}
      onMoveEnd={(_, nextViewport) => setViewport(nextViewport)}
      onDrop={onDrop}
      onDragOver={(event) => { event.preventDefault(); event.dataTransfer.dropEffect = 'copy'; }}
      onPaneClick={() => { closeMenu(); setMessage('画布已就绪'); }}
      fitView={false}
      colorMode={theme}
      proOptions={{ hideAttribution: false }}
      ariaLabelConfig={{ 'node.a11yDescription.default': '按 Enter 选择对象，方向键移动，Delete 删除。' }}
    >
      <Background variant={BackgroundVariant.Dots} gap={24} size={1} />
      <MiniMap pannable zoomable ariaLabel="画布小地图" nodeColor={(node) => node.type === 'group' ? '#e2e8f0' : '#a4adb9'} maskColor="rgba(244,246,249,.65)" />
      <Controls position="bottom-left" showInteractive={false} />

      <Panel position="top-left" className="pa-foundation-title">
        <button type="button" onClick={() => setDialog({ type: 'canvases' })} aria-label="返回画布列表"><MousePointer2 size={18} aria-hidden="true" /></button>
        <strong>{canvasAdapter.get()?.name || '未命名画布'}</strong>
      </Panel>

      <Panel position="top-right" className="pa-foundation-actions">
        <input
          ref={fileInputRef}
          type="file"
          accept="image/png,image/jpeg,image/webp,video/mp4,video/webm,audio/mpeg,audio/wav,audio/ogg,audio/flac,.mp3,.wav,.ogg,.flac"
          multiple
          hidden
          onChange={(event) => {
            const origin = flow.screenToFlowPosition({ x: window.innerWidth * 0.58, y: window.innerHeight * 0.38 });
            importImages(event.target.files, origin);
            event.target.value = '';
          }}
        />
        <button type="button" className="is-primary" aria-label="导入素材" onClick={() => { importTargetRef.current = ''; fileInputRef.current?.click(); }}><ImagePlus size={16} aria-hidden="true" /><span>导入素材</span></button>
        <button type="button" onClick={openMenu} title="创建节点" aria-label="创建节点"><Plus size={17} aria-hidden="true" /></button>
        <button type="button" onClick={() => flow.fitView({ padding: 0.2, duration: 240 })} disabled={!nodes.length} title="适合视图" aria-label="适合视图"><Scan size={16} aria-hidden="true" /></button>
        <span aria-hidden="true" />
        <button type="button" onClick={undo} disabled={!canUndo} title="撤销 · Ctrl+Z" aria-label="撤销"><Undo2 size={16} aria-hidden="true" /></button>
        <button type="button" onClick={redo} disabled={!canRedo} title="重做 · Ctrl+Shift+Z" aria-label="重做"><Redo2 size={16} aria-hidden="true" /></button>
        <button type="button" onClick={openMenu} title="画布菜单" aria-label="画布菜单" aria-expanded={Boolean(menu)}><MoreHorizontal size={17} aria-hidden="true" /></button>
        <span aria-hidden="true" />
        <button type="button" onClick={() => setDialog({ type: 'workflow' })} aria-label="工作流导入导出" title="工作流"><Workflow size={15} /></button>
        <button type="button" onClick={() => setDialog({ type: 'assets' })} aria-label="资产库" title="资产库"><Library size={15} /></button>
        <button type="button" onClick={() => setDialog({ type: 'logs' })} aria-label="任务日志" title="任务日志"><ScrollText size={15} /></button>
        <button type="button" onClick={() => setDialog({ type: 'shortcuts' })} aria-label="快捷键"><Keyboard size={14} /></button>
        <button type="button" onClick={() => setTheme((value) => value === 'light' ? 'dark' : 'light')} aria-label="切换软件主题">{theme === 'light' ? <Moon size={14} /> : <Sun size={14} />}</button>
      </Panel>

      {selectedIds.length > 0 && <SelectionTools ids={selectedIds} hasGroup={selectedGroups.length > 0}
        composerId={activeComposerId} composerHeight={composerHeight}
        duplicate={duplicate} group={group} ungroup={ungroup} removeSelected={removeSelected} media={selectedNode?.data.media}
        create={() => setActiveComposerId(selectedNode.id)} preview={() => setDialog({ type: 'preview', nodeId: selectedNode.id })}
        edit={openEditor} exportMedia={() => exportNode(selectedNode)} mediaType={selectedNode?.type} frame={extractFrame}
        arrange={() => setNodes((current) => arrangeSelection(current, selectedIds))} />}

      <Panel position="bottom-center" className="pa-foundation-status" aria-live="polite">
        {saveStatus.phase === 'error' ? <AlertCircle size={13} aria-hidden="true" /> : saveStatus.phase === 'saving' ? <LoaderCircle size={13} aria-hidden="true" /> : <Check size={13} aria-hidden="true" />}
        <span>{saveStatus.phase === 'error' || saveStatus.phase === 'saving' ? saveStatus.message : message === '画布已就绪' ? saveStatus.message : message}</span>
        {ready && saveStatus.phase === 'error' && <button type="button" onClick={() => canvasAdapter.save(nodesRef.current, edgesRef.current, viewportRef.current, metadataRef.current).catch(() => {})}>重试保存</button>}
      </Panel>
    </ReactFlow>
    {ready && activeComposerId && <NodeComposer activeId={activeComposerId} nodes={nodes} edges={edges} api={paApi} onDraft={updateDraft}
      onReview={openReview} onClose={() => setActiveComposerId('')} onStatus={setMessage} onHeight={setComposerHeight} />}
    <GovernorDialog review={review} stale={reviewStale} onCancel={() => setReview(null)} onConfirm={submitReview} busy={submitting} error={reviewError} />
    {dialog && <WorkbenchDialog dialog={dialog} nodes={nodes} edges={edges} viewport={viewport} jobs={jobs} api={paApi} onClose={() => setDialog(null)}
      onRestoreJob={restoreJobResult} onBusyChange={setDialogBusy}
      metadata={metadata} onMetadata={(value) => { metadataRef.current = value; setMetadata(value); }}
      onImportWorkflow={async (imported, owner) => {
        if (canvasAdapter.get().id !== owner) throw new Error('画布已切换，资源保留在资产库，未追加到其他画布');
        const templates = [...(metadataRef.current.templates || []), ...imported.templates];
        if (templates.length > 100) throw new Error('模板总数超过 100；未追加工作流，请先导出或清理个人模板');
        const nextNodes = [...nodesRef.current.map(node => ({ ...node, selected: false })), ...await hydrateNodeMedia(imported.nodes, paApi)];
        if (canvasAdapter.get().id !== owner) throw new Error('画布已切换，未追加到其他画布');
        const nextEdges = [...edgesRef.current, ...imported.edges], nextMetadata = { ...metadataRef.current, templates };
        nodesRef.current = nextNodes; edgesRef.current = nextEdges; metadataRef.current = nextMetadata;
        setNodes(nextNodes); setEdges(nextEdges); setMetadata(nextMetadata);
        try { await canvasAdapter.save(nextNodes, nextEdges, viewportRef.current, nextMetadata); }
        catch (error) { throw new Error(`追加内容已保留在当前窗口；保存尚未完成，请重试保存：${error.message}`); }
      }}
      onNodes={setNodes} onEdited={applyEdited} canvasAdapter={canvasAdapter} onOpenCanvas={switchCanvas} onCreateCanvas={(name) => switchCanvas('', name)} onImportAsset={async (asset) => {
        const owner = canvasAdapter.get().id;
        const node = createImageNode({ name: asset.name, media: mediaIdentity(asset), src: await paApi.getAssetContentUrl(asset.id), naturalWidth: asset.width, naturalHeight: asset.height,
          position: flow.screenToFlowPosition({ x: window.innerWidth / 2, y: window.innerHeight / 2 }) });
        if (canvasAdapter.get().id !== owner) throw new Error('画布已切换，未把素材加入其他画布');
        node.type = ['video', 'audio'].includes(asset.kind) ? asset.kind : 'image';
        if (node.type === 'audio') node.style = { width: 320, height: 112 };
        setNodes((current) => [...current.map((item) => ({ ...item, selected: false })), node]);
      }} />}
    {menu && <div ref={menuRef} className="pa-context-menu nodrag nopan" role="menu" aria-label="画布操作" style={{ left: menu.x, top: menu.y }}>
      {menu.nodeId ? <>
        <button type="button" role="menuitem" onClick={() => { duplicate(); closeMenu(); }}><Copy size={15} aria-hidden="true" />复制对象<kbd>Ctrl C / V</kbd></button>
        <button type="button" role="menuitem" onClick={() => {
          const node = nodes.find((candidate) => candidate.id === menu.nodeId);
          if (node?.data.media) exportNode(node);
          closeMenu();
        }} disabled={!nodes.find((node) => node.id === menu.nodeId)?.data.src}><Download size={15} aria-hidden="true" />导出原图</button>
        <hr />
        <button type="button" role="menuitem" onClick={() => { removeSelected(); closeMenu(); }}><Trash2 size={15} aria-hidden="true" />删除对象<kbd>Delete</kbd></button>
      </> : <>
        <button type="button" role="menuitem" onClick={() => { importTargetRef.current = ''; fileInputRef.current?.click(); closeMenu(); }}><ImagePlus size={15} aria-hidden="true" />导入图片</button>
        <button type="button" role="menuitem" onClick={() => addEmptyImage(flow.screenToFlowPosition({ x: menu.x, y: menu.y }))}><Plus size={15} aria-hidden="true" />添加图片节点</button>
        <button type="button" role="menuitem" onClick={() => addPrompt(flow.screenToFlowPosition({ x: menu.x, y: menu.y }))}><Type size={15} />添加提示词节点</button>
        <button type="button" role="menuitem" disabled={!nodes.length} onClick={() => { flow.fitView({ padding: .2, duration: 240 }); closeMenu(); }}><Scan size={15} aria-hidden="true" />适合视图</button>
        <hr />
        <button type="button" role="menuitem" disabled={!nodes.length} onClick={() => { closeMenu(); clearPrototype(); }}><Trash2 size={15} aria-hidden="true" />清空原型</button>
      </>}
    </div>}
  </main>;
}
