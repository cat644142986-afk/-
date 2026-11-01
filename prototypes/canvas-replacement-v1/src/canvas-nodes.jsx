import React, { memo, useEffect, useState } from 'react';
import { Handle, NodeResizeControl, Position } from '@xyflow/react';
import { Image, ImagePlus, MoveUpRight, Trash2, Type, Video, Music, AlertCircle, LoaderCircle } from 'lucide-react';
import { stopComposerKeyboardEvent } from '../../../src/js/canvas-transplant-geometry.js';

function DeleteControl({ id, data }) {
  return <button type="button" className="pa-node-delete nodrag nopan" title="删除对象 · 可撤销" aria-label={`删除${data.name || '对象'}`}
    onPointerDown={(event) => event.stopPropagation()} onClick={(event) => { event.preventDefault(); event.stopPropagation(); data.onDelete?.(id); }}><Trash2 size={14} aria-hidden="true" /></button>;
}

function Ports() {
  return <><Handle className="pa-node-port pa-port-in" type="target" position={Position.Left} id="reference-in" title="输入" aria-label="输入端口" />
    <Handle className="pa-node-port pa-port-out" type="source" position={Position.Right} id="reference-out" title="连接为输入" aria-label="输出端口" /></>;
}

function ExecutionStatus({ id, data }) {
  const task = data.execution;
  const [now, setNow] = useState(Date.now());
  useEffect(() => { if (!task || !['queued', 'running', 'pending', 'paused'].includes(task.status)) return undefined; const timer = setInterval(() => setNow(Date.now()), 1000); return () => clearInterval(timer); }, [task?.status]);
  if (!task || ['completed', 'canceled'].includes(task.status)) return null;
  const active = ['queued', 'running', 'pending', 'paused'].includes(task.status);
  const elapsed = task.started_at ? Math.max(0, Math.floor((now - Date.parse(task.started_at)) / 1000)) : null;
  return <div className="pa-node-execution nodrag nopan" role="status">
    {active ? <LoaderCircle size={13} /> : <AlertCircle size={13} />}<span>{({ running: '执行中', queued: '排队中', paused: '已暂停', failed: '执行失败', interrupted: '执行中断', partial: '部分完成' })[task.status] || task.status}</span>
    {active && <button type="button" onClick={() => data.onCancelTask?.(id)}>取消</button>}
    {active && Number.isFinite(elapsed) && <small>{Math.floor(elapsed / 60)}:{String(elapsed % 60).padStart(2, '0')}</small>}
    {task.error && <details><summary>详情</summary>{String(task.error)}</details>}
  </div>;
}

export const ImageNode = memo(function ImageNode({ id, data, selected, dragging }) {
  const hasImage = Boolean(data.src);
  return <div className={`pa-image-node ${hasImage ? 'has-image' : 'is-empty'} ${selected ? 'is-selected' : ''} ${dragging ? 'is-moving' : ''}`}>
    <div className="pa-media-meta" title={data.name}>
      <Image size={13} aria-hidden="true" />
      <span>{data.name || '图片'}</span>
      {hasImage && <small>{data.naturalWidth} × {data.naturalHeight}</small>}
    </div>
    <DeleteControl id={id} data={data} />
    <ExecutionStatus id={id} data={data} />
    {data.mediaError ? <div className="pa-media-missing"><AlertCircle size={20} /><span>来源不可用</span><small>{data.mediaError}</small><button type="button" className="nodrag nopan" onClick={() => data.onRecoverMedia?.(id)}>重新读取</button></div> : hasImage
      ? <img src={data.src} alt={data.name || '画布图片'} draggable="false" />
      : <button type="button" className="pa-empty-image nodrag nopan" onClick={() => data.onImport?.(id)}>
        <ImagePlus size={25} strokeWidth={1.6} aria-hidden="true" />
        <span>导入图片</span>
        <small>或将图片拖入画布</small>
      </button>}
    {hasImage && <NodeResizeControl
      className="pa-image-resize"
      position="bottom-right"
      keepAspectRatio minWidth={48} minHeight={32}
      onResizeStart={() => data.onTransformStart?.()}
      onResizeEnd={(_, params) => data.onResizeEnd?.(id, params)}
    ><MoveUpRight size={12} aria-hidden="true" /></NodeResizeControl>}
    <Ports />
  </div>;
});

export const GroupNode = memo(function GroupNode({ id, data, selected, dragging }) {
  return <div className={`pa-group-node ${selected ? 'is-selected' : ''} ${dragging ? 'is-moving' : ''}`}>
    <span>{data.name || '组合'}</span>
    <DeleteControl id={id} data={data} />
    <NodeResizeControl className="pa-image-resize" position="bottom-right" minWidth={180} minHeight={140}
      onResizeStart={() => data.onTransformStart?.()}
      onResizeEnd={(_, params) => data.onResizeEnd?.(id, params)}
    ><MoveUpRight size={12} aria-hidden="true" /></NodeResizeControl>
  </div>;
});

export const PromptNode = memo(function PromptNode({ id, data, selected, dragging }) {
  return <div className={`pa-prompt-node ${selected ? 'is-selected' : ''} ${dragging ? 'is-moving' : ''}`}>
    <header><Type size={14} aria-hidden="true" /><span>{data.name || '提示词'}</span></header>
    <DeleteControl id={id} data={data} />
    <textarea className="nodrag nopan nowheel" aria-label="提示词节点内容" placeholder="输入提示词…" value={data.text || ''}
      onPointerDown={(event) => event.stopPropagation()} onKeyDown={stopComposerKeyboardEvent}
      onChange={(event) => data.onText?.(id, event.target.value)} />
    <footer><button type="button" className="nodrag nopan" onClick={() => data.onTemplates?.(id)}>模板</button>
      <button type="button" className="nodrag nopan" onClick={() => data.onSplitPrompt?.(id)}>分段预览</button></footer>
    <NodeResizeControl className="pa-image-resize" position="bottom-right" minWidth={230} minHeight={170}
      onResizeStart={() => data.onTransformStart?.()}
      onResizeEnd={(_, params) => data.onResizeEnd?.(id, params)}><MoveUpRight size={12} aria-hidden="true" /></NodeResizeControl>
    <Ports />
  </div>;
});

export const VideoNode = memo(function VideoNode({ id, data, selected, dragging }) {
  return <div className={`pa-image-node pa-video-node ${selected ? 'is-selected' : ''} ${dragging ? 'is-moving' : ''}`}>
    <div className="pa-media-meta"><Video size={13} /><span>{data.name || '视频'}</span><small>{data.media?.duration_seconds?.toFixed(1)}s</small></div>
    <DeleteControl id={id} data={data} />
    <ExecutionStatus id={id} data={data} />
    {data.src ? <video className="nodrag nopan nowheel" src={data.src} controls preload="metadata" playsInline onTimeUpdate={(event) => data.onMediaTime?.(id, event.currentTarget.currentTime)} onPointerDown={(event) => { if (event.button === 0) data.onSelectMedia?.(id, event); event.stopPropagation(); }} onKeyDown={stopComposerKeyboardEvent} /> : <button type="button" className="pa-empty-image nodrag nopan" onClick={() => data.onImport?.(id)}><Video size={25} /><span>导入视频</span></button>}
    {data.src && <NodeResizeControl className="pa-image-resize" position="bottom-right" keepAspectRatio minWidth={100} minHeight={80}
      onResizeStart={() => data.onTransformStart?.()} onResizeEnd={(_, params) => data.onResizeEnd?.(id, params)}><MoveUpRight size={12} aria-hidden="true" /></NodeResizeControl>}
    <Ports />
  </div>;
});

export const AudioNode = memo(function AudioNode({ id, data, selected, dragging }) {
  return <div className={`pa-prompt-node ${selected ? 'is-selected' : ''} ${dragging ? 'is-moving' : ''}`}>
    <header><Music size={14} /><span>{data.name || '音频'}</span></header><DeleteControl id={id} data={data} />
    {data.src && <audio className="nodrag nopan" src={data.src} controls preload="metadata" onPointerDown={(event) => { if (event.button === 0) data.onSelectMedia?.(id, event); event.stopPropagation(); }} onKeyDown={stopComposerKeyboardEvent} />}
    <Ports />
  </div>;
});

export const nodeTypes = { image: ImageNode, group: GroupNode, prompt: PromptNode, video: VideoNode, audio: AudioNode };
