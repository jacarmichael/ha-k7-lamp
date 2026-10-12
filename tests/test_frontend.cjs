const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const source = fs.readFileSync(path.join(__dirname, '../custom_components/k7_lamp/frontend/k7-schedule-card.js'), 'utf8');

function environment(navigator = {}) {
  let Card;
  const context = { HTMLElement: class { isConnected = true; }, window: {}, navigator,
    customElements: { define: (_, cls) => Card = cls } };
  vm.createContext(context);
  vm.runInContext(source, context);
  const card = new Card();
  card._render = () => {};
  card._lamp = {entry_id: 'one'};
  return {card, context};
}

test('moon form values are captured before rendering removes the form', async () => {
  const {card} = environment();
  card._panel = 'moon';
  card.shadowRoot = {
    querySelector: sel => card._panel === 'moon' ? {value: '15', checked: sel.includes('enabled')} : null,
    querySelectorAll: () => [{dataset: {moonch: '1'}, value: '100'}],
  };
  let sent;
  card._ws = async msg => { sent = msg; return {lamps: [{entry_id: 'one', moon: {config: {enabled: true}}}]}; };
  await card._action('moon-save', {});
  assert.equal(sent.type, 'k7_lamp/set_moonlight');
  assert.equal(sent.enabled, true);
  assert.equal(sent.follow_moon, false);
  assert.equal(sent.channels['1'], 100);
  assert.equal(card._msg.kind, 'info');
});

test('selection keeps minutes and duplicate times separate', async () => {
  const {card} = environment();
  card._draft = {schedule: [[8,0,0,0,0,0,0,0], [8,30,20,0,0,0,0,0], [8,30,40,0,0,0,0,0]]};
  card._slot = 0;
  await card._action('next', {});
  assert.equal(card._slotIndex(), 1);
  await card._action('next', {});
  assert.equal(card._slotIndex(), 2);
  await card._action('prev', {});
  assert.equal(card._slotIndex(), 1);
});

test('real time alignment retains disabled slots', () => {
  const {context} = environment();
  context.rows = Array.from({length: 21}, () => [0,0,0,0,0,0,0,0]).concat([
    [8,45,0,0,0,0,0,0], [9,30,20,0,0,0,0,0], [21,15,0,0,0,0,0,0],
  ]);
  const result = vm.runInContext('alignDayStart(rows, 11)', context);
  assert.deepEqual(JSON.parse(JSON.stringify(result)), Array.from({length: 21}, () => [0,0,0,0,0,0,0,0]).concat([
    [10,15,0,0,0,0,0,0], [11,0,20,0,0,0,0,0], [22,45,0,0,0,0,0,0],
  ]));
});

test('midnight dark boundary is refused rather than silently disabled', () => {
  const {context} = environment();
  context.rows = [[8,0,0,0,0,0,0,0], [9,0,40,0,0,0,0,0], [21,0,0,0,0,0,0,0]];
  assert.throws(() => vm.runInContext('shiftRows(rows, 3)', context), /dark boundary/);
});

for (const action of ['moon', 'acc', 'save', 'apply', 'export', 'cancel']) {
  test(`leaving QR import for ${action} stops the camera`, async () => {
    const {card} = environment();
    let stopped = 0;
    card._panel = 'import';
    card._stream = {getTracks: () => [{stop: () => stopped++}]};
    card._ws = async () => ({payload: 'qr'});
    await card._action(action, {});
    assert.equal(stopped, 1);
    assert.equal(card._stream, null);
  });
}

test('permission granted after leaving import cannot restart the camera', async () => {
  let grant;
  const {card} = environment({mediaDevices: {getUserMedia: () => new Promise(resolve => grant = resolve)}});
  let stopped = 0;
  card._panel = 'import';
  const pending = card._startCamera();
  await card._action('moon', {});
  grant({getTracks: () => [{stop: () => stopped++}]});
  await pending;
  assert.equal(stopped, 1);
  assert.equal(card._stream, null);
});

test('camera permission completing after disconnection releases its stream', async () => {
  let grant;
  const {card} = environment({mediaDevices: {getUserMedia: () => new Promise(resolve => grant = resolve)}});
  let stopped = 0;
  card._panel = 'import';
  const pending = card._startCamera();
  card.isConnected = false;
  card.disconnectedCallback();
  grant({getTracks: () => [{stop: () => stopped++}]});
  await pending;
  assert.equal(stopped, 1);
});

test('pending updates can be retried without changing an already-saved draft', () => {
  const {card} = environment();
  const schedule = Array.from({length: 24}, (_, h) => [h,0,0,0,0,0,0,0]);
  card._draft = {manual: [50,50,50,50,50,50], schedule};
  card._lamp = {entry_id: 'one', available: true, pending: true, program: true, target: card._draft};
  card._channels = ['White', 'Royal Blue', 'Green', 'UV', 'Cyan', 'Red'];
  card._visible = card._channels.map(() => true);
  const html = card._body();
  assert.equal(card._dirty(), false);
  assert.match(html, /data-act="apply" >Update target/);
});

test('per-lamp original backup loads exact times even with alignment enabled', () => {
  const {card} = environment();
  const schedule = [[8,30,0,0,0,0,0,0], [9,30,40,0,0,0,0,0], [21,0,0,0,0,0,0,0]];
  card._lamp = {day_start: 11, original: {name: 'Original (one)', manual: [50,50,50,50,50,50], schedule}};
  card._pick('original');
  assert.deepEqual(JSON.parse(JSON.stringify(card._draft.schedule)), schedule);
});
