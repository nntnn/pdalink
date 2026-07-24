package dev.zrimpact.remote;

import org.junit.Test;

import java.util.Random;

import static org.junit.Assert.assertEquals;
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
    public void lowAndHighStayWithinFourPercentAndIncrementCount() {
        ImpactDisplayState state = ImpactDisplayState.ready();
        Random random = new Random(12345L);

        for (int index = 1; index <= 100; index++) {
            state = state.nextLow(random);
            assertEquals(index, state.count());
            assertTrue(state.value() >= 18.8 * 0.96);
            assertTrue(state.value() <= 18.8 * 1.04);
        }

        state = state.nextHigh(random);
        assertEquals(101, state.count());
        assertTrue(state.value() >= 113.6 * 0.96);
        assertTrue(state.value() <= 113.6 * 1.04);
    }
}
