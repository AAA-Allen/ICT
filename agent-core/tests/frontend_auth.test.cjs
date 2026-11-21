const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

const source = fs.readFileSync(path.join(__dirname, '../frontend/app.js'), 'utf8');
const TOKEN = 'synthetic-test-token';
const ok = body => ({ ok: true, status: 200, json: async () => body });
const unauthorized = () => ({ ok: false, status: 401,
  json: async () => ({ error: { code: 'OTHER_CODE', message: TOKEN } }) });

function fixture() {
  const calls = [], writes = [], elements = new Map(), listeners = new Map();
  const element = id => {
    if (!elements.has(id)) elements.set(id, {
      value: '', textContent: '', dataset: {}, innerHTML: '',
      listeners: new Map(),
      showModal() { this.open = true; }, close() { this.open = false; }, focus() {},
      addEventListener(name, callback) { this.listeners.set(name, callback); },
    });
    return elements.get(id);
  };
  const context = {
    URL, Headers, AbortController, console,
    window: { location: { href: 'http://127.0.0.1:8000/', origin: 'http://127.0.0.1:8000' },
      setTimeout, clearTimeout, addEventListener: (name, fn) => listeners.set(name, fn),
      localStorage: { getItem: () => null, setItem: (...args) => writes.push(args) } },
    document: { getElementById: element, addEventListener() {} },
    fetch: async (url, options) => { calls.push({ url, options }); return ok({}); },
  };
  vm.createContext(context);
  vm.runInContext(source, context);
  const run = js => vm.runInContext(js, context);
  context.selectTask = () => {};
  context.renderHistory = () => {};
  context.renderTaskView = () => {};
  context.removeApprovalPanel = () => {};
  const setToken = () => { element('approvalTokenInput').value = TOKEN; context.applyApprovalToken(); };
  return { context, calls, writes, element, run, setToken, listeners };
}

test('only the three same-origin POST endpoints receive the ephemeral token', async () => {
  const f = fixture(); f.setToken();
  const headers = { 'Content-Type': 'application/json' };
  for (const endpoint of ['approval-decision', 'approval-resolution', 'resume']) {
    await f.context.apiFetch(`/api/runs/run-1/${endpoint}`, { method: 'POST', headers });
    assert.equal(f.calls.at(-1).options.headers.get('Authorization'), `Bearer ${TOKEN}`);
    assert.equal(f.calls.at(-1).options.redirect, 'error');
  }
  for (const [url, method] of [
    ['/api/runs/run-1/resume', 'GET'], ['/api/runs/run-1/approval-decision', 'GET'],
    ['/api/runs', 'POST'], ['/api/sessions/session', 'DELETE'],
    ['/api/runs/run-1/events/stream', 'GET'], ['/api/health', 'GET'],
    ['https://example.org/api/runs/run-1/resume', 'POST'],
    ['//example.org/api/runs/run-1/resume', 'POST'],
    ['/api/runs/run-1/resume/extra', 'POST'],
  ]) {
    await f.context.apiFetch(url, { method });
    assert.equal(new Headers(f.calls.at(-1).options.headers).has('Authorization'), false);
  }
  assert.deepEqual(headers, { 'Content-Type': 'application/json' });
  assert.equal(JSON.stringify(f.calls.map(call => call.url)).includes(TOKEN), false);
});

test('required but missing credentials block writes locally and leave reads usable', async () => {
  const f = fixture(); f.run('approvalAuth.configure(true)');
  await assert.rejects(f.context.apiFetch('/api/runs/r/resume', { method: 'POST' }),
    error => error.code === 'APPROVAL_TOKEN_REQUIRED');
  assert.equal(f.calls.length, 0);
  await f.context.apiFetch('/api/runs/r');
  assert.equal(f.calls.length, 1);
  assert.match(f.element('approvalTokenStatus').textContent, /需要审批凭证/);
});

test('401 uses a fixed message, forgets the rejected token, and never exposes response text', async () => {
  const f = fixture(); f.setToken(); f.context.fetch = async () => unauthorized();
  await assert.rejects(f.context.apiFetch('/api/runs/r/resume', { method: 'POST' }),
    error => error.code === 'APPROVAL_AUTH_REQUIRED' && !error.message.includes(TOKEN));
  f.context.fetch = async () => { throw new Error('must not send'); };
  await assert.rejects(f.context.apiFetch('/api/runs/r/resume', { method: 'POST' }),
    error => error.code === 'APPROVAL_TOKEN_REQUIRED');
  assert.match(f.element('approvalTokenStatus').textContent, /需要审批凭证/);
});

test('a stale 401 cannot clear a newly supplied credential', async () => {
  const f = fixture(); f.setToken();
  let rejectOld;
  f.context.fetch = () => new Promise(resolve => { rejectOld = resolve; });
  const oldRequest = f.context.apiFetch('/api/runs/r/resume', { method: 'POST' });
  f.element('approvalTokenInput').value = 'replacement-synthetic-token'; f.context.applyApprovalToken();
  rejectOld(unauthorized());
  await assert.rejects(oldRequest, error => error.code === 'APPROVAL_AUTH_REQUIRED');
  let authorization;
  f.context.fetch = async (_, options) => { authorization = options.headers.get('Authorization'); return ok({}); };
  await f.context.apiFetch('/api/runs/r/resume', { method: 'POST' });
  assert.equal(authorization, 'Bearer replacement-synthetic-token');
});

test('input is emptied and credential never enters history, rendered status, or a new page', async () => {
  const f = fixture(); f.setToken();
  assert.equal(f.element('approvalTokenInput').value, '');
  f.run('state.tasks = [{ id: "r", title: "sample" }]; persistHistory()');
  assert.equal(JSON.stringify(f.writes).includes(TOKEN), false);
  assert.equal(f.element('approvalTokenStatus').textContent.includes(TOKEN), false);
  const reloaded = fixture();
  await reloaded.context.apiFetch('/api/runs/r/resume', { method: 'POST' });
  assert.equal(reloaded.calls[0].options.headers.has('Authorization'), false);
  f.run('approvalAuth.clear()');
  await f.context.apiFetch('/api/runs/r/resume', { method: 'POST' });
  assert.equal(f.calls.at(-1).options.headers.has('Authorization'), false);
});

test('invalid header input is cleared without echoing it or replacing the usable token', async () => {
  const f = fixture(); f.setToken();
  f.element('approvalTokenInput').value = 'bad\r\nheader'; f.context.applyApprovalToken();
  assert.equal(f.element('approvalTokenInput').value, '');
  assert.equal(f.element('approvalTokenStatus').textContent.includes('bad'), false);
  await f.context.apiFetch('/api/runs/r/resume', { method: 'POST' });
  assert.equal(f.calls[0].options.headers.get('Authorization'), `Bearer ${TOKEN}`);
});

test('actual settings controls clear unsubmitted input and pagehide forgets the active credential', async () => {
  const f = fixture();
  // Execute the actual bindings without starting unrelated task/history boot work.
  vm.runInContext(source.slice(source.indexOf("  $('approvalTokenApply').addEventListener"),
    source.indexOf("  $('resumeConfirm').addEventListener")), f.context);
  f.element('approvalTokenInput').value = TOKEN;
  f.element('approvalTokenApply').listeners.get('click')();
  assert.equal(f.element('approvalTokenInput').value, '');
  f.element('approvalTokenInput').value = 'unfinished-input';
  f.element('settingsModal').listeners.get('close')();
  assert.equal(f.element('approvalTokenInput').value, '');
  await f.context.apiFetch('/api/runs/r/resume', { method: 'POST' });
  assert.equal(f.calls.at(-1).options.headers.get('Authorization'), `Bearer ${TOKEN}`);
  f.listeners.get('pagehide')();
  await f.context.apiFetch('/api/runs/r/resume', { method: 'POST' });
  assert.equal(f.calls.at(-1).options.headers.has('Authorization'), false);
  f.setToken();
  f.element('approvalTokenClear').listeners.get('click')();
  await f.context.apiFetch('/api/runs/r/resume', { method: 'POST' });
  assert.equal(f.calls.at(-1).options.headers.has('Authorization'), false);
});

function waitingTask(f) {
  const task = { id: 'r', status: 'waiting', lifecycle: ['old-event'], title: 'sample',
    run: { status: 'WAITING_APPROVAL', pending_approval: { request_id: 'q' } } };
  f.context.task = task; f.run('state.tasks = [task]; state.activeId = task.id');
  return task;
}

test('approval credential failure preserves waiting state and never retries a write', async () => {
  const f = fixture(), task = waitingTask(f); f.run('approvalAuth.configure(true)');
  f.context.fetch = async (url, options) => {
    f.calls.push({ url, options }); return ok({ run: task.run });
  };
  await f.context.submitApprovalDecision(task, 'APPROVED');
  assert.equal(task.status, 'waiting');
  assert.equal(f.run('state.running'), false);
  assert.equal(f.element('settingsModal').open, true);
  assert.deepEqual(f.calls.map(call => call.url), ['/api/runs/r']);
});

test('a rejected resume after a durable approval reads current eligibility without polling or replay', async () => {
  const f = fixture(), task = waitingTask(f); f.setToken();
  const updatedRun = { ...task.run, resumable: true, recovery: { allowed: true } };
  f.context.pollLiveTask = () => { throw new Error('must not poll rejected resume'); };
  f.context.fetch = async (url, options) => {
    f.calls.push({ url, options });
    return url.endsWith('/approval-decision') ? ok({}) : url.endsWith('/resume')
      ? unauthorized() : ok({ run: updatedRun });
  };
  await f.context.submitApprovalDecision(task, 'APPROVED');
  assert.equal(task.status, 'waiting');
  assert.equal(task.run.resumable, true);
  assert.deepEqual(f.calls.map(call => call.url), [
    '/api/runs/r/approval-decision', '/api/runs/r/resume', '/api/runs/r',
  ]);
  assert.equal(JSON.stringify(f.writes).includes(TOKEN), false);
});

test('a supplied credential completes the explicit approval/resume path and the rejection path', async () => {
  for (const decision of ['APPROVED', 'REJECTED']) {
    const f = fixture(), task = waitingTask(f); f.setToken(); f.run('approvalAuth.configure(true)');
    f.context.fetch = async (url, options) => {
      f.calls.push({ url, options });
      if (options.method === 'POST') {
        assert.equal(options.headers.get('Authorization'), `Bearer ${TOKEN}`);
      }
      return url.endsWith('/approval-decision') ? ok({})
        : ok({ run: { status: decision === 'APPROVED' ? 'COMPLETED' : 'BLOCKED',
          reason_code: decision === 'REJECTED' ? 'APPROVAL_REJECTED' : null } });
    };
    await f.context.submitApprovalDecision(task, decision);
    assert.equal(task.status, decision === 'APPROVED' ? 'completed' : 'failed');
    assert.equal(f.calls.length, 2);
    assert.equal(f.calls[1].url, decision === 'APPROVED' ? '/api/runs/r/resume' : '/api/runs/r');
    assert.equal(f.run('state.running'), false);
    assert.equal(JSON.stringify(f.writes).includes(TOKEN), false);
  }
});

test('readback outage keeps the prior task state after credential failure', async () => {
  const f = fixture(), task = waitingTask(f); f.run('approvalAuth.configure(true)');
  f.context.fetch = async () => { throw new Error('offline'); };
  await f.context.submitApprovalDecision(task, 'REJECTED');
  assert.equal(task.status, 'waiting');
  assert.deepEqual(task.lifecycle, ['old-event']);
  assert.equal(f.run('state.running'), false);
});

test('an authoritative active run stays active and is observed without another write', async () => {
  const f = fixture(), task = waitingTask(f); f.run('approvalAuth.configure(true)');
  let observed = 0;
  f.context.pollLiveTask = current => { assert.equal(current, task); observed += 1; };
  f.context.fetch = async () => ok({ run: { status: 'RUNNING' } });
  await f.context.submitApprovalDecision(task, 'APPROVED');
  assert.equal(task.status, 'running');
  assert.equal(f.run('state.running'), true);
  assert.equal(observed, 1);
});

test('manual checkpoint resume authentication failure preserves the failed run', async () => {
  const f = fixture(), task = waitingTask(f);
  task.status = 'failed'; task.run = { status: 'FAILED', reason_code: 'TEST_FAILURE' };
  f.run('approvalAuth.configure(true); resumeContext = { task, original: {} }');
  f.element('resumeStateEditor').value = '{"reviewed":true}';
  f.context.fetch = async () => ok({ run: task.run });
  await f.context.confirmResume();
  assert.equal(task.status, 'failed');
  assert.equal(task.run.reason_code, 'TEST_FAILURE');
  assert.equal(f.element('settingsModal').open, true);
  assert.equal(f.element('resumeModal').open, true);
  assert.equal(f.element('resumeStateEditor').value, '{"reviewed":true}');
  assert.equal(f.run('resumeContext.task'), task);
});

test('resolution credential failure keeps the unknown-outcome record for manual reconciliation', async () => {
  const f = fixture(), task = waitingTask(f);
  task.status = 'failed'; task.run = { status: 'BLOCKED', reason_code: 'TOOL_OUTCOME_UNKNOWN' };
  const panel = f.element('approvalPanel'); panel.dataset.requestId = 'q';
  panel.querySelector = selector => ({ value: selector.includes('Outcome') ? 'COMPLETED' : '{}' });
  f.run('approvalAuth.configure(true)');
  f.context.fetch = async () => ok({ run: task.run });
  await f.context.submitResolution(task);
  assert.equal(task.run.reason_code, 'TOOL_OUTCOME_UNKNOWN');
  assert.equal(task.status, 'failed');
  assert.equal(f.run('state.running'), false);
  assert.equal(f.element('settingsModal').open, true);
});
