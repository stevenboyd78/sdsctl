import test from 'node:test';
import assert from 'node:assert/strict';
import {createHash} from 'node:crypto';
import {fingerprintContinuationCookie} from '../../src/sds200/browser_assets/browser_device_continuation_cookie.mjs';
import {cookieMatchesContinuationRecord,pausedContinuationRecord,classifyContinuationStartup,createInitialInstallation}
  from '../../src/sds200/browser_assets/browser_device_continuation_state.mjs';

const token='sdsctl-browser-session-v1.'+'1'.repeat(64),origin='https://192.0.2.18:8443';
const cookie=(url=origin)=>({name:'__Host-sdsctl-device-session',value:token,
  domain:new URL(url).hostname,hostOnly:true,path:'/',secure:true,httpOnly:true,
  sameSite:'strict',session:false,expirationDate:1300,storeId:'0'});
const config=url=>({origin:url,identity:'a'.repeat(64),epoch:'b'.repeat(64),build:'c'.repeat(64)});
const refusal={message:'Browser continuation cookie identity is unconfirmed.'};
const oracle=(url,c)=>createHash('sha256').update(JSON.stringify([
  'sdsctl-continuation-cookie-v1',url,c.name,c.value,c.domain,c.hostOnly,c.path,c.secure,
  c.httpOnly,c.sameSite,c.session,c.expirationDate,c.storeId,
])).digest('hex');

for(const url of ['https://display.example',origin,'https://[::1]:8443'])
test('actual Web Crypto binds the precise snapshot without creating authority '+url,async()=>{
  const c=cookie(url),saved=structuredClone(c),digest=await fingerprintContinuationCookie(url,c);
  assert.equal(digest,oracle(url,c));assert.match(digest,/^[a-f0-9]{64}$/);
  assert.deepEqual(c,saved);assert(!digest.includes(token));
  const settings=config(url),before={identity:settings.identity,epoch:settings.epoch,mode:'paused',
    binding:{fingerprint:'d'.repeat(64),revision:4,generation:7}};
  const after={...before,mode:'active',binding:{...before.binding,fingerprint:'e'.repeat(64),revision:6}};
  const initial=createInitialInstallation(settings,pausedContinuationRecord(settings),before,
    {wall:()=>1000000,monotonic:()=>500000});
  const pending=initial.pendingRecord('f'.repeat(64));initial.pendingSaved(pending);
  initial.sessionReturned({binding:after.binding,session:{token,expires_in:300}},after);
  initial.cookieInstalled(c);
  const probe={tabId:12,documentId:'document-1',ticket:'2'.repeat(64)};
  initial.probeStarted(probe);
  const accepted=initial.protectedPageVerified({...probe,url:url+'/device-display',
    displayOnly:true,deviceEnrolled:true,remainingSeconds:300},c,after,digest);
  assert.equal(accepted.cookieFingerprint,digest);assert(!JSON.stringify(accepted).includes(token));
  initial.acceptedSaved(accepted,after,c,await fingerprintContinuationCookie(url,c));
  assert.equal(await cookieMatchesContinuationRecord(settings,accepted,c),true);
  assert.deepEqual(classifyContinuationStartup(settings,accepted,after),
    {mode:'verification_required',sessionReady:false});
  for(const replaced of [null,{...c,value:'sdsctl-browser-session-v1.'+'9'.repeat(64)},
    {...c,expirationDate:c.expirationDate-1},{...c,expirationDate:c.expirationDate+1},
    {...c,storeId:'1'},{...c,sameSite:'lax'}])
    assert.equal(await cookieMatchesContinuationRecord(settings,accepted,replaced),false);
  for(const record of [pending,pausedContinuationRecord(settings),
    {...accepted,cookieFingerprint:null},{...accepted,cookieFingerprint:'9'.repeat(64)}])
    assert.equal(await cookieMatchesContinuationRecord(settings,record,c),false);
  assert.deepEqual(c,saved); // No cookie/state removal, replacement or repair.
});
for(const [index,change] of [c=>null,c=>[],c=>({...c,extra:true}),c=>({...c,name:'other'}),
  c=>({...c,value:'PRIVATE'}),c=>({...c,domain:'other.example'}),c=>({...c,hostOnly:false}),
  c=>({...c,path:'/device-display'}),c=>({...c,secure:false}),c=>({...c,httpOnly:false}),
  c=>({...c,sameSite:'lax'}),c=>({...c,session:true}),c=>({...c,storeId:'1'}),
  c=>({...c,partitionKey:{topLevelSite:origin}}),
  ...[undefined,null,0,-1,NaN,Infinity,true,'1300',Number.MAX_SAFE_INTEGER].map(value=>
    c=>({...c,expirationDate:value}))].entries())
test('invalid snapshot is refused with a redacted error '+index,async()=>{
  await assert.rejects(fingerprintContinuationCookie(origin,change(cookie())),refusal);
});
for(const url of [null,'http://192.0.2.18','https://user:PRIVATE@example.com',
  'https://DISPLAY.example','https://display.example/','https://display.example/path',
  'https://display.example?x','https://display.example#x','https://display.example\n'])
test('invalid fixed origin is not a cookie selector '+url,async()=>{
  await assert.rejects(fingerprintContinuationCookie(url,cookie()),refusal);
});
test('origin port is included even though browser cookie scope does not contain it',async()=>{
  const c=cookie();
  assert.notEqual(await fingerprintContinuationCookie(origin,c),
    await fingerprintContinuationCookie('https://192.0.2.18:9443',c));
});
test('bytes are captured before asynchronous hashing; caller must re-read the browser',async()=>{
  const c=cookie(),expected=oracle(origin,c),pending=fingerprintContinuationCookie(origin,c);
  c.value='sdsctl-browser-session-v1.'+'9'.repeat(64);
  assert.equal(await pending,expected);
  assert.notEqual(await fingerprintContinuationCookie(origin,c),expected);
});
test('a failed cryptographic provider cannot expose its private error',async()=>{
  const original=Object.getOwnPropertyDescriptor(globalThis,'crypto');
  try {
    Object.defineProperty(globalThis,'crypto',{configurable:true,value:{subtle:{
      digest:async()=>{throw Error(token);},
    }}});
    await assert.rejects(fingerprintContinuationCookie(origin,cookie()),refusal);
  } finally {Object.defineProperty(globalThis,'crypto',original);}
});
