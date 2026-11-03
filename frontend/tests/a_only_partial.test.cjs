const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

function screen() {
  const CV = { app: { init() {} }, createResult() {} };
  const context = vm.createContext({ CV, window: { CV }, saveStore() {},
    el(tag, attrs) { return { tag, ...attrs }; } });
  for (const file of ['comparison_flow.js', 'live_app.js', 'service_ux.js']) {
    vm.runInContext(fs.readFileSync(path.join(__dirname, '..', 'src', file), 'utf8'), context);
  }
  return context;
}

test('A partial is a completed check with unresolved conditions, not full A confirmation', () => {
  const context = screen();
  const message = { role: 'ai', execution: {} };
  context.receiveResult(message, { status: 'a_partial', stages: [{ id: 'table', status: 'warning' }],
    ui_cases: [], finished_at: '2026-11-03T00:00:00Z', message: 'A 일부 조건 미확정 · 조회 미실행' });
  assert.notEqual(message.kind, 'stopped');
  assert.equal(context.hasComparisonResult(message), false);
  assert.equal(context.isPreviousStop(message), false);
  assert.equal(context.historyBadge({ msgs: [message] }).text, 'A 일부 조건 미확정 · 조회 미실행');
  assert.equal(message.execution.stages[0].status, 'warning');
});

test('stored A partial remains partial after loading history', () => {
  const context = screen();
  const message = { role: 'ai', kind: 'stopped', runStatus: 'a_partial', cases: [],
    loadedFromHistory: true, jobSettled: true, finishedAt: '2026-11-03T00:00:00Z' };
  assert.equal(context.isPreviousStop(message), false);
  assert.equal(context.historyBadge({ msgs: [message] }).text, 'A 일부 조건 미확정 · 조회 미실행');
});

test('completed job snapshot with A partial settles polling', async () => {
  const context = screen();
  context.updateLiveProgress = () => {};
  context.readJson = () => { throw new Error('A completed snapshot must not poll again'); };
  const message = { role: 'ai', jobId: 'test', kind: 'run', execution: { stages: [] } };
  await context.pollExistingJob(message, {}, { done: true, events: [], result: {
    status: 'a_partial', stages: [], ui_cases: [], finished_at: '2026-11-03T00:00:00Z',
    message: 'A 일부 조건 미확정 · 조회 미실행' } });
  assert.equal(message.jobSettled, true);
  assert.equal(message.runStatus, 'a_partial');
  assert.notEqual(message.kind, 'stopped');
});

test('C partial remains a result with cards', () => {
  const context = screen();
  const message = { role: 'ai', execution: {} };
  context.receiveResult(message, { status: 'partial', stages: [], ui_cases: [{ series: [] }],
    finished_at: '2026-11-03T00:00:00Z' });
  assert.equal(context.hasComparisonResult(message), true);
  assert.equal(message.kind, 'result');
  assert.notEqual(context.historyBadge({ msgs: [message] }).text, 'A 일부 조건 미확정 · 조회 미실행');
});
