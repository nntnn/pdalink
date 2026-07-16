import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
JAVA = ROOT / "android/app/src/main/java/dev/pdalink/prototype"


class AndroidWifiBandContractTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.activity = (JAVA / "MainActivity.java").read_text(encoding="utf-8")
        cls.actions = (JAVA / "ProxyActions.java").read_text(encoding="utf-8")
        cls.band = (JAVA / "WifiDirectBand.java").read_text(encoding="utf-8")
        cls.service = (JAVA / "ProxyService.java").read_text(encoding="utf-8")

    def test_activity_exposes_all_band_choices_and_sends_selection(self):
        self.assertIn('addWifiBandOption("Automatic"', self.activity)
        self.assertIn('addWifiBandOption("2.4 GHz"', self.activity)
        self.assertIn('addWifiBandOption("5 GHz"', self.activity)
        self.assertIn("intent.putExtra(ProxyActions.EXTRA_WIFI_BAND, selectedWifiBand)", self.activity)
        self.assertIn("WifiDirectBand.supportsSelection()", self.activity)
        self.assertNotIn("selectWifiBandInUi", self.activity)

    def test_service_builds_configured_group_without_silent_band_fallback(self):
        self.assertIn("new WifiP2pConfig.Builder()", self.service)
        self.assertIn(".setNetworkName(credentials[0])", self.service)
        self.assertIn(".setPassphrase(credentials[1])", self.service)
        self.assertIn(".setGroupOperatingBand(WifiDirectBand.toPlatformBand(requestedWifiBand))", self.service)
        self.assertIn(".enablePersistentMode(false)", self.service)
        failure_block = self.service[
            self.service.index("public void onFailure(int reason)")
            :self.service.index("private WifiP2pConfig buildWifiP2pConfig()")
        ]
        self.assertNotIn("WifiDirectBand.AUTO", failure_block)

    def test_service_preserves_credentials_and_recreates_only_for_mismatch(self):
        self.assertIn("rememberGroupCredentials(group)", self.service)
        self.assertIn("loadOrCreateGroupCredentials()", self.service)
        self.assertIn("PREF_WIFI_DIRECT_SSID", self.service)
        self.assertIn("PREF_WIFI_DIRECT_PASSPHRASE", self.service)
        self.assertIn("WifiDirectBand.matchesFrequency(requestedWifiBand, frequencyMhz)", self.service)
        self.assertIn("recreateGroupForRequestedBand(frequencyMhz)", self.service)

    def test_requested_and_actual_band_are_reported(self):
        self.assertIn("EXTRA_WIFI_BAND", self.actions)
        self.assertIn("EXTRA_WIFI_FREQUENCY_MHZ", self.actions)
        self.assertIn("group.getFrequency()", self.service)
        self.assertIn("Wi-Fi Direct band: requested", self.activity)
        self.assertIn("WifiDirectBand.frequencyLabel(frequencyMhz)", self.activity)

    def test_pre_android_10_falls_back_to_automatic_only(self):
        self.assertIn("Build.VERSION.SDK_INT >= 29", self.band)
        self.assertIn("return supportsSelection() ? normalized : AUTO", self.band)
        self.assertIn("supports Automatic band selection only", self.activity)


if __name__ == "__main__":
    unittest.main()
