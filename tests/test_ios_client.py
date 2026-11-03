import plistlib
import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
IOS = ROOT / "ios"


class IOSClientContractTest(unittest.TestCase):
    def test_connection_uri_parser_matches_shared_security_boundary(self):
        source = (IOS / "Sources/PdaLinkCore/PdaLinkConnectionURI.swift").read_text()
        tests = (IOS / "Tests/PdaLinkCoreTests/PdaLinkConnectionURITests.swift").read_text()

        self.assertIn('"pdalink://connect?"', source)
        self.assertIn("duplicateParameter", source)
        self.assertIn("unknownParameter", source)
        self.assertIn("PrivateIPv4.parse", source)
        self.assertIn("components.percentEncodedPath.isEmpty", source)
        self.assertIn("password.utf8.allSatisfy", source)
        self.assertIn("testRejectsPublicAndAmbiguousProxyAddresses", tests)
        self.assertIn("testRejectsMalformedEncodingAndNonASCIIPassword", tests)

    def test_wifi_join_precedes_exact_proxy_probe_and_vpn_install(self):
        source = (IOS / "PdaLinkClient/App/TunnelController.swift").read_text()
        view_model = (IOS / "PdaLinkClient/App/ConnectionViewModel.swift").read_text()

        join_index = source.index("try await joinWiFi(connection)")
        probe_index = source.index("try await ProxyProbe.verify")
        save_index = source.index("try await save(manager)")
        start_index = source.index("startVPNTunnel()")
        self.assertLess(join_index, probe_index)
        self.assertLess(probe_index, save_index)
        self.assertLess(save_index, start_index)
        self.assertIn("configuration.joinOnce = false", source)
        self.assertIn("removeConfiguration(forSSID:", source)
        self.assertGreaterEqual(source.count("Task.checkCancellation()"), 3)
        self.assertIn("connectionGeneration", view_model)
        self.assertIn("connectionTask == nil", view_model)
        self.assertIn("pendingConnection?.cancel()", view_model)
        self.assertIn("await pendingConnection?.value", view_model)
        self.assertLess(
            view_model.index("await tunnelController.disconnect()"),
            view_model.index("await pendingConnection?.value"),
        )
        self.assertIn("withTaskCancellationHandler", source)
        self.assertIn("WiFiJoinAttempt", source)

        provider_block = source[
            source.index("tunnelProtocol.providerConfiguration = ["):
            source.index("manager.localizedDescription")
        ]
        self.assertIn('"proxyHost"', provider_block)
        self.assertIn('"proxyPort"', provider_block)
        self.assertIn('"ssid"', provider_block)
        self.assertNotIn("password", provider_block.lower())

    def test_packet_tunnel_uses_public_flow_bridge_and_proxy_host_route(self):
        provider = (IOS / "PdaLinkClient/PacketTunnel/PacketTunnelProvider.swift").read_text()
        bridge = (IOS / "PdaLinkClient/PacketTunnel/PacketFlowBridge.swift").read_text()

        self.assertIn("NEIPv4Route.default()", provider)
        self.assertIn('subnetMask: "255.255.255.255"', provider)
        self.assertIn("tun2proxy_run_with_cli_args", provider)
        self.assertIn(' --close-fd-on-drop false', provider)
        self.assertIn(' --dns virtual', provider)
        self.assertLess(provider.index("tun2proxy_stop()"), provider.index("engineQueue.async {\n            completionHandler()"))
        self.assertIn("generation &+= 1", provider)
        self.assertIn("guard self.isActive(startGeneration)", provider)
        self.assertNotIn("stopping = false\n        }\n        let bridge", provider)
        self.assertIn("socketpair", bridge)
        self.assertIn("packetFlow.readPackets", bridge)
        self.assertIn("packetFlow.writePackets", bridge)
        self.assertIn("DispatchSource.makeWriteSource", bridge)
        self.assertIn("pendingOutboundFrames", bridge)
        self.assertNotIn("usleep", bridge)
        self.assertNotIn("value(forKeyPath", provider + bridge)

    def test_proxy_probe_is_bounded_and_pins_the_pac_endpoint(self):
        source = (IOS / "PdaLinkClient/App/ProxyProbe.swift").read_text()
        validator = (IOS / "Sources/PdaLinkCore/PACResponseValidator.swift").read_text()
        validator_tests = (IOS / "Tests/PdaLinkCoreTests/PACResponseValidatorTests.swift").read_text()

        self.assertIn("response.count > 16_384", source)
        self.assertIn("withTaskCancellationHandler", source)
        self.assertIn("PACResponseValidator.validate", source)
        self.assertIn("DispatchQueue(label:", source)
        self.assertIn('statusParts[0] == "HTTP/1.0"', validator)
        self.assertIn("guard streamEnded else { return false }", validator)
        self.assertIn('pair.0 == "PROXY" && pair.1 == expectedEndpoint', validator)
        self.assertIn("testRejectsPortPrefixAndInvalidHTTPVersion", validator_tests)

    def test_project_has_hotspot_and_packet_tunnel_entitlements(self):
        with (IOS / "PdaLinkClient/Resources/PdaLinkClient.entitlements").open("rb") as handle:
            app_entitlements = plistlib.load(handle)
        with (IOS / "PdaLinkClient/Resources/PacketTunnel.entitlements").open("rb") as handle:
            tunnel_entitlements = plistlib.load(handle)
        with (IOS / "PdaLinkClient/Resources/PacketTunnel-Info.plist").open("rb") as handle:
            tunnel_info = plistlib.load(handle)

        self.assertTrue(app_entitlements["com.apple.developer.networking.HotspotConfiguration"])
        self.assertIn(
            "packet-tunnel-provider",
            app_entitlements["com.apple.developer.networking.networkextension"],
        )
        self.assertIn(
            "packet-tunnel-provider",
            tunnel_entitlements["com.apple.developer.networking.networkextension"],
        )
        self.assertEqual(
            tunnel_info["NSExtension"]["NSExtensionPointIdentifier"],
            "com.apple.networkextension.packet-tunnel",
        )

    def test_tun2proxy_source_and_ios_patch_are_hash_and_commit_pinned(self):
        script = (ROOT / "tools/build_ios.sh").read_text()
        project = (IOS / "project.yml").read_text()
        patch = (IOS / "patches/tun2proxy-0.8.2-ios-no-force-exit.patch").read_text()

        self.assertIn('TUN2PROXY_VERSION="0.8.2"', script)
        self.assertIn("20c06c80ad09d3c357fc3b3810c785ad09ff4ee04ed402f4aa60dcf6c936e43a", script)
        self.assertIn("eed123fbbec06295bf83f9be36d5a0f64ed9a8cb", script)
        self.assertIn("40e3cd39ca4cd589fb036e57a89bf2ff5e888d340bf7dd274d17eb4b7c4eea53", script)
        self.assertIn("sha256_file", script)
        self.assertIn("cargo build --locked --release --target aarch64-apple-ios", script)
        self.assertIn('target_os = "ios"', patch)
        self.assertIn("std::process::exit(-1)", patch)
        self.assertIn("Vendor/tun2proxy.xcframework", project)
        self.assertIn("PdaLinkPacketTunnel", project)
        self.assertIn("minimumXcodeGenVersion: 2.42.0", project)
        self.assertIn("CODE_SIGN_ENTITLEMENTS: PdaLinkClient/Resources/PdaLinkClient.entitlements", project)
        self.assertIn("CODE_SIGN_ENTITLEMENTS: PdaLinkClient/Resources/PacketTunnel.entitlements", project)

    def test_build_script_has_valid_bash_syntax(self):
        subprocess.run(
            ["bash", "-n", str(ROOT / "tools/build_ios.sh")],
            check=True,
            cwd=ROOT,
        )


if __name__ == "__main__":
    unittest.main()
