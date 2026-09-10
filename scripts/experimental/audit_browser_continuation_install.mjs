/** Actual sandboxed Chromium storage/cookie I/O; MODELED native/protected-page
 * observations. No server, real sign-in, native host, credential or user profile.
 * The driver seeds a fictional clean pause. This is not consent/flow acceptance.
 */
import assert from 'node:assert/strict';
import {createHash,generateKeyPairSync} from 'node:crypto';
import {mkdtemp,mkdir,readFile,writeFile,copyFile,lstat,realpath} from 'node:fs/promises';
import path from 'node:path';
import {fileURLToPath,pathToFileURL} from 'node:url';

if(process.argv[2]==='--help') {
  console.log('Usage: node audit_browser_continuation_install.mjs STAGE PLAYWRIGHT CHROMIUM ASSETS [healthy|pending-late|cookie-late|accepted-late]');
  console.log('Four absolute paths. Actual sandboxed Chromium storage/cookies, modeled native and page observations.');
  console.log('Fictional clean-state seed only; no real sign-in, network request or production profile.');
  process.exit(0);
}
const [stage,playwright,executable,assets,scenario='healthy']=process.argv.slice(2);
assert([stage,playwright,executable,assets].every(v=>v&&path.isAbsolute(v)));
assert(['healthy','pending-late','cookie-late','accepted-late'].includes(scenario));
const info=await lstat(stage);
assert(info.isDirectory()&&!info.isSymbolicLink()&&info.uid===process.getuid()&&
  (info.mode&0o777)===0o700&&await realpath(stage)===stage);
const {chromium}=await import(pathToFileURL(playwright));
const root=await mkdtemp(path.join(stage,'install-')),extension=path.join(root,'extension');
for(const part of ['extension','config','data','cache','tmp'])await mkdir(path.join(root,part),{mode:0o700});
const put=(name,value)=>writeFile(path.join(root,name),value,{mode:0o600,flag:'wx'});
const digest=body=>createHash('sha256').update(body).digest('hex');
const modules={};
for(const name of ['browser_device_continuation_install.mjs','browser_device_continuation_state.mjs',
  'browser_device_continuation_cookie.mjs']) {
  const body=await readFile(path.join(assets,name));modules[name]=digest(body);
  await copyFile(path.join(assets,name),path.join(extension,name));
}
const key=generateKeyPairSync('rsa',{modulusLength:2048}).publicKey.export({type:'spki',format:'der'});
const id=[...digest(key).slice(0,32)].map(c=>String.fromCharCode(97+parseInt(c,16))).join('');
await put('extension/manifest.json',JSON.stringify({manifest_version:3,version:'0.0.1',
  name:'SDSCTL FICTIONAL CONTINUATION STORAGE',key:key.toString('base64'),
  permissions:['storage','cookies','alarms'],host_permissions:['https://continuation-install.invalid/*'],
  background:{service_worker:'worker.mjs',type:'module'}}));
await put('extension/worker.mjs',`
import {createContinuationInstallation} from './browser_device_continuation_install.mjs';
import {classifyContinuationStartup} from './browser_device_continuation_state.mjs';
const settings={identity:'a'.repeat(64),epoch:'b'.repeat(64),build:'c'.repeat(64),
  origin:'https://continuation-install.invalid'};
const review={identity:settings.identity,epoch:settings.epoch,mode:'paused',
  binding:{fingerprint:'d'.repeat(64),revision:4,generation:7}};
const active={...review,mode:'active',binding:{fingerprint:'e'.repeat(64),revision:6,generation:7}};
let current={...review,binding:{...review.binding,generation:null}};
globalThis.issues=0;globalThis.events=[];globalThis.held=false;globalThis.released=false;
const selected={tabId:123,documentId:'MODEL-probe-document',ticket:'f'.repeat(64)};
const scenario=${JSON.stringify(scenario)};
// These wrappers defer only ONE real browser API invocation in this fixture;
// they do not edit product code or fabricate its eventual Chrome readback.
const invoke=async(name,operation)=>{
  events.push(name);
  if((scenario==='pending-late'&&name==='initial_pending')||
     (scenario==='accepted-late'&&name==='accepted')||
     (scenario==='cookie-late'&&name==='cookie-set')) {
    held=true;await new Promise(resolve=>{globalThis.release=resolve;});
  }
  const result=await operation();
  if(held){released=true;}return result;
};
const scoped=Object.create(chrome),storage=Object.create(chrome.storage),local=Object.create(chrome.storage.local),
  cookies=Object.create(chrome.cookies);
// Chrome extension API methods may validate their receiver. Fault-injection
// proxies forward reads/access restrictions to the actual API object explicitly.
Object.defineProperty(local,'get',{value:query=>chrome.storage.local.get(query)});
Object.defineProperty(local,'setAccessLevel',{value:options=>chrome.storage.local.setAccessLevel(options)});
Object.defineProperty(cookies,'get',{value:query=>chrome.cookies.get(query)});
Object.defineProperty(local,'set',{value:values=>invoke(values.sdsctlDeviceRecovery.phase,
  ()=>chrome.storage.local.set(values))});
Object.defineProperty(storage,'local',{value:local});
Object.defineProperty(scoped,'storage',{value:storage});
Object.defineProperty(cookies,'set',{value:details=>invoke('cookie-set',()=>chrome.cookies.set(details))});
Object.defineProperty(scoped,'cookies',{value:cookies});
globalThis.owner=createContinuationInstallation(scenario==='healthy'?chrome:scoped,settings,review,{
  readCurrent:async()=>structuredClone(current),
  issueInitial:async()=>{issues++;events.push('MODELED-issue');current=structuredClone(active);
    return {binding:active.binding,session:{token:'sdsctl-browser-session-v1.'+'1'.repeat(64),expires_in:300}};},
  createProbe:()=>({open:async()=>selected,verify:async()=>({url:settings.origin+'/device-display',...selected,
    displayOnly:true,deviceEnrolled:true,remainingSeconds:300}),close:()=>{}}),
});
globalThis.run=()=>{globalThis.result=null;globalThis.done=false;
  void owner.run().then(value=>{result=value;done=true;},error=>{
    result={refused:error.message==='Browser continuation installation is unconfirmed; retain saved state.'};done=true;
  });};
globalThis.snapshot=async()=>{
  const values=await chrome.storage.local.get(null),cookie=await chrome.cookies.get({
    url:settings.origin+'/',name:'__Host-sdsctl-device-session'});
  return {issues,events,held,released,done:globalThis.done??false,result:globalThis.result??null,
    keys:Object.keys(values),phase:values.sdsctlDeviceRecovery?.phase,
    cookiePresent:cookie!==null,alarmCount:(await chrome.alarms.getAll()).length,
    restartClass:classifyContinuationStartup(settings,values.sdsctlDeviceRecovery,active),
    bearerInStorage:JSON.stringify(values).includes('sdsctl-browser-session-v1.')};
};
`);
let context;
const launch=()=>chromium.launchPersistentContext(path.join(root,'profile'),{
  executablePath:executable,chromiumSandbox:true,headless:true,timeout:15000,
  env:{...process.env,XDG_CONFIG_HOME:path.join(root,'config'),XDG_DATA_HOME:path.join(root,'data'),
    XDG_CACHE_HOME:path.join(root,'cache'),TMPDIR:path.join(root,'tmp')},
  ignoreDefaultArgs:['--disable-extensions'],
  args:[`--load-extension=${extension}`,`--disable-extensions-except=${extension}`],
});
const until=async(fn)=>{
  const deadline=Date.now()+10000;
  while(Date.now()<deadline){if(await fn())return;await new Promise(r=>setTimeout(r,50));}
  throw Error('Fictional browser-I/O condition not observed');
};
const worker=async()=>context.serviceWorkers()[0]||context.waitForEvent('serviceworker',{timeout:10000});
try {
  context=await launch();let w=await worker();
  assert.equal(w.url(),`chrome-extension://${id}/worker.mjs`);
  await w.evaluate(()=>chrome.storage.local.set({sdsctlDeviceRecovery:{
    version:1,identity:'a'.repeat(64),paused:true,phase:'clean',nextAt:0}}));
  await w.evaluate(()=>run());
  if(scenario!=='healthy') {
    await until(()=>w.evaluate(()=>held));await w.evaluate(()=>owner.invalidate());
    await until(()=>w.evaluate(()=>done));
    assert.deepEqual(await w.evaluate(()=>result),{refused:true});
    await w.evaluate(()=>release());await until(()=>w.evaluate(()=>released));
  } else await until(()=>w.evaluate(()=>done));
  const observed=await w.evaluate(()=>snapshot());
  // Preserve the redacted actual observation even when a later assertion fails.
  // A failed fixture is never reset/replayed to obtain a passing result.
  await put('observed.json',JSON.stringify(observed,null,2)+'\n');
  console.log(JSON.stringify({scenario,fixture:root,observed}));
  assert.equal(observed.bearerInStorage,false);assert.equal(observed.alarmCount,0);
  assert.deepEqual(observed.keys,['sdsctlDeviceRecovery']);
  assert.equal(observed.issues,scenario==='pending-late'?0:1);
  assert.equal(observed.cookiePresent,scenario!=='pending-late');
  assert.equal(observed.phase,['healthy','accepted-late'].includes(scenario)?'accepted':'initial_pending');
  assert.equal(observed.restartClass.sessionReady,false);
  assert.deepEqual(observed.result,scenario==='healthy'?{mode:'accepted',sessionReady:true}:{refused:true});
  await context.close();context=await launch();w=await worker();
  const restart=await w.evaluate(()=>snapshot());
  assert.equal(restart.phase,observed.phase);assert.equal(restart.cookiePresent,observed.cookiePresent);
  assert.equal(restart.issues,0);await w.evaluate(()=>run());await until(()=>w.evaluate(()=>done));
  const retried=await w.evaluate(()=>snapshot());
  assert.deepEqual(retried.result,{refused:true});assert.equal(retried.issues,0);
  assert.deepEqual(retried.events,[]);assert.equal(retried.phase,observed.phase);
  assert.equal(retried.cookiePresent,observed.cookiePresent);
  const report={scenario,fixture:root,browser:await context.browser().version(),
    harness:digest(await readFile(fileURLToPath(import.meta.url))),modules,
    realStorageCookieIO:true,modeledNativeAndPage:true,driverSeededCleanPause:true,
    noRealSignIn:true,noNativeHost:true,observed,restartInitialRefused:true,
    noAutomaticRetry:true,noStateRepair:true};
  await put('result.json',JSON.stringify(report,null,2)+'\n');console.log(JSON.stringify(report));
} finally {await context?.close();}
