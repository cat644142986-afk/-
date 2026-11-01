import { nextPrototypeId } from './canvas-model.js';

export function createEmptyImageNode(position) {
  return {
    id: nextPrototypeId('image'), type: 'image', selected: true,
    position: { ...position }, style: { width: 316, height: 210 },
    data: { name: '图片节点', kind: 'asset' },
  };
}

// Connections express user-chosen inputs. They must never imply a paid run.
export function referenceConnectionAllowed(connection, nodes, edges, ignoredEdgeId = '') {
  const { source, target } = connection;
  if (!source || !target || source === target) return false;
  if (![source, target].every((id) => nodes.some((node) => node.id === id && ['image', 'video', 'audio', 'prompt', 'loop', 'clip'].includes(node.type)))) return false;
  const remaining = edges.filter((edge) => edge.id !== ignoredEdgeId);
  if (remaining.some((edge) => edge.source === source && edge.target === target)) return false;
  const visited = new Set();
  const pending = [target];
  while (pending.length) {
    const id = pending.pop();
    if (id === source) return false;
    if (visited.has(id)) continue;
    visited.add(id);
    remaining.filter((edge) => edge.source === id).forEach((edge) => pending.push(edge.target));
  }
  return true;
}

export function referenceEdge(connection) {
  return {
    ...connection,
    id: connection.id || nextPrototypeId('reference'),
    sourceHandle: 'reference-out', targetHandle: 'reference-in',
    type: 'reference',
  };
}
