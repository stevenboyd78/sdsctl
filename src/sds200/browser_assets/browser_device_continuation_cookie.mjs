// Inert comparison helper. Hashes a supplied cookie snapshot, not browser proof.
// Never reads/writes Chrome cookies or storage, removes a cookie or grants access.
// The trusted controller must obtain and recheck actual browser snapshots itself.
const exact=(v,keys)=>v!==null&&typeof v==='object'&&!Array.isArray(v)&&
  Object.keys(v).sort().join(',')===[...keys].sort().join(',');
const refusal=()=>Error('Browser continuation cookie identity is unconfirmed.');

export async function fingerprintContinuationCookie(selectedOrigin,cookie) {
  try {
    if(typeof selectedOrigin!=='string'||!/^[\x21-\x7e]{1,2048}$/.test(selectedOrigin))throw refusal();
    const origin=new URL(selectedOrigin);
    if(origin.protocol!=='https:'||origin.origin!==selectedOrigin||origin.username||origin.password||
      origin.pathname!=='/'||origin.search||origin.hash||
      !exact(cookie,['name','value','domain','hostOnly','path','secure','httpOnly',
        'sameSite','session','expirationDate','storeId'])||
      cookie.name!=='__Host-sdsctl-device-session'||typeof cookie.value!=='string'||
      !/^sdsctl-browser-session-v1\.[a-f0-9]{64}$/.test(cookie.value)||
      cookie.domain!==origin.hostname||cookie.hostOnly!==true||cookie.path!=='/'||
      cookie.secure!==true||cookie.httpOnly!==true||cookie.sameSite!=='strict'||
      cookie.session!==false||cookie.storeId!=='0'||typeof cookie.expirationDate!=='number'||
      !Number.isFinite(cookie.expirationDate)||cookie.expirationDate<=0||
      cookie.expirationDate>=Number.MAX_SAFE_INTEGER)throw refusal();
    // Capture the exact bytes BEFORE awaiting. Returning a digest cannot prove
    // the browser still holds this snapshot, or that its session remains valid.
    const bytes=new TextEncoder().encode(JSON.stringify([
      'sdsctl-continuation-cookie-v1',selectedOrigin,cookie.name,cookie.value,
      cookie.domain,cookie.hostOnly,cookie.path,cookie.secure,cookie.httpOnly,
      cookie.sameSite,cookie.session,cookie.expirationDate,cookie.storeId,
    ]));
    const hash=await crypto.subtle.digest('SHA-256',bytes);
    return Array.from(new Uint8Array(hash),n=>n.toString(16).padStart(2,'0')).join('');
  } catch {throw refusal();}
}
