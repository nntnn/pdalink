package dev.pdalink.prototype;

import org.junit.Test;
import static org.junit.Assert.*;

public class NotificationGateTest {
    @Test public void dedupesAndCoalescesBurstsAtTwoSeconds() {
        NotificationGate gate = new NotificationGate();
        assertEquals(0, gate.delayMillis("starting", 100));
        gate.didPost("starting", 100);
        assertEquals(-1, gate.delayMillis("starting", 101));
        assertEquals(1999, gate.delayMillis("USB", 101));
        assertEquals(1000, gate.delayMillis("Direct", 1100));
        assertEquals(0, gate.delayMillis("Direct", 2100));
        gate.didPost("Direct", 2100);
        assertEquals(-1, gate.delayMillis("Direct", 10000));
        assertEquals(0, gate.delayMillis("USB", 10000));
    }

    @Test public void returningToPostedContentCancelsPendingChange() {
        NotificationGate gate = new NotificationGate();
        gate.didPost("USB", 0);
        assertEquals(1500, gate.delayMillis("Direct", 500));
        assertEquals(-1, gate.delayMillis("USB", 1500));
    }
}
