import test from 'node:test';
import assert from 'node:assert/strict';

import * as API from '../../src/js/api.js';

test('job polling request aborts locally on timeout without touching a real network', async () => {
  const originalFetch = globalThis.fetch;
  globalThis.fetch = (_url, options) => new Promise((_resolve, reject) => {
    const abort = () => {
      const error = new Error('aborted');
      error.name = 'AbortError';
      reject(error);
    };
    if (options.signal.aborted) abort();
    else options.signal.addEventListener('abort', abort, { once: true });
  });
  try {
    await assert.rejects(
      API.getJobs(100, { timeoutMs: 10 }),
      (error) => error?.code === 'REQUEST_TIMEOUT',
    );
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test('structured HTTP status remains available for idempotency retry decisions', async () => {
  const originalFetch = globalThis.fetch;
  globalThis.fetch = async () => ({
    ok: false,
    status: 409,
    text: async () => JSON.stringify({ detail: { message: 'idempotency conflict' } }),
  });
  try {
    await assert.rejects(
      API.createJob({ mode: 'single', source_asset_ids: ['asset-1'] }),
      (error) => error?.status === 409 && /idempotency conflict/.test(error.message),
    );
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test('pause and resume job controls use the durable POST endpoints', async () => {
  const originalFetch = globalThis.fetch;
  const requests = [];
  globalThis.fetch = async (url, options) => {
    requests.push({ url, method: options?.method });
    return { ok: true, json: async () => ({ ok: true }) };
  };
  try {
    await API.pauseJob('job/one');
    await API.resumeJob('job/one');
    assert.deepEqual(requests, [
      { url: 'http://127.0.0.1:8765/api/jobs/job%2Fone/pause', method: 'POST' },
      { url: 'http://127.0.0.1:8765/api/jobs/job%2Fone/resume', method: 'POST' },
    ]);
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test('product profile APIs encode IDs and preserve optimistic revision payloads', async () => {
  const originalFetch = globalThis.fetch;
  const requests = [];
  globalThis.fetch = async (url, options = {}) => {
    requests.push({ url, method: options.method || 'GET', body: options.body || '' });
    return { ok: true, json: async () => ({ profiles: [], versions: [], profile: {} }) };
  };
  try {
    await API.getProductProfiles(25);
    await API.getProductProfile('profile/sku one');
    await API.getProductProfileVersions('profile/sku one', 12);
    await API.getProductProfileVersion('version/one');
    await API.saveProductProfile('profile/sku one', {
      expected_revision: 3,
      client_request_id: 'save-1',
      profile: { id: 'profile/sku one' },
    });
    assert.deepEqual(requests.map((item) => [item.url, item.method]), [
      ['http://127.0.0.1:8765/api/product-profiles?limit=25', 'GET'],
      ['http://127.0.0.1:8765/api/product-profiles/profile%2Fsku%20one', 'GET'],
      ['http://127.0.0.1:8765/api/product-profiles/profile%2Fsku%20one/versions?limit=12', 'GET'],
      ['http://127.0.0.1:8765/api/product-profile-versions/version%2Fone', 'GET'],
      ['http://127.0.0.1:8765/api/product-profiles/profile%2Fsku%20one', 'PUT'],
    ]);
    assert.equal(JSON.parse(requests.at(-1).body).expected_revision, 3);
  } finally {
    globalThis.fetch = originalFetch;
  }
});
