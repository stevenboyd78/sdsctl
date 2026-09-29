import assert from 'node:assert/strict';
import test from 'node:test';
import vm from 'node:vm';
import {browserAuditLibrary} from './audit_web_dashboard_browser.mjs';

// Deterministic scroll/clipping counterexamples supplement the real Chrome
// matrix and trusted Tab/Shift+Tab tests; no DOM emulation dependency needed.
function fixture({theme = 'lcars', compact = false, overflow = 'auto',
  blockedScroll = false, blockedFocus = false, wide = false, tall = false,
  nested = false, outsidePane = false} = {}) {
  const document = {activeElement: null};
  class Element {
    constructor(id, parentElement, box, style = {}) {
      Object.assign(this, {id, parentElement, box, style, dataset: {}, hidden: false,
        classList: [], localName: 'strong', tabIndex: 0, children: [],
        textContent: 'Current scanner data', scrollTop: 0, scrollHeight: box.height,
        clientHeight: box.height, scrollWidth: box.width, clientWidth: box.width});
      this.childNodes = [{nodeType: 3, textContent: this.textContent}];
    }
    matches(selector) { return selector === '.workspace-pane' && this.id === 'pane'; }
    closest(selector) {
      for (let current = this; current; current = current.parentElement) {
        if (current.matches(selector)) return current;
      }
      return null;
    }
    getAttribute() { return null; }
    getBoundingClientRect() {
      let top = this.box.top;
      for (let current = this.parentElement; current; current = current.parentElement) {
        top -= current.scrollTop;
      }
      return {...this.box, top, bottom: top + this.box.height,
        right: this.box.left + this.box.width};
    }
    scrollTo({top}) {
      if (!blockedScroll) this.scrollTop = Math.max(0, Math.min(top, this.scrollHeight - this.clientHeight));
    }
    focus() { if (!blockedFocus) document.activeElement = this; }
  }
  const root = new Element('root', null, {left: 0, top: 0, width: 800, height: 480});
  root.dataset = {theme, lcarsV2Layout: 'v2', kioskCompact: String(compact)};
  document.documentElement = root;
  const pane = new Element(outsidePane ? 'other' : 'pane', root,
    {left: 10, top: 10, width: 780, height: 300}, {overflowY: 'hidden'});
  const outer = new Element('outer', pane,
    {left: 20, top: 20, width: 750, height: 200}, {overflowY: overflow});
  outer.scrollHeight = nested ? 1100 : 900;
  outer.scrollTop = 17;
  const inner = nested ? new Element('inner', outer,
    {left: 25, top: 600, width: 740, height: 150}, {overflowY: 'auto'}) : outer;
  if (nested) { inner.scrollHeight = 400; inner.scrollTop = 9; }
  const target = new Element('target', inner,
    {left: 30, top: nested ? 900 : 600, width: wide ? 1000 : 100, height: tall ? 400 : 20});
  const defaults = {display: 'block', visibility: 'visible', overflowX: 'hidden',
    overflowY: 'visible', color: 'rgb(255 255 255)', backgroundColor: 'rgb(0 0 0)',
    opacity: '1', filter: 'none', fontSize: '16px', fontWeight: '400'};
  document.querySelectorAll = () => [target];
  document.createRange = () => ({selectNodeContents() {}, detach() {},
    getBoundingClientRect: () => target.getBoundingClientRect()});
  const audit = vm.runInNewContext(`(${browserAuditLibrary.toString()})()`, {
    Element, HTMLElement: Element, HTMLButtonElement: Element, document,
    Node: {TEXT_NODE: 3}, innerWidth: 800, innerHeight: 480,
    getComputedStyle: element => ({...defaults, ...element.style}),
  });
  return {audit, document, target, outer, inner};
}

test('LCARS fields, semantic text and focus targets are reachable in real internal scrollers', () => {
  for (const nested of [false, true]) {
    const {audit, target, outer, inner} = fixture({nested});
    const before = [outer.scrollTop, inner.scrollTop];
    assert.equal(audit.radioValueFailures(target).length, 0);
    assert.equal(audit.semanticClipping().length, 0);
    assert.equal(audit.focusFailures().failures.length, 0);
    assert.deepEqual([outer.scrollTop, inner.scrollTop], before, 'scroll position restored');
  }
});

for (const [name, options] of Object.entries({
  'hidden ancestor': {overflow: 'hidden'},
  'clipped ancestor': {overflow: 'clip'},
  'broken scrolling': {blockedScroll: true},
  'oversized horizontal text': {wide: true},
  'oversized vertical text': {tall: true},
  'document scroller outside workspace': {outsidePane: true},
  'other theme unchanged': {theme: 'matrix'},
  'compact kiosk unchanged': {compact: true},
})) {
  test(`reachability check still rejects ${name}`, () => {
    const {audit, target, outer} = fixture(options);
    assert.ok(audit.radioValueFailures(target).some(failure => /clipped|outside/.test(failure)));
    assert.ok(audit.semanticClipping().some(failure => /clipped|outside/.test(failure)));
    assert.ok(audit.focusFailures().failures.some(failure => /clipped|viewport/.test(failure)));
    assert.equal(outer.scrollTop, 17);
  });
}

test('scroll reachability never substitutes for being able to receive focus', () => {
  const {audit, outer} = fixture({blockedFocus: true});
  assert.match(audit.focusFailures().failures[0], /cannot receive focus/);
  assert.equal(outer.scrollTop, 17);
});

test('clipping/readability failures remain independent', () => {
  const {audit, target} = fixture();
  target.style.color = 'rgb(10 10 10)';
  assert.ok(audit.radioValueFailures(target).some(failure => /contrast/.test(failure)));
  target.scrollWidth = 200;
  assert.ok(audit.semanticClipping().some(failure => /horizontal semantic/.test(failure)));
});

test('decorative clearance measures the actual responsive rail, still rejecting overlap', () => {
  class Element {
    constructor(left = 0) { this.left = left; this.id = ''; this.parentElement = null; }
    getBoundingClientRect() { return {left: this.left, width: 800, height: 60}; }
  }
  const brand = new Element(17);
  const header = new Element();
  header.querySelector = () => brand;
  let rail = 13;
  const audit = vm.runInNewContext(`(${browserAuditLibrary.toString()})()`, {
    Element, document: {querySelector: () => header, querySelectorAll: () => []},
    getComputedStyle: () => ({borderLeftWidth: `${rail}px`, display: 'block', visibility: 'visible'}),
  });
  assert.equal(audit.decorativeClearanceFailures('lcars').length, 0);
  brand.left = 16;
  assert.match(audit.decorativeClearanceFailures('lcars')[0], /intrudes/);
  rail = 160;
  brand.left = 164;
  assert.equal(audit.decorativeClearanceFailures('lcars').length, 0);
  brand.left = 150;
  assert.match(audit.decorativeClearanceFailures('lcars')[0], /intrudes/);
});

test('LCARS utility and Scanner-filter roles stay internally consistent', () => {
  class Element {
    constructor(id, size) { Object.assign(this, {id, size, parentElement: null}); }
    getBoundingClientRect() { return {width: 100, height: this.size}; }
  }
  const scanner = new Element('radio-view-auto', 40);
  const utility = new Element('scanner-reconnect', 22);
  const button = new Element('pane-action', 22);
  const pane = new Element('pane');
  pane.querySelectorAll = () => [button];
  const root = {dataset: {theme: 'lcars', lcarsV2Layout: 'v2'}};
  const audit = vm.runInNewContext(`(${browserAuditLibrary.toString()})()`, {
    Element, HTMLElement: Element, HTMLButtonElement: Element,
    document: {documentElement: root, querySelector: selector =>
      selector === '#radio-view-auto' ? scanner : selector === '#scanner-reconnect' ? utility : pane},
    getComputedStyle: element => ({minHeight: `${element.size}px`, display: 'block', visibility: 'visible',
      fontSize: '12px', lineHeight: '14px', fontWeight: '400', borderRadius: '4px',
      paddingTop: '2px', paddingRight: '2px', paddingBottom: '2px', paddingLeft: '2px',
      borderTopWidth: '1px', borderRightWidth: '1px', borderBottomWidth: '1px', borderLeftWidth: '1px'}),
  });
  assert.equal(audit.subpanelButtonGeometry('controls').length, 0);
  button.size = 30;
  assert.match(audit.subpanelButtonGeometry('controls')[0], /does not match/);
  button.size = 40;
  assert.equal(audit.subpanelButtonGeometry('scanner').length, 0);
  button.size = 22;
  assert.match(audit.subpanelButtonGeometry('scanner')[0], /does not match/);
  root.dataset.theme = 'system';
  assert.match(audit.subpanelButtonGeometry('controls')[0], /does not match/);
});
