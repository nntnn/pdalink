import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class WindowsToolScriptTest(unittest.TestCase):
    def test_windows_usb_internet_has_restore_guardrails(self):
        script = (ROOT / "tools" / "windows_usb_internet.ps1").read_text()

        self.assertIn('[ValidateSet("start", "stop", "status", "test")]', script)
        self.assertIn("Save-ProxyBackup", script)
        self.assertIn("Restore-WindowsProxy", script)
        self.assertIn("tcp:$LocalPort is already forwarded to a different target", script)
        self.assertIn("InternetSetOption", script)
        self.assertIn("system proxy did not resolve to 127.0.0.1:$LocalPort", script)
        self.assertIn("$parts[1] -eq $local", script)
        self.assertIn("$parts[2] -eq $remote", script)
        self.assertIn("leaving current forward in place", script)
        self.assertIn("Convert-RegistryValueForKind", script)
        self.assertIn('"DWord" { return [int]$Value }', script)
        self.assertIn("created_forward", script)
        self.assertIn("ProxyBackupOwned", script)
        self.assertIn("winhttp_managed", script)
        self.assertIn("Assert-WinHttpCanBeManaged", script)
        self.assertIn("Rolling back failed start", script)
        self.assertIn("Leaving pre-existing ADB forward in place", script)

    def test_windows_tcp_relay_targets_explicit_endpoint(self):
        script = (ROOT / "tools" / "windows_tcp_relay.ps1").read_text()

        self.assertIn("WindowsTcpRelay", script)
        self.assertIn("TcpListener", script)
        self.assertIn("TargetAddress", script)
        self.assertIn("TargetPort", script)
        self.assertIn("MaxClients", script)
        self.assertIn("SemaphoreSlim", script)

    def test_windows_usb_smoke_matches_forward_local_column(self):
        script = (ROOT / "tools" / "windows_usb_proxy_smoke.ps1").read_text()

        self.assertIn("Find-ForwardLine", script)
        self.assertIn("$parts[1] -eq $local", script)
        self.assertIn("ExistingLocalForwards", script)

    def test_manual_android_builder_prefers_local_jdk_when_toolchain_incomplete(self):
        script = (ROOT / "tools" / "build_android_manual.sh").read_text()

        self.assertIn("has_suitable_jdk", script)
        self.assertIn("command -v keytool", script)
        self.assertIn("command -v javac", script)
        self.assertIn("java_has_compiler_module", script)
        self.assertIn("jdk.compiler", script)
        self.assertIn("major >= 17", script)
        self.assertIn('if ! has_suitable_jdk && [[ -x "$LOCAL_JDK/bin/java" ]]', script)
        self.assertIn('aapt2" compile --dir', script)
        self.assertIn('RES_FLAT="$OUT_DIR/resources.zip"', script)

    def test_android_bootstrap_downloads_repo_local_jdk_when_needed(self):
        script = (ROOT / "tools" / "bootstrap_android_tools.sh").read_text()

        self.assertIn("ensure_jdk", script)
        self.assertIn("api.adoptium.net/v3/binary/latest/17/ga/linux", script)
        self.assertIn("temurin_arch", script)
        self.assertIn("tar -xzf", script)
        self.assertIn("export PATH=\"$LOCAL_JDK/bin:$PATH\"", script)
        self.assertIn("has_suitable_jdk", script)

    def test_system_service_wrapper_uses_state_log_and_stop_commands(self):
        script = (ROOT / "tools" / "pdalink_system_service.sh").read_text()
        wrapper = (ROOT / "tools" / "pdalink.sh").read_text()

        self.assertIn("pdalink-system.pid", script)
        self.assertIn("pdalink-system.json", script)
        self.assertIn("pdalink-system.log", script)
        self.assertIn("system-start", script)
        self.assertIn("system-status", script)
        self.assertIn("system-stop", script)
        self.assertIn("--state-file", script)
        self.assertIn("PDALINK_SERVICE_START_TIMEOUT", script)
        self.assertIn("collect_config_args", script)
        self.assertIn("cleanup_failed_start", script)
        self.assertIn("ROOT_HELPER", script)
        self.assertIn("sudo -n", script)
        self.assertIn("nohup sudo -n", script)
        self.assertIn("state_pid()", script)
        self.assertIn("state_pid_value", script)
        self.assertIn("system-stop \"${CONFIG_ARGS[@]}\"", script)
        self.assertIn("pdalink_system_service.sh", wrapper)
        self.assertIn("system service {start|status|stop|logs}", wrapper)

    def test_wrapper_exposes_connection_status(self):
        wrapper = (ROOT / "tools" / "pdalink.sh").read_text()

        self.assertIn("connection status [--json]", wrapper)
        self.assertIn("connection-status", wrapper)
        self.assertIn("connection_usage", wrapper)

    def test_wrapper_exposes_wifi_direct_commands(self):
        wrapper = (ROOT / "tools" / "pdalink.sh").read_text()

        self.assertIn(
            "wifi {start-android|stop-android|scan|connect|status|internet|probe|env|curl-test|test|smoke|doctor}",
            wrapper,
        )
        self.assertIn("wifi internet start", wrapper)
        self.assertIn("wifi internet stop", wrapper)
        self.assertIn("wifi_usage", wrapper)
        self.assertIn("wifi_start_android", wrapper)
        self.assertIn("START_WIFI_DIRECT", wrapper)
        self.assertIn("STOP_WIFI_DIRECT", wrapper)
        self.assertIn("wifi-status", wrapper)
        self.assertIn("connect-auto", wrapper)
        self.assertIn("wifi_smoke", wrapper)

    def test_wrapper_masks_passwords_in_command_log(self):
        wrapper = (ROOT / "tools" / "pdalink.sh").read_text()

        self.assertIn("local mask_next=0", wrapper)
        self.assertIn("--password|wifi-sec.psk", wrapper)
        self.assertIn('printf \' %q\' "<redacted>"', wrapper)

    def test_native_linux_watcher_manages_adb_and_service_state(self):
        script = (ROOT / "tools" / "pdalink_watch.py").read_text()
        wrapper = (ROOT / "tools" / "pdalink.sh").read_text()

        self.assertIn("PdaLinkWatcher", script)
        self.assertIn("adb_command", script)
        self.assertIn("forward_check", script)
        self.assertIn("system\", \"service\", command", script)
        self.assertIn("forward_owned", script)
        self.assertIn("service_owned", script)
        self.assertIn("waiting-adb-authorization", script)
        self.assertIn("tcp:{self.config.local_port} is already forwarded", script)
        self.assertIn("watch {run|once|status}", wrapper)
        self.assertIn("pdalink_watch.py", wrapper)
        self.assertIn("exec \"$PYTHON\" \"$ROOT/tools/pdalink_watch.py\"", wrapper)
        self.assertIn("start-foreground-service", script)
        self.assertIn(".ProxyService", script)
        self.assertIn("START_USB", script)

    def test_wrapper_exposes_autostart_and_agent_setup_apply(self):
        wrapper = (ROOT / "tools" / "pdalink.sh").read_text()

        self.assertIn("autostart {enable|disable|status}", wrapper)
        self.assertIn("pdalink-watch.service", wrapper)
        self.assertIn("Restart=always", wrapper)
        self.assertIn("systemctl --user enable --now", wrapper)
        self.assertIn("setup apply", wrapper)
        self.assertIn("--trust-connected", wrapper)
        self.assertIn("config-set auto_connect", wrapper)
        self.assertIn("config-set route_mode", wrapper)

    def test_wrapper_exposes_rescue_shortcuts(self):
        wrapper = (ROOT / "tools" / "pdalink.sh").read_text()
        rescue = (ROOT / "tools" / "pdalink_rescue.sh").read_text()

        self.assertIn("rescue {status|wifi|restart|udev|adb-reset|logs}", wrapper)
        self.assertIn("pdalink_rescue.sh", wrapper)
        self.assertIn("rescue udev", wrapper)
        self.assertIn("resolvectl revert pdalink0", rescue)
        self.assertIn("ip route del 0.0.0.0/1 dev pdalink0", rescue)
        self.assertIn("ip link del dev pdalink0", rescue)
        self.assertIn("--deep", rescue)
        self.assertIn("udev", rescue)
        self.assertIn("adb-reset", rescue)

    def test_android_usb_proxy_uses_fixed_forwarded_port(self):
        service = (ROOT / "android/app/src/main/java/dev/pdalink/prototype/ProxyService.java").read_text()
        manifest = (ROOT / "android/app/src/main/AndroidManifest.xml").read_text()

        self.assertIn("USB_PROXY_CANDIDATE_PORTS = new int[]{8000}", service)
        self.assertIn("WIFI_PROXY_CANDIDATE_PORTS = new int[]{8001}", service)
        self.assertIn("HttpProxyServer usbProxyServer", service)
        self.assertIn("HttpProxyServer wifiProxyServer", service)
        self.assertIn('android:name=".ProxyService"', manifest)
        self.assertIn('android:exported="true"', manifest)
        self.assertIn("USB_HOST = \"127.0.0.1\"", service)

    def test_android_proxy_preserves_transport_state_and_sanitizes_pac_host(self):
        service = (ROOT / "android/app/src/main/java/dev/pdalink/prototype/ProxyService.java").read_text()
        proxy = (ROOT / "android/app/src/main/java/dev/pdalink/prototype/HttpProxyServer.java").read_text()
        activity = (ROOT / "android/app/src/main/java/dev/pdalink/prototype/MainActivity.java").read_text()
        actions = (ROOT / "android/app/src/main/java/dev/pdalink/prototype/ProxyActions.java").read_text()

        self.assertIn("intent == null ? null : intent.getAction()", service)
        self.assertIn("loadSavedMode()", service)
        self.assertIn("saveMode(primaryMode)", service)
        self.assertIn("loadWifiDirectEnabled()", service)
        self.assertIn("savedMode == Mode.WIFI_DIRECT", service)
        self.assertIn("saveMode(Mode.USB)", service)
        self.assertIn("setWifiDirectEnabled(true)", service)
        self.assertIn("ensureUsbProxyServer()", service)
        self.assertIn("ensureWifiProxyServer()", service)
        self.assertIn("clearProxyStartupError(wifiProxyServer)", service)
        self.assertIn("WIFI_HOST,", service)
        self.assertIn("clientCount(usbProxyServer) + clientCount(wifiProxyServer)", service)
        self.assertIn("validPacEndpoint(host)", proxy)
        self.assertIn("jsString(endpoint)", proxy)
        self.assertIn("uri.getRawUserInfo() != null", proxy)
        self.assertIn("EXTRA_TRAFFIC_UP_BYTES", actions)
        self.assertIn("EXTRA_TRAFFIC_DOWN_BYTES", actions)
        self.assertIn("getUpstreamBytes()", service)
        self.assertIn("getDownstreamBytes()", service)
        self.assertIn("Internet usage:", activity)
        self.assertIn("USB proxy:", activity)
        self.assertIn("Wi-Fi Direct proxy:", activity)
        self.assertIn("ACTION_STOP_WIFI_DIRECT", actions)
        self.assertIn("entry + existing", activity)

    def test_android_wifi_stop_preserves_usb_listener(self):
        service = (ROOT / "android/app/src/main/java/dev/pdalink/prototype/ProxyService.java").read_text()
        wrapper = (ROOT / "tools/pdalink.sh").read_text()

        stop_wifi = service[
            service.index("private void stopWifiDirect()")
            :service.index("private void startProductUsbBridge()")
        ]
        self.assertIn("setWifiDirectEnabled(false)", stop_wifi)
        self.assertIn("closeWifiProxyServer()", stop_wifi)
        self.assertNotIn("closeUsbProxyServer()", stop_wifi)

        wrapper_stop = wrapper[
            wrapper.index("wifi_stop_android()")
            :wrapper.index("wifi_connect()")
        ]
        self.assertIn("dev.pdalink.prototype.STOP_WIFI_DIRECT", wrapper_stop)
        self.assertNotIn("-a dev.pdalink.prototype.STOP\n", wrapper_stop)

    def test_android_declares_product_usb_accessory_consent_surface(self):
        manifest = (ROOT / "android/app/src/main/AndroidManifest.xml").read_text()
        activity = (ROOT / "android/app/src/main/java/dev/pdalink/prototype/MainActivity.java").read_text()
        service = (ROOT / "android/app/src/main/java/dev/pdalink/prototype/ProxyService.java").read_text()
        bridge = (ROOT / "android/app/src/main/java/dev/pdalink/prototype/ProductUsbBridge.java").read_text()
        trust = (ROOT / "android/app/src/main/java/dev/pdalink/prototype/ProductUsbHostTrust.java").read_text()
        debug = (ROOT / "android/app/src/main/java/dev/pdalink/prototype/DebugControlActivity.java").read_text()
        actions = (ROOT / "android/app/src/main/java/dev/pdalink/prototype/ProxyActions.java").read_text()
        accessory_filter = (ROOT / "android/app/src/main/res/xml/pdalink_accessory_filter.xml").read_text()
        wrapper = (ROOT / "tools" / "pdalink.sh").read_text()

        self.assertIn("android.hardware.usb.action.USB_ACCESSORY_ATTACHED", manifest)
        self.assertIn("@xml/pdalink_accessory_filter", manifest)
        self.assertIn("UsbManager.ACTION_USB_ACCESSORY_ATTACHED", activity)
        self.assertIn("requestPermission(accessory", activity)
        self.assertIn("sendProductUsbServiceAction(accessory)", activity)
        self.assertIn("Start Product USB", activity)
        self.assertIn("startProductUsbBridge()", service)
        self.assertIn("ProductUsbBridge", service)
        self.assertIn("openAccessory(accessory)", bridge)
        self.assertIn("ProductUsbHostTrust.isTrusted", bridge)
        self.assertIn('"untrusted"', bridge)
        self.assertIn("Product USB host fingerprint missing", bridge)
        self.assertIn("device-ready", bridge)
        self.assertIn("Product USB device-ready sent", bridge)
        self.assertIn("Trust Product USB host?", activity)
        self.assertIn("ProductUsbHostTrust.shouldPrompt", activity)
        self.assertIn("ProductUsbHostTrust.recordPrompt", activity)
        self.assertIn("ProductUsbHostTrust.trust", activity)
        self.assertIn('"unknown".equals(normalized)', trust)
        self.assertIn("PROMPT_COOLDOWN_MS", trust)
        self.assertIn("product_usb_host_trust", trust)
        self.assertIn("Product USB host trusted", bridge)
        self.assertIn("host approval required", service)
        self.assertIn('productUsbStatus = "permission missing"', service)
        self.assertIn("proxy-pac", bridge)
        self.assertIn("ACTION_START_PRODUCT_USB", actions)
        self.assertIn("ACTION_START_PRODUCT_USB.equals(action)", debug)
        self.assertIn("product-usb bridge", wrapper)
        self.assertIn("product-usb-bridge", wrapper)
        self.assertIn('manufacturer="PdaLink"', accessory_filter)

    def test_product_usb_permission_pending_intent_allows_result_extras(self):
        activity = (ROOT / "android/app/src/main/java/dev/pdalink/prototype/MainActivity.java").read_text()
        permission_block = activity[
            activity.index("int permissionFlags")
            :activity.index("usbManager.requestPermission(accessory")
        ]

        self.assertIn("PendingIntent.FLAG_MUTABLE", permission_block)
        self.assertNotIn("PendingIntent.FLAG_IMMUTABLE", permission_block)

    def test_android_connect_tunnel_has_no_lifetime_timeout(self):
        proxy = (ROOT / "android/app/src/main/java/dev/pdalink/prototype/HttpProxyServer.java").read_text()

        self.assertNotIn("CONNECT_TUNNEL_TIMEOUT", proxy)
        self.assertNotIn("CONNECT tunnel timed out", proxy)
        self.assertIn("done.await();", proxy)
        self.assertIn("shutdownOutput(remote)", proxy)
        self.assertIn("shutdownOutput(client)", proxy)
        self.assertIn("activeSockets", proxy)
        self.assertIn("trackSocket(remote)", proxy)
        self.assertIn("closeActiveSockets()", proxy)

    def test_native_linux_installer_guides_clone_to_watch_flow(self):
        script = (ROOT / "tools" / "install_linux.sh").read_text()
        wrapper = (ROOT / "tools" / "pdalink.sh").read_text()
        install_doc = (ROOT / "INSTALL.md").read_text()

        self.assertIn("python3 3.10+", script)
        self.assertIn("bootstrap_android_tools.sh", script)
        self.assertIn("build_android_manual.sh", script)
        self.assertIn("install_linux_privileges.sh", script)
        self.assertIn("devices -l", script)
        self.assertIn('$2 == "device"', script)
        self.assertIn('$2 == "unauthorized"', script)
        self.assertIn("--skip-bootstrap --skip-android-build --skip-privileges", script)
        self.assertIn("./tools/pdalink.sh autostart enable", script)
        self.assertIn("install)", wrapper)
        self.assertIn("setup)", wrapper)
        self.assertIn("install [linux-install options...]", wrapper)
        self.assertIn("./tools/pdalink.sh autostart enable", install_doc)
        self.assertIn("LLM Agent Prompt", install_doc)
        self.assertIn("Do not run system-start directly", install_doc)
        self.assertIn("--skip-udev", install_doc)
        self.assertIn("./tools/pdalink.sh rescue udev", install_doc)
        self.assertIn("no permissions", install_doc)

    def test_linux_privilege_installer_creates_root_owned_helper_and_sudoers(self):
        script = (ROOT / "tools" / "install_linux_privileges.sh").read_text()

        self.assertIn("/opt/pdalink", script)
        self.assertIn("/usr/local/libexec/pdalink", script)
        self.assertIn("/etc/sudoers.d/pdalink", script)
        self.assertIn("pdalink-root", script)
        self.assertIn("visudo -cf", script)
        self.assertIn("NOPASSWD: PDALINK_SYSTEM", script)
        self.assertIn("system-start *", script)
        self.assertIn("system-stop *", script)
        self.assertIn("system-down *", script)

    def test_linux_udev_installer_dry_run_prints_managed_rules_and_recovery_steps(self):
        result = subprocess.run(
            [
                "bash",
                str(ROOT / "tools" / "install_linux_udev.sh"),
                "--dry-run",
                "--no-reload",
            ],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('ATTR{idVendor}=="04e8", ATTR{idProduct}=="6860"', result.stdout)
        self.assertIn('ATTR{idVendor}=="04e8", ATTR{idProduct}=="6864"', result.stdout)
        self.assertIn('ATTR{idVendor}=="18d1", ATTR{idProduct}=="2d01"', result.stdout)
        self.assertIn("udevadm control --reload-rules", result.stdout)
        self.assertIn("./tools/adb.sh kill-server", result.stdout)

    def test_linux_install_wires_udev_installer_with_skip_flag(self):
        script = (ROOT / "tools" / "install_linux.sh").read_text()

        self.assertIn("INSTALL_UDEV=1", script)
        self.assertIn("--skip-udev", script)
        self.assertIn("install_linux_udev.sh", script)
        self.assertIn("sudo \"$ROOT/tools/install_linux_udev.sh\"", script)

    def test_linux_install_dry_run_prints_udev_rules(self):
        with tempfile.NamedTemporaryFile() as apk:
            result = subprocess.run(
                [
                    "bash",
                    str(ROOT / "tools" / "install_linux.sh"),
                    "--dry-run",
                    "--apk",
                    apk.name,
                    "--skip-bootstrap",
                    "--skip-android-build",
                    "--skip-android-install",
                    "--skip-privileges",
                ],
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
            )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('ATTR{idVendor}=="04e8", ATTR{idProduct}=="6864"', result.stdout)
        self.assertIn("DRY-RUN skip udevadm control --reload-rules", result.stdout)

    def test_rescue_udev_dry_run_refreshes_rules_and_adb_status(self):
        result = subprocess.run(
            [
                "bash",
                str(ROOT / "tools" / "pdalink_rescue.sh"),
                "udev",
                "--dry-run",
                "--no-reload",
            ],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("install_linux_udev.sh --dry-run --no-reload", result.stdout)
        self.assertIn("./tools/adb.sh kill-server", result.stdout)
        self.assertIn("./tools/pdalink.sh usb status", result.stdout)


if __name__ == "__main__":
    unittest.main()
