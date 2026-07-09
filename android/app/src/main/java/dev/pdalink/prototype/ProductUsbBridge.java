package dev.pdalink.prototype;

import android.content.Context;
import android.hardware.usb.UsbAccessory;
import android.hardware.usb.UsbManager;
import android.os.ParcelFileDescriptor;
import android.util.Base64;
import android.util.Log;

import org.json.JSONArray;
import org.json.JSONObject;

import java.io.Closeable;
import java.io.DataInputStream;
import java.io.DataOutputStream;
import java.io.FileInputStream;
import java.io.FileOutputStream;
import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.util.Arrays;

final class ProductUsbBridge implements Closeable {
    interface StatusSink {
        void status(String line);
    }

    private static final int MAX_FRAME_SIZE = 1024 * 1024;
    private static final String TAG = "PdaLinkProductUsb";

    private final Context context;
    private final int localProxyPort;
    private final StatusSink statusSink;
    private final Object writeLock = new Object();

    private ParcelFileDescriptor descriptor;
    private DataInputStream input;
    private DataOutputStream output;
    private Thread readerThread;
    private byte[] pendingInput = new byte[0];
    private volatile boolean running;

    ProductUsbBridge(Context context, int localProxyPort, StatusSink statusSink) {
        this.context = context;
        this.localProxyPort = localProxyPort;
        this.statusSink = statusSink;
    }

    boolean start() {
        UsbManager usbManager = (UsbManager) context.getSystemService(Context.USB_SERVICE);
        if (usbManager == null) {
            status("Product USB manager unavailable.");
            return false;
        }
        UsbAccessory[] accessories = usbManager.getAccessoryList();
        if (accessories == null || accessories.length == 0) {
            status("Product USB accessory not connected.");
            return false;
        }
        UsbAccessory accessory = accessories[0];
        if (!usbManager.hasPermission(accessory)) {
            status("Product USB permission missing.");
            return false;
        }
        descriptor = usbManager.openAccessory(accessory);
        if (descriptor == null) {
            status("Product USB accessory open failed.");
            return false;
        }
        input = new DataInputStream(new FileInputStream(descriptor.getFileDescriptor()));
        output = new DataOutputStream(new FileOutputStream(descriptor.getFileDescriptor()));
        running = true;
        readerThread = new Thread(this::readLoop, "PdaLinkProductUsbBridge");
        readerThread.setDaemon(true);
        readerThread.start();
        status("Product USB bridge connected: " + accessoryName(accessory));
        status("Product USB waiting for Linux hello.");
        return true;
    }

    private void readLoop() {
        try {
            sendDeviceReady();
            while (running) {
                JSONObject frame = readFrame();
                handleFrame(frame);
            }
        } catch (IOException ex) {
            if (running) {
                status("Product USB bridge stopped: " + ex.getMessage());
            }
        } catch (Exception ex) {
            if (running) {
                status("Product USB bridge failed: " + ex.getMessage());
            }
        } finally {
            running = false;
        }
    }

    private JSONObject readFrame() throws Exception {
        byte[] chunk = new byte[16 * 1024];
        while (running) {
            JSONObject frame = decodePendingFrame();
            if (frame != null) {
                return frame;
            }
            int count = input.read(chunk);
            if (count < 0) {
                throw new IOException("Product USB stream closed");
            }
            appendPendingInput(chunk, count);
        }
        throw new IOException("Product USB bridge stopped");
    }

    private JSONObject decodePendingFrame() throws IOException {
        while (pendingInput.length >= 4) {
            int size = frameSize(pendingInput);
            if (size <= 0 || size > MAX_FRAME_SIZE) {
                Log.w(TAG, "invalid Product USB frame size " + size + "; resyncing");
                discardPendingInput(1);
                continue;
            }
            if (pendingInput.length > 4 && pendingInput[4] != '{') {
                Log.w(TAG, "misaligned Product USB frame preview " + hexPreview(pendingInput) + "; resyncing");
                discardPendingInput(4);
                continue;
            }
            int frameEnd = 4 + size;
            if (pendingInput.length < frameEnd) {
                return null;
            }
            byte[] raw = Arrays.copyOfRange(pendingInput, 4, frameEnd);
            discardPendingInput(frameEnd);
            JSONObject frame = parseFrame(raw);
            if (frame != null) {
                return frame;
            }
        }
        return null;
    }

    private JSONObject parseFrame(byte[] raw) throws IOException {
        String text = new String(raw, StandardCharsets.UTF_8);
        try {
            return new JSONObject(text);
        } catch (Exception ex) {
            String detail = "invalid Product USB JSON frame size " + raw.length
                    + " preview " + hexPreview(raw) + ": " + ex.getMessage();
            Log.w(TAG, detail, ex);
            status("Product USB frame skipped: " + detail);
            return null;
        }
    }

    private int frameSize(byte[] buffer) {
        return ((buffer[0] & 0xff) << 24)
                | ((buffer[1] & 0xff) << 16)
                | ((buffer[2] & 0xff) << 8)
                | (buffer[3] & 0xff);
    }

    private void appendPendingInput(byte[] chunk, int count) {
        int offset = pendingInput.length;
        pendingInput = Arrays.copyOf(pendingInput, offset + count);
        System.arraycopy(chunk, 0, pendingInput, offset, count);
    }

    private void discardPendingInput(int count) {
        if (count >= pendingInput.length) {
            pendingInput = new byte[0];
            return;
        }
        pendingInput = Arrays.copyOfRange(pendingInput, count, pendingInput.length);
    }

    private void handleFrame(JSONObject frame) throws Exception {
        String type = frame.optString("type", "");
        int streamId = frame.optInt("stream_id", 0);
        if ("hello".equals(type)) {
            JSONObject fields = frame.optJSONObject("fields");
            String fingerprint = fields == null ? "unknown" : fields.optString("fingerprint", "unknown");
            String hostName = fields == null ? "unknown" : fields.optString("host_name", "unknown");
            String normalizedFingerprint = fingerprint.trim();
            if (normalizedFingerprint.isEmpty() || "unknown".equals(normalizedFingerprint)) {
                sendHello(streamId, fingerprint, "untrusted");
                status("Product USB host fingerprint missing.");
                return;
            }
            if (!ProductUsbHostTrust.isTrusted(context, normalizedFingerprint)) {
                sendHello(streamId, normalizedFingerprint, "untrusted");
                status("Product USB host approval required: " + normalizedFingerprint + " host " + hostName);
                return;
            }
            sendHello(streamId, normalizedFingerprint, "trusted");
            status("Product USB host trusted: " + normalizedFingerprint);
            return;
        }
        if ("stream-open".equals(type)) {
            JSONObject fields = frame.optJSONObject("fields");
            String target = fields == null ? "" : fields.optString("target", "");
            if ("proxy-pac".equals(target)) {
                sendData(streamId, proxyPac().getBytes(StandardCharsets.UTF_8));
                sendClose(streamId);
                status("Product USB PAC request served.");
            } else {
                sendError(streamId, "unsupported target " + target);
            }
            return;
        }
        if (!"stream-close".equals(type)) {
            sendError(streamId, "unexpected frame " + type);
        }
    }

    private void sendHello(int streamId, String fingerprint, String hostTrust) throws Exception {
        JSONObject fields = new JSONObject();
        fields.put("version", 1);
        fields.put("permission", "granted");
        fields.put("host_trust", hostTrust);
        fields.put("fingerprint", fingerprint);
        JSONArray capabilities = new JSONArray();
        capabilities.put("proxy-pac");
        fields.put("capabilities", capabilities);
        sendFrame("hello", streamId, fields, new byte[0]);
    }

    private void sendData(int streamId, byte[] payload) throws Exception {
        sendFrame("stream-data", streamId, new JSONObject(), payload);
    }

    private void sendDeviceReady() throws Exception {
        JSONObject fields = new JSONObject();
        fields.put("version", 1);
        fields.put("state", "ready");
        sendFrame("device-ready", 0, fields, new byte[0]);
        status("Product USB device-ready sent.");
    }

    private void sendClose(int streamId) throws Exception {
        sendFrame("stream-close", streamId, new JSONObject(), new byte[0]);
    }

    private void sendError(int streamId, String reason) throws Exception {
        JSONObject fields = new JSONObject();
        fields.put("reason", reason);
        sendFrame("stream-error", streamId, fields, new byte[0]);
    }

    private void sendFrame(String type, int streamId, JSONObject fields, byte[] payload) throws Exception {
        JSONObject frame = new JSONObject();
        frame.put("type", type);
        frame.put("stream_id", streamId);
        frame.put("fields", fields);
        frame.put("payload", Base64.encodeToString(payload, Base64.NO_WRAP));
        byte[] raw = frame.toString().getBytes(StandardCharsets.UTF_8);
        if (raw.length > MAX_FRAME_SIZE) {
            throw new IOException("Product USB frame too large");
        }
        synchronized (writeLock) {
            output.writeInt(raw.length);
            output.write(raw);
            output.flush();
        }
    }

    private String proxyPac() {
        return "function FindProxyForURL(url, host) { return \"PROXY 127.0.0.1:"
                + localProxyPort + "; DIRECT\"; }";
    }

    private void status(String line) {
        statusSink.status(line);
    }

    private String accessoryName(UsbAccessory accessory) {
        String manufacturer = accessory.getManufacturer();
        String model = accessory.getModel();
        if (manufacturer == null || manufacturer.isEmpty()) {
            manufacturer = "unknown";
        }
        if (model == null || model.isEmpty()) {
            model = "unknown";
        }
        return manufacturer + " " + model;
    }

    private String hexPreview(byte[] raw) {
        int limit = Math.min(raw.length, 16);
        StringBuilder builder = new StringBuilder();
        for (int index = 0; index < limit; index++) {
            if (index > 0) {
                builder.append(' ');
            }
            builder.append(String.format("%02x", raw[index] & 0xff));
        }
        if (raw.length > limit) {
            builder.append(" ...");
        }
        return builder.toString();
    }

    @Override
    public void close() {
        running = false;
        closeQuietly(input);
        closeQuietly(output);
        closeQuietly(descriptor);
        input = null;
        output = null;
        descriptor = null;
        pendingInput = new byte[0];
    }

    private void closeQuietly(Closeable closeable) {
        if (closeable == null) {
            return;
        }
        try {
            closeable.close();
        } catch (IOException ignored) {
        }
    }
}
