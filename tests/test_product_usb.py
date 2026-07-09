import sys
import tempfile
import unittest
from pathlib import Path
import struct
from urllib.request import urlopen


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "linux"))

import pdalink_product_usb
import pdalink_aoa


class ProductUsbProtocolTest(unittest.TestCase):
    def test_protocol_negotiation_reports_version_and_capabilities(self):
        result = pdalink_product_usb.negotiate_product_usb_protocol(
            requested_version=1,
            supported_versions={1},
            capabilities={"http-proxy", "heartbeat"},
        )

        self.assertTrue(result.accepted)
        self.assertEqual(result.version, 1)
        self.assertEqual(result.reason, "accepted")
        self.assertEqual(result.capabilities, ("heartbeat", "http-proxy"))

    def test_protocol_negotiation_rejects_unsupported_version(self):
        result = pdalink_product_usb.negotiate_product_usb_protocol(
            requested_version=99,
            supported_versions={1},
            capabilities={"http-proxy"},
        )

        self.assertFalse(result.accepted)
        self.assertEqual(result.reason, "unsupported-version")
        self.assertEqual(result.version, 1)
        self.assertIn("version 99", result.next_step)

    def test_hello_frame_round_trips(self):
        frame = pdalink_product_usb.ProductUsbFrame(
            "hello",
            fields={
                "version": 1,
                "capabilities": ["http-proxy", "heartbeat"],
            },
        )

        encoded = pdalink_product_usb.encode_frame(frame)
        frames, remainder = pdalink_product_usb.decode_frames(encoded)

        self.assertEqual(remainder, b"")
        self.assertEqual(frames, [frame])

    def test_decode_frames_rejects_missing_frame_type(self):
        encoded = struct.pack("!I", len(b'{"stream_id":1}')) + b'{"stream_id":1}'

        with self.assertRaisesRegex(ValueError, "frame type must be a non-empty string"):
            pdalink_product_usb.decode_frames(encoded)

    def test_decode_frames_rejects_invalid_stream_id(self):
        encoded = (
            struct.pack("!I", len(b'{"type":"stream-open","stream_id":"bad"}'))
            + b'{"type":"stream-open","stream_id":"bad"}'
        )

        with self.assertRaisesRegex(ValueError, "stream_id must be a non-negative integer"):
            pdalink_product_usb.decode_frames(encoded)

    def test_fake_transport_delivers_stream_frames_in_order(self):
        linux, android = pdalink_product_usb.FakeProductUsbTransport.pair()

        linux.send(pdalink_product_usb.ProductUsbFrame("stream-open", stream_id=7, fields={"target": "proxy-pac"}))
        linux.send(pdalink_product_usb.ProductUsbFrame("stream-data", stream_id=7, payload=b"GET /proxy.pac"))
        linux.send(pdalink_product_usb.ProductUsbFrame("stream-close", stream_id=7))

        self.assertEqual(android.receive().kind, "stream-open")
        data = android.receive()
        self.assertEqual(data.kind, "stream-data")
        self.assertEqual(data.stream_id, 7)
        self.assertEqual(data.payload, b"GET /proxy.pac")
        self.assertEqual(android.receive().kind, "stream-close")

    def test_handshake_blocks_untrusted_host(self):
        host = pdalink_product_usb.HostIdentity("linux-laptop", "SHA256:abc")
        store = pdalink_product_usb.TrustedHostStore()

        result = pdalink_product_usb.handshake_product_usb_host(host, store)

        self.assertFalse(result.accepted)
        self.assertEqual(result.reason, "approval-required")
        self.assertIn("approve this Linux host", result.next_step)

    def test_handshake_accepts_trusted_host(self):
        host = pdalink_product_usb.HostIdentity("linux-laptop", "SHA256:abc")
        store = pdalink_product_usb.TrustedHostStore.trust(host)

        result = pdalink_product_usb.handshake_product_usb_host(host, store)

        self.assertTrue(result.accepted)
        self.assertEqual(result.reason, "trusted")

    def test_product_usb_hello_accepts_android_trust_response(self):
        host = pdalink_product_usb.HostIdentity("linux-laptop", "SHA256:abc")

        class HelloTransport:
            def __init__(self):
                self.sent = []

            def send(self, frame):
                self.sent.append(frame)

            def receive(self):
                return pdalink_product_usb.ProductUsbFrame(
                    "hello",
                    fields={
                        "version": 1,
                        "permission": "granted",
                        "host_trust": "trusted",
                        "capabilities": ["proxy-pac"],
                    },
                )

        transport = HelloTransport()
        result = pdalink_product_usb.product_usb_hello(transport, host=host)

        self.assertTrue(result.accepted)
        self.assertEqual(result.reason, "trusted")
        self.assertEqual(transport.sent[0].kind, "hello")
        self.assertEqual(transport.sent[0].fields["fingerprint"], "SHA256:abc")

    def test_pyusb_transport_normalizes_read_timeout(self):
        class USBTimeoutError(Exception):
            pass

        class ReadEndpoint:
            def read(self, _size, timeout):
                raise USBTimeoutError("timed out")

        class WriteEndpoint:
            def write(self, _data, timeout):
                return len(_data)

        transport = pdalink_product_usb.PyUsbProductUsbTransport(
            object(),
            ReadEndpoint(),
            WriteEndpoint(),
            timeout_ms=1,
        )

        with self.assertRaisesRegex(TimeoutError, "timed out waiting"):
            transport.receive()

    def test_pyusb_transport_normalizes_write_timeout(self):
        class USBTimeoutError(Exception):
            pass

        class ReadEndpoint:
            def read(self, _size, timeout):
                return b""

        class WriteEndpoint:
            def write(self, _data, timeout):
                raise USBTimeoutError("timed out")

        transport = pdalink_product_usb.PyUsbProductUsbTransport(
            object(),
            ReadEndpoint(),
            WriteEndpoint(),
            timeout_ms=1,
        )

        with self.assertRaisesRegex(TimeoutError, "timed out writing"):
            transport.send(pdalink_product_usb.ProductUsbFrame("hello"))

    def test_pyusb_transport_resyncs_stale_bytes_before_frame(self):
        encoded = pdalink_product_usb.encode_frame(pdalink_product_usb.ProductUsbFrame(
            "device-ready",
            fields={"state": "ready"},
        ))

        class ReadEndpoint:
            def __init__(self):
                self.used = False

            def read(self, _size, timeout):
                if self.used:
                    raise TimeoutError("timed out")
                self.used = True
                return b"stale" + encoded

        class WriteEndpoint:
            def write(self, _data, timeout):
                return len(_data)

        transport = pdalink_product_usb.PyUsbProductUsbTransport(
            object(),
            ReadEndpoint(),
            WriteEndpoint(),
            timeout_ms=1,
        )

        frame = transport.receive()

        self.assertEqual(frame.kind, "device-ready")
        self.assertEqual(frame.fields["state"], "ready")

    def test_pyusb_transport_resyncs_oversized_length_prefix(self):
        encoded = pdalink_product_usb.encode_frame(pdalink_product_usb.ProductUsbFrame(
            "device-ready",
            fields={"state": "ready"},
        ))

        class ReadEndpoint:
            def __init__(self):
                self.chunks = [b"\xff\xff\xff\xff", encoded]

            def read(self, _size, timeout):
                if not self.chunks:
                    raise TimeoutError("timed out")
                return self.chunks.pop(0)

        class WriteEndpoint:
            def write(self, _data, timeout):
                return len(_data)

        transport = pdalink_product_usb.PyUsbProductUsbTransport(
            object(),
            ReadEndpoint(),
            WriteEndpoint(),
            timeout_ms=1,
        )

        frame = transport.receive()

        self.assertEqual(frame.kind, "device-ready")
        self.assertEqual(frame.fields["state"], "ready")

    def test_product_id_usb_debugging_state_tracks_aoa_adb_pids(self):
        self.assertEqual(pdalink_product_usb.product_id_usb_debugging_state("2d00"), "off")
        self.assertEqual(pdalink_product_usb.product_id_usb_debugging_state("18d1:2d01".split(":")[1]), "on")
        self.assertEqual(pdalink_product_usb.product_id_usb_debugging_state("ffff"), "unknown")

    def test_trusted_host_store_persists_trusted_host(self):
        host = pdalink_product_usb.HostIdentity("linux-laptop", "SHA256:abc")
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "product-usb-hosts.json"
            pdalink_product_usb.TrustedHostStore.trust(host).save(path)

            loaded = pdalink_product_usb.TrustedHostStore.load(path)
            result = pdalink_product_usb.handshake_product_usb_host(host, loaded)

        self.assertTrue(result.accepted)
        self.assertEqual(result.reason, "trusted")

    def test_handshake_blocks_revoked_host(self):
        host = pdalink_product_usb.HostIdentity("linux-laptop", "SHA256:abc")
        store = pdalink_product_usb.TrustedHostStore(revoked={"SHA256:abc"})

        result = pdalink_product_usb.handshake_product_usb_host(host, store)

        self.assertFalse(result.accepted)
        self.assertEqual(result.reason, "revoked")

    def test_trusted_host_store_persists_revoked_host(self):
        host = pdalink_product_usb.HostIdentity("linux-laptop", "SHA256:abc")
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "product-usb-hosts.json"
            pdalink_product_usb.TrustedHostStore(revoked={"SHA256:abc"}).save(path)

            loaded = pdalink_product_usb.TrustedHostStore.load(path)
            result = pdalink_product_usb.handshake_product_usb_host(host, loaded)

        self.assertFalse(result.accepted)
        self.assertEqual(result.reason, "revoked")

    def test_proxy_pac_probe_succeeds_over_fake_product_usb(self):
        linux, android = pdalink_product_usb.FakeProductUsbTransport.pair()
        pac = 'function FindProxyForURL(url, host) { return "PROXY product-usb:0"; }'

        pdalink_product_usb.request_proxy_pac(linux, stream_id=3)
        pdalink_product_usb.serve_proxy_pac_once(android, pac)
        result = pdalink_product_usb.read_proxy_pac_response(linux, stream_id=3)

        self.assertTrue(result.ok)
        self.assertEqual(result.pac, pac)

    def test_local_proxy_bridge_serves_pac_over_fake_product_usb(self):
        linux, android = pdalink_product_usb.FakeProductUsbTransport.pair()
        pac = 'function FindProxyForURL(url, host) { return "PROXY product-usb:0"; }'

        with pdalink_product_usb.ProductUsbLocalProxyBridge(
            linux,
            responder=lambda: pdalink_product_usb.serve_proxy_pac_once(android, pac),
        ) as bridge:
            with urlopen(bridge.proxy_pac_url(), timeout=2) as response:
                body = response.read().decode("utf-8")

        self.assertEqual(bridge.bind_host, "127.0.0.1")
        self.assertIn("PROXY product-usb:0", body)


class AoaBootstrapTest(unittest.TestCase):
    def test_aoa_start_sends_pdalink_accessory_strings_then_start(self):
        backend = pdalink_aoa.FakeAoaBackend(protocol=2)

        result = pdalink_aoa.start_pdalink_accessory(backend, serial="SHA256:abc")

        self.assertTrue(result.ok)
        self.assertIn(("get-protocol",), backend.calls)
        self.assertIn(("send-string", pdalink_aoa.AOA_STRING_MANUFACTURER, "PdaLink"), backend.calls)
        self.assertIn(("send-string", pdalink_aoa.AOA_STRING_MODEL, "PdaLink USB"), backend.calls)
        self.assertIn(("send-string", pdalink_aoa.AOA_STRING_SERIAL, "SHA256:abc"), backend.calls)
        self.assertEqual(backend.calls[-1], ("start-accessory",))

    def test_aoa_backend_detaches_active_kernel_drivers(self):
        class Interface:
            def __init__(self, number):
                self.bInterfaceNumber = number

        class Device:
            def __init__(self):
                self.detached = []

            def get_active_configuration(self):
                return [Interface(0), Interface(1), Interface(2)]

            def is_kernel_driver_active(self, interface_number):
                return interface_number in {1, 2}

            def detach_kernel_driver(self, interface_number):
                self.detached.append(interface_number)

        device = Device()

        pdalink_aoa.detach_kernel_drivers(device)

        self.assertEqual(device.detached, [1, 2])

    def test_aoa_start_reports_unsupported_protocol(self):
        backend = pdalink_aoa.FakeAoaBackend(protocol=0)

        result = pdalink_aoa.start_pdalink_accessory(backend, serial="SHA256:abc")

        self.assertFalse(result.ok)
        self.assertIn("does not support Android Open Accessory", result.detail)

    def test_parse_usb_id_accepts_hex_with_or_without_prefix(self):
        self.assertEqual(pdalink_aoa.parse_usb_id("18d1"), 0x18D1)
        self.assertEqual(pdalink_aoa.parse_usb_id("0x2d00"), 0x2D00)


if __name__ == "__main__":
    unittest.main()
