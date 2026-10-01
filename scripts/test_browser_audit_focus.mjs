import assert from 'node:assert/strict';
import test from 'node:test';
import vm from 'node:vm';
import {browserAuditLibrary} from './audit_web_dashboard_browser.mjs';

function fixture({missing = false, blockedFocus = false, displaced = false} = {}) {
  const document = {documentElement: {dataset: {workspacePane: 'scanner'}}, activeElement: null};
  class Element {
    constructor(values = {}) { Object.assign(this, {parentElement: null, tabIndex: 0}, values); }
    getBoundingClientRect() { return {width: 100, height: 30}; }
    closest() { return this.inert ? this : null; }
    getAttribute(name) { return this.attributes?.[name] ?? null; }
    focus() { if (!blockedFocus) document.activeElement = this; }
  }
  class Select extends Element {
    dispatchEvent() {
      document.documentElement.dataset.systemPalette = this.value;
      if (displaced) document.activeElement = null;
    }
  }
  const hidden = new Element({parentElement: new Element({hidden: true})});
  const invisible = new Element({visibility: 'hidden'});
  const disabled = new Element({disabled: true});
  const inert = new Element({inert: true});
  const excluded = new Element({tabIndex: -1});
  const active = new Element();
  const selector = new Select();
  const candidates = [hidden, invisible, disabled, inert, excluded, ...missing ? [] : [active]];
  document.querySelectorAll = query => {
    assert.equal(query, '.workspace-pane[data-workspace-pane="scanner"] button:not(:disabled)');
    return candidates;
  };
  document.querySelector = query => { assert.equal(query, '#system-palette-select'); return selector; };
  const audit = vm.runInNewContext(`(${browserAuditLibrary.toString()})()`, {
    Element, HTMLElement: Element, HTMLSelectElement: Select, document,
    Event: class {}, window: {sdsctlTheme: {currentSystemPalette: () => selector.value}},
    getComputedStyle: element => ({display: 'block', visibility: element.visibility ?? 'visible'}),
  });
  return {audit, document, active};
}

test('palette focus probe skips hidden Mimic, invisible, disabled, inert and untabbable controls', () => {
  const {audit, document, active} = fixture();
  assert.equal(audit.switchSystemPalette('ansi-light').failures.length, 0);
  assert.equal(document.activeElement, active);
});

test('palette focus probe refuses missing visible targets rather than silently passing', () => {
  const {audit} = fixture({missing: true});
  assert.match(audit.switchSystemPalette('ansi-light').failures[0], /no visible enabled pane button/);
});

test('palette focus probe verifies its setup before blaming a palette change', () => {
  const {audit, document} = fixture({blockedFocus: true});
  assert.match(audit.switchSystemPalette('ansi-light').failures[0], /could not focus/);
  assert.equal(document.documentElement.dataset.systemPalette, undefined);
});

test('palette focus probe still rejects actual focus displacement', () => {
  const {audit} = fixture({displaced: true});
  assert.match(audit.switchSystemPalette('ansi-light').failures[0], /displaced focus/);
});
