const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

const source = fs.readFileSync(path.join(__dirname, '../frontend/app.js'), 'utf8');
// Load the actual approval UI functions without bootstrapping the whole app.
const approvalSource = source.slice(source.indexOf('const shortDigest ='),
  source.indexOf('\nasync function submitResolution(task)'));

function fixture(status = 'WAITING_APPROVAL') {
  const requests = [];
  let panel = null;
  function element() {
    return {
      dataset: {}, innerHTML: '',
      querySelector: () => ({ addEventListener() {} }),
      remove() { if (panel === this) panel = null; },
    };
  }
  const task = {
    id: 'same-run', mode: 'live', status: status === 'BLOCKED' ? 'failed' : 'waiting',
    run: { status, reason_code: status === 'BLOCKED' ? 'TOOL_OUTCOME_UNKNOWN' : 'APPROVAL_REQUIRED',
      pending_approval: status === 'BLOCKED' ? null : {
        request_id: 'request-1', tool_name: 'write', argument_digest: 'digest',
      } },
  };
  const context = {
    state: { live: true, activeId: task.id, running: false },
    apiFetch: () => new Promise(resolve => requests.push(resolve)),
    document: { createElement: element }, icon: () => '', escapeHtml: String,
    $: id => id === 'approvalPanel' ? panel : { after(next) { panel = next; } },
    refreshIcons() {}, submitResolution() {},
  };
  vm.createContext(context);
  vm.runInContext(approvalSource, context);
  return { context, task, requests, panel: () => panel };
}

const started = requestId => ({ pending: {
  request_id: requestId, tool_name: 'write', execution_state: 'STARTED',
} });
const flush = () => new Promise(resolve => setImmediate(resolve));

test('a late response cannot replace the next approval in the same run', async () => {
  const f = fixture();
  f.context.renderApprovalPanel(f.task);
  f.task.run = { ...f.task.run, pending_approval: {
    ...f.task.run.pending_approval, request_id: 'request-2',
  } };
  f.context.renderApprovalPanel(f.task);
  const secondPanel = f.panel();
  f.requests[0](started('request-1'));
  await flush();
  assert.equal(f.panel(), secondPanel);
  assert.match(f.panel().innerHTML, /request-2/);
});

test('a response for a different request is rejected even without replacing the run object', async () => {
  const f = fixture();
  f.context.renderApprovalPanel(f.task);
  const currentPanel = f.panel();
  f.task.run.pending_approval.request_id = 'request-2';
  f.requests[0](started('request-1'));
  await flush();
  assert.equal(f.panel(), currentPanel);
});

test('a late response cannot restore a panel after the task completes', async () => {
  const f = fixture();
  f.context.renderApprovalPanel(f.task);
  f.task.status = 'completed';
  f.task.run = { status: 'COMPLETED', pending_approval: null };
  f.context.renderApprovalPanel(f.task);
  f.requests[0](started('request-1'));
  await flush();
  assert.equal(f.panel(), null);
});

test('removing a panel invalidates its outstanding query', async () => {
  const f = fixture();
  f.context.renderApprovalPanel(f.task);
  f.context.removeApprovalPanel();
  f.requests[0](started('request-1'));
  await flush();
  assert.equal(f.panel(), null);
});

test('the current waiting request still exposes reconciliation', async () => {
  const f = fixture();
  f.context.renderApprovalPanel(f.task);
  f.requests[0](started('request-1'));
  await flush();
  assert.equal(f.panel().dataset.requestId, 'request-1');
});

test('blocked unknown outcomes still expose reconciliation without a waiting request', async () => {
  const f = fixture('BLOCKED');
  f.context.renderApprovalPanel(f.task);
  f.requests[0](started('request-1'));
  await flush();
  assert.equal(f.panel().dataset.requestId, 'request-1');
});

test('declared action fields are escaped and private request arguments are not displayed', () => {
  const f = fixture();
  f.context.escapeHtml = value => String(value).replaceAll('<', '&lt;').replaceAll('>', '&gt;');
  const request = { ...f.task.run.pending_approval, arguments: { secret: 'do-not-show' },
    display: { description: 'Create <draft>', fields: { filename: '<target>.md' } } };
  const panel = f.context.buildWaitingPanel(f.task, request);
  assert.ok(panel.innerHTML.includes('&lt;draft&gt;'));
  assert.ok(panel.innerHTML.includes('&lt;target&gt;.md'));
  assert.ok(!panel.innerHTML.includes('do-not-show'));
});
test('legacy approval has an explicit missing-action notice', () => {
  const f = fixture();
  assert.match(f.context.approvalDisplayHtml(undefined), /未提供可公开的动作说明/);
});
test('recovery explanation follows server preflight and never guesses eligibility', () => {
  const f = fixture();
  assert.match(f.context.recoveryExplanation({ resumable: true }), /未知/);
  assert.match(f.context.recoveryExplanation({ recovery: { allowed: false, reason_code: 'TOOL_OUTCOME_UNKNOWN' } }), /不要重复执行/);
  assert.match(f.context.recoveryExplanation({ recovery: { allowed: false, reason_code: 'NOT_RESUMABLE', detail: 'APPROVAL_PENDING' } }), /尚未批准/);
  assert.match(f.context.recoveryExplanation({ recovery: { allowed: true } }), /允许尝试/);
});
