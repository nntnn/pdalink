import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "windows"))

import pdalink_wintun


class FakeWintunApi:
    def __init__(self, packets=None, fail_start=False):
        self.events = []
        self.packets = list(packets or [])
        self.fail_start = fail_start
        self.sent = []
        self.wait_result = True

    def create_adapter(self, name):
        self.events.append(("create_adapter", name))
        return 11

    def close_adapter(self, adapter):
        self.events.append(("close_adapter", adapter))

    def start_session(self, adapter, capacity):
        self.events.append(("start_session", adapter, capacity))
        if self.fail_start:
            raise OSError("session failed")
        return 22

    def end_session(self, session):
        self.events.append(("end_session", session))

    def get_read_wait_event(self, session):
        self.events.append(("get_read_wait_event", session))
        return 33

    def receive_packet(self, session):
        self.events.append(("receive_packet", session))
        return self.packets.pop(0) if self.packets else None

    def wait_for_packet(self, event, timeout_ms):
        self.events.append(("wait_for_packet", event, timeout_ms))
        return self.wait_result

    def send_packet(self, session, packet):
        self.events.append(("send_packet", session, packet))
        self.sent.append(packet)
        return True


class FakePacketDevice:
    adapter_name = "PdaLink-Test"

    def __init__(self):
        self.sent = []
        self.closed = False

    def receive(self, _timeout_ms):
        return None

    def send(self, packet):
        self.sent.append(packet)
        return True

    def close(self):
        self.closed = True


class WindowsWintunTest(unittest.TestCase):
    def test_ring_capacity_requires_bounded_power_of_two(self):
        self.assertEqual(pdalink_wintun.validate_ring_capacity(0x400000), 0x400000)
        for invalid in (0x10000, 0x4000001, 0x30000):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                pdalink_wintun.validate_ring_capacity(invalid)

    def test_packet_device_closes_session_before_created_adapter(self):
        api = FakeWintunApi()
        device = pdalink_wintun.WintunPacketDevice(api, "PdaLink-Test")

        device.close()
        device.close()

        self.assertEqual(api.events[-2:], [("end_session", 22), ("close_adapter", 11)])
        self.assertEqual(api.events.count(("close_adapter", 11)), 1)

    def test_packet_device_closes_adapter_when_session_start_fails(self):
        api = FakeWintunApi(fail_start=True)

        with self.assertRaises(OSError):
            pdalink_wintun.WintunPacketDevice(api, "PdaLink-Test")

        self.assertIn(("close_adapter", 11), api.events)

    def test_packet_device_waits_then_receives_and_sends(self):
        api = FakeWintunApi(packets=[None, b"packet"])
        device = pdalink_wintun.WintunPacketDevice(api, "PdaLink-Test")

        self.assertEqual(device.receive(250), b"packet")
        self.assertTrue(device.send(b"reply"))

        self.assertIn(("wait_for_packet", 33, 250), api.events)
        self.assertEqual(api.sent, [b"reply"])
        device.close()

    def test_windows_adapter_uses_injected_packet_device(self):
        device = FakePacketDevice()
        adapter = pdalink_wintun.WintunHttpAdapter(device, "http://127.0.0.1:18080")

        adapter.write_packet(b"response")
        adapter.close()
        adapter.close()

        self.assertEqual(device.sent, [b"response"])
        self.assertTrue(device.closed)

    def test_windows_adapter_requires_controller_activation(self):
        device = FakePacketDevice()
        adapter = pdalink_wintun.WintunHttpAdapter(device, "http://127.0.0.1:18080")
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with self.assertRaises(TimeoutError):
                adapter.run_windows(
                    activation_file=root / "activate",
                    stop_file=root / "stop",
                    activation_timeout=0,
                )

        self.assertTrue(device.closed)

    def test_ready_file_is_exclusive_and_contains_no_password(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "ready.json"
            payload = {"version": 1, "token": "controller", "adapter_name": "PdaLink-Test"}
            pdalink_wintun.write_json_exclusive(path, payload)
            with self.assertRaises(FileExistsError):
                pdalink_wintun.write_json_exclusive(path, {"token": "replacement"})

            self.assertEqual(json.loads(path.read_text()), payload)
            self.assertNotIn("password", path.read_text().lower())
            self.assertEqual(list(path.parent.glob(f".{path.name}.*.tmp")), [])

    def test_verify_dll_checks_absolute_content_hash(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "wintun.dll"
            path.write_bytes(b"signed-test-placeholder")
            expected = hashlib.sha256(path.read_bytes()).hexdigest()

            self.assertEqual(pdalink_wintun.verify_dll(path, expected), path.resolve())
            with self.assertRaises(ValueError):
                pdalink_wintun.verify_dll(path, "0" * 64)

    def test_cli_requires_controller_files_and_verified_dll(self):
        parser = pdalink_wintun.build_parser()
        with self.assertRaises(SystemExit):
            parser.parse_args([])

        args = parser.parse_args([
            "--dll", "wintun.dll",
            "--dll-sha256", "0" * 64,
            "--adapter-name", "PdaLink-Test",
            "--upstream-interface-index", "17",
            "--ready-file", "ready.json",
            "--activation-file", "activate",
            "--stop-file", "stop",
            "--token", "token",
        ])
        self.assertEqual(args.adapter_name, "PdaLink-Test")
        self.assertEqual(args.ring_capacity, 0x400000)


if __name__ == "__main__":
    unittest.main()
