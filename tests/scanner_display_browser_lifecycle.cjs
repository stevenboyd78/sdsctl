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
  const requests = [];
  const timers = new Map(), host = new Element('main'), standard = new Element('section');
  const document = {hidden: false, createElement: tag => new Element(tag)};
  let frame = structuredClone(input.scenarios.held_trunk);
  for (const item of Object.values(frame.display.frames)) { item.sequence = 100; item.age_seconds = 0; }
  const window = {
    crypto: require('node:crypto').webcrypto,
    setTimeout(fn, delay) { timers.set(++nextTimer, {fn, at: now + Math.max(0, delay)}); return nextTimer; },
    clearTimeout(id) { timers.delete(id); },
  };
  vm.runInNewContext(input.script, {window, document, performance: {now: () => now}, AbortController, TextDecoder});
  const response = payload => new Response(JSON.stringify(payload), {headers: {'content-type': 'application/json'}});
  const controller = window.sdsctlMimic.create({host, standard, url: '/frame', request: async (...args) => {
    calls++; requests.push(args); if (nextResponse) return nextResponse(...args); return response(frame);
  }});
  const find = id => nodes(host).find(node => node.id === id);
  const choose = (id, value) => { const node = find(id); node.value = value; node.listeners.change(); };
  const context = (changes = {}) => controller.context({available: true, active: true, stopped: false, ...changes});
  const raw = () => nodes(host).filter(node => node.dataset.valueStatus === 'raw_source');
  return {
    host, document, window, requests, controller, find, choose, context, raw, response,
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
  assert.equal(h.find('mimic-last-failure').dataset.phase, undefined);
  // Source failures, stale responses and rejected profile reloads erase values.
  const j = harness(); await j.start();
  j.frame.display.failure = 'invalid_profile'; await j.tick(250); assert.equal(j.raw().length, 0);
  j.frame.display.failure = null; j.newer(); await j.tick(250); assert.ok(j.raw().length);
  j.request = () => j.response(input.scenarios.stale);
  await j.tick(250); assert.equal(j.raw().length, 0);
  // The central empty view must explain the actual state, never label every
  // missing screen as unsupported. Rejection still erases all prior values.
  for (const [state, expected] of Object.entries({
    current: 'Scanner layout unavailable.',
    waiting: 'Connected — waiting for a new scanner frame.',
    disconnected: 'Scanner disconnected — waiting for reconnection.',
    stale: 'Scanner data is stale — waiting for a fresh frame.',
    override: 'Scanner menu, popup or replay is active — normal display paused.',
    ambiguous_records: 'Scanner data is inconsistent — waiting for a matching frame.',
    unsupported_screen: 'This scanner screen is not supported by Mimic-SDS.',
  })) {
    const empty = harness(); await empty.start();
    const original = structuredClone(empty.frame);
    for (const f of Object.values(empty.frame.display.frames)) {
      f.status = state; f.screen = null; f.layout_basis = 'unavailable'; f.sequence++;
      for (const key of Object.keys(f.indicators)) f.indicators[key] = null;
    }
    if (state === 'disconnected') empty.frame.display.session_id = null;
    await empty.tick(250);
    assert.equal(empty.raw().length, 0);
    assert.ok(nodes(empty.host).some(n => n.textContent === expected));
    assert.ok(nodes(empty.host).some(n => n.textContent === 'Scanner screen layout is not currently available.'));
    assert.ok(!nodes(empty.host).some(n => n.textContent === 'Waiting for a supported scanner screen.'));
    for (const f of Object.values(empty.frame.display.frames)) {
      f.profile_revision = null; f.source = null; f.profile_status = 'unavailable'; f.sequence++;
    }
    await empty.tick(250);
    assert.ok(nodes(empty.host).some(n => n.textContent === 'An administrator must import a display profile for this scanner.'));
    for (const f of Object.values(original.display.frames)) f.sequence += 3;
    empty.request = () => empty.response(original);
    await empty.tick(250); assert.ok(empty.raw().length);
    empty.controller.stop();
  }
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
    assert.equal(note.dataset.phase, reason);
    assert.ok(note.textContent.includes('interruptions this page: 1'));
    assert.ok(!nodes(d.host).some(node => (node.textContent ?? '').includes(privateText)));
    const recorded = note.textContent;
    d.request = () => d.response(d.frame); d.newer(); await d.tick(2000);
    assert.ok(d.raw().length); assert.equal(note.textContent, recorded);
    d.request = request; await d.tick(250);
    assert.ok(note.textContent.includes('interruptions this page: 2'));
  }
  // Slow but finite headers/body are accepted within the request budget. Time
  // in transit still consumes the observation lease; it never starts on arrival.
  for (const body of [false, true]) {
    const slow = harness(); await slow.start();
    let finish;
    slow.newer();
    const payload = structuredClone(slow.frame);
    slow.request = () => body
      ? new Response(new ReadableStream({start(c) {
        finish = () => { c.enqueue(new TextEncoder().encode(JSON.stringify(payload))); c.close(); };
      }}), {headers:{'content-type':'application/json'}})
      : new Promise(resolve => { finish = () => resolve(slow.response(payload)); });
    await slow.tick(2950); // Poll started at 250 ms; response takes 2.7 seconds.
    assert.ok(slow.raw().length); assert.equal(slow.calls, 2);
    finish(); await flush();
    assert.ok(slow.raw().length);
    assert.equal(slow.find('mimic-last-failure').hidden, true);
    slow.request = () => new Promise(() => {});
    await slow.tick(2299); assert.ok(slow.raw().length);
    await slow.tick(1); assert.equal(slow.raw().length, 0); // 250 + 5000, not 2950 + 5000.
    slow.controller.stop();
  }
  // A response inside the transport budget can already be too old to display.
  const aged = harness(); await aged.start();
  let finishAged;
  aged.newer();
  for (const frame of Object.values(aged.frame.display.frames)) frame.age_seconds = 1;
  aged.request = () => new Promise(resolve => { finishAged = () => resolve(aged.response(aged.frame)); });
  await aged.tick(4850); finishAged(); await flush();
  assert.equal(aged.raw().length, 0);
  assert.equal(aged.find('mimic-last-failure').hidden, true); // Stale, not a transport failure.
  aged.controller.stop();
  // Timeouts distinguish waiting for headers from a stalled streamed body.
  for (const body of [false, true]) {
    const d = harness(); await d.start();
    d.request = (_url, {signal}) => body
      ? new Response(new ReadableStream({start(c) { signal.addEventListener('abort', () => c.error(new Error(privateText))); }}), {headers:{'content-type':'application/json'}})
      : new Promise((_resolve, reject) => signal.addEventListener('abort', () => reject(new Error(privateText))));
    await d.tick(4999); assert.ok(d.raw().length);
    await d.tick(1); assert.equal(d.raw().length, 0); // Expiry, before timeout.
    assert.equal(d.find('mimic-last-failure').hidden, true);
    await d.tick(250);
    assert.equal(d.find('mimic-last-failure').dataset.reason, 'request_timeout');
    assert.equal(d.find('mimic-last-failure').dataset.phase, body ? 'response_body' : 'request');
    assert.ok(d.find('mimic-last-failure').textContent.includes('elapsed 5000 ms'));
    assert.ok(d.find('mimic-last-failure').textContent.includes(`headers: ${body ? '0 ms' : 'not reached'}`));
    assert.ok(d.find('mimic-last-failure').textContent.includes('first byte: not reached'));
    assert.ok(d.find('mimic-last-failure').textContent.includes('body complete: not reached; bytes read: 0'));
    d.controller.stop();
  }
  // Delayed headers and partial body progress are measured separately, without
  // retaining any response text or resetting the original five-second deadline.
  const partial = harness(); await partial.start();
  let acceptHeaders, bodyController;
  partial.request = (_url, {signal}) => new Promise(resolve => {
    acceptHeaders = () => resolve(new Response(new ReadableStream({start(c) {
      bodyController = c;
      signal.addEventListener('abort', () => c.error(new Error(privateText)));
    }}), {headers: {'content-type':'application/json'}}));
  });
  await partial.tick(650); acceptHeaders(); await flush();
  await partial.tick(300); bodyController.enqueue(new TextEncoder().encode('priv')); await flush();
  await partial.tick(4300);
  const partialNote = partial.find('mimic-last-failure');
  assert.equal(partialNote.dataset.phase, 'response_body');
  assert.ok(partialNote.textContent.includes('headers: 400 ms; first byte: 700 ms; body complete: not reached; bytes read: 4'));
  assert.ok(partialNote.textContent.includes('elapsed 5000 ms'));
  const savedPartial = partialNote.textContent;
  partial.request = () => partial.response(partial.frame); partial.newer();
  await partial.tick(2000);
  assert.ok(partial.raw().length); assert.equal(partialNote.textContent, savedPartial);
  // A completed invalid JSON body records complete body time and byte count.
  partial.request = () => new Response('bad', {headers:{'content-type':'application/json'}});
  await partial.tick(250);
  assert.equal(partialNote.dataset.phase, 'json_decode');
  assert.ok(partialNote.textContent.includes('headers: 0 ms; first byte: 0 ms; body complete: 0 ms; bytes read: 3'));
  // Inactivity abort is not a new interruption, and cannot replace retained data.
  partial.request = (_url, {signal}) => new Promise((_resolve, reject) => {
    signal.addEventListener('abort', () => reject(new Error(privateText)));
  });
  const beforeCancel = partialNote.textContent;
  await partial.tick(2000); partial.context({active:false}); await flush();
  assert.equal(partialNote.textContent, beforeCancel);
  const cancelledCalls = partial.calls; await partial.tick(10000);
  assert.equal(partial.calls, cancelledCalls);
  const late = harness(); await late.start();
  let lateResolve;
  late.request = () => new Promise(resolve => { lateResolve = resolve; });
  await late.tick(6250); late.newer(); lateResolve(late.response(late.frame)); await flush();
  assert.equal(late.raw().length, 0);
  assert.equal(late.find('mimic-last-failure').dataset.reason, 'request_timeout');
  assert.equal(late.find('mimic-last-failure').dataset.phase, 'request');
  assert.ok(late.find('mimic-last-failure').textContent.includes('headers: not reached'));
  // An abort-ignoring body also cannot replace the phase/progress at timeout
  // with late JSON validation or publishing. No new request overlaps this read.
  const lateBody = harness(); await lateBody.start();
  let finishBody;
  lateBody.request = () => new Response(new ReadableStream({start(c) { finishBody = c; }}), {headers:{'content-type':'application/json'}});
  await lateBody.tick(6250);
  assert.equal(lateBody.calls, 2);
  finishBody.enqueue(new TextEncoder().encode(JSON.stringify(lateBody.frame))); finishBody.close(); await flush();
  assert.equal(lateBody.raw().length, 0);
  assert.equal(lateBody.find('mimic-last-failure').dataset.phase, 'response_body');
  assert.ok(lateBody.find('mimic-last-failure').textContent.includes('first byte: not reached; body complete: not reached; bytes read: 0'));
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
  // Timing traces are explicit, bounded and independent of auth/freshness.
  const trace = harness(); await trace.start();
  assert.equal(trace.requests[0][1].headers, undefined);
  trace.request = (_url, options) => {
    const response = trace.response(trace.frame);
    if (options.headers) response.headers.set('x-sdsctl-mimic-trace', options.headers['X-SDSCTL-Mimic-Trace']);
    return response;
  };
  trace.find('mimic-trace-start').listeners.click(); await trace.tick(250);
  const firstLabel = trace.requests.at(-1)[1].headers['X-SDSCTL-Mimic-Trace'];
  assert.match(firstLabel, /^[0-9a-f]{16}-1$/);
  assert.equal(trace.requests.at(-1)[1].credentials, 'same-origin');
  assert.ok(trace.find('mimic-trace-status').textContent.includes('App acknowledgement: received'));
  await trace.tick(250);
  assert.equal(trace.requests.at(-1)[1].headers['X-SDSCTL-Mimic-Trace'], firstLabel.replace(/-1$/, '-2'));
  await trace.tick(120000);
  assert.equal(trace.requests.at(-1)[1].headers, undefined);
  assert.ok(trace.find('mimic-trace-status').textContent.includes('tracing is off'));
  assert.equal(trace.raw().length, 0); // Tracing never renews repeated-sequence freshness.
  trace.controller.stop();
  for (const stop of ['button', 'inactive', 'hidden', 'signout']) {
    const t = harness(); await t.start();
    t.find('mimic-trace-start').listeners.click(); await t.tick(250);
    if (stop === 'button') t.find('mimic-trace-start').listeners.click();
    if (stop === 'inactive') t.context({active:false});
    if (stop === 'hidden') { t.document.hidden = true; t.context(); }
    if (stop === 'signout') t.controller.stop();
    assert.ok(!t.find('mimic-trace-status').textContent.includes(firstLabel));
    t.document.hidden = false; t.context(); await t.tick(250);
    if (stop !== 'signout') assert.equal(t.requests.at(-1)[1].headers, undefined);
    t.controller.stop();
  }
  const tracedFailure = harness(); await tracedFailure.start();
  tracedFailure.find('mimic-trace-start').listeners.click();
  tracedFailure.request = (_url, {signal}) => new Promise((_resolve, reject) => signal.addEventListener('abort', () => reject(new Error(privateText))));
  await tracedFailure.tick(5250);
  const failureLabel = tracedFailure.requests.at(-1)[1].headers['X-SDSCTL-Mimic-Trace'];
  assert.ok(tracedFailure.find('mimic-last-failure').textContent.includes(`Trace ID: ${failureLabel}; App acknowledgement: not confirmed`));
  assert.equal(tracedFailure.raw().length, 0);
  tracedFailure.controller.stop();
  assert.equal(tracedFailure.find('mimic-last-failure').textContent, '');
  const unavailableTrace = harness(); await unavailableTrace.start();
  unavailableTrace.window.crypto = undefined;
  unavailableTrace.find('mimic-trace-start').listeners.click();
  await unavailableTrace.tick(250);
  assert.equal(unavailableTrace.requests.at(-1)[1].headers, undefined);
  assert.ok(unavailableTrace.raw().length);
  assert.ok(unavailableTrace.find('mimic-trace-status').textContent.includes('unavailable'));
  unavailableTrace.controller.stop();
  console.log('controller freshness, identity, demand, stop and recovery passed');
})().catch(error => { console.error(error); process.exitCode = 1; });
