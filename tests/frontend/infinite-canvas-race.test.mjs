import assert from 'node:assert/strict';
import test from 'node:test';

import {
  createInfiniteCanvasWorkspaceController,
  spatialClipboardImageFiles,
} from '../../src/js/infinite-canvas-workspace.js';

const SOURCE_ASSET_ID = 'ast_0123456789abcdef0123456789abcdef';
const RESULT_ASSET_ID = 'ast_11111111111111111111111111111111';
const VARIATION_ASSET_ID = 'ast_22222222222222222222222222222222';

function deferred() {
  let resolve;
  let reject;
  const promise = new Promise((resolvePromise, rejectPromise) => {
    resolve = resolvePromise;
    reject = rejectPromise;
  });
  return { promise, reject, resolve };
}

async function settle(rounds = 12) {
  for (let index = 0; index < rounds; index += 1) await Promise.resolve();
}

async function waitFor(predicate, message = 'condition was not reached') {
  for (let index = 0; index < 40; index += 1) {
    if (predicate()) return;
    await settle(2);
  }
  assert.fail(message);
}

test('clipboard file extraction preserves native Excalidraw element paste', () => {
  const image = { name: 'clipboard.png', type: 'image/png' };
  assert.deepEqual(spatialClipboardImageFiles({ files: [image], types: ['Files'] }), [image]);
  assert.deepEqual(spatialClipboardImageFiles({
    files: [image],
    types: ['application/vnd.excalidraw+json', 'image/png'],
  }), []);
});

class FakeElement {
  constructor(selector = '') {
    this.selector = selector;
    this.hidden = false;
    this.disabled = false;
    this.checked = false;
    this.value = '';
    this.innerHTML = '';
    this.textContent = '';
    this.dataset = {};
    this.listeners = new Map();
    this.children = new Map();
    this.focusCalls = 0;
    this.clickCalls = 0;
    this.attributes = new Map();
  }

  addEventListener(type, listener) {
    const listeners = this.listeners.get(type) || [];
    listeners.push(listener);
    this.listeners.set(type, listeners);
  }

  removeEventListener(type, listener) {
    this.listeners.set(type, (this.listeners.get(type) || []).filter((item) => item !== listener));
  }

  async emit(type, event = {}) {
    const value = {
      currentTarget: this,
      defaultPrevented: false,
      preventDefault() { this.defaultPrevented = true; },
      stopPropagation() {},
      target: this,
      ...event,
    };
    for (const listener of this.listeners.get(type) || []) await listener(value);
    return value;
  }

  closest(selector) {
    if (selector === '[data-spatial-recovery]' && this.dataset.spatialRecovery) return this;
    if (selector === '[data-spatial-action]' && this.dataset.spatialAction) return this;
    if (selector === '[data-spatial-delete]' && this.dataset.spatialDelete) return this;
    if (selector === '[data-spatial-delete-cancel]' && this.dataset.spatialDeleteCancel) return this;
    if (selector === '[data-spatial-delete-confirm]' && this.dataset.spatialDeleteConfirm) return this;
    return selector === this.selector ? this : null;
  }

  matches(selector) {
    return selector === this.selector;
  }

  querySelector(selector) {
    return this.children.get(selector) || null;
  }

  querySelectorAll() {
    return [];
  }

  focus() { this.focusCalls += 1; }
  click() { this.clickCalls += 1; }
  setAttribute(name, value) { this.attributes.set(name, String(value)); }
  removeAttribute(name) { this.attributes.delete(name); }
}

function createFakeDocument() {
  const nodes = new Map();
  const node = (selector) => {
    if (!nodes.has(selector)) nodes.set(selector, new FakeElement(selector));
    return nodes.get(selector);
  };
  const form = node('[data-spatial-video-form]');
  const confirmation = node('[data-spatial-video-confirm]');
  const submit = node('button[type="submit"]');
  const status = node('[data-spatial-video-status]');
  form.children.set('[data-spatial-video-confirm]', confirmation);
  form.children.set('button[type="submit"]', submit);
  form.children.set('[data-spatial-video-status]', status);
  const imageAiForm = node('[data-spatial-image-ai-form]');
  const imageAiSubmit = node('[data-spatial-image-ai-form] button[type="submit"]');
  const imageAiStatus = node('[data-spatial-image-ai-status]');
  const imageAiPreview = node('[data-spatial-image-ai-preview]');
  imageAiForm.children.set('button[type="submit"]', imageAiSubmit);
  imageAiForm.children.set('[data-spatial-image-ai-status]', imageAiStatus);
  imageAiForm.children.set('[data-spatial-image-ai-preview]', imageAiPreview);
  node('#spatial-rename-form').hidden = true;
  node('#spatial-command-menu').hidden = true;
  node('#spatial-annotation-menu').hidden = true;
  node('#spatial-zero-composer').hidden = true;
  const deleteDialog = node('#spatial-delete-dialog');
  const deleteCancel = node('#spatial-delete-cancel');
  deleteCancel.selector = '[data-spatial-delete-cancel]';
  const deleteConfirm = node('[data-spatial-delete-confirm]');
  deleteCancel.dataset.spatialDeleteCancel = 'true';
  deleteConfirm.dataset.spatialDeleteConfirm = 'true';
  deleteDialog.children.set('[data-spatial-delete-cancel]', deleteCancel);
  deleteDialog.querySelectorAll = () => [deleteCancel, deleteConfirm];
  deleteDialog.hidden = true;

  const listeners = new Map();
  return {
    activeElement: null,
    documentElement: { dataset: {} },
    nodes,
    node,
    querySelector: node,
    addEventListener(type, listener) {
      const values = listeners.get(type) || [];
      values.push(listener);
      listeners.set(type, values);
    },
    removeEventListener(type, listener) {
      listeners.set(type, (listeners.get(type) || []).filter((item) => item !== listener));
    },
    async emit(type, event = {}) {
      const value = {
        defaultPrevented: false,
        propagationStopped: false,
        immediatePropagationStopped: false,
        preventDefault() { this.defaultPrevented = true; },
        stopPropagation() { this.propagationStopped = true; },
        stopImmediatePropagation() { this.immediatePropagationStopped = true; },
        target: node('#spatial-canvas-host'),
        ...event,
      };
      for (const listener of listeners.get(type) || []) await listener(value);
      return value;
    },
  };
}

function createFakeWindow() {
  let nextId = 1;
  let now = 0;
  const timers = new Map();
  const windowRef = {
    setTimeout(callback, delay = 0) {
      const id = nextId;
      nextId += 1;
      timers.set(id, { callback, delay: Number(delay) || 0, due: now + (Number(delay) || 0) });
      return id;
    },
    clearTimeout(id) {
      timers.delete(id);
    },
    confirm() { return true; },
    requestAnimationFrame(callback) {
      callback();
      return 0;
    },
  };
  return {
    windowRef,
    delays: () => [...timers.values()].map((timer) => timer.delay).sort((left, right) => left - right),
    pendingCount: () => timers.size,
    async advance(milliseconds) {
      now += milliseconds;
      for (const [id, timer] of [...timers]) {
        if (timer.due > now || !timers.has(id)) continue;
        timers.delete(id);
        await timer.callback();
        await settle();
      }
    },
    async runNext() {
      const next = [...timers.entries()].sort((left, right) => (
        left[1].delay - right[1].delay || left[0] - right[0]
      ))[0];
      assert.ok(next, 'expected a pending timer');
      timers.delete(next[0]);
      await next[1].callback();
      await settle();
      return next[1].delay;
    },
  };
}

function scene(label, elements = null) {
  return {
    label,
    elements: elements ?? [{ id: `shape-${label}`, type: 'rectangle', isDeleted: false }],
    appState: { zoom: { value: 1 }, scrollX: 0, scrollY: 0 },
    files: {},
  };
}

function sourceElement(canvasId) {
  return {
    id: `source-${canvasId}`,
    type: 'image',
    x: 0,
    y: 0,
    width: 320,
    height: 240,
    isDeleted: false,
    customData: { asset_id: SOURCE_ASSET_ID },
  };
}

function resultElement(canvasId) {
  return {
    id: `result-${canvasId}`,
    type: 'image',
    x: 360,
    y: 0,
    width: 320,
    height: 240,
    isDeleted: false,
    customData: {
      asset_id: RESULT_ASSET_ID,
      result_id: RESULT_ASSET_ID,
      task_id: null,
      product_profile_version_id: 'profilever:result-v1',
      lineage_parent_id: SOURCE_ASSET_ID,
    },
  };
}

function taskElement(taskId = 'job:video-old') {
  return {
    id: `task-${taskId}`,
    type: 'rectangle',
    x: 0,
    y: 360,
    width: 320,
    height: 176,
    isDeleted: false,
    customData: { task_id: taskId },
  };
}

function imageElement(id, assetId) {
  return {
    id,
    type: 'image',
    x: 0,
    y: 0,
    width: 320,
    height: 240,
    isDeleted: false,
    customData: { asset_id: assetId },
  };
}

function createFakeAdapter({ createCanvas, removeCanvas, updateScene } = {}) {
  const records = new Map(['canvas:a', 'canvas:b'].map((id) => [id, {
    id,
    name: id === 'canvas:a' ? '画布 A' : '画布 B',
    current_revision: 1,
    current_version_id: `version-${id}`,
    last_opened_at: '2026-09-02T12:00:00.000Z',
    summary: { element_count: 1 },
    scene: scene(`initial-${id}`, [sourceElement(id)]),
  }]));
  const createdByRequest = new Map();
  const createCalls = [];
  const updateCalls = [];
  const adapter = {
    kind: 'fake-sqlite',
    async load() { return adapter.list(); },
    list() { return [...records.values()]; },
    get(id) { return records.get(String(id)) || null; },
    async open(id) { return adapter.get(id); },
    async create(options = {}) {
      createCalls.push(options);
      if (createCanvas) {
        return createCanvas({ createCalls, createdByRequest, options, records });
      }
      const requestId = String(options.clientRequestId || `request:${createCalls.length}`);
      if (createdByRequest.has(requestId)) return records.get(createdByRequest.get(requestId));
      const id = `canvas:copy:${createdByRequest.size + 1}`;
      const record = {
        id,
        name: options.name || '冲突副本',
        current_revision: 1,
        last_opened_at: '2026-09-02T12:00:00.000Z',
        summary: { element_count: Array.from(options.scene?.elements || []).length },
        scene: options.scene,
      };
      records.set(id, record);
      createdByRequest.set(requestId, id);
      return record;
    },
    async rename(id) { return adapter.get(id); },
    async remove(id) {
      const record = adapter.get(id);
      if (!record) return null;
      if (removeCanvas) return removeCanvas({ id: String(id), record, records });
      records.delete(String(id));
      return { id: String(id), name: record.name, history_retained: true };
    },
    async updateScene(id, value) {
      const record = adapter.get(id);
      const call = { id, revision: record?.current_revision, scene: value };
      updateCalls.push(call);
      if (updateScene) return updateScene({ call, record, records, updateCalls });
      record.scene = value;
      record.current_revision += 1;
      record.current_version_id = `version-${id}-${record.current_revision}`;
      return record;
    },
  };
  return { adapter, createCalls, createdByRequest, records, updateCalls };
}

function createFakeRuntime({ mutateBusinessItems = false } = {}) {
  const mounts = new Map();
  return {
    mounts,
    runtime: {
      mountInfiniteCanvas(_host, options) {
        const canvasId = options.canvasDocument.id;
        const calls = {
          addBusinessItems: [],
          addBusinessItemOptions: [],
          addBusinessItemsOnce: [],
          selectBusinessReference: [],
          updateScene: [],
          updateTask: [],
          unmount: 0,
          shellModes: [],
          transplantUpdates: [],
        };
        let currentScene = options.canvasDocument.scene;
        const island = {
          getScene: () => currentScene,
          async addBusinessItems(items, insertionOptions = {}) {
            calls.addBusinessItems.push(items);
            calls.addBusinessItemOptions.push(insertionOptions);
            return { skipped: false };
          },
          async addBusinessItemsOnce(items) {
            calls.addBusinessItemsOnce.push(items);
            if (mutateBusinessItems) {
              const additions = Array.from(items || []).map((item, index) => ({
                id: `${item.kind}-${item.references?.task_id || item.references?.result_id || index}`,
                type: item.kind === 'task' ? 'rectangle' : 'image',
                x: 420 + (index * 40),
                y: 0,
                width: 320,
                height: item.kind === 'task' ? 176 : 240,
                isDeleted: false,
                customData: { ...item.references },
              }));
              currentScene = {
                ...currentScene,
                elements: [...Array.from(currentScene?.elements || []), ...additions],
              };
              options.onChange(currentScene);
            }
            return { skipped: false };
          },
          async updateTask(item) {
            calls.updateTask.push(item);
            return { changed: false };
          },
          async selectBusinessReference(reference) {
            calls.selectBusinessReference.push(reference);
            const target = Array.from(currentScene?.elements || []).find((element) => {
              const refs = element?.customData || {};
              return (reference.task_id && refs.task_id === reference.task_id)
                || (reference.result_id && refs.result_id === reference.result_id)
                || (reference.asset_id && refs.asset_id === reference.asset_id);
            }) || null;
            if (target) options.onSelectionChange(target);
            return target;
          },
          updateScene(value) {
            calls.updateScene.push(value);
            currentScene = value;
          },
          scenePointFromClient(point) {
            return { x: Number(point.clientX) + 1000, y: Number(point.clientY) + 2000 };
          },
          stopVideo() {},
          setShellMode(mode) { calls.shellModes.push(mode); },
          updateTransplantShell(value) { calls.transplantUpdates.push(value); },
          unmount() { calls.unmount += 1; },
        };
        const mount = {
          calls,
          island,
          options,
          emitChange(value) {
            currentScene = value;
            options.onChange(value);
          },
        };
        mounts.set(canvasId, mount);
        queueMicrotask(options.onReady);
        return island;
      },
    },
  };
}

function completedVideoJob(canvasId = 'canvas:a') {
  const parameters = {
    spatial_canvas_id: canvasId,
    first_frame_asset_id: SOURCE_ASSET_ID,
  };
  return {
    id: 'job_video_a',
    mode: 'single',
    status: 'completed',
    parameters,
    snapshot: {
      command_id: 'command:image-to-video',
      parameters,
      source_asset_ids: [SOURCE_ASSET_ID],
    },
    items: [{ result_asset_ids: ['ast_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa'] }],
  };
}

function completedWhiteBackgroundJob(canvasId = 'canvas:a', status = 'completed') {
  const parameters = {
    spatial_action: 'white-background',
    spatial_canvas_id: canvasId,
    spatial_source_element_id: `source-${canvasId}`,
  };
  return {
    id: 'job_white_background_a',
    mode: 'single',
    status,
    parameters,
    snapshot: {
      command_id: 'command:existing-generate-single',
      parameters,
      source_asset_ids: [SOURCE_ASSET_ID],
      product_profile_version_id: 'profilever:g4b-v1',
    },
    items: [{ result_asset_ids: status === 'completed' ? ['ast_white_background_result'] : [] }],
  };
}

function completedResultVariationJob(canvasId = 'canvas:a', status = 'completed') {
  const parameters = {
    spatial_action: 'generate-image',
    spatial_canvas_id: canvasId,
    spatial_source_element_id: `result-${canvasId}`,
  };
  return {
    id: 'job_result_variation_a',
    mode: 'single',
    status,
    parameters,
    snapshot: {
      command_id: 'command:existing-generate-single',
      parameters,
      source_asset_ids: [RESULT_ASSET_ID],
      product_profile_version_id: 'profilever:result-v1',
    },
    items: [{ result_asset_ids: status === 'completed' ? [VARIATION_ASSET_ID] : [] }],
  };
}

function createHarness({
  api: apiOverrides = {},
  createCanvas,
  getImageAiDefaults,
  getWhiteBackgroundDefaults,
  mutateBusinessItems = false,
  onImportFiles,
  removeCanvas,
  updateScene,
  runtimeLoader,
  shellMode,
} = {}) {
  const documentRef = createFakeDocument();
  const clock = createFakeWindow();
  const adapterState = createFakeAdapter({ createCanvas, removeCanvas, updateScene });
  const runtimeState = createFakeRuntime({ mutateBusinessItems });
  const api = {
    async getAsset(assetId) {
      return {
        asset: {
          id: assetId,
          name: '测试商品',
          kind: assetId === SOURCE_ASSET_ID ? 'image' : 'video',
          role: assetId === SOURCE_ASSET_ID ? 'workspace_source' : 'result_video',
          mime: assetId === SOURCE_ASSET_ID ? 'image/png' : 'video/webm',
          width: 320,
          height: 240,
        },
      };
    },
    async getJob() { throw new Error('unexpected getJob call'); },
    async getJobs() { return { jobs: [] }; },
    async executeCommand() { throw new Error('unexpected executeCommand call'); },
    getAssetThumbnailUrl: () => 'cover://asset',
    getAssetContentUrl: async () => 'stream://asset',
    ...apiOverrides,
  };
  const controller = createInfiniteCanvasWorkspaceController({
    documentRef,
    windowRef: clock.windowRef,
    api,
    adapter: adapterState.adapter,
    getImageAiDefaults,
    getWhiteBackgroundDefaults,
    onImportFiles,
    runtimeLoader: runtimeLoader || (async () => runtimeState.runtime),
    shellMode,
  });
  return { api, clock, controller, documentRef, ...adapterState, ...runtimeState };
}

async function activateAndOpen(harness, canvasId) {
  harness.controller.setPage(true);
  await settle();
  await harness.controller.openCanvas(canvasId);
  await settle();
  return harness.mounts.get(canvasId);
}

test('Canvas transplant is parallel to the legacy Shell and keeps one mounted island', async () => {
  const harness = createHarness({ shellMode: 'transplant' });
  harness.controller.bind();
  const mount = await activateAndOpen(harness, 'canvas:a');
  assert.equal(mount.options.initialShellMode, 'transplant');
  assert.equal(harness.documentRef.node('#spatial-editor').dataset.shell, 'transplant');
  mount.options.onSelectionChange(sourceElement('canvas:a'));
  await settle();
  assert.equal(mount.calls.transplantUpdates.at(-1).asset.role, 'workspace_source');
  const toggle = harness.documentRef.node('#btn-spatial-shell-toggle');
  await harness.documentRef.node('#page-canvas').emit('click', { target: toggle });
  assert.deepEqual(mount.calls.shellModes, ['legacy']);
  assert.equal(harness.documentRef.node('#spatial-editor').dataset.shell, 'legacy');
  assert.equal(harness.mounts.size, 1);
  harness.controller.destroy();
});

test('Canvas Product Shell keeps an empty work surface and projects composer, single-selection and multi-selection states without a second execution path', async () => {
  const harness = createHarness();
  harness.records.get('canvas:a').scene = scene('empty', []);
  harness.records.get('canvas:a').summary = { element_count: 0 };
  harness.controller.bind();
  const mount = await activateAndOpen(harness, 'canvas:a');

  assert.equal(harness.documentRef.node('#spatial-empty-launcher').hidden, true);

  const zeroOpen = new FakeElement('[data-spatial-zero-open]');
  await harness.documentRef.node('#page-canvas').emit('click', { target: zeroOpen });
  assert.equal(harness.documentRef.node('#spatial-empty-launcher').hidden, false);
  assert.equal(harness.documentRef.node('#spatial-zero-composer').hidden, false);
  const zeroField = new FakeElement('[data-spatial-zero-field]');
  zeroField.value = '一张克制的夏日饮料主视觉';
  await harness.documentRef.node('#page-canvas').emit('input', { target: zeroField });
  await harness.documentRef.node('#page-canvas').emit('submit', {
    target: new FakeElement('[data-spatial-zero-form]'),
  });
  assert.match(harness.documentRef.node('#spatial-zero-status').textContent, /未创建 Task/);

  const zeroClose = new FakeElement('[data-spatial-zero-close]');
  await harness.documentRef.node('#page-canvas').emit('click', { target: zeroClose });
  const source = sourceElement('canvas:a');
  mount.emitChange(scene('with-source', [source]));
  assert.equal(harness.documentRef.node('#spatial-empty-launcher').hidden, true);

  mount.options.onSelectionContextChange({
    activeTool: 'selection', count: 1, elementIds: [source.id], businessElements: [source],
  });
  mount.options.onSelectionChange(source);
  await settle();
  const contextBar = harness.documentRef.node('#spatial-context-bar');
  assert.equal(contextBar.hidden, false);
  assert.match(contextBar.innerHTML, /白底图/);
  assert.match(contextBar.innerHTML, /Fabric 精修/);
  assert.match(contextBar.innerHTML, /告诉 AI 下一步怎么改/);
  assert.equal(harness.documentRef.node('#spatial-inspector').hidden, true);

  mount.options.onSelectionContextChange({
    activeTool: 'selection', count: 2, elementIds: [source.id, 'note-1'], businessElements: [source],
  });
  mount.options.onSelectionChange(null);
  await settle();
  assert.match(contextBar.innerHTML, /已选择 2 个对象/);
  assert.match(contextBar.innerHTML, /Ctrl\+G/);
  harness.controller.destroy();
});

test('video and selection callbacks cannot postpone an unchanged scene save', async () => {
  const harness = createHarness();
  const mount = await activateAndOpen(harness, 'canvas:a');
  const edited = scene('edited');
  mount.emitChange(edited);
  for (let index = 0; index < 8; index += 1) {
    await harness.clock.advance(80);
    mount.emitChange({ ...edited, appState: { ...edited.appState, selectedElementIds: { video: true }, cursorButton: index % 2 ? 'up' : 'down' } });
  }
  assert.equal(harness.updateCalls.length, 1, 'the edit must be durable even while runtime callbacks continue');
  assert.doesNotMatch(harness.documentRef.node('#spatial-save-state').textContent, /正在保存/);
  harness.controller.destroy();
});

test('undo to the durable scene while a different save is in flight is still persisted', async () => {
  const saving = deferred();
  const harness = createHarness({
    async updateScene({ call, record, updateCalls }) {
      if (updateCalls.length === 1) await saving.promise;
      record.scene = call.scene;
      record.current_revision += 1;
      return record;
    },
  });
  const mount = await activateAndOpen(harness, 'canvas:a');
  const original = harness.adapter.get('canvas:a').scene;
  mount.emitChange(scene('edit-in-flight'));
  const firstSave = harness.clock.runNext();
  await waitFor(() => harness.updateCalls.length === 1);
  mount.emitChange(original);
  saving.resolve();
  await firstSave;
  await harness.clock.advance(240);
  assert.equal(harness.updateCalls.length, 2);
  assert.deepEqual(harness.adapter.get('canvas:a').scene, original);
  harness.controller.destroy();
});

test('an unchanged callback retries a failed save and viewport changes remain durable', async () => {
  const harness = createHarness({
    async updateScene({ call, record, updateCalls }) {
      if (updateCalls.length === 1) throw new Error('temporary save outage');
      record.scene = call.scene;
      record.current_revision += 1;
      return record;
    },
  });
  const mount = await activateAndOpen(harness, 'canvas:a');
  const edited = scene('retry');
  mount.emitChange(edited);
  await harness.clock.advance(240);
  assert.match(harness.documentRef.node('#spatial-save-state').textContent, /temporary save outage/);
  assert.equal(harness.documentRef.node('#spatial-recovery-action').dataset.spatialRecovery, 'retry-save');
  mount.emitChange(edited);
  await harness.clock.advance(240);
  assert.equal(harness.updateCalls.length, 2);
  const restoredOrder = { ...edited, elements: edited.elements.map(({ id, type, isDeleted }) => ({ isDeleted, type, id })) };
  mount.emitChange(restoredOrder);
  await harness.clock.advance(240);
  assert.equal(harness.updateCalls.length, 2, 'JSON key order is not an edit');
  const moved = { ...edited, appState: { zoom: { value: 0.75 }, scrollX: 90, scrollY: -45 } };
  mount.emitChange(moved);
  await harness.clock.advance(240);
  assert.equal(harness.updateCalls.length, 3);
  assert.deepEqual(harness.adapter.get('canvas:a').scene.appState, moved.appState);
  harness.controller.destroy();
});

test('an Explorer FileList drop imports ledger assets before adding canvas references', async () => {
  const importCalls = [];
  const importedItem = {
    kind: 'image',
    business_kind: 'asset',
    references: {
      asset_id: 'ast_external_drop',
      result_id: null,
      task_id: null,
      product_profile_version_id: null,
      lineage_parent_id: null,
    },
  };
  const harness = createHarness({
    async onImportFiles(files) {
      importCalls.push(files);
      return [importedItem];
    },
  });
  harness.controller.bind();
  const mount = await activateAndOpen(harness, 'canvas:a');
  const file = { name: 'coffee-powder.jpg', size: 4096, type: 'image/jpeg' };
  const transfer = {
    dropEffect: 'none',
    files: [file],
    getData() { return ''; },
    types: ['Files'],
  };

  const dragover = await harness.documentRef.emit('dragover', { dataTransfer: transfer });
  assert.equal(dragover.defaultPrevented, true);
  assert.equal(dragover.propagationStopped, true);
  assert.equal(dragover.immediatePropagationStopped, true);
  assert.equal(transfer.dropEffect, 'copy');
  assert.equal(harness.documentRef.node('#spatial-canvas-host').dataset.fileDropActive, 'true');
  const drop = await harness.documentRef.emit('drop', {
    clientX: 320,
    clientY: 240,
    dataTransfer: transfer,
  });

  assert.equal(drop.defaultPrevented, true);
  assert.equal(drop.propagationStopped, true);
  assert.equal(drop.immediatePropagationStopped, true);
  assert.equal(harness.documentRef.node('#spatial-canvas-host').dataset.fileDropActive, 'false');
  assert.deepEqual(importCalls, [[file]]);
  assert.deepEqual(mount.calls.addBusinessItems, [[importedItem]]);
  assert.deepEqual(mount.calls.addBusinessItemOptions, [{
    insertionPoint: { x: 1320, y: 2240 },
  }]);
  assert.match(harness.documentRef.node('#spatial-save-state').textContent, /已加入|正在保存/);
  harness.controller.destroy();
});

test('a clipboard image uses the same durable import path without intercepting ordinary paste', async () => {
  const importCalls = [];
  const importedItem = {
    kind: 'image',
    business_kind: 'asset',
    references: {
      asset_id: 'ast_clipboard_image',
      result_id: null,
      task_id: null,
      product_profile_version_id: null,
      lineage_parent_id: null,
    },
  };
  const harness = createHarness({
    async onImportFiles(files) {
      importCalls.push(files);
      return [importedItem];
    },
  });
  harness.controller.bind();
  const mount = await activateAndOpen(harness, 'canvas:a');
  const file = { name: 'clipboard-product.png', size: 2048, type: 'image/png' };
  const paste = await harness.documentRef.emit('paste', {
    clipboardData: { files: [file], items: [], types: ['Files'] },
  });
  assert.equal(paste.defaultPrevented, true);
  assert.deepEqual(importCalls, [[file]]);
  assert.deepEqual(mount.calls.addBusinessItems, [[importedItem]]);
  assert.deepEqual(mount.calls.addBusinessItemOptions, [{}]);

  const textPaste = await harness.documentRef.emit('paste', {
    clipboardData: { files: [], items: [], types: ['text/plain'] },
  });
  assert.equal(textPaste.defaultPrevented, false);
  assert.equal(importCalls.length, 1);
  harness.controller.destroy();
});

test('the visible import control opens the Canvas file picker', async () => {
  const harness = createHarness();
  harness.controller.bind();
  await harness.documentRef.node('#btn-spatial-import').emit('click');
  assert.equal(harness.documentRef.node('#spatial-file-input').clickCalls, 1);
  harness.controller.destroy();
});

test('an Explorer drop never moves from its receiving canvas after an async import', async () => {
  const imported = deferred();
  const importedItem = {
    kind: 'image',
    business_kind: 'asset',
    references: {
      asset_id: 'ast_delayed_external_drop',
      result_id: null,
      task_id: null,
      product_profile_version_id: null,
      lineage_parent_id: null,
    },
  };
  const harness = createHarness({
    async onImportFiles() {
      return imported.promise;
    },
  });
  harness.controller.bind();
  const mountA = await activateAndOpen(harness, 'canvas:a');
  const file = { name: 'delayed-coffee.jpg', size: 4096, type: 'image/jpeg' };
  const dropPromise = harness.documentRef.emit('drop', {
    dataTransfer: {
      files: [file],
      getData() { return ''; },
      types: ['Files'],
    },
  });
  await Promise.resolve();
  const mountB = await activateAndOpen(harness, 'canvas:b');

  imported.resolve([importedItem]);
  await dropPromise;

  assert.deepEqual(mountA.calls.addBusinessItems, []);
  assert.deepEqual(mountB.calls.addBusinessItems, []);
  assert.match(
    harness.documentRef.node('#spatial-save-state').textContent,
    /画布已切换，未加入节点/,
  );
  harness.controller.destroy();
});

test('a video submission resolved after switching from A to B never mutates B', async () => {
  const execution = deferred();
  const executeCalls = [];
  const harness = createHarness({
    api: {
      async executeCommand(commandId, payload) {
        executeCalls.push({ commandId, payload });
        return execution.promise;
      },
    },
  });
  harness.controller.bind();
  const mountA = await activateAndOpen(harness, 'canvas:a');
  const source = sourceElement('canvas:a');
  mountA.options.onSelectionChange(source);
  await settle();
  await harness.controller.openVideoComposer({
    canvasId: 'canvas:a',
    element: source,
  });

  const confirmation = harness.documentRef.node('[data-spatial-video-confirm]');
  confirmation.checked = true;
  await harness.documentRef.node('#page-canvas').emit('change', { target: confirmation });
  await harness.documentRef.node('#page-canvas').emit('submit', {
    target: harness.documentRef.node('[data-spatial-video-form]'),
  });
  await waitFor(() => executeCalls.length === 1, 'video command was not submitted');
  assert.equal(executeCalls[0].payload.spatial_canvas_id, 'canvas:a');

  await harness.controller.openCanvas('canvas:b');
  await settle();
  const mountB = harness.mounts.get('canvas:b');
  execution.resolve({ job: completedVideoJob('canvas:a') });
  await settle(20);

  assert.equal(harness.controller.currentId, 'canvas:b');
  assert.equal(mountA.calls.addBusinessItemsOnce.length, 0);
  assert.equal(mountB.calls.addBusinessItemsOnce.length, 0);
  assert.equal(mountB.calls.updateTask.length, 0);
  harness.controller.destroy();
});

test('a white-background submission resolved after switching from A to B never mutates B', async () => {
  const execution = deferred();
  const executeCalls = [];
  const previewCalls = [];
  const harness = createHarness({
    getWhiteBackgroundDefaults: () => ({
      designSkillId: 'comfyui-food-product-main-image',
    }),
    api: {
      async compileKnowledge(payload) {
        previewCalls.push(payload);
        return {
          execution_context: {
            binding: 'preview',
            context_sha256: 'a'.repeat(64),
            summary: { source_count: 1, positive_rule_count: 4 },
            user_intent: { user_request: payload.user_request },
            provider_adapter: { model: payload.model },
          },
          spatial_context: {
            action: 'white-background',
            spatial_canvas_id: payload.spatial_canvas_id,
            source_element_id: payload.spatial_source_element_id,
            source_asset_id: payload.source_asset_ids[0],
            fingerprint: 'b'.repeat(64),
          },
          skill_snapshot: {
            title: '食品饮料白底主图',
            version: 'content-test',
            content_sha256: 'c'.repeat(64),
            adapter_version: 'context-skill-adapter-v1',
          },
        };
      },
      async executeCommand(commandId, payload) {
        executeCalls.push({ commandId, payload });
        return execution.promise;
      },
    },
  });
  harness.controller.bind();
  const mountA = await activateAndOpen(harness, 'canvas:a');
  const source = sourceElement('canvas:a');
  source.customData.product_profile_version_id = 'profilever:g4b-v1';
  mountA.options.onSelectionChange(source);
  await settle();
  const action = new FakeElement('[data-spatial-action]');
  action.dataset.spatialAction = 'white-background';
  await harness.documentRef.node('#page-canvas').emit('click', { target: action });
  assert.equal(previewCalls.length, 1);
  assert.equal(previewCalls[0].spatial_source_element_id, source.id);

  await harness.documentRef.node('#page-canvas').emit('submit', {
    target: harness.documentRef.node('[data-spatial-image-ai-form]'),
  });
  await waitFor(() => executeCalls.length === 1, 'white-background command was not submitted');
  assert.equal(executeCalls[0].payload.spatial_canvas_id, 'canvas:a');
  assert.equal(executeCalls[0].payload.max_attempts, 1);
  assert.equal(executeCalls[0].payload.parameters.design_skill_id, 'comfyui-food-product-main-image');

  await harness.controller.openCanvas('canvas:b');
  await settle();
  const mountB = harness.mounts.get('canvas:b');
  execution.resolve({ job: completedWhiteBackgroundJob('canvas:a', 'running') });
  await settle(20);

  assert.equal(harness.controller.currentId, 'canvas:b');
  assert.equal(mountA.calls.addBusinessItemsOnce.length, 0);
  assert.equal(mountB.calls.addBusinessItemsOnce.length, 0);
  assert.equal(mountB.calls.updateTask.length, 0);
  harness.controller.destroy();
});

test('Result variation submits the exact selected Result through the shared Canvas AI preview', async () => {
  const execution = deferred();
  const executeCalls = [];
  const previewCalls = [];
  const harness = createHarness({
    getImageAiDefaults: () => ({
      designSkillId: 'comfyui-food-product-main-image',
    }),
    api: {
      async getAsset(assetId) {
        return {
          asset: {
            id: assetId,
            name: assetId === RESULT_ASSET_ID ? '带包装文字的现有结果' : '生图变体',
            kind: 'image',
            role: 'result_main',
            mime: 'image/png',
            width: 320,
            height: 240,
            lineage_parent_id: assetId === RESULT_ASSET_ID ? SOURCE_ASSET_ID : RESULT_ASSET_ID,
          },
        };
      },
      async compileKnowledge(payload) {
        previewCalls.push(payload);
        return {
          execution_context: {
            binding: 'preview',
            context_sha256: 'e'.repeat(64),
            summary: { source_count: 2, positive_rule_count: 6 },
            user_intent: { user_request: payload.user_request },
            provider_adapter: { model: payload.model },
          },
          spatial_context: {
            action: 'generate-image',
            spatial_canvas_id: payload.spatial_canvas_id,
            source_element_id: payload.spatial_source_element_id,
            source_asset_id: payload.source_asset_ids[0],
            lineage_parent_id: payload.source_asset_ids[0],
            fingerprint: 'f'.repeat(64),
          },
          skill_snapshot: {
            title: '食品饮料白底主图',
            version: 'content-test',
            content_sha256: 'a'.repeat(64),
            adapter_version: 'context-skill-adapter-v1',
          },
        };
      },
      async executeCommand(commandId, payload) {
        executeCalls.push({ commandId, payload });
        return execution.promise;
      },
    },
  });
  harness.records.get('canvas:a').scene = scene('result-canvas-a', [resultElement('canvas:a')]);
  harness.controller.bind();
  const mountA = await activateAndOpen(harness, 'canvas:a');
  const result = resultElement('canvas:a');
  mountA.options.onSelectionChange(result);
  await settle();
  const action = new FakeElement('[data-spatial-action]');
  action.dataset.spatialAction = 'generate-image';
  await harness.documentRef.node('#page-canvas').emit('click', { target: action });

  assert.equal(previewCalls.length, 1);
  assert.deepEqual(previewCalls[0].source_asset_ids, [RESULT_ASSET_ID]);
  assert.equal(previewCalls[0].spatial_action, 'generate-image');
  assert.equal(previewCalls[0].spatial_source_element_id, result.id);

  await harness.documentRef.node('#page-canvas').emit('submit', {
    target: harness.documentRef.node('[data-spatial-image-ai-form]'),
  });
  await waitFor(() => executeCalls.length === 1, 'Result variation command was not submitted');
  assert.equal(executeCalls[0].commandId, 'command:existing-generate-single');
  assert.deepEqual(executeCalls[0].payload.source_asset_ids, [RESULT_ASSET_ID]);
  assert.equal(executeCalls[0].payload.parameters.spatial_action, 'generate-image');
  assert.equal(executeCalls[0].payload.parameters.automatic_paid_retry, false);
  assert.equal(executeCalls[0].payload.max_attempts, 1);

  await harness.controller.openCanvas('canvas:b');
  await settle();
  const mountB = harness.mounts.get('canvas:b');
  execution.resolve({ job: completedResultVariationJob('canvas:a', 'running') });
  await settle(20);

  assert.equal(harness.controller.currentId, 'canvas:b');
  assert.equal(mountA.calls.addBusinessItemsOnce.length, 0);
  assert.equal(mountB.calls.addBusinessItemsOnce.length, 0);
  assert.equal(mountB.calls.updateTask.length, 0);
  harness.controller.destroy();
});

test('Canvas reference entry reviews the exact source or Result without creating a second Task path', async () => {
  const previewCalls = [];
  let executeCalls = 0;
  const harness = createHarness({
    getImageAiDefaults: () => ({
      promptVersion: 'prompt_v3',
      designSkillId: 'comfyui-food-product-main-image',
    }),
    api: {
      async getAsset(assetId) {
        return { asset: {
          id: assetId, name: assetId === RESULT_ASSET_ID ? '当前 Result' : '当前素材',
          kind: 'image', role: assetId === RESULT_ASSET_ID ? 'result_main' : 'workspace_source',
          mime: 'image/png', width: 320, height: 240,
        } };
      },
      async compileKnowledge(payload) {
        previewCalls.push(payload);
        return {
          execution_context: {
            binding: 'preview', context_sha256: 'c'.repeat(64),
            summary: {}, user_intent: { user_request: payload.user_request },
          },
          spatial_context: {
            action: payload.spatial_action,
            input_surface: payload.ui_context?.input_surface,
            spatial_canvas_id: payload.spatial_canvas_id,
            source_element_id: payload.spatial_source_element_id,
            source_asset_id: payload.source_asset_ids[0],
            lineage_parent_id: payload.source_asset_ids[0],
            fingerprint: 'd'.repeat(64),
          },
        };
      },
      async executeCommand() { executeCalls += 1; throw new Error('no task expected'); },
    },
  });
  harness.controller.bind();
  const mount = await activateAndOpen(harness, 'canvas:a');
  const source = sourceElement('canvas:a');
  mount.options.onSelectionChange(source);
  await settle();
  const referenceButton = new FakeElement('[data-spatial-reference]');
  await harness.documentRef.node('#page-canvas').emit('click', { target: referenceButton });
  assert.equal(previewCalls.length, 1);
  assert.deepEqual(previewCalls[0].source_asset_ids, [SOURCE_ASSET_ID]);
  assert.equal(previewCalls[0].ui_context.input_surface, 'canvas-reference');
  assert.equal(previewCalls[0].prompt_version, 'prompt_v1');
  assert.equal(previewCalls[0].design_skill_id, '');
  assert.match(harness.documentRef.node('#spatial-inspector').innerHTML, /参考生成/);
  assert.match(harness.documentRef.node('#spatial-inspector').innerHTML, /当前素材/);
  assert.equal(executeCalls, 0);

  const result = resultElement('canvas:a');
  mount.options.onSelectionChange(result);
  await settle();
  await harness.documentRef.node('#page-canvas').emit('click', { target: referenceButton });
  assert.equal(previewCalls.length, 2);
  assert.deepEqual(previewCalls[1].source_asset_ids, [RESULT_ASSET_ID]);
  assert.equal(previewCalls[1].spatial_source_element_id, result.id);
  assert.equal(executeCalls, 0);
  harness.controller.destroy();
});

test('Canvas Conversation only reviews context before confirmation and accepts one exact source or Result', async () => {
  const previewCalls = [];
  let executeCalls = 0;
  const harness = createHarness({
    getImageAiDefaults: () => ({
      designSkillId: 'comfyui-food-product-main-image',
    }),
    api: {
      async getAsset(assetId) {
        return {
          asset: {
            id: assetId,
            name: assetId === RESULT_ASSET_ID ? '当前精确 Result' : '原始素材',
            kind: 'image',
            role: assetId === RESULT_ASSET_ID ? 'result_main' : 'workspace_source',
            mime: 'image/png',
            width: 320,
            height: 240,
          },
        };
      },
      async compileKnowledge(payload) {
        previewCalls.push(payload);
        return {
          execution_context: {
            binding: 'preview',
            context_sha256: String(previewCalls.length).repeat(64),
            summary: { source_count: 1, positive_rule_count: 4 },
            user_intent: { user_request: payload.user_request },
            provider_adapter: { model: payload.model },
          },
          spatial_context: {
            action: payload.spatial_action,
            input_surface: payload.ui_context?.input_surface,
            spatial_canvas_id: payload.spatial_canvas_id,
            source_element_id: payload.spatial_source_element_id,
            source_asset_id: payload.source_asset_ids[0],
            lineage_parent_id: payload.source_asset_ids[0],
            fingerprint: 'f'.repeat(64),
          },
          skill_snapshot: {
            title: '食品饮料白底主图',
            version: 'content-test',
            content_sha256: 'a'.repeat(64),
            adapter_version: 'context-skill-adapter-v1',
          },
        };
      },
      async executeCommand() {
        executeCalls += 1;
        throw new Error('Ctrl+Enter must not create a paid task');
      },
    },
  });
  harness.controller.bind();
  const mount = await activateAndOpen(harness, 'canvas:a');
  const source = sourceElement('canvas:a');
  mount.options.onSelectionChange(source);
  await settle();

  assert.match(harness.documentRef.node('#spatial-inspector').innerHTML, /告诉 AI 下一步怎么改/);
  const sourceField = new FakeElement('[data-spatial-conversation-field]');
  sourceField.tagName = 'TEXTAREA';
  sourceField.value = '把背景改成暖灰摄影棚，保留包装文字与 Logo';
  await harness.documentRef.node('#page-canvas').emit('input', { target: sourceField });
  const reviewShortcut = await harness.documentRef.emit('keydown', {
    key: 'Enter', ctrlKey: true, target: sourceField,
  });
  await waitFor(() => previewCalls.length === 1, 'source conversation preview was not compiled');

  assert.equal(reviewShortcut.defaultPrevented, true);
  assert.equal(executeCalls, 0);
  assert.deepEqual(previewCalls[0].source_asset_ids, [SOURCE_ASSET_ID]);
  assert.equal(previewCalls[0].user_request, sourceField.value);
  assert.deepEqual(previewCalls[0].ui_context, {
    input_surface: 'canvas-conversation',
    contract_version: 'canvas-conversation-input-v1',
  });
  assert.match(harness.documentRef.node('#spatial-inspector').innerHTML, /对话修改/);
  await settle(8);

  const escape = await harness.documentRef.emit('keydown', {
    key: 'Escape', target: harness.documentRef.node('#spatial-canvas-host'),
  });
  await settle();
  assert.equal(escape.defaultPrevented, true);
  assert.equal(harness.documentRef.node('[data-spatial-conversation-field]').focusCalls, 1);

  const cancel = new FakeElement('[data-spatial-image-ai-cancel]');
  const result = resultElement('canvas:a');
  mount.options.onSelectionChange(result);
  await settle();
  const resultField = new FakeElement('[data-spatial-conversation-field]');
  resultField.tagName = 'TEXTAREA';
  resultField.value = '把当前阴影再柔和一点';
  await harness.documentRef.node('#page-canvas').emit('input', { target: resultField });
  await harness.documentRef.emit('keydown', {
    key: 'Enter', ctrlKey: true, target: resultField,
  });
  await waitFor(() => previewCalls.length === 2, 'Result conversation preview was not compiled');

  assert.deepEqual(previewCalls[1].source_asset_ids, [RESULT_ASSET_ID]);
  assert.equal(previewCalls[1].spatial_source_element_id, result.id);
  assert.equal(previewCalls[1].user_request, resultField.value);
  assert.equal(executeCalls, 0);

  await harness.documentRef.node('#page-canvas').emit('click', { target: cancel });
  await harness.controller.openCanvasAiPreview('generate-image', {
    canvasId: 'canvas:a', element: result,
  });
  assert.equal(previewCalls.length, 3);
  assert.equal(previewCalls[2].ui_context, undefined, 'ordinary Ctrl+K/Inspector action remains unchanged');
  assert.match(previewCalls[2].user_request, /当前结果/);
  harness.controller.destroy();
});

test('Canvas Conversation discards a preview when the exact selection changes during preparation', async () => {
  const defaults = deferred();
  let previewCalls = 0;
  const harness = createHarness({
    getImageAiDefaults: () => defaults.promise,
    api: {
      async getAsset(assetId) {
        return {
          asset: {
            id: assetId,
            name: assetId === RESULT_ASSET_ID ? '新选 Result' : '原始素材',
            kind: 'image',
            role: assetId === RESULT_ASSET_ID ? 'result_main' : 'workspace_source',
            mime: 'image/png',
            width: 320,
            height: 240,
          },
        };
      },
      async compileKnowledge() {
        previewCalls += 1;
        throw new Error('stale selection must not compile');
      },
    },
  });
  const mount = await activateAndOpen(harness, 'canvas:a');
  const source = sourceElement('canvas:a');
  mount.options.onSelectionChange(source);
  await settle();
  const pending = harness.controller.openCanvasConversationPreview('把背景改成暖灰色');
  await settle();
  const result = resultElement('canvas:a');
  mount.options.onSelectionChange(result);
  await settle();
  defaults.resolve({ designSkillId: 'comfyui-food-product-main-image' });
  assert.equal(await pending, null);
  assert.equal(previewCalls, 0);
  assert.match(harness.documentRef.node('#spatial-inspector').innerHTML, /新选 Result/);
  assert.match(harness.documentRef.node('#spatial-inspector').innerHTML, /告诉 AI 下一步怎么改/);
  harness.controller.destroy();
});

test('Inspector and Ctrl+K share the current-selection Native AI action adapter', async () => {
  const previewCalls = [];
  const harness = createHarness({
    api: {
      async getAsset(assetId) {
        return {
          asset: {
            id: assetId,
            name: assetId === RESULT_ASSET_ID ? '已选 Result' : '原始素材',
            kind: 'image',
            role: assetId === RESULT_ASSET_ID ? 'result_main' : 'workspace_source',
            mime: 'image/png',
            width: 320,
            height: 240,
          },
        };
      },
      async compileKnowledge(payload) {
        previewCalls.push(payload);
        return {
          execution_context: {
            binding: 'preview',
            context_sha256: 'a'.repeat(64),
            summary: { source_count: 1 },
          },
          spatial_context: {
            action: payload.spatial_action,
            spatial_canvas_id: payload.spatial_canvas_id,
            source_element_id: payload.spatial_source_element_id,
            source_asset_id: payload.source_asset_ids[0],
            fingerprint: 'b'.repeat(64),
          },
        };
      },
    },
  });
  harness.controller.bind();
  const mount = await activateAndOpen(harness, 'canvas:a');
  const source = sourceElement('canvas:a');
  mount.options.onSelectionChange(source);
  await settle();

  assert.deepEqual(
    harness.controller.getCurrentNativeAiActions().map((item) => item.action),
    ['white-background'],
  );
  const host = harness.documentRef.node('#spatial-canvas-host');
  harness.documentRef.activeElement = host;
  const shortcut = await harness.documentRef.emit('keydown', {
    key: 'k',
    ctrlKey: true,
    target: host,
  });
  assert.equal(shortcut.defaultPrevented, true);
  assert.equal(harness.controller.commandMenuOpen, true);
  assert.match(harness.documentRef.node('#spatial-command-menu').innerHTML, /data-spatial-command-action="white-background"/);
  assert.doesNotMatch(harness.documentRef.node('#spatial-command-menu').innerHTML, /data-spatial-command-action="generate-image"/);

  const menuAction = new FakeElement('[data-spatial-command-action]');
  menuAction.dataset.spatialCommandAction = 'white-background';
  await harness.documentRef.node('#page-canvas').emit('click', { target: menuAction });
  assert.equal(harness.controller.commandMenuOpen, false);
  assert.equal(previewCalls.length, 1);

  const cancel = new FakeElement('[data-spatial-image-ai-cancel]');
  await harness.documentRef.node('#page-canvas').emit('click', { target: cancel });
  const inspectorAction = new FakeElement('[data-spatial-action]');
  inspectorAction.dataset.spatialAction = 'white-background';
  await harness.documentRef.node('#page-canvas').emit('click', { target: inspectorAction });
  assert.equal(previewCalls.length, 2);
  assert.deepEqual(previewCalls[1], previewCalls[0]);

  await harness.documentRef.node('#page-canvas').emit('click', { target: cancel });
  const result = resultElement('canvas:a');
  mount.options.onSelectionChange(result);
  await settle();
  assert.deepEqual(
    harness.controller.getCurrentNativeAiActions().map((item) => item.action),
    ['generate-image'],
  );
  harness.documentRef.activeElement = host;
  await harness.documentRef.emit('keydown', { key: 'k', ctrlKey: true, target: host });
  assert.match(harness.documentRef.node('#spatial-command-menu').innerHTML, /data-spatial-command-action="generate-image"/);
  assert.doesNotMatch(harness.documentRef.node('#spatial-command-menu').innerHTML, /data-spatial-command-action="white-background"/);
  await harness.documentRef.emit('keydown', { key: 'Escape', target: host });
  harness.controller.destroy();
});

test('Canvas-local Ctrl+K respects editing layers and restores trapped focus on Escape', async () => {
  const harness = createHarness();
  harness.controller.bind();
  const mount = await activateAndOpen(harness, 'canvas:a');
  const source = sourceElement('canvas:a');
  mount.options.onSelectionChange(source);
  await settle();
  const host = harness.documentRef.node('#spatial-canvas-host');
  harness.documentRef.activeElement = host;

  await harness.documentRef.emit('keydown', { key: 'k', ctrlKey: true, target: host });
  const menu = harness.documentRef.node('#spatial-command-menu');
  assert.equal(harness.controller.commandMenuOpen, true);
  const close = new FakeElement('[data-spatial-command-close]');
  const action = new FakeElement('[data-spatial-command-action]');
  menu.querySelectorAll = () => [close, action];
  harness.documentRef.activeElement = action;
  const tab = await harness.documentRef.emit('keydown', { key: 'Tab', target: action });
  assert.equal(tab.defaultPrevented, true);
  assert.equal(close.focusCalls, 1);

  harness.documentRef.activeElement = host;
  const escape = await harness.documentRef.emit('keydown', { key: 'Escape', target: host });
  assert.equal(escape.defaultPrevented, true);
  assert.equal(harness.controller.commandMenuOpen, false);
  assert.equal(host.focusCalls, 1);

  const input = { tagName: 'INPUT', closest: () => null };
  harness.documentRef.activeElement = input;
  const inputShortcut = await harness.documentRef.emit('keydown', {
    key: 'k', ctrlKey: true, target: input,
  });
  assert.equal(inputShortcut.defaultPrevented, false);
  assert.equal(harness.controller.commandMenuOpen, false);

  const modalControl = {
    tagName: 'BUTTON',
    closest(selector) { return selector.includes('[aria-modal="true"]') ? this : null; },
  };
  harness.documentRef.activeElement = modalControl;
  const modalShortcut = await harness.documentRef.emit('keydown', {
    key: 'k', ctrlKey: true, target: modalControl,
  });
  assert.equal(modalShortcut.defaultPrevented, false);

  const editor = { tagName: 'DIV', isContentEditable: true, closest: () => null };
  harness.documentRef.activeElement = editor;
  const editShortcut = await harness.documentRef.emit('keydown', {
    key: 'k', ctrlKey: true, target: editor,
  });
  assert.equal(editShortcut.defaultPrevented, false);

  mount.options.onSelectionChange(taskElement('job:not-native'));
  await settle();
  harness.documentRef.activeElement = host;
  const unavailableShortcut = await harness.documentRef.emit('keydown', {
    key: 'k', ctrlKey: true, target: host,
  });
  assert.equal(unavailableShortcut.defaultPrevented, false);
  assert.equal(harness.controller.commandMenuOpen, false);
  harness.controller.destroy();
});

test('Canvas delete requires confirmation, traps focus, cancels cleanly and removes only the selected canvas', async () => {
  const harness = createHarness();
  harness.controller.bind();
  await activateAndOpen(harness, 'canvas:a');
  const trigger = harness.documentRef.node('#btn-spatial-delete');
  harness.documentRef.activeElement = trigger;

  assert.equal(harness.controller.openDeleteDialog('canvas:a', trigger), true);
  assert.equal(harness.controller.deleteDialogOpen, true);
  assert.match(harness.documentRef.node('#spatial-delete-title').textContent, /画布 A/);

  const cancel = harness.documentRef.node('#spatial-delete-cancel');
  const confirm = harness.documentRef.node('[data-spatial-delete-confirm]');
  harness.documentRef.activeElement = confirm;
  const tab = await harness.documentRef.emit('keydown', { key: 'Tab', target: confirm });
  assert.equal(tab.defaultPrevented, true);
  assert.equal(cancel.focusCalls, 2);

  const escape = await harness.documentRef.emit('keydown', { key: 'Escape', target: cancel });
  assert.equal(escape.defaultPrevented, true);
  assert.equal(harness.controller.deleteDialogOpen, false);
  assert.equal(harness.records.has('canvas:a'), true);
  assert.equal(trigger.focusCalls, 1);

  harness.controller.openDeleteDialog('canvas:a', trigger);
  await harness.documentRef.node('#page-canvas').emit('click', { target: confirm });
  assert.equal(harness.controller.deleteDialogOpen, false);
  assert.equal(harness.records.has('canvas:a'), false);
  assert.equal(harness.records.has('canvas:b'), true);
  assert.equal(harness.controller.currentId, '');
  assert.equal(harness.documentRef.node('#spatial-library').hidden, false);
  assert.equal(harness.documentRef.node('#spatial-editor').hidden, true);
  assert.match(harness.documentRef.node('#spatial-save-state').textContent, /历史任务与结果证据已保留/);
  harness.controller.destroy();
});

test('Canvas delete failure leaves the confirmation recoverable and succeeds on explicit retry', async () => {
  let attempts = 0;
  const harness = createHarness({
    removeCanvas({ id, record, records }) {
      attempts += 1;
      if (attempts === 1) throw new Error('temporary delete outage');
      records.delete(id);
      return { id, name: record.name, history_retained: true };
    },
  });
  harness.controller.bind();
  await activateAndOpen(harness, 'canvas:a');
  const trigger = harness.documentRef.node('#btn-spatial-delete');
  const confirm = harness.documentRef.node('[data-spatial-delete-confirm]');
  harness.controller.openDeleteDialog('canvas:a', trigger);

  await harness.documentRef.node('#page-canvas').emit('click', { target: confirm });
  assert.equal(harness.controller.deleteDialogOpen, true);
  assert.equal(harness.records.has('canvas:a'), true);
  assert.match(harness.documentRef.node('#spatial-delete-error').textContent, /temporary delete outage/);

  await harness.documentRef.node('#page-canvas').emit('click', { target: confirm });
  assert.equal(attempts, 2);
  assert.equal(harness.controller.deleteDialogOpen, false);
  assert.equal(harness.records.has('canvas:a'), false);
  harness.controller.destroy();
});

test('reopening a canvas restores its white-background task and result exactly once', async () => {
  const job = completedWhiteBackgroundJob('canvas:a');
  const harness = createHarness({
    mutateBusinessItems: true,
    api: {
      async getJobs() { return { jobs: [job] }; },
      async getAsset(assetId) {
        return {
          asset: {
            id: assetId,
            name: assetId === SOURCE_ASSET_ID ? '包装文字样品' : '白底图结果',
            kind: 'image',
            role: assetId === SOURCE_ASSET_ID ? 'workspace_source' : 'result_main',
            width: 320,
            height: 240,
            lineage_parent_id: assetId === SOURCE_ASSET_ID ? null : SOURCE_ASSET_ID,
          },
        };
      },
    },
  });
  const mountA = await activateAndOpen(harness, 'canvas:a');
  await waitFor(() => (
    harness.adapter.get('canvas:a').scene.elements.some((element) => (
      element.customData?.result_id === 'ast_white_background_result'
    ))
  ), 'white-background result was not recovered');

  assert.equal(mountA.calls.addBusinessItemsOnce.length, 2);
  const durable = harness.adapter.get('canvas:a').scene.elements;
  assert.equal(durable.filter((element) => element.customData?.task_id === job.id && !element.customData?.result_id).length, 1);
  assert.equal(durable.filter((element) => element.customData?.result_id === 'ast_white_background_result').length, 1);
  harness.controller.destroy();
});

test('reopening a canvas restores Result variation with the selected Result as lineage parent', async () => {
  const job = completedResultVariationJob('canvas:a');
  const harness = createHarness({
    mutateBusinessItems: true,
    api: {
      async getJobs() { return { jobs: [job] }; },
      async getAsset(assetId) {
        return {
          asset: {
            id: assetId,
            name: assetId === RESULT_ASSET_ID ? '现有结果' : '生图变体',
            kind: 'image',
            role: 'result_main',
            mime: 'image/png',
            width: 320,
            height: 240,
            lineage_parent_id: assetId === RESULT_ASSET_ID ? SOURCE_ASSET_ID : RESULT_ASSET_ID,
          },
        };
      },
    },
  });
  harness.records.get('canvas:a').scene = scene('result-canvas-a', [resultElement('canvas:a')]);
  const mountA = await activateAndOpen(harness, 'canvas:a');
  await waitFor(() => (
    harness.adapter.get('canvas:a').scene.elements.some((element) => (
      element.customData?.result_id === VARIATION_ASSET_ID
    ))
  ), 'Result variation was not recovered');

  assert.equal(mountA.calls.addBusinessItemsOnce.length, 2);
  const resultCall = mountA.calls.addBusinessItemsOnce.find((items) => (
    items.some((item) => item.references?.result_id === VARIATION_ASSET_ID)
  ));
  assert.ok(resultCall);
  assert.equal(resultCall[0].references.lineage_parent_id, RESULT_ASSET_ID);
  const durable = harness.adapter.get('canvas:a').scene.elements;
  assert.equal(durable.filter((element) => element.customData?.task_id === job.id && !element.customData?.result_id).length, 1);
  assert.equal(durable.filter((element) => element.customData?.result_id === VARIATION_ASSET_ID).length, 1);
  assert.equal(
    durable.find((element) => element.customData?.result_id === VARIATION_ASSET_ID)
      ?.customData?.lineage_parent_id,
    RESULT_ASSET_ID,
  );
  harness.controller.destroy();
});

test('a delayed A backfill response is discarded after B becomes current', async () => {
  const firstList = deferred();
  let listCalls = 0;
  const harness = createHarness({
    api: {
      async getJobs() {
        listCalls += 1;
        if (listCalls === 1) return firstList.promise;
        return { jobs: [] };
      },
    },
  });
  const mountA = await activateAndOpen(harness, 'canvas:a');
  await waitFor(() => listCalls === 1, 'A recovery scan did not start');
  await harness.controller.openCanvas('canvas:b');
  await settle();
  const mountB = harness.mounts.get('canvas:b');

  firstList.resolve({ jobs: [completedVideoJob('canvas:a')] });
  await settle(20);

  assert.equal(harness.controller.currentId, 'canvas:b');
  assert.equal(mountA.calls.addBusinessItemsOnce.length, 0);
  assert.equal(mountB.calls.addBusinessItemsOnce.length, 0);
  assert.equal(mountB.calls.updateTask.length, 0);
  harness.controller.destroy();
});

test('a 409 save preserves the newest local scene in an atomic conflict copy without overwriting remote', async () => {
  const firstSave = deferred();
  const harness = createHarness({
    updateScene({ record, updateCalls }) {
      return firstSave.promise;
    },
  });
  const mountA = await activateAndOpen(harness, 'canvas:a');
  const firstScene = scene('first-edit');
  const newestScene = scene('newest-edit');
  mountA.emitChange(firstScene);
  const flushing = harness.controller.flush('canvas:a');
  await waitFor(() => harness.updateCalls.length === 1, 'first scene save did not start');

  mountA.emitChange(newestScene);
  const record = harness.records.get('canvas:a');
  record.current_revision = 7;
  record.scene = scene('remote-revision-7');
  const conflict = new Error('revision conflict');
  conflict.status = 409;
  conflict.current = record;
  firstSave.reject(conflict);
  await flushing;
  assert.deepEqual(harness.clock.delays(), []);
  assert.equal(harness.updateCalls.length, 1);
  assert.equal(harness.updateCalls[0].scene, firstScene);
  assert.equal(harness.createCalls.length, 1);
  assert.equal(harness.createCalls[0].scene, newestScene);
  assert.match(harness.createCalls[0].name, /冲突副本/);
  assert.deepEqual(record.scene, scene('remote-revision-7'));
  assert.equal(record.current_revision, 7);
  const copy = [...harness.records.values()].find((item) => item.id.startsWith('canvas:copy:'));
  assert.deepEqual(copy.scene, newestScene);
  assert.deepEqual(mountA.calls.updateScene, [record.scene]);
  harness.controller.destroy();
});

test('a transient empty Excalidraw callback cannot overwrite a non-empty durable scene', async () => {
  const harness = createHarness();
  const mount = await activateAndOpen(harness, 'canvas:a');
  const durableScene = harness.records.get('canvas:a').scene;

  mount.emitChange(scene('unexpected-empty', []));
  await settle(20);

  assert.deepEqual(mount.calls.updateScene, [durableScene]);
  assert.equal(harness.updateCalls.length, 0);
  assert.equal(harness.clock.pendingCount(), 0);
  assert.equal(
    harness.documentRef.node('#spatial-save-state').textContent,
    '已阻止空场景覆盖 · 上一版本已恢复',
  );
  harness.controller.destroy();
});

test('a conflict copy never adopts video results owned by the original canvas', async () => {
  const firstSave = deferred();
  const job = completedVideoJob('canvas:a');
  let exposeCompletedJob = false;
  const getJobCalls = [];
  const harness = createHarness({
    api: {
      async getJob(jobId) {
        getJobCalls.push(jobId);
        return { job };
      },
      async getJobs() {
        return { jobs: exposeCompletedJob ? [job] : [] };
      },
    },
    updateScene() { return firstSave.promise; },
  });
  harness.controller.bind();
  const mountA = await activateAndOpen(harness, 'canvas:a');
  const localScene = scene('local-video-conflict', [
    sourceElement('canvas:a'),
    taskElement(job.id),
  ]);
  mountA.emitChange(localScene);
  const flushing = harness.controller.flush('canvas:a');
  await waitFor(() => harness.updateCalls.length === 1);

  const remote = harness.records.get('canvas:a');
  remote.current_revision = 11;
  remote.scene = scene('remote-revision-11');
  const conflict = new Error('revision conflict');
  conflict.status = 409;
  conflict.current = remote;
  firstSave.reject(conflict);
  await flushing;

  const copy = [...harness.records.values()].find((item) => item.id.startsWith('canvas:copy:'));
  assert.ok(copy, 'expected the local scene to be preserved as a conflict copy');
  assert.deepEqual(copy.scene, localScene);

  exposeCompletedJob = true;
  await harness.controller.openCanvas(copy.id);
  await settle(20);
  const copyMount = harness.mounts.get(copy.id);

  assert.deepEqual(getJobCalls, [job.id]);
  assert.equal(copyMount.calls.updateTask.length, 0);
  assert.equal(copyMount.calls.addBusinessItemsOnce.length, 0);
  assert.equal(copy.scene.elements.length, localScene.elements.length);
  harness.controller.destroy();
});

test('a failed conflict-copy request is retried with one stable id and blocks close until durable', async () => {
  let copyWritesAllowed = false;
  const firstSave = deferred();
  const harness = createHarness({
    createCanvas({ createdByRequest, options, records }) {
      if (!copyWritesAllowed) throw new Error('temporary conflict-copy outage');
      const requestId = String(options.clientRequestId);
      if (createdByRequest.has(requestId)) return records.get(createdByRequest.get(requestId));
      const record = {
        id: 'canvas:copy:stable',
        name: options.name,
        current_revision: 1,
        last_opened_at: '2026-09-02T12:00:00.000Z',
        summary: { element_count: 0 },
        scene: options.scene,
      };
      records.set(record.id, record);
      createdByRequest.set(requestId, record.id);
      return record;
    },
    updateScene() { return firstSave.promise; },
  });
  const mountA = await activateAndOpen(harness, 'canvas:a');
  const localScene = scene('local-conflict-draft');
  mountA.emitChange(localScene);
  const flushing = harness.controller.flush('canvas:a');
  await waitFor(() => harness.updateCalls.length === 1);
  const remote = harness.records.get('canvas:a');
  remote.current_revision = 8;
  remote.scene = scene('remote-revision-8');
  const conflict = new Error('revision conflict');
  conflict.status = 409;
  conflict.current = remote;
  firstSave.reject(conflict);
  assert.equal(await flushing, null);

  await assert.rejects(
    harness.controller.prepareForClose(),
    (error) => error.code === 'SPATIAL_CANVAS_SAVE_PENDING',
  );
  assert.equal(harness.createCalls.length, 2);
  assert.equal(
    harness.createCalls[0].clientRequestId,
    harness.createCalls[1].clientRequestId,
  );

  copyWritesAllowed = true;
  const retryCopy = new FakeElement('[data-spatial-recovery]');
  retryCopy.dataset.spatialRecovery = 'retry-conflict-copy';
  await harness.documentRef.node('#page-canvas').emit('click', { target: retryCopy });
  assert.equal(await harness.controller.prepareForClose(), true);
  assert.equal(harness.createCalls.length, 3);
  assert.equal(
    harness.createCalls[1].clientRequestId,
    harness.createCalls[2].clientRequestId,
  );
  assert.deepEqual(harness.records.get('canvas:copy:stable').scene, localScene);
  assert.deepEqual(remote.scene, scene('remote-revision-8'));
  harness.controller.destroy();
});

test('an onChange during conflict-copy creation updates the same copy before remote reload', async () => {
  const firstSave = deferred();
  const copyCreate = deferred();
  const harness = createHarness({
    createCanvas() { return copyCreate.promise; },
    updateScene({ call, record }) {
      if (call.id === 'canvas:a') return firstSave.promise;
      record.scene = call.scene;
      record.current_revision += 1;
      return record;
    },
  });
  const mountA = await activateAndOpen(harness, 'canvas:a');
  const firstLocal = scene('local-before-conflict-copy');
  const latestLocal = scene('local-during-conflict-copy');
  mountA.emitChange(firstLocal);
  const flushing = harness.controller.flush('canvas:a');
  await waitFor(() => harness.updateCalls.length === 1);
  const remote = harness.records.get('canvas:a');
  remote.current_revision = 9;
  remote.scene = scene('remote-revision-9');
  const conflict = new Error('revision conflict');
  conflict.status = 409;
  conflict.current = remote;
  firstSave.reject(conflict);
  await waitFor(() => harness.createCalls.length === 1);

  mountA.emitChange(latestLocal);
  const copyRecord = {
    id: 'canvas:copy:single',
    name: harness.createCalls[0].name,
    current_revision: 1,
    last_opened_at: '2026-09-02T12:00:00.000Z',
    summary: { element_count: 0 },
    scene: harness.createCalls[0].scene,
  };
  harness.records.set(copyRecord.id, copyRecord);
  copyCreate.resolve(copyRecord);
  await flushing;

  assert.equal(harness.createCalls.length, 1);
  assert.equal(harness.updateCalls.length, 2);
  assert.equal(harness.updateCalls[1].id, copyRecord.id);
  assert.deepEqual(copyRecord.scene, latestLocal);
  assert.deepEqual(remote.scene, scene('remote-revision-9'));
  assert.deepEqual(mountA.calls.updateScene, [remote.scene]);
  assert.equal(harness.clock.pendingCount(), 0);
  harness.controller.destroy();
});

test('a permanent conflict-copy reference error offers confirmed remote reload as an escape hatch', async () => {
  const firstSave = deferred();
  const harness = createHarness({
    createCanvas() {
      const error = new Error('referenced asset no longer exists');
      error.status = 404;
      throw error;
    },
    updateScene() { return firstSave.promise; },
  });
  harness.controller.bind();
  const mountA = await activateAndOpen(harness, 'canvas:a');
  const localScene = scene('local-with-deleted-reference');
  mountA.emitChange(localScene);
  const flushing = harness.controller.flush('canvas:a');
  await waitFor(() => harness.updateCalls.length === 1);
  const remote = harness.records.get('canvas:a');
  remote.current_revision = 10;
  remote.scene = scene('remote-revision-10');
  const conflict = new Error('revision conflict');
  conflict.status = 409;
  conflict.current = remote;
  firstSave.reject(conflict);
  assert.equal(await flushing, null);
  assert.match(
    harness.documentRef.node('#spatial-editor-loading').innerHTML,
    /data-spatial-conflict-discard/,
  );

  await harness.documentRef.node('#page-canvas').emit('click', {
    target: harness.documentRef.node('[data-spatial-conflict-discard]'),
  });
  assert.equal(await harness.controller.prepareForClose(), true);
  assert.deepEqual(mountA.calls.updateScene, [remote.scene]);
  assert.equal(harness.clock.pendingCount(), 0);
  assert.equal(
    harness.documentRef.node('#spatial-save-state').textContent,
    '已放弃本地冲突修改 · 已载入远端最新版本',
  );
  harness.controller.destroy();
});

test('a failed A save aborts switching to B and destroy retries the pending A scene', async () => {
  let allowSave = false;
  const harness = createHarness({
    updateScene({ call, record }) {
      if (!allowSave) throw new Error('temporary canvas save outage');
      record.scene = call.scene;
      record.current_revision += 1;
      return record;
    },
  });
  const mountA = await activateAndOpen(harness, 'canvas:a');
  const pendingScene = scene('pending-a-before-switch');
  mountA.emitChange(pendingScene);

  const switched = await harness.controller.openCanvas('canvas:b');
  await settle();

  assert.equal(switched, false);
  assert.equal(harness.controller.currentId, 'canvas:a');
  assert.equal(mountA.calls.unmount, 0);
  assert.equal(harness.mounts.has('canvas:b'), false);
  assert.equal(harness.updateCalls.length, 1);
  assert.notDeepEqual(harness.records.get('canvas:a').scene, pendingScene);
  assert.match(harness.documentRef.node('#spatial-save-state').textContent, /保存失败，已留在当前画布/);
  assert.match(harness.documentRef.node('#spatial-save-state').textContent, /未提交修改仍保留/);
  assert.equal(harness.documentRef.node('#spatial-recovery-action').dataset.spatialRecovery, 'retry-save');

  allowSave = true;
  harness.controller.destroy();
  await harness.controller.flush('canvas:a');

  assert.equal(harness.updateCalls.length, 2);
  assert.deepEqual(harness.records.get('canvas:a').scene, pendingScene);
  assert.equal(mountA.calls.unmount, 1);
});

test('prepareForClose rejects without discarding pending scenes and succeeds on retry', async () => {
  let allowSave = false;
  const harness = createHarness({
    updateScene({ call, record }) {
      if (!allowSave) throw new Error('temporary canvas save outage');
      record.scene = call.scene;
      record.current_revision += 1;
      return record;
    },
  });
  const mountA = await activateAndOpen(harness, 'canvas:a');
  const pendingScene = scene('pending-a-before-close');
  mountA.emitChange(pendingScene);

  await assert.rejects(
    harness.controller.prepareForClose(),
    (error) => {
      assert.equal(error.code, 'SPATIAL_CANVAS_SAVE_PENDING');
      assert.deepEqual(error.canvasIds, ['canvas:a']);
      return true;
    },
  );
  assert.equal(harness.updateCalls.length, 1);
  assert.notDeepEqual(harness.records.get('canvas:a').scene, pendingScene);

  allowSave = true;
  assert.equal(await harness.controller.prepareForClose(), true);
  assert.equal(harness.updateCalls.length, 2);
  assert.deepEqual(harness.records.get('canvas:a').scene, pendingScene);
  harness.controller.destroy();
});

test('transient getJobs failures use one exponential-backoff timer and recover', async () => {
  let listCalls = 0;
  const harness = createHarness({
    api: {
      async getJobs() {
        listCalls += 1;
        if (listCalls <= 2) throw new Error('temporary ledger outage');
        return { jobs: [] };
      },
    },
  });
  await activateAndOpen(harness, 'canvas:a');
  await waitFor(() => listCalls === 1 && harness.clock.pendingCount() === 1);
  assert.deepEqual(harness.clock.delays(), [1200]);

  assert.equal(await harness.clock.runNext(), 1200);
  await waitFor(() => listCalls === 2 && harness.clock.pendingCount() === 1);
  assert.deepEqual(harness.clock.delays(), [2400]);

  assert.equal(await harness.clock.runNext(), 2400);
  await waitFor(() => listCalls === 3);
  assert.equal(harness.clock.pendingCount(), 0);
  harness.controller.destroy();
});

test('a rejected stale inspector request cannot overwrite the newer selection', async () => {
  const staleAsset = deferred();
  const assetA = 'ast_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa';
  const assetB = 'ast_bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb';
  const harness = createHarness({
    api: {
      async getAsset(assetId) {
        if (assetId === assetA) return staleAsset.promise;
        return { asset: { id: assetId, name: '素材 B', kind: 'image', role: 'source', width: 640, height: 480 } };
      },
    },
  });
  const mount = await activateAndOpen(harness, 'canvas:a');
  mount.options.onSelectionChange(imageElement('image-a', assetA));
  await settle();
  mount.options.onSelectionChange(imageElement('image-b', assetB));
  await settle();
  staleAsset.reject(new Error('stale asset failed'));
  await settle(20);

  const inspector = harness.documentRef.node('#spatial-inspector');
  assert.match(inspector.innerHTML, /素材 B/);
  assert.doesNotMatch(inspector.innerHTML, /image-a|aaaaaaaa/);
  harness.controller.destroy();
});

test('changing the selected source while submit is preparing cancels the old command', async () => {
  const preparingSubmit = deferred();
  let sourceReads = 0;
  const executeCalls = [];
  const otherAssetId = 'ast_cccccccccccccccccccccccccccccccc';
  const harness = createHarness({
    api: {
      async getAsset(assetId) {
        if (assetId === SOURCE_ASSET_ID) {
          sourceReads += 1;
          if (sourceReads === 3) return preparingSubmit.promise;
        }
        return { asset: { id: assetId, name: '测试素材', kind: 'image', role: 'source', width: 320, height: 240 } };
      },
      async executeCommand(commandId, payload) {
        executeCalls.push({ commandId, payload });
        return { job: completedVideoJob('canvas:a') };
      },
    },
  });
  harness.controller.bind();
  const mount = await activateAndOpen(harness, 'canvas:a');
  const source = sourceElement('canvas:a');
  mount.options.onSelectionChange(source);
  await settle();
  await harness.controller.openVideoComposer({ canvasId: 'canvas:a', element: source });

  const confirmation = harness.documentRef.node('[data-spatial-video-confirm]');
  confirmation.checked = true;
  await harness.documentRef.node('#page-canvas').emit('change', { target: confirmation });
  await harness.documentRef.node('#page-canvas').emit('submit', {
    target: harness.documentRef.node('[data-spatial-video-form]'),
  });
  await waitFor(() => sourceReads === 3, 'submit preparation did not begin');
  mount.options.onSelectionChange(imageElement('other-image', otherAssetId));
  await settle();
  preparingSubmit.resolve({ asset: { id: SOURCE_ASSET_ID, name: '旧素材', kind: 'image', role: 'source' } });
  await settle(20);

  assert.equal(executeCalls.length, 0);
  harness.controller.destroy();
});

test('a transient per-task get failure enters recovery even when the 200-job list succeeds', async () => {
  let taskReads = 0;
  const harness = createHarness({
    api: {
      async getJob() {
        taskReads += 1;
        throw new Error('temporary task lookup failure');
      },
      async getJobs() { return { jobs: [] }; },
    },
  });
  harness.records.get('canvas:a').scene.elements.push(taskElement());
  await activateAndOpen(harness, 'canvas:a');
  await waitFor(() => taskReads === 1 && harness.clock.pendingCount() === 1);

  assert.deepEqual(harness.clock.delays(), [1200]);
  assert.equal(harness.controller.videoRecoveryPending, true);
  harness.controller.destroy();
});

test('transient recovery pauses after eight retries and page re-entry starts a fresh attempt', async () => {
  let listCalls = 0;
  const harness = createHarness({
    api: {
      async getJobs() {
        listCalls += 1;
        throw new Error('persistent temporary outage');
      },
    },
  });
  await activateAndOpen(harness, 'canvas:a');
  const observedDelays = [];
  for (let attempt = 0; attempt < 8; attempt += 1) observedDelays.push(await harness.clock.runNext());

  assert.deepEqual(observedDelays, [1200, 2400, 4800, 9600, 19200, 30000, 30000, 30000]);
  assert.equal(harness.clock.pendingCount(), 0);
  assert.equal(harness.controller.videoRecoveryPending, false);
  assert.equal(
    harness.documentRef.node('#spatial-save-state').textContent,
    '恢复已暂停，重新进入画布或任务中心重试',
  );

  harness.controller.setPage(false);
  harness.controller.setPage(true);
  await waitFor(() => listCalls === 10 && harness.clock.pendingCount() === 1);
  assert.deepEqual(harness.clock.delays(), [1200]);
  harness.controller.destroy();
});

test('a missing task is permanent, shows the task-center action and schedules no retry', async () => {
  const missing = new Error('task not found');
  missing.status = 404;
  const harness = createHarness({
    api: {
      async getJob() { throw missing; },
      async getJobs() { return { jobs: [] }; },
    },
  });
  harness.records.get('canvas:a').scene.elements.push(taskElement('job:missing'));
  await activateAndOpen(harness, 'canvas:a');
  await settle(20);

  assert.equal(harness.clock.pendingCount(), 0);
  assert.equal(harness.controller.videoRecoveryPending, false);
  assert.match(harness.documentRef.node('#spatial-save-state').textContent, /任务中心处理/);
  harness.controller.destroy();
});

test('a completed job without result ids is a permanent contract failure', async () => {
  const job = { ...completedVideoJob('canvas:a'), items: [] };
  const harness = createHarness({
    api: {
      async getJob() { return { job }; },
      async getJobs() { return { jobs: [job] }; },
    },
  });
  harness.records.get('canvas:a').scene.elements.push(taskElement(job.id));
  await activateAndOpen(harness, 'canvas:a');
  await settle(20);

  assert.equal(harness.clock.pendingCount(), 0);
  assert.match(harness.documentRef.node('#spatial-save-state').textContent, /缺少结果合同/);
  assert.match(harness.documentRef.node('#spatial-save-state').textContent, /任务中心处理/);
  harness.controller.destroy();
});

test('a 404 result asset is permanent and does not consume the transient retry budget', async () => {
  const job = completedVideoJob('canvas:a');
  const missingAssetId = job.items[0].result_asset_ids[0];
  const harness = createHarness({
    api: {
      async getAsset(assetId) {
        if (assetId === missingAssetId) {
          const error = new Error('result asset not found');
          error.status = 404;
          throw error;
        }
        return { asset: { id: assetId, name: '测试素材', kind: 'image', role: 'source' } };
      },
      async getJob() { return { job }; },
      async getJobs() { return { jobs: [job] }; },
    },
  });
  harness.records.get('canvas:a').scene.elements.push(taskElement(job.id));
  await activateAndOpen(harness, 'canvas:a');
  await settle(20);

  assert.equal(harness.clock.pendingCount(), 0);
  assert.equal(harness.controller.videoRecoveryPending, false);
  assert.match(harness.documentRef.node('#spatial-save-state').textContent, /结果素材已不存在/);
  harness.controller.destroy();
});

test('an interrupted video job opens a fresh confirmation draft instead of polling forever', async () => {
  const harness = createHarness();
  const job = {
    id: 'job:interrupted',
    mode: 'single',
    status: 'interrupted',
    parameters: { first_frame_asset_id: SOURCE_ASSET_ID },
    snapshot: {
      command_id: 'command:image-to-video',
      source_asset_ids: [SOURCE_ASSET_ID],
      parameters: { first_frame_asset_id: SOURCE_ASSET_ID },
    },
    items: [],
  };
  harness.records.get('canvas:a').scene.elements.push(taskElement(job.id));
  await activateAndOpen(harness, 'canvas:a');
  const draft = await harness.controller.openVideoJob(job);

  assert.equal(draft.sourceAssetId, SOURCE_ASSET_ID);
  assert.equal(draft.callConfirmed, false);
  assert.equal(harness.clock.pendingCount(), 0);
  harness.controller.destroy();
});

test('a rejected runtime loader is cleared so the retry button can load a fresh runtime', async () => {
  let runtimeCalls = 0;
  let succeedingRuntime;
  const harness = createHarness({
    runtimeLoader: async () => {
      runtimeCalls += 1;
      if (runtimeCalls === 1) throw new Error('temporary chunk load failure');
      return succeedingRuntime;
    },
  });
  succeedingRuntime = createFakeRuntime().runtime;
  harness.controller.setPage(true);
  await settle();
  await harness.controller.openCanvas('canvas:a');
  assert.equal(runtimeCalls, 1);
  assert.equal(harness.controller.runtimeLoaded, false);

  await harness.controller.openCanvas('canvas:a');
  await settle();
  assert.equal(runtimeCalls, 2);
  assert.equal(harness.controller.runtimeLoaded, true);
  assert.equal(harness.documentRef.node('#spatial-editor-loading').hidden, true);
  harness.controller.destroy();
});

test('a failed canvas-list read renders an error instead of an empty library and its action reloads records', async () => {
  const harness = createHarness();
  let loadCalls = 0;
  harness.adapter.load = async () => {
    loadCalls += 1;
    if (loadCalls === 1) throw new Error('injected sqlite list failure');
    return harness.adapter.list();
  };
  harness.controller.bind();
  harness.controller.setPage(true);
  await waitFor(() => loadCalls === 1);
  await settle();

  assert.equal(harness.documentRef.node('#spatial-canvas-list').hidden, true);
  assert.equal(harness.documentRef.node('#spatial-library-empty-title').textContent, '画布列表读取失败');
  assert.match(harness.documentRef.node('#spatial-library-empty-detail').textContent, /injected sqlite list failure/);
  const action = harness.documentRef.node('#btn-spatial-empty-new');
  assert.equal(action.dataset.spatialEmptyAction, 'retry-list');

  await action.emit('click');
  await waitFor(() => loadCalls === 2);
  assert.equal(harness.documentRef.node('#spatial-canvas-list').hidden, false);
  assert.equal(harness.documentRef.node('#spatial-library-empty').hidden, true);
  assert.equal(harness.documentRef.node('#spatial-recovery-action').hidden, true);
  harness.controller.destroy();
});

test('the visible spatial save action retries the retained scene without touching another canvas', async () => {
  let saveAllowed = false;
  const harness = createHarness({
    updateScene({ call, record }) {
      if (!saveAllowed) throw new Error('injected spatial save failure');
      record.scene = call.scene;
      record.current_revision += 1;
      return record;
    },
  });
  harness.controller.bind();
  const mount = await activateAndOpen(harness, 'canvas:a');
  const retained = scene('retained-for-visible-retry');
  mount.emitChange(retained);
  await harness.clock.advance(240);
  const action = harness.documentRef.node('#spatial-recovery-action');
  assert.equal(action.dataset.spatialRecovery, 'retry-save');
  assert.notDeepEqual(harness.records.get('canvas:a').scene, retained);

  saveAllowed = true;
  await harness.documentRef.node('#page-canvas').emit('click', { target: action });
  assert.deepEqual(harness.records.get('canvas:a').scene, retained);
  assert.equal(harness.records.get('canvas:b').current_revision, 1);
  assert.equal(action.hidden, true);
  harness.controller.destroy();
});
