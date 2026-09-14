import test from "node:test";
import assert from "node:assert/strict";
import {connectRetirementWorker,connectRetirementPage,createChromeRetirementPorts} from
  "../../src/sds200/browser_assets/browser_device_retirement_ui.mjs";
import {createBrowserRecovery} from "../../src/sds200/browser_assets/browser_device_recovery.mjs";

const id="a".repeat(32),identity="b".repeat(64),intent="c".repeat(64),retirement="d".repeat(64);
const url=`chrome-extension://${id}/recovery.html`,nativeHost="org.sdsctl.retirement";
const ticket="12345678-1234-1234-1234-123456789abc";
const proof={identity,intent,retirement,mode:"paused",revision:5};
const review={mode:"retirement_reviewed",nativeRevision:5,nativeMode:"paused"};
const settle=()=>new Promise(resolve=>setImmediate(resolve));
function workerFixture() {
  const f={time:100000,reviews:0,confirms:[]};
  f.chrome={runtime:{id,getURL:name=>`chrome-extension://${id}/${name}`,
    onMessage:{addListener:handler=>{f.handler=handler;}}}};
  f.controller={reviewPendingRetirement:async()=>{f.reviews++;return {...review};},
    retirePending:async r=>{f.confirms.push(r);return {mode:"retired_paused",localPauseSaved:true,sessionReady:false};}};
  f.sender={id,url,frameId:0,documentLifecycle:"active",documentId:"document-1",tab:{id:7,incognito:false}};
  f.send=async(message,sender=f.sender)=>{
    const replies=[],waiting=f.handler(message,sender,r=>replies.push(r));
    await settle();return {waiting,replies};
  };
  f.start=()=>connectRetirementWorker(f.chrome,f.controller,()=>f.time);
  f.start();return f;
}
test("one-use review/confirmation is document bound and exposes only paused resolution",async()=>{
  const f=workerFixture();assert.equal(f.reviews,0);
  const r=(await f.send({action:"retirement-review"})).replies[0];
  assert.deepEqual({...r,ticket:"redacted"},{...review,ticket:"redacted"});
  assert.deepEqual((await f.send({action:"retirement-confirm",ticket:r.ticket})).replies,[{mode:"retired_paused"}]);
  assert.deepEqual(f.confirms,[{nativeRevision:5,nativeMode:"paused"}]);
  assert.deepEqual((await f.send({action:"retirement-confirm",ticket:r.ticket})).replies,[]);
  assert.deepEqual((await f.send({action:"retirement-review"})).replies,[{mode:"retirement_refused"}]);
});
for(const sender of [null,{id:"z"},{url:url+"?x"},{url:url.replace("recovery","resume")},
  {url:"https://example.invalid"},{frameId:1},{documentLifecycle:"prerender"},{documentId:"bad:id"},
  {documentId:undefined},{tab:{id:7,incognito:true}},{tab:{id:-1,incognito:false}}]) {
  test("untrusted sender cannot review: "+JSON.stringify(sender),async()=>{
    const f=workerFixture();assert.equal((await f.send({action:"retirement-review"},sender===null?null:{...f.sender,...sender})).waiting,false);
    assert.equal(f.reviews,0);assert.deepEqual(f.confirms,[]);
  });
}
for(const change of ["document","tab","ticket","extra","expired","rollback","nan","restart"]) {
  test("changed confirmation refuses: "+change,async()=>{
    const f=workerFixture(),r=(await f.send({action:"retirement-review"})).replies[0];
    const sender=structuredClone(f.sender),message={action:"retirement-confirm",ticket:r.ticket};
    if(change==="document")sender.documentId="document-2";
    if(change==="tab")sender.tab.id++;
    if(change==="ticket")message.ticket=ticket;
    if(change==="extra")message.operation_id=retirement;
    if(change==="expired")f.time+=60000;
    if(change==="rollback")f.time--;
    if(change==="nan")f.time=NaN;
    if(change==="restart")f.start();
    assert.equal((await f.send(message,sender)).waiting,false);assert.deepEqual(f.confirms,[]);
  });
}
for(const result of [null,{...review,nativeMode:"active"},{...review,nativeRevision:true},
  {...review,nativeRevision:Number.MAX_SAFE_INTEGER},{...review,secret:"private"}]) {
  test("worker refuses malformed review "+JSON.stringify(result),async()=>{
    const f=workerFixture();f.controller.reviewPendingRetirement=()=>result;
    assert.deepEqual((await f.send({action:"retirement-review"})).replies,[{mode:"retirement_refused"}]);
    assert.deepEqual((await f.send({action:"retirement-review"})).replies,[{mode:"retirement_refused"}]);
    assert.deepEqual(f.confirms,[]);
  });
}
for(const result of [null,{mode:"retired_paused"},{mode:"retired_paused",localPauseSaved:false,sessionReady:false},
  {mode:"retired_paused",localPauseSaved:true,sessionReady:true},
  {mode:"retired_paused",localPauseSaved:true,sessionReady:false,secret:"private"}]) {
  test("worker requires exact confirmed paused result "+JSON.stringify(result),async()=>{
    const f=workerFixture();f.controller.retirePending=()=>result;
    const r=(await f.send({action:"retirement-review"})).replies[0];
    assert.deepEqual((await f.send({action:"retirement-confirm",ticket:r.ticket})).replies,[{mode:"retirement_refused"}]);
    assert.deepEqual((await f.send({action:"retirement-confirm",ticket:r.ticket})).replies,[]);
  });
}
for(const phase of ["review","confirm"]) test("synchronous or lost adapter failure stays redacted: "+phase,async()=>{
  const f=workerFixture();let calls=0;
  f.controller[phase==="review"?"reviewPendingRetirement":"retirePending"]=()=>{calls++;throw new Error("private");};
  const r=(await f.send({action:"retirement-review"})).replies[0];
  if(phase==="confirm")assert.deepEqual((await f.send({action:"retirement-confirm",ticket:r.ticket})).replies,[{mode:"retirement_refused"}]);
  else assert.deepEqual(r,{mode:"retirement_refused"});
  await f.send({action:"retirement-review"});assert.equal(calls,1);
});
test("delayed review expires and cannot acquire confirmation",async()=>{
  const f=workerFixture();let resolve;
  f.controller.reviewPendingRetirement=()=>new Promise(r=>{resolve=r;});
  const responses=[];assert.equal(f.handler({action:"retirement-review"},f.sender,r=>responses.push(r)),true);
  await settle();f.time+=60000;resolve(review);await settle();
  assert.deepEqual(responses,[{mode:"retirement_refused"}]);assert.deepEqual(f.confirms,[]);
});

function pageFixture() {
  const f={calls:[],elements:{}};
  for(const name of ["review","recovery-form","confirm","resolve","notice","reviewed"]) {
    const handlers={};f.elements[name]={disabled:false,checked:true,textContent:"",
      addEventListener:(event,handler)=>{handlers[event]=handler;},
      emit:(event,trusted=true)=>handlers[event]({isTrusted:trusted,preventDefault(){}})};
  }
  f.window={location:{href:url}};f.window.top=f.window;
  f.runtime={getURL:name=>`chrome-extension://${id}/${name}`,sendMessage:async message=>{
    f.calls.push(message);return message.action==="retirement-review"?{...review,ticket}:{mode:"retired_paused"};}};
  f.document={getElementById:name=>f.elements[name]};
  f.start=()=>connectRetirementPage(f);return f;
}
test("opening is inert and prior checkbox state is reset; two trusted gestures keep pause",async()=>{
  const f=pageFixture();f.start();assert.deepEqual(f.calls,[]);
  assert(f.elements.resolve.disabled);assert.equal(f.elements.confirm.checked,false);
  f.elements.review.emit("click",false);await settle();assert.deepEqual(f.calls,[]);
  f.elements.review.emit("click");await settle();
  assert.match(f.elements.notice.textContent,/remain paused/);
  f.elements["recovery-form"].emit("submit");await settle();assert.equal(f.calls.length,1);
  f.elements.confirm.checked=true;
  f.elements["recovery-form"].emit("submit",false);await settle();assert.equal(f.calls.length,1);
  f.elements["recovery-form"].emit("submit");await settle();
  assert.deepEqual(f.calls,[{action:"retirement-review"},{action:"retirement-confirm",ticket}]);
  assert.match(f.elements.notice.textContent,/No login was attempted/);
  f.elements["recovery-form"].emit("submit");f.elements.review.emit("click");await settle();assert.equal(f.calls.length,2);
});
for(const phase of ["review","confirm"]) test("lost page acknowledgement never retries "+phase,async()=>{
  const f=pageFixture(),original=f.runtime.sendMessage;
  f.runtime.sendMessage=message=>{
    if(message.action===`retirement-${phase}`){f.calls.push(message);throw new Error("private");}
    return original(message);
  };
  f.start();f.elements.review.emit("click");await settle();
  if(phase==="confirm") {f.elements.confirm.checked=true;f.elements["recovery-form"].emit("submit");await settle();}
  assert(!f.elements.notice.textContent.includes("private"));assert(f.elements.resolve.disabled);
  assert.match(f.elements.notice.textContent,phase==="confirm"?/acknowledgement was lost/:/No browser recovery record was changed/);
});
for(const value of [null,{...review,ticket,nativeMode:"active"},{...review,ticket,extra:"private"},
  {...review,ticket:"bad"},{...review,ticket,nativeRevision:0}]) test("malformed page review rejected "+JSON.stringify(value),async()=>{
  const f=pageFixture();f.runtime.sendMessage=async()=>value;f.start();f.elements.review.emit("click");await settle();
  assert(f.elements.resolve.disabled);assert.match(f.elements.notice.textContent,/could not be verified/);
});
for(const change of ["frame","query"]) test("page rejects "+change,()=>{
  const f=pageFixture();if(change==="frame")f.window.top={};else f.window.location.href+="?x";
  assert.throws(f.start,/setup/);assert.deepEqual(f.calls,[]);
});

function portsFixture() {
  const f={calls:[],reply:{version:1,ok:true,evidence:{...proof}}};
  f.chrome={runtime:{sendNativeMessage:async(...args)=>{f.calls.push(args);return f.reply;}}};
  f.ports=createChromeRetirementPorts(f.chrome,{nativeHost,identity});return f;
}
test("native port sends only exact identity/intent and copies validated evidence",async()=>{
  const f=portsFixture();const result=await f.ports.confirm({identity,intent});
  assert.deepEqual(result,proof);assert.notEqual(result,f.reply.evidence);
  assert.deepEqual(f.calls,[[nativeHost,{version:1,action:"confirm-retirement",identity,intent}]]);
});
for(const request of [null,{identity,intent,archive:"/tmp"},{identity:"f".repeat(64),intent},{identity,intent:"../other"}]) {
  test("native port rejects caller selection "+JSON.stringify(request),async()=>{
    const f=portsFixture();await assert.rejects(f.ports.confirm(request),/confirmation/);assert.deepEqual(f.calls,[]);
  });
}
for(const value of [null,{version:1,ok:false,mode:"setup_error"},
  {version:1,ok:true,evidence:{...proof,mode:"active"}},
  {version:1,ok:true,evidence:{...proof,intent:"f".repeat(64)}},
  {version:1,ok:true,evidence:{...proof,revision:true}},
  {version:1,ok:true,evidence:{...proof,session:"private"}},
  {version:1,ok:true,evidence:proof,secret:"private"}]) test("native port rejects invalid response "+JSON.stringify(value),async()=>{
  const f=portsFixture();f.reply=value;await assert.rejects(f.ports.confirm({identity,intent}),/confirmation/);
});
test("trusted host settings are fixed before await, not mutable selection",async()=>{
  const f=portsFixture(),settings={nativeHost,identity};
  const ports=createChromeRetirementPorts(f.chrome,settings);settings.nativeHost="other";
  await ports.confirm({identity,intent});assert.equal(f.calls[0][0],nativeHost);
  for(const invalid of [{nativeHost:"../other",identity},{nativeHost,identity,operation:retirement},{nativeHost,identity:"x"}])
    assert.throws(()=>createChromeRetirementPorts(f.chrome,invalid),/setup/);
});

test("joined page-worker-ports-coordinator resolves intent but never resumes",async()=>{
  const w=workerFixture(),p=pageFixture(),n=portsFixture();let saved={version:2,identity,paused:true,phase:"resume_pending",nextAt:0,intent};
  let cookie="old",alarm=1;
  const ports={now:()=>w.time,load:async()=>structuredClone(saved),save:async value=>{saved=structuredClone(value);},
    retirement:n.ports,clearCookie:async()=>{cookie=null;},cancel:async()=>{alarm=null;},
    native:async({action})=>{assert.equal(action,"suspend");return {version:1,ok:true,mode:"paused",revision:5,retry_after:0,renew_after:0};},
    schedule:async()=>assert.fail("unexpected scheduling"),setCookie:async()=>assert.fail("unexpected session")};
  w.controller=createBrowserRecovery(ports,{origin:"https://192.0.2.18:8443",identity,nativeHost});w.start();
  p.runtime.sendMessage=message=>new Promise(resolve=>w.handler(message,w.sender,resolve));p.start();
  p.elements.review.emit("click");await settle();assert.equal(saved.phase,"resume_pending");assert.equal(cookie,"old");
  p.elements.confirm.checked=true;p.elements["recovery-form"].emit("submit");await settle();
  assert.equal(saved.phase,"clean");assert(saved.paused);assert.equal(cookie,null);assert.equal(alarm,null);
  assert.equal(n.calls.length,2);assert.match(p.elements.notice.textContent,/No login was attempted/);
  const restarted=createBrowserRecovery(ports,{origin:"https://192.0.2.18:8443",identity,nativeHost});
  await restarted.tick();assert(saved.paused);assert.equal(restarted.readiness().sessionReady,false);
});
