package dev.pdalink.prototype;

import static org.junit.Assert.assertFalse;
import static org.junit.Assert.assertTrue;

import org.junit.Test;

public class WifiDirectClientRecoveryTest {
    @Test
    public void schedulesWhenTheLastClientUnexpectedlyLeaves() {
        assertTrue(WifiDirectClientRecovery.shouldSchedule(1, 0, true, false));
    }

    @Test
    public void ignoresInitialEmptyGroupsAndActiveOperations() {
        assertFalse(WifiDirectClientRecovery.shouldSchedule(0, 0, true, false));
        assertFalse(WifiDirectClientRecovery.shouldSchedule(1, 0, false, false));
        assertFalse(WifiDirectClientRecovery.shouldSchedule(1, 0, true, true));
    }

    @Test
    public void ignoresGroupsThatStillHaveAClient() {
        assertFalse(WifiDirectClientRecovery.shouldSchedule(1, 1, true, false));
        assertFalse(WifiDirectClientRecovery.shouldSchedule(2, 1, true, false));
    }
}
