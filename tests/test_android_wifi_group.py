import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
JAVA = ROOT / "android/app/src/main/java/dev/pdalink/prototype"


class AndroidWifiGroupContractTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.service = (JAVA / "ProxyService.java").read_text(encoding="utf-8")
        cls.actions = (JAVA / "ProxyActions.java").read_text(encoding="utf-8")
        cls.activity = (JAVA / "MainActivity.java").read_text(encoding="utf-8")

    def test_unowned_group_is_rejected_without_removal(self):
        conflict = self.service[
            self.service.index("private void rejectUnownedWifiGroup()")
            :self.service.index("private String loadLegacyOwnedGroupSsid()")
        ]
        self.assertIn("closeWifiProxyServer()", conflict)
        self.assertIn("wifiGroupOwned = false", conflict)
        self.assertIn("left untouched", conflict)
        self.assertNotIn("removeGroup", conflict)

    def test_only_owned_group_is_removed(self):
        removal = self.service[
            self.service.index("private void removeWifiGroupBestEffort()")
            :self.service.index("private void stopWifiDirect()")
        ]
        self.assertIn("!wifiGroupOwned", removal)
        self.assertIn("wifiP2pManager.removeGroup", removal)

    def test_group_membership_is_distinct_from_proxy_connections(self):
        self.assertIn("group.getClientList()", self.service)
        self.assertIn("EXTRA_WIFI_GROUP_CLIENT_COUNT", self.actions)
        self.assertIn("EXTRA_WIFI_GROUP_OWNED", self.actions)
        self.assertIn('"\\nWi-Fi clients: " + wifiClients', self.activity)
        self.assertIn("wifiClients = Integer.toString(groupClients)", self.activity)
        self.assertIn("Active proxy connections:", self.activity)

    def test_busy_recovery_does_not_remove_unknown_group(self):
        recovery = self.service[
            self.service.index("private void recoverBusyP2pGroup()")
            :self.service.index("private void retryCreateGroupOnce()")
        ]
        self.assertIn("retryBusyGroupWithoutRemoval", recovery)
        self.assertNotIn("removeGroup(", recovery)


if __name__ == "__main__":
    unittest.main()
