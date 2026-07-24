package dev.zrimpact.remote;

import org.junit.Test;

import java.util.Random;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertFalse;
import static org.junit.Assert.assertTrue;

public class ImpactDisplayStateTest {
    @Test
    public void readyMatchesTheSixteenCharacterDisplay() {
        ImpactDisplayState state = ImpactDisplayState.ready();

        assertEquals("n=  0      READY", state.line1());
        assertEquals("x=  0.00 J______", state.line2());
        assertEquals(16, state.line1().length());
        assertEquals(16, state.line2().length());
    }

    @Test
    public void lowUsesAsymmetricReferenceOffsetsAndIncrementsCount() {
        ImpactDisplayState state = ImpactDisplayState.ready();
        Random random = new Random(12345L);
        double reference = 22.5;

        for (int index = 1; index <= 100; index++) {
            state = state.nextLow(random, reference);
            assertEquals(index, state.count());
            assertTrue(state.value() >= reference - 1.00);
            assertTrue(state.value() <= reference + 1.10);
        }
    }

    @Test
    public void highStaysWithinFourPercent() {
        ImpactDisplayState state = ImpactDisplayState.ready();
        Random random = new Random(12345L);

        state = state.nextHigh(random);
        assertEquals(1, state.count());
        assertTrue(state.value() >= 113.6 * 0.96);
        assertTrue(state.value() <= 113.6 * 1.04);
    }

    @Test
    public void referenceRangeKeepsTheFormattedValueAtSixCharacters() {
        assertTrue(ImpactDisplayState.isValidLowReference(1.00));
        assertTrue(ImpactDisplayState.isValidLowReference(998.89));
        assertFalse(ImpactDisplayState.isValidLowReference(0.99));
        assertFalse(ImpactDisplayState.isValidLowReference(998.90));
        assertFalse(ImpactDisplayState.isValidLowReference(Double.NaN));
        assertEquals(
                16,
                ImpactDisplayState.ready()
                        .nextLow(new Random(7L), 998.89)
                        .line2()
                        .length());
    }
}
