import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import test from 'node:test';
import { fileURLToPath } from 'node:url';

import {
  APP_CLOSE_SAVE_TIMEOUT,
  createAppCloseCoordinator,
} from '../../src/js/app-close-lifecycle.js';
import * as API from '../../src/js/api.js';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..');
const read = (relativePath) => fs.readFileSync(path.join(root, relativePath), 'utf8');

function closeEvent(onPrevent = () => {}) {
  return { preventDefault: onPrevent };
}

test('close is prevented synchronously before any save work starts', async () => {
  let prevented = false;
  let started = false;
  let completed = 0;
  const coordinator = createAppCloseCoordinator({
    onStart: () => {
      assert.equal(prevented, true);
      started = true;
    },
    prepareForClose: () => {
      assert.equal(prevented, true);
      assert.equal(started, true);
      return true;
    },
    completeClose: () => { completed += 1; },
  });

  const closing = coordinator.handleCloseRequested(closeEvent(() => { prevented = true; }));
  assert.equal(prevented, true);
  assert.equal(await closing, true);
  assert.equal(completed, 1);
});

test('repeated close clicks share one save and one final shutdown', async () => {
  let releaseSave;
  let prepareCalls = 0;
  let completeCalls = 0;
  let startCalls = 0;
  let prevented = 0;
  const coordinator = createAppCloseCoordinator({
    onStart: () => { startCalls += 1; },
    prepareForClose: () => {
      prepareCalls += 1;
      return new Promise((resolve) => { releaseSave = resolve; });
    },
    completeClose: () => { completeCalls += 1; },
  });

  const first = coordinator.handleCloseRequested(closeEvent(() => { prevented += 1; }));
  const second = coordinator.handleCloseRequested(closeEvent(() => { prevented += 1; }));
  assert.equal(first, second);
  await Promise.resolve();
  assert.equal(prepareCalls, 1);
  assert.equal(startCalls, 1);
  releaseSave(true);
  assert.equal(await first, true);
  assert.equal(prevented, 2);
  assert.equal(completeCalls, 1);
});

test('save failure keeps the window open and a later close can retry', async () => {
  let prepareCalls = 0;
  let completeCalls = 0;
  const failures = [];
  const coordinator = createAppCloseCoordinator({
    prepareForClose: () => {
      prepareCalls += 1;
      if (prepareCalls === 1) {
        const error = new Error('ledger unavailable');
        error.code = 'SPATIAL_CANVAS_SAVE_PENDING';
        error.canvasIds = ['canvas-a'];
        throw error;
      }
      return true;
    },
    completeClose: () => { completeCalls += 1; },
    onFailure: (error) => { failures.push(error); },
  });

  assert.equal(await coordinator.handleCloseRequested(closeEvent()), false);
  assert.equal(completeCalls, 0);
  assert.equal(failures[0].code, 'SPATIAL_CANVAS_SAVE_PENDING');
  assert.deepEqual(failures[0].canvasIds, ['canvas-a']);

  assert.equal(await coordinator.handleCloseRequested(closeEvent()), true);
  assert.equal(prepareCalls, 2);
  assert.equal(completeCalls, 1);
});

test('a save timeout does not force exit and resets the coordinator for retry', async () => {
  let prepareCalls = 0;
  let completeCalls = 0;
  const failures = [];
  const coordinator = createAppCloseCoordinator({
    timeoutMs: 15,
    prepareForClose: () => {
      prepareCalls += 1;
      return prepareCalls === 1 ? new Promise(() => {}) : true;
    },
    completeClose: () => { completeCalls += 1; },
    onFailure: (error) => { failures.push(error); },
  });

  assert.equal(await coordinator.handleCloseRequested(closeEvent()), false);
  assert.equal(failures[0].code, APP_CLOSE_SAVE_TIMEOUT);
  assert.equal(completeCalls, 0);
  assert.equal(await coordinator.handleCloseRequested(closeEvent()), true);
  assert.equal(prepareCalls, 2);
  assert.equal(completeCalls, 1);
});

test('a final shutdown transport error also leaves close retryable', async () => {
  let completeCalls = 0;
  const coordinator = createAppCloseCoordinator({
    prepareForClose: () => true,
    completeClose: () => {
      completeCalls += 1;
      if (completeCalls === 1) throw new Error('invoke failed');
    },
  });

  assert.equal(await coordinator.handleCloseRequested(closeEvent()), false);
  assert.equal(await coordinator.handleCloseRequested(closeEvent()), true);
  assert.equal(completeCalls, 2);
});

test('browser development mode exposes harmless close lifecycle fallbacks', async () => {
  const unlisten = await API.onAppCloseRequested(() => {});
  assert.equal(typeof unlisten, 'function');
  assert.equal(await API.completeAppClose(), false);
  assert.equal(await API.closeApp(), false);
});

test('application wiring saves before exit and both custom close buttons share the request path', () => {
  const app = read('src/js/app.js');
  const api = read('src/js/api.js');

  assert.match(app, /onStart:\s*\(\)\s*=>\s*setAppCloseInteractionLocked\(true\)/);
  assert.match(app, /await infiniteCanvasWorkspace\.prepareForClose\(\)[\s\S]{0,180}return infiniteCanvasWorkspace\.prepareForClose\(\)/);
  assert.match(app, /body\.inert = Boolean\(locked\)/);
  assert.match(app, /setAppCloseInteractionLocked\(false\)/);
  assert.match(app, /completeClose:\s*\(\)\s*=>\s*API\.completeAppClose\(\)/);
  assert.match(app, /API\.onAppCloseRequested\(appCloseCoordinator\.handleCloseRequested\)/);
  assert.match(app, /btn-close-dot'\)\.addEventListener\('click', requestAppClose\)/);
  assert.match(app, /btn-spatial-close'\)\.addEventListener\('click', requestAppClose\)/);
  assert.match(app, /窗口未关闭，最后修改尚未保存/);
  assert.match(api, /appWindow\.onCloseRequested\(handler\)/);
  assert.match(api, /invoke\('complete_close_app'\)/);
});

test('Rust requests a normal close and only tears down the sidecar after approval or destruction', () => {
  const rust = read('src-tauri/src/main.rs');
  const closeStart = rust.indexOf('fn close_app(');
  const closeEnd = rust.indexOf('fn stop_sidecar_in_slot(', closeStart);
  const shutdownStart = rust.indexOf('fn shutdown_sidecar(', closeStart);
  const closeCommand = rust.slice(closeStart, closeEnd);
  const completeStart = rust.indexOf('fn complete_close_app(', shutdownStart);
  const completeEnd = rust.indexOf('fn base64_decode(', completeStart);
  const completeCommand = rust.slice(completeStart, completeEnd);
  const eventStart = rust.indexOf('.on_window_event(');
  const eventEnd = rust.indexOf('.run(', eventStart);
  const eventHandler = rust.slice(eventStart, eventEnd);

  assert.match(closeCommand, /window\.close\(\)/);
  assert.doesNotMatch(closeCommand, /python_child|app\.exit/);
  assert.match(completeCommand, /shutdown_sidecar\(&state\)/);
  assert.match(completeCommand, /app\.exit\(0\)/);
  assert.match(rust, /while state\.sidecar_starting\.load\(Ordering::SeqCst\)/);
  assert.match(rust, /stop_sidecar_in_slot\(state\);[\s\S]{0,400}stop_sidecar_in_slot\(state\);/);
  assert.doesNotMatch(eventHandler, /CloseRequested/);
  assert.match(eventHandler, /WindowEvent::Destroyed if window\.label\(\) == "main"/);
  assert.match(eventHandler, /shutdown_sidecar\(&state\)/);
});
