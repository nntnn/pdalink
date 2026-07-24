package dev.zrimpact.remote;

import org.junit.Test;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertFalse;
import static org.junit.Assert.assertTrue;

public class DisplayActionLogTest {
    @Test
    public void newestEntryIsDisplayedFirstAndSerializationRestoresOrder() {
        DisplayActionLog log = new DisplayActionLog();
        log.append("2026-07-24 10:00:00", "Low", "n=1, 18.91 J");
        log.append("2026-07-24 10:00:01", "Zero", "n=2, 0.01 J");

        String display = log.displayText();
        assertTrue(display.startsWith("2026-07-24 10:00:01  Zero"));
        assertEquals(display, DisplayActionLog.restore(log.serialize()).displayText());
    }

    @Test
    public void logIsBoundedAndSanitizesLineBreaks() {
        DisplayActionLog log = new DisplayActionLog();
        for (int index = 0; index < DisplayActionLog.MAX_ENTRIES + 5; index++) {
            log.append("time", "Send", "value-" + index);
        }
        log.append("time", "Send", "line1\nline2");

        assertEquals(DisplayActionLog.MAX_ENTRIES, log.size());
        assertTrue(log.displayText().contains("line1 line2"));
        assertFalse(log.serialize().contains("value-0\n"));
    }
}
