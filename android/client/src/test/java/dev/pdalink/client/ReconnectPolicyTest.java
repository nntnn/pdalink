package dev.pdalink.client;
import org.junit.Test;
import static org.junit.Assert.*;

public class ReconnectPolicyTest {
    @Test public void retriesForeverWithBoundedBackoff() {
        assertEquals(5000L, ReconnectPolicy.delayMillis(0));
        assertEquals(10000L, ReconnectPolicy.delayMillis(1));
        assertEquals(40000L, ReconnectPolicy.delayMillis(3));
        assertEquals(60000L, ReconnectPolicy.delayMillis(4));
        assertEquals(60000L, ReconnectPolicy.delayMillis(Integer.MAX_VALUE));
        assertEquals(5000L, ReconnectPolicy.delayMillis(-1));
    }
}
