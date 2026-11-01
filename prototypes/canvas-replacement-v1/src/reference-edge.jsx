import React, { memo } from 'react';
import { BaseEdge, EdgeLabelRenderer, getBezierPath } from '@xyflow/react';
import { X } from 'lucide-react';

export const ReferenceEdge = memo(function ReferenceEdge({ id, data, selected, ...geometry }) {
  const [path, labelX, labelY] = getBezierPath(geometry);
  return <>
    <BaseEdge id={id} path={path} interactionWidth={22} className={`pa-reference-edge ${selected ? 'is-selected' : ''}`} />
    {selected && <EdgeLabelRenderer>
      <div className="pa-edge-tools nodrag nopan" style={{ transform: `translate(-50%, -50%) translate(${labelX}px, ${labelY}px)` }}>
        <span>{data?.kind === 'flow' ? '结果' : '输入'}</span>
        <button type="button" aria-label="断开参考连接" title="断开参考连接" onClick={() => data?.onRemove?.(id)}><X size={13} aria-hidden="true" /></button>
      </div>
    </EdgeLabelRenderer>}
  </>;
});

export const edgeTypes = { reference: ReferenceEdge };
