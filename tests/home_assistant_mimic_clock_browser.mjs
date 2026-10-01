// Actual Chrome + case-specific custom element. Synthetic HA/DTM data only.
import assert from 'node:assert/strict';
import {mkdtemp, rm} from 'node:fs/promises';
import {createServer} from 'node:http';
import os from 'node:os';
import path from 'node:path';
import {checkGeometry, geometry, until} from '../scripts/audit_home_assistant_mimic.mjs';
import {availablePort, CdpClient, evaluate, openChrome, pageWebSocketUrl, stopChild} from '../scripts/audit_web_dashboard_browser.mjs';

const chunks=[];
for await(const chunk of process.stdin)chunks.push(chunk);
const input=JSON.parse(Buffer.concat(chunks).toString('utf8'));
assert.match(input.tag,/^sds200-mimic-acceptance-[a-f0-9]{32}$/);
assert.equal(typeof input.chrome,'string');
const data=JSON.stringify({bundle:input.bundle,tag:input.tag}).replaceAll('<','\\u003c');
const page=`<!doctype html><meta charset="utf-8"><title>Fictional finite clock card</title>
<style>body{margin:0;padding:12px;background:#e6ebf0}main{min-width:0}ha-card{display:block}</style>
<main></main><script>
const {bundle,tag}=${data};
window.fixture={sequence:100,clock:100,requests:0,renewals:0,frozen:false,reject:false,errors:[]};
const api={callWS:async request=>{
  if(request.endpoint==='/ingress/session')return {session:'synthetic_session_example_1234'};
  if(request.endpoint==='/ingress/validate_session')return {};
  if(request.endpoint==='/addons/example_sds200/info')return {state:'started',ingress:true,ingress_url:'/api/hassio_ingress/example_key/'};
  throw Error('Unexpected synthetic HA operation.');
}};
const ui={panels:{scanner:{component_name:'app',title:'sds200',config:{addon:'example_sds200'}}}};
document.addEventListener('context-request',event=>{
  event.stopPropagation();
  if(!['hassApi','hassUi'].includes(event.context))throw Error('Unexpected HA context.');
  event.callback(event.context==='hassApi'?api:ui,()=>{});
});
window.fetch=async(url,options={})=>{
  fixture.requests++;
  const root=location.origin+'/api/hassio_ingress/example_key/api/v1/display-supplemental/';
  const reply=value=>new Response(JSON.stringify(value),{headers:{'content-type':'application/json'}});
  if(options.credentials!=='same-origin'||options.redirect!=='error'||options.cache!=='no-store')throw Error('Unsafe request.');
  if(url===root+'context'&&(!options.method||options.method==='GET'))
    return reply({protocol:'sdsctl.supplemental-context',version:1,context:bundle.supplemental.context});
  if(url===root+'demand'&&options.method==='POST'&&options.body===undefined){
    fixture.renewals++;
    if(fixture.reject)throw Error('Synthetic lost acknowledgement.');
    return reply({protocol:'sdsctl.supplemental-demand',version:1,context:bundle.supplemental.context,
      renewal_id:options.headers['X-SDSCTL-Supplemental-Renewal'],lease_seconds:5});
  }
  if(url===root+'frame'&&(!options.method||options.method==='GET')){
    const frame=structuredClone(bundle);fixture.sequence++;
    for(const item of Object.values(frame.display.frames)){item.sequence=fixture.sequence;item.age_seconds=0;}
    frame.supplemental.psi={sequence:fixture.sequence,age_seconds:0};
    if(!fixture.frozen)fixture.clock++;
    for(const kind of ['clock','favorites']){frame.supplemental[kind].sample_sequence=fixture.clock;frame.supplemental[kind].age_seconds=0;}
    return reply(frame);
  }
  fixture.errors.push('Unexpected request');throw Error('Unexpected request');
};
window.createCard=()=>{
  const card=document.createElement(tag);card.id='card';
  card.setConfig({type:'custom:'+tag,layout:'detail',led_treatment:'border'});
  document.querySelector('main').append(card);return true;
};
window.clockText=()=>[...document.getElementById('card').shadowRoot.querySelectorAll('.mimic-cell')]
  .map(node=>node.textContent).filter(text=>text==='Sep17'||text==='21:26');
</script><script type="module" src="/ordinary.js"></script><script type="module" src="/acceptance.js"></script>`;

const assets=new Map([['/',['text/html',page]],['/ordinary.js',['text/javascript',input.ordinary]],
  ['/acceptance.js',['text/javascript',input.script]],['/favicon.ico',['image/x-icon','']]]);
const server=createServer((req,res)=>{
  const asset=assets.get(req.url);
  if(req.method==='GET'&&asset){res.writeHead(200,{'content-type':asset[0],'cache-control':'no-store'});res.end(asset[1]);}
  else{res.writeHead(404);res.end();}
});
let browser,cdp,profile;
try{
  await new Promise((resolve,reject)=>{server.once('error',reject);server.listen(0,'127.0.0.1',resolve);});
  const origin='http://127.0.0.1:'+server.address().port, errors=[], requests=[];
  profile=await mkdtemp(path.join(os.tmpdir(),'sdsctl-finite-card-browser-'));
  const port=await availablePort();browser=await openChrome(input.chrome,profile,port);
  cdp=await CdpClient.connect(await pageWebSocketUrl(port,15000,browser.child),15000);
  cdp.on('Runtime.exceptionThrown',event=>errors.push(event.exceptionDetails.text));
  cdp.on('Network.requestWillBeSent',event=>requests.push(event.request.url));
  await cdp.send('Runtime.enable');await cdp.send('Page.enable');await cdp.send('Network.enable');
  const loaded=cdp.waitForEvent('Page.loadEventFired');await cdp.send('Page.navigate',{url:origin+'/'});await loaded;
  await until(cdp,`!!customElements.get(${JSON.stringify(input.tag)}) && !!customElements.get('sds200-mimic-card')`,15000);
  assert.deepEqual(await evaluate(cdp,'window.customCards.map(card=>card.type)'),['sds200-mimic-card']);
  assert.equal(await evaluate(cdp,"new (customElements.get('sds200-mimic-card'))()._supplemental"),false);
  await cdp.send('Emulation.setDeviceMetricsOverride',{width:1400,height:900,deviceScaleFactor:1,mobile:false});
  await evaluate(cdp,'createCard()');
  await until(cdp,"clockText().includes('Sep17') && clockText().includes('21:26')",15000);
  let layouts=0;
  for(const width of [400,640,1400]){
    await cdp.send('Emulation.setDeviceMetricsOverride',{width,height:900,deviceScaleFactor:1,mobile:false});
    for(const layout of ['preferred','simple','detail']){
      await evaluate(cdp,`document.getElementById('card').setConfig({layout:${JSON.stringify(layout)},led_treatment:'border'});true`);
      await until(cdp,"clockText().includes('Sep17') && clockText().includes('21:26')",15000);
      checkGeometry(await evaluate(cdp,`(${geometry})('card')`),'border');
      const clocks=await evaluate(cdp,`[...document.getElementById('card').shadowRoot.querySelectorAll('.mimic-cell')]
        .filter(cell=>['Sep17','21:26'].includes(cell.textContent)).map(cell=>{
          const range=document.createRange();range.selectNodeContents(cell);
          const text=range.getBoundingClientRect(),box=cell.getBoundingClientRect();
          return {text:cell.textContent,box:box.toJSON(),range:text.toJSON(),font:getComputedStyle(cell).font,
            fits:text.width>0&&text.height>0&&text.left>=box.left-1&&text.right<=box.right+1&&text.top>=box.top-1&&text.bottom<=box.bottom+1};
        })`);
      assert.ok(clocks.length===2&&clocks.every(cell=>cell.fits),`Clock clips at ${width}/${layout}: ${JSON.stringify(clocks)}`);
      layouts++;
    }
  }
  // Replayed DTM must expire while ordinary PSI continues to advance.
  await evaluate(cdp,'fixture.frozen=true');
  await until(cdp,'clockText().length===0',10000);
  assert.equal(await evaluate(cdp,"document.getElementById('card')._card.dataset.state"),'current');
  await evaluate(cdp,'fixture.frozen=false');
  await until(cdp,"clockText().includes('21:26')",10000);
  await evaluate(cdp,'fixture.reject=true');
  await until(cdp,"document.getElementById('card')._terminal",10000);
  const stopped=await evaluate(cdp,'fixture.requests');
  await evaluate(cdp,"document.getElementById('card').remove();true");
  await new Promise(resolve=>setTimeout(resolve,1100));
  assert.equal(await evaluate(cdp,'fixture.requests'),stopped);
  assert.equal(await evaluate(cdp,"globalThis[Symbol.for('sdsctl.home-assistant.ingress.v1')]._leases"),0);
  assert.deepEqual(await evaluate(cdp,'fixture.errors'),[]);assert.deepEqual(errors,[]);
  assert.ok(requests.every(url=>new URL(url).origin===origin),'Unexpected external page request.');
  console.log(`PASS: ${layouts} actual-browser clock layouts, normal-card isolation, 3% frame, DTM expiry/recovery, terminal lost demand and cleanup.`);
}finally{
  cdp?.close();await stopChild(browser?.child??null);
  server.closeAllConnections();await new Promise(resolve=>server.close(resolve));
  if(profile)await rm(profile,{recursive:true,force:true,maxRetries:3,retryDelay:100});
}
