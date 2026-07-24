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
    public void approvedNetworkKeepsRequestAliveForRecovery() {
        assertEquals(
                60_000,
                DisplayNetworkPolicy.timeoutMillis(true));
        assertTrue(DisplayNetworkPolicy.shouldRetry(true));
    }

    @Test
    public void reconnectDelayBacksOffCapsAndResetsAfterSuccess() {
        DisplayNetworkPolicy policy = new DisplayNetworkPolicy();

        assertEquals(5_000, policy.nextReconnectDelayMillis());
        assertEquals(10_000, policy.nextReconnectDelayMillis());
        assertEquals(20_000, policy.nextReconnectDelayMillis());
        assertEquals(40_000, policy.nextReconnectDelayMillis());
        assertEquals(60_000, policy.nextReconnectDelayMillis());
        assertEquals(60_000, policy.nextReconnectDelayMillis());

        policy.onConnected();

        assertEquals(5_000, policy.nextReconnectDelayMillis());
    }
}
