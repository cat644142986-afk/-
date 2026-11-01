import assert from 'node:assert/strict';
import test from 'node:test';
import { selectMediaNode } from '../src/canvas-model.js';

test('player pointer selection stays in the existing nodes state and supports additive selection', () => {
  const nodes = [{ id: 'image', selected: true }, { id: 'video', selected: false }, { id: 'audio', selected: false }];
  const selected = selectMediaNode(nodes, 'video');
  assert.deepEqual(selected.map(node => node.selected), [false, true, false]);
  const additive = selectMediaNode(nodes, 'video', { shiftKey: true });
  assert.deepEqual(additive.map(node => node.selected), [true, true, false]);
  assert.equal(selectMediaNode(nodes, 'missing'), nodes);
  assert.equal(nodes[0].selected, true); assert.equal(nodes[1].selected, false);
});
import {
  createImageNode,
  deleteSelection,
  duplicateSelection,
  fittedImageSize,
  groupSelection,
  restorePrototype,
  serializePrototype,
  ungroupSelection,
  worldPosition,
} from '../src/canvas-model.js';

test('image nodes preserve the source aspect ratio without card/crop metadata', () => {
  const size = fittedImageSize(1600, 900);
  assert.equal(size.aspectRatio, 1600 / 900);
  assert.ok(Math.abs((size.width / size.height) - (16 / 9)) < Number.EPSILON * 2);
  const node = createImageNode({ src: 'data:image/png;base64,abc', naturalWidth: 1600, naturalHeight: 900 });
  assert.equal(node.data.aspectRatio, 16 / 9);
  assert.ok(Math.abs((node.style.width / node.style.height) - (16 / 9)) < Number.EPSILON * 2);
  assert.equal(node.data.crop, undefined);
  assert.equal(node.data.radius, undefined);
});

test('duplicating a connected selection keeps its internal reference edge and offsets roots', () => {
  const a = createImageNode({ id: 'a', src: 'a', naturalWidth: 100, naturalHeight: 100, position: { x: 10, y: 20 } });
  const b = createImageNode({ id: 'b', src: 'b', naturalWidth: 100, naturalHeight: 100, position: { x: 210, y: 20 } });
  const result = duplicateSelection([a, b], [{ id: 'ab', source: 'a', target: 'b' }]);
  assert.equal(result.nodes.length, 4);
  assert.equal(result.edges.length, 2);
  assert.equal(result.selectedIds.length, 2);
  const copies = result.nodes.filter((node) => result.selectedIds.includes(node.id));
  assert.deepEqual(copies.map((node) => node.position), [{ x: 46, y: 56 }, { x: 246, y: 56 }]);
  assert.ok(result.edges.some((edge) => result.selectedIds.includes(edge.source) && result.selectedIds.includes(edge.target)));
});

test('group and ungroup preserve absolute child positions', () => {
  const a = createImageNode({ id: 'a', src: 'a', naturalWidth: 100, naturalHeight: 100, position: { x: 120, y: 180 } });
  const b = createImageNode({ id: 'b', src: 'b', naturalWidth: 200, naturalHeight: 100, position: { x: 300, y: 260 } });
  const grouped = groupSelection([a, b]);
  assert.ok(grouped.groupId);
  const group = grouped.nodes.find((node) => node.id === grouped.groupId);
  const children = grouped.nodes.filter((node) => node.parentId === grouped.groupId);
  assert.equal(children.length, 2);
  const absoluteBefore = children.map((node) => ({
    id: node.id,
    x: group.position.x + node.position.x,
    y: group.position.y + node.position.y,
  }));
  const ungrouped = ungroupSelection(grouped.nodes);
  const absoluteAfter = ungrouped.nodes.filter((node) => ['a', 'b'].includes(node.id)).map((node) => ({
    id: node.id, x: node.position.x, y: node.position.y,
  }));
  assert.deepEqual(absoluteAfter, absoluteBefore);
});

test('delete removes selected nodes, their children and connected edges', () => {
  const group = { id: 'g', type: 'group', position: { x: 0, y: 0 }, selected: true, data: {}, style: { width: 300, height: 200 } };
  const child = { ...createImageNode({ id: 'a', src: 'a', naturalWidth: 100, naturalHeight: 100 }), parentId: 'g', selected: false };
  const other = { ...createImageNode({ id: 'b', src: 'b', naturalWidth: 100, naturalHeight: 100 }), selected: false };
  const result = deleteSelection([group, child, other], [{ id: 'edge', source: 'a', target: 'b' }]);
  assert.deepEqual(result.nodes.map((node) => node.id), ['b']);
  assert.equal(result.edges.length, 0);
});

test('prototype persistence strips transient selection and function callbacks but restores viewport', () => {
  const node = createImageNode({ id: 'a', src: 'a', naturalWidth: 300, naturalHeight: 200 });
  node.data.onResizeEnd = () => {};
  const serialized = serializePrototype([node], [], { x: 42, y: -17, zoom: 1.25 });
  assert.equal(serialized.nodes[0].selected, undefined);
  assert.equal(serialized.nodes[0].data.onResizeEnd, undefined);
  const restored = restorePrototype(serialized);
  assert.deepEqual(restored.viewport, { x: 42, y: -17, zoom: 1.25 });
  assert.equal(restored.nodes[0].selected, false);
});

test('a child copied alone keeps world position and never inherits a submitted Task', () => {
  const group = { id: 'g', type: 'group', selected: false, position: { x: 100, y: 200 }, data: {} };
  const child = { ...createImageNode({ id: 'child', src: 'original', naturalWidth: 100, naturalHeight: 100, position: { x: 10, y: 20 } }), parentId: 'g' };
  child.data.execution = { task_id: 'job_existing' };
  const result = duplicateSelection([group, child], []);
  const copy = result.nodes.at(-1);
  assert.equal(copy.parentId, undefined);
  assert.deepEqual(copy.position, { x: 146, y: 256 });
  assert.deepEqual(worldPosition(child, [group, child]), { x: 110, y: 220 });
  assert.equal(copy.data.execution, undefined);
});
