"""No real cloud API calls, firewall mutations or system services in this suite."""
import importlib.util
import io
import json
import os
import pathlib
import subprocess
import tempfile
import unittest
import urllib.error
from unittest.mock import patch

ROOT = pathlib.Path(__file__).resolve().parent.parent


def module(name, filename):
    spec = importlib.util.spec_from_file_location(name, ROOT / filename)
    m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m); return m


n = module('network_adapter', 'network-adapter.py')
v = module('verify_node', 'verify-node.py')
d = module('discovery_test', 'discover-existing.py')


class NetworkTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self.temp.name)
        self.root_patch = patch.object(n, 'ROOT', self.root)
        self.root_patch.start()

    def tearDown(self):
        self.root_patch.stop(); self.temp.cleanup()

    def test_auto_mapped_no_questions_or_udp_assumption(self):
        with patch.dict(os.environ, {'IP_SETUP_MODE': 'auto'}, clear=True), patch.object(n, 'mapped_hint', return_value=True), patch('builtins.open', side_effect=AssertionError('no prompt')):
            n.prepare()
        state = n.read(self.root / 'network.json')
        self.assertEqual(state['mode'], 'mapped'); self.assertFalse(state['udp_available'])

    def test_existing_instance_and_secrets_do_not_rotate(self):
        state = {'schema': 1, 'instance': 'test', 'mode': 'mapped', 'udp_available': False, 'mappings': {}}
        settings = {'proxy_port': 12345}; auth = {'socks_username': 'u', 'socks_password': 'secret'}
        first = n.ws_config(state, settings, auth)
        self.assertEqual(first, n.ws_config(state, settings, auth))
        self.assertTrue(all(i['listen'] == '127.0.0.1' for i in first['inbounds']))
        self.assertEqual(first['route']['rules'][0]['inbound'], ['ws-home'])
        self.assertEqual(first['outbounds'][1]['server'], '127.0.0.1')

    def test_mapping_rewrites_only_address_and_port(self):
        original = 'vless://user@1.2.3.4:555?security=reality&sni=www.amazon.com&pbk=key#test'
        result = n.mapped_link(original, 'worker.example.com', 8888)
        self.assertEqual(result, original.replace('1.2.3.4:555', 'worker.example.com:8888'))
        with self.assertRaises(ValueError): n.mapped_link(original, 'bad/host', 8888)

    def test_failed_candidates_never_selected(self):
        candidates = {'tcp': ['bad-direct', 'good-ws'], 'udp': ['bad-hy2', 'good-trojan'], 'home': ['bad-home']}
        selected, reports = n.choose_verified(candidates, lambda uri: {'ok': uri.startswith('good')})
        self.assertEqual(selected, {'tcp': 'good-ws', 'udp': 'good-trojan'})
        self.assertEqual(len(reports), 5)

    def test_share_uri_uses_tls_without_insecure(self):
        state = {}; n.ws_config(state, {'proxy_port': 12345}, {'socks_username': 'u', 'socks_password': 'p'})
        for role in ('tcp', 'udp', 'home'):
            uri = n.ws_uri(role, state['ws'][role], 'node.example.com')
            parsed = v.outbound_for(uri)
            self.assertTrue(parsed['tls']['enabled']); self.assertNotIn('insecure', parsed['tls'])
            self.assertEqual(parsed['transport']['path'], state['ws'][role]['path'])
            self.assertEqual(parsed['type'], 'trojan' if role == 'udp' else 'vless')

    def test_hy2_does_not_silently_skip_authentication(self):
        with patch.dict(os.environ, {}, clear=True), self.assertRaises(ValueError):
            v.outbound_for('hysteria2://password@example.com:443/?insecure=1&pinSHA256=ab')

    def test_dns_does_not_overwrite_somebody_elses_record(self):
        class CF:
            def call(self, method, path, body=None):
                if method != 'GET': raise AssertionError('no mutation allowed')
                return [{'id': 'unrelated', 'content': 'elsewhere', 'type': 'A'}]
        with self.assertRaises(ValueError):
            n.ensure_dns(CF(), {'zone_id': 'zone'}, {'instance': 'own'}, 'host.example.com', 'A', '1.2.3.4', False)

    def test_cloud_cleanup_checks_saved_ownership(self):
        n.write(self.root / 'network.json', {'instance': 'own', 'dns': {'host.example.com': {'id': 'owned', 'type': 'CNAME', 'content': 'own.cfargotunnel.com'}}})
        n.write(self.root / 'cloudflare.json', {'api_token': 'test', 'zone_id': 'zone'})
        with patch.object(n.Cloudflare, 'call', return_value=[{'id': 'owned', 'type': 'CNAME', 'content': 'changed.example.com'}]) as api:
            with self.assertRaises(ValueError): n.cleanup_cloudflare()
            self.assertEqual(api.call_count, 1); self.assertEqual(api.call_args.args[0], 'GET')

    def test_host_firewall_never_flushes_and_rules_are_idempotent(self):
        calls = []
        def runner(args, check=True, timeout=60):
            calls.append(args)
            return subprocess.CompletedProcess(args, 0, 'Status: active' if args == ['ufw', 'status'] else '', '')
        with patch.object(n.shutil, 'which', side_effect=lambda name: '/usr/bin/' + name if name == 'ufw' else None), patch.object(n, 'run', side_effect=runner):
            report = n.firewall([('tcp', 443), ('tcp', 443), ('udp', 444)])
        self.assertEqual(sum(c[:2] == ['ufw', 'allow'] for c in calls), 2)
        self.assertTrue(all(r['host_opened'] and not r['cloud_verified'] for r in report))
        self.assertFalse(any('flush' in a or '-F' in a for c in calls for a in c))

    def test_firewall_failure_is_not_reported_as_success(self):
        with patch.object(n.shutil, 'which', return_value='/bin/ufw'), patch.object(n, 'run', side_effect=RuntimeError('failed')):
            self.assertFalse(n.firewall([('tcp', 443)])[0]['host_opened'])

    def test_cloud_api_requires_authorization(self):
        with patch.dict(os.environ, {}, clear=True), patch.object(n, 'air_call', side_effect=AssertionError('no calls')):
            self.assertFalse(n.auto_air_mappings({}, {}, {'panel_port': 4444}))

    def test_mapping_file_refreshes_ports_without_prompting(self):
        state={'mode':'mapped','mappings':{}}
        native={'tcp':'vless://test@192.0.2.10:21088','home':'vless://home@192.0.2.10:47956'}
        settings={'panel_port':45062}
        self.assertFalse(n.load_mapping_table(state,native,settings))
        path=self.root/'port-mappings.json';table=n.read(path)
        table.update(external_address='worker.example.com',tcp={'21088':11001,'47956':11002,'45062':11003})
        n.write(path,table)
        before=dict(native)
        for offset in (0,100):
            table['tcp']['21088']=11001+offset;n.write(path,table)
            self.assertTrue(n.load_mapping_table(state,native,settings))
            with patch.object(n,'ask',side_effect=AssertionError('no questions')):
                n.configure_mappings(state,native,settings,discovered=True)
            self.assertEqual(state['mappings']['tcp'],11001+offset)
            self.assertEqual(state['panel_external_port'],11003)
        self.assertEqual(native,before)

    def test_platform_interface_discovery_requires_unique_mac(self):
        identifier='11111111-1111-1111-1111-111111111111'
        with patch.dict(os.environ,{},clear=True),patch.object(n,'air_local_mac',return_value='52:54:00:01:02:03'), \
             patch.object(n,'air_call',return_value={'results':[{'id':identifier,'mac_address':'52:54:00:01:02:03'}]}) as call:
            result,identity=n.resolve_air_interface('test-token',{})
            self.assertEqual(result,identifier);self.assertEqual(identity['selection'],'mac')
            self.assertIn('mac_address=',call.call_args.args[2])
        with patch.dict(os.environ,{},clear=True),patch.object(n,'air_local_mac',return_value='52:54:00:01:02:03'), \
             patch.object(n,'air_call',return_value={'results':[]}):
            with self.assertRaisesRegex(ValueError,'无法唯一匹配'):n.resolve_air_interface('test-token',{})

    def test_authorized_provider_refresh_reuses_existing_mappings(self):
        identifier='11111111-1111-1111-1111-111111111111'
        n.write(self.root/'air.json',{'token':'test','interface':identifier})
        native={'tcp':'vless://test@192.0.2.10:21088','home':'vless://home@192.0.2.10:47956'}
        state={'instance':'test','mappings':{}}
        revision=[0]
        def provider(token,method,path,*args):
            self.assertEqual(method,'GET')
            inside=int(n.urllib.parse.parse_qs(n.urllib.parse.urlsplit(path).query)['node_port'][0])
            return {'results':[{'id':'existing-'+str(inside),'interface':identifier,'node_port':inside,
                               'worker_port':inside+revision[0],'worker_fqdn':'worker.example.com'}]}
        with patch.dict(os.environ,{},clear=True),patch.object(n,'air_call',side_effect=provider):
            self.assertTrue(n.auto_air_mappings(state,native,{'panel_port':45062}))
            revision[0]=10
            self.assertTrue(n.auto_air_mappings(state,native,{'panel_port':45062}))
        self.assertEqual(state['mappings']['tcp'],21098)
        self.assertEqual(state['panel_external_port'],45072)
        self.assertFalse(state.get('air_services'))
        self.assertTrue(native['tcp'].startswith('vless://test@'))

    def test_panel_hint_uses_internal_port_with_replacement_notice(self):
        settings={'public_ip':'192.0.2.10','panel_port':45062}
        for state,expected in [({'mode':'mapped','mappings':{}},'https://192.0.2.10:45062'),
                               ({'mode':'mapped','mappings':{},'external_address':'worker.example.com'},'https://worker.example.com:45062')]:
            with patch.dict(os.environ,{},clear=True):
                result=n.concise_result(state,settings,{'username':'test','password':'test-password'},{},{},{},[],[],True,[])
            self.assertIn(expected,result)
            self.assertIn('【请将45062端口，替换成后台端口地址】',result)
            self.assertNotIn('____',result)
            self.assertNotIn('https://外部地址',result)

    def test_pipe_install_early_exit_drains_input_without_broken_pipe(self):
        source=(ROOT/'src/install.sh').read_text(encoding='utf-8')
        drain=next(line for line in source.splitlines() if line.startswith('if [[ -z "${BASH_SOURCE[0]:-}"'))
        windows_bash=pathlib.Path('C:/Program Files/Git/bin/bash.exe')
        bash=str(windows_bash) if windows_bash.exists() else 'bash'
        payload=(drain+'\nexit 2\n#'+'x'*1048576+'\n').encode()
        process=subprocess.Popen([bash],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
        try:
            process.stdin.write(payload);process.stdin.close()
            self.assertEqual(process.wait(timeout=10),2)
            self.assertEqual(process.stderr.read(),b'')
        finally:
            if process.poll() is None:process.kill();process.wait()
            process.stdout.close();process.stderr.close()

    def test_public_user_does_not_join_private_hub(self):
        with patch.dict(os.environ, {}, clear=True), patch.object(n, 'hub_request', side_effect=AssertionError('no calls')):
            self.assertEqual(n.register_hub('https://panel.example.com/', {}, {}), '')

    def test_hub_requires_explicit_enrollment(self):
        with patch.dict(os.environ, {'IP_HUB_URL': 'https://hub.example.com'}, clear=True), patch.object(n, 'hub_request', side_effect=AssertionError('no calls')):
            with self.assertRaises(ValueError): n.register_hub('https://panel.example.com/', {}, {})

    def test_template_has_one_initial_menu_and_auto_skips_parameter_prompts(self):
        source = (ROOT / 'src/install.sh').read_text(encoding='utf-8')
        self.assertEqual(source.count('请选择（回车自动）'), 1)
        self.assertIn('IP_SETUP_MODE=auto', source)
        self.assertIn('"$IP_SETUP_MODE" == manual', source)
        self.assertIn('"$IP_DISABLE_UDP" != 1', source)

    def test_private_setup_never_saves_account_token(self):
        state = {'mode': 'mapped'}
        n.ws_config(state, {'proxy_port': 12345}, {'socks_username': 'test', 'socks_password': 'test'})
        result = {'ok': True, 'machine_id': 'm-one', 'machine_token': 'own-machine-token',
                  'hosts': {r: r + '.example.com' for r in ('panel', 'tcp', 'udp', 'home')},
                  'tunnel_token': 'own-tunnel-runtime-token', 'api_token': 'must-never-be-stored'}
        settings = {'agent_port': 12346, 'public_ip': '192.0.2.1'}
        with patch.dict(os.environ, {'IP_HUB_URL': 'https://hub.example.com', 'IP_HUB_ENROLLMENT': 'once'}, clear=True), \
             patch.object(n, 'ws_origin_setup'), patch.object(n, 'hub_request', return_value=result), patch.object(n, 'start_tunnel') as start:
            n.private_tunnel_setup(state, settings, {'username': 'u', 'password': 'p'}, '/test/sing-box')
            start.assert_called_once_with('own-tunnel-runtime-token')
        self.assertFalse((self.root / 'cloudflare.json').exists())
        self.assertNotIn('must-never-be-stored', (self.root / 'hub.json').read_text())
        self.assertNotIn('api_token', n.read(self.root / 'hub.json'))

    def test_own_three_nodes_unit_is_discovered_on_repeat(self):
        config = self.root / 'existing.json'; config.write_text('{}')
        def output(*args):
            if args[1] == 'list-units': return 'three-nodes.service loaded active running test\nunrelated.service loaded active running other'
            return '/opt/bin/sing-box run -c ' + str(config)
        with patch.object(d, 'run', side_effect=output):
            self.assertEqual(d.active_configs(), [config])

    def test_hub_requests_use_header_that_reaches_cloudflare_worker(self):
        seen=[]
        def respond(request,timeout):
            seen.append((request,timeout));return io.BytesIO(b'{"ok":true}')
        with patch.object(n.urllib.request,'urlopen',side_effect=respond):
            n.hub_request({'url':'https://hub.example.com'},'POST','provision',{},'test-enrollment')
        request,timeout=seen[0]
        self.assertTrue(request.get_header('User-agent').startswith('Mozilla/5.0'))
        self.assertEqual(request.get_header('Authorization'),'Bearer test-enrollment')
        self.assertEqual(timeout,90)

    def test_edge_block_is_not_reported_as_expired_enrollment(self):
        for raw,message in [(b'error code: 1010','边缘拦截'),(b'{"error":"invalid"}','授权码无效')]:
            error=urllib.error.HTTPError('https://hub.example.com',403,'Forbidden',{},io.BytesIO(raw))
            with patch.object(n.urllib.request,'urlopen',side_effect=error):
                with self.assertRaisesRegex(RuntimeError,message):
                    n.hub_request({'url':'https://hub.example.com'},'POST','provision',{},'once')

    def test_closed_ports_keep_generated_links_panel_and_port_list(self):
        import types
        binary=self.root/'sing-box';binary.touch()
        (self.root/'renew-panel-cert.sh').touch()
        n.write(self.root/'network.json',{'mode':'direct','udp_available':True})
        settings={'public_ip':'192.0.2.10','panel_port':30443,'proxy_port':30101,'sing_box_bin':str(binary)}
        n.write(self.root/'settings.json',settings)
        n.write(self.root/'auth.json',{'username':'test','password':'test-password'})
        links={'tcp':'vless://test@192.0.2.10:31001','udp':'hysteria2://test@192.0.2.10:31002',
               'home':'vless://home@192.0.2.10:31003'}
        n.write(self.root/'native-links.json',links)
        verifier=types.SimpleNamespace(verify=lambda *a:{'ok':False,'reason':'port blocked'})
        real_path=pathlib.Path
        def local_path(value):
            return self.root/real_path(value).name if str(value).startswith('/root/') else real_path(value)
        with patch.dict(os.environ,{},clear=True),patch.object(n,'pathlib',types.SimpleNamespace(Path=local_path)), \
             patch.object(n,'UNITS',self.root),patch.object(n,'firewall',return_value=[]) as ports,patch.object(n,'run'), \
             patch.object(n,'load_verifier',return_value=verifier),patch.object(n,'panel_check',return_value=False), \
             patch.object(n,'cf_credentials',side_effect=ValueError('missing authorization')),patch('sys.stdout',new_callable=io.StringIO):
            self.assertEqual(n.apply(),2)
        self.assertEqual((self.root/'three-nodes-links.txt').read_text().splitlines(),list(links.values()))
        self.assertEqual(n.read(self.root/'links.json'),dict.fromkeys(links,''))
        self.assertIn(('tcp',30443),ports.call_args.args[0])
        self.assertIn(('tcp',80),ports.call_args.args[0])
        self.assertIn(['tcp',80],n.read(self.root/'firewall-ports.json'))
        summary=(self.root/'three-nodes-info.txt').read_text(encoding='utf-8')
        self.assertIn('https://192.0.2.10:30443/',summary)
        self.assertIn('已生成，公网检测未通过',summary)
        self.assertIn('请开放TCP端口 80（证书申请与自动续期）',summary)
        self.assertNotIn('住宅代理内部端口',summary)
        detail=(self.root/'deployment-details.txt').read_text(encoding='utf-8')
        self.assertIn('住宅代理内部端口 30101',detail)
        self.assertEqual(n.read(self.root/'network-report.json')['ready_roles'],[])

    def test_unmapped_summary_keeps_native_links_and_one_action_list(self):
        state={'mode':'mapped','udp_available':False,'mappings':{}}
        settings={'public_ip':'192.0.2.10','panel_port':21479,'proxy_port':53583}
        native={'tcp':'vless://test@192.0.2.10:45624','udp':'','home':'vless://home@192.0.2.10:34735'}
        exported=n.generated_exports({'tcp':[''],'udp':[],'home':['']},{},native)
        with patch.dict(os.environ,{},clear=True):
            text=n.concise_result(state,settings,{'username':'test','password':'test-password'},native,exported,{},[],[],True,[])
        self.assertIn(native['tcp'],text);self.assertIn(native['home'],text)
        self.assertIn('需改为平台分配的外部地址、外部端口',text)
        self.assertEqual(text.count('【请在后台开放端口】'),1)
        headers=[line for line in text.splitlines() if line.startswith('╔')]
        self.assertEqual(len(headers),3)
        for header,title in zip(headers,('① 节点链接','② 面板信息','③ 操作提醒')):
            self.assertIn(title,header)
        self.assertLess(text.index('详细日志：'),text.index(headers[0]))
        self.assertLess(text.index('面板证书：'),text.index(headers[0]))
        self.assertEqual(text.count('╚════════════════════════════════════════════╝'),3)
        self.assertEqual(text.rstrip().splitlines()[-1],'╚════════════════════════════════════════════╝')
        self.assertIn(native['tcp'],text.splitlines());self.assertIn(native['home'],text.splitlines())
        self.assertNotIn('下一步：在平台后台创建以下端口映射：',text)
        self.assertIn('请开放TCP端口 21479（管理面板）',text)
        self.assertIn('当前环境不支持UDP节点。',text)
        self.assertIn('已生成两条TCP节点',text)
        self.assertGreater(text.index('当前环境不支持UDP节点。'),text.index(headers[2]))
        self.assertIn('第二条：中转家宽IP节点',text)
        self.assertNotIn('映射平台：将外部地址和端口填入',text)
        self.assertNotIn('需要第二条 TCP 替代入口',text)
        self.assertNotIn('53583',text)
        self.assertNotIn('NVIDIA',text)
        self.assertLess(len(text.splitlines()),40)

    def test_working_cf_summary_lists_optional_native_ports_once(self):
        state={'mode':'mapped','udp_available':False,'mappings':{},'private_hosts':{'panel':'panel.example.com'}}
        n.ws_config(state,{'proxy_port':53583},{'socks_username':'test','socks_password':'test'})
        native={'tcp':'vless://test@192.0.2.10:45624','udp':'','home':'vless://home@192.0.2.10:34735'}
        exported={r:n.ws_uri(r,state['ws'][r],r+'.example.com') for r in ('tcp','udp','home')}
        settings={'public_ip':'192.0.2.10','panel_port':21479}
        with patch.dict(os.environ,{},clear=True):
            text=n.concise_result(state,settings,{'username':'test','password':'test-password'},native,exported,exported,['https://panel.example.com/'],[],True,[])
        self.assertIn('无需新增公网入站映射',text)
        self.assertIn('备用直连端口（可选，需在平台创建映射）',text)
        self.assertEqual(text.count('45624'),1);self.assertEqual(text.count('21479'),1);self.assertEqual(text.count('34735'),1)
        self.assertIn('不是 Hysteria2',text)
        self.assertEqual(text.count('节点链接（'),1)

    def test_quiet_command_preserves_failure_and_private_diagnostics(self):
        source=(ROOT/'src/install.sh').read_text(encoding='utf-8')
        function=next(line for line in source.splitlines() if line.startswith('quiet()'))
        windows_bash=pathlib.Path('C:/Program Files/Git/bin/bash.exe')
        bash=str(windows_bash) if windows_bash.exists() else 'bash'
        log=self.root/'install.log'
        script=function+'\nIP_INSTALL_LOG="$1"\nquiet bash -c \'echo detail; echo problem >&2; exit 7\'\n'
        result=subprocess.run([bash,'-c',script,'quiet-test',str(log)],capture_output=True,text=True)
        self.assertEqual(result.returncode,7)
        self.assertEqual(result.stdout,'');self.assertEqual(result.stderr,'')
        self.assertIn('detail',log.read_text());self.assertIn('problem',log.read_text())
        live=next(line for line in source.splitlines() if line.startswith('live()'))
        script='set -o pipefail\n'+live+'\nIP_INSTALL_LOG="$1"\nlive bash -c \'echo progress; echo failed >&2; exit 7\'\n'
        result=subprocess.run([bash,'-c',script,'live-test',str(log)],capture_output=True,text=True)
        self.assertEqual(result.returncode,7)
        self.assertIn('progress',result.stdout);self.assertIn('failed',result.stdout)
        self.assertIn('progress',log.read_text());self.assertIn('failed',log.read_text())

    def test_unmapped_host_never_exports_egress_ip_as_incoming_address(self):
        state={'mode':'mapped','udp_available':False,'mappings':{}}
        settings={'public_ip':'192.0.2.10','panel_port':30443,'proxy_port':30001}
        self.assertEqual(n.pending_panel_urls(state,settings),[])
        exported=n.generated_exports({'tcp':[''],'udp':[],'home':['']},{})
        self.assertFalse(any(exported.values()))
        instructions='\n'.join(n.access_instructions(state,settings,{'tcp':'vless://test@192.0.2.10:31001'}))
        self.assertIn('住宅面板：TCP 内部端口 30443',instructions)
        self.assertIn('平台给出的外部主机名和外部端口',instructions)
        self.assertNotIn('https://192.0.2.10',instructions)
        state.update(external_address='worker.example.com',panel_external_port=44443)
        self.assertEqual(n.pending_panel_urls(state,settings),['https://worker.example.com:44443/'])

    def test_private_hub_with_working_native_nodes_still_gets_authorized_panel(self):
        import types
        binary=self.root/'sing-box';binary.touch()
        n.write(self.root/'network.json',{'mode':'direct','udp_available':True})
        n.write(self.root/'settings.json',{'public_ip':'192.0.2.10','panel_port':30443,'sing_box_bin':str(binary)})
        n.write(self.root/'auth.json',{'username':'test','password':'test-password'})
        links={r:'vless://test@192.0.2.10:'+str(31000+i) for i,r in enumerate(('tcp','udp','home'))}
        n.write(self.root/'native-links.json',links)
        hosts={r:r+'.example.com' for r in ('panel','tcp','udp','home')}
        verifier=types.SimpleNamespace(verify=lambda *a:{'ok':True})
        real_path=pathlib.Path
        def local_path(value):
            return self.root/real_path(value).name if str(value).startswith('/root/') else real_path(value)
        with patch.dict(os.environ,{'IP_HUB_URL':'https://hub.example.com'},clear=True), \
             patch.object(n,'pathlib',types.SimpleNamespace(Path=local_path)), \
             patch.object(n,'UNITS',self.root),patch.object(n,'firewall',return_value=[]),patch.object(n,'run'), \
             patch.object(n,'load_verifier',return_value=verifier),patch.object(n,'panel_check',return_value=True), \
             patch.object(n,'private_tunnel_setup',return_value=(hosts,{r:'fallback-'+r for r in links})) as provision, \
             patch.object(n,'register_hub',return_value='https://hub.example.com') as register:
            self.assertEqual(n.apply(),0)
            provision.assert_called_once()
            self.assertEqual(register.call_args.args[0],'https://panel.example.com/')
        self.assertEqual(n.read(self.root/'links.json'),links)

    def test_native_builder_skips_udp_without_damaging_other_nodes(self):
        source = (ROOT / 'src/install.sh').read_text(encoding='utf-8')
        code = source.split('export CONFIG ', 1)[1].split("python3 - <<'PY'\n", 1)[1].split('\nPY\n', 1)[0]
        env = {'CONFIG': str(self.root / 'nodes.json'), 'REALITY_PORT': '21001', 'HY2_PORT': '', 'HOME_PORT': '21003',
               'SOCKS_PORT': '21004', 'SOCKS_USER': 'test', 'SOCKS_PASS': 'test', 'SNI': 'www.amazon.com',
               'REALITY_PRIVATE': 'test', 'HOME_PRIVATE': 'test', 'REALITY_UUID': 'test', 'HOME_UUID': 'test',
               'REALITY_SID': 'aabbccdd', 'HOME_SID': 'deadbeef', 'HY2_PASS': 'test',
               'EXIST_TCP': '', 'EXIST_UDP': '', 'NEED_STACK': '1', 'IP_DISABLE_UDP': '1'}
        with patch.dict(os.environ, env, clear=True): exec(compile(code, '<native-builder>', 'exec'), {})
        value = n.read(self.root / 'nodes.json.new')
        self.assertEqual([i['tag'] for i in value['inbounds']], ['reality-direct', 'reality-home'])
        self.assertEqual(value['route']['rules'][0]['outbound'], 'home')


if __name__ == '__main__': unittest.main()
