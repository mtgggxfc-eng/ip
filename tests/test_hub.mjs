import assert from 'node:assert/strict';
import {readFile,writeFile} from 'node:fs/promises';
import {webcrypto} from 'node:crypto';
globalThis.crypto ??= webcrypto;
const source = (await readFile(new URL('../hub/worker.js', import.meta.url), 'utf8'))
  .replace("import PANEL from './panel.html';", 'const PANEL=' + JSON.stringify(await readFile(new URL('../hub/panel.html', import.meta.url), 'utf8')) + ';');
const {handle} = await import('data:text/javascript;base64,' + Buffer.from(source).toString('base64'));
const machines=new Map(), enrollments=new Map();
const db={prepare(sql){return {bind(...args){this.args=args;return this},async run(){const a=this.args;
  if(sql.startsWith('INSERT INTO ip_enrollments'))enrollments.set(a[0],{machine_id:a[1],label:a[2],expires_at:a[3],used:0});
  else if(sql.startsWith('INSERT INTO ip_machines'))machines.set(a[0],{id:a[0],label:a[1],panel_origin:a[2],credentials:a[3],token_hash:a[4],updated_at:a[5],resources:a[6]||'{}'});
  else if(sql.startsWith('UPDATE ip_machines SET resources='))machines.get(a[1]).resources=a[0];
  else if(sql.startsWith('UPDATE ip_machines SET token_hash='))machines.get(a[1]).token_hash=a[0];
  else if(sql.startsWith('UPDATE ip_machines SET'))Object.assign(machines.get(a[3]),{panel_origin:a[0],credentials:a[1],updated_at:a[2]});
  else if(sql.startsWith('DELETE FROM ip_machines'))machines.delete(a[0]);
  return {success:true};},async first(){const a=this.args;
  if(sql.startsWith('UPDATE ip_enrollments')){const e=enrollments.get(a[0]);if(!e||e.used||e.expires_at<=a[1])return null;e.used=1;return e;}
  if(sql.includes('WHERE token_hash='))return [...machines.values()].find(m=>m.token_hash===a[0])||null;
  if(sql.includes('WHERE id='))return machines.get(a[0])||null;
  throw Error('unhandled query');},async all(){return {results:[...machines.values()].map(({id,label,updated_at})=>({id,label,updated_at}))}}}}};
const env={DB:db,ADMIN_USER:'admin',ADMIN_PASSWORD:'test-admin-password-at-least-20',
  CREDENTIAL_KEY:Buffer.alloc(32,7).toString('base64'),ALLOWED_PANEL_ZONES:'example.com'};
const admin='Basic '+Buffer.from(env.ADMIN_USER+':'+env.ADMIN_PASSWORD).toString('base64');
const panelPassword='private-panel-password-for-tests';
const payload={panel_origin:'https://panel-one.example.com/',username:'panel-admin',password:panelPassword};
function request(path,method='GET',auth=admin,body,headers={}){return new Request('https://hub.example.com'+path,{method,
  headers:{...(auth?{Authorization:auth}:{}),'X-Panel-Action':'1',...headers},body:body===undefined?undefined:JSON.stringify(body)})}
async function enroll(label){const r=await handle(request('/api/enrollments','POST',admin,{label}),env);assert.equal(r.status,201);return r.json()}
assert.equal((await handle(request('/', 'GET', ''),env)).status,401);
assert.equal((await handle(request('/api/machines','GET',''),env)).status,401);
assert.equal((await handle(request('/api/register','POST','Bearer '+'a'.repeat(64),payload),env)).status,403);
assert.equal((await handle(request('/api/machine','POST','Bearer '+'a'.repeat(64),payload),env)).status,403);
assert.equal((await handle(request('/api/enrollments','POST','Bearer '+'a'.repeat(64),{label:'bad'}),env)).status,401);
assert.equal((await handle(request('/', 'GET', '', undefined),{...env,ADMIN_PASSWORD:''})).status,503);
const one=await enroll('机器一');
assert(one.install_command.includes('IP_HUB_ENROLLMENT=\''+one.code+'\''));
assert(one.install_command.includes('sudo env IP_HUB_URL=\'https://hub.example.com\''));
assert(!one.install_command.includes(env.ADMIN_PASSWORD));
const legacyAuth='Basic '+Buffer.from('legacy-user:legacy-agent-secret').toString('base64');
let legacyCalls=[];
const legacyEnv={...env,LEGACY_USER:'legacy-user',LEGACY_PASSWORD:'legacy-agent-secret',LEGACY:{async fetch(req){legacyCalls.push(req);return new Response(req.method==='POST'?'OK':'{"slot_map":{"0":"US"},"proxy_port":58123}',{headers:{'Content-Type':'application/json'}})}}};
assert.equal((await handle(request('/api/config','GET',legacyAuth),legacyEnv)).status,200);
assert.equal((await handle(request('/api/report','POST',legacyAuth,{ip:'1.2.3.4'}),legacyEnv)).status,200);
assert.equal(legacyCalls.length,2);
assert.equal((await handle(request('/api/config','POST',legacyAuth,{}),legacyEnv)).status,401);
assert.equal((await handle(request('/api/report','POST','Basic wrong',{}),legacyEnv)).status,401);
assert.equal(legacyCalls.length,2);
assert.equal((await handle(request('/api/config','POST',admin,{}, {'X-IP-Machine':'legacy'}),legacyEnv)).status,200);
const [r1,r2]=await Promise.all([handle(request('/api/register','POST','Bearer '+one.code,payload),env),handle(request('/api/register','POST','Bearer '+one.code,payload),env)]);
assert.deepEqual([r1.status,r2.status].sort(),[201,403]);
const registration=await(r1.status===201?r1:r2).json();
const machine=machines.get(registration.machine_id);
assert(!machine.credentials.includes(panelPassword));assert(!machine.token_hash.includes(registration.machine_token));
const list=await(await handle(request('/api/machines'),env)).text();
assert(!list.includes(panelPassword));assert(!list.includes('panel_origin'));assert(!list.includes('token_hash'));
const page=await(await handle(request('/'),env)).text();
assert(page.includes('machine-picker'));assert(!page.includes(panelPassword));assert(!page.includes(env.ADMIN_PASSWORD));assert(!page.includes(env.CREDENTIAL_KEY));
if(process.argv[2])await writeFile(process.argv[2],page);
assert.equal((await handle(request('/api/machines','GET','Bearer '+registration.machine_token),env)).status,401);
assert.equal((await handle(request('/api/config'),env)).status,400);
const two=await enroll('机器二');
const reg2=await(await handle(request('/api/register','POST','Bearer '+two.code,{...payload,panel_origin:'https://panel-two.example.com/'}),env)).json();
await handle(request('/api/machine','POST','Bearer '+registration.machine_token,{...payload,panel_origin:'https://panel-one-new.example.com/'}),env);
assert.equal(machines.get(reg2.machine_id).panel_origin,'https://panel-two.example.com');
let forwarded=[];
globalThis.fetch=async(url,opts)=>{forwarded.push([url,opts]);return new Response('{"mode":"auto","slot_map":{"0":"US"},"proxy_port":12345}',{headers:{'Content-Type':'application/json'}})};
const proxied=await handle(request('/api/config','POST',admin,{country:'US'},{'X-IP-Machine':registration.machine_id}),env);
assert.equal(proxied.status,200);assert.equal(forwarded[0][0],'https://panel-one-new.example.com/api/config');
assert.equal((await handle(request('/api/proxies?machine='+registration.machine_id),env)).status,200);
assert.equal(forwarded[0][1].headers.get('Authorization'),'Basic '+Buffer.from('panel-admin:'+panelPassword).toString('base64'));
assert.equal((await handle(request('/api/report','POST',admin,{}, {'X-IP-Machine':registration.machine_id}),env)).status,404);
globalThis.fetch=async()=>Response.json([{ip:'<img src=x onerror=alert(1)>',details:JSON.stringify([{country:"US';alert(1)//",node_ip:'<svg onload=alert(1)>',port:'1 onclick=evil'}]),
  candidates:[{ip:"';alert(1)//",country:'US'}],log:'<raw log remains text>',last_seen:1}]);
const sanitized=await(await handle(request('/api/nodes','GET',admin,undefined,{'X-IP-Machine':registration.machine_id}),env)).json();
assert.equal(sanitized[0].ip,'未知');assert.equal(sanitized[0].details,'[]');assert.deepEqual(sanitized[0].candidates,[]);
globalThis.fetch=async()=>new Response('<script>evil()</script>',{headers:{'Content-Type':'text/html'}});
assert.equal((await handle(request('/api/proxies?machine='+registration.machine_id),env)).headers.get('Content-Type'),'text/plain; charset=utf-8');
globalThis.fetch=async()=>new Response('',{status:302,headers:{Location:'http://127.0.0.1'}});
assert.equal((await handle(request('/api/config','GET',admin,undefined,{'X-IP-Machine':registration.machine_id}),env)).status,502);
const bad=await enroll('无效地址');
await assert.rejects(handle(request('/api/register','POST','Bearer '+bad.code,{...payload,panel_origin:'http://127.0.0.1/'}),env));
await handle(request('/api/machines/'+registration.machine_id,'DELETE'),env);
assert.equal((await handle(request('/api/machine','POST','Bearer '+registration.machine_token,payload),env)).status,403);
assert.equal(machines.has(reg2.machine_id),true);
console.log('Hub: login, fail-closed configuration, single-use enrollment, encrypted credentials, machine isolation, request routing, redirect rejection and revocation passed.');

const privateEnv={...env,CF_API_TOKEN:'test-account-token-never-delivered-to-vps',CF_ACCOUNT_ID:'account',CF_ZONE_ID:'zone',CF_ZONE_NAME:'example.com'};
const tunnels=new Map(),dns=new Map();let cfCalls=0;
globalThis.fetch=async(url,opts={})=>{
  cfCalls++;assert(String(url).startsWith('https://api.cloudflare.com/client/v4/'));
  assert.equal(opts.headers.Authorization,'Bearer '+privateEnv.CF_API_TOKEN);
  const path=new URL(url).pathname,method=opts.method,payload=opts.body?JSON.parse(opts.body):null;let result;
  if(path.endsWith('/cfd_tunnel')&&method==='POST'){const id='t-'+(tunnels.size+1);result={id,name:payload.name};tunnels.set(id,result);}
  else if(path.includes('/cfd_tunnel/')){
    const bits=path.split('/cfd_tunnel/')[1].split('/'),t=tunnels.get(bits[0]);assert(t);
    if(bits[1]==='configurations'){assert(payload.config.ingress.slice(0,-1).every(i=>i.service.startsWith('http://127.0.0.1:')));result={};}
    else if(bits[1]==='token')result='test-runtime-token-only-for-'+bits[0];
    else if(bits[1]==='connections'){assert.equal(method,'DELETE');result={};}
    else if(method==='DELETE'){tunnels.delete(bits[0]);result={};}else result=t;
  }else if(path.endsWith('/dns_records')&&method==='GET')result=[...dns.values()].filter(r=>r.name===new URL(url).searchParams.get('name'));
  else if(path.endsWith('/dns_records')&&method==='POST'){result={...payload,id:'dns-'+(dns.size+1)};dns.set(result.id,result);}
  else if(path.includes('/dns_records/')){const id=path.split('/dns_records/')[1];result=dns.get(id);assert(result);if(method==='DELETE')dns.delete(id);}
  else throw Error('unexpected CF endpoint');
  return Response.json({success:true,result});
};
const provisionData={username:'panel-admin',password:panelPassword,ports:{panel:30100,tcp:30101,udp:30102,home:30103},external_address:'worker.example.net',panel_external_port:17777};
const beforeBad=cfCalls;
assert.equal((await handle(request('/api/provision','POST','Bearer '+'e'.repeat(64),provisionData),privateEnv)).status,403);
assert.equal(cfCalls,beforeBad);
const enrollment=await enroll('私人机器');
const provisioned=await(await handle(request('/api/provision','POST','Bearer '+enrollment.code,provisionData),privateEnv)).json();
assert(provisioned.ok);assert(provisioned.tunnel_token.startsWith('test-runtime-token-only-for-'));
assert(!JSON.stringify(provisioned).includes(privateEnv.CF_API_TOKEN));assert.equal(tunnels.size,1);
assert.equal((await handle(request('/api/provision','POST','Bearer '+enrollment.code,provisionData),privateEnv)).status,403);
const retry=await(await handle(request('/api/provision','POST','Bearer '+provisioned.machine_token,provisionData),privateEnv)).json();
assert(retry.ok);assert.equal(tunnels.size,1);assert.equal(retry.machine_id,provisioned.machine_id);
const challenge='a'.repeat(43);
assert.equal((await handle(request('/api/dns-challenge','POST','Bearer '+reg2.machine_token,{action:'present',value:challenge}),privateEnv)).status,400);
await handle(request('/api/dns-challenge','POST','Bearer '+provisioned.machine_token,{action:'present',value:challenge,hostname:'someone-else.example.com'}),privateEnv);
assert([...dns.values()].some(r=>r.type==='TXT'&&r.name==='_acme-challenge.'+provisioned.direct_host));
assert(![...dns.values()].some(r=>r.name==='someone-else.example.com'));
await handle(request('/api/dns-challenge','POST','Bearer '+provisioned.machine_token,{action:'cleanup',value:challenge}),privateEnv);
assert(![...dns.values()].some(r=>r.type==='TXT'));
await handle(request('/api/machines/'+provisioned.machine_id,'DELETE'),privateEnv);
assert.equal(tunnels.size,0);assert.equal(dns.size,0);assert(machines.has(reg2.machine_id));
assert.equal((await handle(request('/api/provision','POST','Bearer '+provisioned.machine_token,provisionData),privateEnv)).status,403);
console.log('Private resources: unauthorized provisioning blocked, enrollment single-use, account token never returned, retries idempotent, DNS challenge scoped to the same machine, own resources cleaned.');
