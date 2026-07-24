package dev.pdalink.prototype;

import org.junit.Test;

import static org.junit.Assert.assertFalse;
import static org.junit.Assert.assertTrue;

public class ZeroDisplayAvailabilityTest {
    @Test
    public void commandsRemainEnabledWhileClientListRecovers() {
        assertTrue(ZeroDisplayAvailability.commandsEnabled(true, false));
        assertTrue(ZeroDisplayAvailability.isRecovering(true, 0));
    }

    @Test
    public void inactiveGroupAndActiveSendDisableCommands() {
        assertFalse(ZeroDisplayAvailability.commandsEnabled(false, false));
        assertFalse(ZeroDisplayAvailability.commandsEnabled(true, true));
        assertFalse(ZeroDisplayAvailability.isRecovering(false, 0));
        assertFalse(ZeroDisplayAvailability.isRecovering(true, 1));
    }
}
