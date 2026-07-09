import io
import json
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path


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
    def __init__(self, devices: str, forwards: str = "", service_running: bool = False):
        self.devices = devices
        self.forwards = forwards
        self.service_running = service_running
        self.calls = []

    def run(self, command, timeout):
        self.calls.append(list(command))
        if command == ["/adb", "devices", "-l"]:
            return pdalink_watch.CommandResult(0, self.devices, "")
        if command == ["/adb", "forward", "--list"]:
            return pdalink_watch.CommandResult(0, self.forwards, "")
        if command[:3] == ["/adb", "-s", "R3CR406AAGE"] and command[3:] == ["forward", "tcp:18080", "tcp:8000"]:
            self.forwards = "R3CR406AAGE tcp:18080 tcp:8000\n"
            return pdalink_watch.CommandResult(0, "", "")
        if command == ["/adb", "forward", "--remove", "tcp:18080"]:
            self.forwards = ""
            return pdalink_watch.CommandResult(0, "", "")
        if command[:3] == ["/adb", "-s", "R3CR406AAGE"] and command[3:6] == ["shell", "am", "start-foreground-service"]:
            return pdalink_watch.CommandResult(0, "Starting service: Intent\n", "")
        if is_service_call(command, "status"):
            stdout = "wrapper: RUNNING pid 123\n" if self.service_running else "wrapper: MISSING .tools/runtime/pdalink-system.pid\n"
            return pdalink_watch.CommandResult(0, stdout, "")
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
            self.assertIn(["/adb", "forward", "--remove", "tcp:18080"], runner.calls)
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
            self.assertFalse(any(call[-2:] == ["-a", "dev.pdalink.prototype.STOP"] for call in runner.calls))
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

            self.assertIn(["/adb", "forward", "--remove", "tcp:18080"], runner.calls)
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
            self.assertIn(["/adb", "forward", "--remove", "tcp:18080"], runner.calls)
            self.assertTrue(any(call[-2:] == ["-a", "dev.pdalink.prototype.STOP"] for call in runner.calls))
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
            self.assertIn(["/adb", "forward", "--remove", "tcp:18080"], runner.calls)
            self.assertTrue(any(call[-2:] == ["-a", "dev.pdalink.prototype.STOP"] for call in runner.calls))
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
            self.assertIn(["/adb", "forward", "--remove", "tcp:18080"], runner.calls)
            self.assertTrue(any(call[-2:] == ["-a", "dev.pdalink.prototype.STOP"] for call in runner.calls))
            payload = json.loads(state_file.read_text(encoding="utf-8"))
            self.assertEqual(payload["mode"], "stopped")
            self.assertFalse(payload["forward_owned"])
            self.assertFalse(payload["service_owned"])

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
