#!/usr/bin/env python3
"""Native Linux USB watcher for the PdaLink data path."""

from __future__ import annotations

import argparse
import json
import os
import queue
import signal
import shutil
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
import subprocess
import sys
import time
from typing import Callable, Iterable
from urllib.error import URLError
from urllib.request import ProxyHandler, build_opener, urlopen


ROOT = Path(__file__).resolve().parents[1]
RUNTIME_DIR = ROOT / ".tools" / "runtime"
DEFAULT_STATE_FILE = RUNTIME_DIR / "pdalink-watch.json"
DEFAULT_LOCAL_PORT = int(os.getenv("PDALINK_USB_LOCAL_PORT", "18080"))
DIRECT_PROXY_URL = "http://192.168.49.1:8001"
DEFAULT_REMOTE_PORT = int(os.getenv("PDALINK_USB_REMOTE_PORT", "8000"))
ANDROID_DEBUG_ACTIVITY = "dev.pdalink.prototype/.DebugControlActivity"
ANDROID_PROXY_SERVICE = "dev.pdalink.prototype/.ProxyService"
ANDROID_ACTION_START = "dev.pdalink.prototype.START_USB"
ANDROID_ACTION_STOP_USB = "dev.pdalink.prototype.STOP_USB"

sys.path.insert(0, str(ROOT / "linux"))
import pdalink_lite  # noqa: E402

try:
    import pdalink_dbus  # noqa: E402
except ImportError:
    pdalink_dbus = None


@dataclass(frozen=True)
class CommandResult:
    returncode: int
    stdout: str = ""
    stderr: str = ""


class SubprocessRunner:
    def run(self, command: list[str], timeout: float) -> CommandResult:
        try:
            result = subprocess.run(command, text=True, capture_output=True, check=False, timeout=timeout)
        except subprocess.TimeoutExpired as exc:
            stdout = exc.stdout if isinstance(exc.stdout, str) else ""
            stderr = exc.stderr if isinstance(exc.stderr, str) else ""
            return CommandResult(124, stdout, stderr or f"timeout after {timeout}s")
        except OSError as exc:
            return CommandResult(127, "", str(exc))
        return CommandResult(result.returncode, result.stdout, result.stderr)


class StopRequested(Exception):
    """Raised when the watcher receives a process termination signal."""


@dataclass
class WatchState:
    mode: str = "init"
    device_serial: str = ""
    device_state: str = ""
    forward_owned: bool = False
    service_owned: bool = False
    local_port: int = DEFAULT_LOCAL_PORT
    remote_port: int = DEFAULT_REMOTE_PORT
    proxy_url: str = ""
    last_error: str = ""
    last_error_code: str = ""
    last_transition_at: str = ""
    last_success_at: str = ""
    last_resume_at: str = ""
    hotspot_credential_fingerprint: str = ""
    hotspot_relay_fingerprint: str = ""
    health: dict[str, str] = field(default_factory=dict)
    updated_at: str = ""

    @classmethod
    def load(cls, path: Path) -> "WatchState":
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return cls()
        return cls(
            mode=str(payload.get("mode") or "init"),
            device_serial=str(payload.get("device_serial") or ""),
            device_state=str(payload.get("device_state") or ""),
            forward_owned=bool(payload.get("forward_owned")),
            service_owned=bool(payload.get("service_owned")),
            local_port=int(payload.get("local_port") or DEFAULT_LOCAL_PORT),
            remote_port=int(payload.get("remote_port") or DEFAULT_REMOTE_PORT),
            proxy_url=str(payload.get("proxy_url") or ""),
            last_error=str(payload.get("last_error") or ""),
            last_error_code=str(payload.get("last_error_code") or ""),
            last_transition_at=str(payload.get("last_transition_at") or payload.get("updated_at") or ""),
            last_success_at=str(payload.get("last_success_at") or ""),
            last_resume_at=str(payload.get("last_resume_at") or ""),
            hotspot_credential_fingerprint=str(payload.get("hotspot_credential_fingerprint") or ""),
            hotspot_relay_fingerprint=str(payload.get("hotspot_relay_fingerprint") or ""),
            health={
                str(key): str(value)
                for key, value in (payload.get("health") or {}).items()
            } if isinstance(payload.get("health"), dict) else {},
            updated_at=str(payload.get("updated_at") or ""),
        )

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.updated_at = datetime.now(timezone.utc).isoformat()
        path.write_text(json.dumps(self.__dict__, indent=2, sort_keys=True) + "\n", encoding="utf-8")


@dataclass(frozen=True)
class WatchConfig:
    adb: str
    transport: str = "adb"
    local_port: int = DEFAULT_LOCAL_PORT
    remote_port: int = DEFAULT_REMOTE_PORT
    proxy_url: str = ""
    poll_interval: float = 2.0
    verify_timeout: float = 15.0
    state_file: Path = DEFAULT_STATE_FILE
    root: Path = ROOT
    no_start_android: bool = False
    no_stop_android: bool = False
    no_system_service: bool = False
    dry_run: bool = False
    no_dns: bool = False
    tcp_idle_timeout: float | None = 0.0
    verbose_system: bool = False
    system_arg: tuple[str, ...] = field(default_factory=tuple)
    require_trusted: bool = True
    trusted_devices_file: Path | None = None
    config_file: Path | None = None
    product_usb_state_file: Path | None = None
    enable_dbus: bool = True
    # Seconds between end-to-end internet checks through the proxy while the
    # path is ready. 0 disables them (the library default, so tests and dry
    # runs never reach the network); the CLI enables them by default.
    internet_check_interval: float = 0.0
    internet_check_timeout: float = 6.0
    wifi_direct_failover: bool = False
    usb_return_delay: float = 20.0
    transport_min_dwell: float = 30.0

    @property
    def effective_proxy_url(self) -> str:
        if self.proxy_url:
            return self.proxy_url
        return f"http://127.0.0.1:{self.local_port}"


@dataclass(frozen=True)
class ForwardCheck:
    ok: bool
    owned: bool
    conflict: str = ""


@dataclass(frozen=True)
class ProxyCheck:
    ok: bool
    detail: str


@dataclass(frozen=True)
class ServiceCheck:
    running: bool
    healthy: bool
    detail: str


def boottime() -> float:
    clock = getattr(time, "CLOCK_BOOTTIME", None)
    if clock is None:
        return time.monotonic()
    return time.clock_gettime(clock)


def safe_detail(raw: str, fallback: str = "") -> str:
    lines = [line.strip() for line in raw.splitlines() if line.strip()]
    detail = lines[-1] if lines else fallback
    return detail[:500]


def parse_credential_fingerprint(raw: str) -> str:
    prefix = "credential-fingerprint: "
    for line in raw.splitlines():
        if line.startswith(prefix):
            value = line[len(prefix):].strip()
            if len(value) == 64 and all(char in "0123456789abcdef" for char in value):
                return value
    return ""


def default_adb_path() -> str:
    env = os.getenv("ADB")
    if env:
        return env
    repo_adb = ROOT / "tools" / "adb.sh"
    if repo_adb.exists() and os.access(repo_adb, os.X_OK):
        return str(repo_adb)
    adb = shutil.which("adb")
    return adb or str(repo_adb)


def shell_script_command(path: Path) -> list[str]:
    if os.access(path, os.X_OK):
        return [str(path)]
    return ["bash", str(path)]


def probe_proxy(proxy_url: str, timeout: float) -> ProxyCheck:
    url = proxy_url.rstrip("/") + "/proxy.pac"
    deadline = time.monotonic() + max(0.0, timeout)
    last_error = "not attempted"
    while True:
        try:
            with urlopen(url, timeout=min(2.0, max(0.1, timeout))) as response:
                if 200 <= response.status < 300:
                    return ProxyCheck(True, f"{url} HTTP {response.status}")
                last_error = f"{url} HTTP {response.status}"
        except (OSError, URLError) as exc:
            last_error = str(exc)
        if time.monotonic() >= deadline:
            return ProxyCheck(False, last_error)
        time.sleep(0.25)


INTERNET_CHECK_URL = "http://connectivitycheck.gstatic.com/generate_204"
INTERNET_FAILING_RECHECK = 15.0


def probe_internet(proxy_url: str, timeout: float) -> ProxyCheck:
    """Fetch a 204 endpoint through the PdaLink proxy to prove phone egress works."""
    opener = build_opener(ProxyHandler({"http": proxy_url, "https": proxy_url}))
    started = time.monotonic()
    try:
        with opener.open(INTERNET_CHECK_URL, timeout=max(0.5, timeout)) as response:
            elapsed_ms = int((time.monotonic() - started) * 1000)
            if response.status == 204:
                return ProxyCheck(True, f"HTTP 204 in {elapsed_ms} ms")
            return ProxyCheck(False, f"unexpected HTTP {response.status} in {elapsed_ms} ms")
    except (OSError, URLError) as exc:
        elapsed_ms = int((time.monotonic() - started) * 1000)
        return ProxyCheck(False, f"{exc} after {elapsed_ms} ms")


class PdaLinkWatcher:
    def __init__(
        self,
        config: WatchConfig,
        runner: SubprocessRunner | None = None,
        probe: Callable[[str, float], ProxyCheck] = probe_proxy,
        internet_probe: Callable[[str, float], ProxyCheck] = probe_internet,
        sleeper: Callable[[float], None] = time.sleep,
        boot_clock: Callable[[], float] = boottime,
        stdout = sys.stdout,
    ) -> None:
        self.config = config
        self.runner = runner or SubprocessRunner()
        self.probe = probe
        self.internet_probe = internet_probe
        self.internet_checked_at: float | None = None
        self.internet_health = ""
        self.internet_failures = 0
        self.sleeper = sleeper
        self.boot_clock = boot_clock
        self.stdout = stdout
        self.state = WatchState.load(config.state_file)
        self.active_proxy_url = (self.state.proxy_url if self.state.service_owned and self.state.proxy_url
                                 else config.effective_proxy_url)
        self.transport_since = self.boot_clock() if config.wifi_direct_failover else 0.0
        self.usb_healthy_since: float | None = None
        self.local_hotspot_sync_after = 0.0
        self.local_hotspot_sync_failures = 0
        self.target_relay_after = 0.0
        self.target_relay_failures = 0
        self.force_hotspot_start = False
        self.action_queue: queue.SimpleQueue[str] = queue.SimpleQueue()
        self.wake_event = threading.Event()
        self.dbus_server = None

    def log(self, message: str) -> None:
        print(message, file=self.stdout, flush=True)

    def adb_command(self, args: Iterable[str], serial: str = "") -> list[str]:
        command = [self.config.adb]
        if serial:
            command.extend(["-s", serial])
        command.extend(args)
        return command

    def run_read(self, command: list[str], timeout: float) -> CommandResult:
        return self.runner.run(command, timeout)

    def run_write(self, command: list[str], timeout: float) -> CommandResult:
        if self.config.dry_run:
            self.log("dry-run: " + " ".join(command))
            return CommandResult(0, "", "")
        return self.runner.run(command, timeout)

    def state_signature(self) -> tuple[object, ...]:
        return (
            self.state.mode,
            self.state.device_serial,
            self.state.device_state,
            self.state.forward_owned,
            self.state.service_owned,
            self.state.local_port,
            self.state.remote_port,
            self.state.proxy_url,
            self.state.last_error,
            self.state.last_error_code,
            self.state.hotspot_credential_fingerprint,
            self.state.hotspot_relay_fingerprint,
            tuple(sorted(self.state.health.items())),
        )

    def save_state(
        self,
        mode: str,
        device: pdalink_lite.AdbDevice | None = None,
        error: str = "",
        error_code: str = "",
        health: dict[str, str] | None = None,
        force: bool = False,
    ) -> bool:
        before = self.state_signature()
        previous_mode = self.state.mode
        self.state.mode = mode
        self.state.device_serial = device.serial if device else ""
        self.state.device_state = device.state if device else ""
        self.state.local_port = self.config.local_port
        self.state.remote_port = self.config.remote_port
        self.state.proxy_url = self.active_proxy_url
        self.state.last_error = error
        self.state.last_error_code = error_code
        if health is not None:
            self.state.health = dict(health)
        self.state.health["transport"] = self.active_transport
        changed = before != self.state_signature()
        if not changed and not force:
            return False
        now = datetime.now(timezone.utc).isoformat()
        self.state.last_transition_at = now
        if mode == "ready" and previous_mode != "ready":
            self.state.last_success_at = now
        self.state.save(self.config.state_file)
        if self.dbus_server is not None:
            self.dbus_server.emit_state_changed(self.state.__dict__)
        return True

    def enqueue_action(self, action: str) -> None:
        self.action_queue.put(action)
        self.wake_event.set()

    def update_user_config(self, **values: str) -> tuple[bool, str]:
        path = self.config.config_file or pdalink_lite.default_config_path()
        try:
            config = pdalink_lite.load_user_config(path)
            config.update(values)
            pdalink_lite.write_user_config(path, config)
        except (OSError, ValueError) as exc:
            return False, str(exc)
        return True, ""

    def process_actions(self) -> tuple[bool, str]:
        while True:
            try:
                action = self.action_queue.get_nowait()
            except queue.Empty:
                return True, ""
            if action == "retry":
                continue
            if action == "resume":
                self.handle_resume()
                continue
            if action == "disconnect":
                ok, error = self.update_user_config(auto_connect="off")
            elif action == "connect-usb":
                ok, error = self.update_user_config(auto_connect="on_trusted_connect")
            elif action == "connect-hotspot":
                ok, error = self.update_user_config(
                    auto_connect="on_trusted_connect",
                    local_hotspot_credentials="auto",
                    local_hotspot_join="if_disconnected",
                )
                if ok:
                    self.force_hotspot_start = True
            else:
                ok, error = False, f"unknown control action: {action}"
            if not ok:
                return False, error

    def handle_resume(self) -> None:
        self.usb_healthy_since = None  # Sleep is not evidence of continuous USB health.
        now = datetime.now(timezone.utc).isoformat()
        self.state.last_resume_at = now
        self.local_hotspot_sync_after = 0.0
        self.local_hotspot_sync_failures = 0
        self.target_relay_after = 0.0
        self.target_relay_failures = 0
        if self.save_state(
            "recovering-resume",
            self.previous_device(),
            health={"resume": "checking"},
            force=True,
        ):
            self.log("recovering-resume: validating transport, proxy, route, and DNS")

    def previous_device(self) -> pdalink_lite.AdbDevice | None:
        if not self.state.device_serial:
            return None
        return pdalink_lite.AdbDevice(self.state.device_serial, self.state.device_state or "device")

    def list_devices(self) -> tuple[list[pdalink_lite.AdbDevice], CommandResult]:
        result = self.run_read(self.adb_command(["devices", "-l"]), timeout=5.0)
        if result.returncode != 0:
            return [], result
        return pdalink_lite.parse_adb_devices(result.stdout), result

    def user_config(self) -> dict[str, str]:
        try:
            return pdalink_lite.load_user_config(self.config.config_file)
        except ValueError as exc:
            self.log(f"user config: invalid ({exc})")
            return pdalink_lite.default_user_config()

    def choose_device(self, devices: list[pdalink_lite.AdbDevice]) -> pdalink_lite.AdbDevice | None:
        ready = [device for device in devices if device.ok]
        if not self.config.require_trusted:
            return ready[0] if ready else None
        try:
            store = pdalink_lite.load_trusted_devices(self.config.trusted_devices_file)
        except ValueError as exc:
            self.log(f"trusted devices: invalid ({exc})")
            return None
        trusted = [device for device in ready if device.serial in store.devices]
        if not trusted:
            return None
        if store.default_serial:
            for device in trusted:
                if device.serial == store.default_serial:
                    return device
            return None
        if len(trusted) == 1:
            return trusted[0]
        return None

    def trust_block_reason(self, devices: list[pdalink_lite.AdbDevice]) -> str:
        ready = [device for device in devices if device.ok]
        if not ready:
            return ""
        if not self.config.require_trusted:
            return ""
        try:
            store = pdalink_lite.load_trusted_devices(self.config.trusted_devices_file)
        except ValueError as exc:
            return f"trusted devices invalid: {exc}"
        trusted = [device for device in ready if device.serial in store.devices]
        if not store.devices:
            return "no trusted Android device configured"
        if not trusted:
            return "authorized device is not trusted"
        if store.default_serial and all(device.serial != store.default_serial for device in trusted):
            return f"default trusted device {store.default_serial} is not connected"
        if len(trusted) > 1 and not store.default_serial:
            return "multiple trusted devices connected; set a default"
        return ""

    def forward_check(self, serial: str) -> ForwardCheck:
        result = self.run_read(self.adb_command(["forward", "--list"]), timeout=5.0)
        if result.returncode != 0:
            return ForwardCheck(False, False, (result.stderr or result.stdout).strip() or "adb forward --list failed")

        for forward in pdalink_lite.parse_adb_forward_list(result.stdout):
            if forward.local_port != self.config.local_port:
                continue
            expected_remote = f"tcp:{self.config.remote_port}"
            expected_local = f"tcp:{self.config.local_port}"
            if forward.serial == serial and forward.local == expected_local and forward.remote == expected_remote:
                owned = (
                    self.state.forward_owned
                    and self.state.device_serial == serial
                    and self.state.local_port == self.config.local_port
                    and self.state.remote_port == self.config.remote_port
                )
                return ForwardCheck(True, owned)
            return ForwardCheck(
                False,
                False,
                f"tcp:{self.config.local_port} is already forwarded to {forward.remote} for {forward.serial}",
            )
        return ForwardCheck(False, False)

    def ensure_forward(self, device: pdalink_lite.AdbDevice) -> tuple[bool, str]:
        check = self.forward_check(device.serial)
        if check.conflict:
            return False, check.conflict
        if check.ok:
            self.state.forward_owned = check.owned
            return True, ""

        command = self.adb_command(
            ["forward", f"tcp:{self.config.local_port}", f"tcp:{self.config.remote_port}"],
            serial=device.serial,
        )
        result = self.run_write(command, timeout=10.0)
        if result.returncode != 0:
            return False, (result.stderr or result.stdout).strip() or "adb forward failed"
        self.state.forward_owned = not self.config.dry_run
        return True, ""

    def start_android(self, device: pdalink_lite.AdbDevice) -> tuple[bool, str]:
        if self.config.no_start_android:
            return True, ""
        result = self.run_write(
            self.adb_command(
                ["shell", "am", "start-foreground-service", "-n", ANDROID_PROXY_SERVICE, "-a", ANDROID_ACTION_START],
                serial=device.serial,
            ),
            timeout=10.0,
        )
        if result.returncode != 0:
            return False, (result.stderr or result.stdout).strip() or "failed to start Android proxy"
        return True, ""

    def stop_android(self, device: pdalink_lite.AdbDevice | None) -> None:
        if self.config.no_stop_android or device is None:
            return
        self.run_write(
            self.adb_command(
                ["shell", "am", "start-foreground-service", "-n", ANDROID_PROXY_SERVICE, "-a", ANDROID_ACTION_STOP_USB],
                serial=device.serial,
            ),
            timeout=10.0,
        )

    def service_args(self, command: str) -> list[str]:
        user_config = self.user_config()
        args = shell_script_command(self.config.root / "tools" / "pdalink.sh")
        args.extend(["system", "service", command])
        args.extend(["--proxy-url", self.active_proxy_url])
        args.extend(["--route-mode", user_config["route_mode"]])
        args.extend(["--dns-mode", user_config["dns_mode"]])
        args.extend(self.config.system_arg)
        if command == "start":
            if self.config.no_dns:
                args.append("--no-dns")
            if self.config.tcp_idle_timeout is not None:
                args.extend(["--tcp-idle-timeout", str(self.config.tcp_idle_timeout)])
            if self.config.verbose_system:
                args.append("--verbose")
            if self.config.transport == "product-usb":
                state_path = self.config.product_usb_state_file or pdalink_lite.default_product_usb_state_path()
                args.extend(["--require-product-usb", "--product-usb-state-file", str(state_path)])
        return args

    def service_check(self) -> ServiceCheck:
        result = self.run_read(self.service_args("status"), timeout=10.0)
        running = "wrapper: RUNNING" in result.stdout
        saved_proxy = next((line.removeprefix("state-proxy: ").strip()
                            for line in result.stdout.splitlines()
                            if line.startswith("state-proxy: ")), "")
        healthy = (running and result.returncode == 0
                   and "upstream-device: UNBOUND" not in result.stdout
                   and (not saved_proxy or saved_proxy == self.active_proxy_url))
        detail = safe_detail(result.stderr or result.stdout, "system service status unavailable")
        return ServiceCheck(running, healthy, detail)

    def service_running(self) -> bool:
        return self.service_check().running

    def start_service(self) -> tuple[bool, str]:
        if self.config.no_system_service:
            return True, ""
        check = self.service_check()
        if check.healthy:
            self.state.service_owned = bool(self.state.service_owned)
            return True, ""
        if check.running:
            if not self.state.service_owned:
                return False, "system service is running but unhealthy and is not owned by this watcher"
            result = self.run_write(self.service_args("stop"), timeout=30.0)
            if result.returncode != 0:
                return False, safe_detail(
                    result.stderr or result.stdout,
                    "unhealthy system service could not be stopped safely",
                )
            self.state.service_owned = False
        result = self.run_write(self.service_args("start"), timeout=30.0)
        if result.returncode != 0:
            return False, (result.stderr or result.stdout).strip() or "system service start failed"
        self.state.service_owned = not self.config.dry_run
        return True, ""

    def hotspot_backoff(self, failures: int) -> float:
        return (5.0, 15.0, 60.0, 300.0)[min(max(failures - 1, 0), 3)]

    def sync_local_hotspot_credentials(
            self,
            device: pdalink_lite.AdbDevice,
            user_config: dict[str, str]) -> dict[str, str]:
        health = {
            "hotspot": self.state.health.get("hotspot", "off"),
            "hotspot_target": self.state.health.get("hotspot_target", "off"),
        }
        mode = user_config.get("local_hotspot_credentials", "off")
        if mode == "off":
            return {"hotspot": "off", "hotspot_target": "off"}
        if time.monotonic() < self.local_hotspot_sync_after:
            return health
        command = [
            sys.executable,
            str(self.config.root / "linux" / "pdalink_lite.py"),
            "local-hotspot-sync",
            "--mode", mode,
            "--serial", device.serial,
            "--wait", "15" if self.force_hotspot_start else "0",
        ]
        if self.force_hotspot_start:
            command.append("--start-android")
        else:
            command.append("--if-active")
        join_policy = user_config.get("local_hotspot_join", "off")
        if self.force_hotspot_start or self.should_join_local_hotspot(join_policy):
            command.append("--connect")
        result = self.run_write(command, timeout=60.0)
        inactive = result.returncode == 0 and "inactive" in result.stdout
        self.force_hotspot_start = False
        if result.returncode != 0:
            self.local_hotspot_sync_failures += 1
            delay = self.hotspot_backoff(self.local_hotspot_sync_failures)
            self.local_hotspot_sync_after = time.monotonic() + delay
            detail = safe_detail(result.stderr or result.stdout, f"exit {result.returncode}")
            health["hotspot"] = f"error: {detail}"
            self.log(f"local-hotspot-sync: {detail}; retry in {int(delay)}s; USB remains active")
            return health

        self.local_hotspot_sync_failures = 0
        self.local_hotspot_sync_after = time.monotonic() + (30.0 if not inactive else 15.0)
        if inactive:
            health["hotspot"] = "inactive"
            return health

        fingerprint = parse_credential_fingerprint(result.stdout)
        if fingerprint:
            self.state.hotspot_credential_fingerprint = fingerprint
        health["hotspot"] = "ready"
        target = user_config.get("local_hotspot_target", "off")
        if target == "off":
            return health
        if fingerprint and fingerprint == self.state.hotspot_relay_fingerprint:
            health["hotspot_target"] = "ready"
            return health
        if time.monotonic() < self.target_relay_after:
            health["hotspot_target"] = "waiting-retry"
            return health

        relay_command = [
            sys.executable,
            str(self.config.root / "linux" / "pdalink_lite.py"),
            "local-hotspot-target-sync",
            "--serial", device.serial,
            "--target", target,
        ]
        relay = self.run_write(relay_command, timeout=50.0)
        if relay.returncode != 0:
            self.target_relay_failures += 1
            delay = self.hotspot_backoff(self.target_relay_failures)
            self.target_relay_after = time.monotonic() + delay
            self.local_hotspot_sync_after = min(self.local_hotspot_sync_after, self.target_relay_after)
            detail = safe_detail(relay.stderr or relay.stdout, f"exit {relay.returncode}")
            health["hotspot_target"] = f"error: {detail}"
            self.log(f"local-hotspot-target: {detail}; retry in {int(delay)}s")
            return health

        self.target_relay_failures = 0
        self.target_relay_after = 0.0
        if fingerprint:
            self.state.hotspot_relay_fingerprint = fingerprint
        health["hotspot_target"] = "ready"
        return health

    def should_join_local_hotspot(self, policy: str) -> bool:
        if policy == "off":
            return False
        if policy == "always":
            return True
        if policy != "if_disconnected":
            self.log(f"local-hotspot join: invalid policy {policy!r}")
            return False
        wifi_connections = [
            connection for connection in pdalink_lite.active_nm_connections()
            if connection.type == "802-11-wireless"
        ]
        return not wifi_connections or all(
            connection.name == pdalink_lite.LOCAL_HOTSPOT_PROFILE_NAME
            for connection in wifi_connections
        )

    def stop_service(self) -> None:
        if self.config.no_system_service or not self.state.service_owned:
            return
        result = self.run_write(self.service_args("stop"), timeout=30.0)
        if result.returncode == 0:
            self.state.service_owned = False

    def remove_forward(self, device: pdalink_lite.AdbDevice | None = None) -> None:
        if not self.state.forward_owned:
            return
        result = self.run_write(
            self.adb_command(
                ["forward", "--remove", f"tcp:{self.config.local_port}"],
                serial=device.serial if device else self.state.device_serial,
            ),
            timeout=10.0,
        )
        if result.returncode == 0:
            self.state.forward_owned = False

    def cleanup(
        self,
        mode: str,
        device: pdalink_lite.AdbDevice | None,
        error: str = "",
        error_code: str = "",
        health: dict[str, str] | None = None,
        stop_android: bool = True,
        stop_device: pdalink_lite.AdbDevice | None = None,
    ) -> int:
        self.reset_internet_check()
        self.stop_service()
        self.remove_forward(stop_device or device)
        if stop_android:
            self.stop_android(stop_device or device)
        changed = self.save_state(mode, device, error, error_code, health)
        if changed:
            if error:
                self.log(f"{mode}: {error}")
            else:
                self.log(mode)
        return 1 if error else 0

    @property
    def active_transport(self) -> str:
        if self.active_proxy_url == DIRECT_PROXY_URL:
            return "wifi-direct"
        return self.config.transport

    def direct_ready(self) -> bool:
        config = pdalink_lite.SystemRouteConfig(
            "pdalink0", "10.66.0.2/24", 1500, (), DIRECT_PROXY_URL)
        try:
            pdalink_lite.proxy_upstream_device(config)
        except (OSError, ValueError):
            return False
        # Bypass ambient desktop proxy settings and require the canonical PAC.
        try:
            pac = pdalink_lite.request_pac("192.168.49.1", 8001, timeout=2.0)
        except OSError:
            return False
        expected = ('function FindProxyForURL(url, host) {\n'
                    '  if (host == "192.168.49.1") return "DIRECT";\n'
                    '  return "PROXY 192.168.49.1:8001; DIRECT";\n}')
        return pac.replace("\r\n", "\n").rstrip("\n") == expected

    def switch_proxy(self, target: str) -> bool:
        if target == self.active_proxy_url:
            return True
        if not self.config.no_system_service:
            check = self.service_check()
            if check.running and not self.state.service_owned:
                self.log("transport switch refused: service is not watcher-owned")
                return False
        self.stop_service()  # Uses the OLD URL for state ownership validation.
        if self.state.service_owned:
            self.log("transport switch refused: old service could not be stopped")
            return False
        self.active_proxy_url = target
        self.transport_since = self.boot_clock()
        self.reset_internet_check()
        return True

    def reconcile(self) -> int:
        if (not self.config.wifi_direct_failover or self.config.transport != "adb"
                or self.user_config()["auto_connect"] != "on_trusted_connect"):
            if (not self.config.wifi_direct_failover
                    and not self.switch_proxy(self.config.effective_proxy_url)):
                return 1
            return self.reconcile_adb()
        actions_ok, error = self.process_actions()
        if not actions_ok:
            return self.cleanup("control-failed", self.previous_device(), error,
                                "control-failed", stop_android=False)
        if self.user_config()["auto_connect"] != "on_trusted_connect":
            return self.reconcile_adb()
        usb_url = self.config.effective_proxy_url
        devices, result = self.list_devices()
        device = self.choose_device(devices) if result.returncode == 0 else None
        usb_ok = False
        if device is not None:
            forward = self.forward_check(device.serial)
            if not forward.conflict:
                if not forward.ok or not self.probe(usb_url, 2.0).ok:
                    started, _ = self.start_android(device)
                    if started:
                        forwarded, _ = self.ensure_forward(device)
                        if forwarded and self.state.forward_owned:
                            self.state.device_serial = device.serial
                            self.state.local_port = self.config.local_port
                            self.state.remote_port = self.config.remote_port
                usb_ok = self.forward_check(device.serial).ok and self.probe(usb_url, 2.0).ok
        now = self.boot_clock()
        if not usb_ok:
            self.usb_healthy_since = None
        elif self.usb_healthy_since is None:
            self.usb_healthy_since = now
        on_direct = self.active_proxy_url == DIRECT_PROXY_URL
        # USB is preferred initially; returning from Direct needs continuous health
        # and a minimum dwell. Loss of Direct bypasses the dwell for recovery.
        direct_ok = self.direct_ready() if on_direct or not usb_ok else False
        return_usb = usb_ok and (not on_direct or not direct_ok or (
            now - self.usb_healthy_since >= self.config.usb_return_delay
            and now - self.transport_since >= self.config.transport_min_dwell))
        target = usb_url if return_usb or not direct_ok else DIRECT_PROXY_URL
        if not self.switch_proxy(target):
            return 1
        if target != DIRECT_PROXY_URL:
            return self.reconcile_adb()
        if device is None:
            self.remove_forward(self.previous_device())
        ok, error = self.start_service()
        if not ok:
            return self.cleanup("service-start-failed", device, error,
                                "system-service-failed", stop_android=False)
        health = {"proxy": "ready", "system": "ready", "usb": "ready" if usb_ok else "unavailable"}
        internet = self.check_internet()
        if internet:
            health["internet"] = internet
        if self.save_state("ready", device, health=health):
            self.log("ready: wifi-direct -> " + self.active_proxy_url)
        return 0

    def reconcile_adb(self) -> int:
        if self.config.transport == "product-usb":
            return self.reconcile_product_usb()

        actions_ok, actions_error = self.process_actions()
        if not actions_ok:
            return self.cleanup(
                "control-failed",
                self.previous_device(),
                actions_error,
                "control-failed",
                {"control": "error"},
                stop_android=False,
            )

        user_config = self.user_config()
        if user_config["auto_connect"] == "off":
            return self.cleanup(
                "auto-connect-off",
                None,
                "auto_connect is off",
                "auto-connect-off",
                {"device": "not-checked", "forward": "stopped", "proxy": "stopped", "system": "stopped"},
                stop_device=self.previous_device(),
            )
        if user_config["auto_connect"] == "when_offline":
            return self.cleanup(
                "waiting-offline-policy",
                None,
                "auto_connect=when_offline is not implemented yet; use on_trusted_connect for automatic routing",
                "offline-policy-unimplemented",
                {"device": "not-checked", "forward": "stopped", "proxy": "stopped", "system": "stopped"},
                stop_device=self.previous_device(),
            )

        devices, result = self.list_devices()
        if result.returncode != 0:
            return self.cleanup(
                "adb-missing",
                None,
                safe_detail(result.stderr or result.stdout, "adb unavailable"),
                "adb-unavailable",
                {"device": "error", "forward": "stopped", "proxy": "stopped", "system": "stopped"},
            )

        device = self.choose_device(devices)
        if device is None:
            trust_reason = self.trust_block_reason(devices)
            if trust_reason:
                return self.cleanup(
                    "waiting-trusted-device",
                    None,
                    trust_reason,
                    "device-untrusted",
                    {"device": "untrusted", "forward": "stopped", "proxy": "stopped", "system": "stopped"},
                    stop_device=self.previous_device(),
                )
            if devices:
                states = ", ".join(f"{item.serial}:{item.state}" for item in devices)
                return self.cleanup(
                    "waiting-adb-authorization",
                    None,
                    f"no authorized Android device ({states})",
                    "adb-authorization-required",
                    {"device": "unauthorized", "forward": "stopped", "proxy": "stopped", "system": "stopped"},
                )
            return self.cleanup(
                "waiting-usb",
                None,
                "no Android device listed",
                "usb-disconnected",
                {"device": "missing", "forward": "stopped", "proxy": "stopped", "system": "stopped"},
            )

        health = {"device": "ready", "forward": "checking", "proxy": "checking", "system": "checking"}

        preflight_forward = self.forward_check(device.serial)
        if preflight_forward.conflict:
            health.update({"forward": "conflict", "proxy": "not-checked", "system": "stopped"})
            return self.cleanup(
                "forward-failed",
                device,
                preflight_forward.conflict,
                "forward-conflict",
                health,
                stop_android=False,
            )

        proxy: ProxyCheck | None = None
        if preflight_forward.ok:
            self.state.forward_owned = preflight_forward.owned
            health["forward"] = "ready"
            proxy = self.probe(self.active_proxy_url, self.config.verify_timeout)
            if not proxy.ok:
                ok, error = self.start_android(device)
                if not ok:
                    health.update({"proxy": "error", "system": "stopped"})
                    return self.cleanup(
                        "android-start-failed", device, error, "android-start-failed", health, stop_android=False
                    )
        else:
            ok, error = self.start_android(device)
            if not ok:
                health.update({"forward": "missing", "proxy": "error", "system": "stopped"})
                return self.cleanup(
                    "android-start-failed", device, error, "android-start-failed", health, stop_android=False
                )
            ok, error = self.ensure_forward(device)
            if not ok:
                health.update({"forward": "error", "proxy": "not-checked", "system": "stopped"})
                return self.cleanup("forward-failed", device, error, "forward-create-failed", health)
            health["forward"] = "ready"

        if proxy is None or not proxy.ok:
            proxy = self.probe(self.active_proxy_url, self.config.verify_timeout)
        if not proxy.ok and preflight_forward.ok and self.state.forward_owned:
            self.remove_forward(device)
            ok, error = self.ensure_forward(device)
            if not ok:
                health.update({"forward": "error", "proxy": "unreachable", "system": "stopped"})
                return self.cleanup("forward-failed", device, error, "forward-repair-failed", health)
            health["forward"] = "repaired"
            proxy = self.probe(self.active_proxy_url, self.config.verify_timeout)
        if not proxy.ok:
            health.update({"proxy": "unreachable", "system": "stopped"})
            return self.cleanup(
                "proxy-unreachable", device, proxy.detail, "proxy-unreachable", health
            )
        health["proxy"] = "ready"

        ok, error = self.start_service()
        if not ok:
            health["system"] = "error"
            return self.cleanup(
                "service-start-failed", device, error, "system-service-failed", health
            )
        health["system"] = "ready"

        health.update(self.sync_local_hotspot_credentials(device, user_config))
        internet = self.check_internet()
        if internet:
            health["internet"] = internet

        if self.save_state("ready", device, health=health):
            self.log(f"ready: {device.serial} -> {self.active_proxy_url}")
        return 0

    def check_internet(self) -> str:
        """Return "ok"/"failing" for phone egress, re-probing on an interval.

        A failing check is reported, not acted on: tearing down the USB path
        because the phone's LTE is slow would make a flaky link worse.
        """
        interval = self.config.internet_check_interval
        if interval <= 0:
            return ""
        now = self.boot_clock()
        wait = INTERNET_FAILING_RECHECK if self.internet_health == "failing" else interval
        if self.internet_checked_at is not None and now - self.internet_checked_at < wait:
            return self.internet_health
        check = self.internet_probe(self.active_proxy_url, self.config.internet_check_timeout)
        self.internet_checked_at = self.boot_clock()
        previous = self.internet_health
        if check.ok:
            if previous == "failing":
                self.log(f"internet: recovered after {self.internet_failures} failed check(s) ({check.detail})")
            elif previous == "":
                self.log(f"internet: ok ({check.detail})")
            self.internet_failures = 0
            self.internet_health = "ok"
        else:
            self.internet_failures += 1
            self.log(f"internet: check {self.internet_failures} failed through proxy: {check.detail}")
            self.internet_health = "failing"
        return self.internet_health

    def reset_internet_check(self) -> None:
        self.internet_checked_at = None
        self.internet_health = ""
        self.internet_failures = 0

    def cleanup_product_usb(self, mode: str, error: str = "") -> int:
        self.stop_service()
        self.state.forward_owned = False
        self.save_state(mode, None, error)
        if error:
            self.log(f"{mode}: {error}")
        else:
            self.log(mode)
        return 1 if error else 0

    def product_usb_status(self) -> pdalink_lite.ProductUsbStatus:
        state_path = self.config.product_usb_state_file or pdalink_lite.default_product_usb_state_path()
        return pdalink_lite.product_usb_status_from_state(pdalink_lite.load_product_usb_state(state_path))

    def reconcile_product_usb(self) -> int:
        user_config = self.user_config()
        if user_config["auto_connect"] == "off":
            return self.cleanup_product_usb("auto-connect-off", "auto_connect is off")
        if user_config["auto_connect"] == "when_offline":
            return self.cleanup_product_usb(
                "waiting-offline-policy",
                "auto_connect=when_offline is not implemented yet; use on_trusted_connect for automatic routing",
            )

        try:
            product_status = self.product_usb_status()
        except ValueError as exc:
            return self.cleanup_product_usb("waiting-product-usb", str(exc))
        if not product_status.ready:
            return self.cleanup_product_usb("waiting-product-usb", product_status.readiness)

        proxy = self.probe(self.active_proxy_url, self.config.verify_timeout)
        if not proxy.ok:
            return self.cleanup_product_usb("proxy-unreachable", proxy.detail)

        if self.service_running() and not self.state.service_owned:
            return self.cleanup_product_usb(
                "service-conflict",
                "system service is already running outside product USB watcher",
            )

        ok, error = self.start_service()
        if not ok:
            return self.cleanup_product_usb("service-start-failed", error)

        self.state.forward_owned = False
        self.save_state("ready", None)
        self.log(f"ready: product-usb -> {self.active_proxy_url}")
        return 0

    def status(self) -> int:
        if self.config.transport == "product-usb":
            return self.status_product_usb()

        devices, result = self.list_devices()
        print("PdaLink watcher status", file=self.stdout)
        print(f"transport: {self.state.health.get('transport', self.active_transport)}", file=self.stdout)
        print(pdalink_lite.ADB_TRANSPORT_LINE, file=self.stdout)
        print(f"state-file: {self.config.state_file}", file=self.stdout)
        print(f"proxy: {self.active_proxy_url}", file=self.stdout)
        print(f"managed-forward: {'yes' if self.state.forward_owned else 'no'}", file=self.stdout)
        print(f"managed-service: {'yes' if self.state.service_owned else 'no'}", file=self.stdout)
        print(f"mode: {self.state.mode}", file=self.stdout)
        if self.state.last_transition_at:
            print(f"last-transition: {self.state.last_transition_at}", file=self.stdout)
        if self.state.last_resume_at:
            print(f"last-resume: {self.state.last_resume_at}", file=self.stdout)
        if result.returncode != 0:
            print(f"adb: FAIL {(result.stderr or result.stdout).strip()}", file=self.stdout)
            if self.active_transport != "wifi-direct":
                return 1
        device = self.choose_device(devices)
        if self.active_transport == "wifi-direct":
            proxy = self.probe(self.active_proxy_url, min(self.config.verify_timeout, 2.0))
            print(f"proxy: {'OK' if proxy.ok else 'FAIL'} {proxy.detail}", file=self.stdout)
        if device:
            print(f"device: OK {device.serial}", file=self.stdout)
            forward = self.forward_check(device.serial)
            if forward.conflict:
                print(f"forward: CONFLICT {forward.conflict}", file=self.stdout)
            else:
                print(f"forward: {'OK' if forward.ok else 'MISSING'} tcp:{self.config.local_port} -> tcp:{self.config.remote_port}", file=self.stdout)
            proxy = self.probe(self.active_proxy_url, min(self.config.verify_timeout, 2.0))
            print(f"proxy: {'OK' if proxy.ok else 'FAIL'} {proxy.detail}", file=self.stdout)
        elif devices:
            states = ", ".join(f"{item.serial}:{item.state}" for item in devices)
            print(f"device: FAIL {states}", file=self.stdout)
        else:
            print("device: MISSING", file=self.stdout)
        service = "RUNNING" if self.service_running() else "MISSING"
        print(f"system-service: {service}", file=self.stdout)
        if self.state.last_error:
            print(f"last-error: {self.state.last_error}", file=self.stdout)
        for name, value in sorted(self.state.health.items()):
            print(f"health-{name}: {value}", file=self.stdout)
        return 0

    def status_product_usb(self) -> int:
        print("PdaLink watcher status", file=self.stdout)
        print(f"transport: {self.state.health.get('transport', self.active_transport)}", file=self.stdout)
        print(pdalink_lite.PRODUCT_USB_TRANSPORT_LINE, file=self.stdout)
        print(f"state-file: {self.config.state_file}", file=self.stdout)
        product_state_path = self.config.product_usb_state_file or pdalink_lite.default_product_usb_state_path()
        print(f"product-usb-state: {product_state_path}", file=self.stdout)
        print(f"proxy: {self.active_proxy_url}", file=self.stdout)
        print(f"managed-service: {'yes' if self.state.service_owned else 'no'}", file=self.stdout)
        try:
            product_status = self.product_usb_status()
            print(f"product-usb-readiness: {product_status.readiness}", file=self.stdout)
        except ValueError as exc:
            print(f"product-usb-readiness: invalid ({exc})", file=self.stdout)
        service = "RUNNING" if self.service_running() else "MISSING"
        print(f"system-service: {service}", file=self.stdout)
        if self.state.last_error:
            print(f"last-error: {self.state.last_error}", file=self.stdout)
        return 0

    def run_loop(self, max_loops: int | None = None) -> int:
        loops = 0
        status = 0
        last_awake = self.boot_clock()
        previous_handlers = install_stop_signal_handlers()
        if self.config.enable_dbus and pdalink_dbus is not None:
            self.dbus_server = pdalink_dbus.PdaLinkDbusServer(
                state_file=self.config.state_file,
                on_action=self.enqueue_action,
                log=self.log,
            )
            self.dbus_server.start()
        try:
            while True:
                now = self.boot_clock()
                if loops and now - last_awake > max(10.0, self.config.poll_interval * 5.0):
                    self.handle_resume()
                status = self.reconcile()
                loops += 1
                if max_loops is not None and loops >= max_loops:
                    return status
                last_awake = self.boot_clock()
                delay = max(0.1, self.config.poll_interval)
                if self.sleeper is time.sleep:
                    self.wake_event.wait(delay)
                    self.wake_event.clear()
                else:
                    self.sleeper(delay)
        except (KeyboardInterrupt, StopRequested):
            restore_signal_handlers(previous_handlers)
            previous_handlers = {}
            devices, _ = self.list_devices()
            device = self.choose_device(devices)
            return self.cleanup("stopped", device)
        finally:
            if self.dbus_server is not None:
                self.dbus_server.stop()
            restore_signal_handlers(previous_handlers)


def install_stop_signal_handlers() -> dict[int, signal.Handlers]:
    previous_handlers = {}
    for signum in (signal.SIGTERM,):
        previous_handlers[signum] = signal.getsignal(signum)
        signal.signal(signum, raise_stop_requested)
    return previous_handlers


def restore_signal_handlers(previous_handlers: dict[int, signal.Handlers]) -> None:
    for signum, handler in previous_handlers.items():
        signal.signal(signum, handler)


def raise_stop_requested(signum: int, _frame) -> None:
    name = signal.Signals(signum).name
    raise StopRequested(name)


def add_common_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--wifi-direct-failover", action="store_true",
                        default=os.getenv("PDALINK_WIFI_DIRECT_FAILOVER", "0") == "1")
    parser.add_argument("--adb", default=default_adb_path())
    parser.add_argument("--transport", choices=("adb", "product-usb"), default=os.getenv("PDALINK_WATCH_TRANSPORT", "adb"))
    parser.add_argument("--local-port", type=int, default=DEFAULT_LOCAL_PORT)
    parser.add_argument("--remote-port", type=int, default=DEFAULT_REMOTE_PORT)
    parser.add_argument("--proxy-url", default=os.getenv("PDALINK_SYSTEM_PROXY", ""))
    parser.add_argument("--state-file", type=Path, default=Path(os.getenv("PDALINK_WATCH_STATE", DEFAULT_STATE_FILE)))
    parser.add_argument("--verify-timeout", "--probe-timeout", dest="verify_timeout", type=float, default=15.0)
    parser.add_argument("--poll-interval", type=float, default=2.0)
    parser.add_argument("--no-start-android", action="store_true")
    parser.add_argument("--no-stop-android", action="store_true")
    parser.add_argument("--no-system-service", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--allow-untrusted", dest="require_trusted", action="store_false")
    parser.add_argument("--trusted-devices-file", type=Path)
    parser.add_argument("--config-file", type=Path)
    parser.add_argument("--product-usb-state-file", type=Path)
    parser.add_argument("--no-dbus", dest="enable_dbus", action="store_false")
    parser.add_argument("--no-dns", action="store_true")
    parser.add_argument("--tcp-idle-timeout", type=float, default=0.0)
    parser.add_argument(
        "--internet-check-interval",
        type=float,
        default=float(os.getenv("PDALINK_INTERNET_CHECK_INTERVAL", "60")),
        help="seconds between end-to-end internet checks through the proxy while ready; 0 disables",
    )
    parser.add_argument("--verbose-system", action="store_true")
    parser.add_argument(
        "--system-arg",
        action="append",
        default=[],
        help="extra argument passed to system service commands; repeat for option names and values",
    )


def config_from_args(args: argparse.Namespace) -> WatchConfig:
    return WatchConfig(
        adb=args.adb,
        wifi_direct_failover=args.wifi_direct_failover,
        transport=args.transport,
        local_port=args.local_port,
        remote_port=args.remote_port,
        proxy_url=args.proxy_url,
        poll_interval=args.poll_interval,
        verify_timeout=args.verify_timeout,
        state_file=args.state_file,
        no_start_android=args.no_start_android,
        no_stop_android=args.no_stop_android,
        no_system_service=args.no_system_service,
        dry_run=args.dry_run,
        require_trusted=args.require_trusted,
        trusted_devices_file=args.trusted_devices_file,
        config_file=args.config_file,
        product_usb_state_file=args.product_usb_state_file,
        enable_dbus=args.enable_dbus,
        no_dns=args.no_dns,
        tcp_idle_timeout=args.tcp_idle_timeout,
        internet_check_interval=args.internet_check_interval,
        verbose_system=args.verbose_system,
        system_arg=tuple(args.system_arg),
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Watch USB/ADB state and manage the native Linux PdaLink internet path")
    sub = parser.add_subparsers(dest="command")

    run_parser = sub.add_parser("run", help="run the watcher loop")
    add_common_args(run_parser)
    run_parser.add_argument("--max-loops", type=int)

    once_parser = sub.add_parser("once", help="reconcile once and exit")
    add_common_args(once_parser)

    status_parser = sub.add_parser("status", help="print watcher, ADB, forward, proxy, and service status")
    add_common_args(status_parser)

    return parser


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv:
        argv = ["run"]
    parser = build_parser()
    args = parser.parse_args(argv)
    watcher = PdaLinkWatcher(config_from_args(args))
    if args.command == "status":
        return watcher.status()
    if args.command == "once":
        return watcher.reconcile()
    return watcher.run_loop(args.max_loops)


if __name__ == "__main__":
    raise SystemExit(main())
