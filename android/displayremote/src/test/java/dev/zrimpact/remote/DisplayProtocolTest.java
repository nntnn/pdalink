package dev.zrimpact.remote;

import org.junit.Test;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertThrows;

public class DisplayProtocolTest {
    @Test
    public void requestIsOneJsonObjectWithEscapedText() {
        assertEquals(
                "{\"version\":1,\"action\":\"display\","
                        + "\"line1\":\"A\\\\B\",\"line2\":\"C\\\"D\"}",
                DisplayProtocol.requestJson("A\\B", "C\"D"));
    }

    @Test
    public void rejectsLongAndNonAsciiLines() {
        assertThrows(
                IllegalArgumentException.class,
                () -> DisplayProtocol.requestJson("12345678901234567", ""));
        assertThrows(
                IllegalArgumentException.class,
                () -> DisplayProtocol.requestJson("한글", ""));
    }
}
