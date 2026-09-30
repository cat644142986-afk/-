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
import { Copy, Group, Ungroup, Trash2, ImagePlus, Scan, Undo2, Redo2, MoreHorizontal, Plus, Download, Check, MousePointer2 } from 'lucide-react';
import {
  createImageNode,
  deleteSelection,
  duplicateSelection,
  groupSelection,
  restorePrototype,
  serializePrototype,
  ungroupSelection,
} from './canvas-model.js';
import { imageFile, readImageFile } from './image-io.js';
import { nodeTypes } from './canvas-nodes.jsx';
import { edgeTypes } from './reference-edge.jsx';
import { createEmptyImageNode, referenceConnectionAllowed, referenceEdge } from './reference-model.js';

const STORAGE_KEY = 'product-atelier:canvas-replacement:v1';

function editableTarget(target) {
  const tag = String(target?.tagName || '').toLowerCase();
  return target?.isContentEditable || ['input', 'textarea', 'select'].includes(tag);
}

function initialDocument() {
  try {
    return restorePrototype(JSON.parse(localStorage.getItem(STORAGE_KEY) || 'null')) || {
      nodes: [], edges: [], viewport: { x: 0, y: 0, zoom: 1 },
    };
  } catch (_) {
    return { nodes: [], edges: [], viewport: { x: 0, y: 0, zoom: 1 } };
  }
}

function SelectionTools({ ids, hasGroup, duplicate, group, ungroup, removeSelected }) {
  const flow = useReactFlow();
  const view = useViewport();
  const bounds = flow.getNodesBounds(ids);
  const top = bounds.y * view.zoom + view.y;
  const center = (bounds.x + bounds.width / 2) * view.zoom + view.x;
  const align = center < 175 ? 'start' : center > window.innerWidth - 175 ? 'end' : 'center';
  return <NodeToolbar nodeId={ids} isVisible position={top > 126 ? Position.Top : Position.Bottom} offset={36} align={align}
    className="pa-selection-toolbar nodrag nopan" role="toolbar" aria-label="选区操作">
    {ids.length > 1 && <span className="pa-selection-count">{ids.length} 个对象</span>}
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
  const [message, setMessage] = useState('画布已就绪');
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
  const applyingHistory = useRef(false);
  const history = useRef({
    past: [],
    present: historySnapshot(initial.nodes, initial.edges),
    future: [],
  });
  const flow = useReactFlow();
  const selectedIds = nodes.filter((node) => node.selected).map((node) => node.id);
  const selectedGroups = nodes.filter((node) => node.selected && node.type === 'group');

  const decorateNodes = useCallback((items) => items.map((node) => ({
    ...node,
    data: {
      ...node.data,
      onImport: (id) => {
        importTargetRef.current = id;
        fileInputRef.current?.click();
      },
      onResizeEnd: (id, params) => {
        setNodes((current) => current.map((candidate) => candidate.id === id ? {
          ...candidate,
          style: { ...candidate.style, width: params.width, height: params.height },
        } : candidate));
      },
    },
  })), []);

  useEffect(() => { nodesRef.current = nodes; }, [nodes]);
  useEffect(() => { edgesRef.current = edges; }, [edges]);
  useEffect(() => { viewportRef.current = viewport; }, [viewport]);

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
    persistenceTimer.current = window.setTimeout(() => {
      const document = serializePrototype(nodesRef.current, edgesRef.current, viewportRef.current);
      try {
        localStorage.setItem(STORAGE_KEY, JSON.stringify(document));
        setMessage(`已保存 · ${document.nodes.length} 个对象`);
      } catch (error) {
        setMessage(`保存失败：${error?.message || error}`);
      }
    }, 260);
    return () => clearTimeout(persistenceTimer.current);
  }, [nodes, edges, viewport]);

  const replaceDocumentFromHistory = useCallback((serialized) => {
    const restored = restorePrototype(JSON.parse(serialized));
    if (!restored) return;
    applyingHistory.current = true;
    setNodes(restored.nodes);
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
    const accepted = Array.from(files || []).filter(imageFile);
    if (!accepted.length) {
      setMessage('请选择 PNG、JPEG、WebP 或 GIF 图片');
      return;
    }
    try {
      const targetId = importTargetRef.current;
      importTargetRef.current = '';
      const images = await Promise.all(accepted.map(readImageFile));
      const additions = images.map((image, index) => createImageNode({
        ...image,
        position: { x: origin.x + index * 36, y: origin.y + index * 36 },
      }));
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
      setMessage(`已导入 ${additions.length} 张图片 · 原始比例保持不变`);
    } catch (error) {
      setMessage(error?.message || String(error));
    }
  }, [decorateNodes]);

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

  useEffect(() => {
    const onKeyDown = (event) => {
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
        setClipboard({ copiedAt: Date.now() });
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
        duplicate();
      }
    };
    window.addEventListener('keydown', onKeyDown, true);
    window.addEventListener('paste', onPaste, true);
    return () => {
      window.removeEventListener('keydown', onKeyDown, true);
      window.removeEventListener('paste', onPaste, true);
    };
  }, [clipboard, closeMenu, duplicate, edges, flow, group, importImages, menu, nodes.length, redo, removeSelected, selectedIds.length, undo, ungroup]);

  const onNodesChange = useCallback((changes) => {
    setNodes((current) => decorateNodes(applyNodeChanges(changes, current)));
  }, [decorateNodes]);
  const onEdgesChange = useCallback((changes) => setEdges((current) => applyEdgeChanges(changes, current)), []);
  const onConnect = useCallback((connection) => {
    setEdges((current) => referenceConnectionAllowed(connection, nodesRef.current, current) ? addEdge(referenceEdge(connection), current) : current);
    setMessage('已建立参考关系');
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
    ...edge, type: 'reference', data: { onRemove: (id) => setEdges((current) => current.filter((candidate) => candidate.id !== id)) },
  })), [edges]);
  const canUndo = history.current.past.length > 0;
  const canRedo = history.current.future.length > 0;
  void historyVersion;

  return <main className="pa-foundation-shell">
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
      onEdgesChange={onEdgesChange}
      onConnect={onConnect}
      onConnectEnd={onConnectEnd}
      onReconnect={onReconnect}
      onReconnectStart={(_, edge) => { reconnectingEdgeRef.current = edge.id; }}
      onReconnectEnd={() => { reconnectingEdgeRef.current = ''; }}
      onNodeContextMenu={(event, node) => openMenu(event, node.id)}
      onPaneContextMenu={(event) => openMenu(event)}
      onMoveEnd={(_, nextViewport) => setViewport(nextViewport)}
      onDrop={onDrop}
      onDragOver={(event) => { event.preventDefault(); event.dataTransfer.dropEffect = 'copy'; }}
      onPaneClick={() => { closeMenu(); setMessage('画布已就绪'); }}
      fitView={false}
      colorMode="light"
      proOptions={{ hideAttribution: false }}
      ariaLabelConfig={{ 'node.a11yDescription.default': '按 Enter 选择对象，方向键移动，Delete 删除。' }}
    >
      <Background variant={BackgroundVariant.Dots} gap={24} size={1} />
      <MiniMap pannable zoomable ariaLabel="画布小地图" nodeColor={(node) => node.type === 'group' ? '#e2e8f0' : '#a4adb9'} maskColor="rgba(244,246,249,.65)" />
      <Controls position="bottom-left" showInteractive={false} />

      <Panel position="top-left" className="pa-foundation-title">
        <MousePointer2 size={18} aria-hidden="true" />
        <strong>未命名画布</strong>
        <small>节点交互原型</small>
      </Panel>

      <Panel position="top-right" className="pa-foundation-actions">
        <input
          ref={fileInputRef}
          type="file"
          accept="image/png,image/jpeg,image/webp,image/gif"
          multiple
          hidden
          onChange={(event) => {
            const origin = flow.screenToFlowPosition({ x: window.innerWidth * 0.58, y: window.innerHeight * 0.38 });
            importImages(event.target.files, origin);
            event.target.value = '';
          }}
        />
        <button type="button" className="is-primary" aria-label="导入图片" onClick={() => { importTargetRef.current = ''; fileInputRef.current?.click(); }}><ImagePlus size={16} aria-hidden="true" /><span>导入图片</span></button>
        <button type="button" onClick={() => addEmptyImage()} title="添加图片节点" aria-label="添加图片节点"><Plus size={17} aria-hidden="true" /></button>
        <button type="button" onClick={() => flow.fitView({ padding: 0.2, duration: 240 })} disabled={!nodes.length} title="适合视图" aria-label="适合视图"><Scan size={16} aria-hidden="true" /></button>
        <span aria-hidden="true" />
        <button type="button" onClick={undo} disabled={!canUndo} title="撤销 · Ctrl+Z" aria-label="撤销"><Undo2 size={16} aria-hidden="true" /></button>
        <button type="button" onClick={redo} disabled={!canRedo} title="重做 · Ctrl+Shift+Z" aria-label="重做"><Redo2 size={16} aria-hidden="true" /></button>
        <button type="button" onClick={openMenu} title="画布菜单" aria-label="画布菜单" aria-expanded={Boolean(menu)}><MoreHorizontal size={17} aria-hidden="true" /></button>
      </Panel>

      {selectedIds.length > 0 && <SelectionTools ids={selectedIds} hasGroup={selectedGroups.length > 0}
        duplicate={duplicate} group={group} ungroup={ungroup} removeSelected={removeSelected} />}

      <Panel position="bottom-center" className="pa-foundation-status" aria-live="polite">
        <Check size={13} aria-hidden="true" /><span>{message}</span>
      </Panel>
    </ReactFlow>
    {menu && <div ref={menuRef} className="pa-context-menu nodrag nopan" role="menu" aria-label="画布操作" style={{ left: menu.x, top: menu.y }}>
      {menu.nodeId ? <>
        <button type="button" role="menuitem" onClick={() => { duplicate(); closeMenu(); }}><Copy size={15} aria-hidden="true" />复制对象<kbd>Ctrl C / V</kbd></button>
        <button type="button" role="menuitem" onClick={() => {
          const node = nodes.find((candidate) => candidate.id === menu.nodeId);
          if (node?.data.src) { const link = document.createElement('a'); link.href = node.data.src; link.download = node.data.name || 'image.png'; link.click(); }
          closeMenu();
        }} disabled={!nodes.find((node) => node.id === menu.nodeId)?.data.src}><Download size={15} aria-hidden="true" />导出原图</button>
        <hr />
        <button type="button" role="menuitem" onClick={() => { removeSelected(); closeMenu(); }}><Trash2 size={15} aria-hidden="true" />删除对象<kbd>Delete</kbd></button>
      </> : <>
        <button type="button" role="menuitem" onClick={() => { importTargetRef.current = ''; fileInputRef.current?.click(); closeMenu(); }}><ImagePlus size={15} aria-hidden="true" />导入图片</button>
        <button type="button" role="menuitem" onClick={() => addEmptyImage(flow.screenToFlowPosition({ x: menu.x, y: menu.y }))}><Plus size={15} aria-hidden="true" />添加图片节点</button>
        <button type="button" role="menuitem" disabled={!nodes.length} onClick={() => { flow.fitView({ padding: .2, duration: 240 }); closeMenu(); }}><Scan size={15} aria-hidden="true" />适合视图</button>
        <hr />
        <button type="button" role="menuitem" disabled={!nodes.length} onClick={() => { closeMenu(); clearPrototype(); }}><Trash2 size={15} aria-hidden="true" />清空原型</button>
      </>}
    </div>}
  </main>;
}
