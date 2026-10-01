#!/usr/bin/env node
// Local generated fixture only: never visits a scanner, daemon or user browser.
import assert from 'node:assert/strict';
import {mkdir, mkdtemp, realpath, writeFile} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import path from 'node:path';
import {pathToFileURL} from 'node:url';
import {
  availablePort, CdpClient, evaluate, openChrome, pageWebSocketUrl, stopChild,
} from './audit_web_dashboard_browser.mjs';

const geometry = `(() => {
  const target = document.querySelector('#consumer-1 .frame-target');
  const grid = target.querySelector('.scanner-grid');
  const issues = [];
  if (document.documentElement.scrollWidth > innerWidth + 1) issues.push('horizontal overflow');
  const cells = [...target.querySelectorAll('.cell')];
  const box = grid?.getBoundingClientRect();
  const rects = cells.map(cell => cell.getBoundingClientRect());
  for (const [i, rect] of rects.entries()) {
    if (rect.width <= 0 || rect.height <= 0 || rect.left < box.left - 1 ||
        rect.right > box.right + 1 || rect.top < box.top - 1 || rect.bottom > box.bottom + 1) {
      issues.push('region outside grid: ' + cells[i].dataset.region);
    }
    for (let j = i + 1; j < rects.length; j++) {
      const other = rects[j];
      if (Math.min(rect.right, other.right) - Math.max(rect.left, other.left) > 1 &&
          Math.min(rect.bottom, other.bottom) - Math.max(rect.top, other.top) > 1) {
        issues.push('overlapping regions');
      }
    }
  }
  for (const cell of cells) {
    const css = getComputedStyle(cell);
    const expected = cell.classList.contains('align-center') ? 'center' : 'left';
    if (css.textAlign !== expected) issues.push('incorrect alignment: ' + cell.dataset.region);
    if (cell.classList.contains('name-cell') && cell.querySelector('.field-label')) {
      issues.push('generic label in name band');
    }
  }
  const surround = target.querySelector('.scanner-surround');
  if (surround && getComputedStyle(surround).animationName !== 'none') {
    issues.push('unqualified LED animation');
  }
  return {issues, mode: grid?.dataset.mode ?? null, height: box?.height ?? null,
    width: box?.width ?? null,
    state: target.querySelector('.mimic-frame').dataset.state,
    raw: target.querySelectorAll('[data-value-status=raw_source]').length,
    details: !!target.querySelector('.field-details')};
})()`;

async function select(cdp, consumer, scenario, style) {
  await evaluate(cdp, `(() => {
    const panel = document.getElementById(${JSON.stringify(consumer)});
    const scenario = panel.querySelector('.scenario');
    const style = panel.querySelector('.style');
    scenario.value = ${JSON.stringify(scenario)};
    style.value = ${JSON.stringify(style)};
    style.dispatchEvent(new Event('change', {bubbles: true}));
  })()`);
}

async function audit(source, output, chrome) {
  // A path, not a URL; the generator is the intended input. Isolated new profile.
  const url = pathToFileURL(await realpath(source)).href;
  await mkdir(output, {recursive: true});
  const profile = await mkdtemp(path.join(tmpdir(), 'sdsctl-frame-audit-'));
  const port = await availablePort();
  const process = await openChrome(chrome, profile, port);
  let cdp;
  const failures = [];
  const requests = [];
  const results = [];
  try {
    cdp = await CdpClient.connect(await pageWebSocketUrl(port, 15000, process.child), 15000);
    cdp.on('Runtime.exceptionThrown', event => failures.push(event.exceptionDetails.text));
    cdp.on('Network.requestWillBeSent', event => requests.push(event.request.url));
    await cdp.send('Runtime.enable');
    await cdp.send('Page.enable');
    await cdp.send('Network.enable');
    const loaded = cdp.waitForEvent('Page.loadEventFired');
    await cdp.send('Page.navigate', {url});
    await loaded;
    assert.equal(await evaluate(cdp, 'document.documentElement.dataset.previewReady'), 'true');
    const scenarios = await evaluate(cdp,
      '[...document.querySelector(".scenario").options].map(option => option.value)');
    assert.equal(scenarios.length, 33);
    const expansion = [];
    for (const [width, height] of [[800, 480], [1920, 1080], [390, 844], [2560, 1440]]) {
      await cdp.send('Emulation.setDeviceMetricsOverride', {
        width, height, deviceScaleFactor: 1, mobile: false,
      });
      let screenHeight;
      for (const scenario of scenarios) {
        for (const style of ['profile', 'simple', 'detail']) {
          await select(cdp, 'consumer-1', scenario, style);
          const result = await evaluate(cdp, geometry);
          assert.deepEqual(result.issues, [], `${width}/${scenario}/${style}`);
          if (result.state !== 'current') assert.equal(result.raw, 0);
          if (result.mode !== null) {
            screenHeight ??= result.height;
            assert.equal(result.height, screenHeight, 'screen height changed between frames');
            assert.equal(result.details, true);
          }
          results.push({width, height, scenario, style, ...result});
        }
      }
      // Saved LCD character counts do not cap the width of fields on larger displays.
      await select(cdp, 'consumer-1', 'held_trunk', 'simple');
      const widths = await evaluate(cdp, `(() => {
        const panel = document.querySelector('#consumer-1');
        const cell = id => panel.querySelector('[data-region="' + id + '"]');
        return Object.fromEntries(['system', 'option_a_1', 'option_1'].map(id =>
          [id, cell(id).getBoundingClientRect().width]));
      })()`);
      expansion.push({width, ...widths});
      for (const style of ['simple', 'detail']) {
        await select(cdp, 'consumer-1', 'held_trunk', style);
        const names = await evaluate(cdp, `(() => {
          const panel = document.querySelector('#consumer-1');
          return ['system', 'department', 'channel'].map(id => {
            const cell = panel.querySelector('[data-region="' + id + '"]');
            const css = getComputedStyle(cell);
            const text = getComputedStyle(cell.querySelector('.field-value'));
            return {hold: cell.dataset.hold, color: css.color, background: css.backgroundColor,
              whiteSpace: text.whiteSpace, lines: text.webkitLineClamp};
          });
        })()`);
        assert.deepEqual(names.map(name => name.hold), ['on', 'on', 'on']);
        assert.deepEqual(names.map(name => name.color), Array(3).fill('rgb(0, 0, 0)'));
        assert.deepEqual(names.map(name => name.background),
          ['rgb(255, 48, 48)', 'rgb(64, 240, 64)', 'rgb(68, 119, 255)']);
        for (const name of names) {
          assert.equal(name.whiteSpace, style === 'simple' ? 'normal' : 'nowrap');
          assert.equal(name.lines, style === 'simple' ? '2' : 'none');
        }
      }
      // LED treatment changes neither geometry nor the precomputed frame nor the other consumer.
      const beforeLed = await evaluate(cdp, geometry);
      const frameBeforeLed = await evaluate(cdp,
        'document.querySelector("#consumer-1 .frame-target").innerHTML');
      const secondBeforeLed = await evaluate(cdp,
        'document.querySelector("#consumer-2").outerHTML');
      for (const treatment of ['border', 'strips']) {
        await evaluate(cdp, `(() => {
          const control = document.querySelector('#consumer-1 .led-style');
          control.value = ${JSON.stringify(treatment)};
          control.dispatchEvent(new Event('change', {bubbles: true}));
        })()`);
        assert.deepEqual(await evaluate(cdp, geometry), beforeLed);
        assert.equal(await evaluate(cdp,
          'document.querySelector("#consumer-1 .frame-target").innerHTML'), frameBeforeLed);
        assert.equal(await evaluate(cdp,
          'document.querySelector("#consumer-2").outerHTML'), secondBeforeLed);
        const colors = await evaluate(cdp, `(() => {
          const css = getComputedStyle(document.querySelector('#consumer-1 .scanner-surround'));
          return [css.borderTopColor, css.borderRightColor];
        })()`);
        assert.equal(colors[0], 'rgb(255, 225, 50)');
        assert.equal(colors[1], treatment === 'border' ? colors[0] : 'rgba(0, 0, 0, 0)');
      }
      for (const [scenario, style] of [
        ['conventional', 'simple'], ['trunk', 'detail'], ['stale', 'detail'],
        ['profile_refresh_failed', 'detail'], ['weather', 'profile'],
        ['held_trunk', 'simple'], ['held_trunk', 'detail'], ['department_held', 'detail'],
        ['held_stale', 'detail'],
      ]) {
        await select(cdp, 'consumer-1', scenario, style);
        await evaluate(cdp, 'scrollTo(0, 0)');
        const {contentSize} = await cdp.send('Page.getLayoutMetrics');
        const screenshot = await cdp.send('Page.captureScreenshot', {
          format: 'png', captureBeyondViewport: true,
          clip: {x: 0, y: 0, width, height: Math.ceil(contentSize.height), scale: 1},
        });
        await writeFile(path.join(output, `${width}-${scenario}-${style}.png`),
          Buffer.from(screenshot.data, 'base64'), {flag: 'wx'});
      }
      // Comparison is independent even when both panels are mounted and visible.
      await evaluate(cdp, 'document.querySelector(".compare").open = true');
      await select(cdp, 'consumer-1', 'conventional', 'simple');
      const before = await evaluate(cdp,
        'document.querySelector("#consumer-1 .frame-target").innerHTML');
      await select(cdp, 'consumer-2', 'trunk', 'detail');
      assert.equal(await evaluate(cdp,
        'document.querySelector("#consumer-1 .frame-target").innerHTML'), before);
      await evaluate(cdp, 'document.querySelector(".compare").open = false');
      // Keyboard Tab reaches style, native arrow changes it; frame replacement keeps focus.
      await evaluate(cdp, 'document.querySelector("#consumer-1 .scenario").focus()');
      for (const type of ['keyDown', 'keyUp']) {
        await cdp.send('Input.dispatchKeyEvent', {type, key: 'Tab', code: 'Tab', windowsVirtualKeyCode: 9});
      }
      assert.equal(await evaluate(cdp, 'document.activeElement.className'), 'style');
      for (const type of ['keyDown', 'keyUp']) {
        await cdp.send('Input.dispatchKeyEvent', {
          type, key: 'ArrowDown', code: 'ArrowDown', windowsVirtualKeyCode: 40,
        });
      }
      assert.equal(await evaluate(cdp, 'document.activeElement.className'), 'style');
      assert.equal(await evaluate(cdp,
        'document.querySelector("#consumer-1 .scanner-grid").dataset.mode'), 'detail_conventional');
      await evaluate(cdp, `document.body.style.fontSize = '30px';
        document.querySelector('#consumer-1 .field-details').open = true`);
      assert.deepEqual((await evaluate(cdp, geometry)).issues, []);
      await evaluate(cdp, `document.body.style.fontSize = '';
        document.querySelector('#consumer-1 .field-details').open = false`);
    }
    const narrow = expansion.find(item => item.width === 800);
    const hdmi = expansion.find(item => item.width === 1920);
    const wide = expansion.find(item => item.width === 2560);
    for (const field of ['system', 'option_a_1', 'option_1']) {
      assert.ok(hdmi[field] > narrow[field] * 2, 'field did not expand at HDMI width');
      assert.ok(wide[field] > hdmi[field] * 1.25, 'field capped at desktop width');
    }
    assert.deepEqual(failures, [], 'browser exceptions');
    assert.deepEqual([...new Set(requests)], [url], 'unexpected resource or network requests');
    await writeFile(path.join(output, 'audit.json'), JSON.stringify({
      status: 'passed', cases: results.length, profile, results,
      independentConsumers: true, keyboard: true, enlargedControls: true,
      expandingFields: expansion, holdInversion: true, ledTreatments: true, alignment: true,
      requests: requests.length, exceptions: failures,
    }, null, 2), {flag: 'wx'});
    console.log(`PASS ${results.length} frame/viewport cases; independent consumers, keyboard, ` +
      `enlarged controls, no page network requests. Evidence: ${output}`);
    console.log(`Isolated test browser profile retained: ${profile}`);
  } finally {
    cdp?.close();
    await stopChild(process.child);
  }
}

const [source, output, chrome = '/usr/bin/google-chrome'] = process.argv.slice(2);
if (!source || !output) throw new Error('Usage: node audit_scanner_display_frames.mjs INDEX OUTPUT [CHROME]');
await audit(source, output, chrome);
