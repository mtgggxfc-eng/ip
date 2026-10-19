import importlib.util,io,json,pathlib,subprocess,tempfile,types,unittest
from unittest.mock import patch
ROOT=pathlib.Path(__file__).resolve().parent.parent
spec=importlib.util.spec_from_file_location('cert_runtime',ROOT/'cert-runtime.py')
c=importlib.util.module_from_spec(spec);spec.loader.exec_module(c)

class CertificateRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=pathlib.Path(self.temp.name)
        self.patches=[patch.object(c,'ROOT',self.root),patch.object(c,'VENV',self.root/'venv')]
        for p in self.patches:p.start()
    def tearDown(self):
        for p in reversed(self.patches):p.stop()
        self.temp.cleanup()
    def test_existing_valid_certbot_is_reused_without_download(self):
        cb=c.VENV/'bin/certbot';cb.parent.mkdir(parents=True);cb.touch()
        with patch.object(c,'call',return_value=types.SimpleNamespace(stdout='certbot 5.4.0')) as call,patch.object(c,'managed_python') as managed:
            self.assertEqual(c.ensure_certbot(),str(cb));managed.assert_not_called();self.assertEqual(call.call_count,1)
    def test_old_system_python_uses_managed_runtime(self):
        with patch.object(c,'usable_python',return_value=False),patch.object(c,'managed_python',return_value='/owned/python3.12') as managed,patch.object(c,'call',return_value=types.SimpleNamespace(stdout='certbot 5.4.0')) as call:
            c.ensure_certbot();managed.assert_called_once()
            args=[list(map(str,x.args[0])) for x in call.call_args_list]
            self.assertIn(['/owned/python3.12','-m','venv','--clear',str(c.VENV)],args)
            self.assertTrue(any('certbot==5.4.0' in a for a in args))
    def test_bad_download_checksum_never_executes(self):
        with patch.object(c.platform,'machine',return_value='x86_64'),patch.object(c.urllib.request,'urlopen',return_value=io.BytesIO(b'bad archive')),patch.object(c,'call') as call:
            with self.assertRaisesRegex(RuntimeError,'SHA256'):c.managed_python()
            call.assert_not_called();self.assertFalse((self.root/'cert-runtime/uv').exists())
    def test_unowned_venv_is_preserved(self):
        c.VENV.mkdir();(c.VENV/'other-app').touch()
        with patch.object(c,'usable_python',return_value=False),patch.object(c,'managed_python',return_value='/owned/python3.12'),patch.object(c,'call') as call:
            with self.assertRaisesRegex(RuntimeError,'归属不明'):c.ensure_certbot()
            call.assert_not_called();self.assertTrue((c.VENV/'other-app').exists())
    def test_issuance_failure_distinguishes_occupied_port_and_rate_limit(self):
        log=self.root/'acme-logs/letsencrypt.log';log.parent.mkdir()
        for text,expected in [('Could not bind TCP port 80','被其他服务占用'),('urn:ietf:params:acme:error:rateLimited','限流'),('Timeout during connect','无法访问公网 TCP 80')]:
            log.write_text(text);self.assertIn(expected,c.issue_failure())
            self.assertFalse(json.loads((self.root/'certificate-status.json').read_text(encoding='utf-8'))['ok'])

if __name__=='__main__':unittest.main()
