"""Offline loop safety checks: no real routes, services, or radios are touched."""
import dataclasses
import io
import socket
import struct
import sys
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'linux'), str(ROOT / 'tools')]
import pdalink_lite as lite
import pdalink_tun as tun
DIRECT_PROXY_URL = "http://192.168.49.1:8001"


def test_upstream_route_requires_physical_wifi_and_rejects_tun_after_link_loss():
    config = lite.SystemRouteConfig('pdalink0', '10.66.0.2/24', 1500, (), DIRECT_PROXY_URL)
    with mock.patch.object(lite, 'route_info', return_value=lite.parse_route_info(
            '192.168.49.1 dev wlan0 src 192.168.49.2')), mock.patch.object(Path, 'exists', return_value=True):
        assert lite.proxy_upstream_device(config) == 'wlan0'
    for route in (None, lite.parse_route_info('192.168.49.1 dev pdalink0 src 10.66.0.2'),
                  lite.parse_route_info('192.168.49.1 via 10.0.0.1 dev wlan0 src 10.0.0.2')):
        with mock.patch.object(lite, 'route_info', return_value=route):
            try:
                lite.proxy_upstream_device(config)
            except ValueError:
                pass
            else:
                raise AssertionError('unsafe proxy route accepted')


def test_bound_socket_never_falls_back_when_wifi_route_disappears():
    proxy = tun.HttpConnectProxy(DIRECT_PROXY_URL)
    proxy.upstream_device = 'wlan0'
    sock = mock.Mock()
    sock.connect.side_effect = OSError('network unreachable after route removal')
    with mock.patch.object(tun.socket, 'socket', return_value=sock), mock.patch.object(
            tun.socket, 'create_connection') as unbound:
        for _ in range(2):  # Both initial and subsequent connections stay bound.
            try:
                proxy.connect('1.1.1.1', 443)
            except OSError:
                pass
        unbound.assert_not_called()
        sock.setsockopt.assert_called_with(socket.SOL_SOCKET, socket.SO_BINDTODEVICE, b'wlan0\0')
        assert sock.close.call_count == 2
        calls = sock.method_calls
        assert calls.index(mock.call.setsockopt(socket.SOL_SOCKET, socket.SO_BINDTODEVICE, b'wlan0\0')) < calls.index(mock.call.connect(('192.168.49.1', 8001)))


def test_windows_socket_interface_binding_precedes_connect():
    proxy = tun.HttpConnectProxy(DIRECT_PROXY_URL)
    proxy.upstream_interface_index = 17
    sock = mock.Mock()
    sock.recv.side_effect = [bytes([c]) for c in b'HTTP/1.1 200 OK\r\n\r\n']
    with mock.patch.object(tun.socket, 'socket', return_value=sock):
        assert proxy.connect('1.1.1.1', 443) is sock
    sock.setsockopt.assert_called_once_with(socket.IPPROTO_IP, 31, struct.pack('!I', 17))


def test_direct_system_start_binds_before_routes_and_preserves_dns_state(tmp_path):
    args = lite.build_parser().parse_args(['system-start', '--proxy-url', DIRECT_PROXY_URL,
        '--state-file', str(tmp_path / 'system.json'), '--route-mode', 'active_override', '--dns-mode', 'resolved'])
    adapter = mock.Mock()
    adapter.iface = 'pdalink0'
    saved = []
    def run_adapter(**kwargs):
        state = lite.load_system_state(tmp_path / 'system.json')
        saved.append(state.config)
        assert state.config.upstream_device == 'wlan0'
        assert state.config.proxy_url == DIRECT_PROXY_URL
        assert adapter.proxy.upstream_device == 'wlan0'
    adapter.run.side_effect = run_adapter
    with mock.patch.object(lite.os, 'geteuid', return_value=0), mock.patch.object(
            lite, 'start_dns_ready_adapter', return_value=None), mock.patch.object(
            lite, 'proxy_upstream_device', return_value='wlan0'), mock.patch.object(
            lite, 'print_system_proxy_probe', return_value=True), mock.patch.object(
            lite, 'existing_tun_route_conflicts', return_value=[]), mock.patch.object(
            tun.TunHttpAdapter, 'open', return_value=adapter), mock.patch.object(
            lite, 'build_dns_setup_commands', return_value=[['resolvectl', 'domain', 'pdalink0', '~.']]), mock.patch.object(
            lite, 'run_privileged_network_commands', return_value=0) as setup, mock.patch.object(
            lite, 'run_network_commands_best_effort', return_value=True), mock.patch.object(
            lite, 'restart_active_tailscale_after_route_override') as tailscale:
        assert lite.system_start(args) == 0
        commands = setup.call_args.args[0]
        assert ['ip', 'route', 'replace', '0.0.0.0/1', 'dev', 'pdalink0'] in commands
        assert ['resolvectl', 'domain', 'pdalink0', '~.'] in commands
        tailscale.assert_called_once_with(saved[0])
    assert not (tmp_path / 'system.json').exists()

def test_legacy_remote_state_does_not_claim_loopback_protection(tmp_path, capsys):
    config = lite.SystemRouteConfig('pdalink0', '10.66.0.2/24', 1500, (), DIRECT_PROXY_URL)
    path = tmp_path / 'legacy.json'
    lite.write_system_state(path, config)
    lite.print_system_runtime_state(path)
    assert 'upstream-device: UNBOUND (legacy state; restart required)' in capsys.readouterr().out

def test_pac_response_requires_http_success_and_is_bounded():
    for response in (b'HTTP/1.1 503 Unavailable\r\n\r\nPROXY 192.168.49.1:8001;',
                     b'x' * 65537):
        sock = mock.MagicMock()
        sock.__enter__.return_value = sock
        sock.recv.side_effect = [response, b'']
        with mock.patch.object(lite.socket, 'create_connection', return_value=sock):
            try:
                lite.request_pac('192.168.49.1', 8001)
            except OSError:
                pass
            else:
                raise AssertionError('invalid PAC response accepted')

def test_connection_status_uses_direct_without_adb_and_rejects_legacy_binding(tmp_path, capsys):
    import dataclasses
    import json
    path = tmp_path / 'system.json'
    args = lite.build_parser().parse_args(['connection-status', '--state-file', str(path), '--json'])
    config = lite.SystemRouteConfig('pdalink0', '10.66.0.2/24', 1500, (), DIRECT_PROXY_URL,
                                   upstream_device='wlan0')
    with mock.patch.object(lite, 'proxy_upstream_device', return_value='wlan0'), mock.patch.object(
            lite, 'process_matches_state', return_value=True), mock.patch.object(
            lite, 'expected_pdalink_pac_status', return_value=('active', 'expected endpoint')), mock.patch.object(
            lite, 'route_info', return_value=lite.parse_route_info('1.1.1.1 dev pdalink0')), mock.patch.object(
            lite, 'run_connection_probe', return_value=lite.CommandResult(0, 'internet works', '')), mock.patch.object(
            lite, 'adb_run') as adb:
        lite.write_system_state(path, config)
        assert lite.connection_status(args) == 0
        result = json.loads(capsys.readouterr().out)
        assert result['transport']['name'] == 'wifi-direct'
        assert result['checks']['proxy']['url'] == DIRECT_PROXY_URL
        adb.assert_not_called()
        lite.write_system_state(path, dataclasses.replace(config, upstream_device=''))
        assert lite.connection_status(args) == 1
        assert json.loads(capsys.readouterr().out)['overall'] == 'disconnected'
