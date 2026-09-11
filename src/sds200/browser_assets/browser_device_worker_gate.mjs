// Synchronous, inert MV3 wake-up receivers. No authority/state selection here.
// Native validation must finish before any role-specific handler is invoked.
const LIMIT=64, DEADLINE=12000;
const refused=()=>({mode:'setup_error'});
const boundedString=(v,n)=>typeof v==='string'&&v.length<=n;
const pages=new Set(['setup.html','startup.html','control.html','resume.html','recovery.html']);
const exact=(v,keys)=>v!==null&&typeof v==='object'&&!Array.isArray(v)&&
  Object.keys(v).sort().join(',')===[...keys].sort().join(',');

function probeSnapshot(message,sender,id,origin) {
  if(origin===null||sender?.id!==id||sender.frameId!==0||
    sender.documentLifecycle!=='active'||sender.origin!==origin||
    sender.url!==origin+'/device-display'||typeof sender.documentId!=='string'||
    !/^[a-zA-Z0-9-]{1,128}$/.test(sender.documentId)||
    !Number.isSafeInteger(sender.tab?.id)||sender.tab.id<0||sender.tab.incognito!==false||
    typeof message?.ticket!=='string'||!/^[a-f0-9]{64}$/.test(message.ticket))return null;
  const selected=message.action==='continuation-probe-selected'&&exact(message,['action','ticket']);
  const result=message.action==='continuation-probe-result'&&
    exact(message,['action','ticket','displayOnly','deviceEnrolled','remainingSeconds'])&&
    message.displayOnly===true&&message.deviceEnrolled===true&&
    typeof message.remainingSeconds==='number'&&Number.isFinite(message.remainingSeconds)&&
    message.remainingSeconds>30&&message.remainingSeconds<=3600;
  if(!selected&&!result)return null;
  return [{...message},{id,url:sender.url,origin,frameId:0,documentLifecycle:'active',
    documentId:sender.documentId,tab:{id:sender.tab.id,incognito:false}}];
}

function messageSnapshot(message,sender,id) {
  if(!message||typeof message!=='object'||Array.isArray(message)||
    !boundedString(message.action,64)||Object.keys(message).length>4||
    Object.entries(message).some(([key,value])=>
      !['action','ticket','binding','outcome'].includes(key)||!boundedString(value,128))||
    sender?.id!==id||sender.frameId!==0||sender.documentLifecycle!=='active'||
    typeof sender.documentId!=='string'||!/^[a-zA-Z0-9-]{1,128}$/.test(sender.documentId)||
    !Number.isSafeInteger(sender.tab?.id)||sender.tab.id<0||sender.tab.incognito!==false||
    !boundedString(sender.url,2048))return null;
  const prefix=`chrome-extension://${id}/`;
  if(!pages.has(sender.url.slice(prefix.length))||!sender.url.startsWith(prefix)) {
    // Only potential logout content messages. The validated controller still
    // requires its EXACT configured server origin, action schema and document.
    if(!['logout-begin','logout-finish'].includes(message.action)||
      !boundedString(sender.origin,2048)||!sender.origin.startsWith('https://'))return null;
    try {if(new URL(sender.origin).origin!==sender.origin||
      !['/','/device-display'].some(p=>sender.url===sender.origin+p))return null;}
    catch {return null;}
  }
  return [{...message},{id:sender.id,url:sender.url,origin:sender.origin,
    frameId:0,documentLifecycle:'active',documentId:sender.documentId,
    tab:{id:sender.tab.id,incognito:false}}];
}

export function createWorkerEventGate(chrome,clock=()=>performance.now(),
  schedule=setTimeout,cancel=clearTimeout) {
  const id=chrome.runtime.id;
  if(typeof id!=='string'||!/^[a-p]{32}$/.test(id))throw Error('Worker context refused');
  const started=clock(), handlers=new Map();
  let state='pending', queue=[], timer, rejectDeadline, probeOrigin=null,consentPrepared=false;
  const deadline=new Promise((_,reject)=>{rejectDeadline=reject;});
  deadline.catch(()=>{});
  const reply=(respond,value)=>{try {respond(value);} catch {/* Closed document: never replay. */}};
  const fail=()=>{
    if(state==='failed')return;
    state='failed';cancel(timer);
    const held=queue;queue=[];
    for(const item of held)if(item.respond)reply(item.respond,refused());
    handlers.clear();rejectDeadline(Error('Worker context refused'));
  };
  const check=()=>{
    const now=clock();
    if(state!=='pending'||!Number.isFinite(started)||!Number.isFinite(now)||
      now<started||now-started>=DEADLINE) {fail();throw Error('Worker context refused');}
  };
  const dispatch=item=>{
    let answered=false;
    const respond=value=>{if(!answered){answered=true;reply(item.respond,value);}};
    try {
      for(const listener of handlers.get(item.name)||[]) {
        const held=listener(...item.args,...(item.respond?[respond]:[]));
        if(item.respond&&(held===true||answered))return held===true;
      }
    } catch {if(item.respond)respond(refused());}
    if(item.respond&&!answered)respond(refused());
    return false;
  };
  const receive=item=>{
    if(state==='active')return dispatch(item);
    if(state==='pending') {
      try {check();if(queue.length>=LIMIT)throw Error('Worker queue full');}
      catch {fail();if(item.respond)reply(item.respond,refused());return false;}
      queue.push(item);return Boolean(item.respond);
    }
    if(item.respond)reply(item.respond,refused());
    return false;
  };
  const collect=name=>Object.freeze({addListener:listener=>{
    if(state!=='pending'||typeof listener!=='function')throw Error('Worker listener refused');
    const list=handlers.get(name)||[];
    if(list.length>=8)throw Error('Worker listener limit');
    list.push(listener);handlers.set(name,list);
  }});
  // A private, single-listener delegate for a later owned probe. The ACTUAL
  // Chrome listener remains synchronous and fixed; no late MV3 registration.
  // Selection is a trusted native-context caller's responsibility, not a page
  // message, proof or storage value. Ordinary role handlers cannot see this lane.
  const probeEvent=name=>Object.freeze({addListener:listener=>{
    if(state==='failed'||typeof listener!=='function')throw Error('Worker listener refused');
    const list=handlers.get(name)||[];
    if(list.includes(listener))return;
    if(list.length)throw Error('Worker probe already owned');
    handlers.set(name,[listener]);
  },removeListener:listener=>{
    const list=handlers.get(name)||[];
    handlers.set(name,list.filter(value=>value!==listener));
  }});
  const prepareContinuationProbe=origin=>{
    check();
    if(probeOrigin!==null||typeof origin!=='string'||!/^[\x21-\x7e]{1,2048}$/.test(origin))
      throw Error('Worker probe context refused');
    let value;
    try {value=new URL(origin);} catch {throw Error('Worker probe context refused');}
    if(value.protocol!=='https:'||value.origin!==origin||value.username||value.password||
      value.pathname!=='/'||value.search||value.hash)throw Error('Worker probe context refused');
    probeOrigin=origin;
    const runtime=Object.create(chrome.runtime),tabs=Object.create(chrome.tabs),
      selected=Object.create(chrome);
    Object.defineProperty(runtime,'onMessage',{value:probeEvent('probe-message')});
    Object.defineProperty(tabs,'onUpdated',{value:probeEvent('probe-tab')});
    Object.defineProperties(selected,{runtime:{value:runtime},tabs:{value:tabs}});
    return selected;
  };
  const runtime=Object.create(chrome.runtime), alarms=Object.create(chrome.alarms),
    tabs=Object.create(chrome.tabs), scoped=Object.create(chrome);
  for(const [owner,key,name] of [[runtime,'onMessage','message'],[runtime,'onStartup','startup'],
    [runtime,'onInstalled','installed'],[alarms,'onAlarm','alarm'],[tabs,'onUpdated','tab']])
    Object.defineProperty(owner,key,{value:collect(name)});
  Object.defineProperties(scoped,{runtime:{value:runtime},alarms:{value:alarms},tabs:{value:tabs}});
  const prepareContinuationConsent=()=>{
    check();if(consentPrepared)throw Error('Worker consent already selected');
    consentPrepared=true;
    const consentTabs=Object.create(tabs),selected=Object.create(scoped);
    Object.defineProperty(consentTabs,'onUpdated',{value:collect('consent-tab')});
    Object.defineProperty(selected,'tabs',{value:consentTabs});
    return selected;
  };
  // All actual Chrome listeners are installed in this synchronous call, before
  // the first native promise. Receivers only snapshot bounded eligible events.
  try {
    chrome.runtime.onMessage.addListener((message,sender,respond)=>{
      const probe=probeSnapshot(message,sender,id,probeOrigin);
      if(probe)return receive({name:'probe-message',args:probe,respond});
      const args=messageSnapshot(message,sender,id);
      return args?receive({name:'message',args,respond}):false;
    });
    chrome.runtime.onStartup.addListener(()=>receive({name:'startup',args:[]}));
    chrome.runtime.onInstalled.addListener(()=>receive({name:'installed',args:[]}));
    chrome.alarms.onAlarm.addListener(alarm=>{
      if(alarm?.name==='sdsctl-device-recovery')receive({name:'alarm',args:[{name:alarm.name}]});
    });
    chrome.tabs.onUpdated.addListener((tabId,change,tab)=>{
      if(consentPrepared&&Number.isSafeInteger(tabId)&&tabId>=0&&change&&
        (change.status==='loading'||Object.hasOwn(change,'url'))) {
        // Cancellation needs a navigation signal, never a destination URL.
        receive({name:'consent-tab',args:[tabId,{navigating:true}]});
      }
      if(probeOrigin!==null&&Number.isSafeInteger(tabId)&&tabId>=0&&change&&
        (['loading','complete'].includes(change.status)||Object.hasOwn(change,'url'))) {
        // Navigation away matters even when its destination is not our origin.
        // Preserve only the URL-change signal, never another tab's actual URL.
        const snapshot={};
        if(['loading','complete'].includes(change.status))snapshot.status=change.status;
        if(Object.hasOwn(change,'url'))snapshot.url='';
        receive({name:'probe-tab',args:[tabId,snapshot]});
      }
      if(Number.isSafeInteger(tabId)&&tabId>=0&&tab?.id===tabId&&tab.incognito===false&&
        change?.status==='complete'&&tab.status==='complete'&&!tab.pendingUrl&&
        ['setup.html','startup.html'].some(page=>tab.url===`chrome-extension://${id}/${page}`))
        receive({name:'tab',args:[tabId,{status:'complete'},
          {id:tabId,incognito:false,status:'complete',url:tab.url}]});
    });
    timer=schedule(fail,DEADLINE);
  } catch {fail();throw Error('Worker receiver unavailable');}
  return Object.freeze({chrome:scoped,deadline,check,fail,prepareContinuationProbe,
    prepareContinuationConsent,open:()=>{
    // The caller checked the deadline immediately before synchronous role
    // construction. Do not invalidate already-constructed controllers mid-turn.
    if(state!=='pending')throw Error('Worker context refused');
    cancel(timer);state='active';
    const held=queue;queue=[];
    // Replay in the same turn as controller construction. A queued logout sets
    // its synchronous pause intent before the startup tick's microtasks run.
    for(const item of held)dispatch(item);
  }});
}
