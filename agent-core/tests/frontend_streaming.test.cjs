const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');
const source = fs.readFileSync(path.join(__dirname, '../frontend/app.js'), 'utf8');
const context = {};
vm.createContext(context);
vm.runInContext(source.slice(source.indexOf('function acceptStreamGap('), source.indexOf('function watchLiveTask(')), context);
const cursor = n => `${'a'.repeat(32)}:${n}`;
const token = (n, text) => ({ run_id: 'run', node_id: 'ask', cursor: cursor(n), text });

test('replayed and out of order tokens do not duplicate output', () => {
  const task = { id: 'run' };
  context.acceptStreamToken(task, token(1, 'a'));
  context.acceptStreamToken(task, token(2, 'b'));
  assert.equal(context.acceptStreamToken(task, token(1, 'a')), false);
  assert.equal(context.acceptStreamToken(task, token(2, 'b')), false);
  context.acceptStreamToken(task, token(3, 'c'));
  assert.equal(task.streamText, 'abc');
});
test('gap clears the incomplete prefix before showing retained suffix', () => {
  const task = { id: 'run', streamText: 'old', streamCursor: cursor(1) };
  context.acceptStreamGap(task, { run_id: 'run', cursor: cursor(8) });
  context.acceptStreamToken(task, token(9, 'suffix'));
  assert.equal(task.streamText, 'suffix');
  assert.equal(task.streamGap, true);
});
test('lost memory clears cursor and subsequent new stream can start', () => {
  const task = { id: 'run', streamText: 'old', streamCursor: cursor(99) };
  context.acceptStreamGap(task, { run_id: 'run', cursor: null });
  context.acceptStreamToken(task, token(1, 'new'));
  assert.equal(task.streamText, 'new');
});
test('frames for other runs do not change preview', () => {
  const task = { id: 'other', streamText: 'keep' };
  context.acceptStreamGap(task, { run_id: 'run', cursor: null });
  context.acceptStreamToken(task, token(1, 'bad'));
  assert.equal(task.streamText, 'keep');
  assert.equal(task.streamGap, undefined);
});

test('actual EventSource listeners resume cursor, deduplicate, show gaps and finish', async () => {
  const listeners = {};
  let url;
  let closed = false;
  const fixture = {
    EventSource: class {
      constructor(value) { url = value; }
      addEventListener(name, handler) { listeners[name] = handler; }
      close() { closed = true; }
    },
    state: { activeId: 'run' },
    window: { setInterval() { return 1; }, clearInterval() {} },
    updateStreamOutput() {}, renderTaskView() {},
  };
  vm.createContext(fixture);
  vm.runInContext(source.slice(source.indexOf('function acceptStreamGap('), source.indexOf('async function pollLiveTask(')), fixture);
  const task = { id: 'run', streamCursor: cursor(2), streamText: 'ab', lifecycle: [] };
  const done = fixture.watchLiveTask(task);
  assert.equal(url, `/api/runs/run/events/stream?cursor=${encodeURIComponent(cursor(2))}`);
  const emit = (event, item) => listeners[event]({ data: JSON.stringify(item) });
  emit('token', token(2, 'b'));
  emit('token', token(3, 'c'));
  assert.equal(task.streamText, 'abc');
  emit('gap', { run_id: 'run', cursor: cursor(8) });
  emit('token', token(9, 'suffix'));
  assert.equal(task.streamText, 'suffix');
  assert.equal(task.streamGap, true);
  emit('done', { status: 'COMPLETED' });
  assert.equal(await done, 'COMPLETED');
  assert.equal(closed, true);
});
