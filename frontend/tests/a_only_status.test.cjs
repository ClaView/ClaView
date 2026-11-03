const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

function screen() {
  const CV = { app: { init() {} }, EXAMPLES: [], createResult() {} };
  const context = vm.createContext({ CV, window: { CV }, saveStore() {},
    el(tag, attrs, ...children) { return { tag, ...attrs, children,
      append(...items) { this.children.push(...items); } }; } });
  for (const file of ['comparison_flow.js', 'live_app.js', 'service_ux.js']) {
    vm.runInContext(fs.readFileSync(path.join(__dirname, '..', 'src', file), 'utf8'), context);
  }
  return context;
}

function received(context, status, extra = {}) {
  const message = { role: 'ai', kind: 'run', execution: {} };
  context.receiveResult(message, { status, stages: [], ui_cases: [],
    finished_at: '2026-11-03T00:00:00Z', run_id: 'test', ...extra });
  return message;
}

test('A-only completion has its own badge without claiming query completion', () => {
  const context = screen();
  const message = received(context, 'a_verified');
  assert.notEqual(message.kind, 'stopped');
  assert.equal(context.hasComparisonResult(message), false);
  assert.equal(context.isPreviousStop(message), false);
  assert.equal(context.historyBadge({ msgs: [message] }).text, 'A 조건 확정 · 조회 미실행');
});

test('old A-only records classified as stopped are not shown as previous failures', () => {
  const context = screen();
  const message = { role: 'ai', kind: 'stopped', runStatus: 'a_verified', cases: [],
    loadedFromHistory: true, finishedAt: '2026-11-03T00:00:00Z' };
  assert.equal(context.isPreviousStop(message), false);
  assert.equal(context.historyBadge({ msgs: [message] }).text, 'A 조건 확정 · 조회 미실행');
});

test('confirmation remains pending rather than A completion', () => {
  const context = screen();
  const message = received(context, 'needs_confirmation', { confirmation: { groups: [] } });
  assert.equal(context.historyBadge({ msgs: [message] }).text, '확인 대기');
  assert.equal(context.isPreviousStop(message), false);
});

test('genuine failures keep the stopped state', () => {
  const context = screen();
  const message = received(context, 'failed');
  assert.equal(message.kind, 'stopped');
  assert.equal(context.isPreviousStop(message), true);
  assert.equal(context.historyBadge({ msgs: [message] }).text, '이전 중단 기록');
});

test('C results retain their query completion badge', () => {
  const context = screen();
  const message = received(context, 'ok', { ui_cases: [{ series: [] }] });
  assert.equal(context.hasComparisonResult(message), true);
  assert.equal(context.historyBadge({ msgs: [message] }).text, '조회 완료');
});

test('not-run explanation keeps intentional scope as a possible reason', () => {
  const context = screen();
  const note = context.CV.ux.text('앞 단계 미확정 또는 지정된 검수 범위 때문에 실행하지 않았습니다.');
  assert.match(note, /확인 범위/);
  assert.match(note, /미확정/);
});

test('A-only warning does not claim that retrieval ran', () => {
  const context = screen();
  context.CV.ux.simplifyProgress = root => root;
  context.stepsEl = () => ({ querySelector() { return {}; } });
  const message = received(context, 'a_verified', { a_warnings: [{ code: 'basis_unconfirmed' }],
    message: 'A 조건 확정 · 조회 미실행' });
  const body = context.el('div');
  context.fillAi(body, message, { msgs: [message] });
  const rendered = JSON.stringify(body);
  assert.match(rendered, /A 검사 경고 1건 · 조회 미실행/);
  assert.doesNotMatch(rendered, /조회는 진행했습니다|이전 실행의 중단 기록/);
});
