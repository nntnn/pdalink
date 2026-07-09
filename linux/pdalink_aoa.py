"""Android Open Accessory bootstrap helpers for PdaLink product USB."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


AOA_GET_PROTOCOL = 51
AOA_SEND_STRING = 52
AOA_START_ACCESSORY = 53
AOA_STRING_MANUFACTURER = 0
AOA_STRING_MODEL = 1
AOA_STRING_DESCRIPTION = 2
AOA_STRING_VERSION = 3
AOA_STRING_URI = 4
AOA_STRING_SERIAL = 5
PDALINK_AOA_STRINGS = {
    AOA_STRING_MANUFACTURER: "PdaLink",
    AOA_STRING_MODEL: "PdaLink USB",
    AOA_STRING_DESCRIPTION: "PdaLink product USB transport",
    AOA_STRING_VERSION: "1",
    AOA_STRING_URI: "https://github.com/nntnn/pdalink",
}


class AoaBackend(Protocol):
    def get_protocol(self) -> int:
        ...

    def send_string(self, index: int, value: str) -> None:
        ...

    def start_accessory(self) -> None:
        ...


@dataclass(frozen=True)
class AoaStartResult:
    ok: bool
    detail: str


class FakeAoaBackend:
    def __init__(self, protocol: int) -> None:
        self.protocol = protocol
        self.calls: list[tuple] = []

    def get_protocol(self) -> int:
        self.calls.append(("get-protocol",))
        return self.protocol

    def send_string(self, index: int, value: str) -> None:
        self.calls.append(("send-string", index, value))

    def start_accessory(self) -> None:
        self.calls.append(("start-accessory",))


class PyUsbAoaBackend:
    def __init__(self, device) -> None:
        self.device = device

    @classmethod
    def open(cls, vid: str, pid: str) -> "PyUsbAoaBackend":
        if not vid or not pid:
            raise ValueError("real AOA start requires explicit --vid and --pid")
        try:
            import usb.core
        except ImportError as exc:
            raise RuntimeError("PyUSB is required for real AOA start; install python3-usb or pyusb") from exc
        device = usb.core.find(idVendor=parse_usb_id(vid), idProduct=parse_usb_id(pid))
        if device is None:
            raise RuntimeError(f"USB device not found for {vid}:{pid}")
        detach_kernel_drivers(device)
        return cls(device)

    def get_protocol(self) -> int:
        raw = self.device.ctrl_transfer(0xC0, AOA_GET_PROTOCOL, 0, 0, 2)
        return int(raw[0]) | (int(raw[1]) << 8)

    def send_string(self, index: int, value: str) -> None:
        data = value.encode("utf-8") + b"\0"
        self.device.ctrl_transfer(0x40, AOA_SEND_STRING, 0, index, data)

    def start_accessory(self) -> None:
        self.device.ctrl_transfer(0x40, AOA_START_ACCESSORY, 0, 0, None)

    def close(self) -> None:
        try:
            import usb.util
            usb.util.dispose_resources(self.device)
        except Exception:
            pass


def detach_kernel_drivers(device) -> None:
    interface_numbers = [0]
    try:
        interface_numbers = sorted({
            int(interface.bInterfaceNumber)
            for interface in device.get_active_configuration()
        })
    except Exception:
        pass
    for interface_number in interface_numbers:
        try:
            if device.is_kernel_driver_active(interface_number):
                device.detach_kernel_driver(interface_number)
        except Exception:
            pass


def parse_usb_id(value: str) -> int:
    cleaned = value.strip().lower()
    if cleaned.startswith("0x"):
        cleaned = cleaned[2:]
    parsed = int(cleaned, 16)
    if parsed < 0 or parsed > 0xFFFF:
        raise ValueError(f"USB id out of range: {value}")
    return parsed


def start_pdalink_accessory(backend: AoaBackend, serial: str) -> AoaStartResult:
    protocol = backend.get_protocol()
    if protocol < 1:
        return AoaStartResult(False, "device does not support Android Open Accessory")
    for index in sorted(PDALINK_AOA_STRINGS):
        backend.send_string(index, PDALINK_AOA_STRINGS[index])
    backend.send_string(AOA_STRING_SERIAL, serial)
    backend.start_accessory()
    return AoaStartResult(True, f"started PdaLink accessory mode with AOA protocol {protocol}")
