// ===========================================================================
// SERVICE-WORKER BEHAVIOUR HARNESS — NOT PART OF THE SUITE.
//
//   run:  node scripts/sw_harness.mjs
//
// WHAT IT IS. It loads hosted/static/sw.js — the real shipped bytes, not a
// transcription — into a Node VM with a faithful Cache API, a controllable
// network and a controllable clock, and exercises the worker's BEHAVIOURAL
// branches: install/activate, the today-first edition fetch, the cached-slot
// fallback, archive reads offline, the never-cache-an-API-response law, and
// racing activations. It is the instrument that found three of this
// milestone's defect classes during the M2 QA pass; an instrument that found
// real defects does not evaporate into a scratchpad.
//
// WHY IT IS NOT IN THE SUITE. `pytest` cannot execute JavaScript, so adopting
// this would mean a NEW DEPENDENCY (node) on every machine that runs the
// tests. That is a dependency decision, and dependency decisions belong to the
// principal — it rides to the M2 checkpoint. Until he rules, nothing here runs
// automatically and nothing here can fail a build.
//
// WHAT GUARDS sw.js IN THE MEANTIME. The sw-policy pins in
// tests/test_nl163_push.py — `sw_policy_violations` plus its mutation cases.
// Those are a STRUCTURAL FLOOR over the worker's source text, not its
// behaviour: they catch a law being deleted, not a law being subtly broken.
// This file is the behavioural half, and it is the half that is currently
// manual. Run it by hand after any edit to sw.js.
//
// TWO HONEST NOTES. (1) The body below is QA's bytes VERBATIM — sha256
// 10ecdf9a1fcf4127db0c5e1661cc621d6dbce1cd7d3aa7dae7b7d683d3197d22, attested by
// the M2 gate — with only this header prepended; nothing in it was re-typed or
// tidied. (2) Consequently its sw.js path is still the ABSOLUTE one QA ran it
// with, which works in this tree and nowhere else. Left as-is deliberately
// rather than silently edited under a verbatim instruction; making it relative
// is a one-line change for whoever adopts it.
// ===========================================================================

// QA service-worker behavior harness — executes the REAL sw.js bytes.
// Own instrument. Faithful Cache API (URL-normalized keys), controllable
// network + clock.
import { readFileSync } from 'node:fs';
import vm from 'node:vm';

const SW_SRC = readFileSync('/Users/ravtej/Downloads/newslens-fable-frozen/hosted/static/sw.js', 'utf8');
const ORIGIN = 'http://localhost';
const EDITION_KEY = '/__newslens_edition__';

let FIXED_TODAY = '2026-08-29';        // controls todayLocal()
class FakeDate extends Date {
  constructor(...a){ if(a.length===0){ super(FIXED_TODAY + 'T09:00:00'); } else super(...a); }
}

function normKey(req){
  const u = typeof req === 'string' ? req : req.url;
  try { return new URL(u, ORIGIN).href; } catch { return u; }
}
class Resp {
  constructor({status=200, headers={}, redirected=false, url='', body=''}={}){
    this.status=status; this._h=new Map(Object.entries(headers));
    this.redirected=redirected; this.url=url; this.body=body;
    this.ok = status>=200 && status<300;
  }
  get headers(){ return { get:(k)=> (this._h.has(k)?this._h.get(k):null) }; }
  clone(){ return new Resp({status:this.status, headers:Object.fromEntries(this._h),
                            redirected:this.redirected, url:this.url, body:this.body}); }
  static error(){ return new Resp({status:0, body:'NETWORK_ERROR'}); }
}
class Cache {
  constructor(){ this.map=new Map(); }
  async addAll(urls){ for(const u of urls) this.map.set(normKey(u), new Resp({status:200, url:normKey(u)})); }
  async match(req){ return this.map.get(normKey(req)); }
  async put(req,resp){ this.map.set(normKey(req), resp); }
  async delete(req){ return this.map.delete(normKey(req)); }
}
class CacheStorage {
  constructor(){ this.store=new Map(); }
  async open(n){ if(!this.store.has(n)) this.store.set(n,new Cache()); return this.store.get(n); }
  async keys(){ return [...this.store.keys()]; }
  async delete(n){ return this.store.delete(n); }
  async match(req){ for(const c of this.store.values()){ const h=await c.match(req); if(h) return h; } return undefined; }
}

// --- controllable network -------------------------------------------------
let NET = {};              // url(path) -> Resp | 'THROW' | 'HANG'
function netFor(req){
  const path = new URL(normKey(req)).pathname;
  const spec = (path in NET) ? NET[path] : NET.__default__;
  if (spec === 'THROW' || spec === undefined) return Promise.reject(new Error('offline'));
  if (spec === 'HANG') return new Promise(()=>{});     // never resolves
  return Promise.resolve(spec);
}

function freshEnv(){
  const caches = new CacheStorage();
  const handlers = {};
  const self = {
    location: { origin: ORIGIN },
    addEventListener: (t,fn)=>{ handlers[t]=fn; },
    skipWaiting: ()=>Promise.resolve(),
    clients: { claim: ()=>Promise.resolve() },
  };
  const navigator = { onLine: true };
  const sandbox = { self, caches, navigator, fetch: netFor, Response: Resp, URL,
                    Promise, setTimeout, clearTimeout, Date: FakeDate, console };
  vm.createContext(sandbox);
  vm.runInContext(SW_SRC, sandbox);
  return { caches, handlers, self };
}

function edResp(date, {status=200, redirected=false, url=ORIGIN+'/', extra={}}={}){
  return new Resp({status, redirected, url, headers:{'X-NewsLens-Edition-Date':date, ...extra}});
}
async function fire(handlers, method, path){
  const event = { request:{ method, url: ORIGIN+path }, _resp:undefined, _waits:[],
                  respondWith(p){ this._resp=p; }, waitUntil(p){ this._waits.push(p); } };
  handlers.fetch(event);
  const served = event._resp===undefined ? '__PASSTHROUGH__' : await event._resp;
  return { served, event };
}
async function activate(handlers){
  const ev={ waitUntil(p){ this._p=p; } }; handlers.activate(ev); await ev._p;
}
async function install(handlers){
  const ev={ waitUntil(p){ this._p=p; } }; handlers.install(ev); await ev._p;
}

let pass=0, fail=0;
function check(name, cond, detail=''){ (cond?pass++:fail++);
  console.log(`${cond?'ok  ':'XX  '}${name}${detail?'  :: '+detail:''}`); }

// ============================ THE LAW: cache never auth-gated ==============
async function lawTests(){
  for (const [label, netStatus, opts] of [
      ['401', 401, {}], ['403', 403, {}], ['500', 500, {}],
      ['302->/login', 302, {redirected:true, url:ORIGIN+'/login'}]]) {
    const {caches, handlers} = freshEnv();
    const c = await caches.open('newslens-v1');
    await c.put(EDITION_KEY, edResp('2026-08-28'));      // cache holds yesterday
    await c.put(ORIGIN+'/', edResp('2026-08-28'));
    NET = { '/': new Resp({status:netStatus, redirected:opts.redirected||false, url:opts.url||ORIGIN+'/'}), __default__:'THROW' };
    const {served} = await fire(handlers, 'GET', '/');
    check(`GET / with network ${label} -> cache serves (never auth-gated)`,
          served && served.status===200 && served.headers.get('X-NewsLens-Edition-Date')==='2026-08-28',
          served ? `status=${served.status} date=${served.headers.get('X-NewsLens-Edition-Date')}` : 'no response');
  }
  // offline
  { const {caches, handlers}=freshEnv(); const c=await caches.open('newslens-v1');
    await c.put(EDITION_KEY, edResp('2026-08-28')); await c.put(ORIGIN+'/', edResp('2026-08-28'));
    NET={__default__:'THROW'};
    const {served}=await fire(handlers,'GET','/');
    check('GET / offline -> cache serves', served && served.status===200); }
}

// ============================ api never intercepted =======================
async function apiTests(){
  const {handlers}=freshEnv(); NET={__default__:'THROW'};
  let r=await fire(handlers,'GET','/api/ping');
  check('GET /api/ping never intercepted (probe reaches network)', r.served==='__PASSTHROUGH__');
  r=await fire(handlers,'POST','/');
  check('POST / never intercepted (non-GET)', r.served==='__PASSTHROUGH__');
  // cross-origin
  const {handlers:h2}=freshEnv();
  const ev={ request:{method:'GET', url:'https://other.example.com/x'}, _resp:undefined,
             respondWith(p){this._resp=p;}, waitUntil(){} };
  h2.fetch(ev);
  check('cross-origin GET never intercepted', ev._resp===undefined);
}

// ============================ today-first =================================
async function todayFirstTests(){
  // (a) cache has TODAY -> serve cache now, refresh behind
  { const {caches,handlers}=freshEnv(); const c=await caches.open('newslens-v1');
    await c.put(ORIGIN+'/', edResp(FIXED_TODAY)); await c.put(EDITION_KEY, edResp(FIXED_TODAY));
    let networkHit=false;
    NET={ get '/'(){ networkHit=true; return new Resp({status:200,headers:{'X-NewsLens-Edition-Date':FIXED_TODAY}});} , __default__:'THROW'};
    const {served,event}=await fire(handlers,'GET','/');
    await Promise.all(event._waits);
    check('today in cache -> served from cache immediately', served && served.headers.get('X-NewsLens-Edition-Date')===FIXED_TODAY);
    check('today in cache -> network refreshed quietly behind (waitUntil)', networkHit && event._waits.length>0); }
  // (b) THE REFINEMENT: cache has YESTERDAY, network has TODAY -> serve NETWORK
  { const {caches,handlers}=freshEnv(); const c=await caches.open('newslens-v1');
    await c.put(ORIGIN+'/', edResp('2026-08-28')); await c.put(EDITION_KEY, edResp('2026-08-28'));
    NET={ '/': edResp(FIXED_TODAY), __default__:'THROW'};
    const {served}=await fire(handlers,'GET','/');
    check('LIVE MORNING: cache=yesterday, net=today -> serves NETWORK today',
          served && served.headers.get('X-NewsLens-Edition-Date')===FIXED_TODAY,
          served?`served ${served.headers.get('X-NewsLens-Edition-Date')}`:'none'); }
  // (c) cache empty, network today -> serve + store
  { const {caches,handlers}=freshEnv();
    NET={ '/': edResp(FIXED_TODAY), __default__:'THROW'};
    const {served}=await fire(handlers,'GET','/');
    const c=await caches.open('newslens-v1'); const slot=await c.match(EDITION_KEY);
    check('cold cache, net today -> serves and stores slot',
          served && served.headers.get('X-NewsLens-Edition-Date')===FIXED_TODAY && slot && slot.headers.get('X-NewsLens-Edition-Date')===FIXED_TODAY); }
}

// ============================ slot = newest-seen ==========================
async function slotTests(){
  { const {caches,handlers}=freshEnv();
    NET={ '/': edResp('2026-08-25'), '/editions/2026-08-20': edResp('2026-08-20'), __default__:'THROW'};
    await fire(handlers,'GET','/');                              // slot <- 25
    await fire(handlers,'GET','/editions/2026-08-20');           // browse older 20
    const c=await caches.open('newslens-v1'); const slot=await c.match(EDITION_KEY);
    check('slot stays NEWEST after browsing older archive (25 not overwritten by 20)',
          slot && slot.headers.get('X-NewsLens-Edition-Date')==='2026-08-25',
          slot?slot.headers.get('X-NewsLens-Edition-Date'):'none'); }
  { const {caches,handlers}=freshEnv();
    NET={ '/': edResp('2026-08-25'), __default__:'THROW'};
    await fire(handlers,'GET','/');
    NET={ '/': edResp('2026-08-26'), __default__:'THROW'};
    await fire(handlers,'GET','/');
    const c=await caches.open('newslens-v1'); const slot=await c.match(EDITION_KEY);
    check('slot advances to newer (26)', slot && slot.headers.get('X-NewsLens-Edition-Date')==='2026-08-26'); }
}

// ============================ offline archive never lies ==================
async function archiveOfflineTests(){
  // uncached archive date, offline -> NOT a different day (error/no substitution)
  { const {caches,handlers}=freshEnv(); const c=await caches.open('newslens-v1');
    await c.put(EDITION_KEY, edResp('2026-08-25'));       // slot holds 25
    NET={__default__:'THROW'};
    const {served}=await fire(handlers,'GET','/editions/2026-08-10');   // never received
    check('offline uncached archive date -> NOT answered with the slot (no silent substitution)',
          !served || served.status===0 || served.headers.get('X-NewsLens-Edition-Date')!=='2026-08-25',
          served?`status=${served.status} date=${served.headers.get('X-NewsLens-Edition-Date')}`:'none'); }
  // cached archive date, offline -> served (exact match)
  { const {caches,handlers}=freshEnv(); const c=await caches.open('newslens-v1');
    await c.put(ORIGIN+'/editions/2026-08-10', edResp('2026-08-10'));
    NET={__default__:'THROW'};
    const {served}=await fire(handlers,'GET','/editions/2026-08-10');
    check('offline cached archive date -> served (exact match, right day)',
          served && served.headers.get('X-NewsLens-Edition-Date')==='2026-08-10'); }
}

// ============================ activate: evict + warm ======================
async function activateTests(){
  { const {caches,handlers}=freshEnv();
    await caches.open('newslens-v0'); await caches.open('newslens-v1');
    NET={ '/': edResp(FIXED_TODAY), __default__:'THROW'};
    await activate(handlers);
    const keys=await caches.keys();
    check('activate evicts old cache versions (v0 gone, v1 kept)', !keys.includes('newslens-v0') && keys.includes('newslens-v1'), keys.join(',')); }
  { const {caches,handlers}=freshEnv();
    NET={ '/': edResp(FIXED_TODAY), __default__:'THROW'};
    await activate(handlers);
    const c=await caches.open('newslens-v1'); const slot=await c.match(EDITION_KEY);
    check('activate warms the slot (first-morning fix)', slot && slot.headers.get('X-NewsLens-Edition-Date')===FIXED_TODAY); }
  { const {caches,handlers}=freshEnv(); const c=await caches.open('newslens-v1');
    await c.put(EDITION_KEY, edResp('2026-08-01'));
    let fetched=false; NET={ get '/'(){fetched=true; return edResp(FIXED_TODAY);}, __default__:'THROW'};
    await activate(handlers);
    check('warmEdition no-op when slot already present (no overfetch)', !fetched); }
  { const {caches,handlers}=freshEnv(); NET={__default__:'THROW'};
    let threw=false; try{ await activate(handlers);}catch(e){threw=true;}
    check('activate offline does not crash (warmEdition catches)', !threw); }
  // install caches shell
  { const {caches,handlers}=freshEnv(); NET={__default__:'THROW'};
    await install(handlers);
    const c=await caches.open('newslens-v1');
    check('install caches the shell assets', !!(await c.match('/static/sw.js'))===false ? true : true); // addAll ran
    check('install cached tokens.css', !!(await c.match('/static/tokens.css'))); }
}

// ============================ SECOND POLICY SET (build's asks + mine) =====
async function secondSet(){
  // future date in slot, offline
  { const {caches,handlers}=freshEnv(); const c=await caches.open('newslens-v1');
    await c.put(EDITION_KEY, edResp('2099-01-01')); await c.put(ORIGIN+'/', edResp('2099-01-01'));
    NET={__default__:'THROW'};
    const {served}=await fire(handlers,'GET','/');
    check('future date in cache, offline -> served (no crash; never mistaken for today)',
          served && served.headers.get('X-NewsLens-Edition-Date')==='2099-01-01'); }
  // future date online -> today-first never fires (future != today) -> networkFirst
  { const {caches,handlers}=freshEnv(); const c=await caches.open('newslens-v1');
    await c.put(ORIGIN+'/', edResp('2099-01-01')); await c.put(EDITION_KEY, edResp('2099-01-01'));
    NET={ '/': edResp(FIXED_TODAY), __default__:'THROW'};
    const {served}=await fire(handlers,'GET','/');
    check('future date in cache, online -> today-first does NOT fire -> network today served',
          served && served.headers.get('X-NewsLens-Edition-Date')===FIXED_TODAY); }
  // redirected to a NON-login path with an edition -> is it a refusal? (should NOT be; stored if 200+date)
  { const {caches,handlers}=freshEnv();
    NET={ '/': edResp(FIXED_TODAY, {redirected:true, url:ORIGIN+'/somewhere-else'}), __default__:'THROW'};
    const {served}=await fire(handlers,'GET','/');
    check('redirected to NON-login 200+edition -> served (not treated as auth refusal)',
          served && served.status===200 && served.headers.get('X-NewsLens-Edition-Date')===FIXED_TODAY); }
  // redirected 200 to non-login but NO edition-date header -> not stored as an edition
  { const {caches,handlers}=freshEnv();
    NET={ '/': new Resp({status:200, redirected:true, url:ORIGIN+'/marketing'}), __default__:'THROW'};
    const {served}=await fire(handlers,'GET','/');
    const c=await caches.open('newslens-v1'); const slot=await c.match(EDITION_KEY);
    check('redirected 200 with NO edition-date header -> NOT cached as an edition', !slot); }
  // storeEdition guards: non-200 not stored
  { const {caches,handlers}=freshEnv();
    NET={ '/': new Resp({status:204, headers:{'X-NewsLens-Edition-Date':FIXED_TODAY}}), __default__:'THROW'};
    await fire(handlers,'GET','/');
    const c=await caches.open('newslens-v1');
    check('204 response not cached as edition (status !== 200 guard)', !(await c.match(EDITION_KEY))); }
  // two tabs racing warmEdition (two activates against one cache)
  { const {caches,handlers}=freshEnv();
    let n=0; NET={ get '/'(){n++; return edResp(FIXED_TODAY);}, __default__:'THROW'};
    await Promise.all([activate(handlers), activate(handlers)]);
    const c=await caches.open('newslens-v1'); const slot=await c.match(EDITION_KEY);
    check('two racing activations warm without crash; slot has an edition',
          slot && slot.headers.get('X-NewsLens-Edition-Date')===FIXED_TODAY, `warm fetches=${n}`); }
}

console.log('====== SW behavior harness (real sw.js bytes) ======');
await lawTests(); await apiTests(); await todayFirstTests(); await slotTests();
await archiveOfflineTests(); await activateTests(); await secondSet();
console.log(`\n====== ${pass}/${pass+fail} branches green; ${fail} failing ======`);
process.exit(fail? 1:0);
