"""Product USB protocol helpers for the ADB-free PdaLink transport."""

from __future__ import annotations

import base64
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import struct
import threading
import time
from typing import Any, Callable, Iterable, Protocol


MAX_FRAME_SIZE = 1024 * 1024
DEFAULT_AOA_VID = "18d1"
AOA_ACCESSORY_PIDS = ("2d00", "2d01", "2d02", "2d03", "2d04", "2d05")
AOA_ADB_PIDS = {"2d01", "2d03", "2d05"}


class ProductUsbTransport(Protocol):
    def send(self, frame: "ProductUsbFrame") -> None:
        ...

    def receive(self, timeout_ms: int | None = None) -> "ProductUsbFrame":
        ...


@dataclass(frozen=True)
class ProductUsbFrame:
    kind: str
    stream_id: int = 0
    payload: bytes = b""
    fields: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class HostIdentity:
    name: str
    fingerprint: str


@dataclass(frozen=True)
class TrustedHostStore:
    hosts: dict[str, HostIdentity] = field(default_factory=dict)
    revoked: set[str] = field(default_factory=set)

    @classmethod
    def trust(cls, host: HostIdentity) -> "TrustedHostStore":
        return cls(hosts={host.fingerprint: host})

    @classmethod
    def load(cls, path: Path) -> "TrustedHostStore":
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return cls()
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError(f"invalid Product USB trusted-host store {path}: {exc}") from exc
        if not isinstance(payload, dict):
            raise ValueError(f"invalid Product USB trusted-host store {path}: payload must be an object")
        hosts_payload = payload.get("hosts") or {}
        if not isinstance(hosts_payload, dict):
            raise ValueError(f"invalid Product USB trusted-host store {path}: hosts must be an object")
        hosts: dict[str, HostIdentity] = {}
        for fingerprint, raw_host in hosts_payload.items():
            if not isinstance(raw_host, dict):
                raise ValueError(f"invalid Product USB trusted-host store {path}: host entry must be an object")
            host = HostIdentity(
                name=str(raw_host.get("name") or ""),
                fingerprint=str(raw_host.get("fingerprint") or fingerprint),
            )
            hosts[host.fingerprint] = host
        revoked_payload = payload.get("revoked") or []
        if not isinstance(revoked_payload, list):
            raise ValueError(f"invalid Product USB trusted-host store {path}: revoked must be a list")
        return cls(hosts=hosts, revoked={str(item) for item in revoked_payload})

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "version": 1,
            "hosts": {
                fingerprint: {
                    "name": host.name,
                    "fingerprint": host.fingerprint,
                }
                for fingerprint, host in sorted(self.hosts.items())
            },
            "revoked": sorted(self.revoked),
        }
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        tmp.replace(path)
        try:
            path.chmod(0o600)
        except OSError:
            pass


@dataclass(frozen=True)
class HandshakeResult:
    accepted: bool
    reason: str
    next_step: str = ""


@dataclass(frozen=True)
class PacProbeResult:
    ok: bool
    detail: str
    pac: str = ""


@dataclass(frozen=True)
class ProtocolNegotiationResult:
    accepted: bool
    version: int
    capabilities: tuple[str, ...] = ()
    reason: str = ""
    next_step: str = ""


class FakeProductUsbTransport:
    def __init__(self) -> None:
        self._peer: FakeProductUsbTransport | None = None
        self._buffer = b""

    @classmethod
    def pair(cls) -> tuple["FakeProductUsbTransport", "FakeProductUsbTransport"]:
        left = cls()
        right = cls()
        left._peer = right
        right._peer = left
        return left, right

    def send(self, frame: ProductUsbFrame) -> None:
        if self._peer is None:
            raise RuntimeError("transport is not paired")
        self._peer._buffer += encode_frame(frame)

    def receive(self, timeout_ms: int | None = None) -> ProductUsbFrame:
        frames, remainder = decode_frames(self._buffer)
        if not frames:
            raise RuntimeError("no product USB frame available")
        self._buffer = b"".join(encode_frame(frame) for frame in frames[1:]) + remainder
        return frames[0]


class PyUsbProductUsbTransport:
    def __init__(self, device, in_endpoint, out_endpoint, *, timeout_ms: int = 5000) -> None:
        self.device = device
        self.in_endpoint = in_endpoint
        self.out_endpoint = out_endpoint
        self.timeout_ms = timeout_ms
        self.interface_number = 0
        self.supports_device_ready_preface = True
        self._buffer = b""
        self._lock = threading.Lock()

    @classmethod
    def open(
        cls,
        *,
        vid: str = DEFAULT_AOA_VID,
        pid: str = "",
        timeout_ms: int = 5000,
    ) -> "PyUsbProductUsbTransport":
        try:
            import usb.core
            import usb.util
        except ImportError as exc:
            raise RuntimeError("PyUSB is required for Product USB bridge; install python3-usb or pyusb") from exc

        vids = [parse_usb_id(vid)]
        pids = [parse_usb_id(pid)] if pid else [parse_usb_id(item) for item in AOA_ACCESSORY_PIDS]
        device = None
        matched_pid = None
        for candidate_pid in pids:
            device = usb.core.find(idVendor=vids[0], idProduct=candidate_pid)
            if device is not None:
                matched_pid = candidate_pid
                break
        if device is None:
            wanted = pid or ",".join(AOA_ACCESSORY_PIDS)
            raise RuntimeError(f"Android Open Accessory USB device not found for {vid}:{wanted}")

        try:
            if device.is_kernel_driver_active(0):
                device.detach_kernel_driver(0)
        except (AttributeError, NotImplementedError, usb.core.USBError):
            pass
        try:
            device.set_configuration()
        except usb.core.USBError:
            pass

        config = device.get_active_configuration()
        interface = config[(0, 0)]
        try:
            usb.util.claim_interface(device, interface.bInterfaceNumber)
        except usb.core.USBError:
            pass
        in_endpoint = usb.util.find_descriptor(
            interface,
            custom_match=lambda endpoint: (
                usb.util.endpoint_type(endpoint.bmAttributes) == usb.util.ENDPOINT_TYPE_BULK
                and usb.util.endpoint_direction(endpoint.bEndpointAddress) == usb.util.ENDPOINT_IN
            ),
        )
        out_endpoint = usb.util.find_descriptor(
            interface,
            custom_match=lambda endpoint: (
                usb.util.endpoint_type(endpoint.bmAttributes) == usb.util.ENDPOINT_TYPE_BULK
                and usb.util.endpoint_direction(endpoint.bEndpointAddress) == usb.util.ENDPOINT_OUT
            ),
        )
        if in_endpoint is None or out_endpoint is None:
            raise RuntimeError("Android Open Accessory bulk endpoints not found")
        transport = cls(device, in_endpoint, out_endpoint, timeout_ms=timeout_ms)
        transport.interface_number = int(interface.bInterfaceNumber)
        transport.product_id = f"{matched_pid:04x}" if matched_pid is not None else pid.lower()
        return transport

    def send(self, frame: ProductUsbFrame) -> None:
        data = encode_frame(frame)
        with self._lock:
            try:
                self.out_endpoint.write(data, timeout=self.timeout_ms)
            except Exception as exc:
                if is_usb_timeout(exc):
                    raise TimeoutError("timed out writing Product USB frame") from exc
                raise

    def receive(self, timeout_ms: int | None = None) -> ProductUsbFrame:
        effective_timeout_ms = self.timeout_ms if timeout_ms is None else timeout_ms
        deadline = time.monotonic() + (effective_timeout_ms / 1000.0)
        while True:
            frames, remainder = decode_transport_frames(self._buffer)
            if frames:
                self._buffer = b"".join(encode_frame(frame) for frame in frames[1:]) + remainder
                return frames[0]
            if time.monotonic() > deadline:
                raise TimeoutError("timed out waiting for Product USB frame")
            try:
                chunk = self.in_endpoint.read(16 * 1024, timeout=effective_timeout_ms)
            except Exception as exc:
                if is_usb_timeout(exc):
                    if time.monotonic() > deadline:
                        raise TimeoutError("timed out waiting for Product USB frame") from exc
                    time.sleep(0.05)
                    continue
                raise
            self._buffer += bytes(chunk)

    def close(self) -> None:
        try:
            import usb.util
            usb.util.release_interface(self.device, self.interface_number)
            usb.util.dispose_resources(self.device)
        except Exception:
            pass


class ProductUsbLocalProxyBridge:
    def __init__(
        self,
        transport: ProductUsbTransport,
        *,
        bind_host: str = "127.0.0.1",
        local_port: int = 0,
        responder: Callable[[], None] | None = None,
    ) -> None:
        self.transport = transport
        self.bind_host = bind_host
        self.local_port = local_port
        self.responder = responder
        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None
        self._next_stream_id = 1
        self._lock = threading.Lock()

    def start(self) -> "ProductUsbLocalProxyBridge":
        bridge = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, _format: str, *_args: object) -> None:
                return

            def do_GET(self) -> None:
                if self.path not in ("/proxy.pac", "/"):
                    self.send_error(404, "not found")
                    return
                try:
                    result = bridge.fetch_proxy_pac()
                except Exception as exc:
                    self.send_error(502, str(exc))
                    return
                if not result.ok:
                    self.send_error(502, result.detail)
                    return
                body = result.pac.encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/x-ns-proxy-autoconfig")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        self._server = ThreadingHTTPServer((self.bind_host, self.local_port), Handler)
        self.local_port = int(self._server.server_address[1])
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()
        return self

    def fetch_proxy_pac(self) -> PacProbeResult:
        with self._lock:
            stream_id = self._next_stream_id
            self._next_stream_id += 1
            request_proxy_pac(self.transport, stream_id=stream_id)
            if self.responder is not None:
                self.responder()
            return read_proxy_pac_response(self.transport, stream_id=stream_id)

    def proxy_pac_url(self) -> str:
        return f"http://{self.bind_host}:{self.local_port}/proxy.pac"

    def close(self) -> None:
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
            self._server = None
        if self._thread is not None:
            self._thread.join(timeout=1.0)
            self._thread = None

    def __enter__(self) -> "ProductUsbLocalProxyBridge":
        return self.start()

    def __exit__(self, _exc_type: object, _exc: object, _tb: object) -> None:
        self.close()


def handshake_product_usb_host(host: HostIdentity, store: TrustedHostStore) -> HandshakeResult:
    if host.fingerprint in store.revoked:
        return HandshakeResult(False, "revoked", "re-approve this Linux host in the Android app")
    if host.fingerprint not in store.hosts:
        return HandshakeResult(False, "approval-required", "approve this Linux host in the Android PdaLink USB trust prompt")
    return HandshakeResult(True, "trusted")


def product_id_usb_debugging_state(pid: str) -> str:
    cleaned = pid.strip().lower()
    if cleaned.startswith("0x"):
        cleaned = cleaned[2:]
    if cleaned in AOA_ADB_PIDS:
        return "on"
    if cleaned in set(AOA_ACCESSORY_PIDS):
        return "off"
    return "unknown"


def parse_usb_id(value: str) -> int:
    cleaned = value.strip().lower()
    if cleaned.startswith("0x"):
        cleaned = cleaned[2:]
    parsed = int(cleaned, 16)
    if parsed < 0 or parsed > 0xFFFF:
        raise ValueError(f"USB id out of range: {value}")
    return parsed


def product_usb_hello(
    transport: ProductUsbTransport,
    *,
    host: HostIdentity,
    timeout_hint: float = 5.0,
) -> HandshakeResult:
    if getattr(transport, "supports_device_ready_preface", False):
        drain_product_usb_preface(transport, timeout_ms=min(500, int(timeout_hint * 1000)))
    transport.send(ProductUsbFrame(
        "hello",
        fields={
            "version": 1,
            "host_name": host.name,
            "fingerprint": host.fingerprint,
            "capabilities": ["proxy-pac"],
        },
    ))
    started = time.monotonic()
    while True:
        if time.monotonic() - started > timeout_hint:
            return HandshakeResult(False, "timeout", "restart Product USB mode and try the bridge again")
        try:
            frame = transport.receive()
        except (RuntimeError, TimeoutError):
            time.sleep(0.05)
            continue
        if frame.kind != "hello":
            continue
        fields = frame.fields
        trust = str(fields.get("host_trust") or "untrusted")
        if trust != "trusted":
            return HandshakeResult(False, trust, "approve this Linux host in the Android PdaLink USB trust prompt")
        return HandshakeResult(True, "trusted")


def drain_product_usb_preface(transport: ProductUsbTransport, *, timeout_ms: int) -> None:
    try:
        transport.receive(timeout_ms=timeout_ms)
    except (RuntimeError, TimeoutError):
        return


def negotiate_product_usb_protocol(
    requested_version: int,
    supported_versions: Iterable[int],
    capabilities: Iterable[str],
) -> ProtocolNegotiationResult:
    supported = sorted({int(version) for version in supported_versions})
    if not supported:
        return ProtocolNegotiationResult(
            False,
            0,
            reason="no-supported-versions",
            next_step="upgrade PdaLink so both sides share a Product USB protocol version",
        )
    if requested_version not in supported:
        return ProtocolNegotiationResult(
            False,
            supported[-1],
            reason="unsupported-version",
            next_step=f"Product USB version {requested_version} is not supported; use version {supported[-1]}",
        )
    return ProtocolNegotiationResult(
        True,
        requested_version,
        tuple(sorted(str(capability) for capability in capabilities)),
        reason="accepted",
    )


def is_usb_timeout(exc: Exception) -> bool:
    return exc.__class__.__name__ == "USBTimeoutError"


def request_proxy_pac(transport: ProductUsbTransport, stream_id: int = 1) -> None:
    transport.send(ProductUsbFrame("stream-open", stream_id=stream_id, fields={"target": "proxy-pac"}))


def serve_proxy_pac_once(transport: ProductUsbTransport, pac: str) -> None:
    request = transport.receive()
    if request.kind != "stream-open" or request.fields.get("target") != "proxy-pac":
        transport.send(ProductUsbFrame(
            "stream-error",
            stream_id=request.stream_id,
            fields={"reason": "expected proxy-pac stream-open"},
        ))
        return
    transport.send(ProductUsbFrame("stream-data", stream_id=request.stream_id, payload=pac.encode("utf-8")))
    transport.send(ProductUsbFrame("stream-close", stream_id=request.stream_id))


def read_proxy_pac_response(transport: ProductUsbTransport, stream_id: int = 1) -> PacProbeResult:
    chunks: list[bytes] = []
    while True:
        frame = transport.receive()
        if frame.stream_id != stream_id:
            return PacProbeResult(False, f"unexpected stream id {frame.stream_id}")
        if frame.kind == "stream-error":
            return PacProbeResult(False, str(frame.fields.get("reason") or "stream error"))
        if frame.kind == "stream-data":
            chunks.append(frame.payload)
            continue
        if frame.kind == "stream-close":
            pac = b"".join(chunks).decode("utf-8")
            if "PROXY" not in pac:
                return PacProbeResult(False, "PAC proxy line not found", pac)
            return PacProbeResult(True, "PAC received over product USB", pac)
        return PacProbeResult(False, f"unexpected frame {frame.kind}")


def encode_frame(frame: ProductUsbFrame) -> bytes:
    if frame.stream_id < 0:
        raise ValueError("stream_id must be non-negative")
    body = {
        "type": frame.kind,
        "stream_id": frame.stream_id,
        "fields": frame.fields,
        "payload": base64.b64encode(frame.payload).decode("ascii"),
    }
    raw = json.dumps(body, separators=(",", ":"), sort_keys=True).encode("utf-8")
    if len(raw) > MAX_FRAME_SIZE:
        raise ValueError("frame too large")
    return struct.pack("!I", len(raw)) + raw


def decode_frames(buffer: bytes) -> tuple[list[ProductUsbFrame], bytes]:
    frames: list[ProductUsbFrame] = []
    offset = 0
    while len(buffer) - offset >= 4:
        size = struct.unpack("!I", buffer[offset:offset + 4])[0]
        if size > MAX_FRAME_SIZE:
            raise ValueError("frame too large")
        frame_start = offset + 4
        frame_end = frame_start + size
        if len(buffer) < frame_end:
            break
        raw = json.loads(buffer[frame_start:frame_end].decode("utf-8"))
        if not isinstance(raw, dict):
            raise ValueError("frame payload must be an object")
        kind = raw.get("type")
        if not isinstance(kind, str) or not kind:
            raise ValueError("frame type must be a non-empty string")
        stream_id = raw.get("stream_id", 0)
        if not isinstance(stream_id, int) or stream_id < 0:
            raise ValueError("stream_id must be a non-negative integer")
        fields = raw.get("fields") or {}
        if not isinstance(fields, dict):
            raise ValueError("frame fields must be an object")
        payload = base64.b64decode(str(raw.get("payload") or ""), validate=True)
        frames.append(ProductUsbFrame(
            kind,
            stream_id=stream_id,
            payload=payload,
            fields=fields,
        ))
        offset = frame_end
    return frames, buffer[offset:]


def decode_transport_frames(buffer: bytes) -> tuple[list[ProductUsbFrame], bytes]:
    while True:
        try:
            return decode_frames(buffer)
        except (UnicodeDecodeError, json.JSONDecodeError, ValueError):
            if len(buffer) <= 4:
                return [], buffer[1:]
            buffer = buffer[1:]
