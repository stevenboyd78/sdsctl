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
  const active = new Element();
  const selector = new Select();
  document.querySelector = query => {
    if (query === '#pane-tab-scanner') return missing ? null : active;
    assert.equal(query, '#system-palette-select');
    return selector;
  };
  const audit = vm.runInNewContext(`(${browserAuditLibrary.toString()})()`, {
    Element, HTMLElement: Element, HTMLSelectElement: Select, document,
    Event: class {}, window: {sdsctlTheme: {currentSystemPalette: () => selector.value}},
    getComputedStyle: element => ({display: 'block', visibility: element.visibility ?? 'visible'}),
  });
  return {audit, document, active};
}

test('palette focus probe preserves the active pane tab', () => {
  const {audit, document, active} = fixture();
  assert.equal(audit.switchSystemPalette('ansi-light').failures.length, 0);
  assert.equal(document.activeElement, active);
});

test('palette focus probe refuses a missing active pane tab rather than silently passing', () => {
  const {audit} = fixture({missing: true});
  assert.match(audit.switchSystemPalette('ansi-light').failures[0], /no active pane tab/);
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
