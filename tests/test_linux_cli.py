import unittest

import argparse
import base64
import io
import json
import os
from pathlib import Path
import select
import socket
import struct
import subprocess
import sys
import tempfile
import threading
import time
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime, timezone
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "linux"))

import pdalink_lite
import pdalink_tun


class LinuxCliTest(unittest.TestCase):
    def test_connection_uri_round_trip_and_reserved_characters(self):
        bundle = pdalink_lite.ConnectionBundle(
            transport="local-hotspot",
            ssid="Pda Link+1",
            security="wpa2-wpa3",
            password="pass&word=123",
            proxy_url="http://10.111.107.245:8001",
            host="10.111.107.245",
            port=8001,
        )

        parsed = pdalink_lite.parse_connection_uri(pdalink_lite.build_connection_uri(bundle))

        self.assertEqual(parsed, bundle)

    def test_connection_uri_rejects_duplicate_and_public_proxy(self):
        duplicate = (
            "pdalink://connect?v=1&v=1&transport=local-hotspot&ssid=PdaLink"
            "&security=wpa2-psk&password=password123&proxy=http%3A%2F%2F10.0.0.1%3A8001"
        )
        public = (
            "pdalink://connect?v=1&transport=local-hotspot&ssid=PdaLink"
            "&security=wpa2-psk&password=password123&proxy=http%3A%2F%2F8.8.8.8%3A8001"
        )

        with self.assertRaisesRegex(ValueError, "duplicate"):
            pdalink_lite.parse_connection_uri(duplicate)
        with self.assertRaisesRegex(ValueError, "private IPv4"):
            pdalink_lite.parse_connection_uri(public)

    def test_connection_import_prepares_local_hotspot_without_printing_password(self):
        uri = pdalink_lite.build_connection_uri(pdalink_lite.ConnectionBundle(
            transport="local-hotspot",
            ssid="AndroidShare_1234",
            security="wpa2-psk",
            password="generated-secret",
            proxy_url="http://10.111.107.245:8001",
            host="10.111.107.245",
            port=8001,
        ))
        args = argparse.Namespace(
            uri=uri,
            image="",
            camera=False,
            camera_index=0,
            scan_timeout=1.0,
            iface="wlp1s0",
            verify_timeout=2.0,
            no_connect=False,
        )
        stdout = io.StringIO()
        with mock.patch.object(
            pdalink_lite,
            "prepare_local_hotspot_profile",
            return_value="pdalink-local-hotspot",
        ) as prepare, mock.patch.object(
            pdalink_lite,
            "activate_connection_with_secret",
            return_value=pdalink_lite.CommandResult(0, "", ""),
        ) as activate, \
                mock.patch.object(pdalink_lite, "verify_after_connect", return_value=0) as verify, \
                redirect_stdout(stdout):
            self.assertEqual(pdalink_lite.connection_import(args), 0)

        self.assertNotIn("generated-secret", stdout.getvalue())
        self.assertEqual(prepare.call_args.args[1:], ("pdalink-local-hotspot", "wlp1s0"))
        activate.assert_called_once_with(
            "pdalink-local-hotspot",
            "generated-secret",
            "wlp1s0",
            timeout=30.0,
        )
        verify.assert_called_once_with("10.111.107.245", (8001,), 2.0)

    def test_local_hotspot_secret_is_passed_by_private_file_not_argv(self):
        observed = {}

        def fake_run(command, timeout):
            observed["command"] = command
            observed["secret"] = Path(command[-1]).read_text()
            observed["mode"] = Path(command[-1]).stat().st_mode & 0o777
            return pdalink_lite.CommandResult(0, "", "")

        with mock.patch.object(pdalink_lite, "run_command", side_effect=fake_run):
            result = pdalink_lite.activate_connection_with_secret(
                "pdalink-local-hotspot",
                "generated-secret",
                "wlp1s0",
            )

        self.assertEqual(result.returncode, 0)
        self.assertNotIn("generated-secret", observed["command"])
        self.assertEqual(observed["secret"], "802-11-wireless-security.psk:generated-secret\n")
        self.assertEqual(observed["mode"], 0o600)
        self.assertFalse(Path(observed["command"][-1]).exists())

    def local_hotspot_broadcast(self, **overrides):
        payload = {
            "ssid": "AndroidShare_1234",
            "password": "generated-secret",
            "interface": "swlan0",
            "host": "10.111.107.245",
            "port": 8001,
            "security": "WPA2/WPA3 transition",
        }
        payload.update(overrides)
        encoded = base64.urlsafe_b64encode(json.dumps(payload).encode()).decode().rstrip("=")
        return f'Broadcast completed: result=-1, data="{encoded}"\n'

    def test_parse_local_hotspot_broadcast_decodes_valid_snapshot(self):
        credentials = pdalink_lite.parse_local_hotspot_broadcast(self.local_hotspot_broadcast())

        self.assertEqual(credentials.ssid, "AndroidShare_1234")
        self.assertEqual(credentials.password, "generated-secret")
        self.assertEqual(credentials.host, "10.111.107.245")
        self.assertEqual(credentials.port, 8001)

    def test_parse_local_hotspot_broadcast_rejects_untrusted_endpoint(self):
        with self.assertRaisesRegex(ValueError, "private IPv4"):
            pdalink_lite.parse_local_hotspot_broadcast(
                self.local_hotspot_broadcast(host="8.8.8.8")
            )

    def test_auto_local_hotspot_sync_updates_profile_without_pin(self):
        credentials = pdalink_lite.parse_local_hotspot_broadcast(self.local_hotspot_broadcast())
        with tempfile.TemporaryDirectory() as tmp, \
                mock.patch.object(pdalink_lite, "prepare_local_hotspot_profile", return_value="pdalink-local-hotspot") as prepare:
            ok, status = pdalink_lite.apply_local_hotspot_credentials(
                credentials,
                mode="auto",
                profile_name="pdalink-local-hotspot",
                pin_path=Path(tmp) / "pin.json",
                iface="wlp1s0",
            )

            self.assertTrue(ok)
            self.assertEqual(status, "updated")
            self.assertFalse((Path(tmp) / "pin.json").exists())
            prepare.assert_called_once_with(credentials, "pdalink-local-hotspot", "wlp1s0")

    def test_prepare_local_hotspot_profile_does_not_put_secret_in_argv(self):
        credentials = pdalink_lite.parse_local_hotspot_broadcast(self.local_hotspot_broadcast())
        with mock.patch.object(pdalink_lite.shutil, "which", return_value="/usr/bin/nmcli"), \
                mock.patch.object(pdalink_lite, "saved_nm_connection_named", return_value=None), \
                mock.patch.object(
                    pdalink_lite,
                    "run_command",
                    return_value=pdalink_lite.CommandResult(0, "", ""),
                ) as run:
            self.assertEqual(
                pdalink_lite.prepare_local_hotspot_profile(
                    credentials,
                    "pdalink-local-hotspot",
                    "wlp1s0",
                ),
                "pdalink-local-hotspot",
            )

        command = run.call_args.args[0]
        self.assertNotIn("generated-secret", command)
        self.assertIn("wifi-sec.psk-flags", command)

    def test_target_relay_passes_secret_only_on_stdin(self):
        credentials = pdalink_lite.parse_local_hotspot_broadcast(self.local_hotspot_broadcast())
        with mock.patch.object(
            pdalink_lite,
            "run_command_with_input",
            return_value=pdalink_lite.CommandResult(0, "target: updated\n", ""),
        ) as run:
            result = pdalink_lite.relay_local_hotspot_credentials(
                credentials,
                "pdalink-zero",
            )

        self.assertEqual(result.returncode, 0)
        command, input_text = run.call_args.args[:2]
        self.assertNotIn("generated-secret", command)
        self.assertIn("generated-secret", input_text)
        self.assertEqual(command[-3:], ["sudo", "-n", "/usr/local/sbin/pdalink-target-apply"])

    def test_local_hotspot_sync_does_not_restart_an_active_reservation(self):
        credentials = pdalink_lite.parse_local_hotspot_broadcast(self.local_hotspot_broadcast())
        args = argparse.Namespace(
            start_android=True,
            serial="",
            wait=1.0,
            if_active=False,
            mode="auto",
            profile="pdalink-local-hotspot",
            pin_file="",
            iface="wlp1s0",
            reset_pin=False,
            connect=False,
            verify_timeout=2.0,
        )

        stdout = io.StringIO()
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(
            pdalink_lite,
            "query_local_hotspot_credentials",
            return_value=credentials,
        ) as query, mock.patch.object(pdalink_lite, "adb_start_local_hotspot") as start, \
                mock.patch.object(pdalink_lite, "local_hotspot_sync_state_matches", return_value=False), \
                mock.patch.object(pdalink_lite, "local_hotspot_profile_active", return_value=False), \
                mock.patch.object(pdalink_lite, "apply_local_hotspot_credentials", return_value=(True, "unchanged")), \
                redirect_stdout(stdout):
            args.sync_state_file = str(Path(tmp) / "sync.json")
            self.assertEqual(pdalink_lite.local_hotspot_sync(args), 0)

        query.assert_called_once_with("")
        start.assert_not_called()
        self.assertIn("credential-fingerprint: ", stdout.getvalue())
        self.assertNotIn(credentials.password, stdout.getvalue())

    def test_local_hotspot_target_sync_is_independent_and_redacts_secret(self):
        credentials = pdalink_lite.parse_local_hotspot_broadcast(self.local_hotspot_broadcast())
        args = argparse.Namespace(serial="phone", target="pdalink-zero")
        stdout = io.StringIO()
        with mock.patch.object(
            pdalink_lite,
            "query_local_hotspot_credentials",
            return_value=credentials,
        ) as query, mock.patch.object(
            pdalink_lite,
            "relay_local_hotspot_credentials",
            return_value=pdalink_lite.CommandResult(0, "target: updated\n", ""),
        ) as relay, redirect_stdout(stdout):
            self.assertEqual(pdalink_lite.local_hotspot_target_sync(args), 0)

        query.assert_called_once_with("phone")
        relay.assert_called_once_with(credentials, "pdalink-zero")
        self.assertIn("local-hotspot target: updated", stdout.getvalue())
        self.assertIn("credential-fingerprint: ", stdout.getvalue())
        self.assertNotIn(credentials.password, stdout.getvalue())

    def test_local_hotspot_target_failure_does_not_print_secret(self):
        credentials = pdalink_lite.parse_local_hotspot_broadcast(self.local_hotspot_broadcast())
        args = argparse.Namespace(serial="phone", target="pdalink-zero")
        stderr = io.StringIO()
        with mock.patch.object(
            pdalink_lite,
            "query_local_hotspot_credentials",
            return_value=credentials,
        ), mock.patch.object(
            pdalink_lite,
            "relay_local_hotspot_credentials",
            return_value=pdalink_lite.CommandResult(255, "", "target unavailable"),
        ), redirect_stderr(stderr):
            self.assertEqual(pdalink_lite.local_hotspot_target_sync(args), 1)

        self.assertIn("target unavailable", stderr.getvalue())
        self.assertNotIn(credentials.password, stderr.getvalue())

    def test_local_hotspot_sync_starts_android_only_when_inactive(self):
        credentials = pdalink_lite.parse_local_hotspot_broadcast(self.local_hotspot_broadcast())
        args = argparse.Namespace(
            start_android=True,
            serial="phone",
            wait=1.0,
            if_active=False,
            mode="auto",
            profile="pdalink-local-hotspot",
            pin_file="",
            iface="wlp1s0",
            reset_pin=False,
            connect=False,
            verify_timeout=2.0,
        )

        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(
            pdalink_lite,
            "query_local_hotspot_credentials",
            side_effect=(ValueError("Local Hotspot is inactive"), credentials),
        ) as query, mock.patch.object(
            pdalink_lite,
            "adb_start_local_hotspot",
            return_value=0,
        ) as start, mock.patch.object(
            pdalink_lite,
            "apply_local_hotspot_credentials",
            return_value=(True, "updated"),
        ), mock.patch.object(
            pdalink_lite,
            "local_hotspot_sync_state_matches",
            return_value=False,
        ), mock.patch.object(
            pdalink_lite,
            "local_hotspot_profile_active",
            return_value=False,
        ):
            args.sync_state_file = str(Path(tmp) / "sync.json")
            self.assertEqual(pdalink_lite.local_hotspot_sync(args), 0)

        self.assertEqual(query.call_count, 2)
        start.assert_called_once_with("phone")

    def test_local_hotspot_sync_does_not_reactivate_unchanged_active_profile(self):
        credentials = pdalink_lite.parse_local_hotspot_broadcast(self.local_hotspot_broadcast())
        args = argparse.Namespace(
            start_android=False,
            serial="phone",
            wait=0.0,
            if_active=False,
            mode="auto",
            profile="pdalink-local-hotspot",
            pin_file="",
            sync_state_file="",
            iface="wlp1s0",
            reset_pin=False,
            connect=True,
            target="off",
            verify_timeout=2.0,
        )

        with mock.patch.object(
            pdalink_lite,
            "query_local_hotspot_credentials",
            return_value=credentials,
        ), mock.patch.object(
            pdalink_lite,
            "local_hotspot_sync_state_matches",
            return_value=True,
        ), mock.patch.object(
            pdalink_lite,
            "local_hotspot_profile_active",
            return_value=True,
        ), mock.patch.object(
            pdalink_lite,
            "apply_local_hotspot_credentials",
        ) as apply, mock.patch.object(
            pdalink_lite,
            "activate_connection_with_secret",
        ) as activate:
            self.assertEqual(pdalink_lite.local_hotspot_sync(args), 0)

        apply.assert_not_called()
        activate.assert_not_called()

    def test_pinned_local_hotspot_sync_hashes_password_and_refuses_rotation(self):
        credentials = pdalink_lite.parse_local_hotspot_broadcast(self.local_hotspot_broadcast())
        rotated = pdalink_lite.parse_local_hotspot_broadcast(
            self.local_hotspot_broadcast(password="rotated-secret")
        )
        with tempfile.TemporaryDirectory() as tmp, \
                mock.patch.object(pdalink_lite, "prepare_local_hotspot_profile", return_value="pdalink-local-hotspot") as prepare:
            pin_path = Path(tmp) / "pin.json"
            ok, status = pdalink_lite.apply_local_hotspot_credentials(
                credentials,
                mode="pinned",
                profile_name="pdalink-local-hotspot",
                pin_path=pin_path,
                iface=None,
            )
            self.assertTrue(ok)
            self.assertEqual(status, "pinned")
            pin = json.loads(pin_path.read_text())
            self.assertNotIn("password", pin)
            self.assertIn("password_sha256", pin)
            self.assertEqual(pin_path.stat().st_mode & 0o777, 0o600)

            prepare.reset_mock()
            ok, status = pdalink_lite.apply_local_hotspot_credentials(
                rotated,
                mode="pinned",
                profile_name="pdalink-local-hotspot",
                pin_path=pin_path,
                iface=None,
            )
            self.assertFalse(ok)
            self.assertEqual(status, "credential-mismatch")
            prepare.assert_not_called()

    def test_pinned_local_hotspot_sync_rejects_filesystem_without_private_modes(self):
        credentials = pdalink_lite.parse_local_hotspot_broadcast(self.local_hotspot_broadcast())
        with tempfile.TemporaryDirectory() as tmp, \
                mock.patch.object(pdalink_lite, "prepare_local_hotspot_profile", return_value="pdalink-local-hotspot"), \
                mock.patch.object(pdalink_lite, "local_hotspot_pin_mode", return_value=0o755):
            pin_path = Path(tmp) / "pin.json"
            with self.assertRaisesRegex(ValueError, "mode 0600"):
                pdalink_lite.apply_local_hotspot_credentials(
                    credentials,
                    mode="pinned",
                    profile_name="pdalink-local-hotspot",
                    pin_path=pin_path,
                    iface=None,
                )

            self.assertFalse(pin_path.exists())

    def test_parse_ports_default(self):
        self.assertEqual(pdalink_lite.parse_ports(None), (8000, 8001))

    def test_parse_ports_custom(self):
        self.assertEqual(pdalink_lite.parse_ports("8001, 9000"), (8001, 9000))

    def test_build_proxy_env(self):
        env = pdalink_lite.build_proxy_env("192.168.49.1", 8000)
        self.assertEqual(env["https_proxy"], "http://192.168.49.1:8000")
        self.assertIn("192.168.49.1", env["no_proxy"])

    def test_parse_proxy_from_pac(self):
        pac = 'function FindProxyForURL(url, host) { return "PROXY 192.168.49.1:8001; DIRECT"; }'
        self.assertEqual(pdalink_lite.parse_proxy_from_pac(pac), ("192.168.49.1", 8001))

    def test_parse_proxy_from_pac_missing(self):
        self.assertIsNone(pdalink_lite.parse_proxy_from_pac("DIRECT"))

    def test_probe_one_requires_pac_proxy_line(self):
        with mock.patch.object(pdalink_lite, "request_pac", return_value="DIRECT"):
            result = pdalink_lite.probe_one("127.0.0.1", 18080)

        self.assertFalse(result.ok)
        self.assertIn("PAC proxy line not found", result.detail)

    def test_expected_pdalink_pac_rejects_different_advertised_proxy(self):
        pac = 'function FindProxyForURL(url, host) { return "PROXY 10.0.0.1:8888; DIRECT"; }'
        with mock.patch.object(pdalink_lite, "request_pac", return_value=pac):
            state, detail = pdalink_lite.expected_pdalink_pac_status("192.168.49.1", 8001)

        self.assertEqual(state, "untrusted")
        self.assertIn("expected 192.168.49.1:8001", detail)

    def test_first_verified_pdalink_port_skips_untrusted_listener(self):
        with mock.patch.object(pdalink_lite, "route_info", return_value=pdalink_lite.RouteInfo(
            "192.168.49.1 dev wlp1s0 src 192.168.49.2",
            dev="wlp1s0",
            src="192.168.49.2",
        )), mock.patch.object(pdalink_lite, "expected_pdalink_pac_status", side_effect=[
            ("untrusted", "wrong proxy"),
            ("active", "expected proxy"),
        ]):
            port = pdalink_lite.first_verified_pdalink_port("192.168.49.1", (8000, 8001))

        self.assertEqual(port, 8001)

    def test_parse_route_info(self):
        route = pdalink_lite.parse_route_info(
            "192.168.49.1 dev wlp62s0 src 192.168.49.243 uid 1000 cache"
        )
        self.assertEqual(route.dev, "wlp62s0")
        self.assertEqual(route.src, "192.168.49.243")
        self.assertEqual(route.via, "")

    def test_route_warning_for_wrong_gateway(self):
        route = pdalink_lite.parse_route_info(
            "192.168.49.1 via 192.168.0.1 dev enp61s0 src 192.168.0.102 uid 1000 cache"
        )
        warning = pdalink_lite.route_warning("192.168.49.1", route)
        self.assertIsNotNone(warning)
        self.assertIn("DIRECT-*", warning)

    def test_route_warning_skips_wifi_direct_route(self):
        route = pdalink_lite.parse_route_info(
            "192.168.49.1 dev wlp62s0 src 192.168.49.243 uid 1000 cache"
        )
        self.assertIsNone(pdalink_lite.route_warning("192.168.49.1", route))

    def test_split_nmcli_terse_unescapes_colons(self):
        fields = pdalink_lite.split_nmcli_terse(r"DIRECT\:PU:WPA2:80:")
        self.assertEqual(fields, ["DIRECT:PU", "WPA2", "80", ""])

    def test_parse_nmcli_wifi_list(self):
        networks = pdalink_lite.parse_nmcli_wifi_list(
            "Home:WPA2:50:*\nDIRECT-PU-HY:S21 WPA2:82:\n"
        )
        self.assertEqual(networks[1].ssid, "DIRECT-PU-HY")
        self.assertEqual(networks[1].signal, 82)
        self.assertFalse(networks[1].in_use)

    def test_parse_nmcli_active_connections(self):
        connections = pdalink_lite.parse_nmcli_active_connections(
            r"pdalink-wifi-direct:802-11-wireless:wlp1s0"
            "\n"
            r"DIRECT\:test:802-11-wireless:wlp2s0"
            "\n"
        )

        self.assertEqual(connections[0].name, "pdalink-wifi-direct")
        self.assertEqual(connections[0].device, "wlp1s0")
        self.assertEqual(connections[1].name, "DIRECT:test")

    def test_active_wifi_direct_connection_matches_profile_ssid(self):
        with mock.patch.object(pdalink_lite, "active_nm_connections", return_value=[
            pdalink_lite.NmActiveConnection("Home", "802-11-wireless", "wlp1s0"),
            pdalink_lite.NmActiveConnection("pdalink-wifi-direct", "802-11-wireless", "wlp1s0"),
        ]), mock.patch.object(pdalink_lite, "nmcli_connection_ssid", side_effect={
            "Home": "Home",
            "pdalink-wifi-direct": "DIRECT-PU-HY",
        }.__getitem__):
            connection = pdalink_lite.active_wifi_direct_connection("DIRECT-PU-HY", "wlp1s0")

        self.assertIsNotNone(connection)
        self.assertEqual(connection.name, "pdalink-wifi-direct")

    def test_update_wifi_direct_profile_secret_updates_saved_profile_by_ssid(self):
        commands = []

        def fake_run_command(command, timeout):
            commands.append(command)
            return pdalink_lite.CommandResult(0, "", "")

        with mock.patch.object(pdalink_lite, "saved_nm_connections", return_value=[
            pdalink_lite.NmActiveConnection("pdalink-wifi-direct", "802-11-wireless"),
        ]), mock.patch.object(pdalink_lite, "nmcli_connection_ssid", return_value="DIRECT-PU-HY"), \
                mock.patch.object(pdalink_lite, "run_command", side_effect=fake_run_command):
            self.assertTrue(pdalink_lite.update_wifi_direct_profile_secret("DIRECT-PU-HY", "new-passphrase"))

        self.assertEqual(commands, [[
            "nmcli",
            "connection",
            "modify",
            "pdalink-wifi-direct",
            "wifi-sec.key-mgmt",
            "wpa-psk",
            "wifi-sec.psk",
            "new-passphrase",
        ]])

    def test_prepare_wifi_direct_profile_adds_local_only_profile_before_activation(self):
        commands = []

        def fake_run_command(command, timeout):
            commands.append(command)
            return pdalink_lite.CommandResult(0, "", "")

        with mock.patch.object(pdalink_lite.shutil, "which", return_value="/usr/bin/nmcli"), \
                mock.patch.object(pdalink_lite, "wifi_connection_profiles_for_ssid", return_value=[]), \
                mock.patch.object(pdalink_lite, "saved_nm_connection_named", return_value=None), \
                mock.patch.object(pdalink_lite, "run_command", side_effect=fake_run_command):
            profile = pdalink_lite.prepare_wifi_direct_profile("DIRECT-PU-HY", "new-passphrase", "wlp1s0")

        self.assertEqual(profile, "pdalink-wifi-direct")
        self.assertEqual(commands[0][:6], ["nmcli", "connection", "add", "type", "wifi", "ifname"])
        self.assertIn("pdalink-wifi-direct", commands[0])
        self.assertIn("wifi-sec.psk", commands[0])
        self.assertIn("connection.autoconnect", commands[0])
        self.assertIn("802-11-wireless.cloned-mac-address", commands[0])
        self.assertIn("ipv4.never-default", commands[0])

    def test_connect_uses_prepared_profile_without_post_activation_modify(self):
        args = argparse.Namespace(
            ssid="DIRECT-PU-HY",
            password="new-passphrase",
            iface="wlp1s0",
            verify=False,
            host="192.168.49.1",
            ports="8000",
            verify_timeout=0,
        )
        commands = []

        def fake_run_nmcli(command):
            commands.append(command)
            return 0

        with mock.patch.object(pdalink_lite, "prepare_wifi_direct_profile", return_value="pdalink-wifi-direct"), \
                mock.patch.object(pdalink_lite, "configure_wifi_direct_profile") as configure, \
                mock.patch.object(pdalink_lite, "run_nmcli", side_effect=fake_run_nmcli):
            self.assertEqual(pdalink_lite.connect(args), 0)

        self.assertEqual(commands, [["connection", "up", "pdalink-wifi-direct", "ifname", "wlp1s0"]])
        configure.assert_not_called()

    def test_enable_gnome_wifi_proxy_snapshots_then_applies_pac(self):
        with tempfile.TemporaryDirectory() as tmp:
            state_path = Path(tmp) / "wifi-proxy.json"
            writes = []
            with mock.patch.object(pdalink_lite, "gnome_proxy_snapshot", return_value={
                "mode": "'none'",
                "autoconfig-url": "''",
            }), mock.patch.object(pdalink_lite, "gsettings_set", side_effect=lambda key, value: writes.append((key, value))), \
                    mock.patch.object(pdalink_lite, "gnome_proxy_owned_by_pdalink", return_value=True):
                pdalink_lite.enable_gnome_wifi_proxy(
                    state_path=state_path,
                    pac_url="http://192.168.49.1:8001/proxy.pac",
                    ssid="DIRECT-PD-S21",
                    profile_name="pdalink-wifi-direct",
                    disconnect_on_stop=True,
                )

            state = json.loads(state_path.read_text(encoding="utf-8"))
            self.assertEqual(state["before"]["mode"], "'none'")
            self.assertEqual(state["pac_url"], "http://192.168.49.1:8001/proxy.pac")
            self.assertNotIn("password", state)
            self.assertEqual(state_path.stat().st_mode & 0o777, 0o600)
            self.assertEqual(writes, [
                ("autoconfig-url", "'http://192.168.49.1:8001/proxy.pac'"),
                ("mode", "'auto'"),
            ])

    def test_enable_gnome_wifi_proxy_rolls_back_failed_apply(self):
        with tempfile.TemporaryDirectory() as tmp:
            state_path = Path(tmp) / "wifi-proxy.json"
            before = {"mode": "'manual'", "autoconfig-url": "'https://old/pac'"}
            with mock.patch.object(pdalink_lite, "gnome_proxy_snapshot", return_value=before), \
                    mock.patch.object(pdalink_lite, "gsettings_set", side_effect=[None, RuntimeError("set failed")]), \
                    mock.patch.object(pdalink_lite, "restore_gnome_proxy") as restore:
                with self.assertRaisesRegex(RuntimeError, "set failed"):
                    pdalink_lite.enable_gnome_wifi_proxy(
                        state_path=state_path,
                        pac_url="http://192.168.49.1:8001/proxy.pac",
                        ssid="DIRECT-PD-S21",
                        profile_name="pdalink-wifi-direct",
                        disconnect_on_stop=True,
                    )

            restore.assert_called_once_with(before)
            self.assertFalse(state_path.exists())

    def test_enable_gnome_wifi_proxy_keeps_state_when_rollback_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            state_path = Path(tmp) / "wifi-proxy.json"
            before = {"mode": "'manual'", "autoconfig-url": "'https://old/pac'"}
            with mock.patch.object(pdalink_lite, "gnome_proxy_snapshot", return_value=before), \
                    mock.patch.object(pdalink_lite, "gsettings_set", side_effect=RuntimeError("apply failed")), \
                    mock.patch.object(pdalink_lite, "restore_gnome_proxy", side_effect=RuntimeError("restore failed")):
                with self.assertRaisesRegex(RuntimeError, "Restore state retained"):
                    pdalink_lite.enable_gnome_wifi_proxy(
                        state_path=state_path,
                        pac_url="http://192.168.49.1:8001/proxy.pac",
                        ssid="DIRECT-PD-S21",
                        profile_name="pdalink-wifi-direct",
                        disconnect_on_stop=True,
                    )

            self.assertTrue(state_path.exists())

    def test_create_private_json_does_not_overwrite_existing_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            state_path = Path(tmp) / "wifi-proxy.json"
            state_path.write_text('{"owner": "first"}\n', encoding="utf-8")

            with self.assertRaisesRegex(RuntimeError, "already exists"):
                pdalink_lite.create_private_json(state_path, {"owner": "second"})

            self.assertEqual(json.loads(state_path.read_text(encoding="utf-8")), {"owner": "first"})

    def test_create_private_json_reports_storage_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            state_path = Path(tmp) / "state.json"
            with mock.patch.object(pdalink_lite.os, "open", side_effect=PermissionError("denied")):
                with self.assertRaisesRegex(RuntimeError, "cannot create Wi-Fi Direct proxy state"):
                    pdalink_lite.create_private_json(state_path, {"owner": "pdalink"})

    def test_wifi_proxy_start_lock_refuses_concurrent_holder(self):
        with tempfile.TemporaryDirectory() as tmp:
            state_path = Path(tmp) / "wifi-proxy.json"
            first_fd = pdalink_lite.acquire_wifi_proxy_start_lock(state_path)
            try:
                with self.assertRaisesRegex(RuntimeError, "already running"):
                    pdalink_lite.acquire_wifi_proxy_start_lock(state_path)
            finally:
                os.close(first_fd)

            second_fd = pdalink_lite.acquire_wifi_proxy_start_lock(state_path)
            os.close(second_fd)

    def test_load_wifi_proxy_state_rejects_unexpected_owner(self):
        with tempfile.TemporaryDirectory() as tmp:
            state_path = Path(tmp) / "wifi-proxy.json"
            pdalink_lite.atomic_write_json(state_path, {
                "version": 1,
                "transport": "wifi-direct",
                "manager": "other-tool",
                "pac_url": "http://192.168.49.1:8001/proxy.pac",
                "profile_name": "pdalink-wifi-direct",
                "disconnect_on_stop": True,
                "before": {"mode": "'none'", "autoconfig-url": "''"},
            })

            with self.assertRaisesRegex(ValueError, "unexpected owner"):
                pdalink_lite.load_wifi_proxy_state(state_path)

    def test_wifi_internet_start_refuses_existing_restore_state_before_connect(self):
        args = argparse.Namespace(
            state_file="",
            ssid=None,
            password="secret123",
            iface=None,
            ssid_pattern="DIRECT-",
            verify_timeout=1,
            host="192.168.49.1",
            ports="8000,8001",
        )
        with tempfile.TemporaryDirectory() as tmp:
            state_path = Path(tmp) / "wifi-proxy.json"
            state_path.write_text("{}", encoding="utf-8")
            args.state_file = str(state_path)
            with mock.patch.object(pdalink_lite, "connect_auto") as connect_auto, redirect_stderr(io.StringIO()):
                self.assertEqual(pdalink_lite.wifi_internet_start(args), 2)

        connect_auto.assert_not_called()

    def test_wifi_internet_stop_refuses_proxy_drift(self):
        args = argparse.Namespace(state_file="", force=False, keep_wifi=False)
        with tempfile.TemporaryDirectory() as tmp:
            state_path = Path(tmp) / "wifi-proxy.json"
            args.state_file = str(state_path)
            pdalink_lite.atomic_write_json(state_path, {
                "version": 1,
                "transport": "wifi-direct",
                "manager": "gsettings",
                "pac_url": "http://192.168.49.1:8001/proxy.pac",
                "profile_name": "pdalink-wifi-direct",
                "disconnect_on_stop": True,
                "before": {"mode": "'none'", "autoconfig-url": "''"},
            })
            with mock.patch.object(pdalink_lite, "gnome_proxy_owned_by_pdalink", return_value=False), \
                    mock.patch.object(pdalink_lite, "restore_gnome_proxy") as restore, \
                    redirect_stderr(io.StringIO()):
                self.assertEqual(pdalink_lite.wifi_internet_stop(args), 2)

            restore.assert_not_called()
            self.assertTrue(state_path.exists())

    def test_wifi_internet_stop_restores_and_disconnects_owned_connection(self):
        args = argparse.Namespace(state_file="", force=False, keep_wifi=False)
        with tempfile.TemporaryDirectory() as tmp:
            state_path = Path(tmp) / "wifi-proxy.json"
            args.state_file = str(state_path)
            before = {"mode": "'none'", "autoconfig-url": "''"}
            pdalink_lite.atomic_write_json(state_path, {
                "version": 1,
                "transport": "wifi-direct",
                "manager": "gsettings",
                "pac_url": "http://192.168.49.1:8001/proxy.pac",
                "profile_name": "pdalink-wifi-direct",
                "disconnect_on_stop": True,
                "before": before,
            })
            with mock.patch.object(pdalink_lite, "gnome_proxy_owned_by_pdalink", return_value=True), \
                    mock.patch.object(pdalink_lite, "restore_gnome_proxy") as restore, \
                    mock.patch.object(pdalink_lite, "disconnect_wifi_profile", return_value=True) as disconnect, \
                    redirect_stdout(io.StringIO()):
                self.assertEqual(pdalink_lite.wifi_internet_stop(args), 0)

            restore.assert_called_once_with(before)
            disconnect.assert_called_once_with("pdalink-wifi-direct")
            self.assertFalse(state_path.exists())

    def test_configure_wifi_direct_profile_marks_connection_local_only(self):
        commands = []

        def fake_run_command(command, timeout):
            commands.append(command)
            return pdalink_lite.CommandResult(0, "", "")

        stdout = io.StringIO()
        with mock.patch.object(pdalink_lite, "active_wifi_direct_connection", return_value=pdalink_lite.NmActiveConnection(
            "pdalink-wifi-direct",
            "802-11-wireless",
            "wlp1s0",
        )), mock.patch.object(pdalink_lite, "run_command", side_effect=fake_run_command), redirect_stdout(stdout):
            self.assertTrue(pdalink_lite.configure_wifi_direct_profile("DIRECT-PU-HY", "wlp1s0"))

        self.assertEqual(commands[0][:4], ["nmcli", "connection", "modify", "pdalink-wifi-direct"])
        self.assertIn("connection.autoconnect", commands[0])
        self.assertIn("802-11-wireless.cloned-mac-address", commands[0])
        self.assertIn("permanent", commands[0])
        self.assertIn("ipv4.never-default", commands[0])
        self.assertIn("ipv4.ignore-auto-dns", commands[0])
        self.assertIn("ipv6.method", commands[0])
        self.assertEqual(commands[1], ["nmcli", "device", "reapply", "wlp1s0"])
        self.assertIn("local-only Wi-Fi Direct", stdout.getvalue())

    def test_choose_wifi_direct_network_prefers_strongest_direct(self):
        networks = [
            pdalink_lite.WifiNetwork("Home", "WPA2", 100),
            pdalink_lite.WifiNetwork("DIRECT-low", "WPA2", 20),
            pdalink_lite.WifiNetwork("DIRECT-high", "WPA2", 80),
        ]
        chosen = pdalink_lite.choose_wifi_direct_network(networks)
        self.assertIsNotNone(chosen)
        self.assertEqual(chosen.ssid, "DIRECT-high")

    def test_choose_wifi_direct_network_prefers_active(self):
        networks = [
            pdalink_lite.WifiNetwork("DIRECT-high", "WPA2", 80),
            pdalink_lite.WifiNetwork("DIRECT-active", "WPA2", 40, in_use=True),
        ]
        chosen = pdalink_lite.choose_wifi_direct_network(networks)
        self.assertIsNotNone(chosen)
        self.assertEqual(chosen.ssid, "DIRECT-active")

    def test_doctor_reports_working_proxy(self):
        args = argparse.Namespace(
            host="192.168.49.1",
            ports="8000",
            curl_url=None,
            adb=False,
            adb_log_lines=80,
        )
        stdout = io.StringIO()
        with mock.patch.object(pdalink_lite, "tool_statuses", return_value=[
            pdalink_lite.ToolStatus("nmcli", "/usr/bin/nmcli"),
        ]), mock.patch.object(pdalink_lite, "route_info", return_value=pdalink_lite.parse_route_info(
            "192.168.49.1 dev wlp62s0 src 192.168.49.243 uid 1000 cache"
        )), mock.patch.object(pdalink_lite, "probe_ports", return_value=[
            pdalink_lite.ProbeResult("192.168.49.1", 8000, True, "PAC advertises 192.168.49.1:8000"),
        ]), redirect_stdout(stdout):
            status = pdalink_lite.doctor(args)

        self.assertEqual(status, 0)
        self.assertIn("proxy: http://192.168.49.1:8000", stdout.getvalue())

    def test_connect_auto_distinguishes_nmcli_failure_from_missing_tool(self):
        args = argparse.Namespace(
            iface="missing0",
            ssid_pattern="DIRECT-",
            password="password123",
            host="192.168.49.1",
            ports="8000",
            verify_timeout=0,
        )
        stderr = io.StringIO()
        with mock.patch.object(pdalink_lite, "scan_wifi_networks", side_effect=RuntimeError("Device not found")), \
                redirect_stderr(stderr):
            self.assertEqual(pdalink_lite.connect_auto(args), 1)

    def test_active_wifi_direct_network_requires_in_use_direct_network(self):
        networks = [
            pdalink_lite.WifiNetwork("DIRECT-visible", "WPA2", 90, in_use=False),
            pdalink_lite.WifiNetwork("Home", "WPA2", 50, in_use=True),
        ]

        self.assertIsNone(pdalink_lite.active_wifi_direct_network(networks))

    def test_wifi_status_reports_ready_proxy(self):
        args = argparse.Namespace(
            host="192.168.49.1",
            ports="8000,8001",
            ssid_pattern="DIRECT-",
            iface=None,
        )
        stdout = io.StringIO()
        with mock.patch.object(pdalink_lite, "tool_statuses", return_value=[
            pdalink_lite.ToolStatus("nmcli", "/usr/bin/nmcli"),
            pdalink_lite.ToolStatus("ip", "/usr/sbin/ip"),
            pdalink_lite.ToolStatus("curl", "/usr/bin/curl"),
        ]), mock.patch.object(pdalink_lite, "active_wifi_direct_network_from_nmcli", return_value=pdalink_lite.WifiNetwork(
            "DIRECT-PD-S21", "", -1, in_use=True,
        )), mock.patch.object(pdalink_lite, "scan_wifi_networks", return_value=[
            pdalink_lite.WifiNetwork("DIRECT-PD-S21", "WPA2", 80, in_use=True),
        ]), mock.patch.object(pdalink_lite, "route_info", return_value=pdalink_lite.parse_route_info(
            "192.168.49.1 dev wlp62s0 src 192.168.49.243 uid 1000 cache"
        )), mock.patch.object(pdalink_lite, "probe_ports", return_value=[
            pdalink_lite.ProbeResult("192.168.49.1", 8000, True, "PAC advertises 192.168.49.1:8000"),
            pdalink_lite.ProbeResult("192.168.49.1", 8001, False, "Connection refused"),
        ]), redirect_stdout(stdout):
            self.assertEqual(pdalink_lite.wifi_status(args), 0)

        output = stdout.getvalue()
        self.assertIn("transport: wifi-direct (manual)", output)
        self.assertIn("wifi: OK DIRECT-PD-S21", output)
        self.assertIn("route: OK", output)
        self.assertIn("proxy: OK http://192.168.49.1:8000", output)
        self.assertIn("./tools/pdalink.sh wifi curl-test", output)

    def test_wifi_status_points_to_connect_when_not_on_direct_network(self):
        args = argparse.Namespace(
            host="192.168.49.1",
            ports="8000",
            ssid_pattern="DIRECT-",
            iface=None,
        )
        stdout = io.StringIO()
        with mock.patch.object(pdalink_lite, "tool_statuses", return_value=[
            pdalink_lite.ToolStatus("nmcli", "/usr/bin/nmcli"),
            pdalink_lite.ToolStatus("ip", "/usr/sbin/ip"),
            pdalink_lite.ToolStatus("curl", "/usr/bin/curl"),
        ]), mock.patch.object(pdalink_lite, "active_wifi_direct_network_from_nmcli", return_value=None), \
                mock.patch.object(pdalink_lite, "scan_wifi_networks", return_value=[
            pdalink_lite.WifiNetwork("Home", "WPA2", 80, in_use=True),
        ]), mock.patch.object(pdalink_lite, "route_info", return_value=pdalink_lite.parse_route_info(
            "192.168.49.1 via 192.168.0.1 dev wlp62s0 src 192.168.0.20 uid 1000 cache"
        )), mock.patch.object(pdalink_lite, "probe_ports", return_value=[
            pdalink_lite.ProbeResult("192.168.49.1", 8000, False, "Network unreachable"),
        ]), redirect_stdout(stdout):
            self.assertEqual(pdalink_lite.wifi_status(args), 1)

        output = stdout.getvalue()
        self.assertIn("wifi: MISSING", output)
        self.assertIn("wifi connect --password", output)

    def test_parse_adb_forward_list(self):
        forwards = pdalink_lite.parse_adb_forward_list(
            "R3CR406AAGE tcp:18080 tcp:8000\nignored\nR3CR406AAGE localabstract:x tcp:9000\n"
        )
        self.assertEqual(len(forwards), 2)
        self.assertEqual(forwards[0].local_port, 18080)
        self.assertEqual(forwards[0].remote_port, 8000)
        self.assertIsNone(forwards[1].local_port)
        self.assertEqual(forwards[1].remote_port, 9000)

    def test_parse_adb_devices(self):
        devices = pdalink_lite.parse_adb_devices(
            "List of devices attached\n"
            "R3CR406AAGE device usb:3-1 model:SM_G996N\n"
            "emulator-5554 unauthorized\n"
        )
        self.assertEqual(len(devices), 2)
        self.assertTrue(devices[0].ok)
        self.assertEqual(devices[0].serial, "R3CR406AAGE")
        self.assertEqual(devices[1].state, "unauthorized")

    def test_parse_adb_devices_keeps_no_permissions_state(self):
        devices = pdalink_lite.parse_adb_devices(
            "List of devices attached\n"
            "R3CR406AAGE no permissions (missing udev rules? user is in the plugdev group) "
            "usb:1-2 transport_id:1\n"
        )
        self.assertEqual(len(devices), 1)
        self.assertEqual(devices[0].serial, "R3CR406AAGE")
        self.assertEqual(devices[0].state, "no permissions")
        self.assertIn("missing udev rules", devices[0].detail)

    def test_parse_lsusb_ids_reports_managed_android_usb_ids(self):
        ids = pdalink_lite.parse_lsusb_ids(
            "Bus 001 Device 005: ID 04e8:6864 Samsung Electronics Co., Ltd GT-I9070 (network tethering, USB debugging enabled)\n"
            "Bus 002 Device 003: ID 18d1:2d01 Google Inc. Android Open Accessory\n"
            "Bus 001 Device 002: ID 8087:0a2b Intel Corp. Bluetooth wireless interface\n"
        )

        self.assertEqual(ids, ["04e8:6864", "18d1:2d01"])

    def test_usb_next_steps_prefers_usb_authorization(self):
        steps = pdalink_lite.usb_next_steps(
            True,
            [pdalink_lite.AdbDevice("R3CR406AAGE", "unauthorized")],
            False,
            None,
            18080,
            8000,
        )
        self.assertIn("approve the USB debugging RSA prompt", steps[0])

    def test_usb_next_steps_points_to_udev_for_no_permissions(self):
        steps = pdalink_lite.usb_next_steps(
            True,
            [pdalink_lite.AdbDevice("R3CR406AAGE", "no permissions", "missing udev rules")],
            False,
            None,
            18080,
            8000,
        )
        self.assertIn("udev", steps[0])
        self.assertIn("./tools/pdalink.sh rescue udev", steps[0])
        self.assertIn("04e8:6864", steps[0])
        self.assertIn("18d1:2d01", steps[0])

    def test_usb_next_steps_ready_points_to_smoke(self):
        steps = pdalink_lite.usb_next_steps(
            True,
            [pdalink_lite.AdbDevice("R3CR406AAGE", "device")],
            True,
            pdalink_lite.ProbeResult("127.0.0.1", 18080, True, "PAC advertises 192.168.49.1:8000"),
            18080,
            8000,
        )
        self.assertIn("usb-smoke --no-start-android", steps[0])

    def test_trusted_device_store_round_trips_default(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "trusted-devices.json"
            store = pdalink_lite.TrustedDeviceStore(
                "R3CR406AAGE",
                {"R3CR406AAGE": {"serial": "R3CR406AAGE", "name": "main-phone"}},
            )
            pdalink_lite.write_trusted_devices(path, store)

            loaded = pdalink_lite.load_trusted_devices(path)

            self.assertEqual(loaded.default_serial, "R3CR406AAGE")
            self.assertEqual(loaded.devices["R3CR406AAGE"]["name"], "main-phone")

    def test_device_trust_connected_refuses_multiple_authorized_devices(self):
        args = argparse.Namespace(
            serial=None,
            connected=True,
            name="",
            default=False,
            trusted_devices_file="",
        )
        with tempfile.TemporaryDirectory() as tmp:
            args.trusted_devices_file = str(Path(tmp) / "trusted-devices.json")
            with mock.patch.object(pdalink_lite, "adb_run", return_value=pdalink_lite.CommandResult(
                0,
                "List of devices attached\nA device\nB device\n",
                "",
            )), redirect_stderr(io.StringIO()):
                self.assertEqual(pdalink_lite.device_trust(args), 2)

    def test_device_trust_connected_writes_metadata_and_default(self):
        args = argparse.Namespace(
            serial=None,
            connected=True,
            name="main-phone",
            default=True,
            trusted_devices_file="",
        )
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "trusted-devices.json"
            args.trusted_devices_file = str(path)
            with mock.patch.object(pdalink_lite, "adb_run", return_value=pdalink_lite.CommandResult(
                0,
                "List of devices attached\nR3CR406AAGE device model:SM_G996N product:p3s\n",
                "",
            )), redirect_stdout(io.StringIO()):
                self.assertEqual(pdalink_lite.device_trust(args), 0)

            loaded = pdalink_lite.load_trusted_devices(path)
            self.assertEqual(loaded.default_serial, "R3CR406AAGE")
            self.assertEqual(loaded.devices["R3CR406AAGE"]["name"], "main-phone")
            self.assertEqual(loaded.devices["R3CR406AAGE"]["model"], "SM_G996N")

    def test_user_config_validates_route_mode(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.json"
            config = pdalink_lite.default_user_config()
            config["route_mode"] = "fallback_low_priority"
            pdalink_lite.write_user_config(path, config)

            self.assertEqual(pdalink_lite.load_user_config(path)["route_mode"], "fallback_low_priority")

            config["route_mode"] = "bad"
            with self.assertRaises(ValueError):
                pdalink_lite.write_user_config(path, config)

    def test_setup_plan_json_reports_trusted_connected_device(self):
        with tempfile.TemporaryDirectory() as tmp:
            config_path = Path(tmp) / "config.json"
            trusted_path = Path(tmp) / "trusted-devices.json"
            pdalink_lite.write_user_config(config_path, pdalink_lite.default_user_config())
            pdalink_lite.write_trusted_devices(
                trusted_path,
                pdalink_lite.TrustedDeviceStore(
                    "R3CR406AAGE",
                    {"R3CR406AAGE": {"serial": "R3CR406AAGE"}},
                ),
            )
            args = argparse.Namespace(
                config_file=str(config_path),
                trusted_devices_file=str(trusted_path),
                json=True,
            )
            stdout = io.StringIO()
            with mock.patch.object(pdalink_lite, "adb_command", return_value=["adb"]), \
                    mock.patch.object(pdalink_lite, "adb_run", return_value=pdalink_lite.CommandResult(
                        0,
                        "List of devices attached\nR3CR406AAGE device\n",
                        "",
                    )), redirect_stdout(stdout):
                self.assertEqual(pdalink_lite.setup_plan(args), 0)

            payload = json.loads(stdout.getvalue())
            self.assertEqual(payload["trusted_connected"], ["R3CR406AAGE"])
            self.assertEqual(payload["config"]["auto_connect"], "off")

    def test_wrapper_setup_plan_json_stdout_is_parseable(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = os.environ.copy()
            env["XDG_CONFIG_HOME"] = tmp
            env["PYTHON"] = sys.executable
            result = subprocess.run(
                ["bash", str(ROOT / "tools" / "pdalink.sh"), "setup", "plan", "--json"],
                text=True,
                capture_output=True,
                check=False,
                env=env,
                timeout=10.0,
            )

        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertIn("config", payload)
        self.assertFalse(result.stdout.startswith("+"))
        self.assertIn("+", result.stderr)

    def test_status_names_adb_as_development_transport(self):
        with tempfile.TemporaryDirectory() as tmp:
            config_path = Path(tmp) / "config.json"
            trusted_path = Path(tmp) / "trusted-devices.json"
            pdalink_lite.write_user_config(config_path, pdalink_lite.default_user_config())
            pdalink_lite.write_trusted_devices(
                trusted_path,
                pdalink_lite.TrustedDeviceStore("", {}),
            )
            args = argparse.Namespace(
                config_file=str(config_path),
                trusted_devices_file=str(trusted_path),
                json=False,
            )

            stdout = io.StringIO()
            with mock.patch.object(pdalink_lite, "adb_run", return_value=pdalink_lite.CommandResult(
                0,
                "List of devices attached\n",
                "",
            )), redirect_stdout(stdout):
                self.assertEqual(pdalink_lite.status_cmd(args), 0)

            self.assertIn("transport: adb (development)", stdout.getvalue())

    def test_status_json_reports_adb_development_transport(self):
        with tempfile.TemporaryDirectory() as tmp:
            config_path = Path(tmp) / "config.json"
            trusted_path = Path(tmp) / "trusted-devices.json"
            pdalink_lite.write_user_config(config_path, pdalink_lite.default_user_config())
            pdalink_lite.write_trusted_devices(
                trusted_path,
                pdalink_lite.TrustedDeviceStore("", {}),
            )
            args = argparse.Namespace(
                config_file=str(config_path),
                trusted_devices_file=str(trusted_path),
                json=True,
            )

            stdout = io.StringIO()
            with mock.patch.object(pdalink_lite, "adb_run", return_value=pdalink_lite.CommandResult(
                0,
                "List of devices attached\n",
                "",
            )), redirect_stdout(stdout):
                self.assertEqual(pdalink_lite.status_cmd(args), 0)

            payload = json.loads(stdout.getvalue())
            self.assertEqual(payload["transport"]["name"], "adb")
            self.assertEqual(payload["transport"]["role"], "development")

    def test_usb_start_sets_adb_forward(self):
        args = argparse.Namespace(
            local_port=18080,
            remote_port=8000,
            start_android=True,
            verify=False,
            verify_timeout=0,
        )
        calls = []

        def fake_adb_run(command, timeout):
            calls.append(command)
            return pdalink_lite.CommandResult(0, "ok\n", "")

        stdout = io.StringIO()
        with mock.patch.object(pdalink_lite, "adb_run", side_effect=fake_adb_run), redirect_stdout(stdout):
            self.assertEqual(pdalink_lite.usb_start(args), 0)

        self.assertIn(["forward", "tcp:18080", "tcp:8000"], calls)
        self.assertIn("usb proxy: http://127.0.0.1:18080", stdout.getvalue())

    def test_usb_start_verify_probes_local_forward(self):
        args = argparse.Namespace(
            local_port=18080,
            remote_port=8000,
            start_android=False,
            verify=True,
            verify_timeout=3,
        )
        stdout = io.StringIO()
        with mock.patch.object(pdalink_lite, "adb_run", return_value=pdalink_lite.CommandResult(0, "", "")), \
                mock.patch.object(pdalink_lite, "verify_after_connect", return_value=0) as verify, \
                redirect_stdout(stdout):
            self.assertEqual(pdalink_lite.usb_start(args), 0)

        verify.assert_called_once_with("127.0.0.1", (18080,), 3)

    def test_usb_status_reports_next_step_when_forward_missing(self):
        args = argparse.Namespace(local_port=18080, remote_port=8000)

        def fake_adb_run(command, timeout):
            if command == ["devices", "-l"]:
                return pdalink_lite.CommandResult(
                    0,
                    "List of devices attached\nR3CR406AAGE device usb:3-1\n",
                    "",
                )
            if command == ["forward", "--list"]:
                return pdalink_lite.CommandResult(0, "", "")
            self.fail(f"unexpected adb command: {command}")

        stdout = io.StringIO()
        with mock.patch.object(pdalink_lite, "adb_command", return_value=["adb"]), \
                mock.patch.object(pdalink_lite, "adb_run", side_effect=fake_adb_run), \
                mock.patch.object(pdalink_lite, "probe_ports", return_value=[
                    pdalink_lite.ProbeResult("127.0.0.1", 18080, False, "Connection refused"),
                ]), redirect_stdout(stdout):
            self.assertEqual(pdalink_lite.usb_status(args), 1)

        output = stdout.getvalue()
        self.assertIn("device: OK R3CR406AAGE", output)
        self.assertIn("forward: MISSING", output)
        self.assertIn("usb-start --verify", output)

    def test_usb_status_reports_observed_usb_ids_for_no_permissions(self):
        args = argparse.Namespace(local_port=18080, remote_port=8000)

        def fake_adb_run(command, timeout):
            if command == ["devices", "-l"]:
                return pdalink_lite.CommandResult(
                    0,
                    "List of devices attached\n"
                    "R3CR406AAGE no permissions (missing udev rules? user is in the plugdev group) usb:1-2\n",
                    "",
                )
            if command == ["forward", "--list"]:
                return pdalink_lite.CommandResult(0, "", "")
            self.fail(f"unexpected adb command: {command}")

        stdout = io.StringIO()
        with mock.patch.object(pdalink_lite, "adb_command", return_value=["adb"]), \
                mock.patch.object(pdalink_lite, "adb_run", side_effect=fake_adb_run), \
                mock.patch.object(pdalink_lite.shutil, "which", return_value="/usr/bin/lsusb"), \
                mock.patch.object(pdalink_lite, "run_command", return_value=pdalink_lite.CommandResult(
                    0,
                    "Bus 001 Device 005: ID 04e8:6864 Samsung Electronics Co., Ltd GT-I9070\n",
                    "",
                )), \
                mock.patch.object(pdalink_lite, "probe_ports", return_value=[
                    pdalink_lite.ProbeResult("127.0.0.1", 18080, False, "Connection refused"),
                ]), redirect_stdout(stdout):
            self.assertEqual(pdalink_lite.usb_status(args), 1)

        output = stdout.getvalue()
        self.assertIn("observed-usb: 04e8:6864", output)
        self.assertIn("./tools/pdalink.sh rescue udev", output)

    def test_usb_status_treats_wrapper_exit_127_as_missing_adb(self):
        args = argparse.Namespace(local_port=18080, remote_port=8000)

        stdout = io.StringIO()
        with mock.patch.object(pdalink_lite, "adb_command", return_value=["tools/adb.sh"]), \
                mock.patch.object(pdalink_lite, "adb_run", return_value=pdalink_lite.CommandResult(
                    127,
                    "",
                    "adb not found.\nRun:\n  tools/bootstrap_android_tools.sh\n",
                )), \
                mock.patch.object(pdalink_lite, "probe_ports") as probe_ports, \
                redirect_stdout(stdout):
            self.assertEqual(pdalink_lite.usb_status(args), 127)

        probe_ports.assert_not_called()
        output = stdout.getvalue()
        self.assertIn("adb: MISSING (not found)", output)
        self.assertIn("tools/bootstrap_android_tools.sh", output)
        self.assertIn("install Android platform tools", output)
        self.assertNotIn("adb: OK", output)
        self.assertNotIn("connect the phone over USB", output)

    def test_usb_status_names_adb_as_development_transport(self):
        args = argparse.Namespace(local_port=18080, remote_port=8000)

        def fake_adb_run(command, timeout):
            if command == ["devices", "-l"]:
                return pdalink_lite.CommandResult(
                    0,
                    "List of devices attached\nR3CR406AAGE device usb:3-1\n",
                    "",
                )
            if command == ["forward", "--list"]:
                return pdalink_lite.CommandResult(0, "R3CR406AAGE tcp:18080 tcp:8000\n", "")
            self.fail(f"unexpected adb command: {command}")

        stdout = io.StringIO()
        with mock.patch.object(pdalink_lite, "adb_command", return_value=["adb"]), \
                mock.patch.object(pdalink_lite, "adb_run", side_effect=fake_adb_run), \
                mock.patch.object(pdalink_lite, "probe_ports", return_value=[
                    pdalink_lite.ProbeResult("127.0.0.1", 18080, True, "PAC advertises 127.0.0.1:18080"),
                ]), redirect_stdout(stdout):
            self.assertEqual(pdalink_lite.usb_status(args), 0)

        self.assertIn("transport: adb (development)", stdout.getvalue())

    def test_connection_status_json_reports_connected_through_pdalink(self):
        args = argparse.Namespace(
            local_port=18080,
            remote_port=8000,
            route_target="1.1.1.1",
            curl_url="https://cloudflare.com/cdn-cgi/trace",
            curl_timeout=5.0,
            json=True,
        )

        def fake_adb_run(command, timeout):
            if command == ["devices", "-l"]:
                return pdalink_lite.CommandResult(
                    0,
                    "List of devices attached\nR3CR406AAGE device usb:1-2\n",
                    "",
                )
            if command == ["forward", "--list"]:
                return pdalink_lite.CommandResult(0, "R3CR406AAGE tcp:18080 tcp:8000\n", "")
            self.fail(f"unexpected adb command: {command}")

        stdout = io.StringIO()
        with mock.patch.object(pdalink_lite, "adb_command", return_value=["adb"]), \
                mock.patch.object(pdalink_lite, "adb_run", side_effect=fake_adb_run), \
                mock.patch.object(pdalink_lite, "probe_ports", return_value=[
                    pdalink_lite.ProbeResult("127.0.0.1", 18080, True, "PAC advertises 127.0.0.1:18080"),
                ]), \
                mock.patch.object(pdalink_lite, "route_info", return_value=pdalink_lite.parse_route_info(
                    "1.1.1.1 dev pdalink0 src 10.66.0.2 uid 1000 cache"
                )), \
                mock.patch.object(pdalink_lite, "run_command", return_value=pdalink_lite.CommandResult(
                    0,
                    "ok\n",
                    "",
                )), redirect_stdout(stdout):
            self.assertEqual(pdalink_lite.connection_status(args), 0)

        payload = json.loads(stdout.getvalue())
        self.assertEqual(payload["overall"], "connected")
        self.assertTrue(payload["checks"]["device"]["ok"])
        self.assertTrue(payload["checks"]["route"]["ok"])
        self.assertEqual(payload["checks"]["route"]["dev"], "pdalink0")
        self.assertTrue(payload["checks"]["internet"]["ok"])

    def test_connection_status_json_reports_degraded_when_internet_uses_usb0(self):
        args = argparse.Namespace(
            local_port=18080,
            remote_port=8000,
            route_target="1.1.1.1",
            curl_url="https://cloudflare.com/cdn-cgi/trace",
            curl_timeout=5.0,
            json=True,
        )

        def fake_adb_run(command, timeout):
            if command == ["devices", "-l"]:
                return pdalink_lite.CommandResult(
                    0,
                    "List of devices attached\nR3CR406AAGE device usb:1-2\n",
                    "",
                )
            if command == ["forward", "--list"]:
                return pdalink_lite.CommandResult(0, "R3CR406AAGE tcp:18080 tcp:8000\n", "")
            self.fail(f"unexpected adb command: {command}")

        stdout = io.StringIO()
        with mock.patch.object(pdalink_lite, "adb_command", return_value=["adb"]), \
                mock.patch.object(pdalink_lite, "adb_run", side_effect=fake_adb_run), \
                mock.patch.object(pdalink_lite, "probe_ports", return_value=[
                    pdalink_lite.ProbeResult("127.0.0.1", 18080, True, "PAC advertises 127.0.0.1:18080"),
                ]), \
                mock.patch.object(pdalink_lite, "route_info", return_value=pdalink_lite.parse_route_info(
                    "1.1.1.1 via 10.71.77.65 dev usb0 src 10.71.77.5 uid 1000 cache"
                )), \
                mock.patch.object(pdalink_lite, "run_command", return_value=pdalink_lite.CommandResult(
                    0,
                    "ok\n",
                    "",
                )), redirect_stdout(stdout):
            self.assertEqual(pdalink_lite.connection_status(args), 1)

        payload = json.loads(stdout.getvalue())
        self.assertEqual(payload["overall"], "degraded")
        self.assertFalse(payload["checks"]["route"]["ok"])
        self.assertEqual(payload["checks"]["route"]["dev"], "usb0")
        self.assertTrue(payload["checks"]["internet"]["ok"])

    def test_connection_status_json_reports_disconnected_when_device_missing(self):
        args = argparse.Namespace(
            local_port=18080,
            remote_port=8000,
            route_target="1.1.1.1",
            curl_url="https://cloudflare.com/cdn-cgi/trace",
            curl_timeout=5.0,
            json=True,
        )

        def fake_adb_run(command, timeout):
            if command == ["devices", "-l"]:
                return pdalink_lite.CommandResult(0, "List of devices attached\n", "")
            if command == ["forward", "--list"]:
                return pdalink_lite.CommandResult(0, "", "")
            self.fail(f"unexpected adb command: {command}")

        stdout = io.StringIO()
        with mock.patch.object(pdalink_lite, "adb_command", return_value=["adb"]), \
                mock.patch.object(pdalink_lite, "adb_run", side_effect=fake_adb_run), \
                mock.patch.object(pdalink_lite, "probe_ports", return_value=[
                    pdalink_lite.ProbeResult("127.0.0.1", 18080, False, "Connection refused"),
                ]), \
                mock.patch.object(pdalink_lite, "route_info", return_value=pdalink_lite.parse_route_info(
                    "1.1.1.1 via 10.71.77.65 dev usb0 src 10.71.77.5 uid 1000 cache"
                )), \
                mock.patch.object(pdalink_lite, "run_command", return_value=pdalink_lite.CommandResult(
                    0,
                    "ok\n",
                    "",
                )), redirect_stdout(stdout):
            self.assertEqual(pdalink_lite.connection_status(args), 1)

        payload = json.loads(stdout.getvalue())
        self.assertEqual(payload["overall"], "disconnected")
        self.assertFalse(payload["checks"]["device"]["ok"])
        self.assertFalse(payload["checks"]["forward"]["ok"])
        self.assertFalse(payload["checks"]["proxy"]["ok"])

    def test_connection_status_parser_exposes_json_status_command(self):
        parser = pdalink_lite.build_parser()

        args = parser.parse_args(["connection-status", "--json"])

        self.assertIs(args.func, pdalink_lite.connection_status)
        self.assertEqual(args.local_port, 18080)
        self.assertEqual(args.remote_port, 8000)
        self.assertEqual(args.route_target, "1.1.1.1")
        self.assertEqual(args.curl_url, "https://cloudflare.com/cdn-cgi/trace")
        self.assertEqual(args.curl_timeout, 10.0)
        self.assertTrue(args.json)

    def test_product_usb_status_reports_permission_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            state_file = Path(tmp) / "product-usb.json"
            args = argparse.Namespace(state_file=str(state_file), json=False)

            stdout = io.StringIO()
            with redirect_stdout(stdout):
                self.assertEqual(pdalink_lite.product_usb_status(args), 1)

            output = stdout.getvalue()
            self.assertIn("PdaLink product USB status", output)
            self.assertIn("transport: product-usb (product)", output)
            self.assertIn("readiness: permission missing", output)
            self.assertIn("approve the PdaLink USB permission prompt", output)

    def test_product_usb_status_json_reports_ready_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            state_file = Path(tmp) / "product-usb.json"
            state_file.write_text(json.dumps({
                "permission": "granted",
                "host_trust": "trusted",
                "transport_state": "connected",
                "proxy_state": "ready",
                "usb_debugging": "off",
                "updated_at": datetime.now(timezone.utc).isoformat(),
            }), encoding="utf-8")
            args = argparse.Namespace(state_file=str(state_file), json=True)

            stdout = io.StringIO()
            with redirect_stdout(stdout):
                self.assertEqual(pdalink_lite.product_usb_status(args), 0)

            payload = json.loads(stdout.getvalue())
            self.assertTrue(payload["ready"])
            self.assertEqual(payload["readiness"], "ready")
            self.assertEqual(payload["transport"]["name"], "product-usb")
            self.assertEqual(payload["transport"]["role"], "product")

    def test_product_usb_status_reports_stale_ready_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            state_file = Path(tmp) / "product-usb.json"
            state_file.write_text(json.dumps({
                "permission": "granted",
                "host_trust": "trusted",
                "transport_state": "connected",
                "proxy_state": "ready",
                "usb_debugging": "off",
                "updated_at": "2020-01-01T00:00:00+00:00",
            }), encoding="utf-8")
            args = argparse.Namespace(state_file=str(state_file), json=False)

            stdout = io.StringIO()
            with redirect_stdout(stdout):
                self.assertEqual(pdalink_lite.product_usb_status(args), 1)

            output = stdout.getvalue()
            self.assertIn("readiness: state stale", output)
            self.assertIn("restart the Product USB watcher", output)

    def test_product_usb_status_reports_untrusted_host(self):
        with tempfile.TemporaryDirectory() as tmp:
            state_file = Path(tmp) / "product-usb.json"
            state_file.write_text(json.dumps({
                "permission": "granted",
                "host_trust": "untrusted",
                "transport_state": "connected",
                "proxy_state": "ready",
                "usb_debugging": "off",
            }), encoding="utf-8")
            args = argparse.Namespace(state_file=str(state_file), json=False)

            stdout = io.StringIO()
            with redirect_stdout(stdout):
                self.assertEqual(pdalink_lite.product_usb_status(args), 1)

            output = stdout.getvalue()
            self.assertIn("readiness: host untrusted", output)
            self.assertIn("approve this Linux host", output)

    def test_product_usb_status_reports_handshake_timeout(self):
        with tempfile.TemporaryDirectory() as tmp:
            state_file = Path(tmp) / "product-usb.json"
            state_file.write_text(json.dumps({
                "permission": "granted",
                "host_trust": "unknown",
                "handshake_reason": "timeout",
                "transport_state": "connected",
                "proxy_state": "missing",
                "usb_debugging": "on",
            }), encoding="utf-8")
            args = argparse.Namespace(state_file=str(state_file), json=False)

            stdout = io.StringIO()
            with redirect_stdout(stdout):
                self.assertEqual(pdalink_lite.product_usb_status(args), 1)

            output = stdout.getvalue()
            self.assertIn("readiness: handshake timeout", output)
            self.assertIn("unlock Android", output)

    def test_product_usb_bridge_records_write_timeout_as_handshake_timeout(self):
        import pdalink_product_usb

        class TimeoutTransport:
            product_id = "2d01"

            def send(self, _frame):
                raise TimeoutError("timed out writing Product USB frame")

            def receive(self):
                raise TimeoutError("timed out waiting for Product USB frame")

            def close(self):
                pass

        with tempfile.TemporaryDirectory() as tmp:
            state_file = Path(tmp) / "product-usb.json"
            args = argparse.Namespace(
                bind_host="127.0.0.1",
                host_fingerprint="debug",
                host_name="dev-host",
                local_port=0,
                once=True,
                pid="2d01",
                state_file=str(state_file),
                timeout=0.01,
                vid="18d1",
            )

            stderr = io.StringIO()
            with mock.patch.object(
                pdalink_product_usb.PyUsbProductUsbTransport,
                "open",
                return_value=TimeoutTransport(),
            ), redirect_stderr(stderr):
                self.assertEqual(pdalink_lite.product_usb_bridge(args), 1)

            payload = json.loads(state_file.read_text(encoding="utf-8"))
            self.assertEqual(payload["permission"], "granted")
            self.assertEqual(payload["transport_state"], "connected")
            self.assertEqual(payload["handshake_reason"], "timeout")
            self.assertIn("unlock Android", stderr.getvalue())

    def test_product_usb_check_prints_no_adb_release_smoke_when_ready(self):
        with tempfile.TemporaryDirectory() as tmp:
            state_file = Path(tmp) / "product-usb.json"
            state_file.write_text(json.dumps({
                "permission": "granted",
                "host_trust": "trusted",
                "transport_state": "connected",
                "proxy_state": "ready",
                "usb_debugging": "off",
                "updated_at": datetime.now(timezone.utc).isoformat(),
            }), encoding="utf-8")
            args = argparse.Namespace(
                state_file=str(state_file),
                json=False,
                curl_url="https://ifconfig.me",
            )

            stdout = io.StringIO()
            with redirect_stdout(stdout):
                self.assertEqual(pdalink_lite.product_usb_check(args), 0)

            output = stdout.getvalue()
            self.assertIn("release-gate: ready", output)
            self.assertIn("USB debugging: off", output)
            self.assertIn("--require-product-usb", output)
            self.assertIn("curl -4 https://ifconfig.me", output)

    def test_product_usb_aoa_start_dry_run_prints_descriptor(self):
        args = argparse.Namespace(
            serial="SHA256:abc",
            dry_run=True,
            vid="",
            pid="",
        )

        stdout = io.StringIO()
        with redirect_stdout(stdout):
            self.assertEqual(pdalink_lite.product_usb_aoa_start(args), 0)

        output = stdout.getvalue()
        self.assertIn("aoa: OK", output)
        self.assertIn("PdaLink USB", output)
        self.assertIn("SHA256:abc", output)

    def test_curl_retry_stops_after_success(self):
        statuses = [56, 0]
        stdout = io.StringIO()
        stderr = io.StringIO()
        with mock.patch.object(pdalink_lite, "run_curl_with_proxy", side_effect=statuses) as curl, \
                mock.patch.object(pdalink_lite.time, "sleep") as sleep, \
                redirect_stdout(stdout), redirect_stderr(stderr):
            status = pdalink_lite.run_curl_with_proxy_retry(
                "https://ifconfig.me",
                "127.0.0.1",
                18080,
                attempts=3,
                delay=1.0,
            )

        self.assertEqual(status, 0)
        self.assertEqual(curl.call_count, 2)
        sleep.assert_called_once_with(1.0)
        self.assertIn("attempt 2/3", stdout.getvalue())
        self.assertIn("retrying", stderr.getvalue())

    def test_usb_smoke_retries_curl_after_forward_verify(self):
        args = argparse.Namespace(
            local_port=18080,
            remote_port=8000,
            start_android=True,
            verify_timeout=5,
            url="https://ifconfig.me",
            curl_attempts=4,
            curl_delay=0.5,
        )
        with mock.patch.object(pdalink_lite, "usb_start", return_value=0) as start, \
                mock.patch.object(pdalink_lite, "run_curl_with_proxy_retry", return_value=0) as curl:
            self.assertEqual(pdalink_lite.usb_smoke(args), 0)

        start.assert_called_once()
        curl.assert_called_once_with("https://ifconfig.me", "127.0.0.1", 18080, attempts=4, delay=0.5)

    def test_parse_tun_routes_defaults_and_validates(self):
        self.assertEqual(pdalink_lite.parse_tun_routes(None), ("0.0.0.0/1", "128.0.0.0/1"))
        self.assertEqual(pdalink_lite.parse_tun_routes("10.0.0.0/8, 172.16.0.0/12"), ("10.0.0.0/8", "172.16.0.0/12"))
        with self.assertRaises(ValueError):
            pdalink_lite.parse_tun_routes("not-a-cidr")

    def test_parse_adapter_command_expands_template(self):
        command = pdalink_lite.parse_adapter_command(
            "adapter --tun {iface} --proxy {proxy} --mtu {mtu}",
            "pdalink0",
            "http://127.0.0.1:18080",
            1500,
        )
        self.assertEqual(command, (
            "adapter",
            "--tun",
            "pdalink0",
            "--proxy",
            "http://127.0.0.1:18080",
            "--mtu",
            "1500",
        ))

    def test_build_system_route_commands(self):
        config = pdalink_lite.SystemRouteConfig(
            iface="pdalink0",
            address="10.66.0.2/24",
            mtu=1400,
            routes=("0.0.0.0/1", "128.0.0.0/1"),
            proxy_url="http://127.0.0.1:18080",
        )
        self.assertEqual(pdalink_lite.build_tun_setup_commands(config)[0], [
            "ip",
            "tuntap",
            "add",
            "dev",
            "pdalink0",
            "mode",
            "tun",
        ])
        self.assertEqual(pdalink_lite.build_tun_route_commands(config), [
            ["ip", "route", "replace", "0.0.0.0/1", "dev", "pdalink0"],
            ["ip", "route", "replace", "128.0.0.0/1", "dev", "pdalink0"],
        ])
        self.assertEqual(pdalink_lite.build_tun_teardown_commands(config)[-1], [
            "ip",
            "link",
            "del",
            "dev",
            "pdalink0",
        ])

    def test_fallback_low_priority_route_uses_metric_default(self):
        config = pdalink_lite.SystemRouteConfig(
            iface="pdalink0",
            address="10.66.0.2/24",
            mtu=1500,
            routes=("0.0.0.0/1", "128.0.0.0/1"),
            proxy_url="http://127.0.0.1:18080",
            route_mode="fallback_low_priority",
            route_metric=50000,
        )

        self.assertEqual(pdalink_lite.build_tun_route_commands(config), [
            ["ip", "route", "add", "default", "dev", "pdalink0", "metric", "50000"],
        ])
        self.assertIn(
            ["ip", "route", "del", "default", "dev", "pdalink0", "metric", "50000"],
            pdalink_lite.build_tun_teardown_commands(config),
        )

    def test_adapter_owned_teardown_leaves_link_close_to_adapter(self):
        config = pdalink_lite.SystemRouteConfig(
            iface="pdalink0",
            address="10.66.0.2/24",
            mtu=1500,
            routes=("0.0.0.0/1", "128.0.0.0/1"),
            proxy_url="http://127.0.0.1:18080",
            dns_servers=("1.1.1.1", "8.8.8.8"),
        )

        with mock.patch.object(pdalink_lite.shutil, "which", return_value="/usr/bin/resolvectl"):
            commands = pdalink_lite.build_adapter_owned_tun_teardown_commands(config)

        self.assertEqual(commands[0], ["resolvectl", "revert", "pdalink0"])
        self.assertNotIn(["ip", "link", "del", "dev", "pdalink0"], commands)

    def test_resolved_dns_commands_only_for_active_override(self):
        active = pdalink_lite.SystemRouteConfig(
            iface="pdalink0",
            address="10.66.0.2/24",
            mtu=1500,
            routes=("0.0.0.0/1", "128.0.0.0/1"),
            proxy_url="http://127.0.0.1:18080",
            dns_servers=("1.1.1.1", "8.8.8.8"),
        )
        fallback = pdalink_lite.SystemRouteConfig(
            iface="pdalink0",
            address="10.66.0.2/24",
            mtu=1500,
            routes=("0.0.0.0/1", "128.0.0.0/1"),
            proxy_url="http://127.0.0.1:18080",
            route_mode="fallback_low_priority",
        )

        with mock.patch.object(pdalink_lite.shutil, "which", return_value="/usr/bin/resolvectl"):
            self.assertEqual(pdalink_lite.build_dns_setup_commands(active)[0], [
                "resolvectl",
                "dns",
                "pdalink0",
                "1.1.1.1",
                "8.8.8.8",
            ])
            self.assertEqual(pdalink_lite.build_dns_setup_commands(fallback), [])

    def test_system_up_dry_run_does_not_execute(self):
        args = argparse.Namespace(
            iface="pdalink0",
            address="10.66.0.2/24",
            mtu=1500,
            routes="0.0.0.0/1,128.0.0.0/1",
            proxy_url="http://127.0.0.1:18080",
            adapter_command="",
            apply=False,
            adapter_ready=False,
        )
        stdout = io.StringIO()
        with mock.patch.object(pdalink_lite.shutil, "which", return_value="/usr/bin/resolvectl"), \
                mock.patch.object(pdalink_lite.subprocess, "run") as run, \
                redirect_stdout(stdout):
            self.assertEqual(pdalink_lite.system_up(args), 0)
        run.assert_not_called()
        output = stdout.getvalue()
        self.assertIn("ip route replace 0.0.0.0/1 dev pdalink0", output)
        self.assertIn("resolvectl dns pdalink0 1.1.1.1 8.8.8.8", output)

    def test_system_up_apply_requires_adapter_ready(self):
        args = argparse.Namespace(
            iface="pdalink0",
            address="10.66.0.2/24",
            mtu=1500,
            routes="0.0.0.0/1,128.0.0.0/1",
            proxy_url="http://127.0.0.1:18080",
            adapter_command="",
            apply=True,
            adapter_ready=False,
        )
        stderr = io.StringIO()
        with mock.patch.object(pdalink_lite.subprocess, "run") as run, redirect_stderr(stderr):
            self.assertEqual(pdalink_lite.system_up(args), 2)
        run.assert_not_called()
        self.assertIn("refusing to change system routes", stderr.getvalue())

    def test_system_up_apply_refuses_missing_resolvectl_for_resolved_dns(self):
        args = argparse.Namespace(
            iface="pdalink0",
            address="10.66.0.2/24",
            mtu=1500,
            routes="0.0.0.0/1,128.0.0.0/1",
            proxy_url="http://127.0.0.1:18080",
            adapter_command="",
            apply=True,
            adapter_ready=True,
            route_mode="active_override",
            route_metric=50000,
            dns_mode="resolved",
            dns_servers="1.1.1.1,8.8.8.8",
        )
        stderr = io.StringIO()
        with mock.patch.object(pdalink_lite.shutil, "which", return_value=None), \
                mock.patch.object(pdalink_lite, "run_privileged_network_commands") as run, \
                redirect_stderr(stderr):
            self.assertEqual(pdalink_lite.system_up(args), 1)

        run.assert_not_called()
        self.assertIn("resolvectl unavailable", stderr.getvalue())

    def test_system_up_apply_includes_resolved_dns_commands(self):
        args = argparse.Namespace(
            iface="pdalink0",
            address="10.66.0.2/24",
            mtu=1500,
            routes="0.0.0.0/1,128.0.0.0/1",
            proxy_url="http://127.0.0.1:18080",
            adapter_command="",
            apply=True,
            adapter_ready=True,
            route_mode="active_override",
            route_metric=50000,
            dns_mode="resolved",
            dns_servers="1.1.1.1,8.8.8.8",
        )
        with mock.patch.object(pdalink_lite.shutil, "which", return_value="/usr/bin/resolvectl"), \
                mock.patch.object(pdalink_lite, "existing_tun_route_conflicts", return_value=[]), \
                mock.patch.object(pdalink_lite, "run_privileged_network_commands", return_value=0) as run:
            self.assertEqual(pdalink_lite.system_up(args), 0)

        commands = run.call_args.args[0]
        self.assertIn(["resolvectl", "dns", "pdalink0", "1.1.1.1", "8.8.8.8"], commands)
        self.assertIn(["resolvectl", "domain", "pdalink0", "~."], commands)

    def test_existing_tun_route_conflicts_flags_non_pdalink_route(self):
        config = pdalink_lite.SystemRouteConfig(
            iface="pdalink0",
            address="10.66.0.2/24",
            mtu=1500,
            routes=("0.0.0.0/1", "128.0.0.0/1"),
            proxy_url="http://127.0.0.1:18080",
        )

        def fake_run(command, **_kwargs):
            if command[-1] == "0.0.0.0/1":
                return pdalink_lite.CommandResult(0, "0.0.0.0/1 dev pdalink0\n", "")
            return pdalink_lite.CommandResult(0, "128.0.0.0/1 via 10.0.0.1 dev tun0\n", "")

        with mock.patch.object(pdalink_lite.shutil, "which", return_value="/usr/sbin/ip"), \
                mock.patch.object(pdalink_lite.subprocess, "run", side_effect=fake_run):
            conflicts = pdalink_lite.existing_tun_route_conflicts(config)

        self.assertEqual(len(conflicts), 1)
        self.assertEqual(conflicts[0].route, "128.0.0.0/1")
        self.assertIn("tun0", conflicts[0].existing)

    def test_system_up_apply_refuses_conflicting_split_route(self):
        args = argparse.Namespace(
            iface="pdalink0",
            address="10.66.0.2/24",
            mtu=1500,
            routes="0.0.0.0/1,128.0.0.0/1",
            proxy_url="http://127.0.0.1:18080",
            adapter_command="",
            apply=True,
            adapter_ready=True,
        )
        conflict = pdalink_lite.RouteConflict("0.0.0.0/1", "0.0.0.0/1 via 10.0.0.1 dev tun0")
        stderr = io.StringIO()
        with mock.patch.object(pdalink_lite, "existing_tun_route_conflicts", return_value=[conflict]), \
                mock.patch.object(pdalink_lite, "run_privileged_network_commands") as run, \
                redirect_stderr(stderr):
            self.assertEqual(pdalink_lite.system_up(args), 2)

        run.assert_not_called()
        self.assertIn("refusing to replace existing system routes", stderr.getvalue())

    def test_system_start_refuses_without_root(self):
        args = argparse.Namespace(
            iface="pdalink0",
            address="10.66.0.2/24",
            mtu=1500,
            routes="0.0.0.0/1,128.0.0.0/1",
            proxy_url="http://127.0.0.1:18080",
            no_dns=False,
            connect_timeout=10.0,
            verbose=False,
            start_usb=False,
            remote_port=8000,
            start_android=True,
            verify_timeout=15.0,
        )
        stderr = io.StringIO()
        with mock.patch.object(pdalink_lite.os, "geteuid", return_value=1000), redirect_stderr(stderr):
            self.assertEqual(pdalink_lite.system_start(args), 77)
        self.assertIn("requires root", stderr.getvalue())

    def test_system_start_refuses_when_product_usb_required_but_not_ready(self):
        with tempfile.TemporaryDirectory() as tmp:
            args = argparse.Namespace(
                iface="pdalink0",
                address="10.66.0.2/24",
                mtu=1500,
                routes="0.0.0.0/1,128.0.0.0/1",
                proxy_url="http://127.0.0.1:18080",
                no_dns=False,
                connect_timeout=10.0,
                verbose=False,
                start_usb=False,
                remote_port=8000,
                start_android=True,
                verify_timeout=15.0,
                require_product_usb=True,
                product_usb_state_file=str(Path(tmp) / "product-usb.json"),
            )
            stderr = io.StringIO()
            with mock.patch.object(pdalink_lite.os, "geteuid") as geteuid, redirect_stderr(stderr):
                self.assertEqual(pdalink_lite.system_start(args), 1)

            geteuid.assert_not_called()
            self.assertIn("refusing to change routes until product USB is ready", stderr.getvalue())

    def test_system_start_starts_usb_before_proxy_probe(self):
        args = argparse.Namespace(
            iface="pdalink0",
            address="10.66.0.2/24",
            mtu=1500,
            routes="0.0.0.0/1,128.0.0.0/1",
            proxy_url="http://127.0.0.1:18080",
            no_dns=False,
            connect_timeout=10.0,
            verbose=False,
            start_usb=True,
            remote_port=8000,
            start_android=True,
            probe_proxy=True,
            verify_timeout=15.0,
        )
        order = []

        def fake_usb_start(_):
            order.append("usb")
            return 0

        def fake_proxy_probe(_):
            order.append("probe")
            return False

        stderr = io.StringIO()
        with mock.patch.object(pdalink_lite.os, "geteuid", return_value=0), \
                mock.patch.object(pdalink_lite, "usb_start", side_effect=fake_usb_start), \
                mock.patch.object(pdalink_lite, "print_system_proxy_probe", side_effect=fake_proxy_probe), \
                redirect_stderr(stderr):
            self.assertEqual(pdalink_lite.system_start(args), 1)

        self.assertEqual(order, ["usb", "probe"])
        self.assertIn("refusing to change routes", stderr.getvalue())

    def test_system_plan_points_to_builtin_start(self):
        args = argparse.Namespace(
            iface="pdalink0",
            address="10.66.0.2/24",
            mtu=1500,
            routes="0.0.0.0/1,128.0.0.0/1",
            proxy_url="http://127.0.0.1:18080",
            adapter_command="",
        )
        stdout = io.StringIO()
        with redirect_stdout(stdout):
            self.assertEqual(pdalink_lite.system_plan(args), 0)
        output = stdout.getvalue()
        self.assertIn("system-start", output)
        self.assertIn("built-in internet adapter", output)

    def test_system_status_reports_builtin_adapter(self):
        class FakeTun:
            def exists(self):
                return True

            def __fspath__(self):
                return "/dev/net/tun"

            def __str__(self):
                return "/dev/net/tun"

        args = argparse.Namespace(
            iface="pdalink0",
            address="10.66.0.2/24",
            mtu=1500,
            routes="0.0.0.0/1,128.0.0.0/1",
            proxy_url="http://127.0.0.1:18080",
            adapter_command="",
            probe_proxy=True,
        )
        stdout = io.StringIO()

        def fake_which(name):
            if name == "ip":
                return "/usr/bin/ip"
            if name == "resolvectl":
                return "/usr/bin/resolvectl"
            return None

        with mock.patch.object(pdalink_lite, "TUN_DEVICE", FakeTun()), \
                mock.patch.object(pdalink_lite.os, "access", return_value=True), \
                mock.patch.object(pdalink_lite.shutil, "which", side_effect=fake_which), \
                mock.patch.object(pdalink_lite, "probe_one", return_value=pdalink_lite.ProbeResult(
                    "127.0.0.1",
                    18080,
                    True,
                    "PAC advertises 127.0.0.1:18080",
                )), redirect_stdout(stdout):
            self.assertEqual(pdalink_lite.system_status(args), 0)

        output = stdout.getvalue()
        self.assertIn("tun: OK /dev/net/tun", output)
        self.assertIn("adapter: BUILTIN", output)
        self.assertIn("proxy-probe: OK", output)
        self.assertIn("state: MISSING", output)
        self.assertIn("dns: RESOLVED", output)

    def test_load_system_state_rejects_malformed_payload_as_value_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            state_path = Path(tmp) / "system.json"
            state_path.write_text(json.dumps({"pid": os.getpid()}), encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "invalid system state file"):
                pdalink_lite.load_system_state(state_path)

    def test_system_start_writes_state_and_cleans_on_exit(self):
        order = []

        class FakeAdapter:
            def __init__(self):
                self.closed = False

            def run(self, *args, **kwargs):
                order.append("adapter-run")
                return None

            def close(self):
                self.closed = True

        class FakeTunHttpAdapter:
            opened = None
            opened_kwargs = None

            @classmethod
            def open(cls, *_args, **kwargs):
                cls.opened = FakeAdapter()
                cls.opened_kwargs = kwargs
                return cls.opened

        with tempfile.TemporaryDirectory() as tmp:
            state_file = str(Path(tmp) / "system.json")
            real_write_system_state = pdalink_lite.write_system_state

            def write_system_state(*write_args, **write_kwargs):
                order.append("state")
                return real_write_system_state(*write_args, **write_kwargs)

            def install_routes(_commands):
                order.append("routes")
                return 0

            def tailscale_after_routes(_config):
                order.append("tailscale")
                return True

            args = argparse.Namespace(
                iface="pdalink0",
                address="10.66.0.2/24",
                mtu=1500,
                routes="0.0.0.0/1,128.0.0.0/1",
                proxy_url="http://127.0.0.1:18080",
                no_dns=False,
                connect_timeout=10.0,
                verbose=False,
                start_usb=False,
                remote_port=8000,
                start_android=True,
                probe_proxy=False,
                verify_timeout=15.0,
                state_file=state_file,
                tcp_idle_timeout=12.5,
                tailscale_compat=True,
            )
            stdout = io.StringIO()
            with mock.patch.object(pdalink_lite, "start_dns_ready_adapter", return_value=None), \
                    mock.patch.object(pdalink_lite.os, "geteuid", return_value=0), \
                    mock.patch.dict(sys.modules, {"pdalink_tun": mock.Mock(TunHttpAdapter=FakeTunHttpAdapter)}), \
                    mock.patch.object(pdalink_lite, "existing_tun_route_conflicts", return_value=[]), \
                    mock.patch.object(
                        pdalink_lite,
                        "run_privileged_network_commands",
                        side_effect=install_routes,
                    ), \
                    mock.patch.object(
                        pdalink_lite,
                        "write_system_state",
                        side_effect=write_system_state,
                    ), \
                    mock.patch.object(
                        pdalink_lite,
                        "restart_active_tailscale_after_route_override",
                        side_effect=tailscale_after_routes,
                    ) as tailscale_compat, \
                    mock.patch.object(pdalink_lite, "run_network_commands_best_effort") as cleanup, \
                    redirect_stdout(stdout):
                self.assertEqual(pdalink_lite.system_start(args), 0)

            self.assertFalse(Path(state_file).exists())
            cleanup.assert_called_once()
            self.assertIn("state:", stdout.getvalue())
            self.assertEqual(FakeTunHttpAdapter.opened_kwargs["tcp_idle_timeout"], 12.5)
            tailscale_compat.assert_called_once()
            self.assertEqual(order, ["routes", "state", "tailscale", "adapter-run"])

    def test_system_runtime_args_default_to_no_tcp_idle_cleanup(self):
        parser = pdalink_lite.build_parser()

        start_args = parser.parse_args(["system-start"])
        no_compat_args = parser.parse_args(["system-start", "--no-tailscale-compat"])
        adapter_args = parser.parse_args(["system-adapter"])

        self.assertEqual(start_args.tcp_idle_timeout, 0.0)
        self.assertTrue(start_args.tailscale_compat)
        self.assertFalse(no_compat_args.tailscale_compat)
        self.assertEqual(adapter_args.tcp_idle_timeout, 0.0)

    def test_tailscale_compat_restarts_only_active_service_for_active_override(self):
        config = pdalink_lite.SystemRouteConfig(
            iface="pdalink0",
            address="10.66.0.2/24",
            mtu=1500,
            routes=("0.0.0.0/1", "128.0.0.0/1"),
            proxy_url="http://127.0.0.1:18080",
            route_mode="active_override",
        )
        results = [
            mock.Mock(returncode=0, stdout="", stderr=""),
            mock.Mock(returncode=0, stdout="", stderr=""),
        ]

        with mock.patch.object(pdalink_lite.shutil, "which", return_value="/usr/bin/systemctl"), \
                mock.patch.object(pdalink_lite.subprocess, "run", side_effect=results) as run, \
                redirect_stdout(io.StringIO()):
            self.assertTrue(pdalink_lite.restart_active_tailscale_after_route_override(config))

        self.assertEqual(run.call_args_list[0].args[0], [
            "/usr/bin/systemctl", "is-active", "--quiet", "tailscaled.service",
        ])
        self.assertEqual(run.call_args_list[1].args[0], [
            "/usr/bin/systemctl", "try-restart", "tailscaled.service",
        ])

    def test_tailscale_compat_skips_inactive_service_and_fallback_route(self):
        active_config = pdalink_lite.SystemRouteConfig(
            iface="pdalink0",
            address="10.66.0.2/24",
            mtu=1500,
            routes=("0.0.0.0/1", "128.0.0.0/1"),
            proxy_url="http://127.0.0.1:18080",
            route_mode="active_override",
        )
        fallback_config = pdalink_lite.SystemRouteConfig(
            iface="pdalink0",
            address="10.66.0.2/24",
            mtu=1500,
            routes=("0.0.0.0/1", "128.0.0.0/1"),
            proxy_url="http://127.0.0.1:18080",
            route_mode="fallback_low_priority",
        )

        with mock.patch.object(pdalink_lite.shutil, "which", return_value="/usr/bin/systemctl"), \
                mock.patch.object(
                    pdalink_lite.subprocess,
                    "run",
                    return_value=mock.Mock(returncode=3, stdout="inactive\n", stderr=""),
                ) as run, \
                redirect_stdout(io.StringIO()):
            self.assertFalse(pdalink_lite.restart_active_tailscale_after_route_override(active_config))
            self.assertFalse(pdalink_lite.restart_active_tailscale_after_route_override(fallback_config))

        run.assert_called_once()

    def test_system_start_preserves_state_when_cleanup_fails(self):
        class FakeAdapter:
            def run(self, *args, **kwargs):
                return None

            def close(self):
                pass

        class FakeTunHttpAdapter:
            @classmethod
            def open(cls, *_args, **_kwargs):
                return FakeAdapter()

        with tempfile.TemporaryDirectory() as tmp:
            state_file = str(Path(tmp) / "system.json")
            args = argparse.Namespace(
                iface="pdalink0",
                address="10.66.0.2/24",
                mtu=1500,
                routes="0.0.0.0/1,128.0.0.0/1",
                proxy_url="http://127.0.0.1:18080",
                no_dns=False,
                connect_timeout=10.0,
                verbose=False,
                start_usb=False,
                remote_port=8000,
                start_android=True,
                probe_proxy=False,
                verify_timeout=15.0,
                state_file=state_file,
            )
            stderr = io.StringIO()
            stdout = io.StringIO()
            with mock.patch.object(pdalink_lite, "start_dns_ready_adapter", return_value=None), \
                    mock.patch.object(pdalink_lite.os, "geteuid", return_value=0), \
                    mock.patch.dict(sys.modules, {"pdalink_tun": mock.Mock(TunHttpAdapter=FakeTunHttpAdapter)}), \
                    mock.patch.object(pdalink_lite, "existing_tun_route_conflicts", return_value=[]), \
                    mock.patch.object(pdalink_lite, "run_privileged_network_commands", return_value=0), \
                    mock.patch.object(pdalink_lite, "run_network_commands_best_effort", return_value=False), \
                    redirect_stdout(stdout), \
                    redirect_stderr(stderr):
                self.assertEqual(pdalink_lite.system_start(args), 1)

            self.assertTrue(Path(state_file).exists())
            self.assertIn("preserving state", stderr.getvalue())

    def test_system_stop_uses_saved_state_for_cleanup(self):
        config = pdalink_lite.SystemRouteConfig(
            iface="pdalink0",
            address="10.66.0.2/24",
            mtu=1500,
            routes=("0.0.0.0/1", "128.0.0.0/1"),
            proxy_url="http://127.0.0.1:18080",
        )
        with tempfile.TemporaryDirectory() as tmp:
            state_path = Path(tmp) / "system.json"
            pdalink_lite.write_system_state(state_path, config)
            payload = json.loads(state_path.read_text(encoding="utf-8"))
            payload["pid"] = os.getpid() + 10000
            state_path.write_text(json.dumps(payload), encoding="utf-8")
            args = argparse.Namespace(
                iface="ignored0",
                address="10.99.0.2/24",
                mtu=1500,
                routes="10.0.0.0/8",
                proxy_url="http://127.0.0.1:18080",
                state_file=str(state_path),
                stop_timeout=0.1,
            )
            captured = []
            stdout = io.StringIO()
            with mock.patch.object(pdalink_lite.os, "geteuid", return_value=0), \
                    mock.patch.object(pdalink_lite, "process_exists", return_value=False), \
                    mock.patch.object(pdalink_lite, "run_network_commands_best_effort", side_effect=lambda commands: captured.extend(commands) or True), \
                    redirect_stdout(stdout):
                self.assertEqual(pdalink_lite.system_stop(args), 0)

            self.assertFalse(state_path.exists())
            self.assertIn(["ip", "route", "del", "0.0.0.0/1", "dev", "pdalink0"], captured)
            self.assertIn(["ip", "link", "del", "dev", "pdalink0"], captured)
            self.assertIn("system internet stopped", stdout.getvalue())

    def test_system_down_removes_state_only_when_config_matches(self):
        config = pdalink_lite.SystemRouteConfig(
            iface="pdalink0",
            address="10.66.0.2/24",
            mtu=1500,
            routes=("0.0.0.0/1", "128.0.0.0/1"),
            proxy_url="http://127.0.0.1:18080",
        )
        with tempfile.TemporaryDirectory() as tmp:
            state_path = Path(tmp) / "system.json"
            pdalink_lite.write_system_state(state_path, config)
            args = argparse.Namespace(
                iface="pdalink0",
                address="10.66.0.2/24",
                mtu=1500,
                routes="0.0.0.0/1,128.0.0.0/1",
                proxy_url="http://127.0.0.1:18080",
                adapter_command="",
                state_file=str(state_path),
                apply=True,
            )

            with mock.patch.object(pdalink_lite.os, "geteuid", return_value=0), \
                    mock.patch.object(pdalink_lite, "run_network_commands_best_effort", return_value=True):
                self.assertEqual(pdalink_lite.system_down(args), 0)

            self.assertFalse(state_path.exists())

    def test_system_down_preserves_unrelated_state(self):
        config = pdalink_lite.SystemRouteConfig(
            iface="pdalink0",
            address="10.66.0.2/24",
            mtu=1500,
            routes=("0.0.0.0/1", "128.0.0.0/1"),
            proxy_url="http://192.168.2.193:18081",
        )
        with tempfile.TemporaryDirectory() as tmp:
            state_path = Path(tmp) / "system.json"
            pdalink_lite.write_system_state(state_path, config)
            args = argparse.Namespace(
                iface="other0",
                address="10.77.0.2/24",
                mtu=1500,
                routes="10.0.0.0/8",
                proxy_url="http://127.0.0.1:18080",
                adapter_command="",
                state_file=str(state_path),
                apply=True,
            )
            stderr = io.StringIO()

            with mock.patch.object(pdalink_lite.os, "geteuid", return_value=0), \
                    mock.patch.object(pdalink_lite, "run_network_commands_best_effort", return_value=True), \
                    redirect_stderr(stderr):
                self.assertEqual(pdalink_lite.system_down(args), 0)

            self.assertTrue(state_path.exists())
            self.assertIn("state not removed", stderr.getvalue())

    def test_system_stop_succeeds_when_running_process_removes_state(self):
        config = pdalink_lite.SystemRouteConfig(
            iface="pdalink0",
            address="10.66.0.2/24",
            mtu=1500,
            routes=("0.0.0.0/1", "128.0.0.0/1"),
            proxy_url="http://127.0.0.1:18080",
        )
        with tempfile.TemporaryDirectory() as tmp:
            state_path = Path(tmp) / "system.json"
            pdalink_lite.write_system_state(state_path, config)
            payload = json.loads(state_path.read_text(encoding="utf-8"))
            payload["pid"] = os.getpid() + 10000
            state_path.write_text(json.dumps(payload), encoding="utf-8")
            args = argparse.Namespace(
                iface="pdalink0",
                address="10.66.0.2/24",
                mtu=1500,
                routes="0.0.0.0/1,128.0.0.0/1",
                proxy_url="http://127.0.0.1:18080",
                state_file=str(state_path),
                stop_timeout=0.1,
            )

            def fake_stop_process(_pid, _timeout):
                state_path.unlink()

            stdout = io.StringIO()
            with mock.patch.object(pdalink_lite.os, "geteuid", return_value=0), \
                    mock.patch.object(pdalink_lite, "process_exists", return_value=True), \
                    mock.patch.object(pdalink_lite, "process_matches_state", return_value=True), \
                    mock.patch.object(pdalink_lite, "stop_process", side_effect=fake_stop_process), \
                    mock.patch.object(pdalink_lite, "run_network_commands_best_effort") as cleanup, \
                    redirect_stdout(stdout):
                self.assertEqual(pdalink_lite.system_stop(args), 0)

            cleanup.assert_not_called()
            self.assertIn("state removed by stopped process", stdout.getvalue())

    def test_system_stop_does_not_signal_reused_pid(self):
        config = pdalink_lite.SystemRouteConfig(
            iface="pdalink0",
            address="10.66.0.2/24",
            mtu=1500,
            routes=("0.0.0.0/1", "128.0.0.0/1"),
            proxy_url="http://127.0.0.1:18080",
        )
        with tempfile.TemporaryDirectory() as tmp:
            state_path = Path(tmp) / "system.json"
            pdalink_lite.write_system_state(state_path, config)
            payload = json.loads(state_path.read_text(encoding="utf-8"))
            payload["pid"] = os.getpid() + 10000
            payload["pid_start_ticks"] = "old-start"
            state_path.write_text(json.dumps(payload), encoding="utf-8")
            args = argparse.Namespace(
                iface="pdalink0",
                address="10.66.0.2/24",
                mtu=1500,
                routes="0.0.0.0/1,128.0.0.0/1",
                proxy_url="http://127.0.0.1:18080",
                state_file=str(state_path),
                stop_timeout=0.1,
            )
            stdout = io.StringIO()
            with mock.patch.object(pdalink_lite.os, "geteuid", return_value=0), \
                    mock.patch.object(pdalink_lite, "process_exists", return_value=True), \
                    mock.patch.object(pdalink_lite, "process_matches_state", return_value=False), \
                    mock.patch.object(pdalink_lite, "stop_process") as stop, \
                    mock.patch.object(pdalink_lite, "run_network_commands_best_effort", return_value=True), \
                    redirect_stdout(stdout):
                self.assertEqual(pdalink_lite.system_stop(args), 0)

            stop.assert_not_called()
            self.assertFalse(state_path.exists())
            self.assertIn("not signaling", stdout.getvalue())

    def test_system_stop_preserves_state_when_cleanup_fails(self):
        config = pdalink_lite.SystemRouteConfig(
            iface="pdalink0",
            address="10.66.0.2/24",
            mtu=1500,
            routes=("0.0.0.0/1", "128.0.0.0/1"),
            proxy_url="http://127.0.0.1:18080",
        )
        with tempfile.TemporaryDirectory() as tmp:
            state_path = Path(tmp) / "system.json"
            pdalink_lite.write_system_state(state_path, config)
            args = argparse.Namespace(
                iface="pdalink0",
                address="10.66.0.2/24",
                mtu=1500,
                routes="0.0.0.0/1,128.0.0.0/1",
                proxy_url="http://127.0.0.1:18080",
                state_file=str(state_path),
                stop_timeout=0.1,
            )
            stderr = io.StringIO()
            stdout = io.StringIO()
            with mock.patch.object(pdalink_lite.os, "geteuid", return_value=0), \
                    mock.patch.object(pdalink_lite, "process_exists", return_value=False), \
                    mock.patch.object(pdalink_lite, "run_network_commands_best_effort", return_value=False), \
                    redirect_stdout(stdout), \
                    redirect_stderr(stderr):
                self.assertEqual(pdalink_lite.system_stop(args), 1)

            self.assertTrue(state_path.exists())
            self.assertIn("preserving state", stderr.getvalue())

    def test_system_stop_preserves_state_when_process_survives_stop(self):
        config = pdalink_lite.SystemRouteConfig(
            iface="pdalink0",
            address="10.66.0.2/24",
            mtu=1500,
            routes=("0.0.0.0/1", "128.0.0.0/1"),
            proxy_url="http://127.0.0.1:18080",
        )
        with tempfile.TemporaryDirectory() as tmp:
            state_path = Path(tmp) / "system.json"
            pdalink_lite.write_system_state(state_path, config)
            payload = json.loads(state_path.read_text(encoding="utf-8"))
            payload["pid"] = os.getpid() + 10000
            state_path.write_text(json.dumps(payload), encoding="utf-8")
            args = argparse.Namespace(
                iface="pdalink0",
                address="10.66.0.2/24",
                mtu=1500,
                routes="0.0.0.0/1,128.0.0.0/1",
                proxy_url="http://127.0.0.1:18080",
                state_file=str(state_path),
                stop_timeout=0.1,
            )
            stderr = io.StringIO()
            stdout = io.StringIO()
            with mock.patch.object(pdalink_lite.os, "geteuid", return_value=0), \
                    mock.patch.object(pdalink_lite, "process_exists", return_value=True), \
                    mock.patch.object(pdalink_lite, "process_matches_state", return_value=True), \
                    mock.patch.object(pdalink_lite, "stop_process", return_value=False), \
                    mock.patch.object(pdalink_lite, "run_network_commands_best_effort") as cleanup, \
                    redirect_stdout(stdout), \
                    redirect_stderr(stderr):
                self.assertEqual(pdalink_lite.system_stop(args), 1)

            cleanup.assert_not_called()
            self.assertTrue(state_path.exists())
            self.assertIn("process still running", stderr.getvalue())

    def test_system_start_rolls_back_when_state_write_fails(self):
        class FakeAdapter:
            def __init__(self):
                self.closed = False

            def run(self, *args, **kwargs):
                raise AssertionError("adapter should not run when state write fails")

            def close(self):
                self.closed = True

        class FakeTunHttpAdapter:
            opened = None

            @classmethod
            def open(cls, *_args, **_kwargs):
                cls.opened = FakeAdapter()
                return cls.opened

        args = argparse.Namespace(
            iface="pdalink0",
            address="10.66.0.2/24",
            mtu=1500,
            routes="0.0.0.0/1,128.0.0.0/1",
            proxy_url="http://127.0.0.1:18080",
            no_dns=False,
            connect_timeout=10.0,
            verbose=False,
            start_usb=False,
            remote_port=8000,
            start_android=True,
            probe_proxy=False,
            verify_timeout=15.0,
            state_file="/unwritable/system.json",
        )
        stderr = io.StringIO()
        with mock.patch.object(pdalink_lite, "start_dns_ready_adapter", return_value=None), \
                mock.patch.object(pdalink_lite.os, "geteuid", return_value=0), \
                mock.patch.dict(sys.modules, {"pdalink_tun": mock.Mock(TunHttpAdapter=FakeTunHttpAdapter)}), \
                mock.patch.object(pdalink_lite, "existing_tun_route_conflicts", return_value=[]), \
                mock.patch.object(pdalink_lite, "run_privileged_network_commands", return_value=0), \
                mock.patch.object(pdalink_lite, "write_system_state", side_effect=OSError("denied")), \
                mock.patch.object(pdalink_lite, "run_network_commands_best_effort", return_value=True) as cleanup, \
                redirect_stderr(stderr):
            self.assertEqual(pdalink_lite.system_start(args), 1)

        self.assertTrue(FakeTunHttpAdapter.opened.closed)
        cleanup.assert_called_once()
        self.assertIn("failed to write system state", stderr.getvalue())

    def test_system_start_rolls_back_when_route_or_dns_setup_fails(self):
        class FakeAdapter:
            def __init__(self):
                self.closed = False

            def run(self, *args, **kwargs):
                raise AssertionError("adapter should not run when setup fails")

            def close(self):
                self.closed = True

        class FakeTunHttpAdapter:
            opened = None

            @classmethod
            def open(cls, *_args, **_kwargs):
                cls.opened = FakeAdapter()
                return cls.opened

        args = argparse.Namespace(
            iface="pdalink0",
            address="10.66.0.2/24",
            mtu=1500,
            routes="0.0.0.0/1,128.0.0.0/1",
            proxy_url="http://127.0.0.1:18080",
            no_dns=False,
            connect_timeout=10.0,
            verbose=False,
            start_usb=False,
            remote_port=8000,
            start_android=True,
            probe_proxy=False,
            verify_timeout=15.0,
            state_file="/tmp/pdalink-state.json",
            route_mode="active_override",
            route_metric=50000,
            dns_mode="off",
            dns_servers="1.1.1.1,8.8.8.8",
        )
        with mock.patch.object(pdalink_lite.os, "geteuid", return_value=0), \
                mock.patch.dict(sys.modules, {"pdalink_tun": mock.Mock(TunHttpAdapter=FakeTunHttpAdapter)}), \
                mock.patch.object(pdalink_lite, "existing_tun_route_conflicts", return_value=[]), \
                mock.patch.object(pdalink_lite, "run_privileged_network_commands", return_value=1), \
                mock.patch.object(pdalink_lite, "run_network_commands_best_effort", return_value=True) as cleanup, \
                redirect_stderr(io.StringIO()):
            self.assertEqual(pdalink_lite.system_start(args), 1)

        self.assertTrue(FakeTunHttpAdapter.opened.closed)
        cleanup.assert_called_once()

    def test_system_start_refuses_active_override_when_resolvectl_missing(self):
        class FakeAdapter:
            def __init__(self):
                self.closed = False

            def close(self):
                self.closed = True

        class FakeTunHttpAdapter:
            opened = None

            @classmethod
            def open(cls, *_args, **_kwargs):
                cls.opened = FakeAdapter()
                return cls.opened

        args = argparse.Namespace(
            iface="pdalink0",
            address="10.66.0.2/24",
            mtu=1500,
            routes="0.0.0.0/1,128.0.0.0/1",
            proxy_url="http://127.0.0.1:18080",
            no_dns=False,
            connect_timeout=10.0,
            verbose=False,
            start_usb=False,
            remote_port=8000,
            start_android=True,
            probe_proxy=False,
            verify_timeout=15.0,
            state_file="/tmp/pdalink-state.json",
            route_mode="active_override",
            route_metric=50000,
            dns_mode="resolved",
            dns_servers="1.1.1.1,8.8.8.8",
        )
        stderr = io.StringIO()
        with mock.patch.object(pdalink_lite.os, "geteuid", return_value=0), \
                mock.patch.dict(sys.modules, {"pdalink_tun": mock.Mock(TunHttpAdapter=FakeTunHttpAdapter)}), \
                mock.patch.object(pdalink_lite, "existing_tun_route_conflicts", return_value=[]), \
                mock.patch.object(pdalink_lite.shutil, "which", return_value=None), \
                mock.patch.object(pdalink_lite, "run_privileged_network_commands") as run, \
                redirect_stderr(stderr):
            self.assertEqual(pdalink_lite.system_start(args), 1)

        run.assert_not_called()
        self.assertTrue(FakeTunHttpAdapter.opened.closed)
        self.assertIn("refusing to change routes", stderr.getvalue())

    def test_system_start_no_dns_disables_resolved_dns_policy(self):
        class FakeAdapter:
            def __init__(self):
                self.closed = False

            def run(self, *args, **kwargs):
                return None

            def close(self):
                self.closed = True

        class FakeTunHttpAdapter:
            opened = None

            @classmethod
            def open(cls, *_args, **_kwargs):
                cls.opened = FakeAdapter()
                return cls.opened

        with tempfile.TemporaryDirectory() as tmp:
            state_file = str(Path(tmp) / "system.json")
            args = argparse.Namespace(
                iface="pdalink0",
                address="10.66.0.2/24",
                mtu=1500,
                routes="0.0.0.0/1,128.0.0.0/1",
                proxy_url="http://127.0.0.1:18080",
                no_dns=True,
                connect_timeout=10.0,
                verbose=False,
                start_usb=False,
                remote_port=8000,
                start_android=True,
                probe_proxy=False,
                verify_timeout=15.0,
                state_file=state_file,
                route_mode="active_override",
                route_metric=50000,
                dns_mode="resolved",
                dns_servers="1.1.1.1,8.8.8.8",
            )
            with mock.patch.object(pdalink_lite.os, "geteuid", return_value=0), \
                    mock.patch.dict(sys.modules, {"pdalink_tun": mock.Mock(TunHttpAdapter=FakeTunHttpAdapter)}), \
                    mock.patch.object(pdalink_lite, "existing_tun_route_conflicts", return_value=[]), \
                    mock.patch.object(pdalink_lite.shutil, "which", return_value=None), \
                    mock.patch.object(pdalink_lite, "run_privileged_network_commands", return_value=0) as run, \
                    mock.patch.object(pdalink_lite, "run_network_commands_best_effort", return_value=True), \
                    redirect_stdout(io.StringIO()):
                self.assertEqual(pdalink_lite.system_start(args), 0)

        commands = run.call_args.args[0]
        self.assertFalse(any(command and command[0] == "resolvectl" for command in commands))
        self.assertTrue(FakeTunHttpAdapter.opened.closed)

    def test_load_system_state_preserves_zero_route_metric(self):
        config = pdalink_lite.SystemRouteConfig(
            iface="pdalink0",
            address="10.66.0.2/24",
            mtu=1500,
            routes=("0.0.0.0/1", "128.0.0.0/1"),
            proxy_url="http://127.0.0.1:18080",
            route_mode="fallback_low_priority",
            route_metric=0,
        )
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "system.json"
            pdalink_lite.write_system_state(path, config)

            loaded = pdalink_lite.load_system_state(path)

            self.assertIsNotNone(loaded)
            self.assertEqual(loaded.config.route_metric, 0)

    def test_teardown_best_effort_treats_absent_resources_as_clean(self):
        commands = [
            ["ip", "route", "del", "0.0.0.0/1", "dev", "pdalink0"],
            ["ip", "link", "del", "dev", "pdalink0"],
        ]

        def fake_run(command, **_kwargs):
            if command[:3] == ["ip", "route", "del"]:
                return mock.Mock(returncode=2, stdout="", stderr="Error: No such process")
            if command[:4] == ["ip", "route", "show", "0.0.0.0/1"]:
                return mock.Mock(returncode=0, stdout="", stderr="")
            if command[:4] == ["ip", "link", "del", "dev"]:
                return mock.Mock(returncode=1, stdout="", stderr="Cannot find device")
            if command[:4] == ["ip", "link", "show", "dev"]:
                return mock.Mock(returncode=1, stdout="", stderr="Device does not exist")
            raise AssertionError(f"unexpected command: {command!r}")

        stdout = io.StringIO()
        stderr = io.StringIO()
        with mock.patch.object(pdalink_lite.subprocess, "run", side_effect=fake_run), \
                redirect_stdout(stdout), \
                redirect_stderr(stderr):
            self.assertTrue(pdalink_lite.run_network_commands_best_effort(commands))

        self.assertEqual(stdout.getvalue().count("already clean"), 2)

    def test_teardown_best_effort_treats_missing_dns_link_as_clean(self):
        commands = [["resolvectl", "revert", "pdalink0"]]

        def fake_run(command, **_kwargs):
            if command == ["resolvectl", "revert", "pdalink0"]:
                return mock.Mock(returncode=1, stdout="", stderr="Link pdalink0 not found")
            if command == ["ip", "link", "show", "dev", "pdalink0"]:
                return mock.Mock(returncode=1, stdout="", stderr="Device does not exist")
            raise AssertionError(f"unexpected command: {command!r}")

        stdout = io.StringIO()
        stderr = io.StringIO()
        with mock.patch.object(pdalink_lite.subprocess, "run", side_effect=fake_run), \
                redirect_stdout(stdout), \
                redirect_stderr(stderr):
            self.assertTrue(pdalink_lite.run_network_commands_best_effort(commands))

        self.assertIn("already clean", stdout.getvalue())

    def test_teardown_best_effort_keeps_real_route_failure(self):
        commands = [["ip", "route", "del", "0.0.0.0/1", "dev", "pdalink0"]]

        def fake_run(command, **_kwargs):
            if command[:3] == ["ip", "route", "del"]:
                return mock.Mock(returncode=2, stdout="", stderr="Operation not permitted")
            if command[:4] == ["ip", "route", "show", "0.0.0.0/1"]:
                return mock.Mock(returncode=0, stdout="0.0.0.0/1 dev pdalink0\n", stderr="")
            raise AssertionError(f"unexpected command: {command!r}")

        with mock.patch.object(pdalink_lite.subprocess, "run", side_effect=fake_run), \
                redirect_stdout(io.StringIO()), \
                redirect_stderr(io.StringIO()):
            self.assertFalse(pdalink_lite.run_network_commands_best_effort(commands))

    def test_system_status_reports_missing_custom_adapter(self):
        class FakeTun:
            def exists(self):
                return True

            def __str__(self):
                return "/dev/net/tun"

        args = argparse.Namespace(
            iface="pdalink0",
            address="10.66.0.2/24",
            mtu=1500,
            routes="0.0.0.0/1,128.0.0.0/1",
            proxy_url="http://127.0.0.1:18080",
            adapter_command="missing-adapter --tun {iface}",
            probe_proxy=False,
        )
        stdout = io.StringIO()
        with mock.patch.object(pdalink_lite, "TUN_DEVICE", FakeTun()), \
                mock.patch.object(pdalink_lite.os, "access", return_value=True), \
                mock.patch.object(pdalink_lite.shutil, "which", side_effect=lambda name: "/usr/bin/ip" if name == "ip" else None), \
                redirect_stdout(stdout):
            self.assertEqual(pdalink_lite.system_status(args), 1)

        self.assertIn("adapter: CONFIGURED executable not found", stdout.getvalue())

    def test_system_status_reports_runtime_helper_drift(self):
        class FakeTun:
            def exists(self):
                return True

            def __str__(self):
                return "/dev/net/tun"

        config = pdalink_lite.SystemRouteConfig(
            iface="pdalink0",
            address="10.66.0.2/24",
            mtu=1500,
            routes=("0.0.0.0/1", "128.0.0.0/1"),
            proxy_url="http://127.0.0.1:18080",
        )
        with tempfile.TemporaryDirectory() as tmp:
            state_path = Path(tmp) / "system.json"
            runtime_path = Path(tmp) / "installed" / "pdalink_lite.py"
            runtime_path.parent.mkdir()
            runtime_path.write_text(
                "#!/usr/bin/env python3\n# deliberately stale runtime\n",
                encoding="utf-8",
            )
            pdalink_lite.write_system_state(state_path, config)
            payload = json.loads(state_path.read_text(encoding="utf-8"))
            payload["pid"] = os.getpid()
            payload["cmdline"] = (
                f"python3 {runtime_path} system-start --tcp-idle-timeout 0.0"
            )
            state_path.write_text(json.dumps(payload), encoding="utf-8")
            args = argparse.Namespace(
                iface="pdalink0",
                address="10.66.0.2/24",
                mtu=1500,
                routes="0.0.0.0/1,128.0.0.0/1",
                proxy_url="http://127.0.0.1:18080",
                route_mode=pdalink_lite.DEFAULT_ROUTE_MODE,
                route_metric=pdalink_lite.DEFAULT_ROUTE_METRIC,
                dns_mode="off",
                dns_servers="1.1.1.1",
                adapter_command="",
                probe_proxy=False,
                state_file=str(state_path),
            )
            stdout = io.StringIO()
            with mock.patch.object(pdalink_lite, "TUN_DEVICE", FakeTun()), \
                    mock.patch.object(pdalink_lite.os, "access", return_value=True), \
                    mock.patch.object(pdalink_lite.shutil, "which", return_value="/usr/sbin/ip"), \
                    mock.patch.object(pdalink_lite, "process_matches_state", return_value=True), \
                    redirect_stdout(stdout):
                self.assertEqual(pdalink_lite.system_status(args), 0)

        output = stdout.getvalue()
        self.assertIn(
            f"state-cmdline: python3 {runtime_path} system-start --tcp-idle-timeout 0.0",
            output,
        )
        self.assertIn(f"runtime-binary: {runtime_path}", output)
        self.assertIn(f"checkout-binary: {Path(pdalink_lite.__file__).resolve()}", output)
        self.assertIn("runtime-drift: YES", output)

    def test_runtime_drift_ignores_different_path_when_file_content_matches(self):
        with tempfile.TemporaryDirectory() as tmp:
            runtime = Path(tmp) / "pdalink_lite.py"
            runtime.write_bytes(Path(pdalink_lite.__file__).read_bytes())

            status = pdalink_lite.system_runtime_drift_status(str(runtime))

        self.assertTrue(status.startswith("NO"), status)
        self.assertIn("content matches", status)

    def test_pdalink_tun_builds_ipv4_tcp_roundtrip(self):
        tcp = pdalink_tun.build_tcp_segment(
            "10.66.0.2",
            "1.1.1.1",
            45678,
            443,
            100,
            0,
            pdalink_tun.TCP_SYN,
        )
        packet = pdalink_tun.build_ipv4_packet("10.66.0.2", "1.1.1.1", pdalink_tun.IP_PROTO_TCP, tcp)
        parsed_ip = pdalink_tun.parse_ipv4_packet(packet)
        parsed_tcp = pdalink_tun.parse_tcp_segment(parsed_ip.payload)

        self.assertEqual(parsed_ip.src, "10.66.0.2")
        self.assertEqual(parsed_ip.dst, "1.1.1.1")
        self.assertEqual(parsed_tcp.src_port, 45678)
        self.assertEqual(parsed_tcp.dst_port, 443)
        self.assertEqual(parsed_tcp.flags, pdalink_tun.TCP_SYN)

    def test_pdalink_tun_builds_udp_dns_reply(self):
        udp = pdalink_tun.build_udp_datagram(53, 53000, b"dns-response")
        packet = pdalink_tun.build_ipv4_packet("1.1.1.1", "10.66.0.2", pdalink_tun.IP_PROTO_UDP, udp)
        parsed_ip = pdalink_tun.parse_ipv4_packet(packet)
        parsed_udp = pdalink_tun.parse_udp_datagram(parsed_ip.payload)

        self.assertEqual(parsed_ip.proto, pdalink_tun.IP_PROTO_UDP)
        self.assertEqual(parsed_udp.src_port, 53)
        self.assertEqual(parsed_udp.dst_port, 53000)
        self.assertEqual(parsed_udp.payload, b"dns-response")

    def _tun_syn(self, dst: str, dst_port: int = 4000) -> bytes:
        tcp = pdalink_tun.build_tcp_segment("10.66.0.2", dst, 45678, dst_port, 100, 0, pdalink_tun.TCP_SYN)
        return pdalink_tun.build_ipv4_packet("10.66.0.2", dst, pdalink_tun.IP_PROTO_TCP, tcp)

    def test_pdalink_tun_resets_private_destination_without_proxy_connect(self):
        read_fd, write_fd = os.pipe()
        adapter = pdalink_tun.TunHttpAdapter(write_fd, "pdalink0", "http://127.0.0.1:18080")
        adapter.proxy = mock.Mock()

        with mock.patch.object(pdalink_tun.threading, "Thread") as thread:
            adapter.handle_packet(self._tun_syn("192.168.50.17"))

        readable, _, _ = select.select([read_fd], [], [], 1)
        self.assertTrue(readable)
        reply = pdalink_tun.parse_ipv4_packet(os.read(read_fd, 4096))
        reply_tcp = pdalink_tun.parse_tcp_segment(reply.payload)
        adapter.close()
        os.close(read_fd)

        thread.assert_not_called()
        adapter.proxy.connect.assert_not_called()
        self.assertEqual(adapter.flows, {})
        self.assertEqual(reply.src, "192.168.50.17")
        self.assertEqual(reply.dst, "10.66.0.2")
        self.assertEqual(reply_tcp.flags, pdalink_tun.TCP_RST | pdalink_tun.TCP_ACK)
        self.assertEqual(reply_tcp.ack, 101)
        self.assertEqual(adapter.tcp_anomalies["rejected_local_destination"], 1)

    def test_pdalink_tun_classifies_local_only_destinations(self):
        for address in ("10.1.2.3", "172.20.0.1", "192.168.50.1", "169.254.1.1", "127.0.0.1", "224.0.0.251"):
            self.assertTrue(pdalink_tun.is_local_only_destination(address), address)
        for address in ("1.1.1.1", "172.32.0.1", "100.101.197.100", "8.8.8.8"):
            self.assertFalse(pdalink_tun.is_local_only_destination(address), address)

    def test_pdalink_tun_resets_syn_when_pending_connect_limit_is_full(self):
        read_fd, write_fd = os.pipe()
        adapter = pdalink_tun.TunHttpAdapter(write_fd, "pdalink0", "http://127.0.0.1:18080")
        for index in range(pdalink_tun.MAX_PENDING_TCP_CONNECTS):
            adapter.flows[("10.66.0.2", 1000 + index, "1.1.1.1", 443)] = mock.Mock(connected=False, closed=False)

        with mock.patch.object(pdalink_tun.threading, "Thread") as thread:
            adapter.handle_packet(self._tun_syn("1.1.1.1", 443))

        readable, _, _ = select.select([read_fd], [], [], 1)
        self.assertTrue(readable)
        os.read(read_fd, 4096)
        adapter.flows.clear()
        adapter.close()
        os.close(read_fd)

        thread.assert_not_called()
        self.assertEqual(adapter.tcp_anomalies["rejected_pending_connect_limit"], 1)

    def test_pdalink_tun_drops_dns_when_inflight_limit_is_full(self):
        read_fd, write_fd = os.pipe()
        adapter = pdalink_tun.TunHttpAdapter(write_fd, "pdalink0", "http://127.0.0.1:18080", verbose=True)
        adapter.dns_slots = mock.Mock()
        adapter.dns_slots.acquire.return_value = False
        udp = pdalink_tun.build_udp_datagram(53000, 53, b"dns-query")
        packet = pdalink_tun.build_ipv4_packet("10.66.0.2", "8.8.8.8", pdalink_tun.IP_PROTO_UDP, udp)

        stdout = io.StringIO()
        with mock.patch.object(pdalink_tun.threading, "Thread") as thread, redirect_stdout(stdout):
            adapter.handle_packet(packet)

        thread.assert_not_called()
        adapter.dns_slots.acquire.assert_called_once_with(blocking=False)
        self.assertIn("too many queries", stdout.getvalue())
        adapter.close()
        os.close(read_fd)

    def test_pdalink_tun_releases_dns_slot_when_thread_start_fails(self):
        read_fd, write_fd = os.pipe()
        adapter = pdalink_tun.TunHttpAdapter(write_fd, "pdalink0", "http://127.0.0.1:18080", verbose=True)
        adapter.dns_slots = mock.Mock()
        adapter.dns_slots.acquire.return_value = True
        udp = pdalink_tun.build_udp_datagram(53000, 53, b"dns-query")
        packet = pdalink_tun.build_ipv4_packet("10.66.0.2", "8.8.8.8", pdalink_tun.IP_PROTO_UDP, udp)

        thread = mock.Mock()
        thread.start.side_effect = RuntimeError("can't start new thread")
        stdout = io.StringIO()
        with mock.patch.object(pdalink_tun.threading, "Thread", return_value=thread), redirect_stdout(stdout):
            adapter.handle_packet(packet)

        adapter.dns_slots.release.assert_called_once()
        self.assertIn("failed to start resolver thread", stdout.getvalue())
        adapter.close()
        os.close(read_fd)

    def test_pdalink_tun_sends_dns_servfail_when_proxy_dns_fails(self):
        read_fd, write_fd = os.pipe()
        adapter = pdalink_tun.TunHttpAdapter(write_fd, "pdalink0", "http://127.0.0.1:18080")
        adapter.dns_slots.acquire()
        adapter.proxy = mock.Mock()
        adapter.proxy.connect.side_effect = OSError("proxy down")
        query_id = 0x1234
        question = b"\x07example\x03com\x00" + struct.pack("!HH", 1, 1)
        query = struct.pack("!HHHHHH", query_id, 0x0100, 1, 0, 0, 0) + question
        udp = pdalink_tun.build_udp_datagram(53000, 53, query)
        packet = pdalink_tun.parse_ipv4_packet(
            pdalink_tun.build_ipv4_packet("10.66.0.2", "8.8.8.8", pdalink_tun.IP_PROTO_UDP, udp)
        )
        datagram = pdalink_tun.parse_udp_datagram(packet.payload)

        adapter.resolve_dns_over_tcp(packet, datagram)
        readable, _, _ = select.select([read_fd], [], [], 1)
        self.assertTrue(readable)
        response = os.read(read_fd, 4096)
        parsed_ip = pdalink_tun.parse_ipv4_packet(response)
        parsed_udp = pdalink_tun.parse_udp_datagram(parsed_ip.payload)
        rid, flags, qdcount, answers, _ns, _ar = struct.unpack("!HHHHHH", parsed_udp.payload[:12])

        adapter.close()
        os.close(read_fd)

        self.assertEqual(parsed_ip.src, "8.8.8.8")
        self.assertEqual(parsed_ip.dst, "10.66.0.2")
        self.assertEqual(parsed_udp.src_port, 53)
        self.assertEqual(parsed_udp.dst_port, 53000)
        self.assertEqual(rid, query_id)
        self.assertTrue(flags & 0x8000)
        self.assertEqual(flags & 0x000F, 2)
        self.assertEqual(qdcount, 1)
        self.assertEqual(answers, 0)

    def test_pdalink_tun_closes_idle_tcp_flows(self):
        read_fd, write_fd = os.pipe()
        adapter = pdalink_tun.TunHttpAdapter(
            write_fd,
            "pdalink0",
            "http://127.0.0.1:18080",
            tcp_idle_timeout=10.0,
        )
        flow = mock.Mock()
        flow.last_activity = 50.0
        flow.client_ip = "10.66.0.2"
        flow.client_port = 53000
        flow.remote_ip = "1.1.1.1"
        flow.remote_port = 443
        adapter.flows[("10.66.0.2", 53000, "1.1.1.1", 443)] = flow

        with mock.patch.object(pdalink_tun.time, "monotonic", return_value=61.0):
            adapter.cleanup_idle_flows()

        flow.close.assert_called_once()
        adapter.flows.clear()
        adapter.close()
        os.close(read_fd)

    def test_pdalink_tun_reports_active_flow_metrics(self):
        read_fd, write_fd = os.pipe()
        adapter = pdalink_tun.TunHttpAdapter(write_fd, "pdalink0", "http://127.0.0.1:18080")
        tcp = pdalink_tun.build_tcp_segment(
            "10.66.0.2",
            "1.1.1.1",
            53000,
            443,
            100,
            0,
            pdalink_tun.TCP_SYN,
        )
        packet = pdalink_tun.parse_ipv4_packet(
            pdalink_tun.build_ipv4_packet("10.66.0.2", "1.1.1.1", pdalink_tun.IP_PROTO_TCP, tcp)
        )
        segment = pdalink_tun.parse_tcp_segment(packet.payload)
        flow = pdalink_tun.TcpFlow(adapter, ("10.66.0.2", 53000, "1.1.1.1", 443), packet, segment)
        flow.connected = True
        flow.sock = mock.Mock()
        adapter.flows[flow.key] = flow

        payload = b"whoami\n"
        data_segment = pdalink_tun.TcpSegment(
            53000,
            443,
            flow.client_next,
            flow.server_next,
            pdalink_tun.TCP_ACK | pdalink_tun.TCP_PSH,
            65535,
            payload,
        )
        flow.handle(data_segment)
        status = adapter.tcp_status()

        adapter.close()
        os.close(read_fd)

        self.assertEqual(status["active_flows"], 1)
        self.assertEqual(status["bytes_up"], len(payload))
        self.assertEqual(status["active"][0]["bytes_up"], len(payload))
        self.assertEqual(status["active"][0]["remote"], "1.1.1.1:443")

    def test_pdalink_tun_records_proxy_close_reason_and_bytes_down(self):
        read_fd, write_fd = os.pipe()
        adapter = pdalink_tun.TunHttpAdapter(write_fd, "pdalink0", "http://127.0.0.1:18080")
        tcp = pdalink_tun.build_tcp_segment(
            "10.66.0.2",
            "1.1.1.1",
            53000,
            443,
            100,
            0,
            pdalink_tun.TCP_SYN,
        )
        packet = pdalink_tun.parse_ipv4_packet(
            pdalink_tun.build_ipv4_packet("10.66.0.2", "1.1.1.1", pdalink_tun.IP_PROTO_TCP, tcp)
        )
        segment = pdalink_tun.parse_tcp_segment(packet.payload)
        flow = pdalink_tun.TcpFlow(adapter, ("10.66.0.2", 53000, "1.1.1.1", 443), packet, segment)
        flow.connected = True
        flow.sock = mock.Mock()
        flow.sock.recv.side_effect = [b"abc", b""]
        adapter.flows[flow.key] = flow

        flow.read_upstream()
        status = adapter.tcp_status()

        adapter.close()
        os.close(read_fd)

        self.assertEqual(status["active_flows"], 0)
        self.assertEqual(status["closed"]["proxy_close"], 1)
        self.assertEqual(status["bytes_down"], 3)

    def test_tcp_flow_does_not_resend_duplicate_payload_upstream(self):
        read_fd, write_fd = os.pipe()
        adapter = pdalink_tun.TunHttpAdapter(write_fd, "pdalink0", "http://127.0.0.1:18080")
        tcp = pdalink_tun.build_tcp_segment(
            "10.66.0.2",
            "1.1.1.1",
            53000,
            443,
            100,
            0,
            pdalink_tun.TCP_SYN,
        )
        packet = pdalink_tun.parse_ipv4_packet(
            pdalink_tun.build_ipv4_packet("10.66.0.2", "1.1.1.1", pdalink_tun.IP_PROTO_TCP, tcp)
        )
        segment = pdalink_tun.parse_tcp_segment(packet.payload)
        flow = pdalink_tun.TcpFlow(adapter, ("10.66.0.2", 53000, "1.1.1.1", 443), packet, segment)
        flow.connected = True
        sock = mock.Mock()
        flow.sock = sock
        adapter.flows[flow.key] = flow
        first_seq = flow.client_next
        payload = b"date\n"

        flow.handle(pdalink_tun.TcpSegment(53000, 443, first_seq, flow.server_next, pdalink_tun.TCP_ACK, 65535, payload))
        flow.handle(pdalink_tun.TcpSegment(53000, 443, first_seq, flow.server_next, pdalink_tun.TCP_ACK, 65535, payload))
        status = adapter.tcp_status()

        adapter.close()
        os.close(read_fd)

        sock.sendall.assert_called_once_with(payload)
        self.assertEqual(status["anomalies"]["duplicate_payload"], 1)

    def test_tcp_flow_counts_out_of_order_payload_without_forwarding(self):
        read_fd, write_fd = os.pipe()
        adapter = pdalink_tun.TunHttpAdapter(write_fd, "pdalink0", "http://127.0.0.1:18080")
        tcp = pdalink_tun.build_tcp_segment(
            "10.66.0.2",
            "1.1.1.1",
            53000,
            443,
            100,
            0,
            pdalink_tun.TCP_SYN,
        )
        packet = pdalink_tun.parse_ipv4_packet(
            pdalink_tun.build_ipv4_packet("10.66.0.2", "1.1.1.1", pdalink_tun.IP_PROTO_TCP, tcp)
        )
        segment = pdalink_tun.parse_tcp_segment(packet.payload)
        flow = pdalink_tun.TcpFlow(adapter, ("10.66.0.2", 53000, "1.1.1.1", 443), packet, segment)
        flow.connected = True
        sock = mock.Mock()
        flow.sock = sock
        adapter.flows[flow.key] = flow

        flow.handle(pdalink_tun.TcpSegment(
            53000,
            443,
            flow.client_next + 10,
            flow.server_next,
            pdalink_tun.TCP_ACK,
            65535,
            b"future",
        ))
        status = adapter.tcp_status()

        adapter.close()
        os.close(read_fd)

        sock.sendall.assert_not_called()
        self.assertEqual(status["anomalies"]["out_of_order_payload"], 1)

    def test_tcp_flow_reports_large_stream_byte_counts(self):
        read_fd, write_fd = os.pipe()
        adapter = pdalink_tun.TunHttpAdapter(write_fd, "pdalink0", "http://127.0.0.1:18080")
        tcp = pdalink_tun.build_tcp_segment(
            "10.66.0.2",
            "1.1.1.1",
            53000,
            443,
            100,
            0,
            pdalink_tun.TCP_SYN,
        )
        packet = pdalink_tun.parse_ipv4_packet(
            pdalink_tun.build_ipv4_packet("10.66.0.2", "1.1.1.1", pdalink_tun.IP_PROTO_TCP, tcp)
        )
        segment = pdalink_tun.parse_tcp_segment(packet.payload)
        flow = pdalink_tun.TcpFlow(adapter, ("10.66.0.2", 53000, "1.1.1.1", 443), packet, segment)
        flow.connected = True
        flow.sock = mock.Mock()
        flow.sock.recv.side_effect = [b"a" * 1400, b"b" * 700, b""]
        adapter.flows[flow.key] = flow

        flow.read_upstream()
        status = adapter.tcp_status()

        adapter.close()
        os.close(read_fd)

        self.assertEqual(status["closed"]["proxy_close"], 1)
        self.assertEqual(status["bytes_down"], 2100)

    def test_tcp_flow_discards_late_proxy_connect_after_idle_close(self):
        read_fd, write_fd = os.pipe()
        adapter = pdalink_tun.TunHttpAdapter(write_fd, "pdalink0", "http://127.0.0.1:18080")
        adapter.proxy = mock.Mock()
        fake_sock = mock.Mock()
        adapter.proxy.connect.return_value = fake_sock
        tcp = pdalink_tun.build_tcp_segment(
            "10.66.0.2",
            "1.1.1.1",
            53000,
            443,
            100,
            0,
            pdalink_tun.TCP_SYN,
        )
        packet = pdalink_tun.parse_ipv4_packet(
            pdalink_tun.build_ipv4_packet("10.66.0.2", "1.1.1.1", pdalink_tun.IP_PROTO_TCP, tcp)
        )
        segment = pdalink_tun.parse_tcp_segment(packet.payload)
        flow = pdalink_tun.TcpFlow(adapter, ("10.66.0.2", 53000, "1.1.1.1", 443), packet, segment)
        flow.closed = True

        flow.open_proxy()

        fake_sock.close.assert_called_once()
        adapter.close()
        os.close(read_fd)

    def test_http_connect_proxy_sends_connect_request(self):
        received = []
        ready = threading.Event()

        def serve(listener):
            ready.set()
            client, _ = listener.accept()
            with client:
                request = client.recv(4096)
                received.append(request)
                client.sendall(b"HTTP/1.1 200 Connection Established\r\n\r\n")

        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        listener.bind(("127.0.0.1", 0))
        listener.listen(1)
        thread = threading.Thread(target=serve, args=(listener,), daemon=True)
        thread.start()
        ready.wait(1)
        port = listener.getsockname()[1]
        proxy = pdalink_tun.HttpConnectProxy(f"http://127.0.0.1:{port}", timeout=2)
        sock = proxy.connect("1.1.1.1", 443)
        sock.close()
        listener.close()

        self.assertTrue(received)
        self.assertIn(b"CONNECT 1.1.1.1:443 HTTP/1.1", received[0])

    def test_tun_adapter_turns_tcp_syn_into_syn_ack(self):
        proxy_ready = threading.Event()
        stop_proxy = threading.Event()

        def serve_proxy(listener):
            proxy_ready.set()
            client, _ = listener.accept()
            with client:
                client.recv(4096)
                client.sendall(b"HTTP/1.1 200 Connection Established\r\n\r\n")
                stop_proxy.wait(1)

        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        listener.bind(("127.0.0.1", 0))
        listener.listen(1)
        proxy_thread = threading.Thread(target=serve_proxy, args=(listener,), daemon=True)
        proxy_thread.start()
        proxy_ready.wait(1)

        read_fd, write_fd = os.pipe()
        adapter = pdalink_tun.TunHttpAdapter(
            write_fd,
            "pdalink0",
            f"http://127.0.0.1:{listener.getsockname()[1]}",
        )
        tcp = pdalink_tun.build_tcp_segment(
            "10.66.0.2",
            "1.1.1.1",
            50000,
            443,
            123,
            0,
            pdalink_tun.TCP_SYN,
        )
        packet = pdalink_tun.build_ipv4_packet("10.66.0.2", "1.1.1.1", pdalink_tun.IP_PROTO_TCP, tcp)
        adapter.handle_packet(packet)

        readable, _, _ = select.select([read_fd], [], [], 2)
        self.assertTrue(readable)
        response = os.read(read_fd, 4096)
        parsed_ip = pdalink_tun.parse_ipv4_packet(response)
        parsed_tcp = pdalink_tun.parse_tcp_segment(parsed_ip.payload)

        stop_proxy.set()
        adapter.close()
        os.close(read_fd)
        listener.close()
        time.sleep(0.01)

        self.assertEqual(parsed_ip.src, "1.1.1.1")
        self.assertEqual(parsed_ip.dst, "10.66.0.2")
        self.assertEqual(parsed_tcp.src_port, 443)
        self.assertEqual(parsed_tcp.dst_port, 50000)
        self.assertEqual(parsed_tcp.flags & (pdalink_tun.TCP_SYN | pdalink_tun.TCP_ACK), pdalink_tun.TCP_SYN | pdalink_tun.TCP_ACK)


class GsettingsBinaryTest(unittest.TestCase):
    def test_prefers_system_gsettings_over_path(self):
        with mock.patch.object(pdalink_lite.os.path, "isfile", side_effect=lambda p: p == "/usr/bin/gsettings"), \
                mock.patch.object(pdalink_lite.os, "access", return_value=True), \
                mock.patch.object(pdalink_lite.shutil, "which", return_value="/opt/conda/bin/gsettings"):
            self.assertEqual(pdalink_lite.gsettings_binary(), "/usr/bin/gsettings")

    def test_falls_back_to_path_when_no_system_binary(self):
        with mock.patch.object(pdalink_lite.os.path, "isfile", return_value=False), \
                mock.patch.object(pdalink_lite.shutil, "which", return_value="/opt/conda/bin/gsettings"):
            self.assertEqual(pdalink_lite.gsettings_binary(), "/opt/conda/bin/gsettings")

    def test_run_gsettings_strips_backend_overrides(self):
        captured = {}

        def fake_run(command, **kwargs):
            captured["command"] = command
            captured["env"] = kwargs["env"]
            return subprocess.CompletedProcess(command, 0, "'none'\n", "")

        env = {"PATH": "/usr/bin", "GSETTINGS_SCHEMA_DIR": "/opt/conda/share", "GSETTINGS_BACKEND": "keyfile"}
        with mock.patch.dict(pdalink_lite.os.environ, env, clear=True), \
                mock.patch.object(pdalink_lite, "gsettings_binary", return_value="/usr/bin/gsettings"), \
                mock.patch.object(pdalink_lite.subprocess, "run", side_effect=fake_run):
            self.assertEqual(pdalink_lite.gsettings_get("mode"), "'none'")
        self.assertEqual(captured["command"], ["/usr/bin/gsettings", "get", "org.gnome.system.proxy", "mode"])
        self.assertNotIn("GSETTINGS_SCHEMA_DIR", captured["env"])
        self.assertNotIn("GSETTINGS_BACKEND", captured["env"])
        self.assertEqual(captured["env"]["PATH"], "/usr/bin")


if __name__ == "__main__":
    unittest.main()
