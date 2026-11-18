"""Check the local profile and the CLI arguments that use it."""
import contextlib
import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

HOST_TOOLS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HOST_TOOLS))

import connection_config
import discover
import skyline_tcplog
import switch_remote
import switch_test


class LocalConnectionTests(unittest.TestCase):
    def setUp(self):
        temporary = self.enterContext(tempfile.TemporaryDirectory())
        self.local = Path(temporary) / '.switch-remote.json'
        self.enterContext(patch.object(connection_config, 'DEFAULT_CONNECTION_CONFIG', self.local))
        self.enterContext(patch.dict(os.environ, {}))
        os.environ.pop('SWITCH_REMOTE_CONFIG', None)
        self.enterContext(contextlib.redirect_stdout(io.StringIO()))

    def configure(self, **settings):
        self.local.write_text(json.dumps(settings), encoding='utf-8')

    def test_status_uses_local_host(self):
        self.configure(host='device.example.invalid')
        with patch.object(switch_remote, 'SwitchClient') as client, \
                patch.object(switch_remote, 'read_token', return_value='synthetic-token'):
            client.return_value.request.return_value = {'ok': True}
            switch_remote.main(['status'])
        self.assertEqual(client.call_args.args[0], 'device.example.invalid')
        client.return_value.request.assert_called_once_with('status')

    def test_explicit_host_overrides_unreadable_config(self):
        self.local.write_text('invalid', encoding='utf-8')
        with patch.object(switch_remote, 'SwitchClient') as client, \
                patch.object(switch_remote, 'read_token', return_value='synthetic-token'):
            client.return_value.request.return_value = {'ok': True}
            switch_remote.main(['--host', 'override.example.invalid', 'status'])
        self.assertEqual(client.call_args.args[0], 'override.example.invalid')

    def test_log_cli_uses_alternate_config_path(self):
        self.configure(host='unused.example.invalid')
        alternative = self.local.with_name('other.json')
        alternative.write_text('{"host":"logger.example.invalid"}', encoding='utf-8')
        os.environ['SWITCH_REMOTE_CONFIG'] = str(alternative)
        with patch.object(skyline_tcplog, 'run', return_value={'connects': 1, 'bytes': 1}) as run:
            skyline_tcplog.main(['--label', 'test'])
        self.assertEqual(run.call_args.args[:2], ('logger.example.invalid', 6969))

    def test_discover_uses_local_network_and_preferred_host(self):
        self.configure(host='192.0.2.2', network='192.0.2.0/30')
        with patch.object(discover, 'scan', new_callable=AsyncMock, return_value=['192.0.2.2']) as scan, \
                patch.object(discover.sr, 'read_token', return_value='synthetic-token'), \
                patch.object(discover, 'confirm', return_value=(True, {})):
            self.assertEqual(discover.main([]), 0)
        scan.assert_awaited_once_with(['192.0.2.2', '192.0.2.1'], switch_remote.DEFAULT_PORT)

    def test_usb_preflight_requires_device_without_config(self):
        with patch.object(switch_test, 'make') as make, \
                contextlib.redirect_stderr(io.StringIO()) as stderr, self.assertRaises(SystemExit) as failure:
            switch_test.main(['preflight'])
        self.assertEqual(failure.exception.code, 2)
        self.assertIn("pass --device or set 'device'", stderr.getvalue())
        make.assert_not_called()

    def test_usb_preflight_uses_configured_device(self):
        self.configure(device='/dev/example-serial')
        with patch.object(switch_test, 'make') as make, \
                patch.object(switch_test, 'preflight', return_value={'ready': True}):
            self.assertEqual(switch_test.main(['preflight']), 0)
        self.assertEqual(make.call_args.args[0].device, '/dev/example-serial')

    def test_invalid_config_fails_before_connection(self):
        self.configure(host=['device.example.invalid'])
        with patch.object(switch_remote, 'SwitchClient') as client, \
                contextlib.redirect_stderr(io.StringIO()) as stderr, self.assertRaises(SystemExit) as failure:
            switch_remote.main(['status'])
        self.assertEqual(failure.exception.code, 2)
        self.assertIn("field 'host' must be a nonempty string", stderr.getvalue())
        client.assert_not_called()


if __name__ == '__main__':
    unittest.main()
