const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const script = fs.readFileSync(process.argv[2], 'utf8');
const key = 'sdsctl.web.theme-typography.v1';
function fixture({theme = 'matrix', stored = null, blocked = false, loading = false, controls = true} = {}) {
  const events = {}, documentEvents = {}, writes = [], observers = [];
  const root = {dataset: {theme}};
  const nodes = {};
  function node() { return {hidden: true, value: '', children: [], events: {},
    addEventListener(name, fn) { this.events[name] = fn; },
    replaceChildren(...children) { this.children = children; }}; }
  if (controls) for (const id of ['theme-typography-pickers', 'theme-font-picker', 'theme-font-select', 'theme-typography-select']) nodes[id] = node();
  const context = {
    document: {documentElement: root, readyState: loading ? 'loading' : 'complete',
      getElementById: id => nodes[id] || null, createElement: node,
      addEventListener: (name, fn) => { documentEvents[name] = fn; }},
    window: {localStorage: {getItem: () => { if (blocked) throw Error('blocked'); return stored; },
      setItem: (k, value) => { if (blocked) throw Error('blocked'); writes.push([k, value]); }},
      addEventListener: (name, fn) => { events[name] = fn; }},
    MutationObserver: class { constructor(fn) { this.fn = fn; } observe(_, options) {
      assert.deepEqual(Array.from(options.attributeFilter), ['data-theme']); observers.push(this.fn);
    }},
  };
  vm.runInNewContext(script, context);
  return {root, nodes, writes, documentEvents,
    theme(value) { root.dataset.theme = value; observers.forEach(fn => fn()); },
    change(font, mode) { nodes['theme-font-select'].value = font; nodes['theme-typography-select'].value = mode; nodes['theme-font-select'].events.change(); },
    storage(value, storageKey = key) { events.storage({key: storageKey, newValue: value}); }};
}
const f = fixture();
assert.equal(f.root.dataset.themeFont, 'plex');
assert.equal(f.nodes['theme-font-select'].children.length, 3);
assert.equal(f.nodes['theme-typography-pickers'].hidden, false);
assert.equal(f.writes.length, 0);
f.change('jetbrains', 'mixed');
assert.equal(f.root.dataset.themeTypography, 'mixed');
assert.equal(f.writes[0][0], key);
const reloaded = fixture({stored: f.writes[0][1], loading: true});
assert.equal(reloaded.root.dataset.themeFont, 'jetbrains');
assert.equal(reloaded.root.dataset.themeTypography, 'mixed');
reloaded.documentEvents.DOMContentLoaded();
assert.equal(reloaded.nodes['theme-font-select'].value, 'jetbrains');
for (const [theme, font, count] of [['first-responder','barlow',1], ['amateur-radio','orbitron',3], ['pip-boy-inspired','share-tech',1]]) {
  f.theme(theme);
  assert.equal(f.root.dataset.themeFont, font);
  assert.equal(f.nodes['theme-font-select'].children.length, count);
  assert.equal(f.nodes['theme-font-picker'].hidden, count === 1);
}
f.theme('amateur-radio'); f.change('atkinson', 'system');
f.theme('matrix');
assert.equal(f.root.dataset.themeFont, 'jetbrains');
for (const theme of ['system','lcars','custom-theme','__proto__','constructor']) {
  f.theme(theme);
  assert.equal(f.root.dataset.themeFont, undefined);
  assert.equal(f.root.dataset.themeTypography, undefined);
  assert.equal(f.nodes['theme-typography-pickers'].hidden, true);
}
f.theme('matrix');
f.storage(JSON.stringify({matrix: {font: 'source-code', mode: 'theme'}}));
assert.equal(f.root.dataset.themeFont, 'source-code');
f.storage(null, null);
assert.equal(f.root.dataset.themeFont, 'plex');
for (const stored of ['[]','false','"value"','invalid','null','{"matrix":[]}', '{"matrix":{"font":"rapid-response","mode":"invalid"}}']) {
  const bad = fixture({stored});
  assert.equal(bad.root.dataset.themeFont, 'plex');
  assert.equal(bad.root.dataset.themeTypography, 'theme');
  assert.equal(bad.writes.length, 0);
}
const blocked = fixture({blocked: true});
blocked.change('source-code', 'system');
assert.equal(blocked.root.dataset.themeFont, 'source-code');
assert.equal(blocked.root.dataset.themeTypography, 'system');
fixture({controls: false}).theme('lcars');
console.log('Typography preference, migration isolation, and fallback checks passed');
