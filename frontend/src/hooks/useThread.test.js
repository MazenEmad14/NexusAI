import assert from 'node:assert/strict';
import { test } from 'node:test';

import { storageKeys } from './useThread.js';

test('each person gets their own conversation list', () => {
  const a = storageKeys('alice@example.com');
  const b = storageKeys('bob@example.com');
  assert.notEqual(a.sessions, b.sessions);
  assert.notEqual(a.active, b.active);
});

test('the key is the same however the address is capitalised, and never the old shared key', () => {
  assert.equal(storageKeys('Alice@Example.com').sessions, storageKeys('alice@example.com').sessions);
  for (const owner of ['alice@example.com', '', undefined, null]) {
    assert.notEqual(storageKeys(owner).sessions, 'nexusai_sessions');
    assert.notEqual(storageKeys(owner).active, 'nexusai_active_session');
  }
});

test('unusual characters in an address cannot collide with another person\'s key', () => {
  assert.notEqual(storageKeys('a:b@x.com').sessions, storageKeys('a@x.com').sessions);
  assert.equal(storageKeys('a b@x.com').sessions.includes(' '), false);
});
