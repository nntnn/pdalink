package dev.zrimpact.remote;

final class DisplayProtocol {
    static final int WIDTH = 16;

    private DisplayProtocol() {}

    static String requestJson(String line1, String line2) {
        validateLine(line1, "line1");
        validateLine(line2, "line2");
        return "{\"version\":1,\"action\":\"display\",\"line1\":\""
                + escape(line1)
                + "\",\"line2\":\""
                + escape(line2)
                + "\"}";
    }

    private static void validateLine(String value, String name) {
        if (value == null) {
            throw new IllegalArgumentException(name + " is required");
        }
        if (value.length() > WIDTH) {
            throw new IllegalArgumentException(name + " must be at most 16 characters");
        }
        for (int i = 0; i < value.length(); i++) {
            char character = value.charAt(i);
            if (character < 0x20 || character > 0x7e) {
                throw new IllegalArgumentException(name + " must be printable ASCII");
            }
        }
    }

    private static String escape(String value) {
        return value.replace("\\", "\\\\").replace("\"", "\\\"");
    }
}
