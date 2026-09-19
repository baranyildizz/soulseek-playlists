import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch, Mock

from slskd_runtime import SlskdRuntime


@unittest.skipUnless(os.name=='nt','Windows startup integration')
class RuntimeTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.exe=Path(self.temp.name)/'slskd.exe';self.exe.touch()
        self.yaml=Path(self.temp.name)/'slskd.yml';self.yaml.touch()
        self.runtime=SlskdRuntime({'slskd_url':'http://localhost:5030',
            'slskd_executable':str(self.exe),'slskd_config_path':str(self.yaml)})

    def tearDown(self):self.temp.cleanup()

    def test_existing_service_is_reused(self):
        with patch.object(self.runtime,'reachable',return_value=True),patch('slskd_runtime.subprocess.Popen') as start:
            self.assertTrue(self.runtime.prepare()[0]);start.assert_not_called()

    def test_cold_start_is_hidden_and_has_no_credentials(self):
        with patch.object(self.runtime,'reachable',return_value=False),patch.object(self.runtime,'running',return_value=False),patch('slskd_runtime.subprocess.Popen') as start:
            start.return_value.poll.return_value=None
            self.assertFalse(self.runtime.prepare()[0])
            self.runtime.prepare();self.runtime.prepare()
            start.assert_called_once()
            self.assertEqual(start.call_args.args[0],[str(self.exe),'--config',str(self.yaml)])
            self.assertTrue(start.call_args.kwargs['creationflags'])

    def test_already_booting_does_not_duplicate(self):
        with patch.object(self.runtime,'reachable',return_value=False),patch.object(self.runtime,'running',return_value=True),patch('slskd_runtime.subprocess.Popen') as start:
            self.assertFalse(self.runtime.prepare()[0]);start.assert_not_called()

    def test_remote_or_disabled_never_launches(self):
        with patch.object(self.runtime,'reachable',return_value=False),patch('slskd_runtime.subprocess.Popen') as start:
            self.runtime.config['slskd_url']='http://example.org:5030'
            self.runtime.prepare()
            self.runtime.config.update(slskd_url='http://localhost:5030',auto_start_slskd=False)
            self.runtime.prepare();start.assert_not_called()

    def test_missing_config_does_not_launch_defaults(self):
        self.yaml.unlink()
        with patch.object(self.runtime,'reachable',return_value=False),patch.object(self.runtime,'running',return_value=False),patch('slskd_runtime.subprocess.Popen') as start:
            ready,message=self.runtime.prepare()
            self.assertFalse(ready);self.assertIn('ayar dosyası',message);start.assert_not_called()

    def test_launch_failure_is_redacted_and_rate_limited(self):
        with patch.object(self.runtime,'reachable',return_value=False),patch.object(self.runtime,'running',return_value=False),patch('slskd_runtime.subprocess.Popen',side_effect=OSError('SECRET')) as start:
            ready,message=self.runtime.prepare()
            self.assertFalse(ready);self.assertNotIn('SECRET',message)
            self.runtime.prepare();start.assert_called_once()

    def test_optional_listen_port_does_not_rewrite_yaml(self):
        before=self.yaml.read_bytes()
        self.runtime.config['slskd_listen_port']=45000
        with patch.object(self.runtime,'reachable',return_value=False),patch.object(self.runtime,'running',return_value=False),patch('slskd_runtime.subprocess.Popen') as start:
            self.runtime.prepare()
            command=start.call_args.args[0]
            self.assertEqual(command[command.index('--slsk-listen-port')+1],'45000')
        self.assertEqual(self.yaml.read_bytes(),before)


if __name__=='__main__':unittest.main()
