"""Offline reconnect contracts; never invoke networking tools."""
import argparse
import io
from contextlib import redirect_stdout
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'linux'))
import pdalink_lite as client


class FixedGroupReconnectTest(unittest.TestCase):
    def test_imported_profile_add_and_update_retain_local_only_autoconnect(self):
        for existing in (None, client.NmActiveConnection('pdalink-wifi-direct', '802-11-wireless')):
            with self.subTest(existing=existing), \
                    mock.patch.object(client.shutil, 'which', return_value='nmcli'), \
                    mock.patch.object(client, 'wifi_connection_profiles_for_ssid', return_value=[]), \
                    mock.patch.object(client, 'saved_nm_connection_named', return_value=existing), \
                    mock.patch.object(client, 'run_command', return_value=client.CommandResult(0, '', '')) as run:
                self.assertEqual(client.prepare_wifi_direct_profile(
                    'DIRECT-PD-PdaLink-ABC123', 'dummy-password', autoconnect=True),
                    'pdalink-wifi-direct')
                command = run.call_args.args[0]
                for key, value in {
                    'connection.autoconnect': 'yes', 'connection.autoconnect-retries': '0',
                    'ipv4.never-default': 'yes', 'ipv4.ignore-auto-dns': 'yes',
                    'ipv6.method': 'disabled', 'ipv6.never-default': 'yes',
                    'ipv6.ignore-auto-dns': 'yes', 'wifi-sec.psk-flags': '0',
                }.items():
                    self.assertEqual(command[command.index(key) + 1], value)

    def test_qr_import_verifies_before_persisting_pac_without_rejoining(self):
        bundle = client.ConnectionBundle('wifi-direct', 'DIRECT-PD-PdaLink-ABC123',
                                        'wpa2-psk', 'dummy-password',
                                        'http://192.168.49.1:8001', '192.168.49.1', 8001)
        with tempfile.TemporaryDirectory() as directory:
            args = argparse.Namespace(uri=client.build_connection_uri(bundle), image='', camera=False,
                                      iface=None, verify_timeout=1, no_connect=False, internet=True,
                                      state_file=str(Path(directory) / 'proxy.json'))
            events = []
            with mock.patch.object(client, 'prepare_wifi_direct_profile', return_value='pdalink-wifi-direct') as prepare, \
                    mock.patch.object(client, 'run_nmcli', return_value=0), \
                    mock.patch.object(client, 'verify_after_connect', side_effect=lambda *a: events.append('verify') or 0), \
                    mock.patch.object(client, 'enable_gnome_wifi_proxy', side_effect=lambda **kw: events.append('pac')) as enable, \
                    redirect_stdout(io.StringIO()) as output:
                self.assertEqual(client.connection_import(args), 0)
            self.assertEqual(events, ['verify', 'pac'])
            self.assertTrue(prepare.call_args.kwargs['autoconnect'])
            self.assertFalse(enable.call_args.kwargs['disconnect_on_stop'])
            self.assertNotIn('dummy-password', output.getvalue())

    def test_qr_import_never_applies_proxy_after_failed_verification(self):
        bundle = client.ConnectionBundle('wifi-direct', 'DIRECT-PD-PdaLink-ABC123',
                                        'wpa2-psk', 'dummy-password',
                                        'http://192.168.49.1:8001', '192.168.49.1', 8001)
        args = argparse.Namespace(uri=client.build_connection_uri(bundle), image='', camera=False,
                                  iface=None, verify_timeout=1, no_connect=False, internet=True)
        with mock.patch.object(client, 'prepare_wifi_direct_profile', return_value='pdalink-wifi-direct'), \
                mock.patch.object(client, 'run_nmcli', return_value=0), \
                mock.patch.object(client, 'verify_after_connect', return_value=1), \
                mock.patch.object(client, 'enable_gnome_wifi_proxy') as enable, redirect_stdout(io.StringIO()):
            self.assertEqual(client.connection_import(args), 1)
            enable.assert_not_called()
