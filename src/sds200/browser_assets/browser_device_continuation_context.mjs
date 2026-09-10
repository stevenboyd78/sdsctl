// Inert parser for the future fixed native continuation role. No listener,
// storage, cookie, native request, initialization or authentication side effects.
const exact=(v,keys)=>v!==null&&typeof v==='object'&&!Array.isArray(v)&&
  Object.keys(v).sort().join(',')===[...keys].sort().join(',');
const hex=v=>typeof v==='string'&&/^[a-f0-9]{64}$/.test(v);
const integer=v=>Number.isSafeInteger(v)&&v>0&&v<Number.MAX_SAFE_INTEGER;
const HOST='org.sdsctl.browser_device';
const refusal=()=>Error('Browser continuation context is unavailable or changed.');

export function validateContinuationContext(value,build,extensionId) {
  try {
    if(!hex(build)||typeof extensionId!=='string'||!/^[a-p]{32}$/.test(extensionId)||
      !exact(value,['version','ok','build','role','config','extensionId','acknowledge','launch','continuation'])||
      value.version!==1||value.ok!==true||value.build!==build||value.extensionId!==extensionId||
      value.role!=='continuation'||value.acknowledge!==false||value.launch!==null||
      !exact(value.config,['origin','identity','nativeHost'])||
      !hex(value.config.identity)||value.config.nativeHost!==HOST||
      typeof value.config.origin!=='string'||!/^[\x21-\x7e]{1,2048}$/.test(value.config.origin))
      throw refusal();
    const origin=new URL(value.config.origin);
    if(origin.protocol!=='https:'||origin.origin!==value.config.origin||origin.username||
      origin.password||origin.pathname!=='/'||origin.search||origin.hash)throw refusal();
    const selected=value.continuation;
    if(!exact(selected,['epoch','mode','binding'])||!hex(selected.epoch)||
      !['paused','active'].includes(selected.mode)||
      !exact(selected.binding,['fingerprint','revision','generation'])||
      !hex(selected.binding.fingerprint)||!integer(selected.binding.revision)||
      (selected.mode==='paused'?selected.binding.generation!==null:
        !integer(selected.binding.generation)))throw refusal();
    return Object.freeze({
      settings:Object.freeze({identity:value.config.identity,origin:value.config.origin,
        epoch:selected.epoch,build}),
      observed:Object.freeze({identity:value.config.identity,epoch:selected.epoch,
        mode:selected.mode,binding:Object.freeze({...selected.binding})}),
    });
  } catch {throw refusal();}
}
