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
  const controller = window.sdsctlMimic.create({host, standard, url: '/frame', request: async () => {
    calls++; if (nextResponse) return nextResponse(); return response(frame);
  }});
  const find = id => nodes(host).find(node => node.id === id);
  const choose = (id, value) => { const node = find(id); node.value = value; node.listeners.change(); };
  const context = (changes = {}) => controller.context({available: true, active: true, stopped: false, ...changes});
  const raw = () => nodes(host).filter(node => node.dataset.valueStatus === 'raw_source');
  return {
    host, document, controller, find, choose, context, raw, response,
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
  // A repeated sequence with age reset to zero must expire, and stay expired.
  await h.tick(5250); assert.equal(h.raw().length, 0);
  await h.tick(1000); assert.equal(h.raw().length, 0);
  h.context({active:false}); h.context(); await flush(); assert.equal(h.raw().length, 0);
  h.newer(); await h.tick(250); assert.ok(h.raw().length);
  // Backwards sequences clear values; a genuinely new sequence recovers.
  for (const f of Object.values(h.frame.display.frames)) f.sequence -= 2;
  await h.tick(250); assert.equal(h.raw().length, 0);
  for (const f of Object.values(h.frame.display.frames)) f.sequence += 3;
  await h.tick(2000); assert.ok(h.raw().length);
  // Wrong endpoint is never adopted, even after clearing the current screen.
  const endpoint = h.frame.display.endpoint_id;
  h.frame.display.endpoint_id = '00000000-0000-0000-0000-000000000099';
  await h.tick(250); assert.equal(h.raw().length, 0);
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
  // Source failures, stale responses and rejected profile reloads erase values.
  const j = harness(); await j.start();
  j.frame.display.failure = 'invalid_profile'; await j.tick(250); assert.equal(j.raw().length, 0);
  j.frame.display.failure = null; j.newer(); await j.tick(250); assert.ok(j.raw().length);
  j.request = () => j.response(input.scenarios.stale);
  await j.tick(250); assert.equal(j.raw().length, 0);
  const k = harness();
  for (const f of Object.values(k.frame.display.frames)) f.age_seconds = 5;
  await k.start(); assert.equal(k.raw().length, 0); // Deadline exactly zero.
  console.log('controller freshness, identity, demand, stop and recovery passed');
})().catch(error => { console.error(error); process.exitCode = 1; });
