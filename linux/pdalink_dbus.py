#!/usr/bin/env python3
"""Session D-Bus control surface for the native Linux PdaLink supervisor."""

from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Callable


BUS_NAME = "dev.pdalink.Client1"
OBJECT_PATH = "/dev/pdalink/Client1"
INTERFACE_NAME = BUS_NAME

INTROSPECTION_XML = f"""
<node>
  <interface name="{INTERFACE_NAME}">
    <method name="GetState">
      <arg name="state" type="s" direction="out"/>
    </method>
    <method name="GetDiagnostics">
      <arg name="diagnostics" type="s" direction="out"/>
    </method>
    <method name="ConnectUsb"/>
    <method name="ConnectHotspot"/>
    <method name="Disconnect"/>
    <method name="Retry"/>
    <method name="SetAutoConnect">
      <arg name="enabled" type="b" direction="in"/>
    </method>
    <signal name="StateChanged">
      <arg name="state" type="s"/>
    </signal>
  </interface>
</node>
"""


def load_public_state(path: Path) -> dict[str, object]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        return {
            "mode": "starting",
            "health": {},
            "last_error": "",
            "last_error_code": "",
        }
    if not isinstance(payload, dict):
        return {"mode": "invalid-state", "health": {}, "last_error": "invalid state payload"}
    allowed = {
        "mode",
        "device_state",
        "forward_owned",
        "service_owned",
        "proxy_url",
        "last_error",
        "last_error_code",
        "last_transition_at",
        "last_success_at",
        "last_resume_at",
        "health",
        "updated_at",
    }
    return {key: payload[key] for key in allowed if key in payload}


def state_json(path: Path) -> str:
    return json.dumps(load_public_state(path), sort_keys=True)


class PdaLinkDbusServer:
    """Own the PdaLink session bus name without running network work in callbacks."""

    def __init__(
        self,
        state_file: Path,
        on_action: Callable[[str], None],
        log: Callable[[str], None],
    ) -> None:
        self.state_file = state_file
        self.on_action = on_action
        self.log = log
        self.thread: threading.Thread | None = None
        self.loop = None
        self.connection = None
        self.owner_id = 0
        self.registration_id = 0
        self.start_error = ""
        self.ready = threading.Event()

    def start(self) -> None:
        if self.thread is not None:
            return
        self.thread = threading.Thread(target=self._run, name="pdalink-dbus", daemon=True)
        self.thread.start()
        self.ready.wait(2.0)
        if self.start_error:
            self.log(f"dbus: unavailable ({self.start_error})")

    def _run(self) -> None:
        try:
            import gi

            gi.require_version("Gio", "2.0")
            from gi.repository import Gio, GLib

            context = GLib.MainContext.new()
            context.push_thread_default()
            try:
                self.connection = Gio.bus_get_sync(Gio.BusType.SESSION, None)
                node = Gio.DBusNodeInfo.new_for_xml(INTROSPECTION_XML)
                interface = node.interfaces[0]
                self.registration_id = self.connection.register_object(
                    OBJECT_PATH,
                    interface,
                    self._method_call,
                    None,
                    None,
                )
                self.owner_id = Gio.bus_own_name_on_connection(
                    self.connection,
                    BUS_NAME,
                    Gio.BusNameOwnerFlags.NONE,
                    None,
                    None,
                )
                system_bus = Gio.bus_get_sync(Gio.BusType.SYSTEM, None)
                system_bus.signal_subscribe(
                    "org.freedesktop.login1",
                    "org.freedesktop.login1.Manager",
                    "PrepareForSleep",
                    "/org/freedesktop/login1",
                    None,
                    Gio.DBusSignalFlags.NONE,
                    self._prepare_for_sleep,
                )
                self.loop = GLib.MainLoop.new(context, False)
                self.ready.set()
                self.loop.run()
            finally:
                context.pop_thread_default()
        except Exception as exc:  # D-Bus is optional; the watcher must still work headlessly.
            self.start_error = f"{type(exc).__name__}: {exc}"
            self.ready.set()

    def _method_call(
        self,
        _connection,
        _sender,
        _object_path,
        _interface_name,
        method_name,
        parameters,
        invocation,
    ) -> None:
        from gi.repository import GLib

        if method_name in ("GetState", "GetDiagnostics"):
            invocation.return_value(GLib.Variant("(s)", (state_json(self.state_file),)))
            return
        actions = {
            "ConnectUsb": "connect-usb",
            "ConnectHotspot": "connect-hotspot",
            "Disconnect": "disconnect",
            "Retry": "retry",
        }
        if method_name == "SetAutoConnect":
            enabled = bool(parameters.unpack()[0])
            self.on_action("connect-usb" if enabled else "disconnect")
            invocation.return_value(None)
            return
        action = actions.get(method_name)
        if action is None:
            invocation.return_dbus_error(
                f"{INTERFACE_NAME}.UnknownMethod",
                f"unknown method: {method_name}",
            )
            return
        self.on_action(action)
        invocation.return_value(None)

    def _prepare_for_sleep(
        self,
        _connection,
        _sender_name,
        _object_path,
        _interface_name,
        _signal_name,
        parameters,
    ) -> None:
        preparing = bool(parameters.unpack()[0])
        if not preparing:
            self.on_action("resume")

    def emit_state_changed(self, state: dict[str, object]) -> None:
        if self.connection is None:
            return
        try:
            from gi.repository import GLib

            public = load_public_state(self.state_file)
            if not public:
                public = state
            payload = json.dumps(public, sort_keys=True)
            self.connection.emit_signal(
                None,
                OBJECT_PATH,
                INTERFACE_NAME,
                "StateChanged",
                GLib.Variant("(s)", (payload,)),
            )
        except Exception as exc:
            self.log(f"dbus: state signal failed ({type(exc).__name__}: {exc})")

    def stop(self) -> None:
        if self.loop is not None:
            self.loop.quit()
        if self.connection is not None and self.registration_id:
            self.connection.unregister_object(self.registration_id)
            self.registration_id = 0
        if self.owner_id:
            try:
                from gi.repository import Gio

                Gio.bus_unown_name(self.owner_id)
            except Exception:
                pass
            self.owner_id = 0
        if self.thread is not None and self.thread.is_alive():
            self.thread.join(timeout=1.0)
        self.thread = None
