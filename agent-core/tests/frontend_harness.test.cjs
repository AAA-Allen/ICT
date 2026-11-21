const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');
const source = fs.readFileSync(path.join(__dirname, '../frontend/app.js'), 'utf8');
const auditSource = source.slice(source.indexOf('function summarizeHarness('), source.indexOf('const AGENT_SELECT_KEY'));
function fixture(extra = {}) {
  const context = { escapeHtml: value => String(value).replaceAll('<', '&lt;').replaceAll('>', '&gt;'), ...extra };
  vm.createContext(context);
  vm.runInContext(auditSource, context);
  return context;
}
function event(sequence, type, summary = {}, extra = {}) {
  return { run_id: 'run', sequence, event_id: `e-${sequence}`, event_type: type,
    status: 'COMPLETED', node_id: 'ask', call_id: 'call', execution_id: 'visit', summary, ...extra };
}
function summary(context, events) { return context.summarizeHarness({ run_id: 'run', events }, 'run'); }

test('missing audit is unknown and explicit usage zero is distinct from absent', () => {
  const c = fixture();
  const empty = summary(c, []);
  assert.equal(empty.status, 'EMPTY');
  assert.match(c.harnessBody(empty), /均未知/);
  const data = summary(c, [event(1, 'model.completed', { usage: { prompt_tokens: 0 } }),
    event(2, 'model.completed', { usage: { completion_tokens: 4 } }), event(3, 'model.completed')]);
  assert.equal(data.usage.prompt_tokens.total, 0);
  assert.equal(data.usage.prompt_tokens.covered, 1);
  assert.equal(data.usage.completion_tokens.total, 4);
  assert.equal(data.usage.total_tokens, undefined);
  assert.match(c.harnessBody(data), /覆盖 1\/3/);
});
test('claims and settlements describe reservations, not requested or successful calls', () => {
  const c = fixture();
  const data = summary(c, [event(1, 'model.requested'),
    event(2, 'run_budget.claimed', { kind: 'model', limits: { model: 6, tool: 10 } }),
    event(3, 'model.failed')]);
  assert.equal(data.modelRequests, 1);
  assert.equal(data.modelReplies, 0);
  assert.equal(data.budget.used.model, 1);
  assert.equal(data.budget.outstanding, 1);
  assert.match(c.harnessBody(data), /未结算 1/);
});
test('planned retry cancelled before invocation is not counted as an executed retry', () => {
  const c = fixture();
  const data = summary(c, [event(1, 'model.attempt_failed', { attempt: 1, retry: true }),
    event(2, 'model.retry_cancelled', { attempt: 1 })]);
  assert.equal(data.plannedRetries, 1);
  assert.equal(data.retries, 0);
  const later = summary(c, [event(1, 'tool.attempt_failed', { attempt: 1, retry: true }),
    event(2, 'tool.attempt_completed', { attempt: 2 })]);
  assert.equal(later.retries, 1);
});
test('approval requests and completed tool results remain distinct', () => {
  const c = fixture();
  const data = summary(c, [event(1, 'tool.requested'), event(2, 'tool.waiting_approval'),
    event(3, 'tool.requested'), event(4, 'tool.completed')]);
  assert.equal(data.toolRequests, 2);
  assert.equal(data.toolResults, 1);
  assert.equal(data.budget, null);
});
test('budget contradictions and orphan settlements never imply available quota', () => {
  const c = fixture();
  for (const events of [[event(1, 'run_budget.settled', { claim_id: 'missing', kind: 'tool' })],
    [event(1, 'run_budget.claimed', { kind: 'model', limits: { model: 2, tool: 2 } }),
      event(2, 'run_budget.claimed', { kind: 'model', limits: { model: 9, tool: 2 } })]]) {
    const data = summary(c, events);
    assert.equal(data.budget.inconsistent, true);
    assert.match(c.harnessBody(data), /额度未知/);
  }
});
test('malformed, wrong-run, duplicate and invalid usage evidence is rejected', () => {
  const c = fixture();
  for (const events of [[event(2, 'model.requested')], [event(1, 'tool.completed', {}, { run_id: 'other' })],
    [event(1, 'model.requested'), event(2, 'model.completed', {}, { event_id: 'e-1' })],
    [event(1, 'model.completed', { usage: { total_tokens: -1 } })],
    [event(1, 'model.completed', { usage: [] })]]) {
    assert.throws(() => summary(c, events), /INVALID_HARNESS_EVIDENCE/);
  }
});
test('timeline escapes untrusted labels and excludes private summary fields', () => {
  const c = fixture();
  const data = summary(c, [event(1, 'tool.completed', { tool: '<img>', arguments: 'secret' }, { node_id: '<script>' })]);
  const html = c.harnessBody(data);
  assert.ok(!html.includes('<img>'));
  assert.ok(!html.includes('<script>'));
  assert.ok(!html.includes('secret'));
});

function panel() { return { isConnected: true, innerHTML: '', querySelector: () => ({ addEventListener() {} }) }; }
test('late refresh cannot overwrite a newer response for the same run', async () => {
  const requests = [];
  const c = fixture({ apiFetch: () => new Promise(resolve => requests.push(resolve)) });
  const p = panel();
  const first = c.loadHarnessEvidence('run', p);
  const second = c.loadHarnessEvidence('run', p);
  requests[1]({ run_id: 'run', events: [event(1, 'model.completed')] });
  await second;
  const current = p.innerHTML;
  requests[0]({ run_id: 'run', events: [] });
  await first;
  assert.equal(p.innerHTML, current);
});
test('removed panel and changed run snapshot reject stale audit responses', async () => {
  let resolve;
  let current = true;
  const c = fixture({ apiFetch: () => new Promise(done => { resolve = done; }) });
  const p = panel();
  const request = c.loadHarnessEvidence('run', p, () => current);
  const loading = p.innerHTML;
  current = false;
  p.isConnected = false;
  resolve({ run_id: 'run', events: [event(1, 'tool.completed')] });
  await request;
  assert.equal(p.innerHTML, loading);
});
test('HTTP failure renders unknown instead of cached zero or task failure', async () => {
  const c = fixture({ apiFetch: async () => { throw new Error('private transport message'); } });
  const p = panel();
  await c.loadHarnessEvidence('run', p);
  assert.match(p.innerHTML, /计数、额度及 usage 未知/);
  assert.ok(!p.innerHTML.includes('private transport message'));
});

test('a late server inspector request cannot replace a newer run', async () => {
  const requests = [];
  const elements = { modalTitle: {}, modalBody: {}, detailHarnessPanel: panel(), detailModal: { showModal() {} } };
  const c = { apiFetch: () => new Promise(resolve => requests.push(resolve)),
    $: id => elements[id], runInspectorBody: run => run.run_id, bindInspectorHandlers() {},
    loadHarnessEvidence() {}, refreshIcons() {} };
  vm.createContext(c);
  vm.runInContext('let inspectorVersion = 0;\n' + source.slice(source.indexOf('async function openServerRunDetail('),
    source.indexOf('async function renderAgentTopology(')), c);
  const first = c.openServerRunDetail('old');
  const second = c.openServerRunDetail('new');
  requests[1]({ run: { run_id: 'new' } });
  await second;
  requests[0]({ run: { run_id: 'old' } });
  await first;
  assert.equal(elements.modalBody.innerHTML, 'new');
});
