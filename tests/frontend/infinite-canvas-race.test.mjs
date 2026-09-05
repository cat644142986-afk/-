import assert from 'node:assert/strict';
import test from 'node:test';

import { createInfiniteCanvasWorkspaceController } from '../../src/js/infinite-canvas-workspace.js';

const SOURCE_ASSET_ID = 'ast_0123456789abcdef0123456789abcdef';

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

  focus() {}
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
        preventDefault() { this.defaultPrevented = true; },
        stopPropagation() {},
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

function createFakeAdapter({ createCanvas, updateScene } = {}) {
  const records = new Map(['canvas:a', 'canvas:b'].map((id) => [id, {
    id,
    name: id === 'canvas:a' ? '画布 A' : '画布 B',
    current_revision: 1,
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
    async updateScene(id, value) {
      const record = adapter.get(id);
      const call = { id, revision: record?.current_revision, scene: value };
      updateCalls.push(call);
      if (updateScene) return updateScene({ call, record, records, updateCalls });
      record.scene = value;
      record.current_revision += 1;
      return record;
    },
  };
  return { adapter, createCalls, createdByRequest, records, updateCalls };
}

function createFakeRuntime() {
  const mounts = new Map();
  return {
    mounts,
    runtime: {
      mountInfiniteCanvas(_host, options) {
        const canvasId = options.canvasDocument.id;
        const calls = {
          addBusinessItems: [],
          addBusinessItemsOnce: [],
          selectBusinessReference: [],
          updateScene: [],
          updateTask: [],
          unmount: 0,
        };
        let currentScene = options.canvasDocument.scene;
        const island = {
          getScene: () => currentScene,
          async addBusinessItems(items) {
            calls.addBusinessItems.push(items);
            return { skipped: false };
          },
          async addBusinessItemsOnce(items) {
            calls.addBusinessItemsOnce.push(items);
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
          stopVideo() {},
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

function createHarness({ api: apiOverrides = {}, createCanvas, onImportFiles, updateScene, runtimeLoader } = {}) {
  const documentRef = createFakeDocument();
  const clock = createFakeWindow();
  const adapterState = createFakeAdapter({ createCanvas, updateScene });
  const runtimeState = createFakeRuntime();
  const api = {
    async getAsset(assetId) {
      return {
        asset: {
          id: assetId,
          name: '测试商品',
          kind: assetId === SOURCE_ASSET_ID ? 'image' : 'video',
          role: assetId === SOURCE_ASSET_ID ? 'source' : 'result_video',
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
    onImportFiles,
    runtimeLoader: runtimeLoader || (async () => runtimeState.runtime),
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
  assert.match(harness.documentRef.node('#spatial-save-state').textContent, /保存失败/);
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
  assert.equal(transfer.dropEffect, 'copy');
  assert.equal(harness.documentRef.node('#spatial-canvas-host').dataset.fileDropActive, 'true');
  const drop = await harness.documentRef.emit('drop', { dataTransfer: transfer });

  assert.equal(drop.defaultPrevented, true);
  assert.equal(harness.documentRef.node('#spatial-canvas-host').dataset.fileDropActive, 'false');
  assert.deepEqual(importCalls, [[file]]);
  assert.deepEqual(mount.calls.addBusinessItems, [[importedItem]]);
  assert.match(harness.documentRef.node('#spatial-save-state').textContent, /已加入|正在保存/);
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
  assert.equal(
    harness.documentRef.node('#spatial-save-state').textContent,
    '保存失败 · 已留在当前画布，请重试',
  );

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
