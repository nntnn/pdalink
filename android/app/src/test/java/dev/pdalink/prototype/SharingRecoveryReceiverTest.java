package dev.pdalink.prototype;

import org.junit.Test;
import static org.junit.Assert.*;

public class SharingRecoveryReceiverTest {
    @Test public void restoresOnlyEnabledSharingOnSystemEvents() {
        assertTrue(SharingRecoveryReceiver.shouldRestore("android.intent.action.BOOT_COMPLETED", true));
        assertTrue(SharingRecoveryReceiver.shouldRestore("android.intent.action.MY_PACKAGE_REPLACED", true));
        assertFalse(SharingRecoveryReceiver.shouldRestore("android.intent.action.BOOT_COMPLETED", false));
        assertFalse(SharingRecoveryReceiver.shouldRestore(null, true));
        assertFalse(SharingRecoveryReceiver.shouldRestore("other", true));
    }
}
