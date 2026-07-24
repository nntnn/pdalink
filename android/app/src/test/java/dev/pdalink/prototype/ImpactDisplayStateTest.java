package dev.pdalink.prototype;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertTrue;

import java.util.Random;

import org.junit.Test;

public class ImpactDisplayStateTest {
    @Test
    public void readyMatchesTheSixteenCharacterReferenceScreen() {
        ImpactDisplayState state = ImpactDisplayState.ready();

        assertEquals(0, state.count());
        assertEquals(0.0, state.joules(), 0.0);
        assertEquals("n=  0      READY", state.line1());
        assertEquals("x=  0.00 J______", state.line2());
        assertEquals(16, state.line1().length());
        assertEquals(16, state.line2().length());
    }

    @Test
    public void lowUsesTheFullPlusOrMinusFourPercentRange() {
        ImpactDisplayState minimum =
                ImpactDisplayState.ready().nextLow(new FixedRandom(0.0));
        ImpactDisplayState maximum =
                ImpactDisplayState.ready().nextLow(new FixedRandom(Math.nextDown(1.0)));

        assertEquals(1, minimum.count());
        assertEquals(18.8 * 0.96, minimum.joules(), 0.000_000_1);
        assertTrue(maximum.joules() <= 18.8 * 1.04);
        assertTrue(maximum.joules() > 18.8 * 1.039_999);
        assertEquals("x= 18.05 J______", minimum.line2());
        assertEquals("x= 19.55 J______", maximum.line2());
    }

    @Test
    public void highUsesTheFullPlusOrMinusFourPercentRange() {
        ImpactDisplayState minimum =
                ImpactDisplayState.ready().nextHigh(new FixedRandom(0.0));
        ImpactDisplayState maximum =
                ImpactDisplayState.ready().nextHigh(new FixedRandom(Math.nextDown(1.0)));

        assertEquals(113.6 * 0.96, minimum.joules(), 0.000_000_1);
        assertTrue(maximum.joules() <= 113.6 * 1.04);
        assertTrue(maximum.joules() > 113.6 * 1.039_999);
        assertEquals("x=109.06 J______", minimum.line2());
        assertEquals("x=118.14 J______", maximum.line2());
    }

    @Test
    public void measurementsIncrementAndReadyResetsTheCount() {
        ImpactDisplayState measured = ImpactDisplayState.ready()
                .nextLow(new FixedRandom(0.5))
                .nextHigh(new FixedRandom(0.5));

        assertEquals(2, measured.count());
        assertEquals("n=  2      READY", measured.line1());
        assertEquals("x=113.60 J______", measured.line2());
        assertEquals(0, ImpactDisplayState.ready().count());
    }

    @Test
    public void countSaturatesBeforeItCanOverflowTheDisplayField() {
        ImpactDisplayState state = ImpactDisplayState.ready();
        Random random = new FixedRandom(0.5);
        for (int index = 0; index < 1_005; index++) {
            state = state.nextLow(random);
        }

        assertEquals(ImpactDisplayState.MAX_COUNT, state.count());
        assertEquals("n=999      READY", state.line1());
        assertEquals(16, state.line1().length());
    }

    private static final class FixedRandom extends Random {
        private final double value;

        FixedRandom(double value) {
            this.value = value;
        }

        @Override
        public double nextDouble() {
            return value;
        }
    }
}
