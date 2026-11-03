#!/usr/bin/env python3
"""Linux helper for the PdaLink Wi-Fi Direct and USB proxy prototype."""

from __future__ import annotations

import argparse
import ast
import base64
import fcntl
import hashlib
import ipaddress
import json
import os
import re
import shlex
import shutil
import signal
import socket
import stat
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable
from urllib.parse import parse_qsl, urlencode, urlparse


DEFAULT_HOST = "192.168.49.1"
DEFAULT_PORTS = (8000, 8001)
WIFI_DIRECT_PROFILE_NAME = "pdalink-wifi-direct"
LOCAL_HOTSPOT_PROFILE_NAME = "pdalink-local-hotspot"
WIFI_DIRECT_PROFILE_SETTINGS = (
    "connection.autoconnect", "no",
    "802-11-wireless.cloned-mac-address", "permanent",
    "ipv4.never-default", "yes",
    "ipv4.ignore-auto-dns", "yes",
    "ipv4.route-metric", "600",
    "ipv6.method", "ignore",
)
USB_HOST = "127.0.0.1"
USB_LOCAL_PORT = 18080
USB_REMOTE_PORT = 8000
MANAGED_UDEV_USB_IDS = ("04e8:6860", "04e8:6864", "18d1:2d01")
ANDROID_DEBUG_ACTIVITY = "dev.pdalink.prototype/.DebugControlActivity"
ANDROID_PROXY_SERVICE = "dev.pdalink.prototype/.ProxyService"
ANDROID_ACTION_START = "dev.pdalink.prototype.START_USB"
ANDROID_ACTION_START_WIFI_DIRECT = "dev.pdalink.prototype.START_WIFI_DIRECT"
ANDROID_ACTION_START_LOCAL_HOTSPOT = "dev.pdalink.prototype.START_LOCAL_HOTSPOT"
ANDROID_ACTION_QUERY_LOCAL_HOTSPOT = "dev.pdalink.prototype.QUERY_LOCAL_HOTSPOT"
ANDROID_LOCAL_HOTSPOT_RECEIVER = "dev.pdalink.prototype/.DebugControlReceiver"
ANDROID_ACTION_STOP = "dev.pdalink.prototype.STOP"
DEFAULT_SYSTEM_IFACE = "pdalink0"
DEFAULT_SYSTEM_ADDRESS = "10.66.0.2/24"
DEFAULT_SYSTEM_MTU = 1500
DEFAULT_SYSTEM_ROUTES = ("0.0.0.0/1", "128.0.0.0/1")
TUN_DEVICE = Path("/dev/net/tun")
DEFAULT_SYSTEM_STATE_FILE = "/run/pdalink/system.json"
DEFAULT_AUTO_CONNECT = "off"
DEFAULT_LOCAL_HOTSPOT_CREDENTIALS = "off"
DEFAULT_LOCAL_HOTSPOT_JOIN = "off"
DEFAULT_LOCAL_HOTSPOT_TARGET = "off"
DEFAULT_ROUTE_MODE = "active_override"
DEFAULT_DNS_MODE = "resolved"
DEFAULT_ROUTE_METRIC = 50000
DEFAULT_DNS_SERVERS = ("1.1.1.1", "8.8.8.8")
CONFIG_VERSION = 1
ADB_TRANSPORT_NAME = "adb"
ADB_TRANSPORT_ROLE = "development"
ADB_TRANSPORT_LINE = f"transport: {ADB_TRANSPORT_NAME} ({ADB_TRANSPORT_ROLE})"
PRODUCT_USB_TRANSPORT_NAME = "product-usb"
PRODUCT_USB_TRANSPORT_ROLE = "product"
PRODUCT_USB_TRANSPORT_LINE = f"transport: {PRODUCT_USB_TRANSPORT_NAME} ({PRODUCT_USB_TRANSPORT_ROLE})"
WIFI_DIRECT_TRANSPORT_NAME = "wifi-direct"
WIFI_DIRECT_TRANSPORT_ROLE = "manual"
WIFI_DIRECT_TRANSPORT_LINE = f"transport: {WIFI_DIRECT_TRANSPORT_NAME} ({WIFI_DIRECT_TRANSPORT_ROLE})"
DEFAULT_PRODUCT_USB_STATE_FILE = "product-usb.json"
PRODUCT_USB_STATE_MAX_AGE_SECONDS = int(os.getenv("PDALINK_PRODUCT_USB_STATE_MAX_AGE_SECONDS", "300"))
DEFAULT_WIFI_PROXY_STATE_FILE = "wifi-direct-proxy.json"
GNOME_PROXY_SCHEMA = "org.gnome.system.proxy"
GNOME_PROXY_KEYS = ("mode", "autoconfig-url")


@dataclass(frozen=True)
class ProbeResult:
    host: str
    port: int
    ok: bool
    detail: str
    pac: str = ""


@dataclass(frozen=True)
class ProductUsbStatus:
    ready: bool
    readiness: str
    next_steps: tuple[str, ...]
    state: dict[str, object]


@dataclass(frozen=True)
class RouteInfo:
    raw: str
    dev: str = ""
    src: str = ""
    via: str = ""


@dataclass(frozen=True)
class WifiNetwork:
    ssid: str
    security: str
    signal: int
    in_use: bool = False


@dataclass(frozen=True)
class NmActiveConnection:
    name: str
    type: str
    device: str = ""


@dataclass(frozen=True)
class ToolStatus:
    name: str
    path: str = ""

    @property
    def ok(self) -> bool:
        return bool(self.path)


@dataclass(frozen=True)
class CommandResult:
    returncode: int
    stdout: str = ""
    stderr: str = ""


@dataclass(frozen=True)
class AdbForward:
    serial: str
    local: str
    remote: str

    @property
    def local_port(self) -> int | None:
        return parse_tcp_forward_port(self.local)

    @property
    def remote_port(self) -> int | None:
        return parse_tcp_forward_port(self.remote)


@dataclass(frozen=True)
class LocalHotspotCredentials:
    ssid: str
    password: str
    interface: str
    host: str
    port: int
    security: str


@dataclass(frozen=True)
class ConnectionBundle:
    transport: str
    ssid: str
    security: str
    password: str
    proxy_url: str
    host: str
    port: int


@dataclass(frozen=True)
class AdbDevice:
    serial: str
    state: str
    detail: str = ""

    @property
    def ok(self) -> bool:
        return self.state == "device"


CONNECTION_URI_VERSION = 1
CONNECTION_URI_KEYS = frozenset(("v", "transport", "ssid", "security", "password", "proxy"))
CONNECTION_TRANSPORTS = frozenset(("local-hotspot", "wifi-direct"))
RFC1918_NETWORKS = tuple(ipaddress.ip_network(value) for value in (
    "10.0.0.0/8",
    "172.16.0.0/12",
    "192.168.0.0/16",
))


def normalize_connection_security(value: str) -> str:
    normalized = value.strip().lower()
    aliases = {
        "wpa2-psk": "wpa2-psk",
        "wpa3-sae": "wpa3-sae",
        "wpa2-wpa3": "wpa2-wpa3",
        "wpa2/wpa3 transition": "wpa2-wpa3",
    }
    try:
        return aliases[normalized]
    except KeyError as exc:
        raise ValueError("unsupported Wi-Fi security mode") from exc


def parse_connection_uri(raw: str) -> ConnectionBundle:
    value = raw.strip()
    if not value:
        raise ValueError("connection URI is empty")
    parsed = urlparse(value)
    if (
        parsed.scheme.lower() != "pdalink"
        or parsed.hostname is None
        or parsed.hostname.lower() != "connect"
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path
        or parsed.params
        or parsed.fragment
    ):
        raise ValueError("connection URI target is invalid")
    try:
        if parsed.port is not None:
            raise ValueError("connection URI target is invalid")
        pairs = parse_qsl(parsed.query, keep_blank_values=True, strict_parsing=True)
    except ValueError as exc:
        raise ValueError("connection URI parameters are malformed") from exc
    values: dict[str, str] = {}
    for key, item in pairs:
        if key not in CONNECTION_URI_KEYS:
            raise ValueError(f"unknown connection URI parameter: {key}")
        if key in values:
            raise ValueError(f"duplicate connection URI parameter: {key}")
        values[key] = item
    missing = [key for key in CONNECTION_URI_KEYS if not values.get(key)]
    if missing:
        raise ValueError(f"missing connection URI parameter: {sorted(missing)[0]}")
    if values["v"] != str(CONNECTION_URI_VERSION):
        raise ValueError("unsupported connection URI version")

    transport = values["transport"].strip().lower()
    if transport not in CONNECTION_TRANSPORTS:
        raise ValueError("unsupported Wi-Fi transport")
    ssid = values["ssid"]
    if not 1 <= len(ssid.encode("utf-8")) <= 32 or "\0" in ssid:
        raise ValueError("SSID must be between 1 and 32 UTF-8 bytes")
    password = values["password"]
    if not 8 <= len(password) <= 63 or any(not 0x20 <= ord(char) <= 0x7e for char in password):
        raise ValueError("Wi-Fi password must be 8-63 printable ASCII characters")
    security = normalize_connection_security(values["security"])

    proxy = urlparse(values["proxy"])
    try:
        proxy_port = proxy.port
        proxy_address = ipaddress.ip_address(proxy.hostname or "")
    except ValueError as exc:
        raise ValueError("proxy endpoint is invalid") from exc
    if (
        proxy.scheme.lower() != "http"
        or proxy.hostname is None
        or proxy.username is not None
        or proxy.password is not None
        or proxy_port is None
        or not 1 <= proxy_port <= 65535
        or proxy.path
        or proxy.params
        or proxy.query
        or proxy.fragment
        or proxy_address.version != 4
        or not any(proxy_address in network for network in RFC1918_NETWORKS)
    ):
        raise ValueError("proxy must be an HTTP endpoint on a private IPv4 address")
    host = str(proxy_address)
    return ConnectionBundle(
        transport=transport,
        ssid=ssid,
        security=security,
        password=password,
        proxy_url=f"http://{host}:{proxy_port}",
        host=host,
        port=proxy_port,
    )


def build_connection_uri(bundle: ConnectionBundle) -> str:
    raw = "pdalink://connect?" + urlencode({
        "v": CONNECTION_URI_VERSION,
        "transport": bundle.transport,
        "ssid": bundle.ssid,
        "security": bundle.security,
        "password": bundle.password,
        "proxy": bundle.proxy_url,
    })
    validated = parse_connection_uri(raw)
    return "pdalink://connect?" + urlencode({
        "v": CONNECTION_URI_VERSION,
        "transport": validated.transport,
        "ssid": validated.ssid,
        "security": validated.security,
        "password": validated.password,
        "proxy": validated.proxy_url,
    })


@dataclass(frozen=True)
class SystemRouteConfig:
    iface: str
    address: str
    mtu: int
    routes: tuple[str, ...]
    proxy_url: str
    adapter_command: tuple[str, ...] = ()
    route_mode: str = DEFAULT_ROUTE_MODE
    route_metric: int = DEFAULT_ROUTE_METRIC
    dns_mode: str = DEFAULT_DNS_MODE
    dns_servers: tuple[str, ...] = DEFAULT_DNS_SERVERS
    upstream_device: str = ""


@dataclass(frozen=True)
class RouteConflict:
    route: str
    existing: str


@dataclass(frozen=True)
class SystemRuntimeState:
    pid: int
    config: SystemRouteConfig
    started_at: str
    pid_start_ticks: str = ""
    cmdline: str = ""


@dataclass(frozen=True)
class TrustedDeviceStore:
    default_serial: str
    devices: dict[str, dict[str, str]]


def parse_ports(raw: str | None) -> tuple[int, ...]:
    if not raw:
        return DEFAULT_PORTS
    ports: list[int] = []
    for item in raw.split(","):
        item = item.strip()
        if not item:
            continue
        port = int(item)
        if port <= 0 or port > 65535:
            raise ValueError(f"invalid port: {port}")
        ports.append(port)
    return tuple(ports)


def build_proxy_env(host: str, port: int) -> dict[str, str]:
    proxy = f"http://{host}:{port}"
    return {
        "http_proxy": proxy,
        "https_proxy": proxy,
        "HTTP_PROXY": proxy,
        "HTTPS_PROXY": proxy,
        "no_proxy": f"localhost,127.0.0.1,{host}",
        "NO_PROXY": f"localhost,127.0.0.1,{host}",
    }


def parse_proxy_from_pac(pac: str) -> tuple[str, int] | None:
    match = re.search(r"PROXY\s+([A-Za-z0-9_.:-]+):(\d+)", pac, re.IGNORECASE)
    if not match:
        return None
    return match.group(1), int(match.group(2))


def parse_route_info(raw: str) -> RouteInfo:
    tokens = raw.split()
    dev = token_after(tokens, "dev")
    src = token_after(tokens, "src")
    via = token_after(tokens, "via")
    return RouteInfo(raw=" ".join(tokens), dev=dev, src=src, via=via)


def token_after(tokens: list[str], key: str) -> str:
    try:
        index = tokens.index(key)
    except ValueError:
        return ""
    if index + 1 >= len(tokens):
        return ""
    return tokens[index + 1]


def route_info(host: str) -> RouteInfo | None:
    if shutil.which("ip") is None:
        return None
    result = subprocess.run(
        ["ip", "route", "get", host],
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        return None
    return parse_route_info(result.stdout)


def route_warning(host: str, route: RouteInfo | None) -> str | None:
    if host != DEFAULT_HOST or route is None:
        return None
    if route.src.startswith("192.168.49.") and not route.via:
        return None
    route_text = route.raw or "unknown route"
    return (
        f"warning: route to {host} does not look like the Wi-Fi Direct link: {route_text}\n"
        "connect to the Android DIRECT-* SSID first, for example:\n"
        "  python3 linux/pdalink_lite.py scan\n"
        "  python3 linux/pdalink_lite.py connect --ssid 'DIRECT-...' --password '...'"
    )


def split_nmcli_terse(line: str) -> list[str]:
    fields: list[str] = []
    current: list[str] = []
    escaped = False
    for char in line.rstrip("\n"):
        if escaped:
            current.append(char)
            escaped = False
            continue
        if char == "\\":
            escaped = True
            continue
        if char == ":":
            fields.append("".join(current))
            current = []
            continue
        current.append(char)
    if escaped:
        current.append("\\")
    fields.append("".join(current))
    return fields


def parse_nmcli_wifi_list(raw: str) -> list[WifiNetwork]:
    networks: list[WifiNetwork] = []
    for line in raw.splitlines():
        if not line.strip():
            continue
        fields = split_nmcli_terse(line)
        if len(fields) < 4:
            continue
        networks.append(WifiNetwork(
            ssid=fields[0],
            security=fields[1],
            signal=parse_signal(fields[2]),
            in_use=fields[3].strip() == "*",
        ))
    return networks


def parse_nmcli_active_connections(raw: str) -> list[NmActiveConnection]:
    connections: list[NmActiveConnection] = []
    for line in raw.splitlines():
        if not line.strip():
            continue
        fields = split_nmcli_terse(line)
        if len(fields) < 2:
            continue
        connections.append(NmActiveConnection(
            name=fields[0],
            type=fields[1],
            device=fields[2] if len(fields) > 2 else "",
        ))
    return connections


def parse_signal(value: str) -> int:
    try:
        return int(value)
    except ValueError:
        return -1


def choose_wifi_direct_network(networks: Iterable[WifiNetwork], pattern: str = "DIRECT-") -> WifiNetwork | None:
    needle = pattern.lower()
    candidates = [
        network for network in networks
        if network.ssid and needle in network.ssid.lower()
    ]
    if not candidates:
        return None
    return sorted(candidates, key=lambda item: (item.in_use, item.signal), reverse=True)[0]


def scan_wifi_networks(iface: str | None = None) -> list[WifiNetwork]:
    if shutil.which("nmcli") is None:
        raise RuntimeError("nmcli not found; install NetworkManager CLI or connect manually.")
    command = ["nmcli", "-t", "-f", "SSID,SECURITY,SIGNAL,IN-USE", "dev", "wifi", "list", "--rescan", "yes"]
    if iface:
        command.extend(["ifname", iface])
    result = subprocess.run(command, text=True, capture_output=True, check=False)
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip() or f"nmcli exited {result.returncode}"
        raise RuntimeError(detail)
    return parse_nmcli_wifi_list(result.stdout)


def active_nm_connections(iface: str | None = None) -> list[NmActiveConnection]:
    if shutil.which("nmcli") is None:
        return []
    result = run_command(
        ["nmcli", "-t", "-f", "NAME,TYPE,DEVICE", "connection", "show", "--active"],
        timeout=3.0,
    )
    if result.returncode != 0:
        return []
    connections = parse_nmcli_active_connections(result.stdout)
    if iface:
        connections = [connection for connection in connections if connection.device == iface]
    return connections


def saved_nm_connections() -> list[NmActiveConnection]:
    if shutil.which("nmcli") is None:
        return []
    result = run_command(
        ["nmcli", "-t", "-f", "NAME,TYPE", "connection", "show"],
        timeout=3.0,
    )
    if result.returncode != 0:
        return []
    return parse_nmcli_active_connections(result.stdout)


def nmcli_connection_ssid(connection_name: str) -> str:
    if shutil.which("nmcli") is None:
        return ""
    result = run_command(
        ["nmcli", "-g", "802-11-wireless.ssid", "connection", "show", connection_name],
        timeout=3.0,
    )
    if result.returncode != 0:
        return ""
    return result.stdout.strip()


def wifi_connection_profiles_for_ssid(ssid: str) -> list[NmActiveConnection]:
    saved = saved_nm_connections()
    profiles = [
        connection for connection in saved
        if connection.type == "802-11-wireless" and nmcli_connection_ssid(connection.name) == ssid
    ]
    if profiles:
        return profiles
    return [
        connection for connection in saved
        if connection.type == "802-11-wireless" and connection.name == ssid
    ]


def saved_nm_connection_named(name: str) -> NmActiveConnection | None:
    for connection in saved_nm_connections():
        if connection.name == name:
            return connection
    return None


def update_wifi_direct_profile_secret(ssid: str, password: str) -> bool:
    profiles = wifi_connection_profiles_for_ssid(ssid)
    if not profiles:
        return True
    ok = True
    for profile in profiles:
        result = run_command(
            ["nmcli", "connection", "modify", profile.name, "wifi-sec.key-mgmt", "wpa-psk", "wifi-sec.psk", password],
            timeout=5.0,
        )
        if result.returncode != 0:
            detail = (result.stderr or result.stdout).strip() or f"nmcli exited {result.returncode}"
            print(f"warning: could not update saved Wi-Fi Direct password for {profile.name!r}: {detail}", file=sys.stderr)
            ok = False
    return ok


def prepare_wifi_direct_profile(ssid: str, password: str | None, iface: str | None = None, *, autoconnect: bool = False) -> str | None:
    if shutil.which("nmcli") is None:
        return None
    settings = list(WIFI_DIRECT_PROFILE_SETTINGS)
    if autoconnect:
        settings[1] = "yes"
        settings[settings.index("ipv6.method") + 1] = "disabled"
        settings.extend(("connection.autoconnect-retries", "0",
                         "ipv6.never-default", "yes", "ipv6.ignore-auto-dns", "yes",
                         "wifi-sec.psk-flags", "0"))
    profiles = wifi_connection_profiles_for_ssid(ssid)
    profile_name = profiles[0].name if profiles else WIFI_DIRECT_PROFILE_NAME
    existing_named = saved_nm_connection_named(profile_name)

    if existing_named is None:
        if not password:
            return None
        command = [
            "nmcli", "connection", "add",
            "type", "wifi",
            "ifname", iface or "*",
            "con-name", profile_name,
            "ssid", ssid,
            "wifi-sec.key-mgmt", "wpa-psk",
            "wifi-sec.psk", password,
            *settings,
        ]
    else:
        command = [
            "nmcli", "connection", "modify", profile_name,
            "802-11-wireless.ssid", ssid,
            *settings,
        ]
        if password:
            command.extend(["wifi-sec.key-mgmt", "wpa-psk", "wifi-sec.psk", password])

    result = run_command(command, timeout=5.0)
    if result.returncode != 0:
        print("warning: could not prepare Wi-Fi Direct profile (check NetworkManager authorization)", file=sys.stderr)
        return None
    return profile_name


def prepare_local_hotspot_profile(
        credentials: LocalHotspotCredentials,
        profile_name: str = LOCAL_HOTSPOT_PROFILE_NAME,
        iface: str | None = None) -> str | None:
    if shutil.which("nmcli") is None:
        return None
    existing = saved_nm_connection_named(profile_name)
    key_management = "sae" if credentials.security == "WPA3-SAE" else "wpa-psk"
    if existing is None:
        command = [
            "nmcli", "connection", "add",
            "type", "wifi",
            "ifname", iface or "*",
            "con-name", profile_name,
            "ssid", credentials.ssid,
            "wifi-sec.key-mgmt", key_management,
            "wifi-sec.psk-flags", "1",
            *WIFI_DIRECT_PROFILE_SETTINGS,
        ]
    else:
        command = [
            "nmcli", "connection", "modify", profile_name,
            "802-11-wireless.ssid", credentials.ssid,
            "wifi-sec.key-mgmt", key_management,
            "wifi-sec.psk-flags", "1",
            "wifi-sec.psk", "",
            *WIFI_DIRECT_PROFILE_SETTINGS,
        ]
    result = run_command(command, timeout=5.0)
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip() or f"nmcli exited {result.returncode}"
        print(f"warning: could not prepare Local Hotspot profile {profile_name!r}: {detail}", file=sys.stderr)
        return None
    return profile_name


def activate_connection_with_secret(
        profile_name: str,
        password: str,
        iface: str | None = None,
        timeout: float = 30.0) -> CommandResult:
    """Activate a Wi-Fi profile without exposing its PSK in process arguments."""
    runtime_dir = os.getenv("XDG_RUNTIME_DIR")
    temp_dir = runtime_dir if runtime_dir and Path(runtime_dir).is_dir() else None
    fd, raw_path = tempfile.mkstemp(prefix="pdalink-nm-secret-", dir=temp_dir, text=True)
    secret_path = Path(raw_path)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as secret_file:
            secret_file.write("802-11-wireless-security.psk:" + password + "\n")
        command = ["nmcli", "connection", "up", profile_name]
        if iface:
            command.extend(["ifname", iface])
        command.extend(["passwd-file", str(secret_path)])
        return run_command(command, timeout=timeout)
    finally:
        try:
            secret_path.unlink(missing_ok=True)
        except OSError:
            pass


def local_hotspot_password_digest(credentials: LocalHotspotCredentials) -> str:
    return hashlib.sha256(credentials.password.encode("utf-8")).hexdigest()


def local_hotspot_credential_fingerprint(credentials: LocalHotspotCredentials) -> str:
    value = "\0".join((
        credentials.ssid,
        credentials.password,
        credentials.host,
        str(credentials.port),
        credentials.security,
    ))
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def local_hotspot_pin_mode(path: Path) -> int:
    return stat.S_IMODE(path.stat().st_mode)


def write_local_hotspot_pin(path: Path, payload: dict[str, object]) -> None:
    atomic_write_json(path, payload)
    if local_hotspot_pin_mode(path) == 0o600:
        return
    try:
        path.unlink(missing_ok=True)
    except OSError:
        pass
    raise ValueError(
        f"Local Hotspot pin filesystem cannot enforce mode 0600: {path}"
    )


def apply_local_hotspot_credentials(
        credentials: LocalHotspotCredentials,
        mode: str,
        profile_name: str,
        pin_path: Path,
        iface: str | None,
        reset_pin: bool = False) -> tuple[bool, str]:
    if mode not in ("auto", "pinned"):
        raise ValueError("Local Hotspot credential mode must be auto or pinned")

    digest = local_hotspot_password_digest(credentials)
    if mode == "pinned" and pin_path.exists() and not reset_pin:
        if local_hotspot_pin_mode(pin_path) != 0o600:
            raise ValueError(f"Local Hotspot pin must have mode 0600: {pin_path}")
        try:
            pin = json.loads(pin_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError(f"invalid Local Hotspot pin {pin_path}: {exc}") from exc
        if not isinstance(pin, dict):
            raise ValueError(f"invalid Local Hotspot pin {pin_path}: payload must be an object")
        if pin.get("ssid") != credentials.ssid or pin.get("password_sha256") != digest:
            return False, "credential-mismatch"

    prepared = prepare_local_hotspot_profile(credentials, profile_name, iface)
    if prepared is None:
        return False, "profile-update-failed"
    if mode == "auto":
        return True, "updated"

    if reset_pin or not pin_path.exists():
        write_local_hotspot_pin(pin_path, {
            "version": 1,
            "ssid": credentials.ssid,
            "password_sha256": digest,
            "profile": profile_name,
        })
        return True, "pinned"
    return True, "matched"


def active_wifi_direct_connection(ssid: str, iface: str | None = None) -> NmActiveConnection | None:
    wifi_connections = [
        connection for connection in active_nm_connections(iface)
        if connection.type == "802-11-wireless"
    ]
    for connection in wifi_connections:
        if nmcli_connection_ssid(connection.name) == ssid:
            return connection
    if len(wifi_connections) == 1:
        return wifi_connections[0]
    for connection in wifi_connections:
        if "direct" in connection.name.lower():
            return connection
    return None


def active_wifi_direct_network_from_nmcli(pattern: str = "DIRECT-", iface: str | None = None) -> WifiNetwork | None:
    needle = pattern.lower()
    for connection in active_nm_connections(iface):
        if connection.type != "802-11-wireless":
            continue
        ssid = nmcli_connection_ssid(connection.name) or connection.name
        if needle in ssid.lower():
            return WifiNetwork(ssid=ssid, security="", signal=-1, in_use=True)
    return None


def configure_wifi_direct_profile(ssid: str, iface: str | None = None) -> bool:
    connection = active_wifi_direct_connection(ssid, iface)
    connection_name = connection.name if connection else ssid
    result = run_command(
        ["nmcli", "connection", "modify", connection_name, *WIFI_DIRECT_PROFILE_SETTINGS],
        timeout=5.0,
    )
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip() or f"nmcli exited {result.returncode}"
        print(f"warning: could not mark Wi-Fi Direct profile {connection_name!r} local-only: {detail}", file=sys.stderr)
        return False
    print(f"Configured NetworkManager profile {connection_name!r} as local-only Wi-Fi Direct.")
    if connection and connection.device:
        reapply = run_command(["nmcli", "device", "reapply", connection.device], timeout=5.0)
        if reapply.returncode != 0:
            detail = (reapply.stderr or reapply.stdout).strip() or f"nmcli exited {reapply.returncode}"
            print(f"warning: could not reapply Wi-Fi Direct profile on {connection.device}: {detail}", file=sys.stderr)
    return True


def run_nmcli(args: list[str]) -> int:
    if shutil.which("nmcli") is None:
        print("nmcli not found; install NetworkManager CLI or connect manually.", file=sys.stderr)
        return 127
    return subprocess.call(["nmcli", *args])


def scan(_: argparse.Namespace) -> int:
    return run_nmcli(["-f", "SSID,SECURITY,SIGNAL,IN-USE", "dev", "wifi", "list", "--rescan", "yes"])


def connect(args: argparse.Namespace) -> int:
    profile_name = prepare_wifi_direct_profile(args.ssid, args.password, args.iface)
    if profile_name:
        command = ["connection", "up", profile_name]
        if args.iface:
            command.extend(["ifname", args.iface])
    else:
        if args.password:
            update_wifi_direct_profile_secret(args.ssid, args.password)
        command = ["dev", "wifi", "connect", args.ssid]
        if args.password:
            command.extend(["password", args.password])
        if args.iface:
            command.extend(["ifname", args.iface])
    status = run_nmcli(command)
    if status != 0:
        return status
    if not args.verify:
        return status
    return verify_after_connect(args.host, parse_ports(args.ports), args.verify_timeout)


def connect_auto(args: argparse.Namespace) -> int:
    try:
        networks = scan_wifi_networks(args.iface)
    except RuntimeError as exc:
        detail = str(exc)
        print(detail, file=sys.stderr)
        return 127 if detail.startswith("nmcli not found") else 1

    network = choose_wifi_direct_network(networks, args.ssid_pattern)
    if network is None:
        print(f"No Wi-Fi network matching {args.ssid_pattern!r} found.", file=sys.stderr)
        print("Run `python3 linux/pdalink_lite.py scan` and confirm the Android app shows an active group.", file=sys.stderr)
        return 1

    print(f"Selected SSID: {network.ssid} (signal {network.signal}, security {network.security or 'open'})")
    if network.security and network.security != "--" and not args.password:
        print("Selected network is secured; pass --password from the Android app.", file=sys.stderr)
        return 2

    connect_args = argparse.Namespace(
        ssid=network.ssid,
        password=args.password,
        iface=args.iface,
        verify=False,
        host=args.host,
        ports=args.ports,
        verify_timeout=args.verify_timeout,
    )
    status = connect(connect_args)
    if status != 0:
        return status
    return verify_after_connect(args.host, parse_ports(args.ports), args.verify_timeout)


def read_connection_uri(args: argparse.Namespace) -> str:
    sources = sum((bool(args.uri), bool(args.image), bool(args.camera)))
    if sources > 1:
        raise ValueError("choose only one of --uri, --image, or --camera")
    if args.uri:
        return args.uri
    if args.image or args.camera:
        scanner = Path(__file__).resolve().parents[1] / "tools" / "scan_connection_qr.py"
        command = [sys.executable, str(scanner)]
        if args.image:
            command.extend(("--image", args.image))
        else:
            command.extend(("--camera", str(args.camera_index)))
        command.extend(("--timeout", str(args.scan_timeout)))
        result = run_command(command, timeout=args.scan_timeout + 5.0)
        if result.returncode != 0:
            detail = (result.stderr or result.stdout).strip() or "QR scanner failed"
            raise RuntimeError(detail)
        return result.stdout.strip()
    if sys.stdin.isatty():
        raise ValueError("pass --uri, --image, --camera, or pipe a connection URI on stdin")
    return sys.stdin.read(4096).strip()


def connection_import(args: argparse.Namespace) -> int:
    try:
        bundle = parse_connection_uri(read_connection_uri(args))
    except (OSError, RuntimeError, ValueError) as exc:
        print(f"connection import failed: {exc}", file=sys.stderr)
        return 2

    print(f"transport: {bundle.transport}")
    print(f"network: {bundle.ssid}")
    print(f"security: {bundle.security}")
    print(f"proxy: {bundle.proxy_url}")
    if args.no_connect:
        return 0

    if bundle.transport == "local-hotspot":
        credentials = LocalHotspotCredentials(
            ssid=bundle.ssid,
            password=bundle.password,
            interface="",
            host=bundle.host,
            port=bundle.port,
            security="WPA3-SAE" if bundle.security == "wpa3-sae" else "WPA2-PSK",
        )
        profile_name = prepare_local_hotspot_profile(
            credentials,
            LOCAL_HOTSPOT_PROFILE_NAME,
            args.iface,
        )
    else:
        profile_name = prepare_wifi_direct_profile(bundle.ssid, bundle.password, args.iface, autoconnect=True)
    if not profile_name:
        print("connection import failed: NetworkManager profile could not be prepared", file=sys.stderr)
        return 1
    if bundle.transport == "local-hotspot":
        activation = activate_connection_with_secret(
            profile_name,
            bundle.password,
            args.iface,
            timeout=30.0,
        )
        if activation.returncode != 0:
            detail = (activation.stderr or activation.stdout).strip() \
                or f"nmcli exited {activation.returncode}"
            print(f"connection import failed: {detail}", file=sys.stderr)
            return activation.returncode or 1
    else:
        command = ["connection", "up", profile_name]
        if args.iface:
            command.extend(("ifname", args.iface))
        status = run_nmcli(command)
        if status != 0:
            return status
    status = verify_after_connect(bundle.host, (bundle.port,), args.verify_timeout)
    if status != 0 or not getattr(args, "internet", False):
        return status
    state_path = resolve_wifi_proxy_state_path(getattr(args, "state_file", ""))
    try:
        lock_fd = acquire_wifi_proxy_start_lock(state_path)
        try:
            pac_url = bundle.proxy_url + "/proxy.pac"
            if state_path.exists():
                state = load_wifi_proxy_state(state_path)
                if state.get("pac_url") == pac_url and gnome_proxy_owned_by_pdalink(pac_url):
                    return 0
                raise RuntimeError("Existing proxy restore state must be stopped before another import")
            enable_gnome_wifi_proxy(state_path=state_path, pac_url=pac_url, ssid=bundle.ssid,
                                    profile_name=profile_name, disconnect_on_stop=False)
        finally:
            os.close(lock_fd)
    except (RuntimeError, ValueError, SystemExit) as exc:
        print(f"Wi-Fi imported; desktop proxy setup failed: {exc}", file=sys.stderr)
        return 1
    print("Persistent GNOME PAC enabled; NetworkManager will rejoin this Direct network.")
    return 0


def wifi_status(args: argparse.Namespace) -> int:
    ports = parse_ports(args.ports)
    print("PdaLink Wi-Fi Direct status")
    print(WIFI_DIRECT_TRANSPORT_LINE)
    print(f"target: {args.host}:{','.join(str(port) for port in ports)}")

    tools = {tool.name: tool for tool in tool_statuses(("nmcli", "ip", "curl"))}
    for name in ("nmcli", "ip", "curl"):
        tool = tools[name]
        marker = "OK" if tool.ok else "MISSING"
        detail = tool.path if tool.path else "not found in PATH"
        print(f"tool {tool.name}: {marker} {detail}")

    active_direct = active_wifi_direct_network_from_nmcli(args.ssid_pattern, args.iface)
    active_networks: list[WifiNetwork] = []
    scan_error = ""
    if active_direct is None:
        try:
            networks = scan_wifi_networks(args.iface)
            active_networks = [network for network in networks if network.in_use]
            active_direct = active_wifi_direct_network(networks, args.ssid_pattern)
        except RuntimeError as exc:
            scan_error = str(exc)

    if active_direct is not None:
        print(f"wifi: OK {active_direct.ssid} signal {active_direct.signal}")
    elif scan_error:
        print(f"wifi: UNKNOWN {scan_error}")
    elif active_networks:
        names = ", ".join(network.ssid or "(hidden)" for network in active_networks)
        print(f"wifi: MISSING active network is not {args.ssid_pattern!r}: {names}")
    else:
        print(f"wifi: MISSING no active {args.ssid_pattern!r} network")

    route = route_info(args.host)
    warning = route_warning(args.host, route)
    if route is None:
        print(f"route: UNKNOWN {args.host}")
    elif warning:
        print(f"route: FAIL {route.raw}")
        print(warning)
    else:
        print(f"route: OK {route.raw}")

    results = probe_ports(args.host, ports)
    working = first_ok(results)
    for result in results:
        marker = "OK" if result.ok else "FAIL"
        print(f"proxy: {marker} http://{result.host}:{result.port} ({result.detail})")

    steps = wifi_direct_next_steps(
        nmcli_ok=tools["nmcli"].ok,
        active_direct=active_direct,
        route_warning_text=warning or "",
        working_proxy=working,
        host=args.host,
        ports=ports,
        ssid_pattern=args.ssid_pattern,
    )
    print_wifi_next_steps(steps)
    return 0 if working and warning is None else 1


def resolve_wifi_proxy_state_path(raw: str | None) -> Path:
    if raw:
        return Path(raw).expanduser()
    return pdalink_state_dir() / DEFAULT_WIFI_PROXY_STATE_FILE


def gsettings_string(value: str) -> str:
    return repr(value)


def parse_gsettings_string(raw: str) -> str:
    try:
        value = ast.literal_eval(raw)
    except (SyntaxError, ValueError):
        return raw
    return value if isinstance(value, str) else raw


SYSTEM_GSETTINGS_CANDIDATES = ("/usr/bin/gsettings", "/bin/gsettings")
# Variables that make a bundled gsettings (e.g. conda's) read a different schema
# set or write a private keyfile instead of the desktop's dconf database.
GSETTINGS_ENV_OVERRIDES = ("GSETTINGS_BACKEND", "GSETTINGS_SCHEMA_DIR", "GIO_MODULE_DIR", "GIO_EXTRA_MODULES")


def gsettings_binary() -> str | None:
    """Prefer the distro gsettings so changes reach the real GNOME (dconf) settings.

    A gsettings found first on PATH may come from conda or another bundled GLib
    without the dconf GIO module; it silently falls back to a keyfile backend.
    """
    for candidate in SYSTEM_GSETTINGS_CANDIDATES:
        if os.path.isfile(candidate) and os.access(candidate, os.X_OK):
            return candidate
    return shutil.which("gsettings")


def run_gsettings(args: list[str]) -> CommandResult:
    binary = gsettings_binary()
    if binary is None:
        return CommandResult(127, "", "gsettings not found")
    env = {key: value for key, value in os.environ.items() if key not in GSETTINGS_ENV_OVERRIDES}
    try:
        result = subprocess.run(
            [binary, *args], text=True, capture_output=True, check=False, timeout=5.0, env=env
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return CommandResult(1, "", str(exc))
    return CommandResult(result.returncode, result.stdout, result.stderr)


def gsettings_get(key: str) -> str:
    result = run_gsettings(["get", GNOME_PROXY_SCHEMA, key])
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip() or f"gsettings exited {result.returncode}"
        raise RuntimeError(f"cannot read GNOME proxy {key}: {detail}")
    return result.stdout.strip()


def gsettings_set(key: str, serialized_value: str) -> None:
    result = run_gsettings(["set", GNOME_PROXY_SCHEMA, key, serialized_value])
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip() or f"gsettings exited {result.returncode}"
        raise RuntimeError(f"cannot set GNOME proxy {key}: {detail}")


def gnome_proxy_snapshot() -> dict[str, str]:
    if gsettings_binary() is None:
        raise RuntimeError(
            "gsettings not found; use `./tools/pdalink.sh wifi env --probe` "
            "or configure the desktop proxy manually"
        )
    return {key: gsettings_get(key) for key in GNOME_PROXY_KEYS}


def restore_gnome_proxy(snapshot: dict[str, str]) -> None:
    # Leave PdaLink auto mode before replacing its PAC URL.
    for key in GNOME_PROXY_KEYS:
        value = snapshot.get(key)
        if not isinstance(value, str) or not value:
            raise RuntimeError(f"invalid GNOME proxy snapshot: missing {key}")
        gsettings_set(key, value)
    restored = {key: gsettings_get(key) for key in GNOME_PROXY_KEYS}
    if restored != snapshot:
        raise RuntimeError("GNOME proxy settings did not retain the saved restore snapshot")


def current_gnome_proxy() -> tuple[str, str]:
    return (
        parse_gsettings_string(gsettings_get("mode")),
        parse_gsettings_string(gsettings_get("autoconfig-url")),
    )


def gnome_proxy_owned_by_pdalink(pac_url: str) -> bool:
    mode, configured_url = current_gnome_proxy()
    return mode == "auto" and configured_url == pac_url


def load_wifi_proxy_state(path: Path) -> dict[str, object]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise ValueError(f"Wi-Fi Direct proxy state not found: {path}") from None
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"invalid Wi-Fi Direct proxy state {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise ValueError(f"invalid Wi-Fi Direct proxy state {path}: payload must be an object")
    if payload.get("version") != 1:
        raise ValueError(f"invalid Wi-Fi Direct proxy state {path}: unsupported version")
    if payload.get("transport") != WIFI_DIRECT_TRANSPORT_NAME or payload.get("manager") != "gsettings":
        raise ValueError(f"invalid Wi-Fi Direct proxy state {path}: unexpected owner")
    pac_url = payload.get("pac_url")
    if not isinstance(pac_url, str):
        raise ValueError(f"invalid Wi-Fi Direct proxy state {path}: missing PAC URL")
    try:
        parsed_pac = urlparse(pac_url)
        pac_port = parsed_pac.port
    except ValueError as exc:
        raise ValueError(f"invalid Wi-Fi Direct proxy state {path}: malformed PAC URL") from exc
    if (
        parsed_pac.scheme != "http"
        or not parsed_pac.hostname
        or pac_port is None
        or parsed_pac.path != "/proxy.pac"
        or parsed_pac.params
        or parsed_pac.query
        or parsed_pac.fragment
    ):
        raise ValueError(f"invalid Wi-Fi Direct proxy state {path}: unexpected PAC URL")
    before = payload.get("before")
    if not isinstance(before, dict) or any(not isinstance(before.get(key), str) for key in GNOME_PROXY_KEYS):
        raise ValueError(f"invalid Wi-Fi Direct proxy state {path}: missing GNOME proxy snapshot")
    if not isinstance(payload.get("profile_name"), str) or not isinstance(payload.get("disconnect_on_stop"), bool):
        raise ValueError(f"invalid Wi-Fi Direct proxy state {path}: missing connection ownership")
    return payload


def enable_gnome_wifi_proxy(
    *,
    state_path: Path,
    pac_url: str,
    ssid: str,
    profile_name: str,
    disconnect_on_stop: bool,
) -> None:
    if state_path.exists():
        raise RuntimeError(
            f"Wi-Fi Direct proxy state already exists: {state_path}. "
            "Run `./tools/pdalink.sh wifi internet stop` first."
        )
    before = gnome_proxy_snapshot()
    state = {
        "version": 1,
        "transport": WIFI_DIRECT_TRANSPORT_NAME,
        "manager": "gsettings",
        "created_at": utc_now(),
        "pac_url": pac_url,
        "ssid": ssid,
        "profile_name": profile_name,
        "disconnect_on_stop": disconnect_on_stop,
        "before": before,
    }
    create_private_json(state_path, state)
    try:
        gsettings_set("autoconfig-url", gsettings_string(pac_url))
        gsettings_set("mode", gsettings_string("auto"))
        if not gnome_proxy_owned_by_pdalink(pac_url):
            raise RuntimeError("GNOME proxy settings did not retain the PdaLink PAC URL")
    except Exception as apply_error:
        try:
            restore_gnome_proxy(before)
        except Exception as restore_error:
            raise RuntimeError(
                f"PdaLink proxy apply failed ({apply_error}); automatic restore also failed "
                f"({restore_error}). Restore state retained at {state_path}."
            ) from restore_error
        else:
            state_path.unlink(missing_ok=True)
        raise apply_error


def disconnect_wifi_profile(profile_name: str) -> bool:
    if not profile_name:
        return True
    active_names = {connection.name for connection in active_nm_connections()}
    if profile_name not in active_names:
        return True
    result = run_command(["nmcli", "connection", "down", profile_name], timeout=15.0)
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip() or f"nmcli exited {result.returncode}"
        print(f"warning: could not disconnect {profile_name!r}: {detail}", file=sys.stderr)
        return False
    print(f"Disconnected Wi-Fi Direct profile {profile_name!r}.")
    return True


def acquire_wifi_proxy_start_lock(state_path: Path) -> int:
    lock_path = state_path.with_name(state_path.name + ".lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        fd = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o600)
        os.fchmod(fd, 0o600)
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except (OSError, BlockingIOError) as exc:
        if "fd" in locals():
            os.close(fd)
        raise RuntimeError("another Wi-Fi Direct proxy start is already running") from exc
    return fd


def wifi_internet_start_locked(args: argparse.Namespace, state_path: Path) -> int:
    if state_path.exists():
        print(
            f"Wi-Fi Direct proxy state already exists: {state_path}. "
            "Run `./tools/pdalink.sh wifi internet stop` first.",
            file=sys.stderr,
        )
        return 2

    active_before = active_wifi_direct_network_from_nmcli(args.ssid_pattern, args.iface)
    if args.ssid:
        connection_args = argparse.Namespace(
            ssid=args.ssid,
            password=args.password,
            iface=args.iface,
            verify=True,
            host=args.host,
            ports=args.ports,
            verify_timeout=args.verify_timeout,
        )
        status = connect(connection_args)
        connected_ssid = args.ssid
    else:
        status = connect_auto(args)
        active_after = active_wifi_direct_network_from_nmcli(args.ssid_pattern, args.iface)
        connected_ssid = active_after.ssid if active_after else ""
    if status != 0:
        return status

    active_after = active_wifi_direct_network_from_nmcli(args.ssid_pattern, args.iface)
    if active_after is not None:
        connected_ssid = active_after.ssid
    if not connected_ssid:
        print("Connected Wi-Fi Direct SSID could not be identified.", file=sys.stderr)
        return 1

    connection = active_wifi_direct_connection(connected_ssid, args.iface)
    profile_name = connection.name if connection else WIFI_DIRECT_PROFILE_NAME
    disconnect_on_stop = active_before is None or active_before.ssid != connected_ssid

    try:
        port = first_verified_pdalink_port(args.host, parse_ports(args.ports))
        pac_url = f"http://{args.host}:{port}/proxy.pac"
        enable_gnome_wifi_proxy(
            state_path=state_path,
            pac_url=pac_url,
            ssid=connected_ssid,
            profile_name=profile_name,
            disconnect_on_stop=disconnect_on_stop,
        )
    except (RuntimeError, SystemExit) as exc:
        print(str(exc), file=sys.stderr)
        if disconnect_on_stop:
            disconnect_wifi_profile(profile_name)
        return 1

    print(f"PdaLink Wi-Fi Direct internet enabled with PAC {pac_url}")
    print(f"Restore state: {state_path}")
    return 0


def wifi_internet_start(args: argparse.Namespace) -> int:
    state_path = resolve_wifi_proxy_state_path(args.state_file)
    try:
        lock_fd = acquire_wifi_proxy_start_lock(state_path)
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    try:
        return wifi_internet_start_locked(args, state_path)
    finally:
        os.close(lock_fd)


def wifi_internet_proxy_status(args: argparse.Namespace) -> int:
    state_path = resolve_wifi_proxy_state_path(args.state_file)
    print("GNOME managed proxy:")
    if not state_path.exists():
        print(f"  state: inactive ({state_path})")
        return 1
    try:
        state = load_wifi_proxy_state(state_path)
        pac_url = str(state.get("pac_url") or "")
        owned = gnome_proxy_owned_by_pdalink(pac_url)
    except (RuntimeError, ValueError) as exc:
        print(f"  state: invalid ({exc})")
        return 1
    state_label = "active" if owned else "drifted"
    state_detail = ""
    if owned:
        parsed_pac = urlparse(pac_url)
        assert parsed_pac.hostname is not None and parsed_pac.port is not None
        pac_state, pac_detail = expected_pdalink_pac_status(parsed_pac.hostname, parsed_pac.port)
        if pac_state != "active":
            state_label = pac_state
            state_detail = f" ({pac_detail})"
    print(f"  state: {state_label}{state_detail}")
    print(f"  PAC: {pac_url}")
    print(f"  restore: {state_path}")
    return 0 if state_label == "active" else 2


def wifi_internet_status(args: argparse.Namespace) -> int:
    wifi_result = wifi_status(args)
    proxy_result = wifi_internet_proxy_status(args)
    return 0 if wifi_result == 0 and proxy_result == 0 else 1


def wifi_internet_stop(args: argparse.Namespace) -> int:
    state_path = resolve_wifi_proxy_state_path(args.state_file)
    try:
        state = load_wifi_proxy_state(state_path)
        pac_url = str(state.get("pac_url") or "")
        owned = gnome_proxy_owned_by_pdalink(pac_url)
        if not owned and not args.force:
            print(
                "GNOME proxy settings changed after PdaLink start; refusing to overwrite them. "
                "Use --force only to recover the saved snapshot.",
                file=sys.stderr,
            )
            return 2
        before = state["before"]
        assert isinstance(before, dict)
        restore_gnome_proxy({str(key): str(value) for key, value in before.items()})
    except (RuntimeError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 1

    try:
        state_path.unlink(missing_ok=True)
    except OSError as exc:
        print(
            f"GNOME proxy was restored, but restore state could not be removed: {exc}",
            file=sys.stderr,
        )
        return 1
    print("Restored previous GNOME proxy settings.")
    if args.keep_wifi or not bool(state.get("disconnect_on_stop")):
        return 0
    profile_name = str(state.get("profile_name") or WIFI_DIRECT_PROFILE_NAME)
    return 0 if disconnect_wifi_profile(profile_name) else 1


def wifi_internet_test(args: argparse.Namespace) -> int:
    if wifi_internet_status(args) != 0:
        return 1
    return curl_test(args)


def active_wifi_direct_network(networks: Iterable[WifiNetwork], pattern: str = "DIRECT-") -> WifiNetwork | None:
    return choose_wifi_direct_network(
        (network for network in networks if network.in_use),
        pattern,
    )


def wifi_direct_next_steps(
    *,
    nmcli_ok: bool,
    active_direct: WifiNetwork | None,
    route_warning_text: str,
    working_proxy: ProbeResult | None,
    host: str,
    ports: tuple[int, ...],
    ssid_pattern: str,
) -> list[str]:
    if working_proxy is not None and not route_warning_text:
        return [f"run ./tools/pdalink.sh wifi curl-test --url https://ifconfig.me through http://{working_proxy.host}:{working_proxy.port}"]
    if not nmcli_ok:
        return ["install NetworkManager CLI or connect to the Android DIRECT-* network manually"]
    if active_direct is None:
        return [
            "start Wi-Fi Direct in the Android app, copy the displayed password, then run "
            "./tools/pdalink.sh wifi connect --password '<password>'"
        ]
    if route_warning_text:
        return [f"reconnect to the Android {ssid_pattern} network; route to {host} is not on the P2P link"]
    if working_proxy is None:
        ports_text = ",".join(str(port) for port in ports)
        return [f"keep the Android app open in Wi-Fi Direct mode, then run ./tools/pdalink.sh wifi probe --ports {ports_text}"]
    return []


def print_wifi_next_steps(steps: list[str]) -> None:
    if not steps:
        return
    print("next:")
    for step in steps:
        print(f"  - {step}")


def verify_after_connect(host: str, ports: Iterable[int], timeout: float) -> int:
    print(f"Waiting for proxy at {host}...")
    deadline = time.monotonic() + timeout
    last_detail = "not probed"
    while time.monotonic() <= deadline:
        route = route_info(host)
        warning = route_warning(host, route)
        if warning is None:
            for result in probe_ports(host, ports):
                if result.ok:
                    print(f"OK {result.host}:{result.port} {result.detail}")
                    return 0
                last_detail = f"{result.host}:{result.port} {result.detail}"
        else:
            last_detail = warning.splitlines()[0]
        time.sleep(1.0)
    print(f"Proxy verification failed: {last_detail}", file=sys.stderr)
    warning = route_warning(host, route_info(host))
    if warning:
        print(warning, file=sys.stderr)
    return 1


def request_pac(host: str, port: int, timeout: float = 3.0) -> str:
    with socket.create_connection((host, port), timeout=timeout) as sock:
        sock.settimeout(timeout)
        request = (
            "GET /proxy.pac HTTP/1.1\r\n"
            f"Host: {host}:{port}\r\n"
            "Connection: close\r\n\r\n"
        ).encode("ascii")
        sock.sendall(request)
        chunks: list[bytes] = []
        size = 0
        while True:
            data = sock.recv(8192)
            if not data:
                break
            size += len(data)
            if size > 65536:
                raise OSError("PAC response too large")
            chunks.append(data)
    raw = b"".join(chunks).decode("utf-8", errors="replace")
    headers, separator, body = raw.partition("\r\n\r\n")
    status = headers.split("\r\n", 1)[0].split()
    if not separator or len(status) < 2 or status[0] not in ("HTTP/1.0", "HTTP/1.1") or status[1] != "200":
        raise OSError("PAC endpoint did not return HTTP 200")
    return body


def probe_one(host: str, port: int) -> ProbeResult:
    try:
        pac = request_pac(host, port)
    except OSError as exc:
        return ProbeResult(host, port, False, str(exc))
    parsed = parse_proxy_from_pac(pac)
    if parsed:
        return ProbeResult(host, port, True, f"PAC advertises {parsed[0]}:{parsed[1]}", pac)
    return ProbeResult(host, port, False, "connected, but PAC proxy line not found", pac)


def expected_pdalink_pac_status(host: str, port: int) -> tuple[str, str]:
    try:
        pac = request_pac(host, port)
    except OSError as exc:
        return "unreachable", str(exc)
    advertised = parse_proxy_from_pac(pac)
    if advertised is None:
        return "untrusted", "PAC proxy line not found"
    if advertised != (host, port):
        return "untrusted", f"PAC advertises {advertised[0]}:{advertised[1]}, expected {host}:{port}"
    return "active", f"PAC advertises {host}:{port}"


def first_verified_pdalink_port(host: str, ports: Iterable[int]) -> int:
    warning = route_warning(host, route_info(host))
    if warning:
        raise RuntimeError(warning)
    failures: list[str] = []
    for port in ports:
        state, detail = expected_pdalink_pac_status(host, port)
        if state == "active":
            return port
        failures.append(f"{port}: {state} ({detail})")
    raise RuntimeError("No trusted PdaLink proxy port found: " + "; ".join(failures))


def probe(args: argparse.Namespace) -> int:
    warning = route_warning(args.host, route_info(args.host))
    if warning:
        print(warning, file=sys.stderr)
    ok = False
    for result in probe_ports(args.host, parse_ports(args.ports)):
        marker = "OK" if result.ok else "FAIL"
        print(f"{marker} {result.host}:{result.port} {result.detail}")
        if args.show_pac and result.pac:
            print(result.pac.rstrip())
        ok = ok or result.ok
    return 0 if ok else 1


def env_cmd(args: argparse.Namespace) -> int:
    port = first_working_port(args.host, parse_ports(args.ports)) if args.probe else parse_ports(args.ports)[0]
    for key, value in build_proxy_env(args.host, port).items():
        print(f"export {key}={shell_quote(value)}")
    return 0


def curl_test(args: argparse.Namespace) -> int:
    port = first_working_port(args.host, parse_ports(args.ports))
    return run_curl_with_proxy(args.url, args.host, port)


def usb_start(args: argparse.Namespace) -> int:
    serial = getattr(args, "adb_serial", "")
    if args.start_android:
        status = adb_start_android_proxy(serial) if serial else adb_start_android_proxy()
        if status != 0:
            return status

    status = (adb_forward_tcp(args.local_port, args.remote_port, serial) if serial
              else adb_forward_tcp(args.local_port, args.remote_port))
    if status != 0:
        return status

    print(f"usb proxy: http://{USB_HOST}:{args.local_port} -> Android tcp:{args.remote_port}")
    if args.verify:
        return verify_after_connect(USB_HOST, (args.local_port,), args.verify_timeout)
    return 0


def usb_stop(args: argparse.Namespace) -> int:
    status = adb_remove_forward(args.local_port)
    if args.stop_android:
        android_status = adb_stop_android_proxy()
        if status == 0:
            status = android_status
    return status


def usb_status(args: argparse.Namespace) -> int:
    command = adb_command()
    print("PdaLink USB status")
    print(ADB_TRANSPORT_LINE)
    if command is None:
        print("adb: MISSING (not found)")
        print_usb_next_steps(usb_next_steps(False, [], False, None, args.local_port, args.remote_port))
        return 127

    devices = adb_run(["devices", "-l"], timeout=5.0)
    if devices.returncode == 127:
        print("adb: MISSING (not found)")
        detail = (devices.stderr or devices.stdout).strip()
        if detail:
            print("adb check:")
            print(detail)
        print_usb_next_steps(usb_next_steps(False, [], False, None, args.local_port, args.remote_port))
        return 127

    print(f"adb: OK {' '.join(command)}")
    parsed_devices = parse_adb_devices(devices.stdout)
    print_usb_devices(parsed_devices, devices.stdout or devices.stderr)
    print_usb_permission_diagnostics(parsed_devices)

    forwards = adb_run(["forward", "--list"], timeout=5.0)
    parsed = parse_adb_forward_list(forwards.stdout)
    active_forwards = [
        item for item in parsed
        if item.local_port == args.local_port and item.remote_port == args.remote_port
    ]
    active = bool(active_forwards)
    print_usb_forward_status(args.local_port, args.remote_port, active, forwards.stdout or forwards.stderr)

    probe_result = first_ok_or_first(probe_ports(USB_HOST, (args.local_port,)))
    if probe_result is None:
        print("proxy: FAIL not probed")
    else:
        marker = "OK" if probe_result.ok else "FAIL"
        print(f"proxy: {marker} http://{probe_result.host}:{probe_result.port} ({probe_result.detail})")

    print_usb_next_steps(usb_next_steps(
        True,
        parsed_devices,
        active,
        probe_result,
        args.local_port,
        args.remote_port,
    ))
    return 0 if has_authorized_adb_device(parsed_devices) and active and probe_result and probe_result.ok else 1


def print_usb_devices(devices: list[AdbDevice], raw: str) -> None:
    if devices:
        ready = [device for device in devices if device.ok]
        if ready:
            print("device: OK " + ", ".join(device.serial for device in ready))
        else:
            states = ", ".join(f"{device.serial}:{device.state}" for device in devices)
            print(f"device: FAIL no authorized device ({states})")
    else:
        print("device: MISSING no adb device listed")
    print("adb devices:")
    print(raw.strip() or "(no adb output)")


def print_usb_permission_diagnostics(devices: list[AdbDevice]) -> None:
    if not any(device.state == "no permissions" for device in devices):
        return
    ids = observed_managed_usb_ids()
    if ids:
        print("observed-usb: " + ", ".join(ids))
    else:
        print("observed-usb: unavailable (install lsusb to show current managed USB IDs)")


def observed_managed_usb_ids() -> list[str]:
    if shutil.which("lsusb") is None:
        return []
    result = run_command(["lsusb"], timeout=2.0)
    if result.returncode != 0:
        return []
    return parse_lsusb_ids(result.stdout)


def print_usb_forward_status(local_port: int, remote_port: int, active: bool, raw: str) -> None:
    marker = "OK" if active else "MISSING"
    print(f"forward: {marker} tcp:{local_port} -> tcp:{remote_port}")
    print("adb forwards:")
    print(raw.strip() or "(none)")


def print_usb_next_steps(steps: list[str]) -> None:
    if not steps:
        return
    print("next:")
    for step in steps:
        print(f"  - {step}")


def usb_next_steps(
    adb_available: bool,
    devices: list[AdbDevice],
    forward_active: bool,
    probe_result: ProbeResult | None,
    local_port: int,
    remote_port: int,
) -> list[str]:
    if not adb_available:
        return ["install Android platform tools or use the project-local tools/adb.sh helper"]
    if not devices:
        return ["connect the phone over USB, enable USB debugging, then run ./tools/adb.sh devices -l"]
    if not has_authorized_adb_device(devices):
        unauthorized = [device.serial for device in devices if device.state == "unauthorized"]
        if unauthorized:
            return ["unlock the phone and approve the USB debugging RSA prompt"]
        no_permissions = [device.serial for device in devices if device.state == "no permissions"]
        if no_permissions:
            return [
                "refresh Linux udev rules with ./tools/pdalink.sh rescue udev "
                f"(managed IDs: {', '.join(MANAGED_UDEV_USB_IDS)}), then reconnect the phone if ADB still reports no permissions"
            ]
        return ["check adb device state, then rerun ./tools/adb.sh devices -l"]
    if not forward_active:
        return [f"run python3 linux/pdalink_lite.py usb-start --verify --local-port {local_port} --remote-port {remote_port}"]
    if probe_result is None or not probe_result.ok:
        return ["start the Android proxy, then rerun python3 linux/pdalink_lite.py usb-start --verify"]
    return [f"run curl through http://{USB_HOST}:{local_port} or run python3 linux/pdalink_lite.py usb-smoke --no-start-android"]


def connection_runtime_state(args: argparse.Namespace) -> SystemRuntimeState | None:
    explicit = getattr(args, "state_file", "") or os.getenv("PDALINK_SYSTEM_STATE_FILE") or os.getenv("PDALINK_SYSTEM_STATE")
    runtime = Path(os.getenv("PDALINK_RUNTIME_DIR", str(Path(__file__).resolve().parents[1] / ".tools/runtime")))
    paths = [Path(explicit)] if explicit else [runtime / "pdalink-system.json", system_state_path("")]
    for path in paths:
        try:
            state = load_system_state(path)
        except ValueError:
            continue
        if state is not None:
            return state
    return None


def remote_connection_status(args: argparse.Namespace, state: SystemRuntimeState) -> int:
    config = state.config
    host, port = proxy_host_port(config.proxy_url)
    upstream_ok = False
    try:
        upstream = proxy_upstream_device(config)
        upstream_ok = bool(config.upstream_device and upstream == config.upstream_device)
    except (OSError, ValueError):
        pass
    pac_state, pac_detail = expected_pdalink_pac_status(host, port)
    route = route_info(args.route_target)
    internet = run_connection_probe(args.curl_url, args.curl_timeout)
    checks = {
        "device": {"ok": upstream_ok and process_matches_state(state),
                   "detail": f"upstream {config.upstream_device or 'UNBOUND'}; state-tracked adapter"},
        "forward": {"ok": True, "detail": "not required for remote proxy"},
        "proxy": {"ok": pac_state == "active", "url": config.proxy_url, "detail": pac_detail},
        "route": {"ok": bool(route and route.dev == config.iface), "target": args.route_target,
                  "dev": route.dev if route else "", "detail": route.raw if route else "route unavailable"},
        "internet": {"ok": internet.returncode == 0, "url": args.curl_url,
                     "detail": (internet.stderr or internet.stdout).strip()},
    }
    base_ok = checks["device"]["ok"] and checks["proxy"]["ok"]
    overall = ("connected" if base_ok and checks["route"]["ok"] and checks["internet"]["ok"]
               else "degraded" if base_ok else "disconnected")
    payload = {"overall": overall, "transport": {
        "name": "wifi-direct" if host == DEFAULT_HOST else "remote-proxy",
        "role": "internet-data"}, "checks": checks}
    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        print_connection_status(payload)
    return 0 if overall == "connected" else 1


def connection_status(args: argparse.Namespace) -> int:
    state = connection_runtime_state(args)
    if state is not None and not proxy_uses_loopback(state.config.proxy_url):
        return remote_connection_status(args, state)
    checks: dict[str, dict[str, object]] = {}
    command = adb_command()
    if command is None:
        checks["device"] = {"ok": False, "state": "missing-adb", "detail": "adb not found"}
        devices: list[AdbDevice] = []
    else:
        result = adb_run(["devices", "-l"], timeout=5.0)
        devices = parse_adb_devices(result.stdout)
        ready = [device for device in devices if device.ok]
        checks["device"] = {
            "ok": bool(ready),
            "state": "authorized" if ready else "missing-authorized-device",
            "serials": [device.serial for device in ready],
            "detail": (result.stderr or result.stdout).strip(),
        }

    if command is None:
        checks["forward"] = {"ok": False, "detail": "adb not found"}
    else:
        forwards = adb_run(["forward", "--list"], timeout=5.0)
        parsed_forwards = parse_adb_forward_list(forwards.stdout)
        active_forwards = [
            item for item in parsed_forwards
            if item.local_port == args.local_port and item.remote_port == args.remote_port
        ]
        checks["forward"] = {
            "ok": bool(active_forwards),
            "local_port": args.local_port,
            "remote_port": args.remote_port,
            "detail": (forwards.stderr or forwards.stdout).strip(),
        }

    probe_result = first_ok_or_first(probe_ports(USB_HOST, (args.local_port,)))
    checks["proxy"] = {
        "ok": bool(probe_result and probe_result.ok),
        "url": f"http://{USB_HOST}:{args.local_port}",
        "detail": probe_result.detail if probe_result else "not probed",
    }

    route = route_info(args.route_target)
    checks["route"] = {
        "ok": bool(route and route.dev == DEFAULT_SYSTEM_IFACE),
        "target": args.route_target,
        "dev": route.dev if route else "",
        "detail": route.raw if route else "route unavailable",
    }

    internet = run_connection_probe(args.curl_url, args.curl_timeout)
    checks["internet"] = {
        "ok": internet.returncode == 0,
        "url": args.curl_url,
        "detail": (internet.stderr or internet.stdout).strip(),
    }

    base_ok = bool(checks["device"]["ok"] and checks["forward"]["ok"] and checks["proxy"]["ok"])
    if base_ok and checks["route"]["ok"] and checks["internet"]["ok"]:
        overall = "connected"
    elif base_ok:
        overall = "degraded"
    else:
        overall = "disconnected"

    payload = {
        "overall": overall,
        "transport": {"name": ADB_TRANSPORT_NAME, "role": ADB_TRANSPORT_ROLE},
        "checks": checks,
    }
    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        print_connection_status(payload)
    return 0 if overall == "connected" else 1


def run_connection_probe(url: str, timeout: float) -> CommandResult:
    return run_command(
        ["curl", "-4", "--noproxy", "*", "--max-time", str(timeout), "-sS", url],
        timeout=timeout + 1.0,
    )


def print_connection_status(payload: dict[str, object]) -> None:
    print("PdaLink connection status")
    print(f"overall: {payload['overall']}")
    print(f"transport: {payload['transport']['name']}")
    checks = payload["checks"]
    assert isinstance(checks, dict)
    for name in ("device", "forward", "proxy", "route", "internet"):
        check = checks[name]
        assert isinstance(check, dict)
        marker = "OK" if check.get("ok") else "FAIL"
        detail = str(check.get("detail") or "")
        print(f"{name}: {marker} {detail}".rstrip())


def usb_smoke(args: argparse.Namespace) -> int:
    start_args = argparse.Namespace(
        local_port=args.local_port,
        remote_port=args.remote_port,
        start_android=args.start_android,
        verify=True,
        verify_timeout=args.verify_timeout,
    )
    status = usb_start(start_args)
    if status != 0:
        return status
    return run_curl_with_proxy_retry(
        args.url,
        USB_HOST,
        args.local_port,
        attempts=max(1, args.curl_attempts),
        delay=max(0.0, args.curl_delay),
    )


def system_status(args: argparse.Namespace) -> int:
    try:
        config = system_config_from_args(args)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2

    print("PdaLink system routing status")
    print(f"iface: {config.iface}")
    print(f"address: {config.address}")
    print(f"route-mode: {config.route_mode}")
    print(f"routes: {system_route_summary(config)}")
    print(f"proxy: {config.proxy_url}")
    print(f"dns-mode: {config.dns_mode}")
    if config.dns_mode == "resolved" and config.route_mode == "active_override":
        print(f"dns-servers: {', '.join(config.dns_servers)}")

    ip_path = shutil.which("ip") or ""
    print_tool_line("ip", ip_path)

    tun_exists = TUN_DEVICE.exists()
    tun_access = tun_exists and os.access(TUN_DEVICE, os.R_OK | os.W_OK)
    if tun_access:
        print(f"tun: OK {TUN_DEVICE}")
    elif tun_exists:
        print(f"tun: FAIL {TUN_DEVICE} exists but is not readable/writable")
    else:
        print(f"tun: MISSING {TUN_DEVICE}")

    adapter_ok = print_adapter_status(config)
    dns_ok = print_dns_status(config)
    proxy_ok = True
    if args.probe_proxy:
        proxy_ok = print_system_proxy_probe(config.proxy_url)

    print_system_runtime_state(system_state_path(getattr(args, "state_file", "")))
    print_system_next_steps(system_next_steps(ip_path, tun_access, adapter_ok, dns_ok, proxy_ok, config))
    return 0 if ip_path and tun_access and adapter_ok and dns_ok and proxy_ok else 1


def product_usb_ready_for_route_change(args: argparse.Namespace) -> bool:
    if not getattr(args, "require_product_usb", False):
        return True
    state_path = resolve_product_usb_state_path(getattr(args, "product_usb_state_file", ""))
    try:
        status = product_usb_status_from_state(load_product_usb_state(state_path))
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return False
    if status.ready:
        return True
    print(f"refusing to change routes until product USB is ready: {status.readiness}", file=sys.stderr)
    for step in status.next_steps:
        print(f"next: {step}", file=sys.stderr)
    return False


def system_plan(args: argparse.Namespace) -> int:
    try:
        config = system_config_from_args(args)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2

    print("PdaLink system routing plan")
    print("mode: dry-run; no network route is changed by this command")
    print(f"proxy: {config.proxy_url}")
    print(f"iface: {config.iface} {config.address} mtu {config.mtu}")
    print(f"route-mode: {config.route_mode}")
    print(f"routes: {system_route_summary(config)}")
    print(f"dns-mode: {config.dns_mode}")
    print()
    print("1. Keep the Android HTTP proxy reachable:")
    proxy_host, proxy_port = proxy_host_port(config.proxy_url)
    if proxy_uses_loopback(config.proxy_url):
        print(f"  ./tools/pdalink.sh usb start --no-start-android --local-port {proxy_port} --verify")
    else:
        print(f"  python3 linux/pdalink_lite.py probe --host {proxy_host} --ports {proxy_port}")
        print("  # Requires a physical IPv4 route; Direct requires on-link Wi-Fi 192.168.49.0/24.")
        print("  # TCP and DNS upstream sockets use SO_BINDTODEVICE; link loss fails closed.")
        print("  # External adapters must provide equivalent upstream loop protection.")
    print()
    print("2. Start the built-in internet adapter as root:")
    print_command(default_system_start_command(config))
    print("  # stop with Ctrl-C; cleanup removes PdaLink routes")
    print()
    print("Advanced external-adapter path:")
    print("A. Start a packet adapter that reads/writes the TUN interface and dials through the proxy:")
    if config.adapter_command:
        print_command(config.adapter_command)
    else:
        placeholder = [
            "<tun-to-proxy-adapter>",
            "--tun",
            config.iface,
            "--proxy",
            config.proxy_url,
        ]
        print_command(placeholder)
        print("  # pass the real command with --adapter-command '... {iface} ... {proxy} ...'")
    print()
    print("B. If the external adapter does not create the TUN interface itself, create it as root:")
    print_commands(build_tun_setup_commands(config))
    print()
    print("C. After the external adapter is running, move IPv4 system traffic to the TUN interface:")
    print_commands(build_tun_route_commands(config))
    dns_commands = build_dns_setup_commands(config)
    if dns_commands:
        print()
        print("C2. Apply per-link DNS for active override mode:")
        print_commands(dns_commands)
    print()
    print("D. Roll back routes and the interface:")
    print_commands(build_tun_teardown_commands(config))
    return 0


def proxy_uses_loopback(proxy_url: str) -> bool:
    host, _ = proxy_host_port(proxy_url)
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def proxy_upstream_device(config: SystemRouteConfig) -> str:
    host, _ = proxy_host_port(config.proxy_url)
    if proxy_uses_loopback(config.proxy_url):
        return ""
    address = ipaddress.ip_address(host)
    if address.version != 4:
        raise ValueError("non-loopback upstream requires a literal IPv4 proxy")
    route = route_info(host)
    if not route or not route.dev or route.dev == config.iface or route.dev == "lo":
        raise ValueError("proxy requires a physical upstream route before TUN startup")
    if host == DEFAULT_HOST and (route_warning(host, route) or
                                not (Path("/sys/class/net") / route.dev / "wireless").exists()):
        raise ValueError("Direct proxy requires an on-link Wi-Fi route on 192.168.49.0/24")
    return route.dev


def system_start(args: argparse.Namespace) -> int:
    try:
        config = system_config_from_args(args)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    if not product_usb_ready_for_route_change(args):
        return 1
    if os.geteuid() != 0:
        print("system-start requires root because it opens TUN and changes routes; rerun with sudo", file=sys.stderr)
        return 77
    if args.no_dns:
        config = replace(config, dns_mode="off")

    if args.start_usb and proxy_host_port(config.proxy_url)[0] not in ("127.0.0.1", "localhost"):
        print("--start-usb requires a loopback proxy", file=sys.stderr)
        return 2

    if args.start_usb:
        status = usb_start(argparse.Namespace(
            adb_serial=getattr(args, "adb_serial", ""),
            local_port=proxy_url_port_or_default(config.proxy_url),
            remote_port=args.remote_port,
            start_android=args.start_android,
            verify=True,
            verify_timeout=args.verify_timeout,
        ))
        if status != 0:
            return status

    if args.probe_proxy and not print_system_proxy_probe(config.proxy_url):
        print("refusing to change routes until the Android proxy is reachable", file=sys.stderr)
        return 1

    conflicts = existing_tun_route_conflicts(config)
    if conflicts:
        print_route_conflicts(config, conflicts)
        return 2

    try:
        from pdalink_tun import TunHttpAdapter

        config = replace(config, upstream_device=proxy_upstream_device(config))
        adapter = TunHttpAdapter.open(
            config.iface,
            config.proxy_url,
            mtu=config.mtu,
            dns=not args.no_dns,
            connect_timeout=args.connect_timeout,
            tcp_idle_timeout=getattr(args, "tcp_idle_timeout", 0.0),
            verbose=args.verbose,
        )
    except (OSError, ValueError) as exc:
        print(f"failed to start built-in adapter: {exc}", file=sys.stderr)
        return 1

    if config.upstream_device:
        adapter.proxy.upstream_device = config.upstream_device

    dns_setup_commands = build_dns_setup_commands(config)
    if should_manage_dns(config) and not dns_setup_commands:
        print("dns: systemd-resolved/resolvectl unavailable; refusing to change routes; pass --dns-mode off to opt out", file=sys.stderr)
        adapter.close()
        return 1

    setup_commands = [
        *build_adapter_owned_tun_setup_commands(config),
        *build_tun_route_commands(config),
        *dns_setup_commands,
    ]
    status = run_privileged_network_commands(setup_commands)
    if status != 0:
        run_network_commands_best_effort(build_adapter_owned_tun_teardown_commands(config))
        adapter.close()
        return status

    state_path = system_state_path(getattr(args, "state_file", ""))
    try:
        write_system_state(state_path, config)
    except OSError as exc:
        print(f"failed to write system state {state_path}: {exc}", file=sys.stderr)
        run_network_commands_best_effort(build_adapter_owned_tun_teardown_commands(config))
        adapter.close()
        return 1
    if getattr(args, "tailscale_compat", False):
        restart_active_tailscale_after_route_override(config)
    print(f"system internet: {config.iface} -> {config.proxy_url}")
    print(f"upstream-device: {config.upstream_device or 'loopback'}")
    print(f"state: {state_path}")
    print("press Ctrl-C to stop and restore routes")
    cleanup_ok = True
    try:
        adapter.run(close_on_exit=False)
    except KeyboardInterrupt:
        print("stopping system internet adapter")
    finally:
        cleanup_ok = run_network_commands_best_effort(build_adapter_owned_tun_teardown_commands(config))
        adapter.close()
        if cleanup_ok:
            remove_system_state(state_path)
        else:
            print(f"cleanup incomplete; preserving state for retry: {state_path}", file=sys.stderr)
    return 0 if cleanup_ok else 1


def system_adapter(args: argparse.Namespace) -> int:
    try:
        config = system_config_from_args(args)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    try:
        from pdalink_tun import TunHttpAdapter

        config = replace(config, upstream_device=proxy_upstream_device(config))
        adapter = TunHttpAdapter.open(
            config.iface,
            config.proxy_url,
            mtu=config.mtu,
            dns=not args.no_dns,
            connect_timeout=args.connect_timeout,
            tcp_idle_timeout=getattr(args, "tcp_idle_timeout", 0.0),
            verbose=args.verbose,
        )
    except (OSError, ValueError) as exc:
        print(f"failed to start built-in adapter: {exc}", file=sys.stderr)
        return 1
    if config.upstream_device:
        adapter.proxy.upstream_device = config.upstream_device
    print(f"adapter: running on {adapter.iface}, proxy {config.proxy_url}")
    try:
        adapter.run()
    except KeyboardInterrupt:
        print("stopping adapter")
    return 0


def system_up(args: argparse.Namespace) -> int:
    try:
        config = system_config_from_args(args)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2

    dns_setup_commands = build_dns_setup_commands(config)
    commands = [*build_tun_setup_commands(config), *build_tun_route_commands(config), *dns_setup_commands]
    if not args.apply:
        print("dry-run: pass --apply --adapter-ready to run these privileged route commands")
        print_commands(commands)
        return 0
    if not args.adapter_ready:
        print("refusing to change system routes before the packet adapter is confirmed running", file=sys.stderr)
        print("start the adapter first, then rerun with --apply --adapter-ready", file=sys.stderr)
        return 2
    if not product_usb_ready_for_route_change(args):
        return 1
    if should_manage_dns(config) and not dns_setup_commands:
        print("dns: systemd-resolved/resolvectl unavailable; refusing to change routes; pass --dns-mode off to opt out", file=sys.stderr)
        return 1
    conflicts = existing_tun_route_conflicts(config)
    if conflicts:
        print_route_conflicts(config, conflicts)
        return 2
    return run_privileged_network_commands(commands)


def system_down(args: argparse.Namespace) -> int:
    try:
        config = system_config_from_args(args)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2

    commands = build_tun_teardown_commands(config)
    if not args.apply:
        print("dry-run: pass --apply to remove these routes and the TUN interface")
        print_commands(commands)
        return 0
    if os.geteuid() != 0:
        print("system route changes require root; rerun with sudo", file=sys.stderr)
        return 77
    cleanup_ok = run_network_commands_best_effort(commands)
    if cleanup_ok and getattr(args, "state_file", ""):
        remove_system_state_if_matching(system_state_path(args.state_file), config)
    return 0 if cleanup_ok else 1


def system_stop(args: argparse.Namespace) -> int:
    if os.geteuid() != 0:
        print("system-stop requires root because it removes TUN routes; rerun with sudo", file=sys.stderr)
        return 77

    path = system_state_path(getattr(args, "state_file", ""))
    try:
        state = load_system_state(path)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    if state is None:
        try:
            config = system_config_from_args(args)
        except ValueError as exc:
            print(str(exc), file=sys.stderr)
            return 2
        print(f"state: MISSING {path}; cleaning routes from arguments")
    else:
        config = state.config
        print(f"state: OK {path}")
        print(f"pid: {state.pid}")
        if state.pid != os.getpid() and process_exists(state.pid):
            if process_matches_state(state):
                process_stopped = stop_process(state.pid, args.stop_timeout)
                if not path.exists():
                    print("state removed by stopped process")
                    return 0
                if not process_stopped:
                    print(f"process still running; preserving state for retry: {path}", file=sys.stderr)
                    return 1
            else:
                print(f"process: pid {state.pid} no longer matches saved state; not signaling")
        else:
            print("process: not running")

    cleanup_ok = run_network_commands_best_effort(build_tun_teardown_commands(config))
    if not cleanup_ok:
        print(f"cleanup incomplete; preserving state for retry: {path}", file=sys.stderr)
        return 1
    remove_system_state(path)
    print(f"system internet stopped for {config.iface}")
    return 0


def system_config_from_args(args: argparse.Namespace) -> SystemRouteConfig:
    routes = parse_tun_routes(args.routes)
    proxy_host_port(args.proxy_url)
    adapter_command = parse_adapter_command(getattr(args, "adapter_command", ""), args.iface, args.proxy_url, args.mtu)
    route_mode = validate_route_mode(getattr(args, "route_mode", DEFAULT_ROUTE_MODE))
    route_metric = validate_route_metric(int(getattr(args, "route_metric", DEFAULT_ROUTE_METRIC)))
    dns_mode = validate_dns_mode(getattr(args, "dns_mode", DEFAULT_DNS_MODE))
    dns_servers = parse_dns_servers(getattr(args, "dns_servers", ",".join(DEFAULT_DNS_SERVERS)))
    return SystemRouteConfig(
        iface=args.iface,
        address=validate_cidr(args.address),
        mtu=validate_mtu(args.mtu),
        routes=routes,
        proxy_url=args.proxy_url,
        adapter_command=adapter_command,
        route_mode=route_mode,
        route_metric=route_metric,
        dns_mode=dns_mode,
        dns_servers=dns_servers,
    )


def parse_tun_routes(raw: str | None) -> tuple[str, ...]:
    if not raw:
        return DEFAULT_SYSTEM_ROUTES
    routes: list[str] = []
    for item in raw.split(","):
        route = item.strip()
        if not route:
            continue
        validate_cidr(route)
        routes.append(route)
    if not routes:
        raise ValueError("at least one route is required")
    return tuple(routes)


def validate_cidr(value: str) -> str:
    try:
        ipaddress.ip_network(value, strict=False)
    except ValueError as exc:
        raise ValueError(f"invalid CIDR route/address: {value}") from exc
    return value


def validate_mtu(value: int) -> int:
    if value < 576 or value > 9000:
        raise ValueError(f"invalid MTU: {value}")
    return value


def validate_route_mode(value: str) -> str:
    if value not in ("active_override", "fallback_low_priority"):
        raise ValueError("route mode must be active_override or fallback_low_priority")
    return value


def validate_route_metric(value: int) -> int:
    if value < 0 or value > 2_147_483_647:
        raise ValueError("route metric must be between 0 and 2147483647")
    return value


def validate_dns_mode(value: str) -> str:
    if value not in ("resolved", "off"):
        raise ValueError("DNS mode must be resolved or off")
    return value


def parse_dns_servers(raw: str | None) -> tuple[str, ...]:
    if raw is None or not raw.strip():
        return DEFAULT_DNS_SERVERS
    servers: list[str] = []
    for item in raw.split(","):
        server = item.strip()
        if not server:
            continue
        try:
            ipaddress.ip_address(server)
        except ValueError as exc:
            raise ValueError(f"invalid DNS server address: {server}") from exc
        servers.append(server)
    if not servers:
        raise ValueError("at least one DNS server is required")
    return tuple(servers)


def parse_dns_servers_payload(raw: object) -> tuple[str, ...]:
    if isinstance(raw, str) or raw is None:
        return parse_dns_servers(raw)
    if isinstance(raw, (list, tuple)):
        return parse_dns_servers(",".join(str(item) for item in raw))
    raise ValueError("DNS servers must be a comma-separated string or list")


def parse_adapter_command(raw: str | None, iface: str, proxy_url: str, mtu: int) -> tuple[str, ...]:
    if not raw:
        return ()
    try:
        expanded = raw.format(iface=iface, proxy=proxy_url, mtu=mtu)
    except (IndexError, KeyError, ValueError) as exc:
        raise ValueError(f"invalid adapter command template: {exc}") from exc
    try:
        parts = shlex.split(expanded)
    except ValueError as exc:
        raise ValueError(f"invalid adapter command: {exc}") from exc
    if not parts:
        raise ValueError("adapter command cannot be empty")
    return tuple(parts)


def proxy_host_port(proxy_url: str) -> tuple[str, int]:
    parsed = urlparse(proxy_url)
    if parsed.scheme != "http" or not parsed.hostname or parsed.port is None:
        raise ValueError("proxy URL must look like http://HOST:PORT")
    return parsed.hostname, parsed.port


def proxy_url_port_or_default(proxy_url: str) -> int:
    return proxy_host_port(proxy_url)[1]


def system_state_path(raw: str | None) -> Path:
    return Path(raw or DEFAULT_SYSTEM_STATE_FILE)


def write_system_state(path: Path, config: SystemRouteConfig) -> SystemRuntimeState:
    state = SystemRuntimeState(
        pid=os.getpid(),
        config=config,
        started_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    )
    payload = {
        "version": 1,
        "pid": state.pid,
        "started_at": state.started_at,
        "pid_start_ticks": process_start_ticks(state.pid),
        "cmdline": process_cmdline(state.pid),
        "iface": config.iface,
        "address": config.address,
        "mtu": config.mtu,
        "routes": list(config.routes),
        "proxy_url": config.proxy_url,
        "upstream_device": config.upstream_device,
        "route_mode": config.route_mode,
        "route_metric": config.route_metric,
        "dns_mode": config.dns_mode,
        "dns_servers": list(config.dns_servers),
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    tmp.replace(path)
    return state


def load_system_state(path: Path) -> SystemRuntimeState | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"invalid system state file {path}: {exc}") from exc
    try:
        if not isinstance(payload, dict):
            raise TypeError("state payload must be a JSON object")
        config = SystemRouteConfig(
            iface=str(payload["iface"]),
            address=validate_cidr(str(payload["address"])),
            mtu=validate_mtu(int(payload["mtu"])),
            routes=tuple(validate_cidr(str(route)) for route in payload["routes"]),
            proxy_url=str(payload["proxy_url"]),
            upstream_device=str(payload.get("upstream_device", "")),
            route_mode=validate_route_mode(str(payload.get("route_mode") or DEFAULT_ROUTE_MODE)),
            route_metric=validate_route_metric(int(payload["route_metric"] if "route_metric" in payload else DEFAULT_ROUTE_METRIC)),
            dns_mode=validate_dns_mode(str(payload.get("dns_mode") or DEFAULT_DNS_MODE)),
            dns_servers=parse_dns_servers_payload(payload.get("dns_servers", DEFAULT_DNS_SERVERS)),
        )
        proxy_host_port(config.proxy_url)
        return SystemRuntimeState(
            pid=int(payload["pid"]),
            config=config,
            started_at=str(payload.get("started_at", "")),
            pid_start_ticks=str(payload.get("pid_start_ticks", "")),
            cmdline=str(payload.get("cmdline", "")),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError(f"invalid system state file {path}: {exc}") from exc


def remove_system_state(path: Path) -> None:
    try:
        path.unlink()
    except FileNotFoundError:
        pass


def remove_system_state_if_matching(path: Path, config: SystemRouteConfig) -> None:
    try:
        state = load_system_state(path)
    except ValueError as exc:
        print(f"state not removed: {exc}", file=sys.stderr)
        return
    if state is None:
        return
    if system_state_matches_config(state.config, config):
        remove_system_state(path)
    else:
        print(f"state not removed; saved config does not match cleanup target: {path}", file=sys.stderr)


def system_state_matches_config(saved: SystemRouteConfig, target: SystemRouteConfig) -> bool:
    return (
        saved.iface == target.iface
        and saved.address == target.address
        and saved.mtu == target.mtu
        and saved.routes == target.routes
        and saved.proxy_url == target.proxy_url
        and saved.route_mode == target.route_mode
        and saved.route_metric == target.route_metric
        and saved.dns_mode == target.dns_mode
        and saved.dns_servers == target.dns_servers
    )


def process_exists(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def process_matches_state(state: SystemRuntimeState) -> bool:
    if not state.pid_start_ticks:
        return False
    current_start = process_start_ticks(state.pid)
    if current_start != state.pid_start_ticks:
        return False
    current_cmdline = process_cmdline(state.pid)
    if state.cmdline and current_cmdline and current_cmdline != state.cmdline:
        return False
    return True


def process_start_ticks(pid: int) -> str:
    try:
        raw = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8")
    except OSError:
        return ""
    try:
        after_comm = raw.rsplit(") ", 1)[1]
    except IndexError:
        return ""
    fields = after_comm.split()
    if len(fields) < 20:
        return ""
    return fields[19]


def process_cmdline(pid: int) -> str:
    try:
        raw = Path(f"/proc/{pid}/cmdline").read_bytes()
    except OSError:
        return ""
    return " ".join(part.decode("utf-8", "replace") for part in raw.split(b"\0") if part)


def system_runtime_binary_from_cmdline(cmdline: str) -> str:
    try:
        parts = shlex.split(cmdline)
    except ValueError:
        parts = cmdline.split()
    for part in parts:
        if Path(part).name == "pdalink_lite.py":
            return part
    return ""


def system_runtime_drift_status(runtime_binary: str) -> str:
    if not runtime_binary:
        return "UNKNOWN runtime binary not recorded"
    runtime_path = Path(runtime_binary)
    if not runtime_path.is_absolute():
        return "UNKNOWN runtime binary path is relative"
    checkout_path = Path(__file__).resolve()
    if runtime_path.resolve(strict=False) == checkout_path:
        return "NO"
    try:
        if runtime_path.read_bytes() == checkout_path.read_bytes():
            return f"NO content matches checkout (runtime path: {runtime_path.resolve(strict=False)})"
    except OSError as exc:
        return f"UNKNOWN could not compare runtime and checkout binaries: {exc}"
    return f"YES runtime {runtime_path.resolve(strict=False)} differs from checkout {checkout_path}"


def stop_process(pid: int, timeout: float) -> bool:
    print(f"stopping process: {pid}")
    try:
        os.kill(pid, signal.SIGINT)
    except ProcessLookupError:
        print("process: not running")
        return True
    deadline = time.monotonic() + max(0.0, timeout)
    while time.monotonic() < deadline:
        if not process_exists(pid):
            print("process: stopped")
            return True
        time.sleep(0.1)
    if process_exists(pid):
        print(f"process: still running after {timeout:g}s; sending SIGTERM")
        try:
            os.kill(pid, signal.SIGTERM)
        except ProcessLookupError:
            print("process: stopped")
            return True
        deadline = time.monotonic() + max(0.0, timeout)
        while time.monotonic() < deadline:
            if not process_exists(pid):
                print("process: stopped")
                return True
            time.sleep(0.1)
        if process_exists(pid):
            print("process: still running after SIGTERM")
            return False
    return True


def build_tun_setup_commands(config: SystemRouteConfig) -> list[list[str]]:
    return [
        ["ip", "tuntap", "add", "dev", config.iface, "mode", "tun"],
        ["ip", "addr", "replace", config.address, "dev", config.iface],
        ["ip", "link", "set", "dev", config.iface, "mtu", str(config.mtu), "up"],
    ]


def build_adapter_owned_tun_setup_commands(config: SystemRouteConfig) -> list[list[str]]:
    return [
        ["ip", "addr", "replace", config.address, "dev", config.iface],
        ["ip", "link", "set", "dev", config.iface, "mtu", str(config.mtu), "up"],
    ]


def build_tun_route_commands(config: SystemRouteConfig) -> list[list[str]]:
    if config.route_mode == "fallback_low_priority":
        return [["ip", "route", "add", "default", "dev", config.iface, "metric", str(config.route_metric)]]
    return [["ip", "route", "replace", route, "dev", config.iface] for route in config.routes]


def existing_tun_route_conflicts(config: SystemRouteConfig) -> list[RouteConflict]:
    conflicts: list[RouteConflict] = []
    if config.route_mode == "fallback_low_priority":
        return conflicts
    ip_path = shutil.which("ip")
    if not ip_path:
        return conflicts
    for route in config.routes:
        result = subprocess.run(
            [ip_path, "route", "show", route],
            text=True,
            capture_output=True,
            check=False,
        )
        if result.returncode != 0:
            continue
        for line in result.stdout.splitlines():
            existing = line.strip()
            if not existing:
                continue
            if re.search(rf"\bdev\s+{re.escape(config.iface)}\b", existing):
                continue
            conflicts.append(RouteConflict(route, existing))
    return conflicts


def print_route_conflicts(config: SystemRouteConfig, conflicts: list[RouteConflict]) -> None:
    print("refusing to replace existing system routes:", file=sys.stderr)
    for conflict in conflicts:
        print(f"  {conflict.route}: {conflict.existing}", file=sys.stderr)
    print(f"run system-down for {config.iface} only if those routes belong to PdaLink", file=sys.stderr)


def build_tun_teardown_commands(config: SystemRouteConfig) -> list[list[str]]:
    commands: list[list[str]] = []
    commands.extend(build_dns_teardown_commands(config))
    if config.route_mode == "fallback_low_priority":
        commands.append(["ip", "route", "del", "default", "dev", config.iface, "metric", str(config.route_metric)])
    else:
        commands.extend([["ip", "route", "del", route, "dev", config.iface] for route in config.routes])
    commands.append(["ip", "link", "del", "dev", config.iface])
    return commands


def build_adapter_owned_tun_teardown_commands(config: SystemRouteConfig) -> list[list[str]]:
    commands: list[list[str]] = []
    commands.extend(build_dns_teardown_commands(config))
    if config.route_mode == "fallback_low_priority":
        commands.append(["ip", "route", "del", "default", "dev", config.iface, "metric", str(config.route_metric)])
    else:
        commands.extend([["ip", "route", "del", route, "dev", config.iface] for route in config.routes])
    return commands


def should_manage_dns(config: SystemRouteConfig) -> bool:
    return config.route_mode == "active_override" and config.dns_mode == "resolved"


def build_dns_setup_commands(config: SystemRouteConfig) -> list[list[str]]:
    if not should_manage_dns(config) or shutil.which("resolvectl") is None:
        return []
    return [
        ["resolvectl", "dns", config.iface, *config.dns_servers],
        ["resolvectl", "domain", config.iface, "~."],
        ["resolvectl", "default-route", config.iface, "yes"],
    ]


def build_dns_teardown_commands(config: SystemRouteConfig) -> list[list[str]]:
    if not should_manage_dns(config) or shutil.which("resolvectl") is None:
        return []
    return [["resolvectl", "revert", config.iface]]


def system_route_summary(config: SystemRouteConfig) -> str:
    if config.route_mode == "fallback_low_priority":
        return f"default dev {config.iface} metric {config.route_metric}"
    return ", ".join(config.routes)


def print_tool_line(name: str, path: str) -> None:
    marker = "OK" if path else "MISSING"
    detail = path or "not found in PATH"
    print(f"tool {name}: {marker} {detail}")


def print_adapter_status(config: SystemRouteConfig) -> bool:
    if config.adapter_command:
        executable = resolve_command_executable(config.adapter_command[0])
        if executable:
            print("adapter: CONFIGURED " + command_text(config.adapter_command))
            return True
        print("adapter: CONFIGURED executable not found: " + command_text(config.adapter_command))
        return False
    print("adapter: BUILTIN " + command_text(default_system_start_command(config)))
    return True


def print_dns_status(config: SystemRouteConfig) -> bool:
    if config.route_mode != "active_override":
        print("dns: SKIPPED route mode does not override existing default route")
        return True
    if config.dns_mode == "off":
        print("dns: OFF")
        return True
    resolvectl = shutil.which("resolvectl") or ""
    if resolvectl:
        print(f"dns: RESOLVED {resolvectl}")
        return True
    print("dns: UNAVAILABLE resolvectl not found; /etc/resolv.conf will not be modified")
    return False


def print_system_runtime_state(path: Path) -> None:
    try:
        state = load_system_state(path)
    except ValueError as exc:
        print(f"state: INVALID {exc}")
        return
    if state is None:
        print(f"state: MISSING {path}")
        return
    if process_matches_state(state):
        status = "running"
    elif process_exists(state.pid):
        status = "pid-reused"
    else:
        status = "stale"
    print(f"state: {status.upper()} {path}")
    print(f"state-pid: {state.pid}")
    print(f"state-started-at: {state.started_at}")
    print(f"state-proxy: {state.config.proxy_url}")
    upstream = state.config.upstream_device or (
        "loopback" if proxy_uses_loopback(state.config.proxy_url) else "UNBOUND (legacy state; restart required)")
    print(f"upstream-device: {upstream}")
    if state.cmdline:
        runtime_binary = system_runtime_binary_from_cmdline(state.cmdline)
        print(f"state-cmdline: {state.cmdline}")
        if runtime_binary:
            print(f"runtime-binary: {runtime_binary}")
            print(f"checkout-binary: {Path(__file__).resolve()}")
            print(f"runtime-drift: {system_runtime_drift_status(runtime_binary)}")


def default_system_start_command(config: SystemRouteConfig) -> list[str]:
    return [
        "sudo",
        "python3",
        "linux/pdalink_lite.py",
        "system-start",
        "--iface",
        config.iface,
        "--address",
        config.address,
        "--mtu",
        str(config.mtu),
        "--routes",
        ",".join(config.routes),
        "--proxy-url",
        config.proxy_url,
        "--route-mode",
        config.route_mode,
        "--route-metric",
        str(config.route_metric),
        "--dns-mode",
        config.dns_mode,
        "--dns-servers",
        ",".join(config.dns_servers),
    ]


def resolve_command_executable(command: str) -> str:
    if "/" in command:
        return command if os.access(command, os.X_OK) else ""
    return shutil.which(command) or ""


def print_system_proxy_probe(proxy_url: str) -> bool:
    try:
        host, port = proxy_host_port(proxy_url)
    except ValueError as exc:
        print(f"proxy-probe: FAIL {exc}")
        return False
    result = probe_one(host, port)
    marker = "OK" if result.ok else "FAIL"
    print(f"proxy-probe: {marker} {host}:{port} {result.detail}")
    return result.ok


def system_next_steps(
    ip_path: str,
    tun_access: bool,
    adapter_ok: bool,
    dns_ok: bool,
    proxy_ok: bool,
    config: SystemRouteConfig,
) -> list[str]:
    steps: list[str] = []
    if not ip_path:
        steps.append("install iproute2 so the helper can create TUN routes")
    if not tun_access:
        steps.append(f"enable the Linux TUN device and permissions for {TUN_DEVICE}")
    if not proxy_ok:
        steps.append("make the configured proxy reachable: " + config.proxy_url)
    if not adapter_ok:
        steps.append("provide a TUN-to-proxy packet adapter with --adapter-command")
    if not dns_ok:
        steps.append("install/enable systemd-resolved or pass --dns-mode off")
    if not steps:
        steps.append(f"run sudo python3 linux/pdalink_lite.py system-start for {config.iface}")
    return steps


def print_system_next_steps(steps: list[str]) -> None:
    if not steps:
        return
    print("next:")
    for step in steps:
        print(f"  - {step}")


def print_commands(commands: Iterable[Iterable[str]]) -> None:
    for command in commands:
        print_command(command)


def print_command(command: Iterable[str]) -> None:
    print("  " + command_text(command))


def command_text(command: Iterable[str]) -> str:
    return " ".join(shell_quote(part) for part in command)


def run_privileged_network_commands(commands: list[list[str]]) -> int:
    if os.geteuid() != 0:
        print("system route changes require root; rerun with sudo", file=sys.stderr)
        return 77
    for command in commands:
        print("+ " + command_text(command))
        result = subprocess.run(command, text=True, capture_output=True, check=False)
        if result.stdout.strip():
            print(result.stdout.strip())
        if result.stderr.strip():
            print(result.stderr.strip(), file=sys.stderr)
        if result.returncode != 0:
            return result.returncode
    return 0


def restart_active_tailscale_after_route_override(config: SystemRouteConfig) -> bool:
    """Reconnect an active tailscaled after active_override replaces its route."""
    if config.route_mode != "active_override":
        return False
    systemctl = shutil.which("systemctl")
    if not systemctl:
        print("tailscale-compat: skipped; systemctl is unavailable")
        return False
    service = "tailscaled.service"
    try:
        active = subprocess.run(
            [systemctl, "is-active", "--quiet", service],
            text=True,
            capture_output=True,
            check=False,
            timeout=10.0,
        )
        if active.returncode != 0:
            print("tailscale-compat: skipped; tailscaled is not active")
            return False
        restarted = subprocess.run(
            [systemctl, "try-restart", service],
            text=True,
            capture_output=True,
            check=False,
            timeout=20.0,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        print(f"tailscale-compat: warning; could not restart active tailscaled: {exc}", file=sys.stderr)
        return False
    if restarted.returncode != 0:
        detail = (restarted.stderr or restarted.stdout).strip() or f"systemctl exited {restarted.returncode}"
        print(f"tailscale-compat: warning; active tailscaled restart failed: {detail}", file=sys.stderr)
        return False
    print("tailscale-compat: restarted active tailscaled after route override")
    return True


def run_network_commands_best_effort(commands: list[list[str]]) -> bool:
    ok = True
    for command in commands:
        print("+ " + command_text(command))
        result = subprocess.run(command, text=True, capture_output=True, check=False)
        if result.stdout.strip():
            print(result.stdout.strip())
        if result.stderr.strip():
            print(result.stderr.strip(), file=sys.stderr)
        if result.returncode != 0:
            if teardown_command_already_absent(command):
                print("already clean")
            else:
                ok = False
    return ok


def teardown_command_already_absent(command: list[str]) -> bool:
    if len(command) >= 3 and command[0] == "resolvectl" and command[1] == "revert":
        return not link_exists(command[2])
    if len(command) >= 6 and command[1:3] == ["route", "del"] and "dev" in command:
        dev_index = command.index("dev")
        if dev_index + 1 >= len(command):
            return False
        route = command[3]
        iface = command[dev_index + 1]
        return not route_exists_for_iface(route, iface)
    if len(command) >= 5 and command[1:4] == ["link", "del", "dev"]:
        return not link_exists(command[4])
    return False


def route_exists_for_iface(route: str, iface: str) -> bool:
    result = subprocess.run(
        ["ip", "route", "show", route],
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        return False
    return any(re.search(rf"\bdev\s+{re.escape(iface)}\b", line) for line in result.stdout.splitlines())


def link_exists(iface: str) -> bool:
    result = subprocess.run(
        ["ip", "link", "show", "dev", iface],
        text=True,
        capture_output=True,
        check=False,
    )
    return result.returncode == 0


def doctor(args: argparse.Namespace) -> int:
    ports = parse_ports(args.ports)
    print("PdaLink doctor")
    print(f"target: {args.host}:{','.join(str(port) for port in ports)}")
    print()

    for tool in tool_statuses(("nmcli", "ip", "curl")):
        marker = "OK" if tool.ok else "MISSING"
        detail = tool.path if tool.path else "not found in PATH"
        print(f"tool {tool.name}: {marker} {detail}")

    route = route_info(args.host)
    if route is None:
        print(f"route {args.host}: UNKNOWN (ip route unavailable or failed)")
    else:
        print(f"route {args.host}: {route.raw}")
        warning = route_warning(args.host, route)
        if warning:
            print(warning)

    print()
    results = probe_ports(args.host, ports)
    working = first_ok(results)
    for result in results:
        marker = "OK" if result.ok else "FAIL"
        print(f"probe {result.host}:{result.port}: {marker} {result.detail}")

    status = 0 if working else 1
    if working:
        print(f"proxy: http://{working.host}:{working.port}")
    else:
        print("proxy: not reachable")

    if args.curl_url and working:
        print()
        status = run_curl_with_proxy(args.curl_url, working.host, working.port)
    elif args.curl_url:
        print("curl-test skipped: no working proxy", file=sys.stderr)

    if args.adb:
        print()
        adb_status(args.adb_log_lines)

    return status


def tool_statuses(names: Iterable[str]) -> list[ToolStatus]:
    return [ToolStatus(name, shutil.which(name) or "") for name in names]


def first_ok(results: Iterable[ProbeResult]) -> ProbeResult | None:
    for result in results:
        if result.ok:
            return result
    return None


def first_ok_or_first(results: Iterable[ProbeResult]) -> ProbeResult | None:
    first: ProbeResult | None = None
    for result in results:
        if first is None:
            first = result
        if result.ok:
            return result
    return first


def probe_ports(host: str, ports: Iterable[int]) -> list[ProbeResult]:
    return [probe_one(host, port) for port in ports]


def run_curl_with_proxy(url: str, host: str, port: int) -> int:
    if shutil.which("curl") is None:
        print("curl not found", file=sys.stderr)
        return 127
    proxy = f"http://{host}:{port}"
    command = ["curl", "-v", "-x", proxy, url]
    print("+ " + " ".join(shell_quote(part) for part in command))
    sys.stdout.flush()
    return subprocess.call(command)


def run_curl_with_proxy_retry(url: str, host: str, port: int, attempts: int, delay: float) -> int:
    last_status = 1
    for attempt in range(1, attempts + 1):
        if attempts > 1:
            print(f"curl smoke attempt {attempt}/{attempts}")
        last_status = run_curl_with_proxy(url, host, port)
        if last_status == 0:
            return 0
        if attempt < attempts:
            print(f"curl smoke failed with exit {last_status}; retrying in {delay:g}s", file=sys.stderr)
            time.sleep(delay)
    return last_status


def adb_status(log_lines: int) -> None:
    command = adb_command()
    if command is None:
        print("adb: not found")
        return
    print("+ " + " ".join(shell_quote(part) for part in [*command, "devices", "-l"]))
    devices = run_capture([*command, "devices", "-l"], timeout=5.0)
    print(devices.strip() or "(no adb output)")

    pid = run_capture([*command, "shell", "pidof", "dev.pdalink.prototype"], timeout=5.0).strip()
    print(f"app pid: {pid or 'not running'}")

    logs = run_capture(
        [*command, "logcat", "-d", "-t", str(log_lines), "-s", "PdaLinkProxy", "PdaLinkService"],
        timeout=8.0,
    ).strip()
    print("recent logs:")
    print(logs or "(no matching PdaLink logs)")


def adb_start_android_proxy(serial: str = "") -> int:
    target = ["-s", serial] if serial else []
    result = adb_run([*target,
        "shell",
        "am",
        "start-foreground-service",
        "-n",
        ANDROID_PROXY_SERVICE,
        "-a",
        ANDROID_ACTION_START,
    ], timeout=10.0)
    print_adb_result(result)
    return result.returncode


def adb_stop_android_proxy() -> int:
    result = adb_run([
        "shell",
        "am",
        "start-foreground-service",
        "-n",
        ANDROID_PROXY_SERVICE,
        "-a",
        ANDROID_ACTION_STOP,
    ], timeout=10.0)
    print_adb_result(result)
    return result.returncode


def adb_forward_tcp(local_port: int, remote_port: int, serial: str = "") -> int:
    target = ["-s", serial] if serial else []
    result = adb_run([*target, "forward", f"tcp:{local_port}", f"tcp:{remote_port}"], timeout=10.0)
    print_adb_result(result)
    if result.returncode == 0:
        print(f"adb forward tcp:{local_port} tcp:{remote_port}")
    return result.returncode


def adb_remove_forward(local_port: int) -> int:
    result = adb_run(["forward", "--remove", f"tcp:{local_port}"], timeout=10.0)
    print_adb_result(result)
    if result.returncode == 0:
        print(f"removed adb forward tcp:{local_port}")
    return result.returncode


def adb_run(args: list[str], timeout: float) -> CommandResult:
    command = adb_command()
    if command is None:
        return CommandResult(127, "", "adb not found")
    return run_command([*command, *args], timeout=timeout)


def parse_local_hotspot_broadcast(raw: str) -> LocalHotspotCredentials:
    match = re.search(r'Broadcast completed:\s*result=(-?\d+),\s*data="([A-Za-z0-9_-]+)"', raw)
    if match is None:
        if "inactive" in raw:
            raise ValueError("Local Hotspot is inactive")
        raise ValueError("ADB Local Hotspot response is missing")
    if int(match.group(1)) != -1:
        raise ValueError("Local Hotspot is inactive")
    encoded = match.group(2)
    try:
        decoded = base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4))
        payload = json.loads(decoded.decode("utf-8"))
    except (ValueError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("ADB Local Hotspot response is invalid") from exc
    if not isinstance(payload, dict):
        raise ValueError("ADB Local Hotspot response must be an object")

    ssid = str(payload.get("ssid") or "")
    password = str(payload.get("password") or "")
    interface = str(payload.get("interface") or "")
    host = str(payload.get("host") or "")
    security = str(payload.get("security") or "")
    try:
        port = int(payload.get("port"))
        address = ipaddress.ip_address(host)
    except (TypeError, ValueError) as exc:
        raise ValueError("Local Hotspot endpoint is invalid") from exc
    if not ssid or len(ssid.encode("utf-8")) > 128:
        raise ValueError("Local Hotspot SSID is invalid")
    if not 8 <= len(password) <= 63 \
            or any(not 0x20 <= ord(char) <= 0x7e for char in password):
        raise ValueError("Local Hotspot password is invalid")
    if not re.fullmatch(r"[A-Za-z0-9_.:-]{1,32}", interface):
        raise ValueError("Local Hotspot interface is invalid")
    if not isinstance(address, ipaddress.IPv4Address) or not address.is_private \
            or address.is_loopback or address.is_link_local or address.is_unspecified:
        raise ValueError("Local Hotspot host must be a private IPv4 address")
    if not 1 <= port <= 65535:
        raise ValueError("Local Hotspot port is invalid")
    return LocalHotspotCredentials(ssid, password, interface, host, port, security)


def query_local_hotspot_credentials(serial: str = "") -> LocalHotspotCredentials:
    target = ["-s", serial] if serial else []
    result = adb_run([
        *target,
        "shell", "am", "broadcast", "--receiver-foreground",
        "-n", ANDROID_LOCAL_HOTSPOT_RECEIVER,
        "-a", ANDROID_ACTION_QUERY_LOCAL_HOTSPOT,
    ], timeout=10.0)
    if result.returncode != 0:
        detail = (result.stderr or "ADB Local Hotspot query failed").strip()
        raise RuntimeError(detail)
    return parse_local_hotspot_broadcast(result.stdout)


def adb_start_local_hotspot(serial: str = "") -> int:
    target = ["-s", serial] if serial else []
    result = adb_run([
        *target,
        "shell", "am", "start-foreground-service",
        "-n", ANDROID_PROXY_SERVICE,
        "-a", ANDROID_ACTION_START_LOCAL_HOTSPOT,
    ], timeout=10.0)
    if result.returncode != 0:
        detail = (result.stderr or "could not start Android Local Hotspot").strip()
        print(detail, file=sys.stderr)
    return result.returncode


def default_local_hotspot_pin_path() -> Path:
    return pdalink_config_dir() / "local-hotspot-pin.json"


def default_local_hotspot_sync_state_path() -> Path:
    return pdalink_state_dir() / "local-hotspot-sync.json"


def local_hotspot_sync_state_matches(
        path: Path,
        credentials: LocalHotspotCredentials,
        profile: str) -> bool:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        return False
    if not isinstance(payload, dict):
        return False
    if payload.get("credential_sha256") != local_hotspot_credential_fingerprint(credentials):
        return False
    if payload.get("profile") != profile or saved_nm_connection_named(profile) is None:
        return False
    return nmcli_connection_ssid(profile) == credentials.ssid


def local_hotspot_profile_active(
        profile: str,
        credentials: LocalHotspotCredentials,
        iface: str | None) -> bool:
    return any(
        connection.name == profile and (not iface or connection.device == iface)
        for connection in active_nm_connections(iface)
    ) and nmcli_connection_ssid(profile) == credentials.ssid


def write_local_hotspot_sync_state(
        path: Path,
        credentials: LocalHotspotCredentials,
        profile: str) -> None:
    atomic_write_json(path, {
        "version": 1,
        "credential_sha256": local_hotspot_credential_fingerprint(credentials),
        "ssid": credentials.ssid,
        "proxy_url": f"http://{credentials.host}:{credentials.port}",
        "profile": profile,
    })


def validate_local_hotspot_target(target: str) -> str:
    value = target.strip()
    if value == "off":
        return value
    if not value or value.startswith("-") or not re.fullmatch(r"[A-Za-z0-9_.@:-]{1,255}", value):
        raise ValueError("local_hotspot_target must be off or a safe SSH host/alias")
    return value


def local_hotspot_target_payload(credentials: LocalHotspotCredentials) -> str:
    return json.dumps({
        "version": 1,
        "transport": "local-hotspot",
        "ssid": credentials.ssid,
        "password": credentials.password,
        "interface": credentials.interface,
        "host": credentials.host,
        "port": credentials.port,
        "security": credentials.security,
    }, separators=(",", ":")) + "\n"


def run_command_with_input(command: list[str], input_text: str, timeout: float) -> CommandResult:
    try:
        result = subprocess.run(
            command,
            input=input_text,
            text=True,
            capture_output=True,
            check=False,
            timeout=timeout,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return CommandResult(1, "", str(exc))
    return CommandResult(result.returncode, result.stdout, result.stderr)


def relay_local_hotspot_credentials(
        credentials: LocalHotspotCredentials,
        target: str,
        timeout: float = 45.0) -> CommandResult:
    validated_target = validate_local_hotspot_target(target)
    if validated_target == "off":
        return CommandResult(0, "target sync disabled\n", "")
    command = [
        "ssh",
        "-o", "BatchMode=yes",
        "-o", "ConnectTimeout=5",
        "-o", "StrictHostKeyChecking=yes",
        validated_target,
        "sudo", "-n", "/usr/local/sbin/pdalink-target-apply",
    ]
    return run_command_with_input(
        command,
        local_hotspot_target_payload(credentials),
        timeout=timeout,
    )


def local_hotspot_sync(args: argparse.Namespace) -> int:
    credentials: LocalHotspotCredentials | None = None
    if args.start_android:
        try:
            credentials = query_local_hotspot_credentials(args.serial)
        except ValueError as exc:
            if "inactive" not in str(exc).lower():
                print(f"local-hotspot: {exc}", file=sys.stderr)
                return 1
        except RuntimeError as exc:
            print(f"local-hotspot: {exc}", file=sys.stderr)
            return 1
        if credentials is None and adb_start_local_hotspot(args.serial) != 0:
            return 1

    deadline = time.monotonic() + max(0.0, args.wait)
    while credentials is None:
        try:
            credentials = query_local_hotspot_credentials(args.serial)
        except ValueError as exc:
            if "inactive" not in str(exc).lower() or time.monotonic() >= deadline:
                if args.if_active and "inactive" in str(exc).lower():
                    print("local-hotspot: inactive")
                    return 0
                print(f"local-hotspot: {exc}", file=sys.stderr)
                return 1
        except RuntimeError as exc:
            print(f"local-hotspot: {exc}", file=sys.stderr)
            return 1
        time.sleep(0.25)

    pin_path = Path(args.pin_file).expanduser() if args.pin_file else default_local_hotspot_pin_path()
    sync_state_raw = getattr(args, "sync_state_file", "")
    sync_state_path = Path(sync_state_raw).expanduser() \
        if sync_state_raw else default_local_hotspot_sync_state_path()
    state_matches = local_hotspot_sync_state_matches(
        sync_state_path,
        credentials,
        args.profile,
    )
    profile_active = local_hotspot_profile_active(
        args.profile,
        credentials,
        args.iface,
    )
    if state_matches:
        ok, status = True, "unchanged"
    else:
        try:
            ok, status = apply_local_hotspot_credentials(
                credentials,
                mode=args.mode,
                profile_name=args.profile,
                pin_path=pin_path,
                iface=args.iface,
                reset_pin=args.reset_pin,
            )
        except ValueError as exc:
            print(f"local-hotspot: {exc}", file=sys.stderr)
            return 2
    if not ok:
        if status == "credential-mismatch":
            print(
                "local-hotspot: pinned credentials changed; profile was not overwritten "
                "(use --reset-pin to accept the current Android values)",
                file=sys.stderr,
            )
        else:
            print(f"local-hotspot: {status}", file=sys.stderr)
        return 1
    print(f"local-hotspot: {status}")
    print(f"credential-fingerprint: {local_hotspot_credential_fingerprint(credentials)}")
    if args.connect and (not state_matches or not profile_active):
        result = activate_connection_with_secret(
            args.profile,
            credentials.password,
            args.iface,
            timeout=30.0,
        )
        if result.returncode != 0:
            detail = (result.stderr or result.stdout).strip() or f"nmcli exited {result.returncode}"
            print(f"local-hotspot: connection failed: {detail}", file=sys.stderr)
            return 1
        if verify_after_connect(credentials.host, (credentials.port,), args.verify_timeout) != 0:
            return 1

    if not state_matches:
        write_local_hotspot_sync_state(sync_state_path, credentials, args.profile)

    target = getattr(args, "target", "off")
    if target != "off":
        try:
            relay = relay_local_hotspot_credentials(credentials, target)
        except ValueError as exc:
            print(f"local-hotspot target: {exc}", file=sys.stderr)
            return 2
        if relay.returncode != 0:
            detail = (relay.stderr or relay.stdout).strip() or f"ssh exited {relay.returncode}"
            print(f"local-hotspot target: sync failed: {detail}", file=sys.stderr)
            return 1
        print("local-hotspot target: updated")
    return 0


def local_hotspot_target_sync(args: argparse.Namespace) -> int:
    try:
        credentials = query_local_hotspot_credentials(args.serial)
        relay = relay_local_hotspot_credentials(credentials, args.target)
    except (RuntimeError, ValueError) as exc:
        print(f"local-hotspot target: {exc}", file=sys.stderr)
        return 2
    if relay.returncode != 0:
        detail = (relay.stderr or relay.stdout).strip() or f"ssh exited {relay.returncode}"
        print(f"local-hotspot target: sync failed: {detail}", file=sys.stderr)
        return 1
    print("local-hotspot target: updated")
    print(f"credential-fingerprint: {local_hotspot_credential_fingerprint(credentials)}")
    return 0


def print_adb_result(result: CommandResult) -> None:
    output = (result.stdout or result.stderr).strip()
    if output:
        print(output)


def parse_adb_forward_list(raw: str) -> list[AdbForward]:
    forwards: list[AdbForward] = []
    for line in raw.splitlines():
        parts = line.split()
        if len(parts) < 3:
            continue
        forwards.append(AdbForward(parts[0], parts[1], parts[2]))
    return forwards


def parse_adb_devices(raw: str) -> list[AdbDevice]:
    devices: list[AdbDevice] = []
    for line in raw.splitlines():
        line = line.strip()
        if not line or line.startswith("List of devices"):
            continue
        parts = line.split(None, 2)
        if len(parts) < 2:
            continue
        state = parts[1]
        detail = parts[2] if len(parts) > 2 else ""
        if state == "no" and detail.startswith("permissions"):
            state = "no permissions"
            detail = detail[len("permissions"):].strip()
        devices.append(AdbDevice(parts[0], state, detail))
    return devices


def parse_lsusb_ids(raw: str) -> list[str]:
    ids: list[str] = []
    managed = set(MANAGED_UDEV_USB_IDS)
    for match in re.finditer(r"\bID\s+([0-9a-fA-F]{4}:[0-9a-fA-F]{4})\b", raw):
        usb_id = match.group(1).lower()
        if usb_id in managed and usb_id not in ids:
            ids.append(usb_id)
    return ids


def has_authorized_adb_device(devices: Iterable[AdbDevice]) -> bool:
    return any(device.ok for device in devices)


def parse_tcp_forward_port(value: str) -> int | None:
    if not value.startswith("tcp:"):
        return None
    try:
        return int(value[4:])
    except ValueError:
        return None


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def xdg_config_home() -> Path:
    raw = os.getenv("PDALINK_CONFIG_HOME") or os.getenv("XDG_CONFIG_HOME")
    return Path(raw).expanduser() if raw else Path.home() / ".config"


def xdg_state_home() -> Path:
    raw = os.getenv("PDALINK_STATE_HOME") or os.getenv("XDG_STATE_HOME")
    return Path(raw).expanduser() if raw else Path.home() / ".local" / "state"


def pdalink_config_dir() -> Path:
    return xdg_config_home() / "pdalink"


def pdalink_state_dir() -> Path:
    return xdg_state_home() / "pdalink"


def default_config_path() -> Path:
    return pdalink_config_dir() / "config.json"


def default_trusted_devices_path() -> Path:
    return pdalink_config_dir() / "trusted-devices.json"


def resolve_config_path(raw: str | None) -> Path:
    return Path(raw).expanduser() if raw else default_config_path()


def resolve_trusted_devices_path(raw: str | None) -> Path:
    return Path(raw).expanduser() if raw else default_trusted_devices_path()


def default_product_usb_state_path() -> Path:
    return pdalink_state_dir() / DEFAULT_PRODUCT_USB_STATE_FILE


def resolve_product_usb_state_path(raw: str | None) -> Path:
    return Path(raw).expanduser() if raw else default_product_usb_state_path()


def atomic_write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    try:
        tmp.chmod(0o600)
    except OSError:
        pass
    tmp.replace(path)
    try:
        path.chmod(0o600)
    except OSError:
        pass


def create_private_json(path: Path, payload: dict) -> None:
    """Create private state without overwriting a concurrent owner's snapshot."""
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError as exc:
        raise RuntimeError(
            f"Wi-Fi Direct proxy state already exists: {path}. "
            "Run `./tools/pdalink.sh wifi internet stop` first."
        ) from exc
    except OSError as exc:
        raise RuntimeError(f"cannot create Wi-Fi Direct proxy state {path}: {exc}") from exc
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as state_file:
            json.dump(payload, state_file, indent=2, sort_keys=True)
            state_file.write("\n")
    except Exception as exc:
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass
        raise RuntimeError(f"cannot write Wi-Fi Direct proxy state {path}: {exc}") from exc


def load_user_config(path: Path | None = None) -> dict[str, str]:
    target = path or default_config_path()
    try:
        payload = json.loads(target.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return default_user_config()
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"invalid PdaLink config {target}: {exc}") from exc
    if not isinstance(payload, dict):
        raise ValueError(f"invalid PdaLink config {target}: payload must be an object")
    config = default_user_config()
    for key in (
            "auto_connect",
            "local_hotspot_credentials",
            "local_hotspot_join",
            "local_hotspot_target",
            "route_mode",
            "dns_mode",
            "notify"):
        if key in payload:
            config[key] = str(payload[key])
    validate_user_config(config)
    return config


def load_product_usb_state(path: Path) -> dict[str, object]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"invalid product USB state {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise ValueError(f"invalid product USB state {path}: payload must be an object")
    return payload


def parse_product_usb_updated_at(raw: object) -> datetime | None:
    if not isinstance(raw, str) or not raw.strip():
        return None
    value = raw.strip()
    if value.endswith("Z"):
        value = value[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def product_usb_state_age_seconds(state: dict[str, object]) -> float | None:
    updated_at = parse_product_usb_updated_at(state.get("updated_at"))
    if updated_at is None:
        return None
    return (datetime.now(timezone.utc) - updated_at).total_seconds()


def product_usb_status_from_state(state: dict[str, object]) -> ProductUsbStatus:
    permission = str(state.get("permission") or "missing")
    host_trust = str(state.get("host_trust") or "untrusted")
    transport_state = str(state.get("transport_state") or "disconnected")
    proxy_state = str(state.get("proxy_state") or "missing")
    usb_debugging = str(state.get("usb_debugging") or "unknown")
    handshake_reason = str(state.get("handshake_reason") or "")

    if permission != "granted":
        return ProductUsbStatus(
            False,
            "permission missing",
            ("approve the PdaLink USB permission prompt on Android",),
            state,
        )
    if host_trust != "trusted":
        if host_trust == "revoked":
            step = "re-approve this Linux host in the Android trusted-host list"
            readiness = "host revoked"
        elif handshake_reason == "timeout":
            step = "unlock Android, keep Product USB mode open, then restart the Linux Product USB bridge"
            readiness = "handshake timeout"
        else:
            step = "approve this Linux host in the Android PdaLink USB trust prompt"
            readiness = "host untrusted"
        return ProductUsbStatus(False, readiness, (step,), state)
    if transport_state != "connected":
        return ProductUsbStatus(
            False,
            "transport unavailable",
            ("connect the Android device over USB and enable PdaLink USB mode",),
            state,
        )
    if proxy_state != "ready":
        return ProductUsbStatus(
            False,
            "proxy unavailable",
            ("start PdaLink USB mode on Android and wait for proxy readiness",),
            state,
        )
    if usb_debugging != "off":
        return ProductUsbStatus(
            False,
            "usb debugging not disabled",
            ("turn off Android USB debugging before product-mode release validation",),
            state,
        )
    age_seconds = product_usb_state_age_seconds(state)
    if age_seconds is None or age_seconds > PRODUCT_USB_STATE_MAX_AGE_SECONDS:
        return ProductUsbStatus(
            False,
            "state stale",
            ("restart the Product USB watcher so readiness is refreshed before changing routes",),
            state,
        )
    return ProductUsbStatus(True, "ready", ("run product USB system smoke",), state)


def default_user_config() -> dict[str, str]:
    return {
        "version": str(CONFIG_VERSION),
        "auto_connect": DEFAULT_AUTO_CONNECT,
        "local_hotspot_credentials": DEFAULT_LOCAL_HOTSPOT_CREDENTIALS,
        "local_hotspot_join": DEFAULT_LOCAL_HOTSPOT_JOIN,
        "local_hotspot_target": DEFAULT_LOCAL_HOTSPOT_TARGET,
        "route_mode": DEFAULT_ROUTE_MODE,
        "dns_mode": DEFAULT_DNS_MODE,
        "notify": "auto",
    }


def validate_user_config(config: dict[str, str]) -> None:
    if config.get("auto_connect") not in ("off", "on_trusted_connect", "when_offline"):
        raise ValueError("auto_connect must be off, on_trusted_connect, or when_offline")
    if config.get("local_hotspot_credentials") not in ("off", "auto", "pinned"):
        raise ValueError("local_hotspot_credentials must be off, auto, or pinned")
    if config.get("local_hotspot_join") not in ("off", "if_disconnected", "always"):
        raise ValueError("local_hotspot_join must be off, if_disconnected, or always")
    validate_local_hotspot_target(config.get("local_hotspot_target", ""))
    if config.get("route_mode") not in ("active_override", "fallback_low_priority"):
        raise ValueError("route_mode must be active_override or fallback_low_priority")
    if config.get("dns_mode") not in ("resolved", "off"):
        raise ValueError("dns_mode must be resolved or off")
    if config.get("notify") not in ("auto", "off"):
        raise ValueError("notify must be auto or off")


def write_user_config(path: Path, config: dict[str, str]) -> None:
    validate_user_config(config)
    payload = dict(config)
    payload["version"] = CONFIG_VERSION
    atomic_write_json(path, payload)


def load_trusted_devices(path: Path | None = None) -> TrustedDeviceStore:
    target = path or default_trusted_devices_path()
    try:
        payload = json.loads(target.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return TrustedDeviceStore("", {})
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"invalid trusted devices file {target}: {exc}") from exc
    if not isinstance(payload, dict):
        raise ValueError(f"invalid trusted devices file {target}: payload must be an object")
    raw_devices = payload.get("devices", {})
    if not isinstance(raw_devices, dict):
        raise ValueError(f"invalid trusted devices file {target}: devices must be an object")
    devices: dict[str, dict[str, str]] = {}
    for serial, raw_device in raw_devices.items():
        if not isinstance(raw_device, dict):
            continue
        device = {str(key): str(value) for key, value in raw_device.items() if value is not None}
        device["serial"] = str(serial)
        devices[str(serial)] = device
    default_serial = str(payload.get("default_serial") or "")
    if default_serial and default_serial not in devices:
        default_serial = ""
    return TrustedDeviceStore(default_serial, devices)


def write_trusted_devices(path: Path, store: TrustedDeviceStore) -> None:
    payload = {
        "version": CONFIG_VERSION,
        "default_serial": store.default_serial,
        "devices": store.devices,
    }
    atomic_write_json(path, payload)


def adb_detail_value(detail: str, key: str) -> str:
    prefix = key + ":"
    for token in detail.split():
        if token.startswith(prefix):
            return token[len(prefix):]
    return ""


def choose_connected_device_for_trust(devices: list[AdbDevice]) -> AdbDevice:
    ready = [device for device in devices if device.ok]
    if not ready:
        raise ValueError("no authorized ADB device is connected")
    if len(ready) > 1:
        serials = ", ".join(device.serial for device in ready)
        raise ValueError(f"multiple authorized devices connected; pass a serial explicitly ({serials})")
    return ready[0]


def trusted_device_for_serial(store: TrustedDeviceStore, serial: str) -> dict[str, str] | None:
    return store.devices.get(serial)


def trusted_serials(store: TrustedDeviceStore) -> set[str]:
    return set(store.devices)


def device_scan(args: argparse.Namespace) -> int:
    result = adb_run(["devices", "-l"], timeout=5.0)
    devices = parse_adb_devices(result.stdout)
    if args.json:
        payload = {
            "adb_ok": result.returncode == 0,
            "devices": [
                {
                    "serial": device.serial,
                    "state": device.state,
                    "detail": device.detail,
                    "model": adb_detail_value(device.detail, "model"),
                    "product": adb_detail_value(device.detail, "product"),
                }
                for device in devices
            ],
        }
        print(json.dumps(payload, indent=2, sort_keys=True))
        return 0 if result.returncode == 0 else result.returncode
    print_usb_devices(devices, result.stdout or result.stderr)
    return 0 if result.returncode == 0 else result.returncode


def device_list(args: argparse.Namespace) -> int:
    try:
        store = load_trusted_devices(resolve_trusted_devices_path(args.trusted_devices_file))
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps({
            "default_serial": store.default_serial,
            "devices": list(store.devices.values()),
        }, indent=2, sort_keys=True))
        return 0
    print("PdaLink trusted devices")
    print(f"file: {resolve_trusted_devices_path(args.trusted_devices_file)}")
    if not store.devices:
        print("(none)")
        return 0
    for serial in sorted(store.devices):
        device = store.devices[serial]
        marker = " default" if serial == store.default_serial else ""
        name = device.get("name", "")
        model = device.get("model", "")
        display = f"{serial}{marker}"
        if name:
            display += f" name={name}"
        if model:
            display += f" model={model}"
        print(display)
    return 0


def device_trust(args: argparse.Namespace) -> int:
    path = resolve_trusted_devices_path(args.trusted_devices_file)
    try:
        store = load_trusted_devices(path)
        serial = args.serial or ""
        selected: AdbDevice | None = None
        if args.connected:
            result = adb_run(["devices", "-l"], timeout=5.0)
            if result.returncode != 0:
                print((result.stderr or result.stdout).strip() or "adb devices failed", file=sys.stderr)
                return result.returncode
            selected = choose_connected_device_for_trust(parse_adb_devices(result.stdout))
            serial = selected.serial
        if not serial:
            raise ValueError("pass a serial or --connected")
        device = dict(store.devices.get(serial, {"serial": serial}))
        if selected is not None:
            device["model"] = adb_detail_value(selected.detail, "model")
            device["product"] = adb_detail_value(selected.detail, "product")
            device["last_seen"] = utc_now()
        if args.name:
            device["name"] = args.name
        device.setdefault("trusted_at", utc_now())
        store.devices[serial] = {key: value for key, value in device.items() if value}
        default_serial = serial if args.default or not store.default_serial else store.default_serial
        write_trusted_devices(path, TrustedDeviceStore(default_serial, store.devices))
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    print(f"trusted device: {serial}")
    if args.default or not store.default_serial:
        print(f"default device: {serial}")
    return 0


def device_untrust(args: argparse.Namespace) -> int:
    path = resolve_trusted_devices_path(args.trusted_devices_file)
    try:
        store = load_trusted_devices(path)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    if args.serial not in store.devices:
        print(f"device not trusted: {args.serial}", file=sys.stderr)
        return 1
    devices = dict(store.devices)
    devices.pop(args.serial, None)
    default_serial = "" if store.default_serial == args.serial else store.default_serial
    write_trusted_devices(path, TrustedDeviceStore(default_serial, devices))
    print(f"untrusted device: {args.serial}")
    return 0


def device_set_default(args: argparse.Namespace) -> int:
    path = resolve_trusted_devices_path(args.trusted_devices_file)
    try:
        store = load_trusted_devices(path)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    if args.serial not in store.devices:
        print(f"device is not trusted: {args.serial}", file=sys.stderr)
        return 1
    write_trusted_devices(path, TrustedDeviceStore(args.serial, store.devices))
    print(f"default device: {args.serial}")
    return 0


def config_list(args: argparse.Namespace) -> int:
    path = resolve_config_path(args.config_file)
    try:
        config = load_user_config(path)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps(config, indent=2, sort_keys=True))
        return 0
    print("PdaLink config")
    print(f"file: {path}")
    for key in (
            "auto_connect",
            "local_hotspot_credentials",
            "local_hotspot_join",
            "local_hotspot_target",
            "route_mode",
            "dns_mode",
            "notify"):
        print(f"{key}: {config[key]}")
    return 0


def config_set(args: argparse.Namespace) -> int:
    path = resolve_config_path(args.config_file)
    try:
        config = load_user_config(path)
        config[args.key] = args.value
        write_user_config(path, config)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    print(f"{args.key}: {args.value}")
    return 0


def config_get(args: argparse.Namespace) -> int:
    try:
        config = load_user_config(resolve_config_path(args.config_file))
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    if args.key not in config:
        print(f"unknown config key: {args.key}", file=sys.stderr)
        return 2
    print(config[args.key])
    return 0


def setup_plan(args: argparse.Namespace) -> int:
    config_path = resolve_config_path(args.config_file)
    trusted_path = resolve_trusted_devices_path(args.trusted_devices_file)
    try:
        config = load_user_config(config_path)
        store = load_trusted_devices(trusted_path)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    adb = adb_command()
    devices_result = adb_run(["devices", "-l"], timeout=5.0) if adb else CommandResult(127, "", "adb not found")
    devices = parse_adb_devices(devices_result.stdout)
    authorized = [device for device in devices if device.ok]
    trusted_connected = [device.serial for device in authorized if device.serial in store.devices]
    actions: list[str] = []
    if not adb:
        actions.append("install Android platform tools or use tools/adb.sh")
    if not store.devices:
        actions.append("trust an authorized Android device")
    if config["auto_connect"] == "off":
        actions.append("set auto_connect to on_trusted_connect when ready")
    payload = {
        "config_file": str(config_path),
        "trusted_devices_file": str(trusted_path),
        "config": config,
        "trusted_default": store.default_serial,
        "trusted_count": len(store.devices),
        "adb_ok": adb is not None and devices_result.returncode == 0,
        "connected_devices": [
            {"serial": device.serial, "state": device.state, "trusted": device.serial in store.devices}
            for device in devices
        ],
        "trusted_connected": trusted_connected,
        "next_actions": actions,
    }
    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        print("PdaLink setup plan")
        print(f"config: {config_path}")
        print(f"trusted devices: {trusted_path}")
        print(f"auto_connect: {config['auto_connect']}")
        print(f"route_mode: {config['route_mode']}")
        print(f"trusted connected: {', '.join(trusted_connected) or 'none'}")
        if actions:
            print("next:")
            for action in actions:
                print(f"  - {action}")
    return 0


def product_usb_status(args: argparse.Namespace) -> int:
    state_path = resolve_product_usb_state_path(args.state_file)
    try:
        state = load_product_usb_state(state_path)
        status = product_usb_status_from_state(state)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2

    payload = {
        "transport": {
            "name": PRODUCT_USB_TRANSPORT_NAME,
            "role": PRODUCT_USB_TRANSPORT_ROLE,
        },
        "state_file": str(state_path),
        "ready": status.ready,
        "readiness": status.readiness,
        "next_steps": list(status.next_steps),
        "state": status.state,
    }
    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=True))
        return 0 if status.ready else 1

    print("PdaLink product USB status")
    print(PRODUCT_USB_TRANSPORT_LINE)
    print(f"state-file: {state_path}")
    print(f"readiness: {status.readiness}")
    print("next:")
    for step in status.next_steps:
        print(f"  - {step}")
    return 0 if status.ready else 1


def product_usb_check(args: argparse.Namespace) -> int:
    state_path = resolve_product_usb_state_path(args.state_file)
    try:
        state = load_product_usb_state(state_path)
        status = product_usb_status_from_state(state)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps({
            "transport": {
                "name": PRODUCT_USB_TRANSPORT_NAME,
                "role": PRODUCT_USB_TRANSPORT_ROLE,
            },
            "release_gate": "ready" if status.ready else "blocked",
            "ready": status.ready,
            "readiness": status.readiness,
            "state_file": str(state_path),
            "next_steps": list(status.next_steps),
        }, indent=2, sort_keys=True))
        return 0 if status.ready else 1

    print("PdaLink product USB release check")
    print(PRODUCT_USB_TRANSPORT_LINE)
    print(f"state-file: {state_path}")
    if not status.ready:
        print(f"release-gate: blocked ({status.readiness})")
        print("next:")
        for step in status.next_steps:
            print(f"  - {step}")
        return 1

    print("release-gate: ready")
    print("USB debugging: off")
    print("manual smoke:")
    print(f"  - sudo python3 linux/pdalink_lite.py system-start --require-product-usb --product-usb-state-file {shell_quote(str(state_path))}")
    print(f"  - curl -4 {args.curl_url}")
    print("  - run a UDP/53 DNS lookup through pdalink0")
    print("  - sudo ./tools/pdalink.sh system stop")
    return 0


def product_usb_aoa_start(args: argparse.Namespace) -> int:
    try:
        import pdalink_aoa
    except ImportError as exc:
        print(f"aoa: FAIL cannot import AOA helper: {exc}", file=sys.stderr)
        return 127

    if args.dry_run:
        backend = pdalink_aoa.FakeAoaBackend(protocol=2)
        result = pdalink_aoa.start_pdalink_accessory(backend, args.serial)
        marker = "OK" if result.ok else "FAIL"
        print(f"aoa: {marker} {result.detail}")
        print("descriptor:")
        for index in sorted(pdalink_aoa.PDALINK_AOA_STRINGS):
            print(f"  - {index}: {pdalink_aoa.PDALINK_AOA_STRINGS[index]}")
        print(f"  - {pdalink_aoa.AOA_STRING_SERIAL}: {args.serial}")
        print("control-transfers:")
        for call in backend.calls:
            print(f"  - {call}")
        return 0 if result.ok else 1

    backend = None
    try:
        backend = pdalink_aoa.PyUsbAoaBackend.open(args.vid, args.pid)
        result = pdalink_aoa.start_pdalink_accessory(backend, args.serial)
    except (RuntimeError, ValueError) as exc:
        print(f"aoa: FAIL {exc}", file=sys.stderr)
        return 2
    finally:
        if backend is not None:
            backend.close()
    marker = "OK" if result.ok else "FAIL"
    print(f"aoa: {marker} {result.detail}")
    return 0 if result.ok else 1


def product_usb_bridge(args: argparse.Namespace) -> int:
    try:
        import pdalink_product_usb
    except ImportError as exc:
        print(f"bridge: FAIL cannot import Product USB helper: {exc}", file=sys.stderr)
        return 127

    state_path = resolve_product_usb_state_path(args.state_file)
    host = pdalink_product_usb.HostIdentity(args.host_name, args.host_fingerprint)
    transport = None
    bridge = None
    try:
        transport = pdalink_product_usb.PyUsbProductUsbTransport.open(
            vid=args.vid,
            pid=args.pid,
            timeout_ms=int(args.timeout * 1000),
        )
        product_id = getattr(transport, "product_id", args.pid)
        usb_debugging = pdalink_product_usb.product_id_usb_debugging_state(product_id)
        handshake = pdalink_product_usb.product_usb_hello(
            transport,
            host=host,
            timeout_hint=args.timeout,
        )
        if not handshake.accepted:
            host_trust = "revoked" if handshake.reason == "revoked" else "untrusted"
            if handshake.reason == "timeout":
                host_trust = "unknown"
            atomic_write_json(state_path, {
                "permission": "granted",
                "host_trust": host_trust,
                "handshake_reason": handshake.reason,
                "transport_state": "connected",
                "proxy_state": "missing",
                "source": "product-usb-bridge",
                "updated_at": datetime.now(timezone.utc).isoformat(),
                "usb_debugging": usb_debugging,
            })
            print(f"bridge: FAIL {handshake.reason}", file=sys.stderr)
            if handshake.next_step:
                print(f"next: {handshake.next_step}", file=sys.stderr)
            return 1

        bridge = pdalink_product_usb.ProductUsbLocalProxyBridge(
            transport,
            bind_host=args.bind_host,
            local_port=args.local_port,
        ).start()
        pac = bridge.fetch_proxy_pac()
        proxy_state = "ready" if pac.ok else "missing"
        atomic_write_json(state_path, {
            "permission": "granted",
            "host_trust": "trusted",
            "transport_state": "connected",
            "proxy_state": proxy_state,
            "source": "product-usb-bridge",
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "usb_debugging": usb_debugging,
        })
        if not pac.ok:
            print(f"bridge: FAIL {pac.detail}", file=sys.stderr)
            return 1

        print("bridge: ready")
        print(f"proxy: http://{args.bind_host}:{bridge.local_port}")
        print(f"state-file: {state_path}")
        print(f"usb-debugging: {usb_debugging}")
        if args.once:
            return 0
        try:
            while True:
                time.sleep(1.0)
        except KeyboardInterrupt:
            return 0
    except TimeoutError as exc:
        usb_debugging = "unknown"
        if transport is not None:
            product_id = getattr(transport, "product_id", args.pid)
            usb_debugging = pdalink_product_usb.product_id_usb_debugging_state(product_id)
        atomic_write_json(state_path, {
            "permission": "granted" if transport is not None else "missing",
            "host_trust": "unknown",
            "handshake_reason": "timeout",
            "transport_state": "connected" if transport is not None else "disconnected",
            "proxy_state": "missing",
            "source": "product-usb-bridge",
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "usb_debugging": usb_debugging,
        })
        print(f"bridge: FAIL {exc}", file=sys.stderr)
        print("next: unlock Android, keep Product USB mode open, then restart the Linux Product USB bridge", file=sys.stderr)
        return 1
    except Exception as exc:
        atomic_write_json(state_path, {
            "permission": "missing",
            "host_trust": "untrusted",
            "transport_state": "disconnected",
            "proxy_state": "missing",
            "source": "product-usb-bridge",
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "usb_debugging": "unknown",
        })
        print(f"bridge: FAIL {exc}", file=sys.stderr)
        return 1
    finally:
        if bridge is not None:
            bridge.close()
        if transport is not None:
            transport.close()


def status_cmd(args: argparse.Namespace) -> int:
    config_path = resolve_config_path(args.config_file)
    trusted_path = resolve_trusted_devices_path(args.trusted_devices_file)
    try:
        config = load_user_config(config_path)
        store = load_trusted_devices(trusted_path)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    devices_result = adb_run(["devices", "-l"], timeout=5.0)
    devices = parse_adb_devices(devices_result.stdout)
    payload = {
        "transport": {
            "name": ADB_TRANSPORT_NAME,
            "role": ADB_TRANSPORT_ROLE,
        },
        "config": config,
        "trusted_default": store.default_serial,
        "trusted_devices": sorted(store.devices),
        "connected_devices": [
            {"serial": device.serial, "state": device.state, "trusted": device.serial in store.devices}
            for device in devices
        ],
    }
    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=True))
        return 0
    print("PdaLink status")
    print(ADB_TRANSPORT_LINE)
    print(f"auto_connect: {config['auto_connect']}")
    print(f"local_hotspot_credentials: {config['local_hotspot_credentials']}")
    print(f"local_hotspot_join: {config['local_hotspot_join']}")
    print(f"local_hotspot_target: {config['local_hotspot_target']}")
    print(f"route_mode: {config['route_mode']}")
    print(f"dns_mode: {config['dns_mode']}")
    print(f"trusted devices: {len(store.devices)}")
    print(f"default device: {store.default_serial or 'none'}")
    if devices:
        print("connected devices:")
        for device in devices:
            trust = "trusted" if device.serial in store.devices else "untrusted"
            print(f"  - {device.serial} {device.state} {trust}")
    else:
        print("connected devices: none")
    return 0


def adb_command() -> list[str] | None:
    repo_adb = Path(__file__).resolve().parents[1] / "tools" / "adb.sh"
    if repo_adb.exists() and os.access(repo_adb, os.X_OK):
        return [str(repo_adb)]
    adb = shutil.which("adb")
    if adb:
        return [adb]
    if repo_adb.exists():
        return ["bash", str(repo_adb)]
    return None


def run_capture(command: list[str], timeout: float) -> str:
    result = run_command(command, timeout)
    if result.stdout:
        return result.stdout
    return result.stderr


def run_command(command: list[str], timeout: float) -> CommandResult:
    try:
        result = subprocess.run(command, text=True, capture_output=True, check=False, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return CommandResult(1, "", str(exc))
    return CommandResult(result.returncode, result.stdout, result.stderr)


def first_working_port(host: str, ports: Iterable[int]) -> int:
    warning = route_warning(host, route_info(host))
    if warning:
        print(warning, file=sys.stderr)
    failures: list[str] = []
    for result in probe_ports(host, ports):
        if result.ok:
            return result.port
        failures.append(f"{result.port}: {result.detail}")
    raise SystemExit("No working proxy port found: " + "; ".join(failures))


def shell_quote(value: str) -> str:
    if re.fullmatch(r"[A-Za-z0-9_./:,=-]+", value):
        return value
    return "'" + value.replace("'", "'\"'\"'") + "'"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="PdaLink Linux Wi-Fi Direct and USB proxy helper")
    sub = parser.add_subparsers(dest="command", required=True)

    scan_parser = sub.add_parser("scan", help="scan visible Wi-Fi networks with nmcli")
    scan_parser.set_defaults(func=scan)

    connect_parser = sub.add_parser("connect", help="connect to the Android Wi-Fi Direct group")
    connect_parser.add_argument("--ssid", required=True)
    connect_parser.add_argument("--password")
    connect_parser.add_argument("--iface")
    connect_parser.add_argument("--verify", action="store_true", help="probe the proxy after connecting")
    connect_parser.add_argument("--verify-timeout", type=float, default=15.0)
    add_target_args(connect_parser)
    connect_parser.set_defaults(func=connect)

    auto_parser = sub.add_parser("connect-auto", help="select a visible Android DIRECT-* group, connect, then verify")
    auto_parser.add_argument("--ssid-pattern", default="DIRECT-")
    auto_parser.add_argument("--password")
    auto_parser.add_argument("--iface")
    auto_parser.add_argument("--verify-timeout", type=float, default=15.0)
    add_target_args(auto_parser)
    auto_parser.set_defaults(func=connect_auto)

    wifi_status_parser = sub.add_parser("wifi-status", help="show Wi-Fi Direct route and proxy status")
    wifi_status_parser.add_argument("--ssid-pattern", default="DIRECT-")
    wifi_status_parser.add_argument("--iface")
    add_target_args(wifi_status_parser)
    wifi_status_parser.set_defaults(func=wifi_status)

    wifi_internet_start_parser = sub.add_parser(
        "wifi-internet-start",
        help="connect Wi-Fi Direct and apply the GNOME PAC proxy transactionally",
    )
    wifi_internet_start_parser.add_argument("--ssid")
    wifi_internet_start_parser.add_argument("--password")
    wifi_internet_start_parser.add_argument("--iface")
    wifi_internet_start_parser.add_argument("--ssid-pattern", default="DIRECT-")
    wifi_internet_start_parser.add_argument("--verify-timeout", type=float, default=15.0)
    wifi_internet_start_parser.add_argument("--state-file", default=os.getenv("PDALINK_WIFI_PROXY_STATE", ""))
    add_wifi_direct_target_args(wifi_internet_start_parser)
    wifi_internet_start_parser.set_defaults(func=wifi_internet_start)

    wifi_internet_status_parser = sub.add_parser(
        "wifi-internet-status",
        help="show Wi-Fi Direct and GNOME managed proxy ownership",
    )
    wifi_internet_status_parser.add_argument("--iface")
    wifi_internet_status_parser.add_argument("--ssid-pattern", default="DIRECT-")
    wifi_internet_status_parser.add_argument("--state-file", default=os.getenv("PDALINK_WIFI_PROXY_STATE", ""))
    add_wifi_direct_target_args(wifi_internet_status_parser)
    wifi_internet_status_parser.set_defaults(func=wifi_internet_status)

    wifi_internet_stop_parser = sub.add_parser(
        "wifi-internet-stop",
        help="restore the previous GNOME proxy and disconnect an owned Direct connection",
    )
    wifi_internet_stop_parser.add_argument("--state-file", default=os.getenv("PDALINK_WIFI_PROXY_STATE", ""))
    wifi_internet_stop_parser.add_argument("--keep-wifi", action="store_true")
    wifi_internet_stop_parser.add_argument("--force", action="store_true")
    wifi_internet_stop_parser.set_defaults(func=wifi_internet_stop)

    wifi_internet_test_parser = sub.add_parser(
        "wifi-internet-test",
        help="verify managed proxy ownership and run an explicit proxy request",
    )
    wifi_internet_test_parser.add_argument("--iface")
    wifi_internet_test_parser.add_argument("--ssid-pattern", default="DIRECT-")
    wifi_internet_test_parser.add_argument("--state-file", default=os.getenv("PDALINK_WIFI_PROXY_STATE", ""))
    wifi_internet_test_parser.add_argument("--url", default="https://ifconfig.me")
    add_wifi_direct_target_args(wifi_internet_test_parser)
    wifi_internet_test_parser.set_defaults(func=wifi_internet_test)

    local_hotspot_sync_parser = sub.add_parser(
        "local-hotspot-sync",
        help="sync Android Local Hotspot credentials into a local-only NetworkManager profile",
    )
    local_hotspot_sync_parser.add_argument("--mode", choices=("auto", "pinned"), default="auto")
    local_hotspot_sync_parser.add_argument("--profile", default=LOCAL_HOTSPOT_PROFILE_NAME)
    local_hotspot_sync_parser.add_argument("--pin-file", default="")
    local_hotspot_sync_parser.add_argument("--sync-state-file", default="")
    local_hotspot_sync_parser.add_argument("--iface")
    local_hotspot_sync_parser.add_argument("--serial", default="")
    local_hotspot_sync_parser.add_argument("--connect", action="store_true")
    local_hotspot_sync_parser.add_argument("--target", default="off")
    local_hotspot_sync_parser.add_argument("--reset-pin", action="store_true")
    local_hotspot_sync_parser.add_argument("--if-active", action="store_true")
    local_hotspot_sync_parser.add_argument("--start-android", action="store_true")
    local_hotspot_sync_parser.add_argument("--wait", type=float, default=10.0)
    local_hotspot_sync_parser.add_argument("--verify-timeout", type=float, default=15.0)
    local_hotspot_sync_parser.set_defaults(func=local_hotspot_sync)

    local_hotspot_target_parser = sub.add_parser(
        "local-hotspot-target-sync",
        help="relay active Android Local Hotspot credentials to a configured Linux target",
    )
    local_hotspot_target_parser.add_argument("--serial", default="")
    local_hotspot_target_parser.add_argument("--target", required=True)
    local_hotspot_target_parser.set_defaults(func=local_hotspot_target_sync)

    connection_import_parser = sub.add_parser(
        "connection-import",
        help="read a PdaLink connection URI or QR and connect with NetworkManager",
    )
    connection_import_parser.add_argument("--uri", default="", help="PdaLink connection URI")
    connection_import_parser.add_argument("--image", default="", help="QR image path")
    connection_import_parser.add_argument("--camera", action="store_true", help="scan from a camera")
    connection_import_parser.add_argument("--camera-index", type=int, default=0)
    connection_import_parser.add_argument("--scan-timeout", type=float, default=60.0)
    connection_import_parser.add_argument("--iface")
    connection_import_parser.add_argument("--verify-timeout", type=float, default=15.0)
    connection_import_parser.add_argument("--no-connect", action="store_true")
    connection_import_parser.add_argument("--internet", action="store_true", help="persist GNOME PAC after verification")
    connection_import_parser.add_argument("--state-file", default=os.getenv("PDALINK_WIFI_PROXY_STATE", ""))
    connection_import_parser.set_defaults(func=connection_import)

    probe_parser = sub.add_parser("probe", help="probe Android proxy PAC endpoint")
    add_target_args(probe_parser)
    probe_parser.add_argument("--show-pac", action="store_true")
    probe_parser.set_defaults(func=probe)

    env_parser = sub.add_parser("env", help="print shell proxy environment exports")
    add_target_args(env_parser)
    env_parser.add_argument("--probe", action="store_true", help="probe and use the first working port")
    env_parser.set_defaults(func=env_cmd)

    curl_parser = sub.add_parser("curl-test", help="run curl through the Android proxy")
    add_target_args(curl_parser)
    curl_parser.add_argument("--url", default="https://ifconfig.me")
    curl_parser.set_defaults(func=curl_test)

    usb_start_parser = sub.add_parser("usb-start", help="start Android proxy and expose it through adb forward")
    add_usb_args(usb_start_parser)
    usb_start_parser.add_argument("--no-start-android", dest="start_android", action="store_false")
    usb_start_parser.add_argument("--verify", action="store_true", help="probe the forwarded localhost proxy")
    usb_start_parser.add_argument("--verify-timeout", type=float, default=15.0)
    usb_start_parser.set_defaults(func=usb_start, start_android=True)

    usb_stop_parser = sub.add_parser("usb-stop", help="remove adb forward for the USB proxy")
    add_usb_args(usb_stop_parser)
    usb_stop_parser.add_argument("--stop-android", action="store_true", help="also stop the Android proxy service")
    usb_stop_parser.set_defaults(func=usb_stop)

    usb_status_parser = sub.add_parser("usb-status", help="show adb forward and probe status for the USB proxy")
    add_usb_args(usb_status_parser)
    usb_status_parser.set_defaults(func=usb_status)

    connection_status_parser = sub.add_parser(
        "connection-status",
        help="show whether Ubuntu internet is currently using PdaLink",
    )
    add_usb_args(connection_status_parser)
    connection_status_parser.add_argument("--state-file", default="", help="active system tunnel state; defaults to service runtime then /run")
    connection_status_parser.add_argument(
        "--route-target",
        default=os.getenv("PDALINK_CONNECTION_ROUTE_TARGET", "1.1.1.1"),
        help="internet address used to inspect the active Ubuntu route",
    )
    connection_status_parser.add_argument(
        "--curl-url",
        default=os.getenv("PDALINK_CONNECTION_CURL_URL", "https://cloudflare.com/cdn-cgi/trace"),
        help="URL used for the direct internet probe",
    )
    connection_status_parser.add_argument(
        "--curl-timeout",
        type=float,
        default=float(os.getenv("PDALINK_CONNECTION_CURL_TIMEOUT", "10.0")),
        help="seconds to wait for the direct internet probe",
    )
    connection_status_parser.add_argument("--json", action="store_true")
    connection_status_parser.set_defaults(func=connection_status)

    usb_smoke_parser = sub.add_parser("usb-smoke", help="start adb forward and curl through the USB proxy")
    add_usb_args(usb_smoke_parser)
    usb_smoke_parser.add_argument("--url", default="https://ifconfig.me")
    usb_smoke_parser.add_argument("--no-start-android", dest="start_android", action="store_false")
    usb_smoke_parser.add_argument("--verify-timeout", type=float, default=15.0)
    usb_smoke_parser.add_argument(
        "--curl-attempts",
        type=int,
        default=3,
        help="retry curl because Android cellular egress may become available after the proxy port opens",
    )
    usb_smoke_parser.add_argument("--curl-delay", type=float, default=1.0, help="seconds between curl retries")
    usb_smoke_parser.set_defaults(func=usb_smoke, start_android=True)

    product_usb_status_parser = sub.add_parser(
        "product-usb-status",
        help="show ADB-free product USB readiness and consent status",
    )
    product_usb_status_parser.add_argument("--state-file", default=os.getenv("PDALINK_PRODUCT_USB_STATE", ""))
    product_usb_status_parser.add_argument("--json", action="store_true")
    product_usb_status_parser.set_defaults(func=product_usb_status)

    product_usb_check_parser = sub.add_parser(
        "product-usb-check",
        help="check ADB-free product USB release readiness and print the no-ADB smoke gate",
    )
    product_usb_check_parser.add_argument("--state-file", default=os.getenv("PDALINK_PRODUCT_USB_STATE", ""))
    product_usb_check_parser.add_argument("--json", action="store_true")
    product_usb_check_parser.add_argument("--curl-url", default="https://ifconfig.me")
    product_usb_check_parser.set_defaults(func=product_usb_check)

    product_usb_aoa_parser = sub.add_parser(
        "product-usb-aoa-start",
        help="send PdaLink Android Open Accessory descriptors to an attached device",
    )
    product_usb_aoa_parser.add_argument("--serial", default=os.getenv("PDALINK_HOST_FINGERPRINT", "dev-host"))
    product_usb_aoa_parser.add_argument("--dry-run", action="store_true")
    product_usb_aoa_parser.add_argument("--vid", default="")
    product_usb_aoa_parser.add_argument("--pid", default="")
    product_usb_aoa_parser.set_defaults(func=product_usb_aoa_start)

    product_usb_bridge_parser = sub.add_parser(
        "product-usb-bridge",
        help="serve the local Product USB bridge over Android Open Accessory bulk endpoints",
    )
    product_usb_bridge_parser.add_argument("--vid", default="18d1")
    product_usb_bridge_parser.add_argument("--pid", default="")
    product_usb_bridge_parser.add_argument("--bind-host", default="127.0.0.1")
    product_usb_bridge_parser.add_argument("--local-port", type=int, default=USB_LOCAL_PORT)
    product_usb_bridge_parser.add_argument("--state-file", default=os.getenv("PDALINK_PRODUCT_USB_STATE", ""))
    product_usb_bridge_parser.add_argument("--host-name", default=socket.gethostname())
    product_usb_bridge_parser.add_argument("--host-fingerprint", default=os.getenv("PDALINK_HOST_FINGERPRINT", "dev-host"))
    product_usb_bridge_parser.add_argument("--timeout", type=float, default=5.0)
    product_usb_bridge_parser.add_argument("--once", action="store_true")
    product_usb_bridge_parser.set_defaults(func=product_usb_bridge)

    status_parser = sub.add_parser("status", help="show PdaLink config and trusted-device status")
    add_config_storage_args(status_parser)
    status_parser.add_argument("--json", action="store_true")
    status_parser.set_defaults(func=status_cmd)

    device_scan_parser = sub.add_parser("device-scan", help="list connected ADB devices")
    device_scan_parser.add_argument("--json", action="store_true")
    device_scan_parser.set_defaults(func=device_scan)

    device_list_parser = sub.add_parser("device-list", help="list trusted Android devices")
    add_config_storage_args(device_list_parser)
    device_list_parser.add_argument("--json", action="store_true")
    device_list_parser.set_defaults(func=device_list)

    device_trust_parser = sub.add_parser("device-trust", help="trust an Android ADB serial for automatic connection")
    add_config_storage_args(device_trust_parser)
    device_trust_parser.add_argument("serial", nargs="?")
    device_trust_parser.add_argument("--connected", action="store_true", help="trust the single connected authorized device")
    device_trust_parser.add_argument("--name", default="")
    device_trust_parser.add_argument("--default", action="store_true", help="make this trusted device the default")
    device_trust_parser.set_defaults(func=device_trust)

    device_untrust_parser = sub.add_parser("device-untrust", help="remove a trusted Android device")
    add_config_storage_args(device_untrust_parser)
    device_untrust_parser.add_argument("serial")
    device_untrust_parser.set_defaults(func=device_untrust)

    device_default_parser = sub.add_parser("device-set-default", help="choose the default trusted Android device")
    add_config_storage_args(device_default_parser)
    device_default_parser.add_argument("serial")
    device_default_parser.set_defaults(func=device_set_default)

    config_list_parser = sub.add_parser("config-list", help="list PdaLink user config")
    add_config_storage_args(config_list_parser)
    config_list_parser.add_argument("--json", action="store_true")
    config_list_parser.set_defaults(func=config_list)

    config_get_parser = sub.add_parser("config-get", help="print one PdaLink config value")
    add_config_storage_args(config_get_parser)
    config_get_parser.add_argument("key")
    config_get_parser.set_defaults(func=config_get)

    config_set_parser = sub.add_parser("config-set", help="set one PdaLink config value")
    add_config_storage_args(config_set_parser)
    config_set_parser.add_argument(
        "key",
        choices=(
            "auto_connect",
            "local_hotspot_credentials",
            "local_hotspot_join",
            "local_hotspot_target",
            "route_mode",
            "dns_mode",
            "notify",
        ),
    )
    config_set_parser.add_argument("value")
    config_set_parser.set_defaults(func=config_set)

    setup_plan_parser = sub.add_parser("setup-plan", help="print a human or JSON setup plan")
    add_config_storage_args(setup_plan_parser)
    setup_plan_parser.add_argument("--json", action="store_true")
    setup_plan_parser.set_defaults(func=setup_plan)

    system_status_parser = sub.add_parser(
        "system-status",
        help="diagnose prerequisites for experimental whole-system TUN routing",
    )
    add_system_args(system_status_parser)
    add_system_state_arg(system_status_parser)
    system_status_parser.add_argument(
        "--no-probe-proxy",
        dest="probe_proxy",
        action="store_false",
        help="skip probing the proxy URL",
    )
    system_status_parser.set_defaults(func=system_status, probe_proxy=True)

    system_plan_parser = sub.add_parser(
        "system-plan",
        help="print a dry-run TUN route and packet-adapter plan",
    )
    add_system_args(system_plan_parser)
    system_plan_parser.set_defaults(func=system_plan)

    system_start_parser = sub.add_parser(
        "system-start",
        help="run the built-in TUN-to-HTTP-proxy adapter and apply routes until Ctrl-C",
    )
    add_system_args(system_start_parser, include_adapter_command=False)
    add_system_state_arg(system_start_parser)
    add_system_runtime_args(system_start_parser)
    add_product_usb_guard_args(system_start_parser)
    add_usb_args(system_start_parser)
    system_start_parser.add_argument(
        "--start-usb",
        action="store_true",
        help="start adb forward before opening TUN; useful when running as a user with sudo-preserved adb access",
    )
    system_start_parser.add_argument("--no-start-android", dest="start_android", action="store_false")
    system_start_parser.add_argument("--no-probe-proxy", dest="probe_proxy", action="store_false")
    system_start_parser.add_argument(
        "--no-tailscale-compat",
        dest="tailscale_compat",
        action="store_false",
        help="do not restart an active tailscaled after active_override routes are installed",
    )
    system_start_parser.add_argument("--verify-timeout", type=float, default=15.0)
    system_start_parser.set_defaults(
        func=system_start,
        start_android=True,
        probe_proxy=True,
        tailscale_compat=True,
    )

    system_adapter_parser = sub.add_parser(
        "system-adapter",
        help="run only the built-in TUN-to-HTTP-proxy packet adapter",
    )
    add_system_args(system_adapter_parser, include_adapter_command=False)
    add_system_runtime_args(system_adapter_parser)
    system_adapter_parser.set_defaults(func=system_adapter)

    system_up_parser = sub.add_parser(
        "system-up",
        help="dry-run or apply TUN setup and system route commands",
    )
    add_system_args(system_up_parser)
    system_up_parser.add_argument("--apply", action="store_true", help="execute privileged route commands")
    add_product_usb_guard_args(system_up_parser)
    system_up_parser.add_argument(
        "--adapter-ready",
        action="store_true",
        help="confirm the packet adapter is already running before applying routes",
    )
    system_up_parser.set_defaults(func=system_up)

    system_down_parser = sub.add_parser(
        "system-down",
        help="dry-run or remove experimental TUN system routes",
    )
    add_system_args(system_down_parser)
    add_system_state_arg(system_down_parser)
    system_down_parser.add_argument("--apply", action="store_true", help="execute privileged route cleanup commands")
    system_down_parser.set_defaults(func=system_down)

    system_stop_parser = sub.add_parser(
        "system-stop",
        help="stop a state-tracked system-start process and clean TUN routes",
    )
    add_system_args(system_stop_parser, include_adapter_command=False)
    add_system_state_arg(system_stop_parser)
    system_stop_parser.add_argument("--stop-timeout", type=float, default=5.0, help="seconds to wait after SIGINT")
    system_stop_parser.set_defaults(func=system_stop)

    doctor_parser = sub.add_parser("doctor", help="diagnose route, tools, proxy ports, and optional curl/adb status")
    add_target_args(doctor_parser)
    doctor_parser.add_argument("--curl-url", help="run a curl smoke test if a proxy port works")
    doctor_parser.add_argument("--adb", action="store_true", help="include adb device and recent Android logs")
    doctor_parser.add_argument("--adb-log-lines", type=int, default=80)
    doctor_parser.set_defaults(func=doctor)

    return parser


def add_target_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--host", default=os.getenv("PDALINK_HOST", DEFAULT_HOST))
    parser.add_argument("--ports", default=os.getenv("PDALINK_PORTS", ",".join(str(p) for p in DEFAULT_PORTS)))


def add_wifi_direct_target_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--host", default=os.getenv("PDALINK_HOST", DEFAULT_HOST))
    parser.add_argument(
        "--ports",
        default=os.getenv("PDALINK_WIFI_PORTS", os.getenv("PDALINK_PORTS", "8001")),
    )


def add_usb_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--local-port", type=int, default=int(os.getenv("PDALINK_USB_LOCAL_PORT", USB_LOCAL_PORT)))
    parser.add_argument("--remote-port", type=int, default=int(os.getenv("PDALINK_USB_REMOTE_PORT", USB_REMOTE_PORT)))


def add_config_storage_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--config-file", default=os.getenv("PDALINK_CONFIG_FILE", ""))
    parser.add_argument("--trusted-devices-file", default=os.getenv("PDALINK_TRUSTED_DEVICES_FILE", ""))


def add_system_args(parser: argparse.ArgumentParser, include_adapter_command: bool = True) -> None:
    parser.add_argument("--iface", default=os.getenv("PDALINK_TUN_IFACE", DEFAULT_SYSTEM_IFACE))
    parser.add_argument("--address", default=os.getenv("PDALINK_TUN_ADDRESS", DEFAULT_SYSTEM_ADDRESS))
    parser.add_argument("--mtu", type=int, default=int(os.getenv("PDALINK_TUN_MTU", DEFAULT_SYSTEM_MTU)))
    parser.add_argument(
        "--routes",
        default=os.getenv("PDALINK_TUN_ROUTES", ",".join(DEFAULT_SYSTEM_ROUTES)),
        help="comma-separated CIDR routes to send into the TUN interface",
    )
    parser.add_argument(
        "--proxy-url",
        default=os.getenv("PDALINK_SYSTEM_PROXY", f"http://{USB_HOST}:{USB_LOCAL_PORT}"),
        help="HTTP proxy reached by the packet adapter, normally the USB-forwarded Android proxy",
    )
    parser.add_argument(
        "--route-mode",
        choices=("active_override", "fallback_low_priority"),
        default=os.getenv("PDALINK_ROUTE_MODE", DEFAULT_ROUTE_MODE),
        help="active_override uses split default routes; fallback_low_priority adds a high-metric default route",
    )
    parser.add_argument(
        "--route-metric",
        type=int,
        default=int(os.getenv("PDALINK_ROUTE_METRIC", DEFAULT_ROUTE_METRIC)),
        help="metric for fallback_low_priority default route",
    )
    parser.add_argument(
        "--dns-mode",
        choices=("resolved", "off"),
        default=os.getenv("PDALINK_DNS_MODE", DEFAULT_DNS_MODE),
        help="DNS management mode for active_override",
    )
    parser.add_argument(
        "--dns-servers",
        default=os.getenv("PDALINK_DNS_SERVERS", ",".join(DEFAULT_DNS_SERVERS)),
        help="comma-separated DNS servers for systemd-resolved per-link DNS",
    )
    if include_adapter_command:
        parser.add_argument(
            "--adapter-command",
            default=os.getenv("PDALINK_TUN_ADAPTER_COMMAND", ""),
            help="packet adapter command template; supports {iface}, {proxy}, and {mtu}",
        )


def add_system_runtime_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--no-dns", action="store_true", help="disable UDP/53 to TCP DNS conversion")
    parser.add_argument("--connect-timeout", type=float, default=10.0, help="seconds for HTTP CONNECT setup")
    parser.add_argument(
        "--tcp-idle-timeout",
        type=float,
        default=0.0,
        help="seconds before an idle TCP flow is closed; default 0 disables idle cleanup",
    )
    parser.add_argument("--verbose", action="store_true", help="print packet adapter debug logs")


def add_product_usb_guard_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--require-product-usb",
        action="store_true",
        help="refuse route changes unless the ADB-free product USB state is ready",
    )
    parser.add_argument(
        "--product-usb-state-file",
        default=os.getenv("PDALINK_PRODUCT_USB_STATE", ""),
        help="product USB readiness state file",
    )


def add_system_state_arg(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--state-file",
        default=os.getenv("PDALINK_SYSTEM_STATE", DEFAULT_SYSTEM_STATE_FILE),
        help="runtime state file for system-start/system-stop coordination",
    )


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
