// Production JavaScript with synthetic DOM, monotonic timers and HTTP responses.
// No browser, network, storage, credentials or physical scanner is accessed.
const assert = require('node:assert/strict');
const vm = require('node:vm');
const input = JSON.parse(require('node:fs').readFileSync(0, 'utf8'));
const flush = async () => { for (let i = 0; i < 12; i++) await new Promise(setImmediate); };
const copy = value => structuredClone(value);
const origin = 'https://scanner.example.test';
const rootUrl = origin + '/api/hassio_ingress/test-prefix/';
const uuid = n => '00000000-0000-0000-0000-' + String(n).padStart(12, '0');
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
  addEventListener(key, fn) { assert.equal(this.listeners[key], undefined); this.listeners[key] = fn; }
}
const nodes = root => [root, ...root.children.flatMap(nodes)];
function harness(options = {}) {
  let now = 0, timerId = 0, handler = null;
  const timers = new Map(), host = new Element('main'), requests = [];
  const document = {hidden:false, createElement: tag => new Element(tag)};
  const payload = copy(input.bundle);
  const window = {location:{origin:options.pageOrigin ?? origin},
    setTimeout(fn, delay) { timers.set(++timerId, {fn, at:now + Math.max(0, delay)}); return timerId; },
    clearTimeout(id) { timers.delete(id); },
  };
  const sandbox = {window, document, performance:{now:() => now}, AbortController, TextDecoder, URL};
  vm.runInNewContext(input.auxiliary, sandbox);
  vm.runInNewContext(input.script, sandbox);
  const response = (body = payload, status = 200) => new Response(JSON.stringify(body),
    {status, headers:{'content-type':'application/json'}});
  const negotiation = () => input.negotiation ? copy(input.negotiation)
    : {protocol:'sdsctl.supplemental-context', version:1, context:copy(payload.supplemental.context)};
  const controller = window.sdsctlMimic.create({host, standard:new Element('section'),
    url:'/FORBIDDEN-ordinary-fallback', supplementalRoot:rootUrl, ...options,
    request:async (url, opts) => {
      requests.push([url, opts]);
      return handler ? handler(url, opts) : response(url.endsWith('/context') ? negotiation() : payload);
    }});
  const find = id => nodes(host).find(n => n.id === id);
  const choose = (id, value) => { const n = find(id); n.value = value; n.listeners.change(); };
  const context = (value = {}) => controller.context({available:true,active:true,stopped:false,...value});
  const text = token => {
    const mode = find('mimic-mode').value || 'preferred';
    const region = payload.display.frames[mode].screen.regions.find(r => r.token === token);
    return region && nodes(host).find(n => n.dataset.region === region.id)?.children[0]?.textContent;
  };
  return {controller, payload, host, document, requests, find, choose, context, response, negotiation, text,
    get negotiations() { return requests.filter(([url]) => url.endsWith('/context')).length; },
    get frames() { return requests.filter(([url]) => url.endsWith('/frame')).length; },
    get pendingTimers() { return timers.size; },
    raw:() => nodes(host).filter(n => n.dataset.valueStatus === 'raw_source'),
    favorite:() => find('mimic-favorites-states').textContent,
    set request(fn) { handler = fn; },
    newer(source = false) {
      payload.supplemental.psi.sequence++;
      for (const frame of Object.values(payload.display.frames)) frame.sequence++;
      if (source) { payload.supplemental.clock.sample_sequence++; payload.supplemental.favorites.sample_sequence++; }
    },
    async changed(mutator) {
      handler = () => response({}, 409); await this.tick(250);
      assert.equal(this.raw().length, 0);
      mutator(payload.supplemental.context);
      const binding = payload.supplemental.context;
      for (const key of ['endpoint_id','stream_id','session_id']) payload.display[key] = binding[key];
      for (const frame of Object.values(payload.display.frames)) frame.profile_revision = binding.profile_revision;
      handler = null; await this.tick(2000);
    },
    async start() { context(); assert.equal(requests.length, 0); choose('mimic-presentation','mimic'); await flush(); },
    async tick(ms) {
      const end = now + ms; let count = 0;
      for (;;) {
        const task = [...timers.entries()].filter(([,t]) => t.at <= end).sort((a,b) => a[1].at-b[1].at)[0];
        if (!task) break;
        assert.ok(++count < 1000, 'timer spin');
        const [id, timer] = task; now = timer.at; timers.delete(id); timer.fn(); await flush();
      }
      now = end; await flush();
    },
  };
}
const clock = h => assert.equal(h.text('Time'), '21:26');
const empty = h => { assert.equal(h.raw().length, 0); assert.equal(h.favorite(), ''); };
const noClock = h => assert.notEqual(h.text('Time'), '21:26');
const streamed = text => {
  let finish;
  const response = new Response(new ReadableStream({start(c) {
    finish = () => { c.enqueue(new TextEncoder().encode(text)); c.close(); };
  }}), {headers:{'content-type':'application/json'}});
  return {response, finish};
};
async function scenario(name) {
  if (name.startsWith('root:')) {
    assert.throws(() => harness({supplementalRoot:input.value})); return;
  }
  if (name === 'mutually-exclusive') {
    assert.throws(() => harness({supplementalContext:input.bundle.supplemental.context})); return;
  }
  const h = harness(name === 'ip-origin' ? {pageOrigin:'http://192.0.2.10:8123',
    supplementalRoot:'http://192.0.2.10:8123/ingress/'} : {});
  if (name === 'happy' || name === 'ip-origin') {
    await h.start(); clock(h); assert.ok(h.favorite());
    assert.equal(h.negotiations, 1); assert.equal(h.frames, 1);
    for (const [url, options] of h.requests) {
      const expectedRoot = name === 'ip-origin' ? 'http://192.0.2.10:8123/ingress/' : rootUrl;
      assert.ok(url.startsWith(expectedRoot + 'api/v1/display-supplemental/'));
      assert.equal(new URL(url).search, ''); assert.equal(options.credentials, 'same-origin');
      assert.equal(options.cache, 'no-store'); assert.equal(options.redirect, 'error');
      assert.equal(options.headers['X-SDSCTL-Supplemental-Version'], '1');
      if (url.endsWith('/frame')) assert.deepEqual(JSON.parse(options.headers['X-SDSCTL-Supplemental-Context']), h.payload.supplemental.context);
      else assert.equal(options.headers['X-SDSCTL-Supplemental-Context'], undefined);
    }
    for (let i = 0; i < 50; i++) h.context();
    assert.equal(h.negotiations, 1); assert.equal(h.frames, 1);
  } else if (name.startsWith('bad-context:')) {
    const value = h.negotiation();
    const mutation = input.mutation;
    if (mutation.field) value.context[mutation.field] = mutation.value;
    else Object.assign(value, mutation);
    h.request = () => h.response(value); await h.start(); empty(h);
    assert.equal(h.frames, 0); assert.equal(h.negotiations, 1);
    await h.tick(2000); assert.equal(h.negotiations, 2); assert.equal(h.frames, 0);
  } else if (name === 'same-context-retirement') {
    await h.start(); clock(h);
    await h.changed(() => h.newer());
    assert.equal(h.negotiations, 2); noClock(h); assert.equal(h.favorite(), '');
    h.newer(true); await h.tick(250); clock(h);
  } else if (name.startsWith('replace:')) {
    await h.start(); h.choose('mimic-mode','detail'); h.choose('mimic-led','border');
    await h.changed(c => {
      if (name === 'replace:session') c.session_id = uuid(9);
      if (name === 'replace:stream') c.stream_id = uuid(9);
      if (name === 'replace:profile') { c.profile_revision = 'f'.repeat(64); c.context_revision++; }
      if (name === 'replace:repair') { c.profile_invalidation++; c.context_revision++; }
      h.payload.supplemental.psi.sequence = 0;
      for (const frame of Object.values(h.payload.display.frames)) frame.sequence = 0;
      h.payload.supplemental.clock.sample_sequence = h.payload.supplemental.favorites.sample_sequence = 1;
    });
    clock(h); assert.equal(h.negotiations, 2);
    assert.equal(h.find('mimic-mode').value, 'detail');
    assert.equal(h.find('mimic-display').dataset.ledTreatment, 'border');
    assert.equal(nodes(h.host).filter(n => n.id === 'mimic-display').length, 1);
    assert.equal(nodes(h.host).filter(n => n.id === 'mimic-presentation').length, 1);
  } else if (name === 'frame-cannot-rebind') {
    await h.start(); h.payload.supplemental.context.session_id = h.payload.display.session_id = uuid(9);
    await h.tick(250); empty(h); assert.equal(h.negotiations, 1);
    await h.tick(2000); empty(h); assert.equal(h.negotiations, 1);
  } else if (name.startsWith('rollback:')) {
    await h.start(); const old = copy(h.payload.supplemental.context);
    await h.changed(c => { c.context_revision++; c.profile_invalidation++; }); clock(h);
    await h.changed(c => {
      if (name === 'rollback:retired') Object.assign(c, old);
      if (name === 'rollback:revision') { c.context_revision--; c.profile_revision = 'f'.repeat(64); }
      if (name === 'rollback:invalidation') { c.profile_invalidation--; c.context_revision++; }
      if (name === 'rollback:endpoint') c.endpoint_id = uuid(9);
    });
    empty(h); assert.equal(h.frames, 4); // initial, two 409s, one accepted replacement
  } else if (name === 'history-cap') {
    await h.start();
    for (let i = 0; i < 64; i++) { await h.changed(c => c.session_id = uuid(100 + i)); clock(h); }
    await h.changed(c => c.session_id = uuid(999)); empty(h); assert.equal(h.pendingTimers, 0);
    const count = h.requests.length; h.context(); await h.tick(10000); assert.equal(h.requests.length, count);
  } else if (name === 'profile-history-cap') {
    await h.start();
    for (let i = 0; i < 64; i++) {
      await h.changed(c => c.profile_revision = String(i).padStart(64,'0')); clock(h);
    }
    await h.changed(c => c.profile_revision = 'f'.repeat(64)); empty(h); assert.equal(h.pendingTimers, 0);
  } else if (name === 'epoch-compaction') {
    await h.start(); const old = copy(h.payload.supplemental.context);
    for (let i = 0; i < 150; i++) { await h.changed(c => c.context_revision++); clock(h); }
    await h.changed(c => Object.assign(c, old)); empty(h);
  } else if (name === 'same-epoch-profile-replay') {
    await h.start(); const old = copy(h.payload.supplemental.context);
    await h.changed(c => c.profile_revision = 'f'.repeat(64)); clock(h);
    await h.changed(c => Object.assign(c, old)); empty(h);
  } else if (name === 'old-connection-new-revision') {
    await h.start(); const old = copy(h.payload.supplemental.context);
    await h.changed(c => c.session_id = uuid(9)); clock(h);
    await h.changed(c => { Object.assign(c, old); c.context_revision += 100; }); empty(h);
  } else if (name.startsWith('unavailable:')) {
    const first = name === 'unavailable:negotiation';
    if (!first) await h.start();
    h.request = () => h.response({}, 503);
    if (first) await h.start(); else await h.tick(250);
    empty(h); h.newer(true); h.request = null; await h.tick(2000); clock(h);
    assert.equal(h.negotiations, first ? 2 : 1);
    assert.ok(h.requests.every(([url]) => !url.includes('FORBIDDEN')));
  } else if (name.startsWith('admission:')) {
    const [,stage,code] = name.split(':');
    if (stage === 'frame') await h.start();
    h.request = () => h.response({PRIVATE:'never displayed'}, Number(code));
    if (stage === 'context') await h.start(); else await h.tick(250);
    empty(h); assert.equal(h.pendingTimers, 0);
    const count = h.requests.length; h.context(); await h.tick(10000); assert.equal(h.requests.length, count);
  } else if (name === 'outer-auth-stop') {
    let stopped = 0;
    h.request = () => { stopped++; h.controller.stop(); return h.response({}, 401); };
    await h.start(); empty(h); h.context(); await h.tick(10000);
    assert.equal(stopped, 1); assert.equal(h.pendingTimers, 0);
  } else if (name.startsWith('late:')) {
    const [,stage,action] = name.split(':'); let resolve, finish;
    if (stage === 'frame') await h.start();
    if (stage === 'body') {
      const value = streamed(JSON.stringify(h.negotiation())); finish = value.finish;
      h.request = () => value.response;
    } else h.request = () => new Promise(r => { resolve = r; });
    if (stage === 'frame') await h.tick(250); else await h.start();
    const old = stage === 'frame' ? copy(h.payload) : h.negotiation();
    if (action === 'stop') h.controller.stop();
    else { h.document.hidden = true; h.context(); }
    empty(h);
    if (action === 'hide') {
      h.document.hidden = false; h.payload.supplemental.context.session_id = h.payload.display.session_id = uuid(9);
      h.request = null; h.context(); await flush();
      // A previous verified frame context sees a mismatch until an explicit 409.
      if (stage === 'frame') empty(h); else clock(h);
    }
    if (finish) finish(); else resolve(h.response(old));
    await flush();
    if (action === 'stop') { empty(h); assert.equal(h.pendingTimers, 0); }
    else if (stage !== 'frame') { clock(h); assert.equal(h.frames, 1); }
  } else if (name.startsWith('timeout:')) {
    let resolve, finish;
    if (name === 'timeout:body') {
      const value = streamed(JSON.stringify(h.negotiation())); finish = value.finish;
      h.request = () => value.response;
    } else h.request = () => new Promise(r => { resolve = r; });
    await h.start(); await h.tick(5001);
    assert.equal(h.requests[0][1].signal.aborted, true);
    if (finish) finish(); else resolve(h.response(h.negotiation()));
    await flush(); empty(h); assert.equal(h.frames, 0);
    h.request = null; await h.tick(2000); clock(h);
  } else if (name === 'total-request-budget') {
    let resolve;
    h.request = () => new Promise(r => { resolve = r; });
    await h.start(); await h.tick(4500); const contextDone = resolve;
    contextDone(h.response(h.negotiation())); await flush();
    await h.tick(501); resolve(h.response()); await flush(); empty(h);
    assert.equal(h.frames, 1); assert.equal(h.requests[1][1].signal.aborted, true);
  } else if (name.startsWith('malformed:')) {
    const body = name === 'malformed:oversized' ? ' '.repeat(2049) : name === 'malformed:utf8'
      ? new Uint8Array([255]) : '{';
    h.request = () => new Response(body, {headers:{'content-type':'application/json'}});
    await h.start(); empty(h); assert.equal(h.frames, 0);
    assert.ok(h.find('mimic-last-failure').dataset.phase.startsWith('context_'));
  } else if (name === 'same-context-expiry') {
    await h.start(); h.request = url => {
      h.newer(); return h.response(url.endsWith('/context') ? h.negotiation() : h.payload);
    };
    await h.tick(5000); noClock(h); assert.equal(h.favorite(), '');
    await h.changed(() => h.newer()); noClock(h); assert.equal(h.favorite(), '');
    h.newer(true); await h.tick(250); clock(h);
  } else if (name === 'late-conflict') {
    await h.start(); let resolve;
    h.request = () => new Promise(r => { resolve = r; }); await h.tick(250);
    h.context({active:false}); h.newer(true); h.request = null; h.context(); await flush(); clock(h);
    const count = h.negotiations; resolve(h.response({}, 409)); await flush();
    await h.tick(250); clock(h); assert.equal(h.negotiations, count);
  } else if (name === 'old-finally-retains-new-deadline') {
    await h.start(); let resolve;
    h.request = () => new Promise(r => { resolve = r; }); await h.tick(250);
    const oldDone = resolve;
    h.context({active:false}); h.newer(true); h.context(); await flush();
    const newDone = resolve;
    oldDone(h.response()); await flush();
    await h.tick(5000); assert.equal(h.requests.at(-1)[1].signal.aborted, true);
    newDone(h.response()); await flush(); empty(h);
  } else if (name === 'visibility-retains-guard') {
    await h.start();
    for (const option of ['hidden','inactive','unavailable','standard']) {
      if (option === 'hidden') { h.document.hidden = true; h.context(); }
      if (option === 'inactive') h.context({active:false});
      if (option === 'unavailable') h.context({available:false});
      if (option === 'standard') h.choose('mimic-presentation','standard');
      empty(h); const count = h.requests.length; await h.tick(500); assert.equal(h.requests.length, count);
      h.newer(); h.document.hidden = false; h.context();
      if (option === 'standard') h.choose('mimic-presentation','mimic');
      await flush(); noClock(h); assert.equal(h.favorite(), ''); assert.equal(h.negotiations, 1);
      h.newer(true); await h.tick(250); clock(h);
    }
  } else if (name === 'independent-expiry') {
    h.payload.supplemental.clock.age_seconds = 2;
    h.payload.supplemental.favorites.age_seconds = 1;
    await h.start(); clock(h); h.request = () => new Promise(() => {});
    await h.tick(3000); noClock(h); assert.ok(h.favorite()); assert.ok(h.raw().length);
    await h.tick(1000); assert.equal(h.favorite(), ''); assert.ok(h.raw().length);
    await h.tick(1000); empty(h);
  } else if (name === 'independent-instances') {
    const other = harness(); await h.start(); await other.start();
    h.controller.stop(); empty(h); clock(other); other.controller.stop();
  } else throw new Error('Unknown scenario: ' + name);
  assert.ok(!nodes(h.host).some(n => (n.textContent ?? '').includes('PRIVATE')));
  h.controller.stop(); empty(h); assert.equal(h.pendingTimers, 0);
}
scenario(input.scenario).then(() => console.log('negotiation scenario passed: ' + input.scenario))
  .catch(error => { console.error(error); process.exitCode = 1; });
