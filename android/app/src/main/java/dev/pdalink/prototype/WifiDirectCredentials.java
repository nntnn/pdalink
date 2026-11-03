package dev.pdalink.prototype;

import java.security.SecureRandom;

final class WifiDirectCredentials {
    private WifiDirectCredentials() {}
    static boolean valid(String ssid, String passphrase) {
        return ssid != null && passphrase != null
                && ssid.matches("^DIRECT-PD-PdaLink-[A-Za-z0-9]{6}$")
                && passphrase.matches("^[ -~]{8,63}$");
    }
    static String[] create(SecureRandom random) {
        return new String[]{"DIRECT-PD-PdaLink-" + token(random, 6), token(random, 16)};
    }
    private static String token(SecureRandom random, int length) {
        String alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz23456789";
        StringBuilder token = new StringBuilder(length);
        for (int index = 0; index < length; index++) {
            token.append(alphabet.charAt(random.nextInt(alphabet.length())));
        }
        return token.toString();
    }
}
