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
    Element, document: {
      querySelector: selector => selector === '.site-header' ? header : null,
      querySelectorAll: () => [],
    },
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

test('horizontal LCARS tabs remain independent inside one rounded frame elbow', () => {
  class Element {
    constructor(box, style = {}) {
      const {left, right, top = 0} = box;
      const width = box.width ?? right - left;
      const bottom = box.bottom ?? top + (box.height ?? 60);
      const height = box.height ?? bottom - top;
      Object.assign(this, {rect: {left, right, top, bottom, width, height}, style, id: '',
        parentElement: null, hidden: false});
    }
    getBoundingClientRect() { return this.rect; }
  }
  const brand = new Element({left: 17, right: 117});
  const header = new Element({left: 0, right: 800});
  header.querySelector = () => brand;
  const first = new Element({left: 25.8, right: 125.8, top: 12.8, bottom: 47.2},
    {borderTopLeftRadius: '3px', borderTopRightRadius: '3px',
      borderBottomRightRadius: '20px', borderBottomLeftRadius: '20px'});
  const last = new Element({left: 687.2, right: 787.2, top: 12.8, bottom: 47.2},
    {borderTopLeftRadius: '3px', borderTopRightRadius: '3px',
      borderBottomRightRadius: '20px', borderBottomLeftRadius: '20px'});
  const tabs = new Element(
    {left: 13, right: 800, top: 0, bottom: 60, width: 787, height: 60},
    {display: 'grid', paddingLeft: '12.8px', paddingRight: '12.8px',
      paddingTop: '12.8px', paddingBottom: '0px'},
  );
  tabs.querySelectorAll = () => [first, last];
  const shell = new Element({left: 0, right: 800, top: 0, bottom: 300});
  shell.style.before = {content: '\"\"', backgroundImage: 'radial-gradient(circle, black, red)',
    width: '32px', height: '30px'};
  const audit = vm.runInNewContext(`(${browserAuditLibrary.toString()})()`, {
    Element,
    document: {
      documentElement: {dataset: {lcarsV2Bands: 'segmented'}},
      querySelector: selector => selector === '.site-header' ? header :
        selector === '.workspace-tabs' ? tabs : selector === '.workspace-shell' ? shell : null,
      querySelectorAll: () => [],
    },
    getComputedStyle: (element, pseudo) => ({borderLeftWidth: '13px', display: 'block',
      visibility: 'visible', ...(pseudo === '::before' ? element.style.before : element.style)}),
  });
  assert.equal(audit.decorativeClearanceFailures('lcars').length, 0);
  first.rect.left = 20;
  assert.match(audit.decorativeClearanceFailures('lcars')[0], /Scanner tab/);
  first.rect.left = 25.8;
  last.rect.right = 795;
  assert.match(audit.decorativeClearanceFailures('lcars')[0], /matching end inset/);
  last.rect.right = 787.2;
  tabs.style.paddingLeft = '4px';
  assert.match(audit.decorativeClearanceFailures('lcars')[0], /Scanner tab/);
  tabs.style.paddingLeft = '12.8px';
  tabs.style.paddingTop = '4px';
  assert.ok(audit.decorativeClearanceFailures('lcars').some(failure => /upper frame/.test(failure)));
  tabs.style.paddingTop = '12.8px';
  first.style.borderTopLeftRadius = '20px';
  assert.ok(audit.decorativeClearanceFailures('lcars').some(failure => /shape differs/.test(failure)));
  first.style.borderTopLeftRadius = '3px';
  shell.style.before.backgroundImage = 'none';
  assert.ok(audit.decorativeClearanceFailures('lcars').some(failure => /continuous rounded elbow/.test(failure)));
});

test('phone LCARS tabs preserve their established block geometry', () => {
  class Element {
    constructor(rect, style = {}) {
      Object.assign(this, {rect, style, id: '', parentElement: null, hidden: false});
    }
    getBoundingClientRect() { return this.rect; }
  }
  const brand = new Element({left: 17, right: 117, width: 100, height: 60});
  const header = new Element({left: 0, right: 390, width: 390, height: 60});
  header.querySelector = () => brand;
  const first = new Element({left: 25.8, right: 125, top: 0, bottom: 44, width: 99.2, height: 44},
    {borderTopLeftRadius: '3px', borderTopRightRadius: '3px',
      borderBottomRightRadius: '20px', borderBottomLeftRadius: '20px'});
  const second = new Element({left: 130, right: 230, top: 0, bottom: 44, width: 100, height: 44},
    {...first.style});
  const tabs = new Element({left: 13, right: 390, top: 0, bottom: 100, width: 377, height: 100},
    {display: 'grid', paddingLeft: '12.8px', paddingRight: '12.8px',
      paddingTop: '0px', paddingBottom: '0px'});
  tabs.querySelectorAll = () => [first, second];
  const shell = new Element({left: 0, right: 390, top: 0, bottom: 500, width: 390, height: 500});
  shell.style.before = {content: '\"\"', backgroundImage: 'radial-gradient(circle, black, red)',
    width: '32px', height: '30px'};
  const audit = vm.runInNewContext(`(${browserAuditLibrary.toString()})()`, {
    Element,
    document: {
      documentElement: {dataset: {lcarsV2Bands: 'segmented'}},
      querySelector: selector => selector === '.site-header' ? header :
        selector === '.workspace-tabs' ? tabs : selector === '.workspace-shell' ? shell : null,
      querySelectorAll: () => [],
    },
    matchMedia: query => ({matches: query === '(forced-colors: none)'}),
    getComputedStyle: (element, pseudo) => ({borderLeftWidth: '13px', display: 'block',
      visibility: 'visible', ...(pseudo === '::before' ? element.style.before : element.style)}),
  });
  assert.equal(audit.decorativeClearanceFailures('lcars').length, 0);
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
