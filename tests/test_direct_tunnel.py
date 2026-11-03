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
import pdalink_watch as watch
import test_pdalink_watch as fixtures
from test_pdalink_watch import FakeRunner, authorized_devices, ok_probe, is_service_call


def test_failover_dwell_and_proxy_health_follow_active_transport(tmp_path):
    cfg = fixtures.PdaLinkWatchTest().make_config(tmp_path / 'watch.json')
    cfg = dataclasses.replace(cfg, wifi_direct_failover=True, internet_check_interval=1)
    runner = FakeRunner('List of devices attached\n')
    now = [0.0]
    probes = []
    w = watch.PdaLinkWatcher(cfg, runner=runner, probe=ok_probe,
        internet_probe=lambda url, timeout: (probes.append(url) or ok_probe(url, timeout)),
        boot_clock=lambda: now[0], stdout=io.StringIO())
    with mock.patch.object(w, 'direct_ready', return_value=True):
        assert w.reconcile() == 0
        assert w.state.health['transport'] == 'wifi-direct'
        assert probes[-1] == watch.DIRECT_PROXY_URL
        runner.devices = authorized_devices()
        now[0] = 5
        assert w.reconcile() == 0
        assert w.active_transport == 'wifi-direct'
        now[0] = 24
        assert w.reconcile() == 0
        assert w.active_transport == 'wifi-direct'
        now[0] = 31
        assert w.reconcile() == 0
        assert w.active_transport == 'adb'
        assert probes[-1] == cfg.effective_proxy_url
        starts = [c for c in runner.calls if is_service_call(c, 'start')]
        assert len(starts) == 2
        assert watch.DIRECT_PROXY_URL in starts[0]
        assert cfg.effective_proxy_url in starts[1]
        assert sum(is_service_call(c, 'stop') for c in runner.calls) == 1


def test_direct_pac_must_match_and_switch_will_not_stop_unowned_service(tmp_path):
    cfg = dataclasses.replace(fixtures.PdaLinkWatchTest().make_config(tmp_path / 'watch.json'), wifi_direct_failover=True)
    w = watch.PdaLinkWatcher(cfg, runner=FakeRunner('', service_running=True), stdout=io.StringIO())
    with mock.patch.object(lite, 'proxy_upstream_device', return_value='wlan0'), mock.patch.object(
            lite, 'request_pac', return_value='PROXY 192.168.49.1:8001; evil'):
        assert not w.direct_ready()
    assert not w.switch_proxy(watch.DIRECT_PROXY_URL)
    assert not any(is_service_call(c, 'stop') for c in w.runner.calls)


def test_failover_default_off_and_cli_opt_in():
    parser = watch.build_parser()
    with mock.patch.dict('os.environ', {}, clear=True):
        assert not watch.config_from_args(parser.parse_args(['once'])).wifi_direct_failover
    assert watch.config_from_args(parser.parse_args(['once', '--wifi-direct-failover'])).wifi_direct_failover

def test_status_without_adb_still_reports_direct_service_and_health(tmp_path):
    cfg = fixtures.PdaLinkWatchTest().make_config(tmp_path / 'watch.json')
    watch.WatchState(mode='ready', service_owned=True, proxy_url=watch.DIRECT_PROXY_URL,
        health={'transport': 'wifi-direct', 'internet': 'ok'}).save(cfg.state_file)
    output = io.StringIO()
    w = watch.PdaLinkWatcher(cfg, runner=FakeRunner('', service_running=True), probe=ok_probe, stdout=output)
    with mock.patch.object(w, 'list_devices', return_value=([], watch.CommandResult(126, '', 'adb unavailable'))):
        assert w.status() == 0
    assert 'proxy: http://192.168.49.1:8001' in output.getvalue()
    assert 'system-service: RUNNING' in output.getvalue()
    assert 'health-internet: ok' in output.getvalue()


def test_usb_probe_failure_selects_direct_and_failed_stop_preserves_old_url(tmp_path):
    cfg = dataclasses.replace(fixtures.PdaLinkWatchTest().make_config(tmp_path / 'watch.json'), wifi_direct_failover=True)
    runner = FakeRunner(authorized_devices(), 'R3CR406AAGE tcp:18080 tcp:8000\n')
    w = watch.PdaLinkWatcher(cfg, runner=runner, probe=lambda *a: watch.ProxyCheck(False, 'USB PAC down'), stdout=io.StringIO())
    with mock.patch.object(w, 'direct_ready', return_value=True):
        assert w.reconcile() == 0
    assert w.active_transport == 'wifi-direct'
    with mock.patch.object(w, 'stop_service'):  # Simulate failed root-helper cleanup.
        assert not w.switch_proxy(cfg.effective_proxy_url)
    assert w.active_proxy_url == watch.DIRECT_PROXY_URL

def test_legacy_unbound_service_is_unhealthy_and_resume_resets_usb_hysteresis(tmp_path):
    cfg = fixtures.PdaLinkWatchTest().make_config(tmp_path / 'watch.json')
    w = watch.PdaLinkWatcher(cfg, runner=FakeRunner(''), stdout=io.StringIO())
    with mock.patch.object(w, 'run_read', return_value=watch.CommandResult(0,
            'wrapper: RUNNING\nstate-proxy: http://192.168.49.1:8001\nupstream-device: UNBOUND (legacy state; restart required)\n', '')):
        assert not w.service_check().healthy
    w.usb_healthy_since = 0.0
    w.handle_resume()
    assert w.usb_healthy_since is None


def test_dns_configuration_flushes_only_after_link_is_fully_configured():
    config = lite.SystemRouteConfig('pdalink0', '10.66.0.2/24', 1500, (), watch.DIRECT_PROXY_URL)
    with mock.patch.object(lite.shutil, 'which', return_value='/stub/resolvectl'):
        assert lite.build_dns_setup_commands(config) == [
            ['resolvectl', 'dns', 'pdalink0', '1.1.1.1', '8.8.8.8'],
            ['resolvectl', 'domain', 'pdalink0', '~.'],
            ['resolvectl', 'default-route', 'pdalink0', 'yes'],
            ['resolvectl', 'flush-caches'],
        ]


def test_dns_probe_is_uncached_interface_scoped_and_retries():
    config = lite.SystemRouteConfig('pdalink0', '10.66.0.2/24', 1500, (), watch.DIRECT_PROXY_URL)
    with mock.patch.object(lite, 'run_command', side_effect=[lite.CommandResult(1, '', 'timeout'),
            lite.CommandResult(0, '1.2.3.4', '')]) as command, mock.patch.object(lite.time, 'sleep'):
        assert lite.wait_for_tunnel_dns(config)
        assert command.call_count == 2
        assert command.call_args.args[0] == ['resolvectl', 'query', '--interface', 'pdalink0',
                                             '--cache=no', '--type=A', 'api.ipify.org']
    with mock.patch.object(lite, 'run_command', return_value=lite.CommandResult(1, '', 'timeout')) as command, \
            mock.patch.object(lite.time, 'sleep'):
        assert not lite.wait_for_tunnel_dns(config)
        assert command.call_count == 6


def test_dns_readiness_pumps_adapter_before_probe_and_stops_on_failure():
    import threading
    config = lite.SystemRouteConfig('pdalink0', '10.66.0.2/24', 1500, (), watch.DIRECT_PROXY_URL)
    class Adapter:
        def __init__(self):
            self.stop_event = threading.Event()
            self.started = threading.Event()
        def run(self, **kwargs):
            self.started.set()
            self.stop_event.wait(2)
    for success in (True, False):
        adapter = Adapter()
        def probe(_config):
            assert adapter.started.wait(1)
            assert not adapter.stop_event.is_set()
            return success
        with mock.patch.object(lite, 'wait_for_tunnel_dns', side_effect=probe):
            if success:
                worker = lite.start_dns_ready_adapter(config, adapter)
                assert worker.thread.is_alive()
                worker.stop()
                assert not worker.thread.is_alive()
            else:
                import pytest
                with pytest.raises(RuntimeError, match='refusing to report connected'):
                    lite.start_dns_ready_adapter(config, adapter)
                assert adapter.stop_event.is_set()


def test_failed_dns_probe_never_writes_ready_state_and_rolls_back(tmp_path):
    args = lite.build_parser().parse_args(['system-start', '--proxy-url', watch.DIRECT_PROXY_URL,
        '--state-file', str(tmp_path / 'system.json'), '--no-tailscale-compat'])
    adapter = mock.Mock()
    with mock.patch.object(lite.os, 'geteuid', return_value=0), \
            mock.patch.object(lite, 'proxy_upstream_device', return_value='wlan0'), \
            mock.patch.object(lite, 'print_system_proxy_probe', return_value=True), \
            mock.patch.object(lite, 'existing_tun_route_conflicts', return_value=[]), \
            mock.patch.object(tun.TunHttpAdapter, 'open', return_value=adapter), \
            mock.patch.object(lite, 'build_dns_setup_commands', return_value=[['resolvectl', 'flush-caches']]), \
            mock.patch.object(lite, 'run_privileged_network_commands', return_value=0), \
            mock.patch.object(lite, 'start_dns_ready_adapter', side_effect=RuntimeError('DNS failed')), \
            mock.patch.object(lite, 'write_system_state') as state, \
            mock.patch.object(lite, 'run_network_commands_best_effort', return_value=True) as cleanup:
        assert lite.system_start(args) == 1
        state.assert_not_called()
        cleanup.assert_called_once()
        adapter.close.assert_called_once()



def test_dns_and_packet_loop_ready_before_state_and_tailscale_work(tmp_path):
    import threading
    path = tmp_path / 'system.json'
    args = lite.build_parser().parse_args(['system-start', '--proxy-url', watch.DIRECT_PROXY_URL,
                                         '--state-file', str(path)])
    adapter = mock.Mock()
    adapter.stop_event = threading.Event()
    pumping = threading.Event()
    def run_adapter(**kwargs):
        pumping.set()
        adapter.stop_event.wait(2)
    adapter.run.side_effect = run_adapter
    def probe(config):
        assert pumping.wait(1)
        assert not path.exists()
        return True
    def tailscale(config):
        assert pumping.is_set()
        assert path.exists()
        assert not adapter.stop_event.is_set()
        adapter.stop_event.set()
    with mock.patch.object(lite.os, 'geteuid', return_value=0), \
            mock.patch.object(lite, 'proxy_upstream_device', return_value='wlan0'), \
            mock.patch.object(lite, 'print_system_proxy_probe', return_value=True), \
            mock.patch.object(lite, 'existing_tun_route_conflicts', return_value=[]), \
            mock.patch.object(tun.TunHttpAdapter, 'open', return_value=adapter), \
            mock.patch.object(lite, 'build_dns_setup_commands', return_value=[['resolvectl', 'flush-caches']]), \
            mock.patch.object(lite, 'run_privileged_network_commands', return_value=0), \
            mock.patch.object(lite, 'wait_for_tunnel_dns', side_effect=probe), \
            mock.patch.object(lite, 'restart_active_tailscale_after_route_override', side_effect=tailscale), \
            mock.patch.object(lite, 'run_network_commands_best_effort', return_value=True):
        assert lite.system_start(args) == 0
        assert not path.exists()
