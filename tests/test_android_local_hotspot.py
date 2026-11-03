import pathlib
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
JAVA = ROOT / "android/app/src/main/java/dev/pdalink/prototype"


class AndroidLocalHotspotContractTests(unittest.TestCase):
    def test_service_owns_and_closes_local_hotspot_reservation(self):
        source = (JAVA / "ProxyService.java").read_text(encoding="utf-8")

        self.assertIn("WifiManager.LocalOnlyHotspotReservation localHotspotReservation", source)
        self.assertIn("startLocalOnlyHotspot", source)
        self.assertIn("reservation.close()", source)
        self.assertIn("closeLocalHotspotReservation();", source)

    def test_wifi_transports_are_switched_exclusively(self):
        source = (JAVA / "ProxyService.java").read_text(encoding="utf-8")

        direct_branch = source.index("ACTION_START_WIFI_DIRECT.equals(action)")
        local_branch = source.index("ACTION_START_LOCAL_HOTSPOT.equals(action)")
        self.assertIn("stopLocalHotspot();", source[direct_branch : direct_branch + 1400])
        self.assertIn("prepareLocalHotspot();", source[local_branch : local_branch + 1200])
        self.assertIn("removeOwnedWifiGroupForLocalHotspot();", source)
        self.assertIn("setWifiDirectEnabled(false);", source)

    def test_local_hotspot_proxy_uses_discovered_interface_address(self):
        source = (JAVA / "ProxyService.java").read_text(encoding="utf-8")

        self.assertIn("LocalHotspotAddressSelector.select", source)
        self.assertIn("wifiProxyHost = selected.address", source)
        self.assertIn("wifiClientPrefix = LocalHotspotAddressSelector.clientPrefix", source)

    def test_service_is_exported_only_to_adb_shell(self):
        manifest = (ROOT / "android/app/src/main/AndroidManifest.xml").read_text(encoding="utf-8")
        service = manifest[manifest.index('<service\n            android:name=".ProxyService"') :]

        self.assertIn('android:exported="true"', service[:280])
        self.assertIn('android:permission="android.permission.DUMP"', service[:280])

    def test_ui_exposes_local_hotspot_start_and_generic_wifi_stop(self):
        source = (JAVA / "MainActivity.java").read_text(encoding="utf-8")
        layout = (ROOT / "android/app/src/main/res/layout/activity_main.xml").read_text(encoding="utf-8")

        self.assertIn('android:id="@+id/wifi_mode_local_hotspot"', layout)
        self.assertIn("startLocalHotspot();", source)
        self.assertIn("ProxyActions.ACTION_START_LOCAL_HOTSPOT", source)
        self.assertIn("ProxyActions.ACTION_STOP_WIFI", source)
        self.assertIn("state.wifiClientSummary()", source)

    def test_ui_exposes_android_selected_hotspot_security(self):
        service = (JAVA / "ProxyService.java").read_text(encoding="utf-8")
        activity = (JAVA / "MainActivity.java").read_text(encoding="utf-8")

        self.assertIn("configuration.getSecurityType()", service)
        self.assertIn("SECURITY_TYPE_WPA3_SAE_TRANSITION", service)
        self.assertIn("ProxyActions.EXTRA_WIFI_SECURITY", service)
        self.assertIn("R.string.detail_wifi_security", activity)

    def test_settings_explain_android_15_credential_rotation_limit(self):
        layout = (ROOT / "android/app/src/main/res/layout/activity_main.xml").read_text(
            encoding="utf-8"
        )
        strings = (ROOT / "android/app/src/main/res/values/strings.xml").read_text(
            encoding="utf-8"
        )

        self.assertIn('android:id="@+id/local_hotspot_credential_policy"', layout)
        self.assertIn("Android-managed on Android 15", strings)
        self.assertIn("may rotate", strings)
        self.assertIn("Choose Wi-Fi Direct", strings)

    def test_ui_refreshes_current_transport_snapshot_when_opened(self):
        activity = (JAVA / "MainActivity.java").read_text(encoding="utf-8")
        on_start = activity[activity.index("protected void onStart()") : activity.index("protected void onStop()")]

        self.assertIn("registerReceiver(statusReceiver", on_start)
        self.assertIn("sendServiceAction(ProxyActions.ACTION_REFRESH);", on_start)
        self.assertLess(
            on_start.index("registerReceiver(statusReceiver"),
            on_start.index("sendServiceAction(ProxyActions.ACTION_REFRESH);"),
        )

    def test_adb_only_receiver_exposes_in_memory_hotspot_snapshot(self):
        manifest = (ROOT / "android/app/src/main/AndroidManifest.xml").read_text(encoding="utf-8")
        service = (JAVA / "ProxyService.java").read_text(encoding="utf-8")
        receiver = (JAVA / "DebugControlReceiver.java").read_text(encoding="utf-8")
        state = (JAVA / "LocalHotspotDebugState.java").read_text(encoding="utf-8")

        receiver_manifest = manifest[manifest.index('<receiver\n            android:name=".DebugControlReceiver"') :]
        self.assertIn('android:exported="true"', receiver_manifest[:300])
        self.assertIn('android:permission="android.permission.DUMP"', receiver_manifest[:300])
        self.assertIn("ACTION_QUERY_LOCAL_HOTSPOT", receiver)
        self.assertIn("LocalHotspotDebugState.encodedSnapshot()", receiver)
        self.assertIn("Base64.URL_SAFE", state)
        self.assertIn("LocalHotspotDebugState.publish(", service)
        self.assertIn("LocalHotspotDebugState.clear();", service)
        self.assertNotIn("SharedPreferences", state)

    def test_local_hotspot_is_recreated_only_after_an_explicit_start_request(self):
        source = (JAVA / "ProxyService.java").read_text(encoding="utf-8")
        start_branch = source[
            source.index("ACTION_START_LOCAL_HOTSPOT.equals(action)") :
            source.index("ACTION_STOP.equals(action)")
        ]
        on_create = source[source.index("public void onCreate()") : source.index("public int onStartCommand")]

        self.assertIn("localHotspotStartAuthorized = true;", start_branch)
        self.assertIn("localHotspotStartAuthorized", source[source.index("private void prepareLocalHotspot()") :])
        self.assertIn("localHotspotEnabled = false;", on_create)
        self.assertNotIn("loadLocalHotspotEnabled()", source)

    def test_stopping_watcher_usb_does_not_close_local_hotspot(self):
        source = (JAVA / "ProxyService.java").read_text(encoding="utf-8")
        stop_usb = source[
            source.index("ACTION_STOP_USB.equals(action)") :
            source.index("ACTION_STOP.equals(action)")
        ]

        self.assertIn("closeUsbProxyServer();", stop_usb)
        self.assertNotIn("stopLocalHotspot();", stop_usb)
        self.assertNotIn("closeLocalHotspotReservation();", stop_usb)


if __name__ == "__main__":
    unittest.main()
