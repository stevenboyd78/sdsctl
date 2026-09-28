// Dependency-free DOM harness for the shipped controller (no browser/network).
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const script = fs.readFileSync(process.argv[2], 'utf8');

class Element {
  constructor(name) {
    this.name = name; this.children = []; this.dataset = {}; this.listeners = {};
    this.attributes = {}; this.hidden = false;
  }
  remove() {
    if (this.parentElement) {
      const siblings = this.parentElement.children;
      siblings.splice(siblings.indexOf(this), 1); this.parentElement = null;
    }
  }
  append(child) { child.remove(); child.parentElement = this; this.children.push(child); }
  insertBefore(child, next) {
    child.remove(); child.parentElement = this;
    this.children.splice(this.children.indexOf(next), 0, child);
  }
  after(child) {
    child.remove(); const parent = this.parentElement; child.parentElement = parent;
    parent.children.splice(parent.children.indexOf(this) + 1, 0, child);
  }
  get nextElementSibling() {
    const siblings = this.parentElement.children;
    return siblings[siblings.indexOf(this) + 1] || null;
  }
  setAttribute(name, value) { this.attributes[name] = value; }
  addEventListener(name, fn) { this.listeners[name] = fn; }
}
function harness({stored = {}, blocked = false, loading = false, incomplete = false} = {}) {
  const root = new Element('html'); root.dataset.theme = 'system';
  const ids = {};
  for (const id of ['lcars-v2-appearance-pickers', 'lcars-v2-palette-select',
    'lcars-v2-type-select', 'radio-activity-panel', 'radio-field-groups', 'radio-system', 'radio-site']) {
    ids[id] = new Element(id);
  }
  const panel = ids['radio-activity-panel'], details = ids['radio-field-groups'];
  const tabs = new Element('tabs'), controls = new Element('controls');
  const hierarchy = new Element('hierarchy'), summary = new Element('summary');
  const header = new Element('header'), originalList = new Element('list');
  const systemRow = new Element('system-row'), siteRow = new Element('site-row');
  const nextRow = new Element('next-row');
  for (const child of [header, controls, hierarchy, summary, details]) panel.append(child);
  hierarchy.append(systemRow); systemRow.append(ids['radio-system']);
  details.append(originalList); originalList.append(siteRow); originalList.append(nextRow);
  siteRow.append(ids['radio-site']);
  panel.querySelector = name => name === '.radio-view-controls' ? controls : hierarchy;
  const originalChildren = [...panel.children];
  const writes = [], events = {}, observers = [], documentEvents = {};
  const wide = {matches: true, addEventListener: (_, fn) => { wide.changed = fn; }};
  const document = {
    readyState: loading ? 'loading' : 'complete', documentElement: root,
    getElementById: id => incomplete ? null : ids[id] || null,
    querySelector: () => incomplete ? null : tabs,
    createElement: name => new Element(name), createComment: name => new Element(name),
    addEventListener: (event, fn) => { documentEvents[event] = fn; },
  };
  const window = {
    localStorage: {
      getItem: key => { if (blocked) throw Error('blocked'); return stored[key] ?? null; },
      setItem: (key, value) => { if (blocked) throw Error('blocked'); writes.push([key, value]); },
    },
    addEventListener: (event, fn) => { events[event] = fn; }, matchMedia: () => wide,
  };
  class MutationObserver {
    constructor(fn) { observers.push(fn); }
    observe(target, options) {
      assert.equal(target, root);
      assert.equal(options.attributeFilter.join(','), 'data-theme,data-kiosk-compact');
    }
  }
  vm.runInNewContext(script, {document, window, MutationObserver});
  return {
    root, ids, tabs, panel, controls, details, hierarchy, originalList, siteRow,
    nextRow, originalChildren, writes, wide, events, documentEvents,
    theme(value) { root.dataset.theme = value; for (const fn of observers) fn(); },
    choose(id, value) { ids[id].value = value; ids[id].listeners.change(); },
  };
}
const paletteKey = 'sdsctl.web.lcars-v2-palette', typeKey = 'sdsctl.web.lcars-v2-typography';
const paletteId = 'lcars-v2-palette-select', typeId = 'lcars-v2-type-select';
const clean = harness();
assert.equal(clean.root.dataset.theme, 'system');
assert.equal(clean.root.dataset.lcarsV2Palette, 'classic');
assert.equal(clean.root.dataset.lcarsV2Type, 'antonio');
assert.equal(clean.ids['lcars-v2-appearance-pickers'].hidden, true);
assert.equal(clean.writes.length, 0);
const site = clean.ids['radio-site'];
site.textContent = 'Live Site';
for (const unused of [1, 2, 3]) {
  clean.theme('lcars-v2');
  assert.equal(clean.ids['lcars-v2-appearance-pickers'].hidden, false);
  assert.equal(clean.tabs.attributes['aria-orientation'], 'vertical');
  assert.equal(clean.siteRow.parentElement.className, 'lcars-v2-site-list');
  assert.equal(site.textContent, 'Live Site');
  assert.equal(clean.controls.nextElementSibling, clean.details);
  for (const theme of ['lcars', 'matrix', 'system']) {
    clean.theme(theme);
    assert.equal(clean.ids['lcars-v2-appearance-pickers'].hidden, true);
    assert.equal(clean.tabs.attributes['aria-orientation'], 'horizontal');
    assert.equal(clean.siteRow.parentElement, clean.originalList);
    assert.equal(clean.siteRow.nextElementSibling, clean.nextRow);
    assert.deepEqual(clean.panel.children, clean.originalChildren);
  }
}
clean.theme('lcars-v2'); clean.wide.matches = false; clean.wide.changed();
assert.equal(clean.tabs.attributes['aria-orientation'], 'horizontal');
for (const palette of ['classic', 'nemesis-blue', 'lower-decks', 'lower-decks-padd', 'voyager', 'picard']) {
  clean.choose(paletteId, palette); assert.equal(clean.root.dataset.lcarsV2Palette, palette);
}
for (const type of ['antonio', 'mixed', 'system']) {
  clean.choose(typeId, type); assert.equal(clean.root.dataset.lcarsV2Type, type);
}
assert.equal(clean.writes.length, 9);
assert.ok(clean.writes.every(([key]) => [paletteKey, typeKey].includes(key)));
clean.events.storage({key: paletteKey, newValue: 'voyager'});
assert.equal(clean.ids[paletteId].value, 'voyager');
assert.equal(clean.writes.length, 9);
clean.events.storage({key: 'unrelated', newValue: 'picard'});
assert.equal(clean.ids[paletteId].value, 'voyager');
clean.events.storage({key: null, newValue: null});
assert.equal(clean.ids[paletteId].value, 'classic');
assert.equal(clean.ids[typeId].value, 'antonio');
const invalid = harness({stored: {[paletteKey]: '<script>', [typeKey]: 'unknown'}});
assert.equal(invalid.root.dataset.lcarsV2Palette, 'classic');
assert.equal(invalid.root.dataset.lcarsV2Type, 'antonio');
assert.equal(invalid.writes.length, 2);
const blocked = harness({blocked: true});
blocked.choose(paletteId, 'picard'); blocked.choose(typeId, 'mixed');
assert.equal(blocked.root.dataset.lcarsV2Palette, 'picard');
assert.equal(blocked.root.dataset.lcarsV2Type, 'mixed');
const early = harness({loading: true, stored: {[paletteKey]: 'nemesis-blue', [typeKey]: 'system'}});
assert.equal(early.root.dataset.lcarsV2Palette, 'nemesis-blue');
assert.equal(early.root.dataset.lcarsV2Type, 'system');
early.documentEvents.DOMContentLoaded();
assert.equal(early.ids[paletteId].value, 'nemesis-blue');
assert.equal(early.ids[typeId].value, 'system');
harness({incomplete: true}).theme('lcars-v2');
// The display-only compact controller temporarily owns details and controls.
const compact = harness();
const disclosure = new Element('compact-details');
disclosure.append(compact.controls); disclosure.append(compact.details);
compact.theme('lcars-v2');
assert.equal(compact.controls.parentElement, disclosure);
compact.theme('lcars');
assert.equal(compact.controls.parentElement, disclosure);
compact.panel.append(compact.controls); compact.panel.append(compact.details);
compact.theme('lcars-v2');
assert.equal(compact.controls.nextElementSibling, compact.details);
console.log('LCARS v2 preference and reversible-layout checks passed');
