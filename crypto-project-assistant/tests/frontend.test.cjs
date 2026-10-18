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

function importContext(file, request) {
  const form = { name: 'Existing project', type: 'defi', facts: { has_token: true } };
  const operation = { signal: { aborted: false } };
  const input = { files: [file], value: file.name };
  const context = vm.createContext({
    $: id => { assert.equal(id, 'file-input'); return input; },
    TextDecoder, formRevision: 0, activeOperation: operation, innerWidth: 1280,
    document: { documentElement: { dataset: { ui: 'dossier' } } },
    beginOperation: () => operation, finishOperation() {},
    acceptResponse: (op, revision) => op === operation && !op.signal.aborted && revision === context.formRevision,
    profileBody: JSON.stringify, request,
    populate(profile) { Object.keys(form).forEach(key => delete form[key]); Object.assign(form, { facts: {} }, profile); },
    render(result) { context.rendered = result; },
    async analyze() {
      const result = await request('/api/analyze', { body: JSON.stringify(form) });
      context.rendered = result; return result;
    },
    showError(message) { context.error = message; },
  });
  const start = html.indexOf("$('file-input').onchange=async()=>{");
  const end = html.indexOf("for(const button of document.querySelectorAll('[data-example]'))", start);
  assert(start >= 0 && end > start);
  vm.runInContext(html.slice(start, end), context);
  return { context, form, input, operation };
}

function importFile(name, raw) {
  const bytes = new TextEncoder().encode(raw);
  return { name, size: bytes.byteLength, arrayBuffer: async () => bytes.buffer };
}

test('a rejected text import preserves the existing form and report', async () => {
  for (const name of ['overlong.txt', 'overlong.md']) {
    const { context, form, input } = importContext(importFile(name, 'X'.repeat(40001)), async () => {
      throw Error('Описание должно быть строкой длиной до 40000 символов');
    });
    const before = JSON.stringify(form);
    context.rendered = { project: { name: 'Existing project' } };
    const report = context.rendered;
    await input.onchange();
    assert.equal(JSON.stringify(form) === before, true, 'Rejected import changed the existing form');
    assert.equal(context.rendered, report);
    assert.match(context.error, /40000/);
    assert.equal(input.value, '');
  }
});

test('a failed or superseded text import does not replace the form', async () => {
  for (const outcome of ['connection error', 'edited', 'cancelled']) {
    let state;
    state = importContext(importFile('description.txt', 'Продукт работает'), async () => {
      if (outcome === 'connection error') throw Error('Нет соединения');
      if (outcome === 'edited') { state.form.name = 'New input'; state.context.formRevision++; }
      if (outcome === 'cancelled') state.operation.signal.aborted = true;
      return { project: { name: 'description', type: 'crypto_project', facts: {} } };
    });
    await state.input.onchange();
    assert.equal(state.form.name, outcome === 'edited' ? 'New input' : 'Existing project');
    assert.equal(state.context.rendered, undefined);
  }
});

test('JSON import uses normalized fields while retaining explicit fact ownership', async () => {
  const raw = { name: ' BTC ', type: ' defi ', description: ' Продукт работает ', facts: { audit_available: null } };
  const normalized = { name: 'Bitcoin', symbol: 'BTC', type: 'defi', description: 'Продукт работает', facts: {
    audit_available: { value: null, origin: 'input' }, product_exists: { value: true, origin: 'description' },
  } };
  const { context, form, input } = importContext(importFile('profile.json', JSON.stringify(raw)), async () => ({ project: normalized }));
  await input.onchange();
  assert.equal(form.type, 'defi');
  assert.equal(form.description, 'Продукт работает');
  assert.equal(form.name, 'Bitcoin');
  assert.equal(form.symbol, ''); // The ticker remains inferred when render fills it.
  assert.deepEqual(Object.keys(form.facts), ['audit_available']);
  assert.equal(form.facts.audit_available.value, null);
  assert.equal(context.rendered.project, normalized);
  assert.equal(context.error, undefined);
});

test('successful text import sends the new description and keeps its recognized facts automatic', async () => {
  let sent;
  const normalized = { name: 'description', description: 'Продукт работает', type: 'crypto_project', facts: {
    product_exists: { value: true, origin: 'description' },
  } };
  const { context, form, input } = importContext(importFile('description.md', 'Продукт работает'), async (path, options) => {
    assert.equal(path, '/api/analyze'); sent = JSON.parse(options.body); return { project: normalized };
  });
  await input.onchange();
  assert.equal(sent.name, 'description');
  assert.equal(sent.description, 'Продукт работает');
  assert.deepEqual(Object.keys(form.facts), []);
  assert.equal(context.rendered.project, normalized);
  assert.equal(context.error, undefined);
});

function lookupContext(request) {
  const nodes = Object.fromEntries(['coin-query', 'lookup-status', 'lookup-choices', 'report'].map(id => [id, {
    value: id === 'coin-query' ? 'BTC' : '', textContent: '', children: [],
    replaceChildren(...children) { this.children = children; }, scrollIntoView() {}, focus() {},
  }]));
  const operation = new AbortController();
  const context = vm.createContext({
    $: id => nodes[id], schema: {}, formRevision: 0, activeOperation: operation, lookupOperation: null,
    beginOperation: () => operation, updateButtons() {},
    finishOperation(op) { if (context.activeOperation === op) context.activeOperation = null; },
    acceptResponse: (op, revision) => context.activeOperation === op && !op.signal.aborted && revision === context.formRevision,
    profileBody: JSON.stringify, request, matchMedia: () => ({ matches: true }),
    element: (tag, textContent) => ({ tag, textContent }),
    populate(profile) { context.profile = profile; context.formRevision++; },
    render(result) { context.rendered = result; }, showError(message) { context.error = message; },
    renderLookupSources() {},
  });
  const lookupSource = source('lookup', 'cancelLookup').split("$('lookup-form')")[0].replace(/^function lookup/, 'async function lookup');
  vm.runInContext(lookupSource + source('cancelLookup', 'download').split("$('lookup-cancel')")[0], context);
  return { context, nodes, operation };
}

test('name lookup fills the collected profile and displays the automatic analysis', async () => {
  const result = { project: { name: 'Bitcoin', symbol: 'BTC', market: { as_of: '2026-10-18' } }, score: { known: 20, total: 26 }, lookup: { notices: [] } };
  const { context, nodes } = lookupContext(async (path, options) => {
    assert.equal(path, '/api/research');
    assert.deepEqual(JSON.parse(options.body), { query: 'BTC', coin_id: '' });
    return result;
  });
  await context.lookup();
  assert.equal(context.profile, result.project);
  assert.equal(context.rendered, result);
  assert.match(nodes['lookup-status'].textContent, /Готово: Bitcoin/);
});

test('ambiguous coin lookup offers choices without replacing the existing profile', async () => {
  let sent;
  const { context, nodes } = lookupContext(async (path, options) => {
    sent = JSON.parse(options.body);
    return { choices: [{ id: 'chosen-id', name: 'Example', symbol: 'XYZ' }] };
  });
  await context.lookup();
  assert.equal(context.profile, undefined);
  assert.equal(nodes['lookup-choices'].children[0].textContent, 'Example / XYZ · chosen-id');
  await nodes['lookup-choices'].children[0].onclick();
  assert.equal(sent.coin_id, 'chosen-id');
});

test('failed, cancelled and superseded coin lookups preserve the existing profile', async () => {
  for (const outcome of ['error', 'edited', 'cancelled']) {
    let state;
    state = lookupContext(async () => {
      if (outcome === 'error') throw Error('CoinGecko: лимит');
      if (outcome === 'edited') state.context.formRevision++;
      if (outcome === 'cancelled') state.context.cancelLookup();
      return { project: { name: 'Wrong' } };
    });
    state.context.profile = { name: 'Existing' };
    await state.context.lookup();
    assert.equal(state.context.profile.name, 'Existing');
    assert.equal(state.context.rendered, undefined);
    if (outcome === 'error') assert.match(state.nodes['lookup-status'].textContent, /лимит/);
  }
});

test('research outcomes preserve the difference between missing and negative evidence', () => {
  const makeNode = (tag, textContent) => ({ tag, textContent, children: [], append(...children) { this.children.push(...children); }, replaceChildren(...children) { this.children = children; } });
  const nodes = Object.fromEntries(['lookup-materials', 'lookup-outcomes', 'lookup-sources', 'lookup-notices', 'lookup-unlocks'].map(id => [id, makeNode('div')]));
  const context = vm.createContext({ $: id => nodes[id], element: makeNode, schema: { criteria: {} } });
  vm.runInContext(source('renderLookupSources', 'download'), context);
  context.renderLookupSources({ notices: [], research: { criteria: {
    yes: { label: 'Known', status: 'confirmed', evidence: 'Exact source quote' },
    no: { label: 'Negative', status: 'refuted', evidence: 'Explicit negative statement' },
    missing: { label: 'Missing', status: 'insufficient_evidence', evidence: '<script>untrusted</script>', source: 'https://project.example' },
  } } });
  const rows = nodes['lookup-outcomes'].children;
  assert.equal(rows.length, 3);
  assert.match(rows[0].children[0].textContent, /Есть подтверждение/);
  assert.match(rows[1].children[0].textContent, /отрицательное подтверждение/);
  assert.match(rows[2].children[0].textContent, /Недостаточно данных/);
  assert.equal(rows[2].children[2].textContent, '<script>untrusted</script>');
  assert.equal(nodes['lookup-unlocks'].hidden, true);
  context.renderLookupSources({notices:[],research:{unlock_events:[{at:'2026-10-20T12:00:01+00:00',amount:'90300',symbol:'OP',recipient:'0xpublic',plan_id:'465',protocol:'Hedgey',block:123,block_time:'2026-10-18T20:00:00Z',source:'https://explorer.example/465',revocable_by:'0xadmin'}]}});
  assert.equal(nodes['lookup-unlocks'].hidden, false);
  assert.match(nodes['lookup-unlocks'].children[0].textContent,/2026-10-20 · 90300 OP/);
  assert.match(nodes['lookup-unlocks'].children[1].textContent,/не все разблокировки/);
  assert.match(nodes['lookup-unlocks'].children[2].children[0].children[0].textContent,/12:00:01 UTC/);
  context.renderLookupSources({notices:[],research:{}});
  assert.equal(nodes['lookup-unlocks'].hidden, true);
  assert.equal(nodes['lookup-unlocks'].children.length, 0);
});
