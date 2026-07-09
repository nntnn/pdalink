#!/usr/bin/env python3
"""Native Linux USB watcher for the PdaLink data path."""

from __future__ import annotations

import argparse
import json
import os
import signal
import shutil
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
import subprocess
import sys
import time
from typing import Callable, Iterable
from urllib.error import URLError
from urllib.request import urlopen


ROOT = Path(__file__).resolve().parents[1]
RUNTIME_DIR = ROOT / ".tools" / "runtime"
DEFAULT_STATE_FILE = RUNTIME_DIR / "pdalink-watch.json"
DEFAULT_LOCAL_PORT = int(os.getenv("PDALINK_USB_LOCAL_PORT", "18080"))
DEFAULT_REMOTE_PORT = int(os.getenv("PDALINK_USB_REMOTE_PORT", "8000"))
ANDROID_DEBUG_ACTIVITY = "dev.pdalink.prototype/.DebugControlActivity"
ANDROID_PROXY_SERVICE = "dev.pdalink.prototype/.ProxyService"
ANDROID_ACTION_START = "dev.pdalink.prototype.START_USB"
ANDROID_ACTION_STOP = "dev.pdalink.prototype.STOP"

sys.path.insert(0, str(ROOT / "linux"))
import pdalink_lite  # noqa: E402


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


class PdaLinkWatcher:
    def __init__(
        self,
        config: WatchConfig,
        runner: SubprocessRunner | None = None,
        probe: Callable[[str, float], ProxyCheck] = probe_proxy,
        sleeper: Callable[[float], None] = time.sleep,
        stdout = sys.stdout,
    ) -> None:
        self.config = config
        self.runner = runner or SubprocessRunner()
        self.probe = probe
        self.sleeper = sleeper
        self.stdout = stdout
        self.state = WatchState.load(config.state_file)

    def log(self, message: str) -> None:
        print(message, file=self.stdout)

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

    def save_state(self, mode: str, device: pdalink_lite.AdbDevice | None = None, error: str = "") -> None:
        self.state.mode = mode
        self.state.device_serial = device.serial if device else ""
        self.state.device_state = device.state if device else ""
        self.state.local_port = self.config.local_port
        self.state.remote_port = self.config.remote_port
        self.state.proxy_url = self.config.effective_proxy_url
        self.state.last_error = error
        self.state.save(self.config.state_file)

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
                ["shell", "am", "start-foreground-service", "-n", ANDROID_PROXY_SERVICE, "-a", ANDROID_ACTION_STOP],
                serial=device.serial,
            ),
            timeout=10.0,
        )

    def service_args(self, command: str) -> list[str]:
        user_config = self.user_config()
        args = shell_script_command(self.config.root / "tools" / "pdalink.sh")
        args.extend(["system", "service", command])
        args.extend(["--proxy-url", self.config.effective_proxy_url])
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

    def service_running(self) -> bool:
        result = self.run_read(self.service_args("status"), timeout=10.0)
        return "wrapper: RUNNING" in result.stdout

    def start_service(self) -> tuple[bool, str]:
        if self.config.no_system_service:
            return True, ""
        if self.service_running():
            self.state.service_owned = bool(self.state.service_owned)
            return True, ""
        result = self.run_write(self.service_args("start"), timeout=30.0)
        if result.returncode != 0:
            return False, (result.stderr or result.stdout).strip() or "system service start failed"
        self.state.service_owned = not self.config.dry_run
        return True, ""

    def stop_service(self) -> None:
        if self.config.no_system_service or not self.state.service_owned:
            return
        result = self.run_write(self.service_args("stop"), timeout=30.0)
        if result.returncode == 0:
            self.state.service_owned = False

    def remove_forward(self) -> None:
        if not self.state.forward_owned:
            return
        result = self.run_write(
            self.adb_command(["forward", "--remove", f"tcp:{self.config.local_port}"]),
            timeout=10.0,
        )
        if result.returncode == 0:
            self.state.forward_owned = False

    def cleanup(
        self,
        mode: str,
        device: pdalink_lite.AdbDevice | None,
        error: str = "",
        stop_android: bool = True,
        stop_device: pdalink_lite.AdbDevice | None = None,
    ) -> int:
        self.stop_service()
        self.remove_forward()
        if stop_android:
            self.stop_android(stop_device or device)
        self.save_state(mode, device, error)
        if error:
            self.log(f"{mode}: {error}")
        else:
            self.log(mode)
        return 1 if error else 0

    def reconcile(self) -> int:
        if self.config.transport == "product-usb":
            return self.reconcile_product_usb()

        user_config = self.user_config()
        if user_config["auto_connect"] == "off":
            return self.cleanup("auto-connect-off", None, "auto_connect is off", stop_device=self.previous_device())
        if user_config["auto_connect"] == "when_offline":
            return self.cleanup(
                "waiting-offline-policy",
                None,
                "auto_connect=when_offline is not implemented yet; use on_trusted_connect for automatic routing",
                stop_device=self.previous_device(),
            )

        devices, result = self.list_devices()
        if result.returncode != 0:
            return self.cleanup("adb-missing", None, (result.stderr or result.stdout).strip() or "adb unavailable")

        device = self.choose_device(devices)
        if device is None:
            trust_reason = self.trust_block_reason(devices)
            if trust_reason:
                return self.cleanup("waiting-trusted-device", None, trust_reason, stop_device=self.previous_device())
            if devices:
                states = ", ".join(f"{item.serial}:{item.state}" for item in devices)
                return self.cleanup("waiting-adb-authorization", None, f"no authorized Android device ({states})")
            return self.cleanup("waiting-usb", None, "no Android device listed")

        preflight_forward = self.forward_check(device.serial)
        if preflight_forward.conflict:
            return self.cleanup("forward-failed", device, preflight_forward.conflict, stop_android=False)

        proxy: ProxyCheck | None = None
        if preflight_forward.ok:
            self.state.forward_owned = preflight_forward.owned
            proxy = self.probe(self.config.effective_proxy_url, self.config.verify_timeout)
            if not proxy.ok:
                ok, error = self.start_android(device)
                if not ok:
                    return self.cleanup("android-start-failed", device, error, stop_android=False)
        else:
            ok, error = self.start_android(device)
            if not ok:
                return self.cleanup("android-start-failed", device, error, stop_android=False)
            ok, error = self.ensure_forward(device)
            if not ok:
                return self.cleanup("forward-failed", device, error)

        if proxy is None or not proxy.ok:
            proxy = self.probe(self.config.effective_proxy_url, self.config.verify_timeout)
        if not proxy.ok:
            return self.cleanup("proxy-unreachable", device, proxy.detail)

        ok, error = self.start_service()
        if not ok:
            return self.cleanup("service-start-failed", device, error)

        self.save_state("ready", device)
        self.log(f"ready: {device.serial} -> {self.config.effective_proxy_url}")
        return 0

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

        proxy = self.probe(self.config.effective_proxy_url, self.config.verify_timeout)
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
        self.log(f"ready: product-usb -> {self.config.effective_proxy_url}")
        return 0

    def status(self) -> int:
        if self.config.transport == "product-usb":
            return self.status_product_usb()

        devices, result = self.list_devices()
        print("PdaLink watcher status", file=self.stdout)
        print(pdalink_lite.ADB_TRANSPORT_LINE, file=self.stdout)
        print(f"state-file: {self.config.state_file}", file=self.stdout)
        print(f"proxy: {self.config.effective_proxy_url}", file=self.stdout)
        print(f"managed-forward: {'yes' if self.state.forward_owned else 'no'}", file=self.stdout)
        print(f"managed-service: {'yes' if self.state.service_owned else 'no'}", file=self.stdout)
        if result.returncode != 0:
            print(f"adb: FAIL {(result.stderr or result.stdout).strip()}", file=self.stdout)
            return 1
        device = self.choose_device(devices)
        if device:
            print(f"device: OK {device.serial}", file=self.stdout)
            forward = self.forward_check(device.serial)
            if forward.conflict:
                print(f"forward: CONFLICT {forward.conflict}", file=self.stdout)
            else:
                print(f"forward: {'OK' if forward.ok else 'MISSING'} tcp:{self.config.local_port} -> tcp:{self.config.remote_port}", file=self.stdout)
            proxy = self.probe(self.config.effective_proxy_url, min(self.config.verify_timeout, 2.0))
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
        return 0

    def status_product_usb(self) -> int:
        print("PdaLink watcher status", file=self.stdout)
        print(pdalink_lite.PRODUCT_USB_TRANSPORT_LINE, file=self.stdout)
        print(f"state-file: {self.config.state_file}", file=self.stdout)
        product_state_path = self.config.product_usb_state_file or pdalink_lite.default_product_usb_state_path()
        print(f"product-usb-state: {product_state_path}", file=self.stdout)
        print(f"proxy: {self.config.effective_proxy_url}", file=self.stdout)
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
        previous_handlers = install_stop_signal_handlers()
        try:
            while True:
                status = self.reconcile()
                loops += 1
                if max_loops is not None and loops >= max_loops:
                    return status
                self.sleeper(max(0.1, self.config.poll_interval))
        except (KeyboardInterrupt, StopRequested):
            restore_signal_handlers(previous_handlers)
            previous_handlers = {}
            devices, _ = self.list_devices()
            device = self.choose_device(devices)
            return self.cleanup("stopped", device)
        finally:
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
    parser.add_argument("--no-dns", action="store_true")
    parser.add_argument("--tcp-idle-timeout", type=float, default=0.0)
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
        no_dns=args.no_dns,
        tcp_idle_timeout=args.tcp_idle_timeout,
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
