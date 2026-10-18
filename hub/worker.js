import PANEL from './panel.html';

// Public code contains no tenant credentials. Deployment secrets are mandatory.
const API_PATHS = new Set(['/api/config', '/api/nodes', '/api/countries', '/api/switch', '/api/proxies', '/api/links', '/api/status']);
const enc = new TextEncoder();
const json = (value, status = 200) => new Response(JSON.stringify(value), {status, headers: {'Content-Type': 'application/json; charset=utf-8', 'Cache-Control': 'no-store', 'X-Content-Type-Options':'nosniff'}});
const hex = bytes => Array.from(bytes, x => x.toString(16).padStart(2, '0')).join('');
const random = () => hex(crypto.getRandomValues(new Uint8Array(32)));
const sha = async text => hex(new Uint8Array(await crypto.subtle.digest('SHA-256', enc.encode(text))));
async function equal(a, b) {
    // Compare fixed length hashes. Never compare passwords by prefix or substring.
    const x = await sha(a || ''), y = await sha(b || '');
    let difference = 0;
    for (let n = 0; n < x.length; n++) difference |= x.charCodeAt(n) ^ y.charCodeAt(n);
    return difference === 0;
}
function basic(user, password) { return 'Basic ' + btoa(user + ':' + password); }
function secretKey(env) {
    const raw = Uint8Array.from(atob(env.CREDENTIAL_KEY), x => x.charCodeAt(0));
    if (raw.length !== 32) throw Error('invalid key');
    return crypto.subtle.importKey('raw', raw, 'AES-GCM', false, ['encrypt', 'decrypt']);
}
async function seal(env, value, machineId) {
    const iv = crypto.getRandomValues(new Uint8Array(12));
    const encrypted = new Uint8Array(await crypto.subtle.encrypt({name: 'AES-GCM', iv, additionalData: enc.encode(machineId)}, await secretKey(env), enc.encode(JSON.stringify(value))));
    return btoa(String.fromCharCode(...iv, ...encrypted));
}
async function unseal(env, value, machineId) {
    const bytes = Uint8Array.from(atob(value), x => x.charCodeAt(0));
    return JSON.parse(new TextDecoder().decode(await crypto.subtle.decrypt({name: 'AES-GCM', iv: bytes.slice(0, 12), additionalData: enc.encode(machineId)}, await secretKey(env), bytes.slice(12))));
}
async function body(request) {
    if (Number(request.headers.get('Content-Length')) > 65536) throw Error('request too large');
    const raw = await request.text();
    if (enc.encode(raw).length > 65536) throw Error('request too large');
    return JSON.parse(raw);
}
async function limitedText(response, maximum=1048576) {
    if(Number(response.headers.get('Content-Length'))>maximum)throw Error('machine response too large');
    if(!response.body)return '';
    const reader=response.body.getReader(),decoder=new TextDecoder();let size=0,text='';
    try{while(true){const {done,value}=await reader.read();if(done)break;size+=value.length;
        if(size>maximum){await reader.cancel();throw Error('machine response too large')}
        text+=decoder.decode(value,{stream:true});}
        return text+decoder.decode();}
    finally{reader.releaseLock();}
}
function machineInput(data, env) {
    const origin = new URL(data.panel_origin);
    const zones = (env.ALLOWED_PANEL_ZONES || '').split(',').map(s => s.trim()).filter(Boolean);
    if (origin.protocol !== 'https:' || origin.username || origin.password || origin.pathname !== '/' || origin.search || origin.hash ||
        !zones.some(zone => origin.hostname.endsWith('.' + zone))) throw Error('invalid panel origin');
    if (typeof data.username !== 'string' || typeof data.password !== 'string' || !data.username || data.password.length < 16 ||
        data.username.includes(':') || data.username.length > 128 || data.password.length > 256) throw Error('invalid credentials');
    return {origin: origin.origin, credentials: {username: data.username, password: data.password}};
}
async function authenticate(request, env) {
    return equal(request.headers.get('Authorization'), basic(env.ADMIN_USER, env.ADMIN_PASSWORD));
}
function safePayload(path, data) {
    const ip = value => typeof value === 'string' && (/^(?:\d{1,3}\.){3}\d{1,3}$/.test(value) && value.split('.').every(n=>Number(n)<=255) || /^[a-fA-F0-9:]{3,45}$/.test(value)) ? value : '';
    const country = value => typeof value === 'string' && /^[A-Z]{2}$/.test(value) ? value : '';
    const integer = (value, fallback=0) => Number.isInteger(value) && value >= 0 && value <= 2147483647 ? value : fallback;
    const detail = item => ({country:country(item?.country),node_ip:ip(item?.node_ip),port:integer(item?.port)});
    if(path==='/api/countries')return Array.isArray(data)?data.filter(country):[];
    if(path==='/api/config') {
        if(!data || typeof data.slot_map!=='object' || !Number.isInteger(data.proxy_port) || data.proxy_port<1 || data.proxy_port>65535)throw Error('invalid machine config');
        const slots=Object.fromEntries(Object.entries(data.slot_map).filter(([key,value])=>/^\d{1,2}$/.test(key)&&country(value)));
        if(!Object.keys(slots).length)throw Error('invalid machine country');
        return {slot_map:slots,force_switch:Object.fromEntries(Object.keys(slots).map(k=>[k,Number.isSafeInteger(data.force_switch?.[k])?data.force_switch[k]:0])),
            proxy_port:data.proxy_port,mode:data.mode==='manual'?'manual':'auto',manual_node_ip:ip(data.manual_node_ip),youtube_check:data.youtube_check===true,
            config_fetch_interval:integer(data.config_fetch_interval,15),heartbeat_interval:integer(data.heartbeat_interval,30),frontend_poll_interval:integer(data.frontend_poll_interval,5)};
    }
    if(path==='/api/nodes')return (Array.isArray(data)?data:[]).slice(0,256).map(row=>{
        let details=[];try{const parsed=JSON.parse(row.details||'[]');if(Array.isArray(parsed))details=parsed.slice(0,64).map(detail).filter(d=>d.country&&d.node_ip&&d.port>0&&d.port<=65535)}catch{}
        return {ip:ip(row.ip)||'未知',details:JSON.stringify(details),
            candidates:(Array.isArray(row.candidates)?row.candidates:[]).slice(0,1000).map(n=>({ip:ip(n.ip),country:country(n.country),ping:integer(n.ping),speed:integer(n.speed)})).filter(n=>n.ip&&n.country),
            log:typeof row.log==='string'?row.log.slice(-24000):'',last_seen:Number.isSafeInteger(row.last_seen)?Math.max(0,row.last_seen):0};
    });
    return data;
}
async function register(request, env) {
    const data = await body(request), input = machineInput(data, env);
    const code = request.headers.get('Authorization')?.replace(/^Bearer /, '') || '';
    if (!/^[a-f0-9]{64}$/.test(code)) return json({error: '接入码无效'}, 403);
    // Atomic consumption: one enrollment can register exactly one machine.
    const entry = await env.DB.prepare('UPDATE ip_enrollments SET used=1 WHERE code_hash=?1 AND used=0 AND expires_at>?2 RETURNING machine_id,label')
        .bind(await sha(code), Date.now()).first();
    if (!entry) return json({error: '接入码已使用、已过期或无效'}, 403);
    const token = random();
    await env.DB.prepare('INSERT INTO ip_machines (id,label,panel_origin,credentials,token_hash,updated_at) VALUES (?1,?2,?3,?4,?5,?6)')
        .bind(entry.machine_id, entry.label, input.origin, await seal(env, input.credentials, entry.machine_id), await sha(token), Date.now()).run();
    return json({machine_id: entry.machine_id, machine_token: token}, 201);
}
async function refresh(request, env) {
    const token = request.headers.get('Authorization')?.replace(/^Bearer /, '') || '';
    if (!/^[a-f0-9]{64}$/.test(token)) return json({error: '机器授权无效'}, 403);
    const machine = await env.DB.prepare('SELECT * FROM ip_machines WHERE token_hash=?1').bind(await sha(token)).first();
    if (!machine) return json({error: '机器授权已撤销或无效'}, 403);
    if (request.method === 'DELETE') {
        await removeMachineResources(env, machine);
        await env.DB.prepare('DELETE FROM ip_machines WHERE id=?1').bind(machine.id).run();
        return json({ok: true});
    }
    const input = machineInput(await body(request), env);
    await env.DB.prepare('UPDATE ip_machines SET panel_origin=?1,credentials=?2,updated_at=?3 WHERE id=?4')
        .bind(input.origin, await seal(env, input.credentials, machine.id), Date.now(), machine.id).run();
    return json({ok: true, machine_id: machine.id});
}
function renderPage(origin, env) {
    const escape = s => String(s).replace(/[&<>"']/g, c => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[c]));
    const fields = {'__PANEL_URL__': origin, '__PANEL_USER__': '统一后台', '__PANEL_PASS__': '使用统一登录凭据',
                    '__SOCKS_USER__': '在对应 VPS 查看', '__SOCKS_PASS__': '在对应 VPS 查看'};
    let page = PANEL.replace('同步到所有已纳管 VPS', '只同步到当前所选 VPS').replace('保存并全局下发', '保存到所选机器');
    for (const [key, value] of Object.entries(fields)) page = page.split(key).join(escape(value));
    const selector = '<div class="mt-4 flex flex-wrap items-center gap-3"><label for="machine-picker" class="text-cyan-300 text-sm">管理机器</label><select id="machine-picker" disabled class="bg-slate-800 border border-slate-600 text-white rounded px-3 py-2"><option>正在读取机器列表…</option></select><button id="add-machine" class="bg-cyan-700 text-white rounded px-3 py-2 text-sm">新增机器</button><span id="machine-state" class="text-sm text-slate-400"></span></div><dialog id="install-dialog" class="bg-slate-900 text-white rounded-xl p-6 border border-slate-600 max-w-3xl w-full"><h2 class="text-xl mb-3">私人安装命令</h2><p class="text-sm text-slate-300 mb-3">只在你自己的 VPS 执行。授权 15 分钟内有效，仅限一台机器。运行后按回车自动安装；完成后刷新本页即可选择该机器。</p><textarea id="install-command" readonly rows="8" class="w-full bg-slate-800 text-cyan-200 rounded p-3 text-sm"></textarea><p id="install-message" class="text-sm text-slate-300 my-3"></p><button id="copy-install" class="bg-cyan-700 rounded px-4 py-2">复制命令</button><button id="close-install" class="ml-3 border border-slate-600 rounded px-4 py-2">关闭</button></dialog>';
    page = page.replace('</h1>', '</h1>' + selector);
    // Install before the original page's polling starts. The existing page and controls stay intact.
    const script = `<script>
    (() => {
      const originalFetch=window.fetch.bind(window), pending=new Set();
      let selected='', writes=0, configReady=false;
      document.getElementById('add-machine').addEventListener('click',async()=>{
        const label=prompt('给新机器起个名称（例如：美国新机）');if(!label?.trim())return;
        const button=document.getElementById('add-machine');button.disabled=true;
        try{const r=await originalFetch('/api/enrollments',{method:'POST',headers:{'Content-Type':'application/json','X-Panel-Action':'1'},body:JSON.stringify({label:label.trim()})});
          const data=await r.json();if(!r.ok)throw Error(data.error||'生成失败');
          document.getElementById('install-command').value=data.install_command;
          document.getElementById('install-message').textContent='请勿公开此命令，过期后重新生成。';document.getElementById('install-dialog').showModal();
        }catch(e){alert(e.message||'生成失败，请重新登录后重试')}finally{button.disabled=false}
      });
      document.getElementById('close-install').addEventListener('click',()=>document.getElementById('install-dialog').close());
      document.getElementById('copy-install').addEventListener('click',async()=>{const area=document.getElementById('install-command');
        try{await navigator.clipboard.writeText(area.value);document.getElementById('install-message').textContent='已复制，粘贴到新 VPS 执行即可。'}
        catch{area.focus();area.select();document.getElementById('install-message').textContent='请按 Ctrl+C 复制已选中的命令。'}});
      const updateProxyLinks=()=>document.querySelectorAll('a[href^="/api/proxies"]').forEach(a=>a.href='/api/proxies?machine='+encodeURIComponent(selected));
      const loading=()=>{configReady=false;document.querySelector('button[onclick="saveConfig()"]').disabled=true;
        document.getElementById('config-form').textContent='正在读取所选机器配置…';document.getElementById('nodes-table').replaceChildren();
        document.getElementById('remote-log').textContent='';document.getElementById('machine-state').textContent='正在连接所选机器…';};
      const ready=originalFetch('/api/machines').then(r=>{if(!r.ok)throw Error('登录或机器列表读取失败');return r.json()}).then(items=>{
        const picker=document.getElementById('machine-picker');picker.replaceChildren();
        for(const m of items){const option=document.createElement('option');option.value=m.id;option.textContent=m.label;picker.append(option)}
        const previous=localStorage.getItem('ip-machine');selected=items.some(m=>m.id===previous)?previous:(items[0]?.id||'');
        picker.value=selected;picker.disabled=!selected;updateProxyLinks();if(selected)loading();
        document.getElementById('machine-state').textContent=selected?'修改仅作用于所选机器':'暂无机器，请先在管理接口创建一次性接入码';
        picker.addEventListener('change',()=>{if(writes)return;for(const c of pending)c.abort();pending.clear();selected=picker.value;localStorage.setItem('ip-machine',selected);updateProxyLinks();loading();
          if(typeof loadConfig==='function')loadConfig();if(typeof fetchNodes==='function')fetchNodes();if(typeof fetchCountries==='function')fetchCountries();});
      }).catch(()=>{document.getElementById('machine-state').textContent='列表读取失败，请重新登录或刷新页面';});
      window.fetch=async(input,init={})=>{
        const url=new URL(typeof input==='string'?input:input.url,location.href);
        if(url.origin!==location.origin||!url.pathname.startsWith('/api/'))return originalFetch(input,init);
        await ready;if(!selected)throw Error('请先接入一台机器');
        const target=selected,headers=new Headers(init.headers||(input instanceof Request?input.headers:undefined));headers.set('X-IP-Machine',target);
        const method=(init.method||(input instanceof Request?input.method:'GET')).toUpperCase(),isWrite=method!=='GET';
        const controller=new AbortController(),picker=document.getElementById('machine-picker');
        if(isWrite&&!configReady)throw Error('所选机器配置尚未加载，不能保存');
        if(isWrite){writes++;picker.disabled=true}else pending.add(controller);
        try{const response=await originalFetch(input,{...init,headers,signal:controller.signal});
          if(method==='GET'&&url.pathname==='/api/config'&&target===selected){
            if(response.ok&&(await response.clone().json()).slot_map){configReady=true;document.querySelector('button[onclick="saveConfig()"]').disabled=false;
              document.getElementById('machine-state').textContent='修改仅作用于所选机器';}
            else{loading();document.getElementById('config-form').textContent='无法读取所选机器配置，请检查连接';throw Error('所选机器配置读取失败');}}
          return response;}
        finally{pending.delete(controller);if(isWrite){writes--;picker.disabled=!!writes}}
      };
    })();</script>`;
    page = page.replace('<script>', script + '<script>');
    return new Response(page, {headers: {'Content-Type': 'text/html; charset=utf-8', 'Cache-Control': 'no-store',
        'X-Content-Type-Options': 'nosniff', 'X-Frame-Options': 'DENY',
        'Content-Security-Policy': "default-src 'self'; script-src 'self' 'unsafe-inline' https://cdn.tailwindcss.com; style-src 'self' 'unsafe-inline'; img-src 'self' data:"}});
}
async function proxyMachine(request, env, url) {
    const id = request.headers.get('X-IP-Machine') || (request.method === 'GET' ? url.searchParams.get('machine') : null);
    if (!id) return json({error: '请先选择机器'}, 400);
    if (request.method !== 'GET' && (request.method !== 'POST' || !['/api/config', '/api/switch'].includes(url.pathname) || request.headers.get('X-Panel-Action') !== '1'))
        return json({error: '不允许的操作'}, 403);
    const headers = new Headers({'X-Panel-Action': '1', 'Content-Type': 'application/json', 'User-Agent':'Mozilla/5.0 (compatible; ip-hub/1.0)'});
    const payload = request.method === 'POST' ? JSON.stringify(await body(request)) : undefined;
    let response;
    if (id === 'legacy' && env.LEGACY) {
        headers.set('Authorization', basic(env.LEGACY_USER, env.LEGACY_PASSWORD));
        response = await env.LEGACY.fetch(new Request('https://legacy.invalid' + url.pathname, {method: request.method, headers, body: payload, redirect: 'manual'}));
    } else {
        const machine = await env.DB.prepare('SELECT id,panel_origin,credentials FROM ip_machines WHERE id=?1').bind(id).first();
        if (!machine) return json({error: '机器不存在或已移除'}, 404);
        const credentials = await unseal(env, machine.credentials, machine.id);
        // Validate again after DB reads to prevent an edited record becoming an SSRF target.
        machineInput({panel_origin: machine.panel_origin, ...credentials}, env);
        headers.set('Authorization', basic(credentials.username, credentials.password));
        response = await fetch(machine.panel_origin + url.pathname, {method: request.method, headers, body: payload,
            redirect: 'manual', signal: AbortSignal.timeout(25000)});
    }
    if (response.status >= 300 && response.status < 400) return json({error: '机器返回重定向，已阻止'}, 502);
    // Never forward remote authentication challenges, cookies, redirects, or arbitrary headers.
    if (response.status === 401 || response.status === 403) return json({error: '机器面板授权失效，请在 VPS 重新登记'}, 502);
    if(url.pathname==='/api/proxies')return new Response(await limitedText(response,65536),{status:response.status,headers:{
        'Content-Type':'text/plain; charset=utf-8','Cache-Control':'no-store','X-Content-Type-Options':'nosniff','Content-Security-Policy':"default-src 'none'"}});
    // A compromised VPS is an untrusted JSON source. The original page uses
    // innerHTML, so validate all values it interpolates, including inline handlers.
    const raw=await limitedText(response);
    if(request.method==='POST')return json({ok:response.ok,...(!response.ok?{error:'所选机器未能保存配置'}:{})},response.status);
    const data=JSON.parse(raw);
    return json(request.method==='GET'?safePayload(url.pathname,data):data,response.status);
}

async function cf(env, method, path, payload) {
    if (!env.CF_API_TOKEN || !env.CF_ACCOUNT_ID || !env.CF_ZONE_ID || !env.CF_ZONE_NAME) throw Error('private CF not configured');
    const response = await fetch('https://api.cloudflare.com/client/v4' + path, {method,
        headers: {Authorization: 'Bearer ' + env.CF_API_TOKEN, 'Content-Type': 'application/json'},
        body: payload === undefined ? undefined : JSON.stringify(payload), signal: AbortSignal.timeout(25000)});
    const value = await response.json();
    if (!response.ok || !value.success) throw Error('private CF operation failed');
    return value.result;
}
async function machineByToken(request, env) {
    const token = request.headers.get('Authorization')?.replace(/^Bearer /, '') || '';
    if (!/^[a-f0-9]{64}$/.test(token)) return null;
    return env.DB.prepare('SELECT * FROM ip_machines WHERE token_hash=?1').bind(await sha(token)).first();
}
async function saveResources(env, machine, resources) {
    await env.DB.prepare('UPDATE ip_machines SET resources=?1 WHERE id=?2').bind(JSON.stringify(resources), machine.id).run();
    machine.resources = JSON.stringify(resources);
}
async function privateDns(env, machine, resources, hostname, type, content, proxied) {
    const base = '/zones/' + env.CF_ZONE_ID + '/dns_records';
    const matches = await cf(env, 'GET', base + '?name=' + encodeURIComponent(hostname));
    const marker = 'ip-private-machine=' + machine.id;
    const saved = resources.dns?.[hostname];
    let record = matches.find(r => r.id === saved?.id || (r.comment === marker && r.type === type && r.content === content));
    if (matches.length && !record) throw Error('DNS conflict');
    if (record && (record.type !== type || record.content !== content || record.proxied !== proxied)) throw Error('DNS changed');
    if (record && record.comment !== marker) throw Error('DNS ownership changed');
    if (!record) record = await cf(env, 'POST', base, {type, name: hostname, content, proxied, ttl: 1, comment: marker});
    (resources.dns ||= {})[hostname] = {id: record.id, type, content};
    await saveResources(env, machine, resources);
}
async function provision(request, env) {
    const data = await body(request);
    if (!env.CF_API_TOKEN || !env.CF_ACCOUNT_ID || !env.CF_ZONE_ID || !env.CF_ZONE_NAME) return json({error: '私人资源服务尚未配置'}, 503);
    const ports = data.ports;
    if (!ports || ['panel','tcp','udp','home'].some(r => !Number.isInteger(ports[r]) || ports[r] < 1024 || ports[r] > 65535)) return json({error: '本机端口无效'}, 400);
    let machine = await machineByToken(request, env), token = request.headers.get('Authorization')?.replace(/^Bearer /, '');
    if (!machine) {
        if (!/^[a-f0-9]{64}$/.test(token || '')) return json({error: '缺少有效的私人授权'}, 403);
        // Validate credentials before consuming an enrollment.
        machineInput({panel_origin: 'https://validation.' + env.CF_ZONE_NAME + '/', username: data.username, password: data.password}, env);
        const enrollment = await env.DB.prepare('UPDATE ip_enrollments SET used=1 WHERE code_hash=?1 AND used=0 AND expires_at>?2 RETURNING machine_id,label')
            .bind(await sha(token), Date.now()).first();
        if (!enrollment) return json({error: '私人授权已使用、已过期或无效'}, 403);
        token = random();
        machine = {id: enrollment.machine_id, label: enrollment.label, resources: '{}'};
        const origin = 'https://panel-' + machine.id.slice(2) + '.' + env.CF_ZONE_NAME;
        await env.DB.prepare('INSERT INTO ip_machines (id,label,panel_origin,credentials,token_hash,updated_at,resources) VALUES (?1,?2,?3,?4,?5,?6,?7)')
            .bind(machine.id, machine.label, origin, await seal(env, {username: data.username, password: data.password}, machine.id), await sha(token), Date.now(), '{}').run();
    }
    const resources = JSON.parse(machine.resources || '{}');
    try {
        const base = '/accounts/' + env.CF_ACCOUNT_ID + '/cfd_tunnel';
        if (!resources.tunnel_id) {
            const tunnel = await cf(env, 'POST', base, {name: 'ip-private-' + machine.id, config_src: 'cloudflare'});
            resources.tunnel_id = tunnel.id; await saveResources(env, machine, resources);
        } else {
            const tunnel = await cf(env, 'GET', base + '/' + resources.tunnel_id);
            if (tunnel.name !== 'ip-private-' + machine.id) throw Error('tunnel ownership mismatch');
        }
        const hosts = Object.fromEntries(['panel','tcp','udp','home'].map(r => [r, r + '-' + machine.id.slice(2) + '.' + env.CF_ZONE_NAME]));
        await cf(env, 'PUT', base + '/' + resources.tunnel_id + '/configurations', {config: {ingress: [
            ...Object.entries(hosts).map(([r, hostname]) => ({hostname, service: 'http://127.0.0.1:' + ports[r]})), {service: 'http_status:404'}]}});
        for (const hostname of Object.values(hosts)) await privateDns(env, machine, resources, hostname, 'CNAME', resources.tunnel_id + '.cfargotunnel.com', true);
        let directHost;
        if (typeof data.external_address === 'string' && /^[a-zA-Z0-9.-]+$/.test(data.external_address) && data.external_address.includes('.') &&
            Number.isInteger(data.panel_external_port) && data.panel_external_port > 0 && data.panel_external_port <= 65535) {
            directHost = 'direct-' + machine.id.slice(2) + '.' + env.CF_ZONE_NAME;
            await privateDns(env, machine, resources, directHost, /^\d+\.\d+\.\d+\.\d+$/.test(data.external_address) ? 'A' : 'CNAME', data.external_address, false);
            resources.direct_host = directHost; await saveResources(env, machine, resources);
        }
        const runToken = await cf(env, 'GET', base + '/' + resources.tunnel_id + '/token');
        return json({ok: true, machine_id: machine.id, machine_token: token, hosts, tunnel_token: runToken, direct_host: directHost});
    } catch {
        // Preserve the per-machine token even if provisioning is partial, so retries
        // do not consume another enrollment or create a different machine.
        return json({ok: false, machine_id: machine.id, machine_token: token, error: '私人资源配置未完成，可使用已保存的机器授权重试'});
    }
}
async function dnsChallenge(request, env) {
    const machine = await machineByToken(request, env);
    if (!machine) return json({error: '机器授权无效'}, 403);
    const resources = JSON.parse(machine.resources || '{}'), data = await body(request);
    if (!resources.direct_host || !['present','cleanup'].includes(data.action) || !/^[A-Za-z0-9_-]{43,128}$/.test(data.value || ''))
        return json({error: '证书验证请求无效'}, 400);
    const hostname = '_acme-challenge.' + resources.direct_host;
    const base = '/zones/' + env.CF_ZONE_ID + '/dns_records';
    const own = (resources.challenges ||= {});
    if (data.action === 'present' && !own[data.value]) {
        if (Object.keys(own).length >= 4) return json({error:'本机证书验证数量已达上限'},429);
        const record = await cf(env, 'POST', base, {type:'TXT',name:hostname,content:data.value,ttl:120,comment:'ip-private-machine='+machine.id});
        own[data.value] = record.id;
    } else if (data.action === 'cleanup' && own[data.value]) {
        const record = await cf(env, 'GET', base + '/' + own[data.value]);
        if (record.name !== hostname || record.type !== 'TXT' || record.content !== data.value || record.comment !== 'ip-private-machine=' + machine.id) throw Error('challenge ownership mismatch');
        await cf(env, 'DELETE', base + '/' + record.id); delete own[data.value];
    }
    await saveResources(env, machine, resources);
    return json({ok:true});
}
async function removeMachineResources(env, machine) {
    const resources = JSON.parse(machine.resources || '{}');
    for (const [value, id] of Object.entries(resources.challenges || {})) {
        const base = '/zones/' + env.CF_ZONE_ID + '/dns_records/';
        const record = await cf(env, 'GET', base + id);
        if (record.type !== 'TXT' || record.content !== value || record.name !== '_acme-challenge.' + resources.direct_host || record.comment !== 'ip-private-machine=' + machine.id) throw Error('DNS changed');
        await cf(env, 'DELETE', base + id); delete resources.challenges[value]; await saveResources(env, machine, resources);
    }
    for (const [hostname, own] of Object.entries(resources.dns || {})) {
        const base = '/zones/' + env.CF_ZONE_ID + '/dns_records/';
        const record = await cf(env, 'GET', base + own.id);
        if (record.name !== hostname || record.type !== own.type || record.content !== own.content || record.comment !== 'ip-private-machine=' + machine.id) throw Error('DNS changed');
        await cf(env, 'DELETE', base + own.id); delete resources.dns[hostname]; await saveResources(env, machine, resources);
    }
    if (resources.tunnel_id) {
        const base = '/accounts/' + env.CF_ACCOUNT_ID + '/cfd_tunnel/' + resources.tunnel_id;
        const tunnel = await cf(env, 'GET', base);
        if (tunnel.name !== 'ip-private-' + machine.id) throw Error('tunnel changed');
        // Revoke connectors before deleting; this is scoped to this owned tunnel.
        await cf(env, 'DELETE', base + '/connections'); await cf(env, 'DELETE', base);
        delete resources.tunnel_id; await saveResources(env, machine, resources);
    }
}

export async function handle(request, env) {
    if (!env.ADMIN_USER || !env.ADMIN_PASSWORD || env.ADMIN_PASSWORD.length < 20 || !env.CREDENTIAL_KEY || !env.ALLOWED_PANEL_ZONES || !env.DB)
        return json({error: '后台尚未配置，已拒绝访问'}, 503);
    const url = new URL(request.url);
    // Existing agents retain their exact config/report URLs and credentials.
    // No legacy source downloads or administrator writes bypass hub isolation.
    if(env.LEGACY && env.LEGACY_USER && env.LEGACY_PASSWORD && !request.headers.has('X-IP-Machine') &&
        ((url.pathname==='/api/config'&&request.method==='GET')||(url.pathname==='/api/report'&&request.method==='POST')) &&
        await equal(request.headers.get('Authorization'),basic(env.LEGACY_USER,env.LEGACY_PASSWORD))) {
        const headers=new Headers({'Authorization':basic(env.LEGACY_USER,env.LEGACY_PASSWORD),'Content-Type':'application/json'});
        const payload=request.method==='POST'?JSON.stringify(await body(request)):undefined;
        const response=await env.LEGACY.fetch(new Request('https://legacy.invalid'+url.pathname,{method:request.method,headers,body:payload,redirect:'manual'}));
        return new Response(await limitedText(response),{status:response.status,headers:{'Content-Type':request.method==='GET'?'application/json; charset=utf-8':'text/plain; charset=utf-8','Cache-Control':'no-store','X-Content-Type-Options':'nosniff'}});
    }
    if (request.method === 'POST' && url.pathname === '/api/provision') return provision(request, env);
    if (request.method === 'POST' && url.pathname === '/api/dns-challenge') return dnsChallenge(request, env);
    if (request.method === 'POST' && url.pathname === '/api/register') return register(request, env);
    if (['POST', 'DELETE'].includes(request.method) && url.pathname === '/api/machine') return refresh(request, env);
    if (!(await authenticate(request, env))) return new Response('需要管理员登录', {status: 401, headers: {
        'WWW-Authenticate': 'Basic realm="Residential Hub", charset="UTF-8"', 'Cache-Control': 'no-store'}});
    if (url.pathname === '/api/enrollments' && request.method === 'POST') {
        if (request.headers.get('X-Panel-Action') !== '1') return json({error: '缺少操作标记'}, 403);
        const data = await body(request), label = String(data.label || '').trim();
        if (!label || label.length > 80) return json({error: '请填写简短的机器名称'}, 400);
        const code = random(), id = 'm-' + random().slice(0, 24), expires = Date.now() + 15 * 60000;
        await env.DB.prepare('INSERT INTO ip_enrollments (code_hash,machine_id,label,expires_at,used) VALUES (?1,?2,?3,?4,0)')
            .bind(await sha(code), id, label, expires).run();
        await env.DB.prepare('DELETE FROM ip_enrollments WHERE expires_at<?1').bind(Date.now() - 86400000).run();
        const quote=s=>"'"+s.replace(/'/g,"'\\''")+"'";
        const vars='IP_HUB_URL='+quote(url.origin)+' IP_HUB_ENROLLMENT='+quote(code);
        const install_command='curl -fsSL https://raw.githubusercontent.com/mtgggxfc-eng/ip/main/jkyj.sh | { if [ "$(id -u)" -eq 0 ]; then env '+vars+' bash; else sudo env '+vars+' bash; fi; }';
        return json({code, machine_id: id, expires_at: expires, install_command}, 201);
    }
    if (url.pathname === '/api/machines' && request.method === 'GET') {
        const {results} = await env.DB.prepare('SELECT id,label,updated_at FROM ip_machines ORDER BY label,id').all();
        return json(env.LEGACY ? [{id: 'legacy', label: '原有住宅面板（旧机器）'}, ...results] : results);
    }
    if (url.pathname.startsWith('/api/machines/') && request.method === 'DELETE') {
        if (request.headers.get('X-Panel-Action') !== '1') return json({error: '缺少操作标记'}, 403);
        const id = url.pathname.slice('/api/machines/'.length);
        if (!/^m-[a-f0-9]{24}$/.test(id)) return json({error: '机器编号无效'}, 400);
        const machine = await env.DB.prepare('SELECT * FROM ip_machines WHERE id=?1').bind(id).first();
        // Revoke machine API access even if subsequent cloud cleanup is unavailable.
        if (machine) await env.DB.prepare('UPDATE ip_machines SET token_hash=?1 WHERE id=?2').bind(await sha(random()), id).run();
        if (machine) await removeMachineResources(env, machine);
        await env.DB.prepare('DELETE FROM ip_machines WHERE id=?1').bind(id).run();
        return json({ok: true});
    }
    if (url.pathname === '/' && request.method === 'GET') return renderPage(url.origin, env);
    if (API_PATHS.has(url.pathname)) return proxyMachine(request, env, url);
    return json({error: '未找到'}, 404);
}

export default {async fetch(request, env) {
    try { return await handle(request, env); }
    catch { return json({error: '操作未完成，请检查授权、机器连接或后台配置'}, 502); }
}};
