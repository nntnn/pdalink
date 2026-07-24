package dev.zrimpact.remote;

import java.util.ArrayDeque;
import java.util.Deque;
import java.util.Iterator;

final class DisplayActionLog {
    static final int MAX_ENTRIES = 100;

    private final Deque<String> entries = new ArrayDeque<>();

    static DisplayActionLog restore(String serialized) {
        DisplayActionLog log = new DisplayActionLog();
        if (serialized == null || serialized.isEmpty()) {
            return log;
        }
        for (String entry : serialized.split("\n")) {
            if (!entry.isBlank()) {
                log.addEntry(entry);
            }
        }
        return log;
    }

    void append(String timestamp, String action, String detail) {
        String entry = clean(timestamp)
                + "  "
                + clean(action)
                + "  "
                + clean(detail);
        addEntry(entry.strip());
    }

    String serialize() {
        return String.join("\n", entries);
    }

    String displayText() {
        StringBuilder result = new StringBuilder();
        Iterator<String> iterator = entries.descendingIterator();
        while (iterator.hasNext()) {
            if (result.length() > 0) {
                result.append('\n');
            }
            result.append(iterator.next());
        }
        return result.toString();
    }

    int size() {
        return entries.size();
    }

    private void addEntry(String entry) {
        entries.addLast(entry);
        while (entries.size() > MAX_ENTRIES) {
            entries.removeFirst();
        }
    }

    private static String clean(String value) {
        if (value == null) {
            return "";
        }
        return value.replace('\n', ' ').replace('\r', ' ').strip();
    }
}
