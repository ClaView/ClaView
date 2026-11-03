const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

function page(result) {
  const requests = [];
  const conversation = { msgs: [] };
  const context = vm.createContext({
    CV: { app: { init() {} }, EXAMPLES: [] },
    S: { convs: [conversation], running: null }, D: { sendBtn: {} },
    active: () => conversation, saveStore() {}, renderThread() {}, renderHist() {},
    location: { protocol: 'http:' }, setTimeout, Date,
  });
  for (const name of ['comparison_flow.js', 'live_app.js']) {
    vm.runInContext(fs.readFileSync(path.join(__dirname, '../src', name), 'utf8'), context);
  }
  context.updateLiveProgress = () => {};
  context.readJson = async (url, options = {}) => {
    requests.push({ url, ...options });
    if (url === '/api/info') return { token: 'test-token', server_pid: 1 };
    if (url === '/api/records/load') return { job_id: 'saved-job', loaded: true };
    if (url === '/api/jobs/saved-job') return { done: true, events: [], result };
    throw new Error('Unexpected request');
  };
  return { context, requests, conversation };
}

test('normal service has no mode label while synthetic data is explicitly marked', () => {
  const { context } = page({});
  assert.match(context.connectionText({ claview_mode: 'synthetic', hcx_ready: true, kosis_ready: true }), /테스트용 · 합성 데이터/);
  const normal = context.connectionText({ claview_mode: 'live', a_ready: true, hcx_ready: true, kosis_ready: true });
  assert.match(normal, /질문할 수/);
  assert.doesNotMatch(normal, /실호출|모드|테스트|합성/);
  assert.match(context.connectionText({ claview_mode: 'live', a_ready: false, hcx_ready: true, kosis_ready: true }), /설정을 확인/);
});

test('loading a completed record restores output without query or resume submission', async () => {
  const { context, requests, conversation } = page({ status: 'ok', stages: [],
    ui_cases: [{ series: [] }], run_id: 'run-a', message: 'saved', calls: { A: 1 } });
  await context.loadServerRecord({ query: '2024년 M2', run_id: 'run-a' });
  assert.deepEqual(requests.map(r => r.url), ['/api/info', '/api/records/load', '/api/jobs/saved-job']);
  assert.deepEqual(JSON.parse(requests[1].body), { run_id: 'run-a' });
  assert.equal(requests[1].headers['X-Claview-Token'], 'test-token');
  const message = conversation.msgs.at(-1);
  assert.equal(message.savedRecord, true);
  assert.equal(message.runStatus, 'ok');
  assert.equal(message.cases.length, 1);
  assert.equal(context.S.running, null);
});

test('loading a pending record restores the card without answering automatically', async () => {
  const confirmation = { kind: 'a_view', groups: [{ question_id: 'period' }] };
  const { context, requests, conversation } = page({ status: 'needs_confirmation', stages: [],
    ui_cases: [], run_id: 'run-b', confirmation, message: '기간을 선택해 주세요.' });
  await context.loadServerRecord({ query: 'M2', run_id: 'run-b' });
  const message = conversation.msgs.at(-1);
  assert.equal(message.confirmation, confirmation);
  assert.equal(message.serverRecordLoaded, true);
  assert.equal(message.resumedJobId, undefined);
  assert.equal(requests.some(r => r.url === '/api/resume' || r.url === '/api/query'), false);
});

test('record statuses distinguish A completion from retrieval completion', () => {
  const { context } = page({});
  assert.match(context.recordStatusLabel('a_verified'), /조회 미실행/);
  assert.equal(context.recordStatusLabel('ok'), '조회 완료');
  assert.equal(context.recordStatusLabel('needs_confirmation'), '확인 대기');
});

test('saved synthetic results retain their source after the server mode changes', () => {
  const { context } = page({});
  const message = context.newMessage('example');
  context.receiveResult(message, { status: 'ok', stages: [], ui_cases: [], claview_mode: 'synthetic' });
  const saved = JSON.parse(JSON.stringify(message));
  assert.equal(saved.mode, 'synthetic');
  assert.equal(saved.execution.dataMode, 'synthetic');
  assert.match(context.recordOriginText(saved), /합성.*실제 통계 수치가 아닙니다/);
  context.receiveResult(message, { status: 'ok', stages: [], ui_cases: [], claview_mode: 'live' });
  assert.equal(context.recordOriginText(message), '');
  assert.match(context.recordOriginText({ mode: 'live' }), /출처 정보가 없습니다/);
});
