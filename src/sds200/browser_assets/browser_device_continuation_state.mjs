// Experimental browser acceptance core. Inert: no listeners, native dispatch,
// storage, cookie, network or timer side effects. Trusted adapters must own those
// operations and the worker queue; supplied observations are NOT authority.
const exact=(v,keys)=>v!==null&&typeof v==='object'&&!Array.isArray(v)&&
  Object.keys(v).sort().join(',')===[...keys].sort().join(',');
const hex=v=>typeof v==='string'&&/^[a-f0-9]{64}$/.test(v);
const integer=v=>Number.isSafeInteger(v)&&v>0&&v<Number.MAX_SAFE_INTEGER;
const finite=v=>typeof v==='number'&&Number.isFinite(v)&&v>=0&&v<Number.MAX_SAFE_INTEGER;
const copy=v=>Object.freeze({...v,...(v.binding?{binding:Object.freeze({...v.binding})}:{})});
const same=(a,b)=>exact(a,Object.keys(b))&&Object.keys(b).every(k=>
  k==='binding'&&b.binding!==null?same(a[k],b[k]):a[k]===b[k]);
const refuse=()=>{throw Error('Browser continuation is unconfirmed; retain saved state.');};

function selection(value) {
  if(!exact(value,['identity','epoch','build','origin'])||
    ![value.identity,value.epoch,value.build].every(hex)||typeof value.origin!=='string'||
    !/^[\x21-\x7e]{1,2048}$/.test(value.origin))refuse();
  const url=new URL(value.origin);
  if(url.protocol!=='https:'||url.origin!==value.origin||url.username||url.password||
    url.pathname!=='/'||url.search||url.hash)refuse();
  return Object.freeze({...value});
}
function binding(value) {
  if(!exact(value,['fingerprint','revision','generation'])||!hex(value.fingerprint)||
    !integer(value.revision)||!integer(value.generation))refuse();
  return Object.freeze({...value});
}
function native(value,config,mode) {
  if(!exact(value,['identity','epoch','mode','binding'])||value.identity!==config.identity||
    value.epoch!==config.epoch||value.mode!==mode)refuse();
  return binding(value.binding);
}
function record(value,config) {
  if(!exact(value,['version','identity','epoch','build','phase','binding','intent'])||
    value.version!==3||value.identity!==config.identity||value.epoch!==config.epoch||
    value.build!==config.build||!['paused','initial_pending','accepted'].includes(value.phase))refuse();
  if(value.phase==='paused') {
    if(value.binding!==null||value.intent!==null)refuse();
  } else {
    binding(value.binding);
    if(!hex(value.intent))refuse();
  }
  return copy(value);
}

// Candidate bytes only. A future owned activation adapter must actually observe
// the clean paused browser/native selection before persisting this schema.
export function pausedContinuationRecord(settings) {
  try {
  const config=selection(settings);
  return copy({version:3,identity:config.identity,epoch:config.epoch,build:config.build,
    phase:'paused',binding:null,intent:null});
  } catch {refuse();}
}

// Read-only classification, NEVER a login/renewal permission or session proof.
// The trusted worker must load the real record, obtain a fresh owned native
// observation and independently verify current server authority and browser use.
export function classifyContinuationStartup(settings,saved,observed) {
  try {
    const config=selection(settings),state=record(saved,config);
    if(state.phase==='initial_pending')refuse();
    const current=native(observed,config,state.phase==='paused'?'paused':'active');
    if(state.phase==='accepted'&&!same(current,state.binding))refuse();
    return Object.freeze({mode:state.phase==='paused'?'paused':'verification_required',sessionReady:false});
  } catch {
    return Object.freeze({mode:'administrator_required',sessionReady:false});
  }
}

export function createInitialInstallation(settings,saved,reviewed,clocks) {
  try {return initialInstallation(settings,saved,reviewed,clocks);}
  catch {refuse();}
}

function initialInstallation(settings,saved,reviewed,clocks) {
  const config=selection(settings),before=record(saved,config);
  if(before.phase!=='paused')refuse();
  const expected=native(reviewed,config,'paused');
  if(!integer(expected.revision+3))refuse();
  // Construct only AFTER fresh document-bound consent, never before human review.
  // These elapsed checks are not a process-kill deadline. The owning adapters
  // must bound browser calls and retain the independent native supervisor.
  const sample=()=>{
    const pair=[clocks.wall(),clocks.monotonic()];
    if(!pair.every(finite))refuse();
    return pair;
  };
  const started=sample();let last=started,phase='new',pending=null,accepted=null;
  let issued=null,expiry=0,cookieExpiry=null,probe=null;
  const check=()=>{
    const pair=sample();
    if(pair.some((n,i)=>n<last[i]||n-started[i]>=45000))refuse();
    last=pair;
  };
  const remaining=()=>{
    check();
    if(!issued)refuse();
    const elapsed=Math.max(...last.map((n,i)=>n-started[i]));
    const lifetime=cookieExpiry===null?issued.expires_in*1000:
      Math.min(issued.expires_in*1000,cookieExpiry*1000-started[0]);
    if(lifetime-elapsed<=30000)refuse();
  };
  const step=(required,next,run)=>{
    try {
      if(phase!==required)refuse();
      check();const result=run();check();
      if(issued)remaining();
      phase=next;
      if(next==='accepted')issued=null; // No token getter or reconstructed handoff.
      return result;
    } catch {
      phase='unconfirmed';issued=null;refuse();
    }
  };
  const cookie=value=>{
    remaining();
    if(!exact(value,['name','value','domain','hostOnly','path','secure','httpOnly',
      'sameSite','session','expirationDate','storeId'])||
      value.name!=='__Host-sdsctl-device-session'||value.value!==issued.token||
      value.domain!==new URL(config.origin).hostname||value.hostOnly!==true||value.path!=='/'||
      value.secure!==true||value.httpOnly!==true||value.sameSite!=='strict'||value.session!==false||
      value.storeId!=='0'||!finite(value.expirationDate)||value.expirationDate>expiry/1000||
      value.expirationDate*1000-started[0]-Math.max(...last.map((n,i)=>n-started[i]))<=30000||
      (cookieExpiry!==null&&value.expirationDate!==cookieExpiry))refuse();
    cookieExpiry=value.expirationDate;
  };
  const unchanged=value=>{
    if(!accepted||!same(native(value,config,'active'),accepted.binding))refuse();
  };
  return Object.freeze({
    pendingRecord:intent=>step('new','pending_write',()=>{
      if(!hex(intent))refuse();
      pending=copy({...before,phase:'initial_pending',binding:expected,intent});
      return copy(pending);
    }),
    pendingSaved:observed=>step('pending_write','issue',()=>{
      if(!same(record(observed,config),pending))refuse();
      // Comparison inputs only, NOT a serialized approval ticket. The one owned
      // native operation must freshly reconstruct consent, ownership and state.
      return copy({epoch:config.epoch,intent:pending.intent,binding:expected});
    }),
    sessionReturned:(result,observed)=>step('issue','cookie',()=>{
      if(!exact(result,['binding','session'])||!exact(result.session,['token','expires_in']))refuse();
      const after=binding(result.binding),session=result.session;
      if(after.revision!==expected.revision+3||after.generation!==expected.generation||
        after.fingerprint===expected.fingerprint||typeof session.token!=='string'||
        !/^sdsctl-browser-session-v1\.[a-f0-9]{64}$/.test(session.token)||
        !finite(session.expires_in)||session.expires_in<=30||session.expires_in>3600)refuse();
      accepted=copy({...pending,phase:'accepted',binding:after});
      unchanged(observed);
      issued=Object.freeze({...session});remaining();
      // Count the entire browser attempt conservatively, including time before
      // issuance. Native already deducts its own elapsed time as well.
      expiry=started[0]+issued.expires_in*1000;
      if(!finite(expiry))refuse();
      return Object.freeze({url:config.origin+'/',name:'__Host-sdsctl-device-session',
        value:issued.token,path:'/',secure:true,httpOnly:true,sameSite:'strict',
        expirationDate:expiry/1000,storeId:'0'});
    }),
    cookieInstalled:observed=>step('cookie','probe_selection',()=>{cookie(observed);}),
    probeStarted:observed=>step('probe_selection','probe',()=>{
      if(!exact(observed,['tabId','documentId','ticket'])||
        !Number.isSafeInteger(observed.tabId)||observed.tabId<0||
        typeof observed.documentId!=='string'||!/^[a-zA-Z0-9-]{1,128}$/.test(observed.documentId)||
        !hex(observed.ticket))refuse();
      probe=Object.freeze({...observed});
    }),
    protectedPageVerified:(proof,observedCookie,observedNative)=>step('probe','accepted_write',()=>{
      if(!exact(proof,['url','tabId','documentId','ticket','displayOnly','deviceEnrolled','remainingSeconds'])||
        proof.url!==config.origin+'/device-display'||proof.tabId!==probe.tabId||
        proof.documentId!==probe.documentId||proof.ticket!==probe.ticket||
        proof.displayOnly!==true||proof.deviceEnrolled!==true||!finite(proof.remainingSeconds)||
        proof.remainingSeconds<=30||proof.remainingSeconds>3600)refuse();
      // The real isolated top-frame adapter must bind this observation to its
      // exact probe/document and check the SAME cookie before and after access.
      // A supplied object passed to this inert core is not proof of a real page.
      cookie(observedCookie);unchanged(observedNative);remaining();
      return copy(accepted);
    }),
    acceptedSaved:(observed,observedNative,observedCookie)=>step('accepted_write','accepted',()=>{
      if(!same(record(observed,config),accepted))refuse();
      unchanged(observedNative);cookie(observedCookie);remaining();
      return Object.freeze({mode:'accepted',sessionReady:true});
    }),
    invalidate:()=>{phase='unconfirmed';issued=null;},
  });
}
