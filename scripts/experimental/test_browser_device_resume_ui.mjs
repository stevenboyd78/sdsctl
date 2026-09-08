import test from "node:test";
import assert from "node:assert/strict";
import {connectResumeWorker, connectResumePage, connectResumeContent, createChromeResumePorts} from
  "../../src/sds200/browser_assets/browser_device_resume.mjs";

const id="a".repeat(32), url="chrome-extension://"+id+"/resume.html";
const ticket="12345678-1234-1234-1234-123456789abc";
const origin="https://192.0.2.18:8443";
const settle=()=>new Promise(resolve=>setImmediate(resolve));
const reviewed={mode:"reviewed",nativeRevision:3,serverGeneration:4};
const deferred=()=>{let resolve;const promise=new Promise(r=>{resolve=r;});return {resolve,promise};};
function workerFixture() {
  const f={time:100000,reviewCalls:0,resumeCalls:[]};
  f.chrome={runtime:{id,getURL:name=>"chrome-extension://"+id+"/"+name,
    onMessage:{addListener:handler=>{f.handler=handler;}}}};
  f.controller={reviewResume:async()=>{f.reviewCalls++;return reviewed;},
    resume:async review=>{f.resumeCalls.push(review);return {mode:"active",secret:"must not escape"};}};
  f.sender={id,url,frameId:0,documentLifecycle:"active",documentId:"document-1",tab:{id:7,incognito:false}};
  f.send=async(message,sender=f.sender)=>{
    const replies=[];const waiting=f.handler(message,sender,value=>replies.push(value));
    await settle();return {waiting,replies};
  };
  connectResumeWorker(f.chrome,f.controller,()=>f.time);return f;
}

test("worker binds one review and confirmation to exact document and returns only redacted status",async()=>{
  const f=workerFixture();
  assert.equal(f.reviewCalls,0);
  const first=await f.send({action:"resume-review"}), response=first.replies[0];
  assert.equal(first.waiting,true);assert.equal(response.mode,"reviewed");
  assert.deepEqual(Object.keys(response).sort(),["mode","nativeRevision","serverGeneration","ticket"]);
  const confirmed=await f.send({action:"resume-confirm",ticket:response.ticket});
  assert.deepEqual(confirmed.replies,[{mode:"resumed"}]);
  assert.deepEqual(f.resumeCalls,[{nativeRevision:3,serverGeneration:4}]);
  assert.deepEqual((await f.send({action:"resume-confirm",ticket:response.ticket})).replies,[]);
  assert.deepEqual((await f.send({action:"resume-review"})).replies,[{mode:"resume_refused"}]);
});
for(const override of [
  {id:"b".repeat(32)},{url:origin+"/"},{url:url+"?x=1"},{url:url.replace("resume","control")},
  {frameId:1},{documentLifecycle:"prerender"},{documentId:""},{documentId:undefined},
  {documentId:"bad:document"},{tab:{id:7,incognito:true}},{tab:{id:-1,incognito:false}},
]) test("worker refuses untrusted resume sender "+JSON.stringify(override),async()=>{
  const f=workerFixture();
  assert.equal((await f.send({action:"resume-review"},{...f.sender,...override})).waiting,false);
  assert.equal(f.reviewCalls,0);assert.deepEqual(f.resumeCalls,[]);
});
for(const change of ["document","tab","expired","rollback","ticket","fields"]) {
  test("confirmation refuses changed "+change,async()=>{
    const f=workerFixture(), r=(await f.send({action:"resume-review"})).replies[0];
    const sender=structuredClone(f.sender), message={action:"resume-confirm",ticket:r.ticket};
    if(change==="document")sender.documentId="document-2";
    if(change==="tab")sender.tab.id++;
    if(change==="expired")f.time+=60000;
    if(change==="rollback")f.time--;
    if(change==="ticket")message.ticket=ticket;
    if(change==="fields")message.nativeRevision=3;
    assert.equal((await f.send(message,sender)).waiting,false);
    assert.deepEqual(f.resumeCalls,[]);
  });
}
test("lost or refused worker review is not silently retried",async()=>{
  const f=workerFixture();
  f.controller.reviewResume=async()=>{throw new Error("private information");};
  assert.deepEqual((await f.send({action:"resume-review"})).replies,[{mode:"resume_refused"}]);
  assert.deepEqual((await f.send({action:"resume-review"})).replies,[{mode:"resume_refused"}]);
  assert.equal((await f.send({action:"resume-confirm",ticket})).waiting,false);
});

function pageFixture() {
  const f={calls:[],elements:{}};
  for(const name of ["review","resume-form","confirm","resume","notice","reviewed"]) {
    const handlers={};
    f.elements[name]={disabled:["confirm","resume"].includes(name),checked:false,textContent:"",
      addEventListener:(kind,handler)=>{handlers[kind]=handler;},
      emit:(kind,trusted=true)=>handlers[kind]({isTrusted:trusted,preventDefault(){}})};
  }
  f.window={location:{href:url}};f.window.top=f.window;
  f.runtime={getURL:name=>"chrome-extension://"+id+"/"+name,sendMessage:async message=>{
    f.calls.push(message);return message.action==="resume-review"?{...reviewed,ticket}:{mode:"resumed"};
  }};
  f.document={getElementById:name=>f.elements[name]};
  f.start=()=>connectResumePage(f);return f;
}
test("page opening is inert; two trusted gestures and checked consent produce one attempt",async()=>{
  const f=pageFixture();f.start();
  assert.deepEqual(f.calls,[]);assert(f.elements.resume.disabled);
  f.elements.review.emit("click",false);await settle();assert.deepEqual(f.calls,[]);
  f.elements["resume-form"].emit("submit");await settle();assert.deepEqual(f.calls,[]);
  f.elements.review.emit("click");await settle();
  assert.match(f.elements.notice.textContent,/Server permission verified/);
  assert.equal(f.elements.confirm.disabled,false);assert.equal(f.elements.resume.disabled,false);
  f.elements["resume-form"].emit("submit");await settle();assert.equal(f.calls.length,1);
  f.elements.confirm.checked=true;
  f.elements["resume-form"].emit("submit",false);await settle();assert.equal(f.calls.length,1);
  f.elements["resume-form"].emit("submit");await settle();
  assert.deepEqual(f.calls,[{action:"resume-review"},{action:"resume-confirm",ticket}]);
  assert.match(f.elements.notice.textContent,/Automatic sign-in resumed/);
  f.elements["resume-form"].emit("submit");f.elements.review.emit("click");await settle();
  assert.equal(f.calls.length,2);assert(f.elements.resume.disabled);
});
for(const result of [null,{mode:"reviewed"},{...reviewed,ticket,extra:"secret"},
  {...reviewed,ticket,nativeRevision:true},{...reviewed,ticket:"bad"}]) {
  test("page refuses malformed review "+JSON.stringify(result),async()=>{
    const f=pageFixture();f.runtime.sendMessage=async()=>result;f.start();
    f.elements.review.emit("click");await settle();
    assert(f.elements.resume.disabled);assert.match(f.elements.notice.textContent,/no resume was attempted/);
    assert(!f.elements.notice.textContent.includes("secret"));
  });
}
test("page keeps a lost confirmation distinct and never repeats it",async()=>{
  const f=pageFixture(), original=f.runtime.sendMessage;
  f.runtime.sendMessage=async message=>{
    if(message.action==="resume-confirm"){f.calls.push(message);throw new Error("private token");}
    return original(message);
  };
  f.start();f.elements.review.emit("click");await settle();f.elements.confirm.checked=true;
  f.elements["resume-form"].emit("submit");await settle();
  assert.match(f.elements.notice.textContent,/acknowledgement was lost/);
  assert(!f.elements.notice.textContent.includes("private token"));
  f.elements["resume-form"].emit("submit");await settle();assert.equal(f.calls.length,2);
});
for(const kind of ["subframe","query"]) test("page refuses "+kind,()=>{
  const f=pageFixture();
  if(kind==="subframe")f.window.top={};else f.window.location.href+="?x=1";
  assert.throws(f.start,/setup/);assert.deepEqual(f.calls,[]);
});

const sessionBody=JSON.stringify({display_only:true,device_enrolled:true,remaining_seconds:300});
function contentFixture(body=sessionBody) {
  const f={calls:[]}, window={location:{href:origin+"/device-display"}};window.top=window;
  const runtime={id,onMessage:{addListener:handler=>{f.handler=handler;}}};
  f.response={status:200,redirected:false,url:origin+"/auth/session",
    headers:new Headers({"content-type":"application/json"}),
    body:new Response(body).body};
  f.window=window;f.fetcher=async(...args)=>{f.calls.push(args);return f.response;};
  connectResumeContent({window,runtime,fetcher:(...args)=>f.fetcher(...args)},origin);
  f.send=async(message={action:"resume-probe",ticket},sender={id})=>{
    const replies=[],waiting=f.handler(message,sender,r=>replies.push(r));
    await settle();return {waiting,replies};
  };return f;
}
test("isolated probe checks real same-origin protected endpoint and exposes only verdict",async()=>{
  const f=contentFixture();
  assert.deepEqual((await f.send()).replies,[{ticket,verified:true}]);
  assert.equal(f.calls[0][0],origin+"/auth/session");
  const options=f.calls[0][1];
  assert.equal(options.credentials,"same-origin");assert.equal(options.redirect,"error");
  assert.equal(options.cache,"no-store");assert.equal(options.method,"GET");
  assert.deepEqual(options.headers,{Accept:"application/json"});assert(options.signal instanceof AbortSignal);
});
for(const body of [
  '{"display_only":true,"display_only":false,"device_enrolled":true,"remaining_seconds":300}',
  '{"display_only":false,"display_only":true,"device_enrolled":true,"remaining_seconds":300}',
  JSON.stringify({display_only:false,device_enrolled:true,remaining_seconds:300}),
  JSON.stringify({display_only:true,device_enrolled:false,remaining_seconds:300}),
  ...[0,30,3601,"300",null].map(remaining_seconds=>JSON.stringify({display_only:true,device_enrolled:true,remaining_seconds})),
  '{"display_only":true,"device_enrolled":true,"remaining_seconds":300,"token":"private"}',
  sessionBody+" ".repeat(1024),"<html>login</html>","null","[]",
  new Uint8Array([0xff,0xfe]),
]) test("probe rejects invalid protected body "+String(body).slice(0,100),async()=>{
  assert.deepEqual((await contentFixture(body).send()).replies,[{ticket,verified:false}]);
});
for(const kind of ["status","redirect","url","type","navigate","network"]) {
  test("probe fails closed on "+kind,async()=>{
    const f=contentFixture();
    if(kind==="status")f.response.status=401;
    if(kind==="redirect")f.response.redirected=true;
    if(kind==="url")f.response.url=origin+"/elsewhere";
    if(kind==="type")f.response.headers=new Headers({"content-type":"text/html"});
    if(kind==="navigate")f.fetcher=async()=>{f.window.location.href=origin+"/";return f.response;};
    if(kind==="network")f.fetcher=async()=>{throw new Error("private");};
    assert.deepEqual((await f.send()).replies,[{ticket,verified:false}]);
  });
}
for(const kind of ["sender","page","frame","fields","ticket"]) {
  test("probe refuses untrusted "+kind+" before request",async()=>{
    const f=contentFixture(), sender={id}, message={action:"resume-probe",ticket};
    if(kind==="sender")sender.id="b".repeat(32);
    if(kind==="page")f.window.location.href+="#x";
    if(kind==="frame")f.window.top={};
    if(kind==="fields")message.url=origin+"/";
    if(kind==="ticket")message.ticket="bad";
    assert.equal((await f.send(message,sender)).waiting,false);assert.deepEqual(f.calls,[]);
  });
}

function portsFixture() {
  const f={calls:[],removed:[],cookies:0};
  f.cookie={name:"__Host-sdsctl-device-session",secure:true,httpOnly:true,hostOnly:true,path:"/",
    sameSite:"strict",session:false,expirationDate:Date.now()/1000+300,value:"private-session"};
  f.page={id:11,url:origin+"/device-display",status:"complete",incognito:false};
  f.chrome={
    runtime:{sendNativeMessage:async(host,request)=>{f.calls.push([host,request]);return f.reply;}},
    cookies:{get:async()=>{f.cookies++;return {...f.cookie};}},
    tabs:{
      create:async options=>{f.calls.push(options);return f.page;},
      get:async target=>{assert.equal(target,11);return f.page;},
      remove:async target=>{f.removed.push(target);},
      sendMessage:async(target,message,options)=>{
        assert.equal(target,11);assert.deepEqual(options,{frameId:0});
        assert.equal(message.action,"resume-probe");return {ticket:message.ticket,verified:true};
      },
    },
  };
  f.ports=createChromeResumePorts(f.chrome,{origin,nativeHost:"org.sdsctl.browser_device"});return f;
}
test("native adapter selects fixed host and exact review/prepare/commit fields",async()=>{
  const f=portsFixture(), intent="d".repeat(64),approval={ticket:"c".repeat(64),revision:4,expires_at:500};
  f.reply={version:1,ok:true,approval};
  await f.ports.review();
  assert.deepEqual(await f.ports.prepare({intent,nativeRevision:3,serverGeneration:4}),approval);
  await f.ports.commit({intent,approval});
  assert.deepEqual(f.calls,[
    ["org.sdsctl.browser_device",{version:1,action:"review-resume"}],
    ["org.sdsctl.browser_device",{version:1,action:"prepare-resume",intent,revision:3,generation:4}],
    ["org.sdsctl.browser_device",{version:1,action:"commit-resume",intent,...approval}],
  ]);
});
for(const value of [null,{version:1,ok:false},{version:2,ok:true,approval:{}},
  {version:1,ok:true,approval:{},extra:"private"}]) {
  test("native adapter rejects malformed prepare wrapper "+JSON.stringify(value),async()=>{
    const f=portsFixture();f.reply=value;
    await assert.rejects(f.ports.prepare({intent:"x",nativeRevision:3,serverGeneration:4}),/prepare/);
  });
}
test("browser proof owns and closes only its inactive exact-origin probe",async()=>{
  const f=portsFixture();
  assert.equal(await f.ports.verifySession(),true);assert.equal(f.cookies,2);
  assert.deepEqual(f.calls,[{url:origin+"/device-display",active:false}]);assert.deepEqual(f.removed,[11]);
});
for(const change of [
  {secure:false},{httpOnly:false},{hostOnly:false},{path:"/device-display"},{sameSite:"lax"},
  {session:true},{name:"unrelated"},{expirationDate:0},
]) test("browser proof refuses unsafe cookie "+JSON.stringify(change),async()=>{
  const f=portsFixture();Object.assign(f.cookie,change);
  assert.equal(await f.ports.verifySession(),false);assert.deepEqual(f.calls,[]);assert.deepEqual(f.removed,[]);
});
for(const kind of ["cookie-replaced","page-navigated","incognito","get-failed"]) {
  test("browser proof rejects "+kind+" and closes its own tab",async()=>{
    const f=portsFixture(), send=f.chrome.tabs.sendMessage;
    f.chrome.tabs.sendMessage=async(...args)=>{
      const reply=await send(...args);
      if(kind==="cookie-replaced")f.cookie.value="replaced";
      if(kind==="page-navigated")f.page={...f.page,url:origin+"/"};
      return reply;
    };
    if(kind==="incognito")f.page.incognito=true;
    if(kind==="get-failed")f.chrome.tabs.get=async()=>{throw new Error("private");};
    assert.equal(await f.ports.verifySession(),false);assert.deepEqual(f.removed,[11]);
  });
}
test("newer document cannot claim a pending review while server proof is outstanding",async()=>{
  const f=workerFixture(), gate=deferred(), replies=[];
  f.controller.reviewResume=()=>gate.promise;
  assert.equal(f.handler({action:"resume-review"},f.sender,r=>replies.push(r)),true);
  f.sender={...f.sender,documentId:"next-document"};
  assert.deepEqual((await f.send({action:"resume-review"})).replies,[{mode:"resume_refused"}]);
  gate.resolve(reviewed);await settle();
  assert.equal((await f.send({action:"resume-confirm",ticket:replies[0].ticket})).waiting,false);
  assert.deepEqual(f.resumeCalls,[]);
});

test("stalled browser create expires at real twenty-second deadline and late tab is retired",{timeout:23000},async()=>{
  const f=portsFixture(), gate=deferred();
  f.chrome.tabs.create=()=>gate.promise;
  const started=performance.now();
  assert.equal(await f.ports.verifySession(),false);
  assert(performance.now()-started>=19000 && performance.now()-started<23000);
  assert.deepEqual(f.removed,[]);
  gate.resolve(f.page);await settle();
  assert.deepEqual(f.removed,[11]);
});
