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
