#!/usr/bin/env python3
"""GTK4/libadwaita client for the native Linux PdaLink supervisor."""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("Gio", "2.0")
from gi.repository import Adw, Gio, GLib, Gtk  # noqa: E402

from pdalink_dbus import BUS_NAME, INTERFACE_NAME, OBJECT_PATH  # noqa: E402


APP_ID = "dev.pdalink.Client"


@dataclass(frozen=True)
class ClientViewModel:
    mode: str
    title: str
    description: str
    icon_name: str
    action: str
    error: str
    transition_at: str
    health: dict[str, str]
    auto_connect: bool


def view_model_from_state(state: dict[str, object]) -> ClientViewModel:
    mode = str(state.get("mode") or "starting")
    error = str(state.get("last_error") or "")
    health_raw = state.get("health")
    health = {
        str(key): str(value)
        for key, value in health_raw.items()
    } if isinstance(health_raw, dict) else {}
    if mode == "ready":
        title = "Connected"
        description = "Ubuntu internet is using the PdaLink path."
        icon_name = "network-vpn-symbolic"
        action = "Disconnect"
    elif mode in ("recovering-resume", "connecting", "starting"):
        title = "Repairing connection"
        description = "Checking the phone, proxy, tunnel, routes, and DNS."
        icon_name = "emblem-synchronizing-symbolic"
        action = "Retry"
    elif mode == "auto-connect-off":
        title = "Disconnected"
        description = "The supervisor is running and automatic connection is disabled."
        icon_name = "network-offline-symbolic"
        action = "Connect USB"
    elif mode in ("waiting-usb", "waiting-adb-authorization", "waiting-trusted-device"):
        title = "Waiting for phone"
        description = error or "Connect and authorize a trusted Android phone."
        icon_name = "phone-symbolic"
        action = "Retry"
    else:
        title = "Connection needs attention"
        description = error or "Open diagnostics for the failing connection stage."
        icon_name = "dialog-warning-symbolic"
        action = "Retry"
    return ClientViewModel(
        mode=mode,
        title=title,
        description=description,
        icon_name=icon_name,
        action=action,
        error=error,
        transition_at=str(state.get("last_transition_at") or state.get("updated_at") or ""),
        health=health,
        auto_connect=mode != "auto-connect-off",
    )


class SupervisorProxy:
    def __init__(self, on_state, on_error) -> None:
        self.on_state = on_state
        self.on_error = on_error
        self.proxy = None

    def connect(self) -> None:
        try:
            self.proxy = Gio.DBusProxy.new_for_bus_sync(
                Gio.BusType.SESSION,
                Gio.DBusProxyFlags.NONE,
                None,
                BUS_NAME,
                OBJECT_PATH,
                INTERFACE_NAME,
                None,
            )
            self.proxy.connect("g-signal", self._on_signal)
            self.refresh()
        except GLib.Error as exc:
            self.on_error(str(exc))

    def _on_signal(self, _proxy, _sender, signal_name, parameters) -> None:
        if signal_name != "StateChanged":
            return
        self._consume_json(str(parameters.unpack()[0]))

    def _consume_json(self, raw: str) -> None:
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError as exc:
            self.on_error(f"Invalid supervisor state: {exc}")
            return
        if not isinstance(payload, dict):
            self.on_error("Invalid supervisor state payload")
            return
        self.on_state(payload)

    def refresh(self) -> bool:
        if self.proxy is None:
            self.connect()
            return True
        try:
            result = self.proxy.call_sync(
                "GetState",
                None,
                Gio.DBusCallFlags.NONE,
                3000,
                None,
            )
            self._consume_json(str(result.unpack()[0]))
        except GLib.Error as exc:
            self.on_error(str(exc))
        return True

    def call(self, method: str, parameters=None) -> None:
        if self.proxy is None:
            self.on_error("PdaLink supervisor is not available")
            return
        self.proxy.call(
            method,
            parameters,
            Gio.DBusCallFlags.NONE,
            5000,
            None,
            self._call_finished,
            None,
        )

    def _call_finished(self, proxy, result, _user_data) -> None:
        try:
            proxy.call_finish(result)
        except GLib.Error as exc:
            self.on_error(str(exc))
            return
        GLib.timeout_add(150, self._refresh_once)

    def _refresh_once(self) -> bool:
        self.refresh()
        return False


class PdaLinkWindow(Adw.ApplicationWindow):
    HEALTH_LABELS = {
        "device": "Android device",
        "forward": "USB forward",
        "proxy": "Android proxy",
        "system": "Ubuntu tunnel",
        "hotspot": "Local Hotspot",
        "hotspot_target": "Linux target relay",
        "resume": "Resume recovery",
    }

    def __init__(self, application: Adw.Application) -> None:
        super().__init__(application=application, title="PdaLink")
        self.set_default_size(520, 680)
        self.previous_mode = ""
        self.updating_switch = False

        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        outer.append(Adw.HeaderBar())

        scrolled = Gtk.ScrolledWindow(vexpand=True)
        content = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL,
            spacing=18,
            margin_top=18,
            margin_bottom=24,
            margin_start=18,
            margin_end=18,
        )
        scrolled.set_child(content)
        outer.append(scrolled)
        self.set_content(outer)

        self.status_page = Adw.StatusPage()
        self.status_page.set_title("Starting PdaLink")
        self.status_page.set_description("Waiting for the Linux supervisor.")
        self.status_page.set_icon_name("emblem-synchronizing-symbolic")
        content.append(self.status_page)

        buttons = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8, homogeneous=True)
        self.usb_button = Gtk.Button(label="Connect USB")
        self.usb_button.add_css_class("suggested-action")
        self.usb_button.connect("clicked", lambda _button: self.proxy.call("ConnectUsb"))
        buttons.append(self.usb_button)
        self.hotspot_button = Gtk.Button(label="Connect Hotspot")
        self.hotspot_button.connect("clicked", lambda _button: self.proxy.call("ConnectHotspot"))
        buttons.append(self.hotspot_button)
        content.append(buttons)

        secondary = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8, homogeneous=True)
        self.retry_button = Gtk.Button(label="Retry")
        self.retry_button.connect("clicked", lambda _button: self.proxy.call("Retry"))
        secondary.append(self.retry_button)
        self.disconnect_button = Gtk.Button(label="Disconnect")
        self.disconnect_button.add_css_class("destructive-action")
        self.disconnect_button.connect("clicked", lambda _button: self.proxy.call("Disconnect"))
        secondary.append(self.disconnect_button)
        content.append(secondary)

        self.health_group = Adw.PreferencesGroup(title="Connection stages")
        self.health_rows: dict[str, Adw.ActionRow] = {}
        for key, label in self.HEALTH_LABELS.items():
            row = Adw.ActionRow(title=label, subtitle="Not checked")
            self.health_rows[key] = row
            self.health_group.add(row)
        content.append(self.health_group)

        settings = Adw.PreferencesGroup(title="Automation")
        auto_row = Adw.ActionRow(
            title="Connect trusted phone automatically",
            subtitle="The supervisor stays running when the connection is disabled.",
        )
        self.auto_switch = Gtk.Switch(valign=Gtk.Align.CENTER)
        self.auto_switch.connect("notify::active", self._auto_connect_changed)
        auto_row.add_suffix(self.auto_switch)
        auto_row.set_activatable_widget(self.auto_switch)
        settings.add(auto_row)
        content.append(settings)

        diagnostics = Adw.PreferencesGroup(title="Diagnostics")
        self.error_row = Adw.ActionRow(title="Last error", subtitle="None")
        diagnostics.add(self.error_row)
        self.transition_row = Adw.ActionRow(title="Last state change", subtitle="Unknown")
        diagnostics.add(self.transition_row)
        content.append(diagnostics)

        self.proxy = SupervisorProxy(self.apply_state, self.show_error)
        self.proxy.connect()
        GLib.timeout_add_seconds(5, self.proxy.refresh)

    def _auto_connect_changed(self, switch, _param) -> None:
        if self.updating_switch:
            return
        self.proxy.call("SetAutoConnect", GLib.Variant("(b)", (switch.get_active(),)))

    def show_error(self, detail: str) -> None:
        self.status_page.set_title("Supervisor unavailable")
        self.status_page.set_description(detail)
        self.status_page.set_icon_name("dialog-error-symbolic")
        self.error_row.set_subtitle(detail)

    def apply_state(self, state: dict[str, object]) -> None:
        view = view_model_from_state(state)
        self.status_page.set_title(view.title)
        self.status_page.set_description(view.description)
        self.status_page.set_icon_name(view.icon_name)
        self.error_row.set_subtitle(view.error or "None")
        self.transition_row.set_subtitle(view.transition_at or "Unknown")
        for key, row in self.health_rows.items():
            row.set_subtitle(view.health.get(key, "Not checked"))
        self.updating_switch = True
        self.auto_switch.set_active(view.auto_connect)
        self.updating_switch = False
        self.disconnect_button.set_sensitive(view.mode != "auto-connect-off")
        self.retry_button.set_sensitive(view.mode != "ready")

        if self.previous_mode == "ready" and view.mode != "ready":
            notification = Gio.Notification.new("PdaLink connection needs attention")
            notification.set_body(view.error or view.description)
            self.get_application().send_notification("connection-state", notification)
        elif self.previous_mode and self.previous_mode != "ready" and view.mode == "ready":
            notification = Gio.Notification.new("PdaLink reconnected")
            notification.set_body("Ubuntu internet is connected through PdaLink again.")
            self.get_application().send_notification("connection-state", notification)
        self.previous_mode = view.mode


class PdaLinkApplication(Adw.Application):
    def __init__(self) -> None:
        super().__init__(application_id=APP_ID, flags=Gio.ApplicationFlags.DEFAULT_FLAGS)

    def do_activate(self) -> None:
        window = self.props.active_window
        if window is None:
            window = PdaLinkWindow(self)
        window.present()


def main(argv: list[str] | None = None) -> int:
    app = PdaLinkApplication()
    return app.run(sys.argv if argv is None else argv)


if __name__ == "__main__":
    raise SystemExit(main())
