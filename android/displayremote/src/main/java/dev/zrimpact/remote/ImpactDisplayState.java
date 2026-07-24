package dev.zrimpact.remote;

import java.util.Locale;
import java.util.Random;

final class ImpactDisplayState {
    static final double LOW_CENTER = 18.8;
    static final double HIGH_CENTER = 113.6;
    static final double TOLERANCE = 0.04;

    private final int count;
    private final double value;

    private ImpactDisplayState(int count, double value) {
        this.count = Math.max(0, Math.min(999, count));
        this.value = value;
    }

    static ImpactDisplayState ready() {
        return new ImpactDisplayState(0, 0.0);
    }

    ImpactDisplayState nextLow(Random random) {
        return next(randomValue(random, LOW_CENTER));
    }

    ImpactDisplayState nextHigh(Random random) {
        return next(randomValue(random, HIGH_CENTER));
    }

    private ImpactDisplayState next(double nextValue) {
        return new ImpactDisplayState(Math.min(999, count + 1), nextValue);
    }

    String line1() {
        return String.format(Locale.US, "n=%3d      READY", count);
    }

    String line2() {
        return String.format(Locale.US, "x=%6.2f J______", value);
    }

    int count() {
        return count;
    }

    double value() {
        return value;
    }

    private static double randomValue(Random random, double center) {
        if (random == null) {
            throw new IllegalArgumentException("random is required");
        }
        double scale = 1.0 - TOLERANCE + random.nextDouble() * TOLERANCE * 2.0;
        return center * scale;
    }
}
