import assert from 'node:assert/strict';
import test from 'node:test';
import { createEmptyImageNode, referenceConnectionAllowed, referenceEdge } from '../src/reference-model.js';
import { restorePrototype, serializePrototype } from '../src/canvas-model.js';

test('reference links reject self, missing/group targets, duplicate inputs and cycles', () => {
  const nodes = ['a', 'b', 'c'].map((id) => ({ id, type: 'image' }));
  nodes.push({ id: 'g', type: 'group' });
  const edges = [{ id: 'ab', source: 'a', target: 'b' }, { id: 'bc', source: 'b', target: 'c' }];
  for (const target of ['a', 'missing', 'g', 'b']) assert.equal(referenceConnectionAllowed({ source: 'a', target }, nodes, edges), false);
  assert.equal(referenceConnectionAllowed({ source: 'c', target: 'a' }, nodes, edges), false);
  assert.equal(referenceConnectionAllowed({ source: 'a', target: 'c' }, nodes, edges), true);
  assert.equal(referenceConnectionAllowed({ source: 'a', target: 'b' }, nodes, edges, 'ab'), true);
});

test('port-to-space creates an empty node and an explicit durable reference without execution', () => {
  const a = { ...createEmptyImageNode({ x: 10, y: 20 }), id: 'a' };
  const b = { ...createEmptyImageNode({ x: 400, y: 20 }), id: 'b' };
  const edge = referenceEdge({ source: a.id, target: b.id });
  assert.equal(edge.sourceHandle, 'reference-out');
  assert.equal(edge.targetHandle, 'reference-in');
  const restored = restorePrototype(serializePrototype([a, b], [edge], { x: 0, y: 0, zoom: 1 }));
  assert.equal(restored.edges[0].type, 'reference');
  assert.equal(restored.edges[0].target, 'b');
  assert.deepEqual(restored.nodes[1].position, { x: 400, y: 20 });
  assert.equal(restored.nodes[1].data.task_id, undefined);
  assert.equal(restored.nodes[1].data.src, undefined);
});
