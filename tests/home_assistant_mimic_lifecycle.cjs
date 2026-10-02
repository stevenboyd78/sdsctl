// Real card code; synthetic HA context, DOM and clock. Never contacts a scanner.
const assert = require('node:assert/strict');
const vm = require('node:vm');
const input = JSON.parse(require('node:fs').readFileSync(0, 'utf8'));
const flush = async () => { for (let i=0;i<16;i++) await new Promise(setImmediate); };
class Element {
  constructor(tag) { this.tag=tag; this.children=[]; this.dataset={}; this.listeners={}; this.style={setProperty:(k,v)=>this.style[k]=v}; this.classList={add(){}}; }
  append(...items) { for (const item of items) { item.parentElement=this; this.children.push(item); } }
  replaceChildren(...items) { this.children=[]; this.append(...items); }
  setAttribute(key,value) { this[key]=value; }
  addEventListener(key,fn) { this.listeners[key]=fn; }
  attachShadow() { this.shadowRoot=new Element('shadow'); }
  dispatchEvent(event) { this.contexts ??= {}; this.contexts[event.context]=event.callback; }
}
function nodes(root) { return [root,...root.children.flatMap(nodes)]; }
function harness() {
  let now=0, next=0, sessions=0, calls=0, frontPanelCalls=0;
  const origin=input.origin??'https://ha.example.test';
  const timers=new Map(), definitions=new Map(), observers=[], cookies=[];
  const document={hidden:false, createElement:tag=>new Element(tag), addEventListener(){},removeEventListener(){}};
  Object.defineProperty(document,'cookie',{get:()=>cookies.at(-1)||'',set:value=>cookies.push(value)});
  const window={customCards:[],setTimeout:(fn,ms)=>{ timers.set(++next,{fn,at:now+ms});return next; },clearTimeout:id=>timers.delete(id),
    setInterval:(fn,ms)=>{ timers.set(++next,{fn,at:now+ms,repeat:ms});return next; },clearInterval:id=>timers.delete(id)};
  const panel=slug=>({component_name:'app',title:'sds200',config:{addon:slug}});
  const ui={panels:{scanner:panel('example_sds200')}};
  let ws = async request=>{
    if(request.endpoint==='/ingress/session'){ sessions++; return {session:'valid_session_example_1234'}; }
    if(request.endpoint==='/ingress/validate_session') return {};
    return {state:'started',ingress:true,ingress_url:'/api/hassio_ingress/example_key/'};
  };
  const api={callWS:request=>ws(request)};
  let frame=structuredClone(input.supplemental ? input.bundle : input.scenarios.held_trunk);
  for(const f of Object.values(frame.display.frames)){f.sequence=100;f.age_seconds=0;}
  if(input.supplemental)frame.supplemental.psi={sequence:100,age_seconds:0};
  const response=(payload=frame,status=200)=>new Response(JSON.stringify(payload),{status,headers:{'content-type':'application/json'}});
  const contextResponse=()=>({protocol:'sdsctl.supplemental-context',version:1,context:frame.supplemental.context});
  let renewed=false;
  const defaultRequest=async(url,options)=>{
    assert.equal(options.credentials,'same-origin');assert.equal(options.redirect,'error');assert.equal(options.cache,'no-store');
    if(url.endsWith('/api/v1/scanner/front-panel')){
      assert.equal(input.supplemental??false,false);
      assert.equal(url,origin+'/api/hassio_ingress/example_key/api/v1/scanner/front-panel');
      assert.equal(options.method,undefined);frontPanelCalls++;
      return response({protocol:'sdsctl.web',version:1,front_panel:structuredClone(input.front_panel)});
    }
    if(input.supplemental){
      assert.equal(options.headers['X-SDSCTL-Supplemental-Version'],'1');
      if(url.endsWith('/demand')){
        assert.equal(options.method,'POST');assert.equal(options.body,undefined);
        assert.deepEqual(JSON.parse(options.headers['X-SDSCTL-Supplemental-Context']),frame.supplemental.context);
        if(!renewed){renewed=true;frame.supplemental.context.context_revision++;}
        return response({protocol:'sdsctl.supplemental-demand',version:1,context:frame.supplemental.context,
          renewal_id:options.headers['X-SDSCTL-Supplemental-Renewal'],lease_seconds:5});
      }
      if(url.endsWith('/context')){
        assert.equal(url,origin+'/api/hassio_ingress/example_key/api/v1/display-supplemental/context');
        assert.equal(options.headers['X-SDSCTL-Supplemental-Context'],undefined);
        return response(contextResponse());
      }
      assert.equal(url,origin+'/api/hassio_ingress/example_key/api/v1/display-supplemental/frame');
      assert.deepEqual(JSON.parse(options.headers['X-SDSCTL-Supplemental-Context']),frame.supplemental.context);
    }else assert.equal(url,origin+'/api/hassio_ingress/example_key/api/v1/display-frame');
    return response();
  };
  let request=defaultRequest;
  const ctx=vm.createContext({window,document,location:{origin,protocol:new URL(origin).protocol},HTMLElement:Element,CustomEvent:class {},
    customElements:{get:tag=>definitions.get(tag),define:(tag,constructor)=>definitions.set(tag,constructor)},
    IntersectionObserver:class {constructor(callback){this.callback=callback;observers.push(this);}observe(){}disconnect(){}},
    performance:{now:()=>now},AbortController,TextDecoder,URL,crypto:require('node:crypto').webcrypto,
    fetch:(...args)=>{calls++;return request(...args);}});
  if(input.case==='aux_legacy_owner')vm.runInContext(`globalThis[Symbol.for("sdsctl.home-assistant.ingress.v1")] = Object.freeze({
    acquire:async()=>()=>{}, invalidate(){}, resolve:async()=>{throw Error("Old owner does not support the new route.");}, get _leases(){return 0;}
  });`,ctx);
  if(input.case==='acceptance_registration_first')vm.runInContext(input.ordinary,ctx);
  vm.runInContext(input.script,ctx);
  const Card=definitions.get(input.tag??'sds200-mimic-card');
  const owner=vm.runInContext('globalThis[Symbol.for("sdsctl.home-assistant.ingress.v1")]',ctx);
  const card=()=>{const c=input.tag?new Card():new Card({supplemental:input.supplemental??false,
    supplementalDemand:input.case.startsWith('demand_')});c.setConfig({});return c;};
  const start=async c=>{c.connectedCallback();c.contexts.hassApi(api);c.contexts.hassUi(ui,()=>{});observers.at(-1).callback([{isIntersecting:true}]);await flush();};
  const raw=c=>nodes(c.shadowRoot).filter(node=>node.dataset.valueStatus==='raw_source');
  return {Card,card,start,raw,ctx,owner,window,document,api,ui,panel,cookies,timers,response,contextResponse,defaultRequest,definitions,
    get calls(){return calls;},get frontPanelCalls(){return frontPanelCalls;},get sessions(){return sessions;},get frame(){return frame;},
    set ws(fn){ws=fn;},set request(fn){request=fn;},
    newer(){for(const f of Object.values(frame.display.frames))f.sequence++;if(input.supplemental)frame.supplemental.psi.sequence++;},
    scenario(name){frame=structuredClone(input.scenarios[name]);for(const f of Object.values(frame.display.frames)){if(f.sequence!==null){f.sequence=100;f.age_seconds=0;}}},
    loadWaterfall(){return vm.runInContext(`(()=>{${input.waterfall}\nreturn sds200WaterfallIngressSession;})();`,ctx);},
    async tick(duration){const end=now+duration;for(;;){const entry=[...timers.entries()].filter(([,v])=>v.at<=end).sort((a,b)=>a[1].at-b[1].at)[0];if(!entry)break;
      const [id,task]=entry;now=task.at;timers.delete(id);if(task.repeat)timers.set(id,{...task,at:now+task.repeat});task.fn();await flush();}now=end;await flush();},
  };
}
const clockShown=c=>nodes(c._surround).some(node=>node.textContent==='21:26');
const frontPanelButtons=c=>c._frontPanelGrid===null?[]:c._frontPanelGrid.children;
function assertFrontPanel(c,inventory){
  const buttons=frontPanelButtons(c);assert.equal(buttons.length,27);
  assert.deepEqual(buttons.map(button=>button.children[0].textContent),inventory.keys.map(entry=>entry.code));
  buttons.forEach((button,index)=>{
    const entry=inventory.keys[index];
    assert.equal(button.tag,'button');assert.equal(button.type,'button');assert.equal(button.disabled,true);
    assert.deepEqual(button.listeners,{});assert.equal(button.dataset.referenceStatus,entry.reference_status);
    assert.equal(button.dataset.controlStatus,entry.control_status);assert.equal(button.title,entry.unavailable_reason);
    assert.equal(button['aria-describedby'],'front-panel-status');
    assert.deepEqual(button.children.map(node=>node.textContent),[entry.code,entry.label,
      `reference: ${entry.reference_status}; control: ${entry.control_status}`,entry.context_note,entry.unavailable_reason]);
  });
  const qualified=inventory.keys.filter(entry=>entry.available).length;
  if(qualified===1){
    assert.ok(c._frontPanelStatus.textContent.includes('Menu is qualified in the operator Web dashboard'));
    assert.ok(c._frontPanelStatus.textContent.includes('Home Assistant card remains read-only'));
  }else assert.ok(c._frontPanelStatus.textContent.includes('all controls remain unavailable'));
  const unsupported=inventory.keys.filter(entry=>entry.control_status==='unsupported').length;
  if(unsupported===0)assert.ok(!c._frontPanelStatus.textContent.includes('unsupported for this model'));
  else assert.ok(c._frontPanelStatus.textContent.includes(`(${unsupported} unsupported for this model)`));
}
const cases={
  async front_panel(h){
    const c=h.card();await h.start(c);assert.ok(h.raw(c).length);assert.equal(h.frontPanelCalls,1);
    assertFrontPanel(c,input.front_panel);
    c._frontPanel.open=true;const requests=h.frontPanelCalls;await h.tick(1000);assert.equal(h.frontPanelCalls,requests);
    c.disconnectedCallback();assert.equal(frontPanelButtons(c).length,0);assert.equal(h.timers.size,0);
  },
  async front_panel_models(h){
    for(const [model,inventory] of Object.entries(input.front_panels)){
      let inventoryRequests=0;
      h.request=(url,options)=>{
        if(!url.endsWith('/api/v1/scanner/front-panel'))return h.defaultRequest(url,options);
        assert.equal(url,(input.origin??'https://ha.example.test')+'/api/hassio_ingress/example_key/api/v1/scanner/front-panel');
        assert.equal(options.credentials,'same-origin');assert.equal(options.redirect,'error');assert.equal(options.cache,'no-store');
        assert.equal(options.method,undefined);inventoryRequests++;
        return h.response({protocol:'sdsctl.web',version:1,front_panel:structuredClone(inventory)});
      };
      const c=h.card();await h.start(c);assert.ok(h.raw(c).length);assert.equal(inventoryRequests,1);
      assertFrontPanel(c,inventory);
      const buttons=frontPanelButtons(c);
      const volume=buttons.find(button=>button.children[0].textContent==='V');
      assert.equal(volume.children[1].textContent,model==='sds100'?'Backlight':'Volume-knob push');
      assert.equal(buttons.filter(button=>button.dataset.controlStatus==='unsupported').length,model==='sds100'?2:0);
      c.disconnectedCallback();assert.equal(frontPanelButtons(c).length,0);assert.equal(h.timers.size,0);
    }
  },
  async front_panel_invalid(h){
    const malformed=[];
    const reordered=structuredClone(input.front_panel);[reordered.keys[0],reordered.keys[1]]=[reordered.keys[1],reordered.keys[0]];malformed.push(reordered);
    const enabled=structuredClone(input.front_panel);enabled.controls_available=true;enabled.keys[0].available=true;malformed.push(enabled);
    const hostile=structuredClone(input.front_panel);hostile.keys[0].label='PRIVATE\u001b[31m';malformed.push(hostile);
    const missing=structuredClone(input.front_panel);missing.keys.pop();malformed.push(missing);
    for(const value of malformed){
      h.request=(url,options)=>url.endsWith('/api/v1/scanner/front-panel')
        ? h.response({protocol:'sdsctl.web',version:1,front_panel:value}) : h.defaultRequest(url,options);
      const c=h.card();await h.start(c);assert.ok(h.raw(c).length);assert.equal(frontPanelButtons(c).length,0);
      assert.equal(c._frontPanelStatus.textContent,'Front-panel inventory is unavailable; all controls remain disabled.');
      assert.ok(!c._frontPanelStatus.textContent.includes('PRIVATE'));c.disconnectedCallback();assert.equal(h.timers.size,0);
    }
  },
  async front_panel_lifecycle(h){
    let inventoryRequests=0;
    h.request=(url,options)=>{
      if(!url.endsWith('/api/v1/scanner/front-panel'))return h.defaultRequest(url,options);
      inventoryRequests++;
      return new Promise((resolve,reject)=>options.signal.addEventListener('abort',()=>reject(Error('PRIVATE timeout')), {once:true}));
    };
    const timed=h.card();await h.start(timed);assert.ok(h.raw(timed).length);await h.tick(2000);
    assert.ok(h.raw(timed).length);assert.equal(frontPanelButtons(timed).length,0);
    assert.equal(timed._frontPanelStatus.textContent,'Front-panel inventory is unavailable; all controls remain disabled.');
    assert.equal(inventoryRequests,1);timed.disconnectedCallback();assert.equal(h.timers.size,0);

    let finish;
    h.request=(url,options)=>url.endsWith('/api/v1/scanner/front-panel')
      ? new Promise(resolve=>{finish=()=>resolve(h.response({protocol:'sdsctl.web',version:1,front_panel:input.front_panel}));})
      : h.defaultRequest(url,options);
    const late=h.card();await h.start(late);assert.ok(h.raw(late).length);late.disconnectedCallback();finish();await flush();
    assert.equal(frontPanelButtons(late).length,0);
    assert.equal(late._frontPanelStatus.textContent,'Front-panel inventory is unavailable; all controls remain disabled.');
    assert.equal(h.timers.size,0);
  },
  async acceptance_registration(h){
    const first=input.case==='acceptance_registration_first';
    assert.ok(input.tag);assert.deepEqual([...h.definitions.keys()],first?['sds200-mimic-card',input.tag]:[input.tag]);
    assert.equal(h.window.customCards.length,first?1:0);assert.equal(h.calls,0);
    const c=h.card();assert.equal(c._supplemental,true);assert.equal(c._supplementalDemand,true);
    c.setConfig({type:`custom:${input.tag}`,layout:'detail',led_treatment:'border'});
    assert.throws(()=>c.setConfig({type:'custom:sds200-mimic-card'}));
    assert.throws(()=>c.setConfig({supplemental:false}));
    assert.throws(()=>c.setConfig({supplementalDemand:false}));
    // Both load orders share authentication, not card constructors or opt-in.
    const candidate=h.Card;vm.runInContext(input.ordinary,h.ctx);
    const ordinary=h.definitions.get('sds200-mimic-card');assert.notEqual(candidate,ordinary);
    assert.equal(new ordinary()._supplemental,false);assert.equal(new ordinary()._supplementalDemand,false);
    vm.runInContext(input.script,h.ctx);assert.equal(h.definitions.get(input.tag),candidate);
    assert.equal(h.window.customCards.length,1);assert.equal(h.window.customCards[0].type,'sds200-mimic-card');
    assert.equal(h.calls,0);assert.equal(h.owner._leases,0);assert.equal(h.timers.size,0);
    await h.start(c);assert.ok(clockShown(c));c.disconnectedCallback();assert.equal(h.owner._leases,0);
  },
  async acceptance_registration_first(h){await cases.acceptance_registration(h);},
  async demand_happy(h){
    const c=h.card();await h.start(c);assert.ok(clockShown(c));assert.equal(h.calls,3);
    await h.tick(250);assert.equal(h.calls,5);assert.equal(c._terminal,false);
    c.disconnectedCallback();const count=h.calls;await h.tick(10000);assert.equal(h.calls,count);assert.equal(h.owner._leases,0);
  },
  async demand_lost(h){
    h.request=(url,opts)=>{if(url.endsWith('/demand'))throw Error('PRIVATE');return h.defaultRequest(url,opts);};
    const c=h.card();await h.start(c);assert.equal(c._terminal,true);assert.equal(h.raw(c).length,0);
    const count=h.calls;await h.tick(10000);assert.equal(h.calls,count);assert.equal(h.owner._leases,0);c.disconnectedCallback();
  },
  async demand_bad(h){
    h.request=(url,opts)=>url.endsWith('/demand')?h.response({protocol:'bad'}):h.defaultRequest(url,opts);
    const c=h.card();await h.start(c);assert.equal(c._terminal,true);assert.equal(h.raw(c).length,0);c.disconnectedCallback();
  },
  async demand_timeout(h){
    let finish;h.request=(url,opts)=>url.endsWith('/demand')?new Promise(resolve=>{finish=()=>resolve(h.defaultRequest(url,opts));}):h.defaultRequest(url,opts);
    const c=h.card();await h.start(c);await h.tick(5000);assert.equal(c._terminal,true);assert.equal(h.owner._leases,0);
    finish();await flush();assert.equal(h.raw(c).length,0);const count=h.calls;await h.tick(10000);assert.equal(h.calls,count);c.disconnectedCallback();
  },
  async demand_hide(h){
    let finish;h.request=(url,opts)=>url.endsWith('/demand')?new Promise(resolve=>{finish=()=>resolve(h.defaultRequest(url,opts));}):h.defaultRequest(url,opts);
    const c=h.card();await h.start(c);h.document.hidden=true;c._reconcile();h.document.hidden=false;c._reconcile();
    assert.equal(c._terminal,true);finish();await flush();assert.equal(h.raw(c).length,0);assert.equal(h.owner._leases,0);c.disconnectedCallback();
  },
  async demand_reject(h){
    let rejected=false;h.request=(url,opts)=>{if(url.endsWith('/demand')&&!rejected){rejected=true;return h.response({},409);}return h.defaultRequest(url,opts);};
    const c=h.card();await h.start(c);assert.equal(h.raw(c).length,0);assert.equal(c._terminal,false);
    await h.tick(2000);assert.ok(clockShown(c));c.disconnectedCallback();
  },
  async demand_hidden_return(h){
    let finish;h.request=(url,opts)=>url.endsWith('/demand')?new Promise(resolve=>{finish=()=>resolve(h.defaultRequest(url,opts));}):h.defaultRequest(url,opts);
    const c=h.card();await h.start(c);h.document.hidden=true;finish();await flush();assert.equal(c._terminal,true);
    h.document.hidden=false;c._reconcile();const count=h.calls;await h.tick(10000);assert.equal(h.calls,count);assert.equal(h.raw(c).length,0);c.disconnectedCallback();
  },
  async demand_siblings(h){
    const a=h.card(),b=h.card();await h.start(a);await h.start(b);assert.equal(h.owner._leases,2);
    let fail=true;h.request=(url,opts)=>{if(url.endsWith('/demand')&&fail){fail=false;throw Error('PRIVATE');}return h.defaultRequest(url,opts);};
    await h.tick(250);assert.equal([a,b].filter(c=>c._terminal).length,1);assert.equal(h.owner._leases,1);
    const survivor=[a,b].find(c=>!c._terminal);assert.ok(clockShown(survivor));h.newer();await h.tick(250);assert.ok(clockShown(survivor));
    a.disconnectedCallback();b.disconnectedCallback();assert.equal(h.owner._leases,0);
  },
  async aux_default_off(h){
    const c=new h.Card();assert.equal(c._supplemental,false);assert.equal(c._auxGuard,null);
    assert.throws(()=>c.setConfig({supplemental:true}));assert.equal(h.calls,0);
    assert.equal(h.window.sdsctlSupplemental,undefined); // resource-private guard
    assert.throws(()=>new h.Card({supplemental:'true'}));
    assert.throws(()=>new h.Card({supplementalDemand:true}));
    assert.throws(()=>new h.Card({supplemental:true,supplementalDemand:1}));
    assert.throws(()=>c.setConfig({supplementalDemand:true}));
  },
  async aux_profile(h){
    const original=JSON.stringify(h.frame),c=h.card();await h.start(c);
    assert.ok(clockShown(c));assert.ok(c._favorites.textContent.includes('00:'));assert.ok(c._favorites.textContent.includes('99:'));
    assert.ok(c._auxNote.textContent.includes('not LCD F0/S0/D0'));
    assert.ok(nodes(c.shadowRoot).some(node=>node.tag==='style'&&node.textContent.includes('white-space:pre-wrap')));
    for(const layout of ['preferred','simple','detail']){c.setConfig({layout});assert.ok(clockShown(c));}
    assert.equal(JSON.stringify(h.frame),original);c.disconnectedCallback();assert.equal(h.timers.size,0);
  },
  async aux_expiry(h){
    h.frame.supplemental.clock.age_seconds=4;h.frame.supplemental.favorites.age_seconds=1;
    const c=h.card();await h.start(c);assert.ok(clockShown(c));
    await h.tick(1000);assert.equal(clockShown(c),false);assert.ok(c._favorites.textContent);
    assert.ok(h.raw(c).length);await h.tick(3000);assert.equal(c._favorites.textContent,'');assert.ok(h.raw(c).length);
    await h.tick(1000);assert.equal(h.raw(c).length,0);c.disconnectedCallback();assert.equal(h.timers.size,0);
  },
  async aux_duplicate(h){
    h.request=async(url,options)=>{if(url.endsWith('/frame'))h.newer();return h.defaultRequest(url,options);};
    const c=h.card();await h.start(c);await h.tick(5100);
    assert.ok(h.raw(c).length);assert.equal(clockShown(c),false);assert.equal(c._favorites.textContent,'');
    h.frame.supplemental.clock.sample_sequence++;h.frame.supplemental.favorites.sample_sequence++;
    await h.tick(250);assert.ok(clockShown(c));assert.ok(c._favorites.textContent);c.disconnectedCallback();
  },
  async aux_resume(h){
    const c=h.card();await h.start(c);const guard=c._auxGuard;
    h.document.hidden=true;c._visibility();assert.equal(h.raw(c).length,0);assert.equal(h.owner._leases,0);
    h.document.hidden=false;c._visibility();await flush();assert.equal(h.raw(c).length,0);assert.equal(c._auxGuard,guard);
    h.newer();await h.tick(2000);assert.ok(h.raw(c).length);assert.equal(clockShown(c),false);
    c.disconnectedCallback();assert.equal(h.timers.size,0);
  },
  async aux_context(h){
    const c=h.card(),requests=[];await h.start(c);const first=c._auxGuard;
    h.frame.supplemental.context.context_revision++;h.newer();
    h.request=async(url,options)=>{requests.push(url.endsWith('/context')?'context':'frame');
      if(url.endsWith('/frame')&&JSON.parse(options.headers['X-SDSCTL-Supplemental-Context']).context_revision!==h.frame.supplemental.context.context_revision)return h.response({},409);
      return h.defaultRequest(url,options);};
    await h.tick(250);assert.equal(h.raw(c).length,0);await h.tick(2000);
    assert.deepEqual(requests.slice(0,3),['frame','context','frame']);assert.notEqual(c._auxGuard,first);assert.ok(clockShown(c));
    c.disconnectedCallback();
  },
  async aux_replay(h){
    for(const field of ['endpoint_id','context_revision','profile_invalidation','session_id','profile_revision']){
      const c=h.card();await h.start(c);const initial=structuredClone(h.frame.supplemental.context);
      if(field==='endpoint_id')c._bindSupplemental({...initial,endpoint_id:'00000000-0000-0000-0000-000000000099'});
      else if(field.endsWith('revision')&&field!=='profile_revision'||field==='profile_invalidation'){
        c._bindSupplemental({...initial,[field]:initial[field]+1});c._bindSupplemental(initial);
      }else{c._bindSupplemental({...initial,[field]:field==='session_id'?'00000000-0000-0000-0000-000000000099':'f'.repeat(64)});c._bindSupplemental(initial);}
      assert.equal(c._terminal,true,field);assert.equal(h.raw(c).length,0);assert.equal(h.owner._leases,0);
      const calls=h.calls;c._reconcile();await h.tick(3000);assert.equal(h.calls,calls);c.disconnectedCallback();
    }
  },
  async aux_epochs(h){
    const c=h.card();await h.start(c);const initial=structuredClone(h.frame.supplemental.context);
    for(let i=1;i<=150;i++)c._bindSupplemental({...initial,context_revision:initial.context_revision+i});
    assert.equal(c._terminal,false);assert.equal(c._retiredConnections.size,0);assert.equal(c._retiredProfiles.size,0);c.disconnectedCallback();
  },
  async aux_limits(h){
    for(const kind of ['session_id','profile_revision']){
      const c=h.card();await h.start(c);const initial=structuredClone(h.frame.supplemental.context);
      for(let i=1;i<=65;i++)c._bindSupplemental({...initial,[kind]:kind==='session_id'?`00000000-0000-0000-0000-${String(i+100).padStart(12,'0')}`:i.toString(16).padStart(64,'0')});
      assert.equal(c._terminal,true);assert.equal(h.owner._leases,0);assert.ok(c._retiredProfiles.size<=64&&c._retiredConnections.size<=64);c.disconnectedCallback();
    }
  },
  async aux_auth(h){
    for(const status of [401,403])for(const phase of ['context','frame']){
      h.request=async(url,options)=>url.endsWith('/'+phase)?h.response({},status):h.defaultRequest(url,options);
      const c=h.card();await h.start(c);assert.equal(c._terminal,true);assert.equal(h.owner._leases,0);assert.equal(h.raw(c).length,0);
      const calls=h.calls;c._reconcile();await h.tick(10000);assert.equal(h.calls,calls);c.disconnectedCallback();assert.equal(h.timers.size,0);
    }
  },
  async aux_late_auth(h){
    let resolve;h.request=()=>new Promise(done=>resolve=done);const c=h.card();await h.start(c);
    h.document.hidden=true;c._visibility();h.request=h.defaultRequest;h.newer();h.document.hidden=false;c._visibility();await flush();
    assert.ok(clockShown(c));const cookies=h.cookies.length;resolve(h.response({},401));await flush();
    assert.equal(c._terminal,false);assert.equal(h.cookies.length,cookies);assert.ok(clockShown(c));c.disconnectedCallback();assert.equal(h.timers.size,0);
  },
  async aux_late_body(h){
    let body;h.request=async()=>new Response(new ReadableStream({start(controller){body=controller;}}),{headers:{'content-type':'application/json'}});
    const c=h.card();await h.start(c);c.disconnectedCallback();assert.equal(h.owner._leases,0);assert.equal(h.timers.size,0);
    body.enqueue(new TextEncoder().encode(JSON.stringify(h.contextResponse())));body.close();await flush();
    assert.equal(h.raw(c).length,0);assert.equal(h.calls,1);assert.equal(h.timers.size,0);
  },
  async aux_stall(h){
    const c=h.card();h.frame.supplemental.clock.age_seconds=4;await h.start(c);
    h.request=()=>new Promise(()=>{});await h.tick(1000);assert.equal(clockShown(c),false);assert.ok(h.raw(c).length);
    await h.tick(4500);assert.equal(h.raw(c).length,0);assert.equal(h.owner._leases,0);
    const calls=h.calls;await h.tick(10000);assert.equal(h.calls,calls);c.disconnectedCallback();assert.equal(h.timers.size,0);
  },
  async aux_shared(h){
    const owner=h.loadWaterfall();assert.equal(owner,h.owner);vm.runInContext(input.script,h.ctx);
    const release=await owner.acquire(h.api),a=h.card(),b=h.card();await h.start(a);await h.start(b);
    assert.equal(h.owner._leases,3);assert.equal(h.sessions,1);a._endSupplemental();assert.equal(h.owner._leases,2);assert.ok(clockShown(b));
    a.disconnectedCallback();release();assert.equal(h.owner._leases,1);b.disconnectedCallback();assert.equal(h.owner._leases,0);assert.equal(h.timers.size,0);
  },
  async aux_limits_body(h){
    for(const payload of [()=>new Response('x'.repeat(2049),{headers:{'content-type':'application/json'}}),()=>h.response({...h.contextResponse(),version:99}),()=>new Response(new Uint8Array([255]),{headers:{'content-type':'application/json'}})]){
      h.request=async()=>payload();const c=h.card();await h.start(c);assert.equal(h.raw(c).length,0);assert.equal(c._auxGuard,null);assert.equal(h.owner._leases,0);c.disconnectedCallback();assert.equal(h.timers.size,0);
    }
    h.request=async(url,options)=>url.endsWith('/context')?h.defaultRequest(url,options):new Response('x'.repeat(262145),{headers:{'content-type':'application/json'}});
    const c=h.card();await h.start(c);assert.equal(h.raw(c).length,0);assert.equal(h.owner._leases,0);c.disconnectedCallback();
  },
  async aux_discovery(h){
    const ui={panels:{one:h.panel('local_sds200_one'),two:h.panel('local_sds200_two')}};
    await assert.rejects(h.owner.resolve(h.api,ui,'api/v1/display-supplemental/context'),/More than one/);
    await assert.rejects(h.owner.resolve(h.api,h.ui,'api/v1/display-supplemental/frame'));
    await assert.rejects(h.owner.resolve(h.api,h.ui,'https://bad.test/context'));
    h.ws=async req=>{if(req.endpoint.includes('two'))throw Error('PRIVATE');return {state:'started',ingress:true,ingress_url:'/api/hassio_ingress/example_key/'};};
    await assert.rejects(h.owner.resolve(h.api,ui,'api/v1/display-supplemental/context'));
  },
  async aux_new_ingress(h){
    const c=h.card();await h.start(c);const guard=c._auxGuard;
    h.ws=async req=>req.endpoint==='/ingress/session'?{session:'next_ingress_session_1234'}:{state:'started',ingress:true,ingress_url:'/api/hassio_ingress/other_key/'};
    h.request=(url,options)=>h.defaultRequest(url.replace('/other_key/','/example_key/'),options);
    h.ui.panels.scanner.title='sds200 renamed';h.newer();c.contexts.hassUi(h.ui);await flush();
    assert.equal(c._auxGuard,guard);assert.ok(h.raw(c).length);assert.equal(clockShown(c),false);c.disconnectedCallback();
  },
  async aux_instance(h){
    const a=h.card(),b=h.card();a.setConfig({layout:'simple',led_treatment:'border'});b.setConfig({layout:'detail'});await h.start(a);await h.start(b);
    assert.ok(clockShown(a)&&clockShown(b));assert.equal(a._surround.children[0].dataset.mode,'simple_trunk');assert.equal(b._surround.children[0].dataset.mode,'detail_trunk');
    a.setConfig({layout:'detail'});assert.equal(b._config.layout,'detail');a.disconnectedCallback();b.disconnectedCallback();
  },
  async aux_clock_budget(h){
    let resolveContext,resolveFrame;
    h.request=url=>new Promise(done=>{if(url.endsWith('/context'))resolveContext=done;else resolveFrame=done;});
    const c=h.card();await h.start(c);await h.tick(2000);resolveContext(h.response(h.contextResponse()));await flush();
    await h.tick(2000);resolveFrame(h.response());await flush();assert.ok(clockShown(c));
    h.request=h.defaultRequest;await h.tick(2900);assert.ok(clockShown(c));await h.tick(100);assert.equal(clockShown(c),false);c.disconnectedCallback();
  },
  async aux_unmount(h){await cases.unmount(h);await cases.late_context(harness());await cases.late_auth(harness());},
  async aux_old_deadline(h){
    let finishOld,finishNew;h.request=()=>new Promise(done=>finishOld=done);const c=h.card();await h.start(c);
    h.document.hidden=true;c._visibility();h.document.hidden=false;
    h.request=()=>new Promise(done=>finishNew=done);c._visibility();await flush();const timer=c._requestTimer;
    finishOld(h.response(h.contextResponse()));await flush();assert.equal(c._requestTimer,timer);assert.ok(h.timers.has(timer));
    await h.tick(5000);assert.equal(h.owner._leases,0);finishNew(h.response(h.contextResponse()));await flush();assert.equal(h.raw(c).length,0);c.disconnectedCallback();assert.equal(h.timers.size,0);
  },
  async aux_total_budget(h){
    let finishContext,finishFrame;h.request=url=>new Promise(done=>{if(url.endsWith('/context'))finishContext=done;else finishFrame=done;});
    const c=h.card();await h.start(c);await h.tick(4000);finishContext(h.response(h.contextResponse()));await flush();
    await h.tick(1100);finishFrame(h.response());await flush();assert.equal(h.raw(c).length,0);assert.equal(h.owner._leases,0);c.disconnectedCallback();assert.equal(h.timers.size,0);
  },
  async aux_frame_cannot_rebind(h){
    const c=h.card();await h.start(c);const old=c._auxGuard;
    h.request=async(url,options)=>{
      if(url.endsWith('/context'))return h.defaultRequest(url,options);
      const bad=structuredClone(h.frame);bad.supplemental.context.context_revision++;return h.response(bad);
    };
    await h.tick(250);assert.equal(h.raw(c).length,0);assert.equal(c._auxGuard,old);c.disconnectedCallback();
  },
  async aux_statuses(h){
    for(const status of ['unavailable','disabled','blocked','invalid_rtc','invalid_source','stale']){
      h.frame.supplemental.clock={status,sample_sequence:null,age_seconds:null,value:null};
      const c=h.card();await h.start(c);assert.ok(h.raw(c).length);assert.equal(clockShown(c),false);
      assert.ok(c._auxNote.textContent.includes(`clock: ${status}`));c.disconnectedCallback();
    }
  },
  async aux_guard_private(h){
    h.window.sdsctlSupplemental=Object.freeze({create(){throw Error('wrong global helper');}});
    const c=h.card();await h.start(c);assert.ok(clockShown(c));assert.notEqual(c._auxGuard,h.window.sdsctlSupplemental);c.disconnectedCallback();
  },
  async aux_legacy_owner(h){
    const owner=h.owner,c=h.card();await h.start(c);assert.equal(h.calls,0);assert.equal(h.raw(c).length,0);
    assert.equal(vm.runInContext('globalThis[Symbol.for("sdsctl.home-assistant.ingress.v1")]',h.ctx),owner);
    c.disconnectedCallback();assert.equal(h.timers.size,0);
  },
  async empty_states(h){
    for(const [state,expected] of Object.entries({
      current:'Scanner layout unavailable.',
      waiting:'Connected — waiting for a new scanner frame.',
      disconnected:'Scanner disconnected — waiting for reconnection.',
      stale:'Scanner data is stale — waiting for a fresh frame.',
      override:'Scanner menu, popup or replay is active — normal display paused.',
      ambiguous_records:'Scanner data is inconsistent — waiting for a matching frame.',
      unsupported_screen:'This scanner screen is not supported by Mimic-SDS.',
    })){
      h.scenario('held_trunk');
      const c=h.card();await h.start(c);assert.ok(h.raw(c).length);
      const original=structuredClone(h.frame.display),data=h.frame.display;
      for(const f of Object.values(data.frames)){
        f.status=state;f.screen=null;f.layout_basis='unavailable';f.sequence++;
        for(const key of Object.keys(f.indicators))f.indicators[key]=null;
      }
      if(state==='disconnected')data.session_id=null;
      await h.tick(250);
      assert.equal(h.raw(c).length,0);
      assert.equal(c._surround.children[0].textContent,expected);
      assert.ok(c._note.textContent.startsWith('Scanner screen layout is not currently available.'));
      for(const f of Object.values(data.frames)){
        f.profile_revision=null;f.source=null;f.profile_status='unavailable';f.sequence++;
      }
      await h.tick(250);
      assert.equal(c._surround.children[0].textContent,'An administrator must import a display profile for this scanner.');
      for(const f of Object.values(original.frames))f.sequence+=3;
      Object.assign(data,original);
      await h.tick(250);assert.ok(h.raw(c).length);
      c.disconnectedCallback();assert.equal(h.timers.size,0);
    }
  },
  async configuration(h){
    const c=h.card(); assert.equal(c._config.layout,'preferred');assert.equal(c.getCardSize(),9);
    for(const value of [null,[],{url:'https://bad.test'},{profile:'/private'},{layout:'auto'},{led_treatment:'blink'},
      {density:'huge'},{show_details:'false'},{title:'x'.repeat(129)},{type:'wrong'},{title:'a\u001bb'},
      {grid_options:{bad:1}},{grid_options:{rows:0}},{grid_options:{columns:13}},{grid_options:{rows:'6'}}]) assert.throws(()=>c.setConfig(value));
    const input={title:'Demo',layout:'simple',led_treatment:'border',show_details:false,grid_options:{rows:'auto',columns:'full'}};
    c.setConfig(input);input.grid_options.rows=8;assert.equal(c._config.grid_options.rows,'auto');assert.equal(c.dataset.fixed,'false');
    c.setConfig({grid_options:{rows:8,columns:12}});assert.equal(c.dataset.fixed,'true');
    for(const item of h.Card.getConfigForm().schema) assert.ok(h.Card.getConfigForm().computeLabel(item));
  },
  async render_all(h){
    const c=h.card(); let sequence=100;
    for(const payload of Object.values(input.scenarios)){
      const data=structuredClone(payload.display);for(const f of Object.values(data.frames)){if(f.sequence!==null){f.sequence=++sequence;f.age_seconds=0;}}
      // The controller receives decoded wire data in ordinary operation. Rendering
      // independent fixtures here tests every canonical geometry, not transitions.
      c._identity=null;c._accept(data,0);
      for(const layout of ['preferred','simple','detail']){c.setConfig({layout});
        assert.equal(c._surround.children[0].children.length,data.frames[layout].screen?.regions.length??0);
        if(data.frames[layout].status!=='current')assert.equal(h.raw(c).length,0);
        for(const region of data.frames[layout].screen?.regions??[]){
          if(data.frames[layout].status==='current' && region.value_status==='raw_source' &&
             ['Day','Time'].includes(region.token)){
            const cell=nodes(c.shadowRoot).find(node=>node.dataset.region===region.id);
            assert.ok(cell, 'Configured clock cell must be present');
            assert.ok(nodes(cell).some(node=>node.textContent===region.text),
              'Configured scanner clock value must render literally');
          }
        }
      }
    }
  },
  async freshness(h){const c=h.card();await h.start(c);assert.ok(h.raw(c).length);await h.tick(5500);assert.equal(h.raw(c).length,0);
    c._stop();c._reconcile();await flush();assert.equal(h.raw(c).length,0);h.newer();await h.tick(250);assert.ok(h.raw(c).length);c.disconnectedCallback();},
  async identity(h){const c=h.card();await h.start(c);const endpoint=h.frame.display.endpoint_id;h.frame.display.endpoint_id='00000000-0000-0000-0000-000000000099';
    await h.tick(250);assert.equal(h.raw(c).length,0);h.frame.display.endpoint_id=endpoint;h.newer();await h.tick(2000);assert.ok(h.raw(c).length);
    for(const f of Object.values(h.frame.display.frames))f.sequence=1;await h.tick(250);assert.equal(h.raw(c).length,0);
    h.frame.display.stream_id='00000000-0000-0000-0000-000000000088';await h.tick(2000);assert.ok(h.raw(c).length);c.disconnectedCallback();},
  async hidden(h){const c=h.card();await h.start(c);h.document.hidden=true;c._visibility();assert.equal(h.raw(c).length,0);assert.equal(h.owner._leases,0);
    const calls=h.calls;await h.tick(5000);assert.equal(h.calls,calls);h.document.hidden=false;h.newer();c._visibility();await flush();assert.ok(h.raw(c).length);c.disconnectedCallback();assert.equal(h.timers.size,0);},
  async unmount(h){const c=h.card();let resolve;h.request=()=>new Promise(done=>resolve=done);await h.start(c);c.disconnectedCallback();resolve(h.response());await flush();
    assert.equal(h.raw(c).length,0);assert.equal(h.owner._leases,0);assert.equal(h.timers.size,0);},
  async late_auth(h){const c=h.card();let resolve;h.ws=()=>new Promise(done=>resolve=done);await h.start(c);c.disconnectedCallback();resolve({session:'late_valid_session_1234'});await flush();
    assert.equal(h.cookies.length,0);assert.equal(h.owner._leases,0);assert.equal(h.calls,0);assert.equal(h.timers.size,0);},
  async auth_timeout(h){const c=h.card();let resolve;h.ws=()=>new Promise(done=>resolve=done);await h.start(c);await h.tick(8000);assert.equal(h.owner._leases,0);
    resolve({session:'late_valid_session_1234'});await flush();assert.equal(h.cookies.length,0);c.disconnectedCallback();assert.equal(h.timers.size,0);},
  async invalid_auth(h){for(const session of ['bad;cookie=oops','short',null,{},'x'.repeat(257)]){h.ws=async()=>({session});const c=h.card();await h.start(c);assert.equal(h.owner._leases,0);assert.equal(h.cookies.length,0);c.disconnectedCallback();}},
  async shared_leases(h){const waterfallOwner=h.loadWaterfall();assert.equal(waterfallOwner,h.owner);vm.runInContext(input.script,h.ctx);
    assert.equal(vm.runInContext('globalThis[Symbol.for("sdsctl.home-assistant.ingress.v1")]',h.ctx),h.owner);
    const waterfallRelease=await waterfallOwner.acquire(h.api),a=h.card(),b=h.card();await h.start(a);await h.start(b);
    assert.equal(h.sessions,1);assert.equal(h.owner._leases,3);a.disconnectedCallback();assert.equal(h.owner._leases,2);assert.ok(h.raw(b).length);
    waterfallRelease();assert.equal(h.owner._leases,1);b.disconnectedCallback();assert.equal(h.owner._leases,0);assert.equal(h.timers.size,0);},
  async late_refresh(h){let reject;const release=await h.owner.acquire(h.api);h.ws=()=>new Promise((_,fail)=>reject=fail);await h.tick(60000);release();
    h.ws=async()=>({session:'new_session_after_restart_1234'});const next=await h.owner.acquire(h.api);
    const before=h.cookies.length;reject(Error('private failure'));await flush();assert.equal(h.cookies.length,before);assert.equal(h.owner._leases,1);
    assert.ok(h.document.cookie.includes('new_session_after_restart'));next();assert.equal(h.timers.size,0);},
  async late_context(h){const c=h.card();c.connectedCallback();const old={...c.contexts};let unsubscribed=0;c.disconnectedCallback();c.connectedCallback();
    old.hassApi(h.api);old.hassUi(h.ui,()=>unsubscribed++);await flush();assert.equal(unsubscribed,1);assert.equal(c._api,null);assert.equal(h.calls,0);c.disconnectedCallback();},
  async discovery(h){
    const valid={state:'started',ingress:true,ingress_url:'/api/hassio_ingress/example_key/'};
    for(const bad of [{...valid,ingress_url:'https://other.test/api/hassio_ingress/example_key/'},{...valid,ingress_url:'/api/hassio_ingress/example_key/?token=secret'},
      {...valid,ingress_url:'https://user:pass@ha.example.test/api/hassio_ingress/example_key/'},{...valid,ingress_url:'%%%%'}, {...valid,state:'unknown'}, {...valid,ingress:false}]){
      h.ws=async()=>bad;await assert.rejects(h.owner.resolve(h.api,h.ui,'api/v1/display-frame'));
    }
    h.ws=async()=>valid;const ui={panels:{one:h.panel('local_sds200_one'),two:h.panel('local_sds200_two')}};
    await assert.rejects(h.owner.resolve(h.api,ui,'api/v1/display-frame'),/More than one/);
    h.ws=async req=>{if(req.endpoint.includes('two'))throw Error('private secret');return valid;};await assert.rejects(h.owner.resolve(h.api,ui,'api/v1/display-frame'),/authentication/);
    await assert.rejects(h.owner.resolve(h.api,h.ui,'api/v1/control'));
  },
  async discovery_timeout(h){h.ws=()=>new Promise(()=>{});const promise=h.owner.resolve(h.api,h.ui,'api/v1/display-frame');const rejected=assert.rejects(promise);await h.tick(8000);await rejected;},
  async context_change(h){const c=h.card();await h.start(c);h.ui.panels.second=h.panel('local_sds200_two');c.contexts.hassUi(h.ui);await flush();assert.equal(h.raw(c).length,0);assert.equal(h.owner._leases,0);c.disconnectedCallback();},
  async invalid_frames(h){for(const mutate of [p=>p.protocol='bad',p=>p.display.extra='private',p=>p.display.frames.preferred.screen.rows=999,p=>p.display.frames.preferred.indicators.alert_led='Orange',
    p=>p.display.frames.preferred.screen.regions[0].stored_color={text:'url(x)',background:'000000'},p=>p.display.frames.simple.sequence++,p=>p.display.frames.preferred.status='stale']){
      const bad=structuredClone(h.frame);mutate(bad);h.request=async()=>h.response(bad);const c=h.card();await h.start(c);assert.equal(h.raw(c).length,0);assert.equal(h.owner._leases,0);c.disconnectedCallback();}},
  async response_bounds(h){for(const response of [()=>new Response('x'.repeat(262145),{headers:{'content-type':'application/json'}}),()=>new Response('<html>private</html>'),
    ()=>new Response(new Uint8Array([255]),{headers:{'content-type':'application/json'}}),()=>h.response(h.frame,404)]){
      h.request=async()=>response();const c=h.card();await h.start(c);assert.equal(h.raw(c).length,0);assert.equal(h.owner._leases,0);c.disconnectedCallback();}},
  async expired_auth(h){const c=h.card();await h.start(c);h.request=async()=>h.response({},401);await h.tick(250);assert.equal(h.raw(c).length,0);assert.equal(h.owner._leases,0);
    h.request=async()=>h.response();h.newer();await h.tick(2000);assert.ok(h.raw(c).length);assert.equal(h.sessions,2);c.disconnectedCallback();},
  async fetch_timeout(h){const c=h.card();let resolve;h.request=()=>new Promise(done=>resolve=done);await h.start(c);await h.tick(2000);assert.equal(h.owner._leases,0);
    resolve(h.response());await flush();assert.equal(h.raw(c).length,0);c.disconnectedCallback();assert.equal(h.timers.size,0);},
  async instance_options(h){const a=h.card(),b=h.card();a.setConfig({layout:'simple',led_treatment:'border'});b.setConfig({layout:'detail'});await h.start(a);await h.start(b);
    assert.equal(a._surround.children[0].dataset.mode,'simple_trunk');assert.equal(b._surround.children[0].dataset.mode,'detail_trunk');
    const cells=nodes(a.shadowRoot);const system=cells.find(n=>n.dataset.region==='system');assert.equal(system.dataset.hold,'on');
    assert.equal(system.style.backgroundColor,'#'+h.frame.display.frames.simple.screen.regions.find(r=>r.id==='system').stored_color.text);assert.equal(a._surround.dataset.led,h.frame.display.frames.preferred.indicators.alert_led);
    a.setConfig({layout:'detail',led_treatment:'strips'});assert.equal(b._config.layout,'detail');assert.equal(b._config.led_treatment,'strips');a.disconnectedCallback();b.disconnectedCallback();},
};
(async()=>{await cases[input.case](harness());console.log(`${input.case} passed`);})().catch(error=>{console.error(error);process.exitCode=1;});
