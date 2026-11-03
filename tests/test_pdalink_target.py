import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "linux"))

import pdalink_lite
import pdalink_target


class FakeRunner:
    PROFILE_UUID = "8e126239-7e1b-4a78-8cb8-317e58c4a8bb"

    def __init__(self, scan_frequency=2437, restart_ok=True, activation_ok=True):
        self.scan_frequency = scan_frequency
        self.restart_ok = restart_ok
        self.activation_ok = activation_ok
        self.calls = []

    def run(self, command, timeout):
        self.calls.append(list(command))
        if "SSID,FREQ" in command:
            return pdalink_target.CommandResult(
                0,
                f"AndroidShare_1234:{self.scan_frequency}\n",
                "",
            )
        if command[:3] == ["nmcli", "-t", "-f"]:
            return pdalink_target.CommandResult(0, "pdalink-local-hotspot:wlan0\n", "")
        if command[:3] == ["nmcli", "-g", "connection.uuid"]:
            return pdalink_target.CommandResult(0, self.PROFILE_UUID + "\n", "")
        if command[:3] == ["nmcli", "connection", "modify"]:
            return pdalink_target.CommandResult(0, "", "")
        if command[:3] == ["nmcli", "connection", "reload"]:
            return pdalink_target.CommandResult(0, "", "")
        if command[:3] == ["nmcli", "connection", "up"]:
            return pdalink_target.CommandResult(
                0 if self.activation_ok else 1,
                "",
                "activation failed",
            )
        if command[:2] == ["systemctl", "restart"]:
            return pdalink_target.CommandResult(0 if self.restart_ok else 1, "", "restart failed")
        if command[:3] == ["systemctl", "is-active", "--quiet"]:
            return pdalink_target.CommandResult(0, "", "")
        raise AssertionError(f"unexpected command: {command!r}")


def credentials(password="generated-secret"):
    return pdalink_lite.LocalHotspotCredentials(
        ssid="AndroidShare_1234",
        password=password,
        interface="swlan0",
        host="10.123.164.62",
        port=8001,
        security="WPA2/WPA3 transition",
    )


def payload(password="generated-secret"):
    return pdalink_lite.local_hotspot_target_payload(credentials(password))


class PdaLinkTargetTest(unittest.TestCase):
    def test_parse_payload_rejects_public_proxy_and_unknown_fields(self):
        public = json.loads(payload())
        public["host"] = "8.8.8.8"
        with self.assertRaisesRegex(ValueError, "private IPv4"):
            pdalink_target.parse_payload(json.dumps(public))

        extra = json.loads(payload())
        extra["command"] = "reboot"
        with self.assertRaisesRegex(ValueError, "unsupported fields"):
            pdalink_target.parse_payload(json.dumps(extra))

        injected = json.loads(payload())
        injected["password"] = "password\nsetting:value"
        with self.assertRaisesRegex(ValueError, "invalid Wi-Fi password"):
            pdalink_target.parse_payload(json.dumps(injected))

    def test_parse_ssid_frequencies_unescapes_colons(self):
        values = pdalink_target.parse_ssid_frequencies(
            "Other:2412 MHz\nAndroidShare\\:1234:2437 MHz\n",
            "AndroidShare:1234",
        )
        self.assertEqual(values, [2437])

    def test_apply_rejects_5ghz_before_changing_profile(self):
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(
            pdalink_lite,
            "prepare_local_hotspot_profile",
        ) as prepare:
            applier = pdalink_target.TargetApplier(
                paths=pdalink_target.TargetPaths(
                    Path(tmp) / "client.env",
                    Path(tmp) / "state.json",
                    Path(tmp) / "connections",
                ),
                runner=FakeRunner(scan_frequency=5745),
            )
            with self.assertRaisesRegex(pdalink_target.TargetApplyError, "2.4 GHz"):
                applier.apply(credentials())
            prepare.assert_not_called()

    def test_apply_updates_profile_service_and_non_secret_state(self):
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(
            pdalink_lite,
            "prepare_local_hotspot_profile",
            return_value="pdalink-local-hotspot",
        ) as prepare, mock.patch.object(
            pdalink_lite,
            "probe_ports",
            return_value=[pdalink_lite.ProbeResult("10.123.164.62", 8001, True, "ok")],
        ):
            paths = pdalink_target.TargetPaths(
                Path(tmp) / "client.env",
                Path(tmp) / "state.json",
                Path(tmp) / "connections",
            )
            runner = FakeRunner()
            applier = pdalink_target.TargetApplier(paths=paths, runner=runner, sleeper=lambda _: None)

            self.assertEqual(applier.apply(credentials()), "updated")

            prepare.assert_called_once_with(credentials(), "pdalink-local-hotspot", "wlan0")
            profile = paths.network_connections / "pdalink-local-hotspot.nmconnection"
            profile_text = profile.read_text()
            self.assertIn("autoconnect=true", profile_text)
            self.assertIn("autoconnect-retries=0", profile_text)
            self.assertIn("psk=generated-secret", profile_text)
            self.assertIn("psk-flags=0", profile_text)
            self.assertEqual(profile.stat().st_mode & 0o777, 0o600)
            self.assertFalse(any(
                "generated-secret" in argument
                for command in runner.calls
                for argument in command
            ))
            self.assertEqual(
                paths.environment.read_text(),
                "PDALINK_PROXY_URL=http://10.123.164.62:8001\n",
            )
            state_text = paths.state.read_text()
            self.assertNotIn("generated-secret", state_text)
            self.assertEqual(paths.state.stat().st_mode & 0o777, 0o600)

    def test_restart_failure_restores_previous_environment(self):
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(
            pdalink_lite,
            "prepare_local_hotspot_profile",
            return_value="pdalink-local-hotspot",
        ), mock.patch.object(
            pdalink_lite,
            "probe_ports",
            return_value=[pdalink_lite.ProbeResult("10.123.164.62", 8001, True, "ok")],
        ):
            paths = pdalink_target.TargetPaths(
                Path(tmp) / "client.env",
                Path(tmp) / "state.json",
                Path(tmp) / "connections",
            )
            paths.environment.write_text("PDALINK_PROXY_URL=http://10.1.2.3:8001\n")
            applier = pdalink_target.TargetApplier(
                paths=paths,
                runner=FakeRunner(restart_ok=False),
                sleeper=lambda _: None,
            )

            with self.assertRaisesRegex(pdalink_target.TargetApplyError, "restart failed"):
                applier.apply(credentials())

            self.assertEqual(
                paths.environment.read_text(),
                "PDALINK_PROXY_URL=http://10.1.2.3:8001\n",
            )
            self.assertFalse(paths.state.exists())

    def test_activation_failure_restores_previous_networkmanager_keyfile(self):
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(
            pdalink_lite,
            "prepare_local_hotspot_profile",
            return_value="pdalink-local-hotspot",
        ):
            paths = pdalink_target.TargetPaths(
                Path(tmp) / "client.env",
                Path(tmp) / "state.json",
                Path(tmp) / "connections",
            )
            paths.network_connections.mkdir()
            profile = paths.network_connections / "existing.nmconnection"
            previous = (
                "[connection]\n"
                "id=pdalink-local-hotspot\n"
                f"uuid={FakeRunner.PROFILE_UUID}\n"
                "type=wifi\n"
                "autoconnect=false\n"
                "\n"
                "[wifi-security]\n"
                "key-mgmt=wpa-psk\n"
                "psk=old-secret\n"
                "psk-flags=0\n"
            ).encode()
            profile.write_bytes(previous)
            applier = pdalink_target.TargetApplier(
                paths=paths,
                runner=FakeRunner(activation_ok=False),
                sleeper=lambda _: None,
            )

            with self.assertRaisesRegex(
                pdalink_target.TargetApplyError,
                "activation failed",
            ):
                applier.apply(credentials())

            self.assertEqual(profile.read_bytes(), previous)
            self.assertFalse(paths.state.exists())


if __name__ == "__main__":
    unittest.main()
