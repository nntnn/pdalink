package dev.pdalink.prototype;

import java.util.Locale;
import java.util.Random;

final class ImpactDisplayState {
    static final double LOW_CENTER_JOULES = 18.8;
    static final double HIGH_CENTER_JOULES = 113.6;
    static final double TOLERANCE_FRACTION = 0.04;
    static final int MAX_COUNT = 999;

    private final int count;
    private final double joules;

    private ImpactDisplayState(int count, double joules) {
        this.count = count;
        this.joules = joules;
    }

    static ImpactDisplayState ready() {
        return new ImpactDisplayState(0, 0.0);
    }

    ImpactDisplayState nextLow(Random random) {
        return nextMeasurement(LOW_CENTER_JOULES, random);
    }

    ImpactDisplayState nextHigh(Random random) {
        return nextMeasurement(HIGH_CENTER_JOULES, random);
    }

    int count() {
        return count;
    }

    double joules() {
        return joules;
    }

    String line1() {
        return String.format(Locale.US, "n=%3d%11s", count, "READY");
    }

    String line2() {
        return String.format(Locale.US, "x=%6.2f J______", joules);
    }

    private ImpactDisplayState nextMeasurement(double center, Random random) {
        if (random == null) {
            throw new IllegalArgumentException("random source is required");
        }
        double minimum = center * (1.0 - TOLERANCE_FRACTION);
        double maximum = center * (1.0 + TOLERANCE_FRACTION);
        double value = minimum + ((maximum - minimum) * random.nextDouble());
        return new ImpactDisplayState(Math.min(MAX_COUNT, count + 1), value);
    }
}
