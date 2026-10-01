// Actual production controller + opt-in dependency; fake DOM/time/HTTP only.
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
  append(...items) { for (const item of items) { item.parentElement = this; this.children.push(item); } }
  prepend(...items) { for (const item of items) item.parentElement = this; this.children.unshift(...items); }
  replaceChildren(...items) { this.children = []; this.append(...items); }
  setAttribute(key, value) { this[key] = value; }
  addEventListener(key, fn) { this.listeners[key] = fn; }
}
const nodes = root => [root, ...root.children.flatMap(nodes)];
function harness({optIn = true, dependency = true} = {}) {
  let now = 0, timerId = 0, calls = 0, nextResponse;
  const timers = new Map(), host = new Element('main'), requests = [];
  const document = {hidden: false, createElement: tag => new Element(tag)};
  const payload = structuredClone(input.bundle);
  const window = {
    setTimeout(fn, delay) { timers.set(++timerId, {fn, at: now + Math.max(0, delay)}); return timerId; },
    clearTimeout(id) { timers.delete(id); },
  };
  const sandbox = {window, document, performance: {now: () => now}, AbortController, TextDecoder};
  if (dependency) vm.runInNewContext(input.auxiliary, sandbox);
  vm.runInNewContext(input.script, sandbox);
  const response = (body = payload) => new Response(JSON.stringify(body), {headers:{'content-type':'application/json'}});
  const controller = window.sdsctlMimic.create({host, standard: new Element('section'), url: '/test-only',
    supplementalContext: optIn ? payload.supplemental.context : null,
    request: async (...args) => {
      calls++; requests.push(args);
      return nextResponse ? nextResponse(...args) : response(optIn ? payload : {protocol:'sdsctl.web',version:1,display:payload.display});
    }});
  const find = id => nodes(host).find(node => node.id === id);
  const choose = (id, value) => { const node = find(id); node.value = value; node.listeners.change(); };
  const context = (changes = {}) => controller.context({available:true,active:true,stopped:false,...changes});
  const cell = token => {
    const region = payload.display.frames[find('mimic-mode').value || 'preferred'].screen.regions.find(r => r.token === token);
    return region && nodes(host).find(node => node.dataset.region === region.id);
  };
  const text = token => cell(token)?.children[0]?.textContent;
  return {controller, payload, response, window, host, document, requests, find, choose, context, text,
    raw: () => nodes(host).filter(n => n.dataset.valueStatus === 'raw_source'),
    favorite: () => find('mimic-favorites-states')?.textContent,
    get calls() { return calls; }, get pendingTimers() { return timers.size; },
    set request(fn) { nextResponse = fn; },
    newer({clock = 0, favorites = 0} = {}) {
      payload.supplemental.psi.sequence++;
      for (const frame of Object.values(payload.display.frames)) frame.sequence++;
      payload.supplemental.clock.sample_sequence += clock;
      payload.supplemental.favorites.sample_sequence += favorites;
    },
    async start() { context(); assert.equal(calls, 0); choose('mimic-presentation','mimic'); await flush(); },
    async tick(ms) {
      const end = now + ms; let count = 0;
      for (;;) {
        const task = [...timers.entries()].filter(([,v]) => v.at <= end).sort((a,b) => a[1].at-b[1].at)[0];
        if (!task) break;
        assert.ok(++count < 1000, 'timer spin');
        const [id, value] = task; now = value.at; timers.delete(id); value.fn(); await flush();
      }
      now = end; await flush();
    },
  };
}
function assertClock(h, expected = '21:26') { assert.equal(h.text('Time'), expected); }
function assertNoAux(h) { assert.notEqual(h.text('Time'), '21:26'); assert.equal(h.favorite(), ''); }
(async () => {
  // Both opt-in dependencies are required; an unsolicited script alone is inert.
  assert.throws(() => harness({dependency:false}));
  const ordinary = harness({optIn:false}); await ordinary.start();
  assert.ok(ordinary.raw().length); assert.equal(ordinary.find('mimic-favorites-states'), undefined);
  assert.notEqual(ordinary.text('Time'), '21:26'); ordinary.controller.stop();

  const basic = harness(); const original = JSON.stringify(basic.payload); await basic.start();
  assertClock(basic); assert.equal(basic.text('Day'), 'Sep17');
  assert.equal(basic.favorite().split('\n').length, 10);
  assert.equal((basic.favorite().match(/\d\d:/g) || []).length, 100);
  assert.ok(!/F0:|S0:|D0:/.test(basic.favorite()));
  for (const mode of ['simple','detail','preferred']) { basic.choose('mimic-mode',mode); assertClock(basic); }
  for (let i = 0; i < 50; i++) basic.context();
  assert.equal(basic.calls, 1); assert.equal(JSON.stringify(basic.payload), original);
  assert.equal(basic.requests[0][1].cache, 'no-store');
  assert.equal(basic.requests[0][1].redirect, 'error');
  basic.controller.stop(); assertNoAux(basic); assert.equal(basic.pendingTimers, 0);

  // New PSI never renews repeated clock/Favorites, even with falsely reset age.
  const duplicate = harness(); await duplicate.start();
  duplicate.request = () => { duplicate.newer(); return duplicate.response(); };
  await duplicate.tick(4500); assertClock(duplicate); assert.equal(duplicate.favorite(), '');
  await duplicate.tick(500); assertNoAux(duplicate); assert.ok(duplicate.raw().length);
  duplicate.newer({clock:1}); await duplicate.tick(250); assertClock(duplicate); assert.equal(duplicate.favorite(), '');
  duplicate.newer({favorites:1}); await duplicate.tick(250); assert.ok(duplicate.favorite());
  duplicate.controller.stop();

  // No response completion is needed for local independent field expiry.
  const stalled = harness(); stalled.payload.supplemental.clock.age_seconds = 2;
  stalled.payload.supplemental.favorites.age_seconds = 1; await stalled.start();
  let finish;
  stalled.request = () => new Promise(resolve => { finish = () => resolve(stalled.response()); });
  await stalled.tick(3000); assertClock(stalled, '—'); assert.ok(stalled.favorite()); assert.ok(stalled.raw().length);
  await stalled.tick(1000); assertNoAux(stalled); assert.ok(stalled.raw().length);
  await stalled.tick(1250); assert.equal(stalled.raw().length, 0);
  stalled.newer({clock:1,favorites:1}); finish(); await flush();
  assert.equal(stalled.raw().length, 0); assertNoAux(stalled);
  stalled.controller.stop();

  // Slow streamed body counts against the lease from request start, not receipt.
  const slow = harness(); slow.payload.supplemental.clock.age_seconds = 3;
  slow.payload.supplemental.favorites.age_seconds = 0;
  slow.request = () => new Response(new ReadableStream({start(c) {
    finish = () => { c.enqueue(new TextEncoder().encode(JSON.stringify(slow.payload))); c.close(); };
  }}), {headers:{'content-type':'application/json'}});
  await slow.start(); await slow.tick(1500); finish(); await flush(); assertClock(slow);
  slow.request = () => new Promise(() => {});
  await slow.tick(500); assertClock(slow, '—'); assert.ok(slow.favorite()); assert.ok(slow.raw().length);
  slow.controller.stop(); assertNoAux(slow);

  // Visibility, feature loss and page selection retain retired sample IDs.
  for (const kind of ['hidden','inactive','unavailable','standard']) {
    const h = harness(); await h.start();
    if (kind === 'hidden') { h.document.hidden = true; h.context(); }
    if (kind === 'inactive') h.context({active:false});
    if (kind === 'unavailable') h.context({available:false});
    if (kind === 'standard') h.choose('mimic-presentation','standard');
    assertNoAux(h); const stoppedCalls = h.calls; await h.tick(500); assert.equal(h.calls, stoppedCalls);
    h.newer(); h.document.hidden = false; h.context();
    if (kind === 'standard') h.choose('mimic-presentation','mimic');
    await flush(); assertNoAux(h);
    h.newer({clock:1,favorites:1}); await h.tick(250); assertClock(h); h.controller.stop();
  }

  // A response from the old generation cannot replace a newer accepted view.
  const late = harness(); await late.start();
  let old;
  late.request = () => new Promise(resolve => { old = resolve; }); await late.tick(250);
  const oldPayload = structuredClone(late.payload);
  late.context({active:false}); late.newer({clock:1,favorites:1}); late.request = () => late.response();
  late.context(); await flush(); assertClock(late);
  old(late.response(oldPayload)); await flush(); assertClock(late);
  late.controller.stop(); assertNoAux(late);

  // Context/body validation is atomic: no half-new scanner or auxiliary view.
  const mutations = [
    p => p.extra = 'PRIVATE', p => p.version = 2,
    p => p.supplemental.context.context_revision++, p => p.supplemental.context.profile_invalidation++,
    p => p.supplemental.context.profile_revision = 'f'.repeat(64),
    p => p.supplemental.context.session_id = '00000000-0000-0000-0000-000000000009',
    p => p.supplemental.context.stream_id = '00000000-0000-0000-0000-000000000009',
    p => p.supplemental.context.endpoint_id = '00000000-0000-0000-0000-000000000009',
    p => p.supplemental.psi.sequence++, p => p.supplemental.psi.age_seconds += 0.1,
    p => p.display.frames.detail.sequence++, p => p.display.failure = 'invalid_profile',
    p => p.supplemental.clock.value = '2026-02-30T21:26:59',
    p => p.supplemental.favorites.value = '2'.repeat(101),
    p => p.display.source_status = 'unavailable',
    p => { for (const f of Object.values(p.display.frames)) f.profile_refresh_pending = true; },
    p => { for (const f of Object.values(p.display.frames)) f.profile_status = 'refresh_failed'; },
  ];
  for (const mutate of mutations) {
    const h = harness(); await h.start(); const good = structuredClone(h.payload);
    mutate(h.payload); await h.tick(250); assert.equal(h.raw().length, 0); assertNoAux(h);
    assert.equal(h.find('mimic-last-failure').dataset.reason, 'frame_validation');
    assert.ok(!nodes(h.host).some(n => (n.textContent ?? '').includes('PRIVATE')));
    for (const key of Object.keys(h.payload)) delete h.payload[key];
    Object.assign(h.payload, good); h.newer(); await h.tick(2000); assertNoAux(h);
    h.newer({clock:1,favorites:1}); await h.tick(250);
    assert.equal(h.text('Time'), '21:26', `Recovery after ${mutate}: ${h.find('mimic-last-failure').textContent}`);
    h.controller.stop();
  }

  // Conflicting same-ID clock data clears only clock, never silently repairs it.
  const conflict = harness(); await conflict.start(); conflict.newer();
  conflict.payload.supplemental.clock.value = '2026-09-17T22:00:00';
  await conflict.tick(250); assertClock(conflict, '—'); assert.ok(conflict.favorite());
  conflict.payload.supplemental.clock.value = '2026-09-17T21:26:59';
  conflict.newer(); await conflict.tick(250); assertClock(conflict, '—');
  conflict.newer({clock:1}); await conflict.tick(250); assertClock(conflict); conflict.controller.stop();

  // An unavailable auxiliary clock never falls back to server-baked stale text.
  const unavailable = harness();
  unavailable.payload.supplemental.clock = {status:'unavailable',value:null,sample_sequence:null,age_seconds:null};
  for (const frame of Object.values(unavailable.payload.display.frames))
    for (const region of frame.screen.regions.filter(r => ['Day','Time'].includes(r.token))) {
      region.value_status = 'raw_source'; region.text = 'STALE_CLOCK';
    }
  await unavailable.start(); assertClock(unavailable, '—');
  assert.ok(!nodes(unavailable.host).some(n => n.textContent === 'STALE_CLOCK'));
  unavailable.controller.stop();

  // Respect the profile: no clock in Empty/rejected slots or large name fields.
  for (const status of ['empty','blank','configuration_unavailable','unsupported_selection']) {
    const h = harness();
    for (const frame of Object.values(h.payload.display.frames))
      for (const region of frame.screen.regions.filter(r => ['Day','Time'].includes(r.token))) {
        // Only use statuses supported by the actual frame validator.
        region.value_status = status === 'unsupported_selection' ? 'mode_unqualified' : status;
      }
    await h.start(); assert.notEqual(h.text('Time'), '21:26'); h.controller.stop();
  }
  const placement = harness();
  for (const frame of Object.values(placement.payload.display.frames)) {
    const region = frame.screen.regions.find(r => r.id === 'system');
    region.selection = 'configured'; region.token = 'Day'; region.value_status = 'raw_source'; region.text = 'KEEP';
  }
  await placement.start(); assert.ok(nodes(placement.host).some(n => n.dataset.region === 'system' && n.children[0].textContent === 'KEEP'));
  placement.controller.stop();

  // A stopped controller cannot receive a late streamed body or re-enable via status.
  const signedOut = harness(); await signedOut.start();
  let bodyDone;
  signedOut.request = () => new Response(new ReadableStream({start(c) {
    bodyDone = () => { c.enqueue(new TextEncoder().encode(JSON.stringify(signedOut.payload))); c.close(); };
  }}), {headers:{'content-type':'application/json'}});
  await signedOut.tick(250); signedOut.controller.stop();
  bodyDone(); await flush(); signedOut.context(); await signedOut.tick(6000);
  assertNoAux(signedOut); assert.equal(signedOut.raw().length, 0); assert.equal(signedOut.calls, 2);
  assert.equal(signedOut.pendingTimers, 0);

  // Transport failures retire auxiliary values as well as clearing scanner DOM.
  for (const response of [
    () => new Response('PRIVATE', {status:503}),
    () => new Response('x'.repeat(256 * 1024 + 1), {headers:{'content-type':'application/json'}}),
    () => new Response(new Uint8Array([255]), {headers:{'content-type':'application/json'}}),
  ]) {
    const h = harness(); await h.start(); h.request = response;
    await h.tick(250); assertNoAux(h); assert.equal(h.raw().length, 0);
    h.newer(); h.request = () => h.response(); await h.tick(2000); assertNoAux(h);
    h.newer({clock:1,favorites:1}); await h.tick(250); assertClock(h); h.controller.stop();
  }

  // Context is an explicit immutable binding, never rebound by new frame IDs.
  const pinned = harness(); await pinned.start();
  pinned.payload.display.session_id = pinned.payload.supplemental.context.session_id = '00000000-0000-0000-0000-000000000009';
  pinned.newer({clock:1,favorites:1}); await pinned.tick(250); assert.equal(pinned.raw().length, 0);
  pinned.context(); await pinned.tick(2000); assert.equal(pinned.raw().length, 0); pinned.controller.stop();

  // Consumers are independent: one sign-out cannot clear another page's values.
  const one = harness(), two = harness(); await one.start(); await two.start();
  one.controller.stop(); assertNoAux(one); assertClock(two); two.controller.stop();
  console.log('supplemental WebUI lifecycle passed');
})().catch(error => { console.error(error); process.exitCode = 1; });
