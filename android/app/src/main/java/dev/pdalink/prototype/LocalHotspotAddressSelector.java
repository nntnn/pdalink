package dev.pdalink.prototype;

import java.net.Inet4Address;
import java.net.InetAddress;
import java.net.NetworkInterface;
import java.net.SocketException;
import java.util.ArrayList;
import java.util.Collections;
import java.util.Enumeration;
import java.util.HashSet;
import java.util.List;
import java.util.Locale;
import java.util.Set;

final class LocalHotspotAddressSelector {
    static final class InterfaceAddress {
        final String interfaceName;
        final String address;

        InterfaceAddress(String interfaceName, String address) {
            this.interfaceName = interfaceName;
            this.address = address;
        }
    }

    private LocalHotspotAddressSelector() {
    }

    static List<InterfaceAddress> snapshot() throws SocketException {
        List<InterfaceAddress> addresses = new ArrayList<>();
        Enumeration<NetworkInterface> interfaces = NetworkInterface.getNetworkInterfaces();
        if (interfaces == null) {
            return addresses;
        }
        for (NetworkInterface networkInterface : Collections.list(interfaces)) {
            for (InetAddress address : Collections.list(networkInterface.getInetAddresses())) {
                if (address instanceof Inet4Address) {
                    addresses.add(new InterfaceAddress(networkInterface.getName(), address.getHostAddress()));
                }
            }
        }
        return addresses;
    }

    static InterfaceAddress select(
            List<InterfaceAddress> before,
            List<InterfaceAddress> current) {
        Set<String> previousAddresses = new HashSet<>();
        for (InterfaceAddress address : before) {
            previousAddresses.add(address.address);
        }

        List<InterfaceAddress> candidates = new ArrayList<>();
        for (InterfaceAddress address : current) {
            if (!previousAddresses.contains(address.address)
                    && isPrivateIpv4(address.address)
                    && !isExcludedInterface(address.interfaceName)) {
                candidates.add(address);
            }
        }
        InterfaceAddress selected = null;
        int selectedRank = Integer.MAX_VALUE;
        for (InterfaceAddress candidate : candidates) {
            int rank = interfaceRank(candidate);
            if (rank < selectedRank) {
                selected = candidate;
                selectedRank = rank;
            }
        }
        return selected;
    }

    static String clientPrefix(String ipv4) {
        int lastDot = ipv4 == null ? -1 : ipv4.lastIndexOf('.');
        return lastDot <= 0 ? "" : ipv4.substring(0, lastDot + 1);
    }

    private static int interfaceRank(InterfaceAddress address) {
        String name = normalizeName(address.interfaceName);
        if (name.startsWith("swlan") || name.startsWith("ap_br_swlan")) {
            return 0;
        }
        if (name.startsWith("softap") || name.startsWith("ap_br_softap")) {
            return 1;
        }
        if (name.startsWith("wlan") || name.startsWith("ap_br_wlan")) {
            return 2;
        }
        return 3;
    }

    private static boolean isExcludedInterface(String interfaceName) {
        String name = normalizeName(interfaceName);
        return name.startsWith("p2p")
                || name.startsWith("rmnet")
                || name.startsWith("v4-rmnet")
                || name.startsWith("lo")
                || name.startsWith("tun")
                || name.startsWith("dummy");
    }

    private static String normalizeName(String value) {
        return value == null ? "" : value.toLowerCase(Locale.US);
    }

    private static boolean isPrivateIpv4(String value) {
        if (value == null) {
            return false;
        }
        String[] parts = value.split("\\.");
        if (parts.length != 4) {
            return false;
        }
        try {
            int first = Integer.parseInt(parts[0]);
            int second = Integer.parseInt(parts[1]);
            return first == 10
                    || (first == 172 && second >= 16 && second <= 31)
                    || (first == 192 && second == 168);
        } catch (NumberFormatException ex) {
            return false;
        }
    }
}
