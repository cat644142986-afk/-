import React, { memo } from 'react';
import { Handle, NodeResizeControl, Position } from '@xyflow/react';
import { Image, ImagePlus, MoveUpRight } from 'lucide-react';

export const ImageNode = memo(function ImageNode({ id, data, selected, dragging }) {
  const hasImage = Boolean(data.src);
  return <div className={`pa-image-node ${hasImage ? 'has-image' : 'is-empty'} ${selected ? 'is-selected' : ''} ${dragging ? 'is-moving' : ''}`}>
    <div className="pa-media-meta" title={data.name}>
      <Image size={13} aria-hidden="true" />
      <span>{data.name || '图片'}</span>
      {hasImage && <small>{data.naturalWidth} × {data.naturalHeight}</small>}
    </div>
    {hasImage
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
      onResizeEnd={(_, params) => data.onResizeEnd?.(id, params)}
    ><MoveUpRight size={12} aria-hidden="true" /></NodeResizeControl>}
    <Handle className="pa-node-port pa-port-in" type="target" position={Position.Left} id="reference-in" title="参考输入" aria-label="参考输入端口" />
    <Handle className="pa-node-port pa-port-out" type="source" position={Position.Right} id="reference-out" title="连接为参考" aria-label="参考输出端口" />
  </div>;
});

export const GroupNode = memo(function GroupNode({ id, data, selected }) {
  return <div className={`pa-group-node ${selected ? 'is-selected' : ''}`}>
    <span>{data.name || '组合'}</span>
    <NodeResizeControl className="pa-image-resize" position="bottom-right" minWidth={180} minHeight={140}
      onResizeEnd={(_, params) => data.onResizeEnd?.(id, params)}
    ><MoveUpRight size={12} aria-hidden="true" /></NodeResizeControl>
  </div>;
});

export const nodeTypes = { image: ImageNode, group: GroupNode };
