// Fault injection for browser helpers; complete UI flows are checked in a browser.
const assert = require('node:assert/strict');
const { readFileSync } = require('node:fs');
const { join } = require('node:path');
const { test } = require('node:test');
const vm = require('node:vm');

const html = readFileSync(join(__dirname, '../web/index.html'), 'utf8');
function source(name, next) {
  const start = html.indexOf('function ' + name + '(');
  const end = html.indexOf('function ' + next + '(', start + 1);
  assert(start >= 0 && end > start);
  return html.slice(start, end).replace(/^function request/, 'async function request');
}
function storageContext() {
  const nodes = Object.fromEntries(['name', 'symbol', 'type', 'price_usd', 'fdv-basis', 'draft-status'].map(id => [id, { value: '', textContent: '' }]));
  nodes.name.value = 'Bitcoin'; nodes.symbol.value = 'BTC'; nodes.type.value = 'crypto_project';
  nodes.price_usd.value = '0'; nodes['fdv-basis'].value = 'total';
  const criterion = { select: { value: '' }, note: { value: 'Сведения отсутствуют' }, source: { value: 'Документ' }, date: { value: '2026-10-07' }, recognition: { textContent: '' }, manual: true };
  const stored = new Map();
  const context = vm.createContext({
    $: id => nodes[id], nodes, schema: { frames: { crypto_project: {} } },
    draftFields: ['name', 'symbol', 'type', 'price_usd', 'fdv-basis'],
    controls: new Map([['product_exists', criterion]]), draftKey: 'draft', draftTimer: null,
    inferredName: false, inferredSymbol: true, formRevision: 0,
    updateCount() {}, scheduleDraft() { context.scheduled = true; },
    localStorage: { getItem: key => stored.get(key) ?? null, setItem: (key, value) => stored.set(key, value) },
  });
  vm.runInContext(source('saveDraft', 'restoreDraft') + source('restoreDraft', 'validateForm'), context);
  return { context, nodes, criterion, stored };
}

test('draft round trip preserves zero, explicit unknown, evidence and inferred identity', () => {
  const { context, nodes, criterion } = storageContext();
  context.saveDraft();
  nodes.name.value = ''; nodes.price_usd.value = ''; criterion.manual = false; criterion.note.value = '';
  context.inferredSymbol = false;
  context.restoreDraft();
  assert.equal(nodes.name.value, 'Bitcoin'); assert.equal(nodes.price_usd.value, '0');
  assert.equal(criterion.select.value, ''); assert.equal(criterion.manual, true);
  assert.equal(criterion.note.value, 'Сведения отсутствуют'); assert.equal(context.inferredSymbol, true);
});

test('a corrupt or unsupported draft preserves the current form', () => {
  for (const raw of ['{', 'null', '{"version":2}', 'x'.repeat(1048577)]) {
    const { context, nodes, stored } = storageContext(); stored.set('draft', raw);
    context.restoreDraft();
    assert.equal(nodes.name.value, 'Bitcoin');
    assert.match(nodes['draft-status'].textContent, /повреждён/);
  }
});

test('invalid facts cannot partially restore a draft', () => {
  const { context, nodes, stored } = storageContext(); context.saveDraft();
  const draft = JSON.parse(stored.get('draft')); draft.fields.name = 'Wrong'; draft.facts.product_exists.manual = 'true';
  stored.set('draft', JSON.stringify(draft)); context.restoreDraft();
  assert.equal(nodes.name.value, 'Bitcoin'); assert.match(nodes['draft-status'].textContent, /повреждён/);
});

test('storage denial and exhausted quota do not interrupt input', () => {
  const { context, nodes } = storageContext();
  context.localStorage = { getItem() { throw Error('SecurityError'); }, setItem() { throw Error('QuotaExceededError'); } };
  assert.doesNotThrow(() => context.restoreDraft());
  assert.doesNotThrow(() => context.saveDraft());
  assert.equal(nodes.name.value, 'Bitcoin'); assert.match(nodes['draft-status'].textContent, /Сохраните профиль в файл/);
});

test('typing before schema loading takes precedence over a saved draft', () => {
  const { context, nodes } = storageContext(); context.saveDraft();
  nodes.name.value = 'My new project'; context.formRevision = 1; context.restoreDraft();
  assert.equal(nodes.name.value, 'My new project'); assert.equal(context.scheduled, true);
});

function requestContext(fetch) {
  const context = vm.createContext({ fetch, AbortController, setTimeout, clearTimeout });
  vm.runInContext(source('request', 'scheduleDraft'), context);
  return context;
}
test('disconnected application has an actionable Russian message', async () => {
  const context = requestContext(async () => { throw TypeError('Failed to fetch'); });
  await assert.rejects(context.request('/api/analyze'), /Нет соединения с приложением/);
});
test('malformed JSON and a wrong response shape are reported clearly', async () => {
  for (const value of [null, [], 'text']) {
    const context = requestContext(async () => ({ ok: true, json: async () => value }));
    await assert.rejects(context.request('/api/schema'), /некорректный ответ/);
  }
  const context = requestContext(async () => ({ ok: true, json: async () => { throw SyntaxError('Unexpected end'); } }));
  await assert.rejects(context.request('/api/schema'), /некорректный ответ/);
});
test('HTTP validation errors retain the server explanation', async () => {
  const context = requestContext(async () => ({ ok: false, status: 400, json: async () => ({ error: 'Проверьте дату снимка' }) }));
  await assert.rejects(context.request('/api/analyze'), /Проверьте дату снимка/);
});
function pendingFetch(path, { signal }) {
  return new Promise((resolve, reject) => {
    if (signal.aborted) reject(new DOMException('Cancelled', 'AbortError'));
    else signal.addEventListener('abort', () => reject(new DOMException('Cancelled', 'AbortError')), { once: true });
  });
}
test('request deadline stops waiting and explains retry', async () => {
  const context = requestContext(pendingFetch);
  await assert.rejects(context.request('/api/analyze', { timeout: 5 }), /не ответил вовремя/);
});
test('user cancellation stays a cancellation rather than a connection error', async () => {
  const context = requestContext(pendingFetch); const controller = new AbortController();
  const pending = context.request('/api/analyze', { signal: controller.signal }); controller.abort();
  await assert.rejects(pending, error => error.name === 'AbortError');
});
