package dev.pdalink.client;

import org.junit.Test;
import java.io.IOException;
import java.io.StringReader;
import static org.junit.Assert.*;

public class ProxyPacPolicyTest {
    private final String pac = "function FindProxyForURL(url, host) {\n"
            + "  if (host == \"192.168.49.1\") return \"DIRECT\";\n"
            + "  return \"PROXY 192.168.49.1:8001; DIRECT\";\n}";

    @Test public void acceptsHostPacAndWindowsNewlines() {
        assertTrue(ProxyPacPolicy.matches(pac, "192.168.49.1", 8001));
        assertTrue(ProxyPacPolicy.matches(pac.replace("\n", "\r\n") + "\r\n", "192.168.49.1", 8001));
    }
    @Test public void readsCompletePac() throws IOException {
        assertEquals(pac, ProxyPacPolicy.read(new StringReader(pac)));
    }
    @Test(expected = IOException.class) public void rejectsOversizeInsteadOfTrustingTruncatedPrefix() throws IOException {
        ProxyPacPolicy.read(new StringReader(pac + "\n".repeat(4096) + "evil();"));
    }
    @Test public void rejectsPortPrefixAndEmbeddedOrModifiedPac() {
        assertFalse(ProxyPacPolicy.matches(pac.replace(":8001;", ":80010;"), "192.168.49.1", 8001));
        assertFalse(ProxyPacPolicy.matches("// " + pac, "192.168.49.1", 8001));
        assertFalse(ProxyPacPolicy.matches(pac + "\nevil();", "192.168.49.1", 8001));
        assertFalse(ProxyPacPolicy.matches(pac, "192.168.49.2", 8001));
        assertFalse(ProxyPacPolicy.matches(null, "192.168.49.1", 8001));
    }
}
