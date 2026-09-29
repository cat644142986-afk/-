import React, { memo, useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  addEdge,
  applyEdgeChanges,
  applyNodeChanges,
  Background,
  BackgroundVariant,
  Controls,
  Handle,
  MiniMap,
  NodeResizer,
  Panel,
  Position,
  ReactFlow,
  SelectionMode,
  useReactFlow,
} from '@xyflow/react';
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

const ImageNode = memo(function ImageNode({ id, data, selected }) {
  return <div className="pa-image-node" data-kind={data.kind || 'asset'}>
    <NodeResizer
      isVisible={selected}
      keepAspectRatio
      minWidth={96}
      minHeight={96}
      lineClassName="pa-node-resizer-line"
      handleClassName="pa-node-resizer-handle"
      onResizeEnd={(_, params) => data.onResizeEnd?.(id, params)}
    />
    <img src={data.src} alt={data.name || '画布图片'} draggable="false" />
    {selected && <span className="pa-node-caption">{data.name || '图片'}</span>}
    <Handle className="pa-node-handle" type="target" position={Position.Left} id="reference-in" />
    <Handle className="pa-node-handle" type="source" position={Position.Right} id="reference-out" />
  </div>;
});

const GroupNode = memo(function GroupNode({ id, data, selected }) {
  return <div className="pa-group-node">
    <NodeResizer
      isVisible={selected}
      minWidth={180}
      minHeight={140}
      lineClassName="pa-node-resizer-line"
      handleClassName="pa-node-resizer-handle"
      onResizeEnd={(_, params) => data.onResizeEnd?.(id, params)}
    />
    <span>{data.name || '组合'}</span>
  </div>;
});

const nodeTypes = { image: ImageNode, group: GroupNode };

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
      const images = await Promise.all(accepted.map(readImageFile));
      const additions = images.map((image, index) => createImageNode({
        ...image,
        position: { x: origin.x + index * 36, y: origin.y + index * 36 },
      }));
      setNodes((current) => [
        ...current.map((node) => ({ ...node, selected: false })),
        ...decorateNodes(additions),
      ]);
      setMessage(`已导入 ${additions.length} 张图片 · 原始比例保持不变`);
    } catch (error) {
      setMessage(error?.message || String(error));
    }
  }, [decorateNodes]);

  const onDrop = useCallback((event) => {
    event.preventDefault();
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

  useEffect(() => {
    const onKeyDown = (event) => {
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
      if ((event.key === 'Delete' || event.key === 'Backspace') && selectedIds.length) {
        event.preventDefault();
        removeSelected();
        return;
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
  }, [clipboard, duplicate, flow, group, importImages, nodes.length, redo, removeSelected, selectedIds.length, undo, ungroup]);

  const onNodesChange = useCallback((changes) => {
    setNodes((current) => decorateNodes(applyNodeChanges(changes, current)));
  }, [decorateNodes]);
  const onEdgesChange = useCallback((changes) => setEdges((current) => applyEdgeChanges(changes, current)), []);
  const onConnect = useCallback((connection) => {
    setEdges((current) => addEdge({ ...connection, type: 'smoothstep' }, current));
    setMessage('已建立参考关系');
  }, []);

  const clearPrototype = useCallback(() => {
    if (!window.confirm('清空这个隔离原型中的全部测试对象？')) return;
    setNodes([]);
    setEdges([]);
    setMessage('原型已清空');
  }, []);

  const flowNodes = useMemo(() => decorateNodes(nodes), [decorateNodes, nodes]);
  const canUndo = history.current.past.length > 0;
  const canRedo = history.current.future.length > 0;
  void historyVersion;

  return <main className="pa-foundation-shell">
    <ReactFlow
      nodes={flowNodes}
      edges={edges}
      nodeTypes={nodeTypes}
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
      onMoveEnd={(_, nextViewport) => setViewport(nextViewport)}
      onDrop={onDrop}
      onDragOver={(event) => { event.preventDefault(); event.dataTransfer.dropEffect = 'copy'; }}
      onPaneClick={() => setMessage('画布已就绪')}
      fitView={false}
      colorMode="light"
      proOptions={{ hideAttribution: false }}
      ariaLabelConfig={{ 'node.a11yDescription.default': '按 Enter 选择对象，方向键移动，Delete 删除。' }}
    >
      <Background variant={BackgroundVariant.Dots} gap={24} size={1.2} />
      <MiniMap pannable zoomable ariaLabel="画布小地图" nodeColor={(node) => node.type === 'group' ? '#d9d5cc' : '#85827c'} />
      <Controls position="bottom-left" showInteractive={false} />

      <Panel position="top-left" className="pa-foundation-title">
        <span>PRODUCT ATELIER</span>
        <strong>Canvas Foundation</strong>
        <small>并行原型 · 不读取正式数据</small>
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
        <button type="button" className="is-primary" onClick={() => fileInputRef.current?.click()}>导入图片</button>
        <button type="button" onClick={() => flow.fitView({ padding: 0.18, duration: 240 })} disabled={!nodes.length}>适合视图</button>
        <span aria-hidden="true" />
        <button type="button" onClick={undo} disabled={!canUndo} aria-label="撤销">↶</button>
        <button type="button" onClick={redo} disabled={!canRedo} aria-label="重做">↷</button>
        <button type="button" className="is-quiet" onClick={clearPrototype} disabled={!nodes.length}>清空</button>
      </Panel>

      {selectedIds.length > 0 && <Panel position="top-center" className="pa-selection-toolbar-host">
        <div className="pa-selection-toolbar nodrag nopan nokey">
          <strong>{selectedIds.length > 1 ? `${selectedIds.length} 个对象` : nodes.find((node) => node.id === selectedIds[0])?.data?.name || '对象'}</strong>
          <button type="button" onClick={duplicate}>复制</button>
          {selectedIds.length > 1 && !selectedGroups.length && <button
            type="button"
            onClick={group}
          >组合</button>}
          {selectedGroups.length > 0 && <button
            type="button"
            onClick={ungroup}
          >取消组合</button>}
          <button
            type="button"
            className="is-danger"
            onClick={removeSelected}
          >删除</button>
        </div>
      </Panel>}

      <Panel position="bottom-center" className="pa-foundation-status" aria-live="polite">
        <span>{message}</span>
        <small>{nodes.length} 对象 · {edges.length} 关系 · {Math.round(viewport.zoom * 100)}%</small>
      </Panel>
    </ReactFlow>
  </main>;
}
