package dev.pdalink.client;

import java.io.IOException;
import java.io.Reader;

/** Match the host's entire PAC, including endpoint boundaries; never trust a substring. */
final class ProxyPacPolicy {
    private ProxyPacPolicy() {}

    static String read(Reader reader) throws IOException {
        StringBuilder body = new StringBuilder();
        char[] buffer = new char[512];
        int count;
        while ((count = reader.read(buffer)) != -1) {
            if (body.length() + count > 4096) throw new IOException("PAC body too large");
            body.append(buffer, 0, count);
        }
        return body.toString();
    }

    static boolean matches(String body, String host, int port) {
        String expected = "function FindProxyForURL(url, host) {\n"
                + "  if (host == \"" + host + "\") return \"DIRECT\";\n"
                + "  return \"PROXY " + host + ":" + port + "; DIRECT\";\n}";
        return body != null && expected.equals(body.replace("\r\n", "\n")
                .replace("\r", "\n").replaceAll("\n+$", ""));
    }
}
