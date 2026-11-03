#!/usr/bin/env python3
"""Wintun packet backend for PdaLink's experimental Windows IPv4 tunnel.

The portable packet engine remains in linux/pdalink_tun.py. This module owns
only the signed Wintun DLL lifecycle and its packet ring. Supported traffic is
therefore the same as the Linux prototype: IPv4 TCP and UDP/53 DNS.
"""

from __future__ import annotations

import argparse
import ctypes
import hashlib
import json
import os
import signal
import sys
import subprocess
import threading
import time
from pathlib import Path
from typing import Protocol


ROOT = Path(__file__).resolve().parents[1]
LINUX_DIR = ROOT / "linux"
if str(LINUX_DIR) not in sys.path:
    sys.path.insert(0, str(LINUX_DIR))

from pdalink_tun import TCP_IDLE_TIMEOUT, TunHttpAdapter  # noqa: E402


ERROR_HANDLE_EOF = 38
ERROR_BUFFER_OVERFLOW = 111
ERROR_NO_MORE_ITEMS = 259
WAIT_OBJECT_0 = 0
WAIT_TIMEOUT = 258
WINTUN_MIN_RING_CAPACITY = 0x20000
WINTUN_MAX_RING_CAPACITY = 0x4000000
WINTUN_MAX_IP_PACKET_SIZE = 0xFFFF
DEFAULT_RING_CAPACITY = 0x400000


class WintunError(OSError):
    pass


class PacketDevice(Protocol):
    adapter_name: str

    def receive(self, timeout_ms: int) -> bytes | None:
        ...

    def send(self, packet: bytes) -> bool:
        ...

    def close(self) -> None:
        ...


def validate_ring_capacity(value: int) -> int:
    if value < WINTUN_MIN_RING_CAPACITY or value > WINTUN_MAX_RING_CAPACITY:
        raise ValueError(
            f"Wintun ring capacity must be between {WINTUN_MIN_RING_CAPACITY:#x} "
            f"and {WINTUN_MAX_RING_CAPACITY:#x}"
        )
    if value & (value - 1):
        raise ValueError("Wintun ring capacity must be a power of two")
    return value


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def verify_dll(path: Path, expected_sha256: str) -> Path:
    resolved = path.expanduser().resolve(strict=True)
    if resolved.name.lower() != "wintun.dll":
        raise ValueError("Wintun library path must end in wintun.dll")
    expected = expected_sha256.strip().lower()
    if len(expected) != 64 or any(character not in "0123456789abcdef" for character in expected):
        raise ValueError("expected Wintun DLL SHA-256 must be 64 hexadecimal characters")
    actual = sha256_file(resolved)
    if actual != expected:
        raise ValueError(f"Wintun DLL SHA-256 mismatch: expected {expected}, got {actual}")
    return resolved


def windows_error(prefix: str, error: int | None = None) -> WintunError:
    code = ctypes.get_last_error() if error is None else error
    try:
        detail = ctypes.FormatError(code).strip()
    except (AttributeError, OSError):
        detail = "Windows error"
    return WintunError(code, f"{prefix}: {detail} ({code})")


class WintunApi:
    """Small ctypes binding around the official Wintun 0.14 API."""

    def __init__(self, dll_path: Path):
        if os.name != "nt":
            raise OSError("Wintun is available only on Windows")
        self.dll_path = dll_path.resolve(strict=True)
        self.dll = ctypes.WinDLL(str(self.dll_path), use_last_error=True)
        self.kernel32 = ctypes.WinDLL("kernel32.dll", use_last_error=True)
        self._configure_signatures()

    def _configure_signatures(self) -> None:
        void_pointer = ctypes.c_void_p
        dword = ctypes.c_uint32

        self.dll.WintunCreateAdapter.argtypes = [ctypes.c_wchar_p, ctypes.c_wchar_p, void_pointer]
        self.dll.WintunCreateAdapter.restype = void_pointer
        self.dll.WintunCloseAdapter.argtypes = [void_pointer]
        self.dll.WintunCloseAdapter.restype = None
        self.dll.WintunStartSession.argtypes = [void_pointer, dword]
        self.dll.WintunStartSession.restype = void_pointer
        self.dll.WintunEndSession.argtypes = [void_pointer]
        self.dll.WintunEndSession.restype = None
        self.dll.WintunGetReadWaitEvent.argtypes = [void_pointer]
        self.dll.WintunGetReadWaitEvent.restype = void_pointer
        self.dll.WintunReceivePacket.argtypes = [void_pointer, ctypes.POINTER(dword)]
        self.dll.WintunReceivePacket.restype = void_pointer
        self.dll.WintunReleaseReceivePacket.argtypes = [void_pointer, void_pointer]
        self.dll.WintunReleaseReceivePacket.restype = None
        self.dll.WintunAllocateSendPacket.argtypes = [void_pointer, dword]
        self.dll.WintunAllocateSendPacket.restype = void_pointer
        self.dll.WintunSendPacket.argtypes = [void_pointer, void_pointer]
        self.dll.WintunSendPacket.restype = None
        self.kernel32.WaitForSingleObject.argtypes = [void_pointer, dword]
        self.kernel32.WaitForSingleObject.restype = dword

    def create_adapter(self, name: str) -> int:
        ctypes.set_last_error(0)
        adapter = self.dll.WintunCreateAdapter(name, "PdaLink", None)
        if not adapter:
            raise windows_error(f"WintunCreateAdapter({name}) failed")
        return int(adapter)

    def close_adapter(self, adapter: int) -> None:
        self.dll.WintunCloseAdapter(adapter)

    def start_session(self, adapter: int, capacity: int) -> int:
        ctypes.set_last_error(0)
        session = self.dll.WintunStartSession(adapter, capacity)
        if not session:
            raise windows_error("WintunStartSession failed")
        return int(session)

    def end_session(self, session: int) -> None:
        self.dll.WintunEndSession(session)

    def get_read_wait_event(self, session: int) -> int:
        event = self.dll.WintunGetReadWaitEvent(session)
        if not event:
            raise windows_error("WintunGetReadWaitEvent failed")
        return int(event)

    def receive_packet(self, session: int) -> bytes | None:
        packet_size = ctypes.c_uint32()
        ctypes.set_last_error(0)
        packet = self.dll.WintunReceivePacket(session, ctypes.byref(packet_size))
        if not packet:
            error = ctypes.get_last_error()
            if error == ERROR_NO_MORE_ITEMS:
                return None
            if error == ERROR_HANDLE_EOF:
                raise EOFError("Wintun adapter is terminating")
            raise windows_error("WintunReceivePacket failed", error)
        try:
            return ctypes.string_at(packet, packet_size.value)
        finally:
            self.dll.WintunReleaseReceivePacket(session, packet)

    def wait_for_packet(self, event: int, timeout_ms: int) -> bool:
        result = self.kernel32.WaitForSingleObject(event, timeout_ms)
        if result == WAIT_OBJECT_0:
            return True
        if result == WAIT_TIMEOUT:
            return False
        raise windows_error("WaitForSingleObject failed")

    def send_packet(self, session: int, packet: bytes) -> bool:
        if not packet or len(packet) > WINTUN_MAX_IP_PACKET_SIZE:
            raise ValueError(f"invalid Wintun IP packet length: {len(packet)}")
        ctypes.set_last_error(0)
        target = self.dll.WintunAllocateSendPacket(session, len(packet))
        if not target:
            error = ctypes.get_last_error()
            if error == ERROR_BUFFER_OVERFLOW:
                return False
            if error == ERROR_HANDLE_EOF:
                raise EOFError("Wintun adapter is terminating")
            raise windows_error("WintunAllocateSendPacket failed", error)
        ctypes.memmove(target, packet, len(packet))
        self.dll.WintunSendPacket(session, target)
        return True


class WintunPacketDevice:
    def __init__(self, api: WintunApi, adapter_name: str, ring_capacity: int = DEFAULT_RING_CAPACITY):
        self.api = api
        self.adapter_name = adapter_name
        self.adapter: int | None = None
        self.session: int | None = None
        self.read_event: int | None = None
        capacity = validate_ring_capacity(ring_capacity)
        try:
            self.adapter = api.create_adapter(adapter_name)
            self.session = api.start_session(self.adapter, capacity)
            self.read_event = api.get_read_wait_event(self.session)
        except Exception:
            self.close()
            raise

    def receive(self, timeout_ms: int) -> bytes | None:
        if self.session is None or self.read_event is None:
            return None
        packet = self.api.receive_packet(self.session)
        if packet is not None:
            return packet
        if not self.api.wait_for_packet(self.read_event, timeout_ms):
            return None
        return self.api.receive_packet(self.session)

    def send(self, packet: bytes) -> bool:
        if self.session is None:
            return False
        return self.api.send_packet(self.session, packet)

    def close(self) -> None:
        session, self.session = self.session, None
        self.read_event = None
        if session is not None:
            self.api.end_session(session)
        adapter, self.adapter = self.adapter, None
        if adapter is not None:
            self.api.close_adapter(adapter)


class WintunHttpAdapter(TunHttpAdapter):
    def __init__(
        self,
        device: PacketDevice,
        proxy_url: str,
        *,
        mtu: int = 1500,
        dns: bool = True,
        connect_timeout: float = 10.0,
        tcp_idle_timeout: float = TCP_IDLE_TIMEOUT,
        verbose: bool = False,
    ):
        super().__init__(
            -1,
            device.adapter_name,
            proxy_url,
            mtu=mtu,
            dns=dns,
            connect_timeout=connect_timeout,
            tcp_idle_timeout=tcp_idle_timeout,
            verbose=verbose,
        )
        self.device = device
        self.device_closed = False

    def write_packet(self, packet: bytes) -> None:
        with self.write_lock:
            if self.device_closed:
                return
            if not self.device.send(packet):
                self.log("drop outbound Wintun packet: send ring is full")

    def close(self) -> None:
        self.stop_event.set()
        for flow in list(self.flows.values()):
            flow.close("adapter_shutdown")
        with self.write_lock:
            if not self.device_closed:
                self.device_closed = True
                self.device.close()

    def run_windows(
        self,
        *,
        activation_file: Path,
        stop_file: Path,
        activation_timeout: float,
        close_on_exit: bool = True,
    ) -> None:
        deadline = time.monotonic() + activation_timeout
        try:
            while not activation_file.exists():
                if stop_file.exists() or self.stop_event.is_set():
                    return
                if time.monotonic() >= deadline:
                    raise TimeoutError("controller did not activate Wintun routes before the deadline")
                time.sleep(0.1)

            self.log(f"adapter active on {self.iface}")
            while not self.stop_event.is_set() and not stop_file.exists():
                raw = self.device.receive(500)
                if raw:
                    self.handle_packet(raw)
                self.cleanup_idle_flows()
        except EOFError:
            self.log("Wintun adapter terminated")
        finally:
            if close_on_exit:
                self.close()


def write_json_exclusive(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.{time.time_ns()}.tmp")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    fd = os.open(temporary, flags, 0o600)
    try:
        encoded = (json.dumps(payload, sort_keys=True) + "\n").encode("utf-8")
        remaining = memoryview(encoded)
        while remaining:
            written = os.write(fd, remaining)
            if written <= 0:
                raise OSError("failed to write Wintun readiness file")
            remaining = remaining[written:]
        os.fsync(fd)
    finally:
        os.close(fd)
    try:
        # Publish a fully flushed inode and fail when the ownership path exists.
        # ProgramData and the temporary file are on the same NTFS volume.
        os.link(temporary, path)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def supervise_wifi(controller: Path, state_path: Path, token: str,
                   stop_file: Path, stop_event: threading.Event) -> None:
    """Bounded reconnect attempts; PowerShell validates saved ownership and index.

    Runs in the detached backend, so an SSH logout does not end recovery.
    The controller mutex serializes repair with start/stop.
    """
    delay = 5
    while not stop_event.wait(delay) and not stop_file.exists():
        try:
            subprocess.run(
                ["powershell.exe", "-NoLogo", "-NoProfile", "-NonInteractive",
                 "-ExecutionPolicy", "Bypass", "-File", str(controller), "recover",
                 "-StatePath", str(state_path), "-RecoveryToken", token],
                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL, timeout=45, check=False,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
        except (OSError, subprocess.TimeoutExpired):
            pass  # Keep the packet engine pinned and retry; never select another NIC.
        delay = min(60, delay * 2)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="PdaLink experimental Windows Wintun adapter")
    parser.add_argument("--stdout-log", type=Path)
    parser.add_argument("--stderr-log", type=Path)
    parser.add_argument("--controller", type=Path)
    parser.add_argument("--state-path", type=Path)
    parser.add_argument("--dll", required=True, type=Path)
    parser.add_argument("--dll-sha256", required=True)
    parser.add_argument("--adapter-name", required=True)
    parser.add_argument("--upstream-interface-index", type=int, required=True)
    parser.add_argument("--proxy-url", default="http://192.168.49.1:8001")
    parser.add_argument("--ready-file", required=True, type=Path)
    parser.add_argument("--activation-file", required=True, type=Path)
    parser.add_argument("--stop-file", required=True, type=Path)
    parser.add_argument("--token", required=True)
    parser.add_argument("--ring-capacity", type=lambda raw: int(raw, 0), default=DEFAULT_RING_CAPACITY)
    parser.add_argument("--mtu", type=int, default=1500)
    parser.add_argument("--activation-timeout", type=float, default=30.0)
    parser.add_argument("--connect-timeout", type=float, default=10.0)
    parser.add_argument("--tcp-idle-timeout", type=float, default=TCP_IDLE_TIMEOUT)
    parser.add_argument("--no-dns", action="store_true")
    parser.add_argument("--verbose", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    # CIM launch has no inherited console/log handles.
    if args.stdout_log:
        sys.stdout = args.stdout_log.open("a", encoding="utf-8", buffering=1)
    if args.stderr_log:
        sys.stderr = args.stderr_log.open("a", encoding="utf-8", buffering=1)
    if bool(args.controller) != bool(args.state_path):
        raise ValueError("controller and state path must be supplied together")
    if not args.token or len(args.token) > 128:
        raise ValueError("controller token is missing or too long")
    if not 1 <= args.upstream_interface_index <= 0xFFFFFFFF:
        raise ValueError("physical upstream interface index is required")
    dll_path = verify_dll(args.dll, args.dll_sha256)
    validate_ring_capacity(args.ring_capacity)
    api = WintunApi(dll_path)
    device = WintunPacketDevice(api, args.adapter_name, args.ring_capacity)
    adapter = WintunHttpAdapter(
        device,
        args.proxy_url,
        mtu=args.mtu,
        dns=not args.no_dns,
        connect_timeout=args.connect_timeout,
        tcp_idle_timeout=args.tcp_idle_timeout,
        verbose=args.verbose,
    )

    adapter.proxy.upstream_interface_index = args.upstream_interface_index

    def request_stop(_signum: int, _frame: object) -> None:
        adapter.stop_event.set()

    signal.signal(signal.SIGINT, request_stop)
    signal.signal(signal.SIGTERM, request_stop)
    if hasattr(signal, "SIGBREAK"):
        signal.signal(signal.SIGBREAK, request_stop)

    try:
        write_json_exclusive(
            args.ready_file,
            {
                "version": 1,
                "owner": "pdalink-wintun",
                "pid": os.getpid(),
                "token": args.token,
                "adapter_name": args.adapter_name,
                "created_at": time.time(),
            },
        )
        if args.controller:
            threading.Thread(target=supervise_wifi, args=(
                args.controller, args.state_path, args.token, args.stop_file, adapter.stop_event,
            ), daemon=True, name="pdalink-wifi-recovery").start()
        adapter.run_windows(
            activation_file=args.activation_file,
            stop_file=args.stop_file,
            activation_timeout=args.activation_timeout,
        )
    finally:
        adapter.close()
        try:
            args.ready_file.unlink()
        except FileNotFoundError:
            pass
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"pdalink_wintun: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
