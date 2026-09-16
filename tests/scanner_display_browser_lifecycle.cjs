// Deterministic DOM/clock harness for the real browser controller, no network.
const assert = require('node:assert/strict');
const vm = require('node:vm');
const input = JSON.parse(require('node:fs').readFileSync(0, 'utf8'));
const flush = async () => { for (let i = 0; i < 12; i++) await new Promise(setImmediate); };
class Element {
  constructor(tag) {
    this.tag = tag; this.children = []; this.dataset = {}; this.listeners = {};
    this.style = {setProperty: (key, value) => { this.style[key] = value; }};
    this.classList = {add() {}};
  }
  append(...children) { for (const child of children) { child.parentElement = this; this.children.push(child); } }
  prepend(...children) { for (const child of children) child.parentElement = this; this.children.unshift(...children); }
  replaceChildren(...children) { this.children = []; this.append(...children); }
  setAttribute(key, value) { this[key] = value; }
  addEventListener(key, callback) { this.listeners[key] = callback; }
}
function nodes(root) { return [root, ...root.children.flatMap(nodes)]; }
function harness() {
  let now = 0, nextTimer = 0, calls = 0, nextResponse = null;
  const timers = new Map(), host = new Element('main'), standard = new Element('section');
  const document = {hidden: false, createElement: tag => new Element(tag)};
  let frame = structuredClone(input.scenarios.held_trunk);
  for (const item of Object.values(frame.display.frames)) { item.sequence = 100; item.age_seconds = 0; }
  const window = {
    setTimeout(fn, delay) { timers.set(++nextTimer, {fn, at: now + Math.max(0, delay)}); return nextTimer; },
    clearTimeout(id) { timers.delete(id); },
  };
  vm.runInNewContext(input.script, {window, document, performance: {now: () => now}, AbortController, TextDecoder});
  const response = payload => new Response(JSON.stringify(payload), {headers: {'content-type': 'application/json'}});
  const controller = window.sdsctlMimic.create({host, standard, url: '/frame', request: async (...args) => {
    calls++; if (nextResponse) return nextResponse(...args); return response(frame);
  }});
  const find = id => nodes(host).find(node => node.id === id);
  const choose = (id, value) => { const node = find(id); node.value = value; node.listeners.change(); };
  const context = (changes = {}) => controller.context({available: true, active: true, stopped: false, ...changes});
  const raw = () => nodes(host).filter(node => node.dataset.valueStatus === 'raw_source');
  return {
    host, document, controller, find, choose, context, raw, response,
    cells: () => nodes(host).filter(node => node.dataset.region),
    get frame() { return frame; }, get calls() { return calls; },
    set request(fn) { nextResponse = fn; },
    async start() { context(); assert.equal(calls, 0); choose('mimic-presentation', 'mimic'); await flush(); },
    async tick(duration) {
      const end = now + duration;
      for (;;) {
        const next = [...timers.entries()].filter(([, value]) => value.at <= end).sort((a,b) => a[1].at-b[1].at)[0];
        if (!next) break;
        const [id, task] = next; now = task.at; timers.delete(id); task.fn(); await flush();
      }
      now = end; await flush();
    },
    newer() { for (const item of Object.values(frame.display.frames)) item.sequence++; },
  };
}
(async () => {
  const h = harness(); await h.start(); assert.ok(h.raw().length);
  // Ordinary status/events must not restart polling or clear an unchanged view.
  const originalCells = h.cells(), originalCalls = h.calls;
  for (let i = 0; i < 50; i++) h.context();
  assert.deepEqual(h.cells(), originalCells); assert.equal(h.calls, originalCalls);
  // A repeated sequence with age reset to zero must expire, and stay expired.
  await h.tick(5250); assert.equal(h.raw().length, 0);
  await h.tick(1000); assert.equal(h.raw().length, 0);
  h.context({active:false}); h.context(); await flush(); assert.equal(h.raw().length, 0);
  h.newer(); await h.tick(250); assert.ok(h.raw().length);
  // Backwards sequences clear values; a genuinely new sequence recovers.
  for (const f of Object.values(h.frame.display.frames)) f.sequence -= 2;
  await h.tick(250); assert.equal(h.raw().length, 0);
  assert.equal(h.find('mimic-last-failure').dataset.reason, 'frame_sequence');
  for (const f of Object.values(h.frame.display.frames)) f.sequence += 3;
  await h.tick(2000); assert.ok(h.raw().length);
  // Wrong endpoint is never adopted, even after clearing the current screen.
  const endpoint = h.frame.display.endpoint_id;
  h.frame.display.endpoint_id = '00000000-0000-0000-0000-000000000099';
  await h.tick(250); assert.equal(h.raw().length, 0);
  assert.equal(h.find('mimic-last-failure').dataset.reason, 'endpoint_identity');
  h.frame.display.endpoint_id = endpoint; h.newer(); await h.tick(2000); assert.ok(h.raw().length);
  // New connection/feed identity permits a reset sequence, never old DOM.
  h.frame.display.stream_id = '00000000-0000-0000-0000-000000000098';
  for (const f of Object.values(h.frame.display.frames)) f.sequence = 0;
  await h.tick(250); assert.ok(h.raw().length);
  h.document.hidden = true; h.context(); assert.equal(h.raw().length, 0);
  const hiddenCalls = h.calls; await h.tick(1000); assert.equal(h.calls, hiddenCalls);
  h.document.hidden = false; h.newer(); h.context(); await flush(); assert.ok(h.raw().length);
  h.choose('mimic-presentation', 'standard');
  const inactiveCalls = h.calls; await h.tick(1000); assert.equal(h.calls, inactiveCalls);
  // Even a transport that ignores abort cannot repopulate a signed-out screen.
  let resolve;
  h.request = () => new Promise(done => { resolve = done; });
  h.choose('mimic-presentation', 'mimic'); await flush();
  h.controller.stop(); resolve(h.response(h.frame)); await flush();
  h.context(); // A late status callback cannot undo terminal sign-out.
  assert.equal(h.raw().length, 0);
  const stoppedCalls = h.calls; await h.tick(5000); assert.equal(h.calls, stoppedCalls);
  assert.equal(h.find('mimic-last-failure').textContent, '');
  assert.equal(h.find('mimic-last-failure').dataset.reason, undefined);
  // Source failures, stale responses and rejected profile reloads erase values.
  const j = harness(); await j.start();
  j.frame.display.failure = 'invalid_profile'; await j.tick(250); assert.equal(j.raw().length, 0);
  j.frame.display.failure = null; j.newer(); await j.tick(250); assert.ok(j.raw().length);
  j.request = () => j.response(input.scenarios.stale);
  await j.tick(250); assert.equal(j.raw().length, 0);
  const k = harness();
  for (const f of Object.values(k.frame.display.frames)) f.age_seconds = 5;
  await k.start(); assert.equal(k.raw().length, 0); // Deadline exactly zero.
  // Site hold applies to configured SiteName cells, independently of other holds.
  const s = harness();
  for (const f of Object.values(s.frame.display.frames)) f.indicators.site_hold = true;
  await s.start(); s.choose('mimic-mode', 'detail');
  function checkSite(held) {
    const frame = s.frame.display.frames.detail;
    for (const region of frame.screen.regions.filter(r => r.token === 'SiteName')) {
      const cell = s.cells().find(c => c.dataset.region === region.id);
      assert.ok(cell); assert.equal(cell.dataset.hold, held ? 'on' : 'off');
      const reversed = region.reverse_colors || held;
      assert.equal(cell.style.color, '#' + region.stored_color[reversed ? 'background' : 'text']);
      assert.equal(cell.style.backgroundColor, '#' + region.stored_color[reversed ? 'text' : 'background']);
    }
  }
  checkSite(true);
  for (const f of Object.values(s.frame.display.frames)) f.indicators.site_hold = false;
  s.newer(); await s.tick(250); checkSite(false);
  // Confirmed off icons contain no placeholder dash; missing sources still do.
  const icon = s.frame.display.frames.detail.screen.regions.find(r => r.id === 'icon_1');
  assert.ok(icon); icon.token = 'REC'; icon.value_status = 'raw_source'; icon.text = 'REC';
  s.newer(); await s.tick(250);
  const iconCell = () => s.cells().find(c => c.dataset.region === icon.id);
  assert.equal(iconCell().children[0].textContent, 'REC');
  icon.value_status = 'blank'; icon.text = null;
  s.newer(); await s.tick(250); assert.equal(iconCell().children.length, 0);
  icon.value_status = 'data_unavailable';
  s.newer(); await s.tick(250); assert.equal(iconCell().children[0].textContent, '—');
  // Bounded, phase-only diagnostics survive recovery; private error/body text
  // must never enter the DOM or replace existing validation/freshness checks.
  const privateText = 'PRIVATE_BODY_URL_CREDENTIAL_SENTINEL';
  for (const [reason, request] of [
    ['request', () => { throw new Error(privateText); }],
    ['http_status', () => new Response(privateText, {status:503})],
    ['content_type', () => new Response(privateText, {headers:{'content-type':'text/html'}})],
    ['response_body', () => new Response(new ReadableStream({start(c) { c.error(new Error(privateText)); }}), {headers:{'content-type':'application/json'}})],
    ['response_size', () => new Response('x'.repeat(256 * 1024 + 1), {headers:{'content-type':'application/json'}})],
    ['utf8_decode', () => new Response(new Uint8Array([0xff]), {headers:{'content-type':'application/json'}})],
    ['json_decode', () => new Response(privateText, {headers:{'content-type':'application/json'}})],
    ['frame_validation', () => new Response(JSON.stringify({private:privateText}), {headers:{'content-type':'application/json'}})],
  ]) {
    const d = harness(); await d.start(); d.request = request;
    await d.tick(250);
    assert.equal(d.raw().length, 0);
    const note = d.find('mimic-last-failure');
    assert.equal(note.dataset.reason, reason); assert.equal(note.hidden, false);
    assert.ok(note.textContent.includes('interruptions this page: 1'));
    assert.ok(!nodes(d.host).some(node => (node.textContent ?? '').includes(privateText)));
    const recorded = note.textContent;
    d.request = () => d.response(d.frame); d.newer(); await d.tick(2000);
    assert.ok(d.raw().length); assert.equal(note.textContent, recorded);
    d.request = request; await d.tick(250);
    assert.ok(note.textContent.includes('interruptions this page: 2'));
  }
  // Fetch and streamed body timeouts use a fixed timeout reason; cancellation
  // of an inactive/signed-out request must not create a spurious diagnostic.
  for (const body of [false, true]) {
    const d = harness(); await d.start();
    d.request = (_url, {signal}) => body
      ? new Response(new ReadableStream({start(c) { signal.addEventListener('abort', () => c.error(new Error(privateText))); }}), {headers:{'content-type':'application/json'}})
      : new Promise((_resolve, reject) => signal.addEventListener('abort', () => reject(new Error(privateText))));
    await d.tick(2250); assert.equal(d.raw().length, 0);
    assert.equal(d.find('mimic-last-failure').dataset.reason, 'request_timeout');
    assert.ok(d.find('mimic-last-failure').textContent.includes('elapsed 2000 ms'));
    d.controller.stop();
  }
  const late = harness(); await late.start();
  let lateResolve;
  late.request = () => new Promise(resolve => { lateResolve = resolve; });
  await late.tick(3250); late.newer(); lateResolve(late.response(late.frame)); await flush();
  assert.equal(late.raw().length, 0);
  assert.equal(late.find('mimic-last-failure').dataset.reason, 'request_timeout');
  const renderFailure = harness(); await renderFailure.start();
  const makeElement = renderFailure.document.createElement;
  renderFailure.document.createElement = () => {
    renderFailure.document.createElement = makeElement;
    throw new Error(privateText);
  };
  renderFailure.newer(); await renderFailure.tick(250);
  assert.equal(renderFailure.raw().length, 0);
  assert.equal(renderFailure.find('mimic-last-failure').dataset.reason, 'rendering');
  assert.ok(!nodes(renderFailure.host).some(node => (node.textContent ?? '').includes(privateText)));
  console.log('controller freshness, identity, demand, stop and recovery passed');
})().catch(error => { console.error(error); process.exitCode = 1; });
