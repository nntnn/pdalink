package dev.zrimpact.remote;

import java.util.Locale;
import java.util.Random;

final class ImpactDisplayState {
    static final double DEFAULT_LOW_REFERENCE = 18.8;
    static final double LOW_MIN_OFFSET = -1.00;
    static final double LOW_MAX_OFFSET = 1.10;
    static final double LOW_REFERENCE_MIN = 1.00;
    static final double LOW_REFERENCE_MAX = 998.89;
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

    ImpactDisplayState nextLow(Random random, double reference) {
        if (!isValidLowReference(reference)) {
            throw new IllegalArgumentException("invalid low reference");
        }
        return next(randomOffsetValue(
                random,
                reference,
                LOW_MIN_OFFSET,
                LOW_MAX_OFFSET));
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

    static boolean isValidLowReference(double reference) {
        return Double.isFinite(reference)
                && reference >= LOW_REFERENCE_MIN
                && reference <= LOW_REFERENCE_MAX;
    }

    private static double randomOffsetValue(
            Random random,
            double reference,
            double minimumOffset,
            double maximumOffset) {
        if (random == null) {
            throw new IllegalArgumentException("random is required");
        }
        return reference
                + minimumOffset
                + random.nextDouble() * (maximumOffset - minimumOffset);
    }

    private static double randomValue(Random random, double center) {
        if (random == null) {
            throw new IllegalArgumentException("random is required");
        }
        double scale = 1.0 - TOLERANCE + random.nextDouble() * TOLERANCE * 2.0;
        return center * scale;
    }
}
