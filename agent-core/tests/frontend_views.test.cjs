const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');
const source = fs.readFileSync(path.join(__dirname, '../frontend/app.js'), 'utf8');

function fixture() {
  const elements = new Map(), removed = [], requests = [];
  function element(id) {
    if (!elements.has(id)) elements.set(id, { innerHTML: '', textContent: '',
      classList: { add() {}, toggle() {} }, querySelectorAll: () => [],
      remove() { removed.push(id); elements.delete(id); }, addEventListener() {} });
    return elements.get(id);
  }
  const context = { document: { addEventListener() {}, getElementById: element,
    querySelectorAll: () => [] }, window: {}, };
  vm.createContext(context); vm.runInContext(source, context);
  const run = js => vm.runInContext(js, context);
  context.apiFetch = () => new Promise(resolve => requests.push(resolve));
  context.refreshIcons = context.updateCancelButton = context.updateResumeButton = () => {};
  run('state.view = "logs"; state.live = true');
  return { context, elements, removed, requests, run, element };
}

test('task redraws and token previews cannot write into the log view', () => {
  const f = fixture();
  f.context.document.getElementById = () => { throw new Error('task touched the log DOM'); };
  f.context.renderTaskView({ streamText: 'unrelated-task' });
  f.context.updateStreamOutput({ streamText: 'unrelated-task' });
});

test('opening logs removes task-specific panels and invalidates pending approvals', async () => {
  const f = fixture();
  for (const name of ['approvalPanel', 'harnessPanel', 'recoveryPanel', 'streamOutput', 'chatView']) f.element(name);
  const prior = f.run('approvalPanelVersion');
  const pending = f.context.renderLogsView();
  for (const name of ['approvalPanel', 'harnessPanel', 'recoveryPanel', 'streamOutput', 'chatView']) {
    assert.ok(f.removed.includes(name), name);
  }
  assert.ok(f.run('approvalPanelVersion') > prior);
  f.requests[0]({ runs: [], total: 0, active: [] }); await pending;
});

test('a late logs response cannot replace a task after navigation', async () => {
  const f = fixture();
  const pending = f.context.renderLogsView();
  f.run('state.view = "tasks"');
  f.element('timeline').innerHTML = 'current-task';
  f.requests[0]({ runs: [], total: 0, active: [] }); await pending;
  assert.equal(f.element('timeline').innerHTML, 'current-task');
});

test('an older logs request cannot append to or overwrite a newer list', async () => {
  const f = fixture();
  const old = f.context.renderLogsView(), current = f.context.renderLogsView();
  f.requests[1]({ runs: [], total: 2, active: [] }); await current;
  f.requests[0]({ runs: [], total: 99, active: [] }); await old;
  assert.equal(f.run('serverRunsView.total'), 2);
  assert.match(f.element('timeline').innerHTML, /2 条/);
  assert.doesNotMatch(f.element('timeline').innerHTML, /99 条/);
});

test('background task completion keeps logs open while an explicit task selection activates tasks', () => {
  const f = fixture();
  f.run('state.tasks = [{ id: "r", title: "sample" }]; state.activeId = "r"');
  let rendered = 0, histories = 0;
  f.context.renderTaskView = () => rendered++;
  f.context.renderHistory = () => histories++;
  f.element('executionTitle').textContent = '运行日志';
  f.context.selectTask('r');
  assert.equal(f.run('state.view'), 'logs');
  assert.equal(f.element('executionTitle').textContent, '运行日志');
  assert.equal(rendered, 0);
  f.context.selectTask('r', true);
  assert.equal(f.run('state.view'), 'tasks');
  assert.equal(f.element('executionTitle').textContent, 'sample');
  assert.equal(rendered, 1);
  assert.equal(histories, 2);
});
