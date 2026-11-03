import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "linux"))

import pdalink_dbus


class LinuxClientDbusTest(unittest.TestCase):
    def test_public_state_redacts_device_and_hotspot_secrets(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "watch.json"
            path.write_text(json.dumps({
                "mode": "ready",
                "device_serial": "secret-device-serial",
                "hotspot_credential_fingerprint": "a" * 64,
                "hotspot_relay_fingerprint": "b" * 64,
                "password": "secret-password",
                "proxy_url": "http://127.0.0.1:18080",
                "health": {"device": "ready", "system": "ready"},
            }), encoding="utf-8")

            public = pdalink_dbus.load_public_state(path)

        self.assertEqual(public["mode"], "ready")
        self.assertEqual(public["health"]["system"], "ready")
        self.assertNotIn("device_serial", public)
        self.assertNotIn("hotspot_credential_fingerprint", public)
        self.assertNotIn("hotspot_relay_fingerprint", public)
        self.assertNotIn("password", public)

    def test_missing_state_is_a_safe_starting_payload(self):
        payload = pdalink_dbus.load_public_state(Path("/does/not/exist/pdalink.json"))

        self.assertEqual(payload["mode"], "starting")
        self.assertEqual(payload["health"], {})

    def test_control_surface_exposes_expected_methods(self):
        for method in (
            "GetState",
            "GetDiagnostics",
            "ConnectUsb",
            "ConnectHotspot",
            "Disconnect",
            "Retry",
            "SetAutoConnect",
        ):
            self.assertIn(f'<method name="{method}"', pdalink_dbus.INTROSPECTION_XML)


@unittest.skipUnless(importlib.util.find_spec("gi"), "PyGObject is not installed")
class LinuxClientViewModelTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        global pdalink_client
        import pdalink_client

    def test_ready_state_is_connected(self):
        view = pdalink_client.view_model_from_state({
            "mode": "ready",
            "health": {"device": "ready", "system": "ready"},
        })

        self.assertEqual(view.title, "Connected")
        self.assertEqual(view.action, "Disconnect")
        self.assertTrue(view.auto_connect)

    def test_disabled_state_keeps_supervisor_available(self):
        view = pdalink_client.view_model_from_state({
            "mode": "auto-connect-off",
            "last_error": "auto_connect is off",
        })

        self.assertEqual(view.title, "Disconnected")
        self.assertEqual(view.action, "Connect USB")
        self.assertFalse(view.auto_connect)

    def test_error_state_preserves_diagnostics(self):
        view = pdalink_client.view_model_from_state({
            "mode": "service-start-failed",
            "last_error": "route is missing",
            "last_transition_at": "2026-07-22T12:00:00+00:00",
            "health": {"system": "error"},
        })

        self.assertEqual(view.title, "Connection needs attention")
        self.assertEqual(view.error, "route is missing")
        self.assertEqual(view.health["system"], "error")
        self.assertEqual(view.transition_at, "2026-07-22T12:00:00+00:00")


if __name__ == "__main__":
    unittest.main()
