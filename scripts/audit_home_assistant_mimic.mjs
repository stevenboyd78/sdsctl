#!/usr/bin/env node
/** Real custom element and Chrome layout; fictional scanner data and HA context only. */
import assert from "node:assert/strict";
import {execFileSync} from "node:child_process";
import {createServer} from "node:http";
import {mkdtemp, readFile, rm} from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import {fileURLToPath, pathToFileURL} from "node:url";
import {
  availablePort, CdpClient, evaluate, findExecutable, openChrome, pageWebSocketUrl, stopChild,
} from "./audit_web_dashboard_browser.mjs";

const ROOT = path.dirname(path.dirname(fileURLToPath(import.meta.url)));
const HELP = `Usage: node scripts/audit_home_assistant_mimic.mjs [--chrome PATH] [--python PATH] [--timeout-ms N]

Audit the packaged Mimic-SDS Home Assistant card in an isolated headless Chrome.
Uses fictional frames and a synthetic HA context on a new loopback-only server;
never contacts Home Assistant, a scanner, or an existing browser profile.
Checks all screen fixtures/layouts, panel-relative LED frames, stable automatic
height, fixed-row containment, resizing, keyboard details, and multi-card cleanup.
Requires Node 24+, Chrome/Chromium and the repository's Python development dependencies.
No screenshots are written. Only this run's temporary browser profile is removed.
`;

export function parseArguments(args) {
  const options = {chrome: null, python: null, timeoutMs: 30000, help: false};
  for (let i = 0; i < args.length; i++) {
    const argument = args[i];
    if (["-h", "--help"].includes(argument)) { options.help = true; continue; }
    assert.ok(["--chrome", "--python", "--timeout-ms"].includes(argument), "Unknown audit option.");
    const value = args[++i];
    assert.ok(typeof value === "string" && value.length > 0 && !value.startsWith("--"), "Missing option value.");
    if (argument === "--timeout-ms") options.timeoutMs = Number(value);
    else options[argument.slice(2)] = value;
  }
  assert.ok(Number.isInteger(options.timeoutMs) && options.timeoutMs > 0 && options.timeoutMs <= 120000,
    "Timeout must be an integer between 1 and 120000 milliseconds.");
  return options;
}

const PYTHON_FIXTURES = `
import json, runpy
from sds200.scanner_display_frame import project_scanner_display_frame
scenarios = runpy.run_path("scripts/render_scanner_display_frames.py")["build_scenarios"]()
result = {}
for name, variants in scenarios.items():
    preferred = variants["profile"]
    result[name] = dict(protocol="sdsctl.web", version=1, display=dict(
        schema_version=1, endpoint_id="00000000-0000-0000-0000-000000000064",
        stream_id="00000000-0000-0000-0000-000000000001",
        session_id=None if preferred.status == "disconnected" else "00000000-0000-0000-0000-000000000002",
        failure=None, source_status="matching", frames={
            "preferred" if style == "profile" else style: project_scanner_display_frame(frame)
            for style, frame in variants.items()}))
print(json.dumps(result))
`;

export function fixturePage(scenarios) {
  const payload = JSON.stringify(scenarios).replaceAll("<", "\\u003c");
  return `<!doctype html><meta charset="utf-8"><title>Fictional Mimic card browser audit</title>
<style>body{margin:0;padding:12px;background:#e6ebf0}main{min-width:0}ha-card{display:block;border-radius:12px;
border:1px solid #657287;--primary-text-color:#edf4fc;--ha-card-background:#101923}</style>
<main></main><script>
const scenarios = ${payload};
window.fixture = {name:'held_trunk', requests:0, sequence:0, sessions:0, unsubscribed:0, errors:[]};
const api = {callWS: async request => {
  if(request.endpoint==='/ingress/session'){fixture.sessions++;return {session:'synthetic_session_example_1234'};}
  if(request.endpoint==='/ingress/validate_session')return {};
  if(request.endpoint==='/addons/example_sds200/info')return {state:'started',ingress:true,ingress_url:'/api/hassio_ingress/example_key/'};
  throw Error('Unexpected synthetic HA operation.');
}};
const ui = {panels:{scanner:{component_name:'app',title:'sds200',config:{addon:'example_sds200'}}}};
document.addEventListener('context-request',event=>{
  event.stopPropagation();
  if(!['hassApi','hassUi'].includes(event.context))throw Error('Unexpected HA context.');
  event.callback(event.context==='hassApi'?api:ui,()=>fixture.unsubscribed++);
});
window.fetch=async(url,options={})=>{
  if(String(url)!==location.origin+'/api/hassio_ingress/example_key/api/v1/display-frame'||
      options.method && options.method!=='GET'){
    fixture.errors.push('Unexpected card request.');throw Error('Unexpected card request.');
  }
  fixture.requests++;fixture.sequence++;
  const data=structuredClone(scenarios[fixture.name]);
  for(const frame of Object.values(data.display.frames)){
    if(frame.sequence!==null)frame.sequence=fixture.sequence;
    if(frame.status==='current')frame.age_seconds=0;
  }
  return new Response(JSON.stringify(data),{headers:{'content-type':'application/json'}});
};
window.addCard=(id,config)=>{
  const card=document.createElement('sds200-mimic-card');card.id=id;card.setConfig(config);
  document.querySelector('main').append(card);return card;
};
window.scenarioNames=Object.keys(scenarios);
</script><script type="module" src="/waterfall.js"></script><script type="module" src="/mimic.js"></script>`;
}

export function checkGeometry(value, treatment) {
  assert.ok(value.width > 0 && value.height > 0, "Scanner panel has no size.");
  assert.ok(Math.abs(value.border - .03 * Math.min(value.width, value.height)) < 1.01,
    "LED frame must be 3% of this panel's shorter dimension.");
  assert.ok(value.edges.every(edge => edge === value.border), "LED edges have unequal thickness.");
  assert.equal(value.right, treatment === "border" ? value.top : "rgba(0, 0, 0, 0)", "LED treatment mismatch.");
  assert.equal(value.outside, 0, "Scanner cells escape the grid's content area.");
  assert.equal(value.gridFits, true, "Scanner grid escapes its surrounding panel.");
  assert.equal(value.overlap, 0, "Scanner regions overlap.");
  assert.equal(value.overflow, false, "Card causes horizontal page overflow.");
  if (value.cells > 0) {
    assert.equal(value.columns, 30, "Scanner column geometry changed.");
    assert.equal(value.rows, 20, "Scanner row geometry changed.");
  }
}

const geometry = `(id) => {
  const host=document.getElementById(id),root=host.shadowRoot;
  const panel=root.querySelector('.mimic-surround'),grid=root.querySelector('.mimic-grid');
  const box=panel.getBoundingClientRect(),bounds=grid.getBoundingClientRect(),css=getComputedStyle(grid);
  const edges=['Top','Right','Bottom','Left'].map(side=>parseFloat(css['border'+side+'Width']));
  const cells=[...grid.querySelectorAll('.mimic-cell')].map(cell=>cell.getBoundingClientRect());
  const outside=cells.filter(cell=>cell.left<bounds.left+edges[3]-1||cell.right>bounds.right-edges[1]+1||
    cell.top<bounds.top+edges[0]-1||cell.bottom>bounds.bottom-edges[2]+1).length;
  let overlap=0;
  for(let i=0;i<cells.length;i++)for(let j=i+1;j<cells.length;j++){
    const a=cells[i],b=cells[j];
    if(Math.min(a.right,b.right)-Math.max(a.left,b.left)>1&&Math.min(a.bottom,b.bottom)-Math.max(a.top,b.top)>1)overlap++;
  }
  const card=root.querySelector('ha-card');
  return {width:box.width,height:box.height,border:edges[0],edges,top:css.borderTopColor,right:css.borderRightColor,
    gridFits:Math.abs(box.width-bounds.width)<1&&Math.abs(box.height-bounds.height)<1&&Math.abs(box.left-bounds.left)<1&&Math.abs(box.top-bounds.top)<1,
    outside,overlap,cells:cells.length,columns:css.gridTemplateColumns.split(' ').length,rows:css.gridTemplateRows.split(' ').length,
    overflow:document.documentElement.scrollWidth>innerWidth+1,hostHeight:host.getBoundingClientRect().height,
    cardHeight:card.getBoundingClientRect().height,scrolls:card.scrollHeight>card.clientHeight,
    state:card.dataset.state,raw:root.querySelectorAll('[data-value-status="raw_source"]').length};
}`;

async function until(cdp, expression, timeoutMs) {
  const end = performance.now() + timeoutMs;
  while (performance.now() < end) {
    if (await evaluate(cdp, expression)) return;
    await new Promise(resolve => setTimeout(resolve, 25));
  }
  throw new Error(`Mimic browser audit condition timed out: ${expression}`);
}

export async function fixtureAssets(python, timeoutMs = 30000) {
  const scenarios = JSON.parse(execFileSync(python, ['-B', '-c', PYTHON_FIXTURES], {
    cwd: ROOT, env: {...process.env, PYTHONPATH: path.join(ROOT, 'src'), PYTHONNOUSERSITE: '1'},
    encoding: 'utf8', timeout: timeoutMs, maxBuffer: 8 * 1024 * 1024,
  }));
  assert.equal(Object.keys(scenarios).length, 33);
  const assets = new Map([
    ['/', ['text/html; charset=utf-8', fixturePage(scenarios)]],
    ['/mimic.js', ['text/javascript', await readFile(path.join(ROOT, 'src/sds200/themes/home-assistant/mimic-sds/sds200-mimic-card.js'))]],
    ['/waterfall.js', ['text/javascript', await readFile(path.join(ROOT, 'src/sds200/themes/home-assistant/waterfall/sds200-waterfall-card.js'))]],
  ]);
  return {scenarios, assets};
}

async function run(options) {
  assert.ok(Number(process.versions.node.split('.')[0]) >= 24 && typeof WebSocket === "function", "Node 24+ is required.");
  const python = await findExecutable(options.python, [path.join(ROOT, '.venv/bin/python'), 'python3'], 'Python');
  const chrome = await findExecutable(options.chrome, ['google-chrome', 'chromium', 'chromium-browser'], 'Chrome');
  const {scenarios, assets} = await fixtureAssets(python, options.timeoutMs);
  const requests = [], errors = [];
  const server = createServer((request, response) => {
    const asset = assets.get(request.url);
    if (request.method === 'GET' && asset) {
      response.writeHead(200, {'content-type': asset[0], 'cache-control': 'no-store'});response.end(asset[1]);
    } else { response.writeHead(404);response.end(); }
  });
  let browser, cdp, profile;
  try {
    await new Promise((resolve, reject) => { server.once('error', reject);server.listen(0, '127.0.0.1', resolve); });
    const origin = `http://127.0.0.1:${server.address().port}`;
    profile = await mkdtemp(path.join(os.tmpdir(), 'sdsctl-ha-mimic-audit-'));
    const remotePort = await availablePort();
    browser = await openChrome(chrome, profile, remotePort);
    cdp = await CdpClient.connect(await pageWebSocketUrl(remotePort, options.timeoutMs, browser.child), options.timeoutMs);
    cdp.on('Runtime.exceptionThrown', event => errors.push(event.exceptionDetails.text));
    cdp.on('Network.requestWillBeSent', event => requests.push(event.request.url));
    await cdp.send('Runtime.enable');await cdp.send('Page.enable');await cdp.send('Network.enable');
    const loaded = cdp.waitForEvent('Page.loadEventFired');
    await cdp.send('Page.navigate', {url: origin + '/'});await loaded;
    await until(cdp, "!!customElements.get('sds200-mimic-card') && !!customElements.get('sds200-waterfall-card')", options.timeoutMs);
    await cdp.send('Emulation.setDeviceMetricsOverride', {width: 1400, height: 1100, deviceScaleFactor: 1, mobile: false});
    await evaluate(cdp, "addCard('card',{layout:'detail',led_treatment:'border',grid_options:{rows:'auto',columns:'full'}}); true");
    await until(cdp, "document.getElementById('card')._card.dataset.state==='current'", options.timeoutMs);
    const measure = () => evaluate(cdp, `(${geometry})('card')`);
    const selectScenario = async name => {
      const previous = await evaluate(cdp, `fixture.name=${JSON.stringify(name)}; fixture.requests`);
      const frame = scenarios[name].display.frames.preferred;
      await until(cdp, `fixture.requests>${previous} && (() => {
        const frame=document.getElementById('card')._latest?.frames.preferred;
        return frame?.status===${JSON.stringify(frame.status)} &&
          frame?.sequence===${frame.sequence === null ? 'null' : 'fixture.sequence'};
      })()`, options.timeoutMs);
    };
    const configure = async config => {
      await evaluate(cdp, `document.getElementById('card').setConfig(${JSON.stringify(config)}); true`);
      await evaluate(cdp, 'new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)))');
    };
    let frames = 0, sizes = 0;
    for (const name of Object.keys(scenarios)) {
      await selectScenario(name);
      for (const layout of ['preferred', 'simple', 'detail']) {
        await configure({layout, led_treatment: 'border'});
        const value = await measure();
        checkGeometry(value, 'border');
        const expected = scenarios[name].display.frames[layout];
        assert.equal(value.state, expected.status, name);
        assert.equal(value.cells, expected.screen?.regions.length ?? 0, name);
        if (value.state !== 'current') assert.equal(value.raw, 0, name);
        frames++;
      }
    }
    await selectScenario('held_trunk');
    const colors = await evaluate(cdp, `(() => {
      const root=document.getElementById('card').shadowRoot;
      return ['system','department','channel'].map(id=>{
        const cell=root.querySelector('[data-region="'+id+'"]'),css=getComputedStyle(cell);
        return {held:cell.dataset.hold,foreground:css.color,background:css.backgroundColor};
      });
    })()`);
    const rgb = hex => `rgb(${[0,2,4].map(index=>parseInt(hex.slice(index,index+2),16)).join(', ')})`;
    const heldFrame = scenarios.held_trunk.display.frames.detail;
    assert.deepEqual(colors, ['system','department','channel'].map(id=>{
      const stored=heldFrame.screen.regions.find(region=>region.id===id).stored_color;
      return {held:'on',foreground:rgb(stored.background),background:rgb(stored.text)};
    }), 'Held names must invert their own profile colors.');
    for (const [width, dpr] of [[320, 1], [390, 2], [800, 1], [1400, 1]]) {
      await cdp.send('Emulation.setDeviceMetricsOverride', {width, height: 1100, deviceScaleFactor: dpr, mobile: false});
      for (const [density, height] of [['compact', 280], ['standard', 360], ['tall', 480]]) {
        let before;
        for (const treatment of ['border', 'strips']) {
          await configure({density, led_treatment: treatment, grid_options: {rows: 'auto', columns: 'full'}});
          const value = await measure();checkGeometry(value, treatment);
          assert.equal(value.height, height);
          if (before) assert.equal(value.hostHeight, before.hostHeight, 'LED choice changes auto height.');
          before = value;sizes++;
        }
        const oldSequence = await evaluate(cdp, 'fixture.sequence');
        await until(cdp, `fixture.sequence>=${oldSequence + 3}`, options.timeoutMs);
        assert.equal((await measure()).hostHeight, before.hostHeight, 'Repeated frames grow automatic rows.');
      }
    }
    // Host-only resizing (an HA sidebar/column change) must scale the LED too.
    const wide = await measure();
    await evaluate(cdp, "document.querySelector('main').style.width='300px'");
    await configure({density: 'tall', led_treatment: 'border'});
    const narrow = await measure();checkGeometry(narrow, 'border');assert.ok(narrow.border < wide.border);
    await evaluate(cdp, "document.querySelector('main').style.width=''");
    for (const height of [640, 260]) {
      await evaluate(cdp, `document.getElementById('card').style.height='${height}px'`);
      await configure({layout: 'detail', led_treatment: 'border', grid_options: {rows: 10}});
      const fixed = await measure();checkGeometry(fixed, 'border');
      assert.equal(fixed.hostHeight, height);assert.equal(fixed.cardHeight, height);
      if (height === 260) assert.ok(fixed.scrolls, 'Short fixed rows need an internal scrolling escape.');
      sizes++;
    }
    await evaluate(cdp, "document.getElementById('card').style.height=''");
    await configure({layout: 'detail', led_treatment: 'border'});
    // Trusted keyboard input reaches the native disclosure, which survives redraws.
    await cdp.send('Input.dispatchKeyEvent', {type:'keyDown',key:'Tab',code:'Tab',windowsVirtualKeyCode:9});
    await cdp.send('Input.dispatchKeyEvent', {type:'keyUp',key:'Tab',code:'Tab',windowsVirtualKeyCode:9});
    assert.equal(await evaluate(cdp, "document.activeElement?.shadowRoot?.activeElement?.tagName"), 'SUMMARY');
    await cdp.send('Input.dispatchKeyEvent', {type:'keyDown',key:'Enter',code:'Enter',windowsVirtualKeyCode:13,text:'\r'});
    await cdp.send('Input.dispatchKeyEvent', {type:'keyUp',key:'Enter',code:'Enter',windowsVirtualKeyCode:13});
    assert.equal(await evaluate(cdp, "document.getElementById('card')._details.open"), true);
    const oldSequence = await evaluate(cdp, 'fixture.sequence');
    await until(cdp, `fixture.sequence>=${oldSequence + 3}`, options.timeoutMs);
    assert.equal(await evaluate(cdp, "document.activeElement?.shadowRoot?.activeElement?.tagName"), 'SUMMARY');
    await evaluate(cdp, "document.getElementById('card')._details.open=false;addCard('second',{density:'compact',layout:'simple',led_treatment:'strips'});true");
    await until(cdp, "document.getElementById('second')._card.dataset.state==='current'", options.timeoutMs);
    assert.equal(await evaluate(cdp, 'fixture.sessions'), 1);
    assert.equal(await evaluate(cdp, "globalThis[Symbol.for('sdsctl.home-assistant.ingress.v1')]._leases"), 2);
    assert.deepEqual(await evaluate(cdp, "['card','second'].map(id=>document.getElementById(id)._config.layout)"), ['detail','simple']);
    await evaluate(cdp, "document.getElementById('card').remove();document.getElementById('second').remove();true");
    await until(cdp, "globalThis[Symbol.for('sdsctl.home-assistant.ingress.v1')]._leases===0", options.timeoutMs);
    const stopped = await evaluate(cdp, 'fixture.requests');
    await new Promise(resolve=>setTimeout(resolve, 750));
    assert.equal(await evaluate(cdp, 'fixture.requests'), stopped, 'Removed cards keep polling.');
    assert.equal(await evaluate(cdp, 'fixture.unsubscribed'), 2);
    assert.deepEqual(await evaluate(cdp, 'fixture.errors'), []);assert.deepEqual(errors, []);
    assert.ok(requests.every(url=>new URL(url).origin===origin), 'Audit reached a non-fixture origin.');
    console.log(`PASS: ${frames} Mimic frame/layout cases, ${sizes} sizing cases, held profile colors, 3% LED geometry, stable rows, trusted keyboard details, shared session and complete removal cleanup.`);
  } finally {
    cdp?.close();await stopChild(browser?.child ?? null);
    server.closeAllConnections();await new Promise(resolve=>server.close(resolve));
    if (profile) await rm(profile, {recursive:true,force:true,maxRetries:3,retryDelay:100});
  }
}

if (process.argv[1] && import.meta.url === pathToFileURL(path.resolve(process.argv[1])).href) {
  try {
    const options = parseArguments(process.argv.slice(2));
    if (options.help) process.stdout.write(HELP);else await run(options);
  } catch (error) { console.error(error.stack ?? String(error));process.exitCode = 1; }
}
