import dataclasses
import io
import json
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

import pdalink_watch


def service_command(command):
    script = str(ROOT / "tools" / "pdalink.sh")
    if command[:2] == ["bash", script]:
        return command[2:]
    if command and command[0] == script:
        return command[1:]
    return []


def is_service_call(command, action):
    return service_command(command)[:3] == ["system", "service", action]


class FakeRunner:
    def __init__(
        self,
        devices: str,
        forwards: str = "",
        service_running: bool = False,
        service_healthy: bool = True,
        hotspot_result=None,
        target_result=None,
    ):
        self.devices = devices
        self.forwards = forwards
        self.service_running = service_running
        self.service_healthy = service_healthy
        self.hotspot_result = hotspot_result
        self.target_result = target_result
        self.calls = []

    def run(self, command, timeout):
        self.calls.append(list(command))
        if command == ["/adb", "devices", "-l"]:
            return pdalink_watch.CommandResult(0, self.devices, "")
        if command == ["/adb", "-s", "R3CR406AAGE", "forward", "--list"]:
            return pdalink_watch.CommandResult(0, self.forwards, "")
        if command[:3] == ["/adb", "-s", "R3CR406AAGE"] and command[3:] == ["forward", "tcp:18080", "tcp:8000"]:
            self.forwards = "R3CR406AAGE tcp:18080 tcp:8000\n"
            return pdalink_watch.CommandResult(0, "", "")
        if command in (
            ["/adb", "forward", "--remove", "tcp:18080"],
            ["/adb", "-s", "R3CR406AAGE", "forward", "--remove", "tcp:18080"],
        ):
            self.forwards = ""
            return pdalink_watch.CommandResult(0, "", "")
        if command[:3] == ["/adb", "-s", "R3CR406AAGE"] and command[3:6] == ["shell", "am", "start-foreground-service"]:
            return pdalink_watch.CommandResult(0, "Starting service: Intent\n", "")
        if "local-hotspot-sync" in command:
            return self.hotspot_result or pdalink_watch.CommandResult(0, "local-hotspot: inactive\n", "")
        if "local-hotspot-target-sync" in command:
            return self.target_result or pdalink_watch.CommandResult(0, "local-hotspot target: updated\n", "")
        if is_service_call(command, "status"):
            stdout = "wrapper: RUNNING pid 123\n" if self.service_running else "wrapper: MISSING .tools/runtime/pdalink-system.pid\n"
            return pdalink_watch.CommandResult(0 if self.service_healthy else 1, stdout, "route: MISSING" if not self.service_healthy else "")
        if is_service_call(command, "start"):
            self.service_running = True
            return pdalink_watch.CommandResult(0, "service: running\n", "")
        if is_service_call(command, "stop"):
            self.service_running = False
            return pdalink_watch.CommandResult(0, "system internet: stopped\n", "")
        raise AssertionError(f"unexpected command: {command!r}")


def authorized_devices():
    return "List of devices attached\nR3CR406AAGE device usb:3-1 model:SM_G996N\n"


def unauthorized_devices():
    return "List of devices attached\nR3CR406AAGE unauthorized usb:3-1\n"


def ok_probe(url, timeout):
    return pdalink_watch.ProxyCheck(True, f"{url}/proxy.pac HTTP 200")


def fail_probe(url, timeout):
    return pdalink_watch.ProxyCheck(False, "connection refused")


class SequenceProbe:
    def __init__(self, *checks):
        self.checks = list(checks)

    def __call__(self, url, timeout):
        if len(self.checks) > 1:
            return self.checks.pop(0)
        return self.checks[0]


class PdaLinkWatchTest(unittest.TestCase):
    def make_config(self, state_file):
        trusted_file = state_file.with_name("trusted-devices.json")
        config_file = state_file.with_name("config.json")
        trusted_file.write_text(json.dumps({
            "version": 1,
            "default_serial": "R3CR406AAGE",
            "devices": {
                "R3CR406AAGE": {
                    "serial": "R3CR406AAGE",
                    "name": "test-phone",
                },
            },
        }), encoding="utf-8")
        config_file.write_text(json.dumps({
            "version": 1,
            "auto_connect": "on_trusted_connect",
            "route_mode": "active_override",
            "dns_mode": "off",
            "notify": "auto",
        }), encoding="utf-8")
        return pdalink_watch.WatchConfig(
            adb="/adb",
            state_file=state_file,
            root=ROOT,
            verify_timeout=0,
            trusted_devices_file=trusted_file,
            config_file=config_file,
            enable_dbus=False,
        )

    def make_product_usb_config(self, state_file, product_state_file):
        config_file = state_file.with_name("config.json")
        config_file.write_text(json.dumps({
            "version": 1,
            "auto_connect": "on_trusted_connect",
            "route_mode": "active_override",
            "dns_mode": "off",
            "notify": "auto",
        }), encoding="utf-8")
        return pdalink_watch.WatchConfig(
            adb="/adb",
            state_file=state_file,
            root=ROOT,
            verify_timeout=0,
            config_file=config_file,
            transport="product-usb",
            product_usb_state_file=product_state_file,
            enable_dbus=False,
        )

    def write_product_usb_ready_state(self, path):
        path.write_text(json.dumps({
            "permission": "granted",
            "host_trust": "trusted",
            "transport_state": "connected",
            "proxy_state": "ready",
            "usb_debugging": "off",
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }), encoding="utf-8")

    def test_once_starts_android_forward_and_system_service(self):
        with tempfile.TemporaryDirectory() as tmp:
            state_file = Path(tmp) / "watch.json"
            runner = FakeRunner(authorized_devices())
            watcher = pdalink_watch.PdaLinkWatcher(
                self.make_config(state_file),
                runner=runner,
                probe=ok_probe,
                stdout=io.StringIO(),
            )

            self.assertEqual(watcher.reconcile(), 0)

            self.assertIn([
                "/adb",
                "-s",
                "R3CR406AAGE",
                "shell",
                "am",
                "start-foreground-service",
                "-n",
                "dev.pdalink.prototype/.ProxyService",
                "-a",
                "dev.pdalink.prototype.START_USB",
            ], runner.calls)
            self.assertIn(["/adb", "-s", "R3CR406AAGE", "forward", "tcp:18080", "tcp:8000"], runner.calls)
            self.assertTrue(any(is_service_call(call, "start") for call in runner.calls))

            payload = json.loads(state_file.read_text(encoding="utf-8"))
            self.assertEqual(payload["mode"], "ready")
            self.assertTrue(payload["forward_owned"])
            self.assertTrue(payload["service_owned"])
            self.assertEqual(payload["proxy_url"], "http://127.0.0.1:18080")

    def test_ready_path_records_internet_health_without_tearing_down(self):
        with tempfile.TemporaryDirectory() as tmp:
            state_file = Path(tmp) / "watch.json"
            config = dataclasses.replace(self.make_config(state_file), internet_check_interval=60.0)
            clock = [1000.0]
            sequence = SequenceProbe(
                pdalink_watch.ProxyCheck(False, "timed out after 6000 ms"),
                pdalink_watch.ProxyCheck(True, "HTTP 204 in 80 ms"),
            )
            calls = []

            def internet(url, timeout):
                calls.append(url)
                return sequence(url, timeout)
            stdout = io.StringIO()
            watcher = pdalink_watch.PdaLinkWatcher(
                config,
                runner=FakeRunner(authorized_devices()),
                probe=ok_probe,
                internet_probe=internet,
                boot_clock=lambda: clock[0],
                stdout=stdout,
            )

            self.assertEqual(watcher.reconcile(), 0)
            payload = json.loads(state_file.read_text(encoding="utf-8"))
            self.assertEqual(payload["mode"], "ready")
            self.assertEqual(payload["health"]["internet"], "failing")
            self.assertEqual(payload["health"]["system"], "ready")

            # Within the failing re-check window the probe is not repeated.
            clock[0] += 5.0
            watcher.reconcile()
            self.assertEqual(calls, ["http://127.0.0.1:18080"])

            clock[0] += pdalink_watch.INTERNET_FAILING_RECHECK
            self.assertEqual(watcher.reconcile(), 0)
            payload = json.loads(state_file.read_text(encoding="utf-8"))
            self.assertEqual(payload["mode"], "ready")
            self.assertEqual(payload["health"]["internet"], "ok")
            self.assertIn("internet: recovered after 1 failed check(s)", stdout.getvalue())

    def test_internet_check_is_disabled_by_default(self):
        with tempfile.TemporaryDirectory() as tmp:
            state_file = Path(tmp) / "watch.json"

            def unexpected_probe(url, timeout):
                raise AssertionError("internet probe must not run when disabled")

            watcher = pdalink_watch.PdaLinkWatcher(
                self.make_config(state_file),
                runner=FakeRunner(authorized_devices()),
                probe=ok_probe,
                internet_probe=unexpected_probe,
                stdout=io.StringIO(),
            )

            self.assertEqual(watcher.reconcile(), 0)
            payload = json.loads(state_file.read_text(encoding="utf-8"))
            self.assertNotIn("internet", payload["health"])

    def test_ready_path_syncs_active_local_hotspot_when_configured(self):
        with tempfile.TemporaryDirectory() as tmp:
            state_file = Path(tmp) / "watch.json"
            config = self.make_config(state_file)
            user_config = json.loads(config.config_file.read_text(encoding="utf-8"))
            user_config["local_hotspot_credentials"] = "auto"
            config.config_file.write_text(json.dumps(user_config), encoding="utf-8")
            runner = FakeRunner(authorized_devices())
            watcher = pdalink_watch.PdaLinkWatcher(
                config,
                runner=runner,
                probe=ok_probe,
                stdout=io.StringIO(),
            )

            self.assertEqual(watcher.reconcile(), 0)

            sync_calls = [call for call in runner.calls if "local-hotspot-sync" in call]
            self.assertEqual(len(sync_calls), 1)
            self.assertIn("auto", sync_calls[0])
            self.assertIn("R3CR406AAGE", sync_calls[0])

    def test_if_disconnected_join_does_not_displace_unrelated_wifi(self):
        with tempfile.TemporaryDirectory() as tmp:
            state_file = Path(tmp) / "watch.json"
            config = self.make_config(state_file)
            user_config = json.loads(config.config_file.read_text(encoding="utf-8"))
            user_config.update({
                "local_hotspot_credentials": "auto",
                "local_hotspot_join": "if_disconnected",
            })
            config.config_file.write_text(json.dumps(user_config), encoding="utf-8")
            runner = FakeRunner(authorized_devices())
            watcher = pdalink_watch.PdaLinkWatcher(
                config,
                runner=runner,
                probe=ok_probe,
                stdout=io.StringIO(),
            )

            with mock.patch.object(
                pdalink_watch.pdalink_lite,
                "active_nm_connections",
                return_value=[pdalink_watch.pdalink_lite.NmActiveConnection(
                    "office-wifi", "802-11-wireless", "wlp1s0"
                )],
            ):
                self.assertEqual(watcher.reconcile(), 0)

            sync_call = next(call for call in runner.calls if "local-hotspot-sync" in call)
            self.assertNotIn("--connect", sync_call)

    def test_if_disconnected_join_connects_when_only_managed_wifi_is_active(self):
        with tempfile.TemporaryDirectory() as tmp:
            state_file = Path(tmp) / "watch.json"
            config = self.make_config(state_file)
            user_config = json.loads(config.config_file.read_text(encoding="utf-8"))
            user_config.update({
                "local_hotspot_credentials": "auto",
                "local_hotspot_join": "if_disconnected",
                "local_hotspot_target": "pdalink-zero",
            })
            config.config_file.write_text(json.dumps(user_config), encoding="utf-8")
            runner = FakeRunner(authorized_devices())
            watcher = pdalink_watch.PdaLinkWatcher(
                config,
                runner=runner,
                probe=ok_probe,
                stdout=io.StringIO(),
            )

            with mock.patch.object(
                pdalink_watch.pdalink_lite,
                "active_nm_connections",
                return_value=[pdalink_watch.pdalink_lite.NmActiveConnection(
                    "pdalink-local-hotspot", "802-11-wireless", "wlp1s0"
                )],
            ):
                self.assertEqual(watcher.reconcile(), 0)

            sync_call = next(call for call in runner.calls if "local-hotspot-sync" in call)
            self.assertIn("--connect", sync_call)
            self.assertNotIn("--target", sync_call)

    def test_product_usb_once_starts_guarded_system_service_without_adb(self):
        with tempfile.TemporaryDirectory() as tmp:
            state_file = Path(tmp) / "watch.json"
            product_state_file = Path(tmp) / "product-usb.json"
            self.write_product_usb_ready_state(product_state_file)
            runner = FakeRunner("")
            watcher = pdalink_watch.PdaLinkWatcher(
                self.make_product_usb_config(state_file, product_state_file),
                runner=runner,
                probe=ok_probe,
                stdout=io.StringIO(),
            )

            self.assertEqual(watcher.reconcile(), 0)

            self.assertFalse(any(call and call[0] == "/adb" for call in runner.calls))
            start_calls = [call for call in runner.calls if is_service_call(call, "start")]
            self.assertTrue(start_calls)
            self.assertIn("--require-product-usb", start_calls[-1])
            self.assertIn("--product-usb-state-file", start_calls[-1])
            self.assertIn(str(product_state_file), start_calls[-1])

            payload = json.loads(state_file.read_text(encoding="utf-8"))
            self.assertEqual(payload["mode"], "ready")
            self.assertTrue(payload["service_owned"])
            self.assertFalse(payload["forward_owned"])

    def test_product_usb_not_ready_stops_owned_service_without_adb(self):
        with tempfile.TemporaryDirectory() as tmp:
            state_file = Path(tmp) / "watch.json"
            pdalink_watch.WatchState(
                mode="ready",
                forward_owned=True,
                service_owned=True,
                proxy_url="http://127.0.0.1:18080",
            ).save(state_file)
            product_state_file = Path(tmp) / "product-usb.json"
            product_state_file.write_text(json.dumps({
                "permission": "granted",
                "host_trust": "trusted",
                "transport_state": "disconnected",
                "proxy_state": "ready",
                "usb_debugging": "off",
                "updated_at": datetime.now(timezone.utc).isoformat(),
            }), encoding="utf-8")
            runner = FakeRunner("", service_running=True)
            watcher = pdalink_watch.PdaLinkWatcher(
                self.make_product_usb_config(state_file, product_state_file),
                runner=runner,
                probe=ok_probe,
                stdout=io.StringIO(),
            )

            self.assertEqual(watcher.reconcile(), 1)

            self.assertFalse(any(call and call[0] == "/adb" for call in runner.calls))
            self.assertTrue(any(is_service_call(call, "stop") for call in runner.calls))
            self.assertFalse(any(is_service_call(call, "start") for call in runner.calls))
            payload = json.loads(state_file.read_text(encoding="utf-8"))
            self.assertEqual(payload["mode"], "waiting-product-usb")
            self.assertEqual(payload["last_error"], "transport unavailable")
            self.assertFalse(payload["service_owned"])
            self.assertFalse(payload["forward_owned"])

    def test_product_usb_ready_does_not_reuse_unowned_running_service(self):
        with tempfile.TemporaryDirectory() as tmp:
            state_file = Path(tmp) / "watch.json"
            product_state_file = Path(tmp) / "product-usb.json"
            self.write_product_usb_ready_state(product_state_file)
            runner = FakeRunner("", service_running=True)
            watcher = pdalink_watch.PdaLinkWatcher(
                self.make_product_usb_config(state_file, product_state_file),
                runner=runner,
                probe=ok_probe,
                stdout=io.StringIO(),
            )

            self.assertEqual(watcher.reconcile(), 1)

            self.assertFalse(any(call and call[0] == "/adb" for call in runner.calls))
            self.assertFalse(any(is_service_call(call, "start") for call in runner.calls))
            payload = json.loads(state_file.read_text(encoding="utf-8"))
            self.assertEqual(payload["mode"], "service-conflict")
            self.assertIn("already running outside product USB watcher", payload["last_error"])

    def test_ready_path_does_not_restart_android_when_proxy_is_alive(self):
        with tempfile.TemporaryDirectory() as tmp:
            state_file = Path(tmp) / "watch.json"
            state = pdalink_watch.WatchState(
                mode="ready",
                device_serial="R3CR406AAGE",
                device_state="device",
                forward_owned=True,
                service_owned=True,
                local_port=18080,
                remote_port=8000,
                proxy_url="http://127.0.0.1:18080",
            )
            state.save(state_file)
            runner = FakeRunner(authorized_devices(), "R3CR406AAGE tcp:18080 tcp:8000\n", service_running=True)
            watcher = pdalink_watch.PdaLinkWatcher(
                self.make_config(state_file),
                runner=runner,
                probe=ok_probe,
                stdout=io.StringIO(),
            )

            self.assertEqual(watcher.reconcile(), 0)

            self.assertFalse(any(call[-2:] == ["-a", "dev.pdalink.prototype.START_USB"] for call in runner.calls))
            self.assertFalse(any(is_service_call(call, "start") for call in runner.calls))

    def test_existing_forward_restarts_android_when_proxy_is_down(self):
        with tempfile.TemporaryDirectory() as tmp:
            state_file = Path(tmp) / "watch.json"
            state = pdalink_watch.WatchState(
                mode="ready",
                device_serial="R3CR406AAGE",
                device_state="device",
                forward_owned=True,
                service_owned=True,
                local_port=18080,
                remote_port=8000,
                proxy_url="http://127.0.0.1:18080",
            )
            state.save(state_file)
            runner = FakeRunner(authorized_devices(), "R3CR406AAGE tcp:18080 tcp:8000\n")
            watcher = pdalink_watch.PdaLinkWatcher(
                self.make_config(state_file),
                runner=runner,
                probe=SequenceProbe(
                    pdalink_watch.ProxyCheck(False, "connection refused"),
                    pdalink_watch.ProxyCheck(True, "http://127.0.0.1:18080/proxy.pac HTTP 200"),
                ),
                stdout=io.StringIO(),
            )

            self.assertEqual(watcher.reconcile(), 0)

            self.assertTrue(any(call[-2:] == ["-a", "dev.pdalink.prototype.START_USB"] for call in runner.calls))
            self.assertTrue(any(is_service_call(call, "start") for call in runner.calls))

    def test_stale_owned_forward_is_recreated_in_same_reconcile(self):
        with tempfile.TemporaryDirectory() as tmp:
            state_file = Path(tmp) / "watch.json"
            pdalink_watch.WatchState(
                mode="ready",
                device_serial="R3CR406AAGE",
                device_state="device",
                forward_owned=True,
                service_owned=False,
                local_port=18080,
                remote_port=8000,
                proxy_url="http://127.0.0.1:18080",
            ).save(state_file)
            runner = FakeRunner(
                authorized_devices(),
                "R3CR406AAGE tcp:18080 tcp:8000\n",
            )
            watcher = pdalink_watch.PdaLinkWatcher(
                self.make_config(state_file),
                runner=runner,
                probe=SequenceProbe(
                    pdalink_watch.ProxyCheck(False, "connection refused"),
                    pdalink_watch.ProxyCheck(False, "stale forward"),
                    pdalink_watch.ProxyCheck(True, "HTTP 200"),
                ),
                stdout=io.StringIO(),
            )

            self.assertEqual(watcher.reconcile(), 0)

            remove = ["/adb", "-s", "R3CR406AAGE", "forward", "--remove", "tcp:18080"]
            create = ["/adb", "-s", "R3CR406AAGE", "forward", "tcp:18080", "tcp:8000"]
            self.assertIn(remove, runner.calls)
            self.assertIn(create, runner.calls)
            payload = json.loads(state_file.read_text(encoding="utf-8"))
            self.assertEqual(payload["health"]["forward"], "repaired")

    def test_owned_unhealthy_system_service_is_restarted(self):
        with tempfile.TemporaryDirectory() as tmp:
            state_file = Path(tmp) / "watch.json"
            pdalink_watch.WatchState(
                mode="ready",
                device_serial="R3CR406AAGE",
                device_state="device",
                forward_owned=True,
                service_owned=True,
                local_port=18080,
                remote_port=8000,
                proxy_url="http://127.0.0.1:18080",
            ).save(state_file)
            runner = FakeRunner(
                authorized_devices(),
                "R3CR406AAGE tcp:18080 tcp:8000\n",
                service_running=True,
                service_healthy=False,
            )
            watcher = pdalink_watch.PdaLinkWatcher(
                self.make_config(state_file),
                runner=runner,
                probe=ok_probe,
                stdout=io.StringIO(),
            )

            self.assertEqual(watcher.reconcile(), 0)

            stop_index = next(index for index, call in enumerate(runner.calls) if is_service_call(call, "stop"))
            start_index = next(index for index, call in enumerate(runner.calls) if is_service_call(call, "start"))
            self.assertLess(stop_index, start_index)

    def test_target_relay_failure_does_not_fail_local_hotspot_or_usb(self):
        with tempfile.TemporaryDirectory() as tmp:
            state_file = Path(tmp) / "watch.json"
            config = self.make_config(state_file)
            user_config = json.loads(config.config_file.read_text(encoding="utf-8"))
            user_config.update({
                "local_hotspot_credentials": "auto",
                "local_hotspot_target": "pdalink-zero",
            })
            config.config_file.write_text(json.dumps(user_config), encoding="utf-8")
            fingerprint = "a" * 64
            runner = FakeRunner(
                authorized_devices(),
                hotspot_result=pdalink_watch.CommandResult(
                    0,
                    f"local-hotspot: updated\ncredential-fingerprint: {fingerprint}\n",
                    "",
                ),
                target_result=pdalink_watch.CommandResult(1, "", "ssh target unavailable"),
            )
            watcher = pdalink_watch.PdaLinkWatcher(
                config,
                runner=runner,
                probe=ok_probe,
                stdout=io.StringIO(),
            )

            self.assertEqual(watcher.reconcile(), 0)

            payload = json.loads(state_file.read_text(encoding="utf-8"))
            self.assertEqual(payload["mode"], "ready")
            self.assertEqual(payload["health"]["hotspot"], "ready")
            self.assertIn("ssh target unavailable", payload["health"]["hotspot_target"])
            self.assertEqual(payload["hotspot_credential_fingerprint"], fingerprint)
            self.assertEqual(payload["hotspot_relay_fingerprint"], "")

    def test_unauthorized_device_stops_owned_service_and_forward(self):
        with tempfile.TemporaryDirectory() as tmp:
            state_file = Path(tmp) / "watch.json"
            state = pdalink_watch.WatchState(
                mode="ready",
                device_serial="R3CR406AAGE",
                device_state="device",
                forward_owned=True,
                service_owned=True,
                local_port=18080,
                remote_port=8000,
                proxy_url="http://127.0.0.1:18080",
            )
            state.save(state_file)
            runner = FakeRunner(unauthorized_devices(), "R3CR406AAGE tcp:18080 tcp:8000\n", service_running=True)
            watcher = pdalink_watch.PdaLinkWatcher(
                self.make_config(state_file),
                runner=runner,
                probe=ok_probe,
                stdout=io.StringIO(),
            )

            self.assertEqual(watcher.reconcile(), 1)

            self.assertTrue(any(is_service_call(call, "stop") for call in runner.calls))
            self.assertIn(["/adb", "-s", "R3CR406AAGE", "forward", "--remove", "tcp:18080"], runner.calls)
            self.assertFalse(any(call[-2:] == ["-a", "dev.pdalink.prototype.START_USB"] for call in runner.calls))

            payload = json.loads(state_file.read_text(encoding="utf-8"))
            self.assertEqual(payload["mode"], "waiting-adb-authorization")
            self.assertFalse(payload["forward_owned"])
            self.assertFalse(payload["service_owned"])

    def test_forward_conflict_prevents_android_and_system_start(self):
        with tempfile.TemporaryDirectory() as tmp:
            runner = FakeRunner(authorized_devices(), "R3CR406AAGE tcp:18080 tcp:9000\n")
            watcher = pdalink_watch.PdaLinkWatcher(
                self.make_config(Path(tmp) / "watch.json"),
                runner=runner,
                probe=ok_probe,
                stdout=io.StringIO(),
            )

            self.assertEqual(watcher.reconcile(), 1)

            self.assertFalse(any(call[-2:] == ["-a", "dev.pdalink.prototype.START_USB"] for call in runner.calls))
            self.assertFalse(any(call[-2:] == ["-a", "dev.pdalink.prototype.STOP_USB"] for call in runner.calls))
            self.assertFalse(any(is_service_call(call, "start") for call in runner.calls))

    def test_proxy_failure_cleans_forward_but_does_not_start_service(self):
        with tempfile.TemporaryDirectory() as tmp:
            state_file = Path(tmp) / "watch.json"
            runner = FakeRunner(authorized_devices())
            watcher = pdalink_watch.PdaLinkWatcher(
                self.make_config(state_file),
                runner=runner,
                probe=fail_probe,
                stdout=io.StringIO(),
            )

            self.assertEqual(watcher.reconcile(), 1)

            self.assertIn(["/adb", "-s", "R3CR406AAGE", "forward", "--remove", "tcp:18080"], runner.calls)
            self.assertFalse(any(is_service_call(call, "start") for call in runner.calls))
            payload = json.loads(state_file.read_text(encoding="utf-8"))
            self.assertEqual(payload["mode"], "proxy-unreachable")
            self.assertFalse(payload["forward_owned"])

    def test_status_does_not_mutate_android_forward_or_service(self):
        with tempfile.TemporaryDirectory() as tmp:
            output = io.StringIO()
            runner = FakeRunner(authorized_devices(), "R3CR406AAGE tcp:18080 tcp:8000\n", service_running=True)
            watcher = pdalink_watch.PdaLinkWatcher(
                self.make_config(Path(tmp) / "watch.json"),
                runner=runner,
                probe=ok_probe,
                stdout=output,
            )

            self.assertEqual(watcher.status(), 0)

            self.assertFalse(any(call[-2:] == ["-a", "dev.pdalink.prototype.START_USB"] for call in runner.calls))
            self.assertFalse(any(is_service_call(call, "start") for call in runner.calls))
            self.assertIn("system-service: RUNNING", output.getvalue())

    def test_status_names_adb_as_development_transport(self):
        with tempfile.TemporaryDirectory() as tmp:
            output = io.StringIO()
            runner = FakeRunner(authorized_devices(), "R3CR406AAGE tcp:18080 tcp:8000\n", service_running=True)
            watcher = pdalink_watch.PdaLinkWatcher(
                self.make_config(Path(tmp) / "watch.json"),
                runner=runner,
                probe=ok_probe,
                stdout=output,
            )

            self.assertEqual(watcher.status(), 0)

            self.assertIn("transport: adb (development)", output.getvalue())

    def test_untrusted_authorized_device_does_not_start_android(self):
        with tempfile.TemporaryDirectory() as tmp:
            state_file = Path(tmp) / "watch.json"
            trusted_file = Path(tmp) / "trusted-devices.json"
            config_file = Path(tmp) / "config.json"
            trusted_file.write_text(json.dumps({"version": 1, "default_serial": "", "devices": {}}), encoding="utf-8")
            config_file.write_text(json.dumps({
                "version": 1,
                "auto_connect": "on_trusted_connect",
                "route_mode": "active_override",
                "dns_mode": "off",
                "notify": "auto",
            }), encoding="utf-8")
            runner = FakeRunner(authorized_devices())
            watcher = pdalink_watch.PdaLinkWatcher(
                pdalink_watch.WatchConfig(
                    adb="/adb",
                    state_file=state_file,
                    root=ROOT,
                    verify_timeout=0,
                    trusted_devices_file=trusted_file,
                    config_file=config_file,
                ),
                runner=runner,
                probe=ok_probe,
                stdout=io.StringIO(),
            )

            self.assertEqual(watcher.reconcile(), 1)
            self.assertFalse(any(call[-2:] == ["-a", "dev.pdalink.prototype.START_USB"] for call in runner.calls))
            payload = json.loads(state_file.read_text(encoding="utf-8"))
            self.assertEqual(payload["mode"], "waiting-trusted-device")

    def test_trust_revoked_stops_previous_android_service(self):
        with tempfile.TemporaryDirectory() as tmp:
            state_file = Path(tmp) / "watch.json"
            state = pdalink_watch.WatchState(
                mode="ready",
                device_serial="R3CR406AAGE",
                device_state="device",
                forward_owned=True,
                service_owned=True,
                local_port=18080,
                remote_port=8000,
                proxy_url="http://127.0.0.1:18080",
            )
            state.save(state_file)
            trusted_file = Path(tmp) / "trusted-devices.json"
            config_file = Path(tmp) / "config.json"
            trusted_file.write_text(json.dumps({"version": 1, "default_serial": "", "devices": {}}), encoding="utf-8")
            config_file.write_text(json.dumps({
                "version": 1,
                "auto_connect": "on_trusted_connect",
                "route_mode": "active_override",
                "dns_mode": "off",
                "notify": "auto",
            }), encoding="utf-8")
            runner = FakeRunner(authorized_devices(), "R3CR406AAGE tcp:18080 tcp:8000\n", service_running=True)
            watcher = pdalink_watch.PdaLinkWatcher(
                pdalink_watch.WatchConfig(
                    adb="/adb",
                    state_file=state_file,
                    root=ROOT,
                    verify_timeout=0,
                    trusted_devices_file=trusted_file,
                    config_file=config_file,
                ),
                runner=runner,
                probe=ok_probe,
                stdout=io.StringIO(),
            )

            self.assertEqual(watcher.reconcile(), 1)

            self.assertTrue(any(is_service_call(call, "stop") for call in runner.calls))
            self.assertIn(["/adb", "-s", "R3CR406AAGE", "forward", "--remove", "tcp:18080"], runner.calls)
            self.assertTrue(any(call[-2:] == ["-a", "dev.pdalink.prototype.STOP_USB"] for call in runner.calls))
            payload = json.loads(state_file.read_text(encoding="utf-8"))
            self.assertEqual(payload["mode"], "waiting-trusted-device")
            self.assertFalse(payload["forward_owned"])
            self.assertFalse(payload["service_owned"])

    def test_auto_connect_off_does_not_start_android(self):
        with tempfile.TemporaryDirectory() as tmp:
            state_file = Path(tmp) / "watch.json"
            config = self.make_config(state_file)
            config.config_file.write_text(json.dumps({
                "version": 1,
                "auto_connect": "off",
                "route_mode": "active_override",
                "dns_mode": "off",
                "notify": "auto",
            }), encoding="utf-8")
            runner = FakeRunner(authorized_devices())
            watcher = pdalink_watch.PdaLinkWatcher(
                config,
                runner=runner,
                probe=ok_probe,
                stdout=io.StringIO(),
            )

            self.assertEqual(watcher.reconcile(), 1)
            self.assertFalse(any(call[-2:] == ["-a", "dev.pdalink.prototype.START_USB"] for call in runner.calls))
            payload = json.loads(state_file.read_text(encoding="utf-8"))
            self.assertEqual(payload["mode"], "auto-connect-off")

    def test_auto_connect_off_stops_previous_android_service(self):
        with tempfile.TemporaryDirectory() as tmp:
            state_file = Path(tmp) / "watch.json"
            state = pdalink_watch.WatchState(
                mode="ready",
                device_serial="R3CR406AAGE",
                device_state="device",
                forward_owned=True,
                service_owned=True,
                local_port=18080,
                remote_port=8000,
                proxy_url="http://127.0.0.1:18080",
            )
            state.save(state_file)
            config = self.make_config(state_file)
            config.config_file.write_text(json.dumps({
                "version": 1,
                "auto_connect": "off",
                "route_mode": "active_override",
                "dns_mode": "off",
                "notify": "auto",
            }), encoding="utf-8")
            runner = FakeRunner(authorized_devices(), "R3CR406AAGE tcp:18080 tcp:8000\n", service_running=True)
            watcher = pdalink_watch.PdaLinkWatcher(
                config,
                runner=runner,
                probe=ok_probe,
                stdout=io.StringIO(),
            )

            self.assertEqual(watcher.reconcile(), 1)

            self.assertTrue(any(is_service_call(call, "stop") for call in runner.calls))
            self.assertIn(["/adb", "-s", "R3CR406AAGE", "forward", "--remove", "tcp:18080"], runner.calls)
            self.assertTrue(any(call[-2:] == ["-a", "dev.pdalink.prototype.STOP_USB"] for call in runner.calls))
            payload = json.loads(state_file.read_text(encoding="utf-8"))
            self.assertEqual(payload["mode"], "auto-connect-off")
            self.assertFalse(payload["forward_owned"])
            self.assertFalse(payload["service_owned"])

    def test_sigterm_stop_runs_cleanup(self):
        with tempfile.TemporaryDirectory() as tmp:
            state_file = Path(tmp) / "watch.json"
            state = pdalink_watch.WatchState(
                mode="ready",
                device_serial="R3CR406AAGE",
                device_state="device",
                forward_owned=True,
                service_owned=True,
                local_port=18080,
                remote_port=8000,
                proxy_url="http://127.0.0.1:18080",
            )
            state.save(state_file)
            runner = FakeRunner(authorized_devices(), "R3CR406AAGE tcp:18080 tcp:8000\n", service_running=True)

            def stop_after_first_loop(_delay):
                raise pdalink_watch.StopRequested("SIGTERM")

            watcher = pdalink_watch.PdaLinkWatcher(
                self.make_config(state_file),
                runner=runner,
                probe=ok_probe,
                sleeper=stop_after_first_loop,
                stdout=io.StringIO(),
            )

            self.assertEqual(watcher.run_loop(), 0)

            self.assertTrue(any(is_service_call(call, "stop") for call in runner.calls))
            self.assertIn(["/adb", "-s", "R3CR406AAGE", "forward", "--remove", "tcp:18080"], runner.calls)
            self.assertTrue(any(call[-2:] == ["-a", "dev.pdalink.prototype.STOP_USB"] for call in runner.calls))
            payload = json.loads(state_file.read_text(encoding="utf-8"))
            self.assertEqual(payload["mode"], "stopped")
            self.assertFalse(payload["forward_owned"])
            self.assertFalse(payload["service_owned"])

    def test_run_loop_detects_suspend_gap_and_revalidates_ready_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            state_file = Path(tmp) / "watch.json"
            pdalink_watch.WatchState(
                mode="ready",
                device_serial="R3CR406AAGE",
                device_state="device",
                forward_owned=True,
                service_owned=True,
                local_port=18080,
                remote_port=8000,
                proxy_url="http://127.0.0.1:18080",
            ).save(state_file)
            ticks = iter((0.0, 0.0, 2.0, 20.0))
            output = io.StringIO()
            watcher = pdalink_watch.PdaLinkWatcher(
                self.make_config(state_file),
                runner=FakeRunner(
                    authorized_devices(),
                    "R3CR406AAGE tcp:18080 tcp:8000\n",
                    service_running=True,
                ),
                probe=ok_probe,
                sleeper=lambda _delay: None,
                boot_clock=lambda: next(ticks),
                stdout=output,
            )

            self.assertEqual(watcher.run_loop(max_loops=2), 0)

            payload = json.loads(state_file.read_text(encoding="utf-8"))
            self.assertTrue(payload["last_resume_at"])
            self.assertEqual(payload["mode"], "ready")
            self.assertIn("recovering-resume", output.getvalue())

    def test_repeated_ready_reconcile_does_not_rewrite_or_relog_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            state_file = Path(tmp) / "watch.json"
            output = io.StringIO()
            runner = FakeRunner(authorized_devices())
            watcher = pdalink_watch.PdaLinkWatcher(
                self.make_config(state_file),
                runner=runner,
                probe=ok_probe,
                stdout=output,
            )

            self.assertEqual(watcher.reconcile(), 0)
            first_updated_at = json.loads(state_file.read_text(encoding="utf-8"))["updated_at"]
            output.seek(0)
            output.truncate(0)
            self.assertEqual(watcher.reconcile(), 0)

            second_updated_at = json.loads(state_file.read_text(encoding="utf-8"))["updated_at"]
            self.assertEqual(second_updated_at, first_updated_at)
            self.assertEqual(output.getvalue(), "")

    def test_when_offline_policy_does_not_start_until_implemented(self):
        with tempfile.TemporaryDirectory() as tmp:
            state_file = Path(tmp) / "watch.json"
            config = self.make_config(state_file)
            config.config_file.write_text(json.dumps({
                "version": 1,
                "auto_connect": "when_offline",
                "route_mode": "active_override",
                "dns_mode": "off",
                "notify": "auto",
            }), encoding="utf-8")
            runner = FakeRunner(authorized_devices())
            watcher = pdalink_watch.PdaLinkWatcher(
                config,
                runner=runner,
                probe=ok_probe,
                stdout=io.StringIO(),
            )

            self.assertEqual(watcher.reconcile(), 1)
            self.assertFalse(any(call[-2:] == ["-a", "dev.pdalink.prototype.START_USB"] for call in runner.calls))
            payload = json.loads(state_file.read_text(encoding="utf-8"))
            self.assertEqual(payload["mode"], "waiting-offline-policy")

    def test_user_route_config_passed_to_system_service(self):
        with tempfile.TemporaryDirectory() as tmp:
            state_file = Path(tmp) / "watch.json"
            config = self.make_config(state_file)
            config.config_file.write_text(json.dumps({
                "version": 1,
                "auto_connect": "on_trusted_connect",
                "route_mode": "fallback_low_priority",
                "dns_mode": "off",
                "notify": "auto",
            }), encoding="utf-8")
            runner = FakeRunner(authorized_devices())
            watcher = pdalink_watch.PdaLinkWatcher(
                config,
                runner=runner,
                probe=ok_probe,
                stdout=io.StringIO(),
            )

            self.assertEqual(watcher.reconcile(), 0)
            start_calls = [call for call in runner.calls if is_service_call(call, "start")]
            self.assertTrue(start_calls)
            self.assertIn("--route-mode", start_calls[-1])
            self.assertIn("fallback_low_priority", start_calls[-1])
            self.assertIn("--dns-mode", start_calls[-1])
            self.assertIn("off", start_calls[-1])
            self.assertIn("--tcp-idle-timeout", start_calls[-1])
            self.assertIn("0.0", start_calls[-1])


if __name__ == "__main__":
    unittest.main()


def test_two_phones_only_trusted_host_is_targeted(tmp_path):
    config = PdaLinkWatchTest().make_config(tmp_path / "watch.json")
    # Untrusted phone deliberately precedes the trusted host.
    runner = FakeRunner("List of devices attached\nR39M202FZ7E device model:SM_G975N\n"
                        "R3CR406AAGE device model:SM_G996N\n")
    watcher = pdalink_watch.PdaLinkWatcher(config, runner=runner, probe=ok_probe, stdout=io.StringIO())
    assert watcher.reconcile() == 0
    assert watcher.state.device_serial == "R3CR406AAGE"
    start = next(c for c in runner.calls if is_service_call(c, "start"))
    assert start[start.index("--adb-serial") + 1] == "R3CR406AAGE"
    assert watcher.reconcile() == 0
    watcher.cleanup("stopped", watcher.previous_device())
    for command in runner.calls:
        if command[0] == "/adb" and command[1:] != ["devices", "-l"]:
            assert command[1:3] == ["-s", "R3CR406AAGE"]

def test_delegated_usb_start_keeps_selected_adb_serial():
    lite = pdalink_watch.pdalink_lite
    args = lite.build_parser().parse_args(['usb-start', '--adb-serial', 'R3CR406AAGE'])
    with mock.patch.object(lite, 'adb_command', return_value=['/fake/adb']), \
            mock.patch.object(lite, 'run_command', return_value=lite.CommandResult(0, '', '')) as command:
        assert lite.usb_start(args) == 0
        assert len(command.call_args_list) == 2
        for call in command.call_args_list:
            assert call.args[0][:3] == ['/fake/adb', '-s', 'R3CR406AAGE']


class MismatchRunner(FakeRunner):
    """Old server rejects serial-selected writes until restarted."""
    def __init__(self):
        super().__init__(authorized_devices() + "R39M202FZ7E device\n",
                         forwards="R39M202FZ7E tcp:22222 tcp:2222\n")
        self.mismatch = True
        self.snapshot_fails = False
        self.restore_fails = False

    def run(self, command, timeout):
        args = command[1:]
        if args == ["forward", "--list"]:
            self.calls.append(command)
            return pdalink_watch.CommandResult(1 if self.snapshot_fails else 0, self.forwards, "")
        if args == ["version"]:
            self.calls.append(command)
            return pdalink_watch.CommandResult(0, "Android Debug Bridge version 1.0.41\nVersion 37.0.0\n")
        if args in (["kill-server"], ["start-server"]):
            self.calls.append(command)
            self.forwards = ""
            if args == ["start-server"]:
                self.mismatch = False
            return pdalink_watch.CommandResult(0)
        if len(args) > 3 and args[2] == "forward" and args[3] != "--list":
            self.calls.append(command)
            if self.mismatch:
                return pdalink_watch.CommandResult(1, "", "adb: error: more than one device/emulator")
            if args[3] == "--no-rebind":
                if self.restore_fails:
                    return pdalink_watch.CommandResult(1, "", "device offline")
                self.forwards += f"{args[1]} {args[4]} {args[5]}\n"
            elif args[3] == "--remove":
                self.forwards = "\n".join(line for line in self.forwards.splitlines()
                                          if not line.startswith(args[1] + " "))
            else:
                self.forwards += f"{args[1]} {args[3]} {args[4]}\n"
            return pdalink_watch.CommandResult(0)
        return super().run(command, timeout)


def mismatch_watcher(tmp_path, **options):
    config = PdaLinkWatchTest().make_config(tmp_path / "watch.json")
    runner = MismatchRunner()
    output = io.StringIO()
    now = [1000.0]
    watcher = pdalink_watch.PdaLinkWatcher(dataclasses.replace(config, **options), runner=runner,
                                         probe=ok_probe, stdout=output, boot_clock=lambda: now[0])
    return watcher, runner, output, now


def test_mismatch_restarts_restores_foreign_and_retries(tmp_path):
    watcher, runner, output, _ = mismatch_watcher(tmp_path)
    device = pdalink_watch.pdalink_lite.parse_adb_devices(authorized_devices())[0]
    assert watcher.ensure_forward(device) == (True, "")
    assert "R39M202FZ7E tcp:22222 tcp:2222" in runner.forwards
    assert "R3CR406AAGE tcp:18080 tcp:8000" in runner.forwards
    assert "client Android Debug Bridge version 1.0.41 Version 37.0.0 vs running server" in output.getvalue()
    assert "restored R39M202FZ7E tcp:22222 tcp:2222" in output.getvalue()
    assert runner.calls.count(["/adb", "kill-server"]) == 1
    assert runner.calls.index(["/adb", "forward", "--list"]) < runner.calls.index(["/adb", "kill-server"])
    assert watcher.state.forward_owned


def test_mismatch_backoff_then_later_recovery(tmp_path):
    watcher, runner, output, now = mismatch_watcher(tmp_path)
    device = pdalink_watch.pdalink_lite.parse_adb_devices(authorized_devices())[0]
    assert watcher.ensure_forward(device)[0]
    runner.mismatch = True
    runner.forwards = ""
    ok, error = watcher.ensure_forward(device)
    assert not ok and "backoff active" in error
    watcher.ensure_forward(device)
    assert output.getvalue().count("backoff active") == 1
    assert runner.calls.count(["/adb", "kill-server"]) == 1
    now[0] += 301
    assert watcher.ensure_forward(device)[0]
    assert runner.calls.count(["/adb", "kill-server"]) == 2


def test_mismatch_opt_out_flag_and_env(tmp_path):
    parser = pdalink_watch.build_parser()
    with mock.patch.dict("os.environ", {"PDALINK_ADB_SERVER_RESTART": "0"}):
        args = pdalink_watch.build_parser().parse_args(["once"])
        assert not pdalink_watch.config_from_args(args).adb_server_restart
    assert not pdalink_watch.config_from_args(parser.parse_args(["once", "--no-adb-server-restart"])).adb_server_restart
    watcher, runner, _, _ = mismatch_watcher(tmp_path, adb_server_restart=False)
    device = pdalink_watch.pdalink_lite.parse_adb_devices(authorized_devices())[0]
    ok, error = watcher.ensure_forward(device)
    assert not ok and "restart disabled" in error
    assert ["/adb", "kill-server"] not in runner.calls


def test_mismatch_remove_restores_only_foreign(tmp_path):
    watcher, runner, _, _ = mismatch_watcher(tmp_path)
    watcher.state.device_serial = "R3CR406AAGE"
    watcher.state.forward_owned = True
    runner.forwards += "R3CR406AAGE tcp:18080 tcp:8000\n"
    watcher.remove_forward()
    assert not watcher.state.forward_owned
    assert "R39M202FZ7E tcp:22222 tcp:2222" in runner.forwards
    assert "tcp:18080" not in runner.forwards
    assert not any("--no-rebind" in c and "tcp:18080" in c for c in runner.calls)


def test_mismatch_requires_authorized_serial_and_snapshot(tmp_path):
    watcher, runner, _, _ = mismatch_watcher(tmp_path)
    device = pdalink_watch.pdalink_lite.parse_adb_devices(authorized_devices())[0]
    runner.devices = unauthorized_devices()
    assert not watcher.ensure_forward(device)[0]
    assert ["/adb", "kill-server"] not in runner.calls
    runner.devices = authorized_devices()
    runner.snapshot_fails = True
    assert "cannot snapshot" in watcher.ensure_forward(device)[1]
    assert ["/adb", "kill-server"] not in runner.calls


def test_failover_forward_error_in_log_and_status(tmp_path):
    watcher, runner, output, _ = mismatch_watcher(tmp_path, wifi_direct_failover=True,
                                                adb_server_restart=False)
    with mock.patch.object(watcher, "direct_ready", return_value=True):
        assert watcher.reconcile() == 0
        assert watcher.reconcile() == 0
    assert "adb-server-mismatch" in watcher.state.health["usb"]
    assert output.getvalue().count("usb: adb-server-mismatch") == 1
    watcher.status()
    assert "health-usb: adb-server-mismatch" in output.getvalue()


def test_lite_serial_forward_reports_mismatch(tmp_path, capsys):
    lite = pdalink_watch.pdalink_lite
    with mock.patch.object(lite, "adb_run", side_effect=[
        lite.CommandResult(1, "", "adb: error: more than one device/emulator"),
        lite.CommandResult(0, authorized_devices(), ""),
    ]):
        assert lite.adb_forward_tcp(18080, 8000, "R3CR406AAGE") == 1
    assert "adb-server-mismatch" in capsys.readouterr().err


def test_failover_android_start_error_in_log_and_status(tmp_path):
    watcher, _, output, _ = mismatch_watcher(tmp_path, wifi_direct_failover=True)
    with mock.patch.object(watcher, "direct_ready", return_value=True), \
            mock.patch.object(watcher, "start_android", return_value=(False, "Android service denied")):
        assert watcher.reconcile() == 0
        assert watcher.reconcile() == 0
    assert watcher.state.health["usb"] == "Android service denied"
    assert output.getvalue().count("usb: Android service denied") == 1


def test_mismatch_failed_restore_is_logged(tmp_path):
    watcher, runner, output, _ = mismatch_watcher(tmp_path)
    runner.restore_fails = True
    device = pdalink_watch.pdalink_lite.parse_adb_devices(authorized_devices())[0]
    assert watcher.ensure_forward(device)[0]
    assert "restore failed R39M202FZ7E tcp:22222 tcp:2222: device offline" in output.getvalue()


def test_mismatch_retry_only_once(tmp_path):
    watcher, runner, _, _ = mismatch_watcher(tmp_path)
    original = runner.run

    def still_mismatched(command, timeout):
        result = original(command, timeout)
        if command == ["/adb", "start-server"]:
            runner.mismatch = True
        return result

    runner.run = still_mismatched
    device = pdalink_watch.pdalink_lite.parse_adb_devices(authorized_devices())[0]
    ok, error = watcher.ensure_forward(device)
    assert not ok and "retry failed" in error
    assert runner.calls.count(["/adb", "-s", device.serial, "forward", "tcp:18080", "tcp:8000"]) == 2
    assert runner.calls.count(["/adb", "kill-server"]) == 1


def test_normal_forward_does_not_restart_or_inspect_version(tmp_path):
    config = PdaLinkWatchTest().make_config(tmp_path / "watch.json")
    runner = FakeRunner(authorized_devices())
    watcher = pdalink_watch.PdaLinkWatcher(config, runner=runner, probe=ok_probe, stdout=io.StringIO())
    assert watcher.reconcile() == 0
    assert not any(c[1:] in (["kill-server"], ["start-server"], ["version"], ["forward", "--list"])
                   for c in runner.calls if c[0] == "/adb")


def test_mismatch_restart_failure_uses_backoff(tmp_path):
    watcher, runner, _, _ = mismatch_watcher(tmp_path)
    original = runner.run

    def failed_start(command, timeout):
        if command == ["/adb", "start-server"]:
            runner.calls.append(command)
            return pdalink_watch.CommandResult(1, "", "cannot bind server socket")
        return original(command, timeout)

    runner.run = failed_start
    device = pdalink_watch.pdalink_lite.parse_adb_devices(authorized_devices())[0]
    ok, error = watcher.ensure_forward(device)
    assert not ok and "start-server failed: cannot bind server socket" in error
    assert "backoff active" in watcher.ensure_forward(device)[1]
    assert runner.calls.count(["/adb", "kill-server"]) == 1


def test_mismatch_dry_run_never_writes(tmp_path):
    watcher, runner, output, _ = mismatch_watcher(tmp_path, dry_run=True)
    device = pdalink_watch.pdalink_lite.parse_adb_devices(authorized_devices())[0]
    assert watcher.ensure_forward(device)[0]
    assert "dry-run:" in output.getvalue()
    assert not watcher.state.forward_owned
    assert runner.calls == [["/adb", "-s", device.serial, "forward", "--list"]]


def test_lite_ambiguous_error_for_unauthorized_serial_is_unchanged(capsys):
    lite = pdalink_watch.pdalink_lite
    with mock.patch.object(lite, "adb_run", side_effect=[
        lite.CommandResult(1, "", "adb: error: more than one device/emulator"),
        lite.CommandResult(0, unauthorized_devices(), ""),
    ]):
        assert lite.adb_forward_tcp(18080, 8000, "R3CR406AAGE") == 1
    output = capsys.readouterr()
    assert "adb-server-mismatch" not in output.err
    assert "more than one device/emulator" in output.out
