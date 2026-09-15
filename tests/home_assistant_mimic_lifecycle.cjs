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
  let now=0, next=0, sessions=0, calls=0;
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
  let frame=structuredClone(input.scenarios.held_trunk);
  for(const f of Object.values(frame.display.frames)){f.sequence=100;f.age_seconds=0;}
  const response=(payload=frame,status=200)=>new Response(JSON.stringify(payload),{status,headers:{'content-type':'application/json'}});
  let request=async(url,options)=>{
    assert.equal(url,'https://ha.example.test/api/hassio_ingress/example_key/api/v1/display-frame');
    assert.equal(options.credentials,'same-origin');assert.equal(options.redirect,'error');assert.equal(options.cache,'no-store');
    return response();
  };
  const ctx=vm.createContext({window,document,location:{origin:'https://ha.example.test',protocol:'https:'},HTMLElement:Element,CustomEvent:class {},
    customElements:{get:tag=>definitions.get(tag),define:(tag,constructor)=>definitions.set(tag,constructor)},
    IntersectionObserver:class {constructor(callback){this.callback=callback;observers.push(this);}observe(){}disconnect(){}},
    performance:{now:()=>now},AbortController,TextDecoder,URL,fetch:(...args)=>{calls++;return request(...args);}});
  vm.runInContext(input.script,ctx);
  const Card=definitions.get('sds200-mimic-card');
  const owner=vm.runInContext('globalThis[Symbol.for("sdsctl.home-assistant.ingress.v1")]',ctx);
  const card=()=>{const c=new Card();c.setConfig({});return c;};
  const start=async c=>{c.connectedCallback();c.contexts.hassApi(api);c.contexts.hassUi(ui,()=>{});observers.at(-1).callback([{isIntersecting:true}]);await flush();};
  const raw=c=>nodes(c.shadowRoot).filter(node=>node.dataset.valueStatus==='raw_source');
  return {Card,card,start,raw,ctx,owner,window,document,api,ui,panel,cookies,timers,response,
    get calls(){return calls;},get sessions(){return sessions;},get frame(){return frame;},
    set ws(fn){ws=fn;},set request(fn){request=fn;},
    newer(){for(const f of Object.values(frame.display.frames))f.sequence++;},
    scenario(name){frame=structuredClone(input.scenarios[name]);for(const f of Object.values(frame.display.frames)){if(f.sequence!==null){f.sequence=100;f.age_seconds=0;}}},
    loadWaterfall(){return vm.runInContext(`(()=>{${input.waterfall}\nreturn sds200WaterfallIngressSession;})();`,ctx);},
    async tick(duration){const end=now+duration;for(;;){const entry=[...timers.entries()].filter(([,v])=>v.at<=end).sort((a,b)=>a[1].at-b[1].at)[0];if(!entry)break;
      const [id,task]=entry;now=task.at;timers.delete(id);if(task.repeat)timers.set(id,{...task,at:now+task.repeat});task.fn();await flush();}now=end;await flush();},
  };
}
const cases={
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
