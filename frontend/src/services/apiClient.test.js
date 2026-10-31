import assert from 'node:assert/strict';
import { test } from 'node:test';

import { ApiError, createApiClient, errorMessageFrom, resolveApiBase } from './apiClient.js';

/** A scripted fetch: each rule answers the calls whose "METHOD path" it matches, in order. */
function scriptedFetch(rules) {
  const calls = [];
  const queues = Object.fromEntries(Object.entries(rules).map(([key, answers]) => [key, [...answers]]));
  const fetchImpl = async (url, init = {}) => {
    const method = (init.method || 'GET').toUpperCase();
    const path = url.replace(/^https?:\/\/[^/]+/, '');
    calls.push({ method, path, init });
    const queue = queues[`${method} ${path}`];
    if (!queue || queue.length === 0) throw new Error(`unexpected call: ${method} ${path}`);
    const answer = queue.length > 1 ? queue.shift() : queue[0];
    if (answer instanceof Error) throw answer;
    return answer;
  };
  return { fetchImpl, calls };
}

const json = (body, status = 200) =>
  new Response(JSON.stringify(body), { status, headers: { 'content-type': 'application/json' } });

const noSleep = async () => {};

function client(rules, options = {}) {
  const scripted = scriptedFetch(rules);
  return { ...scripted, api: createApiClient({ fetchImpl: scripted.fetchImpl, sleep: noSleep, ...options }) };
}

test('resolveApiBase: env value, dev default, same origin in production', () => {
  assert.equal(resolveApiBase({ VITE_API_BASE: 'https://api.example.com//' }), 'https://api.example.com');
  assert.equal(resolveApiBase({ DEV: true }), 'http://localhost:5000');
  assert.equal(resolveApiBase({ DEV: false }), '');
  assert.equal(resolveApiBase({ VITE_API_BASE: '', DEV: true }), '');
});

test('errorMessageFrom handles strings, validation lists and nothing', () => {
  assert.equal(errorMessageFrom({ detail: 'Nope' }, 400), 'Nope');
  assert.equal(errorMessageFrom({ detail: [{ msg: 'a' }, { msg: 'b' }] }, 422), 'a; b');
  assert.equal(errorMessageFrom({}, 500), 'HTTP 500');
});

test('requests carry credentials and the csrf header only when they change something', async () => {
  const { api, calls } = client({
    'POST /signin': [json({ ok: true })],
    'GET /thing': [json({ ok: true })],
    'POST /thing': [json({ ok: true })],
  });
  api.setCsrfToken('tok');
  await api.request('/thing');
  await api.request('/thing', { method: 'POST', body: '{}' });
  assert.equal(calls[0].init.credentials, 'include');
  assert.equal(calls[0].init.headers['X-CSRF-Token'], undefined);
  assert.equal(calls[1].init.headers['X-CSRF-Token'], 'tok');
});

test('a 401 renews the session once and repeats the call', async () => {
  const { api, calls } = client({
    'GET /data': [json({ detail: 'expired' }, 401), json({ value: 1 })],
    'POST /api/v1/auth/local/refresh': [json({ csrf_token: 'new' })],
  });
  assert.deepEqual(await api.request('/data'), { value: 1 });
  assert.equal(calls.filter(c => c.path.endsWith('/refresh')).length, 1);
  assert.equal(api._csrfToken(), 'new');
});

test('concurrent 401s share a single renewal', async () => {
  const { api, calls } = client({
    'GET /a': [json({}, 401), json({ a: 1 })],
    'GET /b': [json({}, 401), json({ b: 1 })],
    'POST /api/v1/auth/local/refresh': [json({ csrf_token: 'x' })],
  });
  const [a, b] = await Promise.all([api.request('/a'), api.request('/b')]);
  assert.deepEqual([a, b], [{ a: 1 }, { b: 1 }]);
  assert.equal(calls.filter(c => c.path.endsWith('/refresh')).length, 1);
});

test('when renewal fails the session is reported expired and the 401 is thrown', async () => {
  let expired = 0;
  const { api } = client(
    {
      'GET /data': [json({ detail: 'expired' }, 401)],
      'POST /api/v1/auth/local/refresh': [json({ detail: 'gone' }, 401)],
    },
    { onSessionExpired: () => { expired += 1; } },
  );
  await assert.rejects(api.request('/data'), (err) => err instanceof ApiError && err.status === 401);
  assert.equal(expired, 1);
});

test('a 401 from sign-in is wrong credentials: no renewal, no expiry callback', async () => {
  let expired = 0;
  const { api, calls } = client(
    { 'POST /api/v1/auth/local/signin': [json({ detail: 'Invalid email or password' }, 401)] },
    { onSessionExpired: () => { expired += 1; } },
  );
  await assert.rejects(
    api.request('/api/v1/auth/local/signin', { method: 'POST', body: '{}', retries: 0 }),
    (err) => err.status === 401 && err.message === 'Invalid email or password',
  );
  assert.equal(expired, 0);
  assert.equal(calls.length, 1);
});

test('a csrf rejection reloads the token and retries once', async () => {
  const { api } = client({
    'POST /thing': [json({ detail: 'CSRF token missing or invalid' }, 403), json({ done: true })],
    'GET /api/v1/auth/local/me': [json({ user: {}, csrf_token: 'fresh' })],
  });
  api.setCsrfToken('stale');
  assert.deepEqual(await api.request('/thing', { method: 'POST', body: '{}' }), { done: true });
  assert.equal(api._csrfToken(), 'fresh');
});

test('retries server errors and network errors, never other client errors', async () => {
  const flaky = client({ 'GET /x': [json({ detail: 'boom' }, 500), new TypeError('network'), json({ ok: 1 })] });
  assert.deepEqual(await flaky.api.request('/x'), { ok: 1 });

  const bad = client({ 'GET /y': [json({ detail: 'nope' }, 404)] });
  await assert.rejects(bad.api.request('/y'), (err) => err.status === 404);
  assert.equal(bad.calls.length, 1);
});

test('an abort is never retried', async () => {
  const abort = Object.assign(new Error('aborted'), { name: 'AbortError' });
  const { api, calls } = client({ 'GET /z': [abort] });
  await assert.rejects(api.request('/z'), (err) => err.name === 'AbortError');
  assert.equal(calls.length, 1);
});

test('loadSession returns the user, renewing an expired session first, or null', async () => {
  const renewed = client({
    'GET /api/v1/auth/local/me': [json({}, 401), json({ user: { email: 'a@b.c' }, csrf_token: 't' })],
    'POST /api/v1/auth/local/refresh': [json({ csrf_token: 't' })],
  });
  assert.deepEqual(await renewed.api.loadSession(), { email: 'a@b.c' });
  assert.equal(renewed.api._csrfToken(), 't');

  const anonymous = client({
    'GET /api/v1/auth/local/me': [json({}, 401)],
    'POST /api/v1/auth/local/refresh': [json({}, 401)],
  });
  assert.equal(await anonymous.api.loadSession(), null);
});
