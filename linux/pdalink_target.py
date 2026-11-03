#!/usr/bin/env python3
"""Apply a PdaLink Local Hotspot snapshot on a managed Raspberry Pi target."""

from __future__ import annotations

import configparser
import ipaddress
import io
import json
import os
import re
import subprocess
import sys
import time
import uuid
from dataclasses import dataclass
from pathlib import Path


HERE = Path(__file__).resolve().parent
for candidate in (HERE, Path("/opt/pdalink/linux")):
    if str(candidate) not in sys.path:
        sys.path.insert(0, str(candidate))

import pdalink_lite  # noqa: E402


MAX_PAYLOAD_BYTES = 8192
DEFAULT_PROFILE = "pdalink-local-hotspot"
DEFAULT_INTERFACE = "wlan0"
DEFAULT_SERVICE = "pdalink-client.service"


class TargetApplyError(RuntimeError):
    pass


@dataclass(frozen=True)
class TargetPaths:
    environment: Path = Path("/etc/pdalink/client.env")
    state: Path = Path("/var/lib/pdalink/target-state.json")
    network_connections: Path = Path("/etc/NetworkManager/system-connections")


@dataclass(frozen=True)
class CommandResult:
    returncode: int
    stdout: str = ""
    stderr: str = ""


class CommandRunner:
    def run(self, command: list[str], timeout: float) -> CommandResult:
        try:
            result = subprocess.run(
                command,
                text=True,
                capture_output=True,
                check=False,
                timeout=timeout,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            return CommandResult(1, "", str(exc))
        return CommandResult(result.returncode, result.stdout, result.stderr)


def parse_payload(raw: str) -> pdalink_lite.LocalHotspotCredentials:
    if len(raw.encode("utf-8")) > MAX_PAYLOAD_BYTES:
        raise ValueError("payload is too large")
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError("payload is not valid JSON") from exc
    if not isinstance(payload, dict):
        raise ValueError("payload must be an object")
    allowed = {
        "version", "transport", "ssid", "password", "interface",
        "host", "port", "security",
    }
    if set(payload) - allowed:
        raise ValueError("payload contains unsupported fields")
    if payload.get("version") != 1 or payload.get("transport") != "local-hotspot":
        raise ValueError("unsupported payload version or transport")

    ssid = str(payload.get("ssid") or "")
    password = str(payload.get("password") or "")
    interface = str(payload.get("interface") or "")
    host = str(payload.get("host") or "")
    security = str(payload.get("security") or "")
    try:
        port = int(payload.get("port"))
        address = ipaddress.ip_address(host)
    except (TypeError, ValueError) as exc:
        raise ValueError("invalid proxy endpoint") from exc
    if not ssid or len(ssid.encode("utf-8")) > 128:
        raise ValueError("invalid SSID")
    if not 8 <= len(password) <= 63 \
            or any(not 0x20 <= ord(char) <= 0x7e for char in password):
        raise ValueError("invalid Wi-Fi password")
    if interface and not re.fullmatch(r"[A-Za-z0-9_.:-]{1,32}", interface):
        raise ValueError("invalid Android interface")
    if not isinstance(address, ipaddress.IPv4Address) or not address.is_private \
            or address.is_loopback or address.is_link_local or address.is_unspecified:
        raise ValueError("proxy host must be a private IPv4 address")
    if not 1 <= port <= 65535:
        raise ValueError("invalid proxy port")
    pdalink_lite.normalize_connection_security(security)
    return pdalink_lite.LocalHotspotCredentials(
        ssid=ssid,
        password=password,
        interface=interface,
        host=host,
        port=port,
        security=security,
    )


def parse_ssid_frequencies(raw: str, ssid: str) -> list[int]:
    frequencies: list[int] = []
    for line in raw.splitlines():
        fields = pdalink_lite.split_nmcli_terse(line)
        if len(fields) != 2 or fields[0] != ssid:
            continue
        match = re.fullmatch(r"(\d+)(?:\s+MHz)?", fields[1].strip())
        if match:
            frequencies.append(int(match.group(1)))
    return frequencies


def credential_fingerprint(credentials: pdalink_lite.LocalHotspotCredentials) -> str:
    return pdalink_lite.local_hotspot_credential_fingerprint(credentials)


def atomic_write(path: Path, text: str, mode: int = 0o600) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(text, encoding="utf-8")
    temporary.chmod(mode)
    temporary.replace(path)
    path.chmod(mode)


def load_state(path: Path) -> dict[str, object]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


class TargetApplier:
    def __init__(
            self,
            paths: TargetPaths = TargetPaths(),
            runner: CommandRunner | None = None,
            profile: str = DEFAULT_PROFILE,
            interface: str = DEFAULT_INTERFACE,
            service: str = DEFAULT_SERVICE,
            sleeper=time.sleep) -> None:
        self.paths = paths
        self.runner = runner or CommandRunner()
        self.profile = profile
        self.interface = interface
        self.service = service
        self.sleeper = sleeper

    def run_checked(self, command: list[str], timeout: float, label: str) -> CommandResult:
        result = self.runner.run(command, timeout)
        if result.returncode != 0:
            detail = (result.stderr or result.stdout).strip() or f"exit {result.returncode}"
            raise TargetApplyError(f"{label}: {detail}")
        return result

    def require_24_ghz(self, ssid: str) -> int:
        result = self.run_checked([
            "nmcli", "-t", "--escape", "yes", "-f", "SSID,FREQ",
            "device", "wifi", "list", "ifname", self.interface, "--rescan", "yes",
        ], 15.0, "Wi-Fi scan failed")
        frequencies = parse_ssid_frequencies(result.stdout, ssid)
        compatible = [frequency for frequency in frequencies if 2400 <= frequency < 2500]
        if compatible:
            return compatible[0]
        if frequencies:
            raise TargetApplyError("Local Hotspot is not using the required 2.4 GHz band")
        raise TargetApplyError("Local Hotspot SSID was not visible on wlan0")

    def connection_uuid(self) -> str:
        result = self.run_checked([
            "nmcli", "-g", "connection.uuid",
            "connection", "show", self.profile,
        ], 5.0, "NetworkManager profile UUID lookup failed")
        value = result.stdout.strip()
        try:
            return str(uuid.UUID(value))
        except ValueError as exc:
            raise TargetApplyError("NetworkManager returned an invalid profile UUID") from exc

    def keyfile_config(self, raw: str = "") -> configparser.ConfigParser:
        config = configparser.ConfigParser(
            interpolation=None,
            strict=False,
            empty_lines_in_values=False,
        )
        config.optionxform = str
        if raw:
            try:
                config.read_string(raw)
            except configparser.Error as exc:
                raise TargetApplyError("NetworkManager profile keyfile is invalid") from exc
        return config

    def profile_keyfile(self, profile_uuid: str) -> Path:
        try:
            candidates = sorted(self.paths.network_connections.glob("*.nmconnection"))
        except OSError as exc:
            raise TargetApplyError("NetworkManager profile directory is unavailable") from exc
        for candidate in candidates:
            try:
                config = self.keyfile_config(candidate.read_text(encoding="utf-8"))
            except (OSError, TargetApplyError):
                continue
            if config.get("connection", "uuid", fallback="") == profile_uuid:
                return candidate
        safe_name = re.sub(r"[^A-Za-z0-9_.-]", "_", self.profile)
        return self.paths.network_connections / f"{safe_name}.nmconnection"

    def persistent_profile_ready(self) -> bool:
        try:
            profile_uuid = self.connection_uuid()
            path = self.profile_keyfile(profile_uuid)
            config = self.keyfile_config(path.read_text(encoding="utf-8"))
        except (OSError, TargetApplyError):
            return False
        return (
            config.getboolean("connection", "autoconnect", fallback=False)
            and config.get("connection", "autoconnect-retries", fallback="") == "0"
            and config.get("wifi-security", "psk-flags", fallback="") == "0"
            and bool(config.get("wifi-security", "psk", fallback=""))
        )

    def write_persistent_profile(
            self,
            credentials: pdalink_lite.LocalHotspotCredentials) -> tuple[Path, bytes | None]:
        profile_uuid = self.connection_uuid()
        path = self.profile_keyfile(profile_uuid)
        try:
            previous = path.read_bytes()
        except FileNotFoundError:
            previous = None
        except OSError as exc:
            raise TargetApplyError("NetworkManager profile could not be read") from exc

        config = self.keyfile_config(
            previous.decode("utf-8") if previous is not None else ""
        )
        for section in ("connection", "wifi", "wifi-security", "ipv4", "ipv6"):
            if not config.has_section(section):
                config.add_section(section)

        config["connection"].update({
            "id": self.profile,
            "uuid": profile_uuid,
            "type": "wifi",
            "interface-name": self.interface,
            "autoconnect": "true",
            "autoconnect-retries": "0",
        })
        config["wifi"].update({
            "mode": "infrastructure",
            "ssid": credentials.ssid,
            "cloned-mac-address": "permanent",
        })
        config["wifi-security"].update({
            "key-mgmt": "sae" if credentials.security == "WPA3-SAE" else "wpa-psk",
            "psk": credentials.password,
            "psk-flags": "0",
        })
        config["ipv4"].update({
            "method": "auto",
            "dns": "1.1.1.1;8.8.8.8;",
            "dns-priority": "-50",
            "never-default": "true",
            "ignore-auto-dns": "true",
            "route-metric": "600",
        })
        config["ipv6"]["method"] = "ignore"

        output = io.StringIO()
        config.write(output, space_around_delimiters=False)
        try:
            self.paths.network_connections.mkdir(parents=True, exist_ok=True, mode=0o700)
            atomic_write(path, output.getvalue())
            self.run_checked(
                ["nmcli", "connection", "reload"],
                10.0,
                "NetworkManager profile reload failed",
            )
        except (OSError, TargetApplyError):
            self.restore_profile_keyfile(path, previous)
            raise
        return path, previous

    def restore_profile_keyfile(self, path: Path, previous: bytes | None) -> None:
        try:
            if previous is None:
                path.unlink(missing_ok=True)
            else:
                atomic_write(path, previous.decode("utf-8"))
        finally:
            self.runner.run(["nmcli", "connection", "reload"], 10.0)

    def target_is_ready(self, fingerprint: str) -> bool:
        state = load_state(self.paths.state)
        if state.get("credential_sha256") != fingerprint:
            return False
        active = self.runner.run(
            ["nmcli", "-t", "-f", "NAME,DEVICE", "connection", "show", "--active"],
            5.0,
        )
        service = self.runner.run(["systemctl", "is-active", "--quiet", self.service], 5.0)
        if active.returncode != 0 or service.returncode != 0:
            return False
        return self.persistent_profile_ready() and any(
            fields == [self.profile, self.interface]
            for fields in (
                pdalink_lite.split_nmcli_terse(line)
                for line in active.stdout.splitlines()
            )
        )

    def restore_environment(self, previous: bytes | None) -> None:
        if previous is None:
            self.paths.environment.unlink(missing_ok=True)
        else:
            atomic_write(self.paths.environment, previous.decode("utf-8"))
        self.runner.run(["systemctl", "restart", self.service], 30.0)

    def apply(self, credentials: pdalink_lite.LocalHotspotCredentials) -> str:
        fingerprint = credential_fingerprint(credentials)
        if self.target_is_ready(fingerprint):
            return "unchanged"

        frequency = self.require_24_ghz(credentials.ssid)
        profile = pdalink_lite.prepare_local_hotspot_profile(
            credentials,
            self.profile,
            self.interface,
        )
        if profile is None:
            raise TargetApplyError("NetworkManager profile update failed")
        self.run_checked([
            "nmcli", "connection", "modify", profile,
            "ipv4.dns", "1.1.1.1,8.8.8.8",
            "ipv4.dns-priority", "-50",
        ], 5.0, "NetworkManager DNS update failed")
        profile_path: Path | None = None
        previous_profile: bytes | None = None
        try:
            profile_path, previous_profile = self.write_persistent_profile(credentials)
            self.run_checked([
                "nmcli", "connection", "up", profile,
                "ifname", self.interface,
            ], 30.0, "Local Hotspot activation failed")

            probe = pdalink_lite.first_ok(
                pdalink_lite.probe_ports(credentials.host, (credentials.port,))
            )
            if probe is None:
                raise TargetApplyError("Android proxy did not answer after Wi-Fi activation")
        except (OSError, TargetApplyError):
            if profile_path is not None:
                self.restore_profile_keyfile(profile_path, previous_profile)
            raise

        try:
            previous_environment = self.paths.environment.read_bytes()
        except FileNotFoundError:
            previous_environment = None
        proxy_url = f"http://{credentials.host}:{credentials.port}"
        try:
            atomic_write(self.paths.environment, f"PDALINK_PROXY_URL={proxy_url}\n")
            self.run_checked(
                ["systemctl", "restart", self.service],
                30.0,
                "PdaLink client restart failed",
            )
            self.sleeper(2.0)
            self.run_checked(
                ["systemctl", "is-active", "--quiet", self.service],
                10.0,
                "PdaLink client is not active",
            )
        except (OSError, TargetApplyError):
            self.restore_environment(previous_environment)
            raise

        atomic_write(self.paths.state, json.dumps({
            "version": 1,
            "credential_sha256": fingerprint,
            "ssid": credentials.ssid,
            "proxy_url": proxy_url,
            "frequency_mhz": frequency,
            "profile": profile,
        }, indent=2, sort_keys=True) + "\n")
        return "updated"


def main() -> int:
    if os.geteuid() != 0:
        print("pdalink-target-apply must run as root", file=sys.stderr)
        return 1
    raw = sys.stdin.read(MAX_PAYLOAD_BYTES + 1)
    try:
        credentials = parse_payload(raw)
        status = TargetApplier().apply(credentials)
    except (OSError, TargetApplyError, ValueError) as exc:
        print(f"target apply failed: {exc}", file=sys.stderr)
        return 1
    print(f"target: {status}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
