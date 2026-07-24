package dev.zrimpact.remote;

import org.junit.Test;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertFalse;
import static org.junit.Assert.assertTrue;

public class DisplayNetworkPolicyTest {
    @Test
    public void firstApprovalGetsTimeWithoutAnAutomaticPromptLoop() {
        assertEquals(
                60_000,
                DisplayNetworkPolicy.timeoutMillis(false));
        assertFalse(DisplayNetworkPolicy.shouldRetry(false));
    }

    @Test
    public void approvedNetworkUsesShortAutomaticRetries() {
        assertEquals(
                10_000,
                DisplayNetworkPolicy.timeoutMillis(true));
        assertTrue(DisplayNetworkPolicy.shouldRetry(true));
    }
}
