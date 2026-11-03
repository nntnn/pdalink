import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class AndroidClientContractTest(unittest.TestCase):
    def test_host_qr_is_explicit_and_screen_capture_protected(self):
        activity = (ROOT / "android/app/src/main/java/dev/pdalink/prototype/MainActivity.java").read_text()
        layout = (ROOT / "android/app/src/main/res/layout/activity_main.xml").read_text()

        self.assertIn('android:id="@+id/show_connection_qr_button"', layout)
        self.assertIn("PdaLinkConnectionUri.create", activity)
        self.assertIn("new QRCodeWriter().encode", activity)
        self.assertIn("WindowManager.LayoutParams.FLAG_SECURE", activity)

    def test_client_owns_wifi_request_and_vpn_in_isolated_process(self):
        manifest = (ROOT / "android/client/src/main/AndroidManifest.xml").read_text()
        service = (ROOT / "android/client/src/main/java/dev/pdalink/client/PdaLinkVpnService.java").read_text()

        self.assertIn('android:permission="android.permission.BIND_VPN_SERVICE"', manifest)
        self.assertIn('android:process=":vpn"', manifest)
        self.assertIn("WifiNetworkSpecifier.Builder", service)
        self.assertIn("removeCapability(NetworkCapabilities.NET_CAPABILITY_INTERNET)", service)
        self.assertIn("connectivityManager.bindProcessToNetwork(network)", service)
        self.assertIn("builder.addDisallowedApplication(getPackageName())", service)
        self.assertIn("network.openConnection(pacUrl)", service)
        self.assertLess(service.index("probeProxy(network)"), service.index("establishTunnel()"))
        self.assertIn("Tun2proxy.run", service)
        self.assertIn(' --proxy " + connection.proxyUrl', service)

    def test_native_dependency_is_version_and_hash_pinned(self):
        bootstrap = (ROOT / "tools/bootstrap_android_tools.sh").read_text()
        client_gradle = (ROOT / "android/client/build.gradle").read_text()

        self.assertIn("tun2proxy/releases/download/v0.8.2", bootstrap)
        self.assertIn("903f8b780743df5a22ba44e50e1b02cbb0a190ac252ffb03c84d75ef9c0754a9", bootstrap)
        self.assertIn("tun2proxy-0.8.2", client_gradle)

    def test_connection_uri_rejects_public_proxy_and_duplicate_fields(self):
        source = (ROOT / "android/shared/src/main/java/dev/pdalink/shared/PdaLinkConnectionUri.java").read_text()

        self.assertIn("Duplicate connection URI parameter", source)
        self.assertIn("isPrivateIpv4(host)", source)
        self.assertIn('"pdalink://connect"', source)


if __name__ == "__main__":
    unittest.main()
