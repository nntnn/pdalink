package dev.pdalink.prototype;

import java.net.Inet4Address;
import java.net.Inet6Address;
import java.net.InetAddress;
import java.net.UnknownHostException;

/**
 * PdaLink relays internet traffic only. Destinations on local or private
 * networks can never be reached through the phone's cellular egress, so they
 * are refused immediately instead of holding a socket until connect timeout.
 */
final class DestinationPolicy {
    private DestinationPolicy() {
    }

    static final class BlockedDestinationException extends java.io.IOException {
        BlockedDestinationException(String host) {
            super("Destination " + host + " is on a local network; PdaLink relays internet traffic only");
        }
    }

    /** Checks an IP literal without performing a DNS lookup. Hostnames return false. */
    static boolean isLocalOnlyLiteral(String host) {
        if (host == null || host.isEmpty()) {
            return false;
        }
        String value = host;
        if (value.startsWith("[") && value.endsWith("]")) {
            value = value.substring(1, value.length() - 1);
        }
        byte[] ipv4 = parseIpv4(value);
        if (ipv4 != null) {
            return isLocalOnlyIpv4(ipv4);
        }
        if (value.indexOf(':') >= 0 && isIpv6Literal(value)) {
            try {
                return isLocalOnly(InetAddress.getByName(value));
            } catch (UnknownHostException ignored) {
                return false;
            }
        }
        return false;
    }

    static boolean isLocalOnly(InetAddress address) {
        if (address == null) {
            return false;
        }
        if (address instanceof Inet4Address) {
            return isLocalOnlyIpv4(address.getAddress());
        }
        if (address instanceof Inet6Address) {
            byte[] bytes = address.getAddress();
            if (address.isLoopbackAddress() || address.isAnyLocalAddress()
                    || address.isLinkLocalAddress() || address.isMulticastAddress()) {
                return true;
            }
            // fc00::/7 unique local addresses.
            if ((bytes[0] & 0xFE) == 0xFC) {
                return true;
            }
            // ::ffff:a.b.c.d IPv4-mapped addresses.
            boolean mapped = true;
            for (int i = 0; i < 10; i++) {
                if (bytes[i] != 0) {
                    mapped = false;
                    break;
                }
            }
            if (mapped && bytes[10] == (byte) 0xFF && bytes[11] == (byte) 0xFF) {
                return isLocalOnlyIpv4(new byte[] {bytes[12], bytes[13], bytes[14], bytes[15]});
            }
        }
        return false;
    }

    private static boolean isLocalOnlyIpv4(byte[] b) {
        int first = b[0] & 0xFF;
        int second = b[1] & 0xFF;
        return first == 0
                || first == 10
                || first == 127
                || (first == 169 && second == 254)
                || (first == 172 && second >= 16 && second <= 31)
                || (first == 192 && second == 168)
                || first >= 224;
    }

    private static byte[] parseIpv4(String value) {
        String[] parts = value.split("\\.", -1);
        if (parts.length != 4) {
            return null;
        }
        byte[] out = new byte[4];
        for (int i = 0; i < 4; i++) {
            String part = parts[i];
            if (part.isEmpty() || part.length() > 3) {
                return null;
            }
            int octet = 0;
            for (int j = 0; j < part.length(); j++) {
                char ch = part.charAt(j);
                if (ch < '0' || ch > '9') {
                    return null;
                }
                octet = octet * 10 + (ch - '0');
            }
            if (octet > 255) {
                return null;
            }
            out[i] = (byte) octet;
        }
        return out;
    }

    private static boolean isIpv6Literal(String value) {
        for (int i = 0; i < value.length(); i++) {
            char ch = value.charAt(i);
            boolean ok = (ch >= '0' && ch <= '9') || (ch >= 'a' && ch <= 'f') || (ch >= 'A' && ch <= 'F')
                    || ch == ':' || ch == '.';
            if (!ok) {
                return false;
            }
        }
        return true;
    }
}
