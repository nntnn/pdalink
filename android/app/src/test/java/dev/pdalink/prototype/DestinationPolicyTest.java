package dev.pdalink.prototype;

import static org.junit.Assert.assertFalse;
import static org.junit.Assert.assertTrue;

import java.io.IOException;
import java.net.ConnectException;
import java.net.InetAddress;
import java.net.SocketTimeoutException;
import java.net.UnknownHostException;

import org.junit.Test;

public class DestinationPolicyTest {
    @Test
    public void refusesPrivateLoopbackLinkLocalAndMulticastIpv4Literals() {
        String[] blocked = {
                "10.0.0.1", "172.16.0.1", "172.31.255.255", "192.168.50.17",
                "127.0.0.1", "169.254.10.1", "0.0.0.0", "224.0.0.251", "255.255.255.255",
        };
        for (String host : blocked) {
            assertTrue(host, DestinationPolicy.isLocalOnlyLiteral(host));
        }
    }

    @Test
    public void allowsPublicAddressesAndHostnames() {
        String[] allowed = {
                "1.1.1.1", "8.8.8.8", "172.32.0.1", "100.101.197.100",
                "example.com", "10.example.com", "192.168.1.1.nip.io", "", "1.2.3",
        };
        for (String host : allowed) {
            assertFalse(host, DestinationPolicy.isLocalOnlyLiteral(host));
        }
    }

    @Test
    public void classifiesIpv6Literals() {
        assertTrue(DestinationPolicy.isLocalOnlyLiteral("::1"));
        assertTrue(DestinationPolicy.isLocalOnlyLiteral("[fe80::1]"));
        assertTrue(DestinationPolicy.isLocalOnlyLiteral("fd7a:115c:a1e0::1"));
        assertTrue(DestinationPolicy.isLocalOnlyLiteral("::ffff:192.168.0.1"));
        assertFalse(DestinationPolicy.isLocalOnlyLiteral("2606:4700:4700::1111"));
    }

    @Test
    public void classifiesResolvedAddresses() throws UnknownHostException {
        assertTrue(DestinationPolicy.isLocalOnly(InetAddress.getByName("192.168.0.10")));
        assertFalse(DestinationPolicy.isLocalOnly(InetAddress.getByName("1.1.1.1")));
    }

    @Test
    public void doesNotRetryDeadTargetsOnTheDefaultNetwork() {
        assertFalse(NetworkSelector.shouldRetryOnDefaultNetwork(new SocketTimeoutException("timeout")));
        assertFalse(NetworkSelector.shouldRetryOnDefaultNetwork(new ConnectException("refused")));
        assertFalse(NetworkSelector.shouldRetryOnDefaultNetwork(
                new DestinationPolicy.BlockedDestinationException("10.0.0.1")));
        assertTrue(NetworkSelector.shouldRetryOnDefaultNetwork(new UnknownHostException("dns")));
        assertTrue(NetworkSelector.shouldRetryOnDefaultNetwork(new IOException("network unreachable")));
    }
}
