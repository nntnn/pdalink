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
        self.assertIn("state.wifiClientSummary()", self.activity)
        self.assertIn("R.string.detail_wifi_clients", self.activity)
        self.assertIn("R.string.detail_proxy_connections", self.activity)

    def test_busy_recovery_does_not_remove_unknown_group(self):
        recovery = self.service[
            self.service.index("private void recoverBusyP2pGroup()")
            :self.service.index("private void retryCreateGroupOnce()")
        ]
        self.assertIn("retryBusyGroupWithoutRemoval", recovery)
        self.assertNotIn("removeGroup(", recovery)

    def test_normal_start_uses_an_autonomous_group_even_with_a_remembered_peer(self):
        start = self.service[
            self.service.index("if (ProxyActions.ACTION_START_WIFI_DIRECT.equals(action)) {")
            :self.service.index(
                "if (ProxyActions.ACTION_INVITE_WIFI_DIRECT_PEER.equals(action)) {"
            )
        ]
        self.assertIn("wifiProxyHost = WIFI_DIRECT_HOST", start)
        self.assertIn("wifiClientPrefix = WIFI_DIRECT_CLIENT_PREFIX", start)
        self.assertIn("wifiNativePeerReconnectEnabled = false", start)
        self.assertIn('pendingWifiDirectPeer = ""', start)
        self.assertIn("cancelWifiDirectClientDropRecovery()", start)
        self.assertIn("createGroup()", start)

        selection = self.service[
            self.service.index(
                "private void reconnectRememberedNativePeerOrCreateGroup"
            )
            :self.service.index("private void createGroupInternal")
        ]
        self.assertIn("if (!wifiNativePeerReconnectEnabled)", selection)
        self.assertLess(
            selection.index("createGroupInternal(retryAfterBusy)"),
            selection.index("loadRememberedNativePeerAddress()"),
        )

    def test_native_peer_reconnect_requires_an_explicit_invite_session(self):
        invite = self.service[
            self.service.index("private void inviteWifiDirectPeer")
            :self.service.index("private void removeOwnedGroupForNativePeerNegotiation")
        ]
        self.assertIn("wifiNativePeerReconnectEnabled = true", invite)

        stop = self.service[
            self.service.index("private void stopWifiDirect()")
            :self.service.index("private void prepareLocalHotspot()")
        ]
        self.assertIn("wifiNativePeerReconnectEnabled = false", stop)

    def test_client_drop_recovery_waits_before_recreating_an_owned_group(self):
        self.assertIn(
            "WIFI_DIRECT_CLIENT_DROP_RECOVERY_DELAY_MS = 45000L",
            self.service,
        )

        acceptance = self.service[
            self.service.index("private void acceptOwnedWifiGroup")
            :self.service.index("private String groupClientAddress")
        ]
        self.assertIn(
            "else if (WifiDirectClientRecovery.shouldSchedule",
            acceptance,
        )

        recovery = self.service[
            self.service.index("private void scheduleWifiDirectClientDropRecovery")
            :self.service.index("private void cancelWifiDirectClientDropRecovery")
        ]
        self.assertIn("WIFI_DIRECT_CLIENT_DROP_RECOVERY_DELAY_MS", recovery)
        self.assertNotIn("!wifiNativePeerReconnectEnabled", recovery)


if __name__ == "__main__":
    unittest.main()
