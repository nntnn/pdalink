#!/usr/bin/env python3
"""Minimal Linux TUN to HTTP CONNECT proxy adapter for PdaLink.

This is intentionally small and prototype-oriented. It supports outbound IPv4
TCP streams and UDP/53 DNS queries. Other protocols are ignored.
"""

from __future__ import annotations

import ipaddress
import os
import random
import select
import socket
import struct
import threading
import time
from collections import Counter
from dataclasses import dataclass
from urllib.parse import urlparse

try:
    import fcntl
except ImportError:  # Windows imports the packet engine but supplies Wintun I/O.
    fcntl = None


IFF_TUN = 0x0001
IFF_NO_PI = 0x1000
TUNSETIFF = 0x400454CA

IP_PROTO_TCP = 6
IP_PROTO_UDP = 17
DNS_MAX_IN_FLIGHT = 64
DNS_TCP_MAX_RESPONSE = 4096
TCP_IDLE_TIMEOUT = 0.0
MAX_TCP_FLOWS = 512
MAX_PENDING_TCP_CONNECTS = 64

# The phone relays internet traffic only. Destinations on local or private
# networks can never be reached through the phone's cellular egress, so a SYN
# for one is answered with an immediate reset instead of a proxy CONNECT that
# would hold a phone-side socket until its connect timeout.
LOCAL_ONLY_NETWORKS = tuple(
    ipaddress.ip_network(cidr)
    for cidr in (
        "0.0.0.0/8",
        "10.0.0.0/8",
        "127.0.0.0/8",
        "169.254.0.0/16",
        "172.16.0.0/12",
        "192.168.0.0/16",
        "224.0.0.0/4",
        "240.0.0.0/4",
    )
)


def is_local_only_destination(address: str) -> bool:
    try:
        ip = ipaddress.ip_address(address)
    except ValueError:
        return False
    return any(ip in network for network in LOCAL_ONLY_NETWORKS)

TCP_FIN = 0x01
TCP_SYN = 0x02
TCP_RST = 0x04
TCP_PSH = 0x08
TCP_ACK = 0x10


@dataclass(frozen=True)
class IPv4Packet:
    src: str
    dst: str
    proto: int
    payload: bytes
    ident: int = 0
    ttl: int = 64


@dataclass(frozen=True)
class TcpSegment:
    src_port: int
    dst_port: int
    seq: int
    ack: int
    flags: int
    window: int
    payload: bytes


@dataclass(frozen=True)
class UdpDatagram:
    src_port: int
    dst_port: int
    payload: bytes


class PacketError(ValueError):
    pass


class ProxyConnectError(OSError):
    pass


def checksum(data: bytes) -> int:
    if len(data) % 2:
        data += b"\x00"
    total = 0
    for index in range(0, len(data), 2):
        total += (data[index] << 8) + data[index + 1]
        total = (total & 0xFFFF) + (total >> 16)
    return (~total) & 0xFFFF


def ipv4_bytes(address: str) -> bytes:
    return socket.inet_aton(address)


def parse_ipv4_packet(raw: bytes) -> IPv4Packet:
    if len(raw) < 20:
        raise PacketError("short IPv4 packet")
    version_ihl = raw[0]
    version = version_ihl >> 4
    ihl = (version_ihl & 0x0F) * 4
    if version != 4 or ihl < 20:
        raise PacketError("not an IPv4 packet")
    if len(raw) < ihl:
        raise PacketError("truncated IPv4 header")
    total_length = struct.unpack("!H", raw[2:4])[0]
    if total_length < ihl or len(raw) < total_length:
        raise PacketError("truncated IPv4 payload")
    flags_offset = struct.unpack("!H", raw[6:8])[0]
    fragment_offset = flags_offset & 0x1FFF
    more_fragments = bool(flags_offset & 0x2000)
    if fragment_offset or more_fragments:
        raise PacketError("fragmented IPv4 packets are not supported")
    ident = struct.unpack("!H", raw[4:6])[0]
    ttl = raw[8]
    proto = raw[9]
    src = socket.inet_ntoa(raw[12:16])
    dst = socket.inet_ntoa(raw[16:20])
    return IPv4Packet(src, dst, proto, raw[ihl:total_length], ident=ident, ttl=ttl)


def build_ipv4_packet(src: str, dst: str, proto: int, payload: bytes, ident: int = 0, ttl: int = 64) -> bytes:
    total_length = 20 + len(payload)
    header = struct.pack(
        "!BBHHHBBH4s4s",
        0x45,
        0,
        total_length,
        ident & 0xFFFF,
        0,
        ttl,
        proto,
        0,
        ipv4_bytes(src),
        ipv4_bytes(dst),
    )
    header = header[:10] + struct.pack("!H", checksum(header)) + header[12:]
    return header + payload


def parse_tcp_segment(raw: bytes) -> TcpSegment:
    if len(raw) < 20:
        raise PacketError("short TCP segment")
    src_port, dst_port, seq, ack, offset_flags, window = struct.unpack("!HHIIHH", raw[:16])
    data_offset = ((offset_flags >> 12) & 0x0F) * 4
    if data_offset < 20 or len(raw) < data_offset:
        raise PacketError("truncated TCP header")
    flags = offset_flags & 0x01FF
    return TcpSegment(src_port, dst_port, seq, ack, flags, window, raw[data_offset:])


def build_tcp_segment(
    src_ip: str,
    dst_ip: str,
    src_port: int,
    dst_port: int,
    seq: int,
    ack: int,
    flags: int,
    payload: bytes = b"",
    window: int = 65535,
) -> bytes:
    offset_flags = (5 << 12) | (flags & 0x01FF)
    header = struct.pack("!HHIIHHHH", src_port, dst_port, seq & 0xFFFFFFFF, ack & 0xFFFFFFFF, offset_flags, window, 0, 0)
    pseudo = ipv4_bytes(src_ip) + ipv4_bytes(dst_ip) + struct.pack("!BBH", 0, IP_PROTO_TCP, len(header) + len(payload))
    tcp_sum = checksum(pseudo + header + payload)
    header = header[:16] + struct.pack("!H", tcp_sum) + header[18:]
    return header + payload


def parse_udp_datagram(raw: bytes) -> UdpDatagram:
    if len(raw) < 8:
        raise PacketError("short UDP datagram")
    src_port, dst_port, length, _ = struct.unpack("!HHHH", raw[:8])
    if length < 8 or len(raw) < length:
        raise PacketError("truncated UDP datagram")
    return UdpDatagram(src_port, dst_port, raw[8:length])


def build_udp_datagram(src_port: int, dst_port: int, payload: bytes) -> bytes:
    length = 8 + len(payload)
    return struct.pack("!HHHH", src_port, dst_port, length, 0) + payload


class HttpConnectProxy:
    def __init__(self, proxy_url: str, timeout: float = 10.0):
        parsed = urlparse(proxy_url)
        if parsed.scheme != "http" or not parsed.hostname or parsed.port is None:
            raise ValueError("proxy URL must look like http://HOST:PORT")
        self.host = parsed.hostname
        self.port = parsed.port
        self.timeout = timeout
        self.upstream_device = ""
        self.upstream_interface_index = 0

    def connect(self, dst_host: str, dst_port: int) -> socket.socket:
        if self.upstream_device or self.upstream_interface_index:
            # A device constraint survives route removal: never fall back to TUN.
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            try:
                sock.settimeout(self.timeout)
                if self.upstream_device:
                    sock.setsockopt(socket.SOL_SOCKET, socket.SO_BINDTODEVICE,
                                    self.upstream_device.encode("ascii") + b"\0")
                else:
                    # Windows IP_UNICAST_IF takes the interface index in network byte order.
                    sock.setsockopt(socket.IPPROTO_IP, 31,
                                    struct.pack("!I", self.upstream_interface_index))
                sock.connect((self.host, self.port))
            except BaseException:
                sock.close()
                raise
        else:
            sock = socket.create_connection((self.host, self.port), timeout=self.timeout)
        sock.settimeout(self.timeout)
        target = f"{dst_host}:{dst_port}"
        request = (
            f"CONNECT {target} HTTP/1.1\r\n"
            f"Host: {target}\r\n"
            "Proxy-Connection: keep-alive\r\n"
            "Connection: keep-alive\r\n\r\n"
        ).encode("ascii")
        sock.sendall(request)
        response = read_http_headers(sock)
        status = response.split(b"\r\n", 1)[0]
        parts = status.split()
        if len(parts) < 2 or parts[1] != b"200":
            sock.close()
            text = status.decode("iso-8859-1", errors="replace")
            raise ProxyConnectError(f"CONNECT {target} failed: {text}")
        sock.settimeout(None)
        return sock


def read_http_headers(sock: socket.socket, limit: int = 8192) -> bytes:
    data = bytearray()
    while b"\r\n\r\n" not in data:
        chunk = sock.recv(1)
        if not chunk:
            break
        data.extend(chunk)
        if len(data) > limit:
            raise ProxyConnectError("proxy response headers too large")
    return bytes(data)


class TunHttpAdapter:
    def __init__(
        self,
        tun_fd: int,
        iface: str,
        proxy_url: str,
        mtu: int = 1500,
        dns: bool = True,
        connect_timeout: float = 10.0,
        tcp_idle_timeout: float = TCP_IDLE_TIMEOUT,
        verbose: bool = False,
    ):
        self.tun_fd = tun_fd
        self.iface = iface
        self.mtu = mtu
        self.dns = dns
        self.tcp_idle_timeout = tcp_idle_timeout
        self.verbose = verbose
        self.proxy = HttpConnectProxy(proxy_url, timeout=connect_timeout)
        self.flows: dict[tuple[str, int, str, int], TcpFlow] = {}
        self.tcp_close_reasons: Counter[str] = Counter()
        self.tcp_anomalies: Counter[str] = Counter()
        self.closed_tcp_bytes_up = 0
        self.closed_tcp_bytes_down = 0
        self.write_lock = threading.Lock()
        self.stop_event = threading.Event()
        self.dns_slots = threading.BoundedSemaphore(DNS_MAX_IN_FLIGHT)

    @classmethod
    def open(
        cls,
        iface: str,
        proxy_url: str,
        mtu: int = 1500,
        dns: bool = True,
        connect_timeout: float = 10.0,
        tcp_idle_timeout: float = TCP_IDLE_TIMEOUT,
        verbose: bool = False,
    ) -> "TunHttpAdapter":
        if fcntl is None:
            raise OSError("Linux TUN open requires the fcntl module")
        tun_fd = os.open("/dev/net/tun", os.O_RDWR)
        ifr = struct.pack("16sH", iface.encode("ascii")[:15], IFF_TUN | IFF_NO_PI)
        result = fcntl.ioctl(tun_fd, TUNSETIFF, ifr)
        real_iface = result[:16].split(b"\x00", 1)[0].decode("ascii")
        return cls(
            tun_fd,
            real_iface,
            proxy_url,
            mtu=mtu,
            dns=dns,
            connect_timeout=connect_timeout,
            tcp_idle_timeout=tcp_idle_timeout,
            verbose=verbose,
        )

    def close(self) -> None:
        self.stop_event.set()
        for flow in list(self.flows.values()):
            flow.close("adapter_shutdown")
        if self.tun_fd >= 0:
            fd = self.tun_fd
            self.tun_fd = -1
            try:
                os.close(fd)
            except OSError:
                pass

    def log(self, message: str) -> None:
        if self.verbose:
            print(message, flush=True)

    def write_packet(self, packet: bytes) -> None:
        with self.write_lock:
            os.write(self.tun_fd, packet)

    def run(self, close_on_exit: bool = True) -> None:
        self.log(f"adapter running on {self.iface}")
        try:
            while not self.stop_event.is_set():
                readable, _, _ = select.select([self.tun_fd], [], [], 0.5)
                if not readable:
                    self.cleanup_idle_flows()
                    continue
                raw = os.read(self.tun_fd, max(2048, self.mtu + 128))
                self.handle_packet(raw)
                self.cleanup_idle_flows()
        finally:
            if close_on_exit:
                self.close()

    def cleanup_idle_flows(self) -> None:
        if self.tcp_idle_timeout <= 0:
            return
        deadline = time.monotonic() - self.tcp_idle_timeout
        for flow in list(self.flows.values()):
            if flow.last_activity < deadline:
                self.log(f"close idle TCP {flow.client_ip}:{flow.client_port}->{flow.remote_ip}:{flow.remote_port}")
                flow.close("local_idle_cleanup")

    def record_tcp_anomaly(self, reason: str) -> None:
        self.tcp_anomalies[reason] += 1

    def record_tcp_close(self, reason: str, flow: "TcpFlow") -> None:
        self.tcp_close_reasons[reason] += 1
        self.closed_tcp_bytes_up += flow.bytes_up
        self.closed_tcp_bytes_down += flow.bytes_down
        self.log(
            "tcp-close "
            f"reason={reason} "
            f"{flow.client_ip}:{flow.client_port}->{flow.remote_ip}:{flow.remote_port} "
            f"bytes_up={flow.bytes_up} bytes_down={flow.bytes_down}"
        )

    def tcp_status(self) -> dict[str, object]:
        now = time.monotonic()
        active = [flow.snapshot(now) for flow in self.flows.values() if not flow.closed]
        active_bytes_up = sum(int(item["bytes_up"]) for item in active)
        active_bytes_down = sum(int(item["bytes_down"]) for item in active)
        return {
            "active_flows": len(active),
            "active": active,
            "closed": self.tcp_close_reasons.copy(),
            "anomalies": self.tcp_anomalies.copy(),
            "bytes_up": self.closed_tcp_bytes_up + active_bytes_up,
            "bytes_down": self.closed_tcp_bytes_down + active_bytes_down,
        }

    def handle_packet(self, raw: bytes) -> None:
        try:
            packet = parse_ipv4_packet(raw)
        except PacketError as exc:
            self.log(f"drop IPv4: {exc}")
            return
        if packet.proto == IP_PROTO_TCP:
            self.handle_tcp(packet)
        elif packet.proto == IP_PROTO_UDP and self.dns:
            self.handle_udp(packet)
        else:
            self.log(f"drop proto={packet.proto} {packet.src}->{packet.dst}")

    def handle_tcp(self, packet: IPv4Packet) -> None:
        try:
            segment = parse_tcp_segment(packet.payload)
        except PacketError as exc:
            self.log(f"drop TCP: {exc}")
            return
        key = (packet.src, segment.src_port, packet.dst, segment.dst_port)
        flow = self.flows.get(key)
        if segment.flags & TCP_SYN and not segment.flags & TCP_ACK:
            if flow is None:
                reject = self.syn_reject_reason(packet.dst)
                if reject:
                    self.record_tcp_anomaly(reject)
                    if self.verbose:
                        self.log(f"reject SYN {packet.dst}:{segment.dst_port}: {reject}")
                    self.send_tcp_reset(packet, segment)
                    return
                flow = TcpFlow(self, key, packet, segment)
                self.flows[key] = flow
                flow.start()
            return
        if flow is None:
            self.send_tcp_reset(packet, segment)
            return
        flow.handle(segment)

    def syn_reject_reason(self, dst: str) -> str:
        if is_local_only_destination(dst):
            return "rejected_local_destination"
        if len(self.flows) >= MAX_TCP_FLOWS:
            return "rejected_flow_limit"
        pending = sum(1 for flow in list(self.flows.values()) if not flow.connected and not flow.closed)
        if pending >= MAX_PENDING_TCP_CONNECTS:
            return "rejected_pending_connect_limit"
        return ""

    def handle_udp(self, packet: IPv4Packet) -> None:
        try:
            datagram = parse_udp_datagram(packet.payload)
        except PacketError as exc:
            self.log(f"drop UDP: {exc}")
            return
        if datagram.dst_port != 53:
            self.log(f"drop UDP {packet.dst}:{datagram.dst_port}")
            return
        if not self.dns_slots.acquire(blocking=False):
            self.log(f"drop DNS {packet.dst}: too many queries in flight")
            return
        try:
            thread = threading.Thread(target=self.resolve_dns_over_tcp, args=(packet, datagram), daemon=True)
            thread.start()
        except RuntimeError as exc:
            self.dns_slots.release()
            self.log(f"drop DNS {packet.dst}: failed to start resolver thread: {exc}")

    def resolve_dns_over_tcp(self, packet: IPv4Packet, datagram: UdpDatagram) -> None:
        try:
            with self.proxy.connect(packet.dst, 53) as sock:
                sock.sendall(struct.pack("!H", len(datagram.payload)) + datagram.payload)
                header = recv_exact(sock, 2)
                response_length = struct.unpack("!H", header)[0]
                if response_length > DNS_TCP_MAX_RESPONSE:
                    raise ProxyConnectError(f"DNS TCP response too large: {response_length}")
                response = recv_exact(sock, response_length)
        except OSError as exc:
            self.log(f"DNS failed via {packet.dst}: {exc}")
            self.send_dns_error(packet, datagram)
            return
        finally:
            self.dns_slots.release()
        udp = build_udp_datagram(datagram.dst_port, datagram.src_port, response)
        reply = build_ipv4_packet(packet.dst, packet.src, IP_PROTO_UDP, udp)
        self.write_packet(reply)

    def send_dns_error(self, packet: IPv4Packet, datagram: UdpDatagram) -> None:
        response = build_dns_error_response(datagram.payload)
        if not response:
            return
        udp = build_udp_datagram(datagram.dst_port, datagram.src_port, response)
        reply = build_ipv4_packet(packet.dst, packet.src, IP_PROTO_UDP, udp)
        self.write_packet(reply)

    def send_tcp_reset(self, packet: IPv4Packet, segment: TcpSegment) -> None:
        ack = (segment.seq + len(segment.payload)) & 0xFFFFFFFF
        if segment.flags & TCP_SYN:
            ack = (ack + 1) & 0xFFFFFFFF
        tcp = build_tcp_segment(
            packet.dst,
            packet.src,
            segment.dst_port,
            segment.src_port,
            0,
            ack,
            TCP_RST | TCP_ACK,
        )
        self.write_packet(build_ipv4_packet(packet.dst, packet.src, IP_PROTO_TCP, tcp))

    def remove_flow(self, key: tuple[str, int, str, int]) -> None:
        self.flows.pop(key, None)


class TcpFlow:
    def __init__(self, adapter: TunHttpAdapter, key: tuple[str, int, str, int], packet: IPv4Packet, syn: TcpSegment):
        self.adapter = adapter
        self.key = key
        self.client_ip, self.client_port, self.remote_ip, self.remote_port = key
        self.client_next = (syn.seq + 1) & 0xFFFFFFFF
        self.server_next = random.randrange(1, 0xFFFFFFFF)
        self.sock: socket.socket | None = None
        self.connected = False
        self.closed = False
        self.last_activity = time.monotonic()
        self.opened_at = self.last_activity
        self.bytes_up = 0
        self.bytes_down = 0
        self.lock = threading.Lock()

    def start(self) -> None:
        thread = threading.Thread(target=self.open_proxy, daemon=True)
        thread.start()

    def open_proxy(self) -> None:
        try:
            sock = self.adapter.proxy.connect(self.remote_ip, self.remote_port)
        except OSError as exc:
            self.adapter.log(f"CONNECT failed {self.remote_ip}:{self.remote_port}: {exc}")
            self.send(flags=TCP_RST | TCP_ACK)
            self.close("proxy_connect_failure")
            return
        with self.lock:
            if self.closed:
                try:
                    sock.close()
                except OSError:
                    pass
                return
            self.sock = sock
            self.connected = True
            self.last_activity = time.monotonic()
        self.send(flags=TCP_SYN | TCP_ACK, advance=1)
        reader = threading.Thread(target=self.read_upstream, daemon=True)
        reader.start()

    def handle(self, segment: TcpSegment) -> None:
        if self.closed:
            return
        self.last_activity = time.monotonic()
        if segment.flags & TCP_RST:
            self.close("local_reset")
            return
        if not self.connected:
            return
        if segment.payload:
            failed = False
            with self.lock:
                if segment.seq != self.client_next:
                    if segment.seq < self.client_next:
                        self.adapter.record_tcp_anomaly("duplicate_payload")
                    else:
                        self.adapter.record_tcp_anomaly("out_of_order_payload")
                    expected = self.client_next
                else:
                    expected = (self.client_next + len(segment.payload)) & 0xFFFFFFFF
                    self.client_next = expected
                    sock = self.sock
                    if sock is not None:
                        try:
                            sock.sendall(segment.payload)
                            self.bytes_up += len(segment.payload)
                        except OSError:
                            failed = True
            if failed:
                self.close("proxy_write_failed")
                return
            self.send(flags=TCP_ACK, ack_override=expected)
        elif segment.flags & TCP_FIN:
            with self.lock:
                self.client_next = (segment.seq + 1) & 0xFFFFFFFF
            self.send(flags=TCP_ACK)
            self.send(flags=TCP_FIN | TCP_ACK, advance=1)
            self.close("local_fin")

    def read_upstream(self) -> None:
        reason = "proxy_close"
        try:
            while not self.closed and self.sock is not None:
                data = self.sock.recv(1400)
                if not data:
                    break
                self.last_activity = time.monotonic()
                self.bytes_down += len(data)
                self.send(flags=TCP_ACK | TCP_PSH, payload=data, advance=len(data))
        except OSError:
            reason = "proxy_read_failed"
        if not self.closed:
            self.send(flags=TCP_FIN | TCP_ACK, advance=1)
        self.close(reason)

    def send(self, flags: int, payload: bytes = b"", advance: int = 0, ack_override: int | None = None) -> None:
        with self.lock:
            seq = self.server_next
            ack = self.client_next if ack_override is None else ack_override
            tcp = build_tcp_segment(
                self.remote_ip,
                self.client_ip,
                self.remote_port,
                self.client_port,
                seq,
                ack,
                flags,
                payload=payload,
            )
            if advance:
                self.server_next = (self.server_next + advance) & 0xFFFFFFFF
        packet = build_ipv4_packet(self.remote_ip, self.client_ip, IP_PROTO_TCP, tcp)
        self.adapter.write_packet(packet)

    def snapshot(self, now: float | None = None) -> dict[str, object]:
        current = time.monotonic() if now is None else now
        return {
            "client": f"{self.client_ip}:{self.client_port}",
            "remote": f"{self.remote_ip}:{self.remote_port}",
            "duration_seconds": max(0.0, current - self.opened_at),
            "bytes_up": self.bytes_up,
            "bytes_down": self.bytes_down,
        }

    def close(self, reason: str = "local_close") -> None:
        with self.lock:
            if self.closed:
                return
            self.closed = True
            sock = self.sock
            self.sock = None
        self.adapter.record_tcp_close(reason, self)
        if sock is not None:
            try:
                sock.close()
            except OSError:
                pass
        self.adapter.remove_flow(self.key)


def recv_exact(sock: socket.socket, size: int) -> bytes:
    chunks = bytearray()
    while len(chunks) < size:
        chunk = sock.recv(size - len(chunks))
        if not chunk:
            raise ProxyConnectError("connection closed while reading")
        chunks.extend(chunk)
    return bytes(chunks)


def build_dns_error_response(query: bytes, rcode: int = 2) -> bytes:
    if len(query) < 12:
        return b""
    query_id, flags, qdcount, _ancount, _nscount, _arcount = struct.unpack("!HHHHHH", query[:12])
    response_flags = 0x8000 | (flags & 0x0100) | 0x0080 | (rcode & 0x000F)
    return struct.pack("!HHHHHH", query_id, response_flags, qdcount, 0, 0, 0) + query[12:]
