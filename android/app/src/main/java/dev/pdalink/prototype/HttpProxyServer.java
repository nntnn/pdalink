package dev.pdalink.prototype;

import android.content.Context;
import android.util.Log;

import java.io.BufferedInputStream;
import java.io.BufferedOutputStream;
import java.io.ByteArrayOutputStream;
import java.io.Closeable;
import java.io.IOException;
import java.io.InputStream;
import java.io.OutputStream;
import java.net.InetSocketAddress;
import java.net.ServerSocket;
import java.net.Socket;
import java.net.URI;
import java.net.URISyntaxException;
import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.Collections;
import java.util.List;
import java.util.Locale;
import java.util.Set;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.ConcurrentHashMap;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.atomic.AtomicInteger;
import java.util.concurrent.atomic.AtomicLong;

final class HttpProxyServer implements Closeable {
    private static final String TAG = "PdaLinkProxy";
    private static final int CLIENT_SOCKET_TIMEOUT_MS = 30_000;
    private static final int REMOTE_SOCKET_TIMEOUT_MS = 30_000;
    private static final int MAX_LINE_LENGTH = 8192;
    private static final int MAX_HEADER_BYTES = 64 * 1024;
    private static final int MAX_HEADER_COUNT = 100;
    private static final long MAX_CONTENT_LENGTH = 16L * 1024L * 1024L;
    private static final int MAX_RECENT_SESSIONS = 20;

    interface SocketFactory {
        Socket open(TargetHost host) throws IOException;
    }

    interface StatusSink {
        void status(String line);
    }

    interface SessionSink {
        void sessionsChanged();
    }

    static final class TargetHost {
        final String host;
        final int port;

        TargetHost(String host, int port) {
            this.host = host;
            this.port = port;
        }
    }

    private final Context context;
    private final int[] candidatePorts;
    private final String bindAddress;
    private final String allowedClientPrefix;
    private final String transport;
    private final SocketFactory socketFactory;
    private final StatusSink statusSink;
    private final SessionSink sessionSink;
    private final ProxySessionStore sessionStore = new ProxySessionStore(MAX_RECENT_SESSIONS);
    private final ExecutorService executor = Executors.newCachedThreadPool();
    private final Set<Socket> activeSockets = Collections.newSetFromMap(new ConcurrentHashMap<>());
    private final AtomicInteger activeClients = new AtomicInteger();
    private final AtomicLong upstreamBytes = new AtomicLong();
    private final AtomicLong downstreamBytes = new AtomicLong();

    private volatile boolean running;
    private volatile String lastRequest = "";
    private ServerSocket serverSocket;
    private int port = -1;

    HttpProxyServer(Context context, int[] candidatePorts, SocketFactory socketFactory, StatusSink statusSink) {
        this(context, null, candidatePorts, socketFactory, statusSink);
    }

    HttpProxyServer(Context context, String bindAddress, int[] candidatePorts, SocketFactory socketFactory, StatusSink statusSink) {
        this(context, bindAddress, "", candidatePorts, socketFactory, statusSink);
    }

    HttpProxyServer(Context context, String bindAddress, String allowedClientPrefix, int[] candidatePorts, SocketFactory socketFactory, StatusSink statusSink) {
        this(context, bindAddress, allowedClientPrefix, candidatePorts, socketFactory, statusSink,
                "127.0.0.1".equals(bindAddress) ? "USB" : "Wi-Fi", () -> { });
    }

    HttpProxyServer(
            Context context,
            String bindAddress,
            String allowedClientPrefix,
            int[] candidatePorts,
            SocketFactory socketFactory,
            StatusSink statusSink,
            String transport,
            SessionSink sessionSink) {
        this.context = context;
        this.candidatePorts = candidatePorts;
        this.bindAddress = bindAddress;
        this.allowedClientPrefix = allowedClientPrefix == null ? "" : allowedClientPrefix;
        this.transport = transport == null ? "" : transport;
        this.socketFactory = socketFactory;
        this.statusSink = statusSink;
        this.sessionSink = sessionSink;
    }

    void start() {
        if (running) {
            return;
        }
        for (int candidate : candidatePorts) {
            try {
                if (hasBindAddress()) {
                    serverSocket = new ServerSocket();
                    serverSocket.bind(new InetSocketAddress(bindAddress, candidate));
                } else {
                    serverSocket = new ServerSocket(candidate);
                }
                port = candidate;
                running = true;
                executor.execute(this::acceptLoop);
                status("Proxy listening on " + listenHostPort());
                return;
            } catch (IOException ex) {
                status("Port " + candidate + " unavailable: " + ex.getMessage());
            }
        }
        status("No proxy port available");
    }

    boolean isRunning() {
        return running;
    }

    int getPort() {
        return port;
    }

    String listenSummary() {
        return running ? "listening on " + listenHostPort() : "not running";
    }

    int getClientCount() {
        return activeClients.get();
    }

    String getLastRequest() {
        return lastRequest;
    }

    long getUpstreamBytes() {
        return upstreamBytes.get();
    }

    long getDownstreamBytes() {
        return downstreamBytes.get();
    }

    List<ProxySessionSnapshot> getSessionSnapshots() {
        return sessionStore.snapshot();
    }

    private void acceptLoop() {
        while (running) {
            try {
                Socket client = serverSocket.accept();
                if (!clientAllowed(client)) {
                    String address = clientAddress(client);
                    closeQuietly(client);
                    status("Rejected proxy client outside Wi-Fi Direct link: " + address);
                    continue;
                }
                trackSocket(client);
                try {
                    executor.execute(() -> handleClient(client));
                } catch (RuntimeException ex) {
                    untrackSocket(client);
                    closeQuietly(client);
                    throw ex;
                }
            } catch (IOException ex) {
                if (running) {
                    status("Accept failed: " + ex.getMessage());
                }
            }
        }
    }

    private void handleClient(Socket client) {
        String address = clientAddress(client);
        ProxySessionStore.SessionHandle session = sessionStore.begin(
                transport, address, System.currentTimeMillis());
        activeClients.incrementAndGet();
        boolean sessionPublished = false;
        try (Socket ignored = client) {
            client.setSoTimeout(CLIENT_SOCKET_TIMEOUT_MS);
            client.setTcpNoDelay(true);
            BufferedInputStream input = new BufferedInputStream(client.getInputStream());
            BufferedOutputStream output = new BufferedOutputStream(client.getOutputStream());
            try {
                Request request = readRequest(input);
                if (request == null) {
                    return;
                }
                sessionStore.updateRequest(session, request.method, requestDestination(request));
                boolean pacRequest = isPacRequest(request);
                if (pacRequest) {
                    sessionStore.excludeFromHistory(session);
                } else {
                    sessionsChanged();
                    sessionPublished = true;
                }
                lastRequest = request.method + " " + request.target;
                if ("CONNECT".equalsIgnoreCase(request.method)) {
                    handleConnect(client, request, input, output, session);
                } else if (isPacRequest(request)) {
                    writePac(output, !"HEAD".equalsIgnoreCase(request.method), request);
                } else if (isHttpProxyTarget(request.target)) {
                    handleHttp(request, input, output, session);
                } else {
                    writeResponse(output, 400, "Bad Request", "Unsupported proxy request target\n");
                }
            } catch (ProxyHttpException ex) {
                writeResponse(output, ex.code, ex.reason, ex.body);
            }
        } catch (IOException ex) {
            status("Client failed: " + errorMessage(ex));
            Log.i(TAG, "client failed", ex);
        } finally {
            untrackSocket(client);
            activeClients.decrementAndGet();
            sessionStore.finish(session, System.currentTimeMillis());
            if (sessionPublished) {
                sessionsChanged();
            }
        }
    }

    private Request readRequest(InputStream input) throws IOException {
        String first = readAsciiLine(input);
        if (first == null || first.isEmpty()) {
            return null;
        }
        String[] parts = first.split(" ", 3);
        if (parts.length != 3) {
            throw new ProxyHttpException(400, "Bad Request", "Malformed request line\n");
        }
        if (parts[0].isEmpty() || parts[1].isEmpty() || !parts[2].startsWith("HTTP/")) {
            throw new ProxyHttpException(400, "Bad Request", "Malformed request line\n");
        }
        List<String> headers = new ArrayList<>();
        int headerBytes = 0;
        String line;
        while (true) {
            line = readAsciiLine(input);
            if (line == null) {
                throw new ProxyHttpException(400, "Bad Request", "Unexpected end of headers\n");
            }
            if (line.isEmpty()) {
                break;
            }
            if (headers.size() >= MAX_HEADER_COUNT) {
                throw new ProxyHttpException(431, "Request Header Fields Too Large", "Too many request headers\n");
            }
            if (line.indexOf(':') < 1) {
                throw new ProxyHttpException(400, "Bad Request", "Malformed request header\n");
            }
            headerBytes += line.length() + 2;
            if (headerBytes > MAX_HEADER_BYTES) {
                throw new ProxyHttpException(431, "Request Header Fields Too Large", "Request headers too large\n");
            }
            headers.add(line);
        }
        return new Request(parts[0], parts[1], parts[2], headers);
    }

    private void handleConnect(
            Socket client,
            Request request,
            InputStream clientInput,
            OutputStream clientOutput,
            ProxySessionStore.SessionHandle session) throws IOException {
        TargetHost target;
        try {
            target = parseConnectTarget(request.target);
        } catch (IOException ex) {
            writeResponse(clientOutput, 400, "Bad Request", ex.getMessage() + "\n");
            return;
        }
        status("CONNECT " + target.host + ":" + target.port);
        try (Socket remote = socketFactory.open(target)) {
            trackSocket(remote);
            try {
                remote.setTcpNoDelay(true);
                clientOutput.write("HTTP/1.1 200 Connection Established\r\n\r\n".getBytes(StandardCharsets.ISO_8859_1));
                clientOutput.flush();
                client.setSoTimeout(0);
                remote.setSoTimeout(0);
                tunnel(client, remote, clientInput, clientOutput, session);
            } finally {
                untrackSocket(remote);
            }
        }
    }

    private void handleHttp(
            Request request,
            InputStream clientInput,
            OutputStream clientOutput,
            ProxySessionStore.SessionHandle session) throws IOException {
        String transferEncoding = headerValue(request, "transfer-encoding");
        if (transferEncoding != null && !"identity".equalsIgnoreCase(transferEncoding)) {
            writeResponse(clientOutput, 501, "Not Implemented", "Transfer-Encoding request bodies are not supported by this prototype\n");
            return;
        }
        String expect = headerValue(request, "expect");
        if (expect != null && !expect.toLowerCase(Locale.US).contains("100-continue")) {
            writeResponse(clientOutput, 417, "Expectation Failed", "Unsupported Expect header\n");
            return;
        }

        URI uri;
        try {
            uri = URI.create(request.target);
        } catch (IllegalArgumentException ex) {
            writeResponse(clientOutput, 400, "Bad Request", "Malformed proxy request target\n");
            return;
        }
        if (!"http".equalsIgnoreCase(uri.getScheme())) {
            writeResponse(clientOutput, 400, "Bad Request", "Unsupported proxy request target\n");
            return;
        }
        String host = uri.getHost();
        if (host == null) {
            writeResponse(clientOutput, 400, "Bad Request", "Missing host\n");
            return;
        }
        long contentLength;
        try {
            contentLength = contentLength(request);
        } catch (IOException ex) {
            writeResponse(clientOutput, 400, "Bad Request", ex.getMessage() + "\n");
            return;
        }
        if (contentLength > MAX_CONTENT_LENGTH) {
            writeResponse(clientOutput, 413, "Payload Too Large", "Request body too large for this prototype\n");
            return;
        }

        int remotePort = uri.getPort() == -1 ? 80 : uri.getPort();
        if (remotePort <= 0 || remotePort > 65535) {
            writeResponse(clientOutput, 400, "Bad Request", "Invalid target port\n");
            return;
        }
        String path = uri.getRawPath() == null || uri.getRawPath().isEmpty() ? "/" : uri.getRawPath();
        if (uri.getRawQuery() != null) {
            path += "?" + uri.getRawQuery();
        }

        if (contentLength > 0 && hasExpectContinue(request)) {
            clientOutput.write("HTTP/1.1 100 Continue\r\n\r\n".getBytes(StandardCharsets.ISO_8859_1));
            clientOutput.flush();
        }

        status(request.method + " http://" + host + ":" + remotePort + path);
        try (Socket remote = socketFactory.open(new TargetHost(host, remotePort))) {
            trackSocket(remote);
            try {
                remote.setSoTimeout(REMOTE_SOCKET_TIMEOUT_MS);
                remote.setTcpNoDelay(true);
                OutputStream remoteOut = new BufferedOutputStream(remote.getOutputStream());
                remoteOut.write((request.method + " " + path + " " + request.version + "\r\n")
                        .getBytes(StandardCharsets.ISO_8859_1));
                remoteOut.write(("Host: " + hostHeader(host, remotePort) + "\r\n").getBytes(StandardCharsets.ISO_8859_1));
                for (String header : request.headers) {
                    String lower = header.toLowerCase(Locale.US);
                    if (lower.startsWith("host:")
                            || lower.startsWith("proxy-connection:")
                            || lower.startsWith("connection:")
                            || lower.startsWith("expect:")
                            || lower.startsWith("transfer-encoding:")
                            || lower.startsWith("keep-alive:")) {
                        continue;
                    }
                    remoteOut.write((header + "\r\n").getBytes(StandardCharsets.ISO_8859_1));
                }
                remoteOut.write("Connection: close\r\n\r\n".getBytes(StandardCharsets.ISO_8859_1));
                long upstream = 0L;
                if (contentLength > 0) {
                    upstream = copyFixed(clientInput, remoteOut, contentLength, session, true);
                }
                remoteOut.flush();

                long downstream = copy(remote.getInputStream(), clientOutput, session, false);
                status("HTTP traffic up " + formatBytes(upstream) + " down " + formatBytes(downstream));
            } finally {
                untrackSocket(remote);
            }
        }
    }

    private void tunnel(
            Socket client,
            Socket remote,
            InputStream clientInput,
            OutputStream clientOutput,
            ProxySessionStore.SessionHandle session) throws IOException {
        CountDownLatch done = new CountDownLatch(2);
        AtomicLong upstream = new AtomicLong();
        AtomicLong downstream = new AtomicLong();
        executor.execute(() -> {
            try {
                long copied = copy(clientInput, remote.getOutputStream(), session, true);
                upstream.set(copied);
            } catch (IOException ignored) {
            } finally {
                shutdownOutput(remote);
                done.countDown();
            }
        });
        executor.execute(() -> {
            try {
                long copied = copy(remote.getInputStream(), clientOutput, session, false);
                downstream.set(copied);
            } catch (IOException ignored) {
            } finally {
                shutdownOutput(client);
                done.countDown();
            }
        });
        try {
            done.await();
        } catch (InterruptedException ex) {
            Thread.currentThread().interrupt();
        } finally {
            status("CONNECT traffic up " + formatBytes(upstream.get()) + " down " + formatBytes(downstream.get()));
        }
    }

    private boolean isPacRequest(Request request) {
        return ("GET".equalsIgnoreCase(request.method) || "HEAD".equalsIgnoreCase(request.method))
                && ("/".equals(request.target)
                || "/proxy.pac".equals(request.target)
                || (port > 0 && request.target.startsWith("http://" + pacHost() + ":" + port + "/")));
    }

    private boolean isHttpProxyTarget(String target) {
        return target != null && target.regionMatches(true, 0, "http://", 0, "http://".length());
    }

    private String requestDestination(Request request) {
        if ("CONNECT".equalsIgnoreCase(request.method)) {
            try {
                TargetHost target = parseConnectTarget(request.target);
                return hostHeader(target.host, target.port);
            } catch (IOException ignored) {
                return "";
            }
        }
        if (isPacRequest(request)) {
            return "PdaLink proxy setup";
        }
        if (!isHttpProxyTarget(request.target)) {
            return "";
        }
        try {
            URI uri = URI.create(request.target);
            String host = uri.getHost();
            if (host == null) {
                return "";
            }
            return hostHeader(host, uri.getPort() == -1 ? 80 : uri.getPort());
        } catch (IllegalArgumentException ignored) {
            return "";
        }
    }

    private void writePac(OutputStream output, boolean includeBody, Request request) throws IOException {
        String endpoint = pacProxyEndpoint(request);
        String host = pacProxyHost(endpoint);
        String pac = "function FindProxyForURL(url, host) {\n"
                + "  if (host == \"" + jsString(host) + "\") return \"DIRECT\";\n"
                + "  return \"PROXY " + jsString(endpoint) + "; DIRECT\";\n"
                + "}\n";
        byte[] body = pac.getBytes(StandardCharsets.UTF_8);
        output.write(("HTTP/1.1 200 OK\r\n"
                + "Content-Type: application/x-ns-proxy-autoconfig\r\n"
                + "Content-Length: " + body.length + "\r\n"
                + "Connection: close\r\n\r\n").getBytes(StandardCharsets.ISO_8859_1));
        if (includeBody) {
            output.write(body);
        }
        output.flush();
    }

    private void writeResponse(OutputStream output, int code, String reason, String bodyText) throws IOException {
        byte[] body = bodyText.getBytes(StandardCharsets.UTF_8);
        output.write(("HTTP/1.1 " + code + " " + reason + "\r\n"
                + "Content-Type: text/plain; charset=utf-8\r\n"
                + "Content-Length: " + body.length + "\r\n"
                + "Connection: close\r\n\r\n").getBytes(StandardCharsets.ISO_8859_1));
        output.write(body);
        output.flush();
    }

    private long contentLength(Request request) throws IOException {
        Long parsedLength = null;
        for (String header : request.headers) {
            int idx = header.indexOf(':');
            if (idx < 1) {
                continue;
            }
            if (!header.substring(0, idx).trim().equalsIgnoreCase("content-length")) {
                continue;
            }
            String value = header.substring(idx + 1).trim();
            try {
                long parsed = Long.parseLong(value);
                if (parsed < 0) {
                    throw new NumberFormatException("negative");
                }
                if (parsedLength != null && parsedLength != parsed) {
                    throw new IOException("Conflicting Content-Length");
                }
                parsedLength = parsed;
            } catch (NumberFormatException ex) {
                throw new IOException("Invalid Content-Length");
            }
        }
        return parsedLength == null ? 0L : parsedLength;
    }

    private boolean hasExpectContinue(Request request) {
        String value = headerValue(request, "expect");
        return value != null && value.toLowerCase(Locale.US).contains("100-continue");
    }

    private String headerValue(Request request, String name) {
        for (String header : request.headers) {
            int idx = header.indexOf(':');
            if (idx < 1) {
                continue;
            }
            if (header.substring(0, idx).trim().equalsIgnoreCase(name)) {
                return header.substring(idx + 1).trim();
            }
        }
        return null;
    }

    private TargetHost parseConnectTarget(String value) throws IOException {
        if (value == null || value.isEmpty()) {
            throw new IOException("Missing CONNECT target");
        }
        if (value.startsWith("[")) {
            int end = value.indexOf(']');
            if (end < 0) {
                throw new IOException("Malformed IPv6 CONNECT target");
            }
            String host = value.substring(1, end);
            int port = 443;
            if (end + 1 < value.length()) {
                if (value.charAt(end + 1) != ':') {
                    throw new IOException("Malformed CONNECT target");
                }
                port = parsePort(value.substring(end + 2));
            }
            if (host.isEmpty()) {
                throw new IOException("Missing CONNECT host");
            }
            return new TargetHost(host, port);
        }

        int idx = value.lastIndexOf(':');
        if (idx < 0) {
            return new TargetHost(value, 443);
        }
        if (idx == 0) {
            throw new IOException("Missing CONNECT host");
        }
        return new TargetHost(value.substring(0, idx), parsePort(value.substring(idx + 1)));
    }

    private int parsePort(String value) throws IOException {
        try {
            int parsed = Integer.parseInt(value);
            if (parsed <= 0 || parsed > 65535) {
                throw new NumberFormatException("out of range");
            }
            return parsed;
        } catch (NumberFormatException ex) {
            throw new IOException("Invalid CONNECT port");
        }
    }

    private String hostHeader(String host, int port) {
        String displayHost = host.contains(":") && !host.startsWith("[") ? "[" + host + "]" : host;
        return port == 80 ? displayHost : displayHost + ":" + port;
    }

    private String readAsciiLine(InputStream input) throws IOException {
        ByteArrayOutputStream buffer = new ByteArrayOutputStream(128);
        int b;
        while ((b = input.read()) != -1) {
            if (b == '\n') {
                break;
            }
            if (b != '\r') {
                buffer.write(b);
            }
            if (buffer.size() > MAX_LINE_LENGTH) {
                throw new ProxyHttpException(431, "Request Header Fields Too Large", "HTTP line too long\n");
            }
        }
        if (b == -1 && buffer.size() == 0) {
            return null;
        }
        return buffer.toString("ISO-8859-1");
    }

    private long copy(
            InputStream input,
            OutputStream output,
            ProxySessionStore.SessionHandle session,
            boolean upstream) throws IOException {
        byte[] buffer = new byte[32 * 1024];
        long total = 0L;
        int read;
        while ((read = input.read(buffer)) != -1) {
            output.write(buffer, 0, read);
            output.flush();
            total += read;
            recordTraffic(session, upstream, read);
        }
        return total;
    }

    private long copyFixed(
            InputStream input,
            OutputStream output,
            long length,
            ProxySessionStore.SessionHandle session,
            boolean upstream) throws IOException {
        byte[] buffer = new byte[32 * 1024];
        long remaining = length;
        long total = 0L;
        while (remaining > 0) {
            int read = input.read(buffer, 0, (int) Math.min(buffer.length, remaining));
            if (read == -1) {
                throw new IOException("request body ended early");
            }
            output.write(buffer, 0, read);
            remaining -= read;
            total += read;
            recordTraffic(session, upstream, read);
        }
        return total;
    }

    private void recordTraffic(
            ProxySessionStore.SessionHandle session, boolean upstream, long byteCount) {
        if (upstream) {
            upstreamBytes.addAndGet(byteCount);
            sessionStore.addTraffic(session, byteCount, 0L);
        } else {
            downstreamBytes.addAndGet(byteCount);
            sessionStore.addTraffic(session, 0L, byteCount);
        }
    }

    private void shutdownOutput(Socket socket) {
        try {
            socket.shutdownOutput();
        } catch (IOException ignored) {
        }
    }

    private void trackSocket(Socket socket) {
        activeSockets.add(socket);
    }

    private void untrackSocket(Socket socket) {
        activeSockets.remove(socket);
    }

    private void closeActiveSockets() {
        for (Socket socket : activeSockets) {
            closeQuietly(socket);
        }
    }

    private void closeQuietly(Socket socket) {
        try {
            socket.close();
        } catch (IOException ignored) {
        }
    }

    private void status(String line) {
        Log.i(TAG, line);
        statusSink.status(line);
    }

    private void sessionsChanged() {
        sessionSink.sessionsChanged();
    }

    private boolean clientAllowed(Socket client) {
        if (allowedClientPrefix.isEmpty()) {
            return true;
        }
        return clientAddress(client).startsWith(allowedClientPrefix);
    }

    private String clientAddress(Socket client) {
        if (client.getInetAddress() == null) {
            return "";
        }
        return client.getInetAddress().getHostAddress();
    }

    @Override
    public void close() {
        running = false;
        if (serverSocket != null) {
            try {
                serverSocket.close();
            } catch (IOException ignored) {
            }
        }
        closeActiveSockets();
        executor.shutdownNow();
    }

    private boolean hasBindAddress() {
        return bindAddress != null && !bindAddress.isEmpty();
    }

    private String listenHostPort() {
        return (hasBindAddress() ? bindAddress : "0.0.0.0") + ":" + port;
    }

    private String pacHost() {
        return hasBindAddress() ? bindAddress : "192.168.49.1";
    }

    private String pacProxyEndpoint(Request request) {
        if (hasBindAddress()) {
            String host = headerValue(request, "host");
            String endpoint = validPacEndpoint(host);
            if (endpoint != null) {
                return endpoint;
            }
        }
        return pacHost() + ":" + port;
    }

    private String validPacEndpoint(String value) {
        if (value == null) {
            return null;
        }
        String candidate = value.trim();
        if (candidate.isEmpty() || candidate.length() > 255) {
            return null;
        }
        for (int i = 0; i < candidate.length(); i++) {
            char ch = candidate.charAt(i);
            if (ch <= 0x20 || ch == '"' || ch == '\'' || ch == '\\' || ch == ';') {
                return null;
            }
        }
        try {
            URI uri = new URI("http://" + candidate);
            String path = uri.getRawPath();
            if (uri.getHost() == null || uri.getPort() <= 0 || uri.getRawUserInfo() != null
                    || (path != null && !path.isEmpty()) || uri.getRawQuery() != null || uri.getRawFragment() != null) {
                return null;
            }
            return candidate;
        } catch (URISyntaxException | IllegalArgumentException ex) {
            return null;
        }
    }

    private String pacProxyHost(String endpoint) {
        if (endpoint.startsWith("[")) {
            int end = endpoint.indexOf(']');
            return end > 0 ? endpoint.substring(1, end) : endpoint;
        }
        int colon = endpoint.lastIndexOf(':');
        return colon > 0 ? endpoint.substring(0, colon) : endpoint;
    }

    private String jsString(String value) {
        StringBuilder escaped = new StringBuilder();
        for (int i = 0; i < value.length(); i++) {
            char ch = value.charAt(i);
            switch (ch) {
                case '\\':
                    escaped.append("\\\\");
                    break;
                case '"':
                    escaped.append("\\\"");
                    break;
                case '\n':
                    escaped.append("\\n");
                    break;
                case '\r':
                    escaped.append("\\r");
                    break;
                case '\t':
                    escaped.append("\\t");
                    break;
                default:
                    if (ch < 0x20) {
                        escaped.append(String.format(Locale.US, "\\u%04x", (int) ch));
                    } else {
                        escaped.append(ch);
                    }
                    break;
            }
        }
        return escaped.toString();
    }

    private String formatBytes(long bytes) {
        if (bytes < 1024L) {
            return bytes + " B";
        }
        double value = bytes;
        String[] units = {"B", "KB", "MB", "GB", "TB"};
        int unit = 0;
        while (value >= 1024.0 && unit < units.length - 1) {
            value /= 1024.0;
            unit++;
        }
        return String.format(Locale.US, "%.1f %s", value, units[unit]);
    }

    private String errorMessage(IOException ex) {
        String message = ex.getMessage();
        return message == null || message.isEmpty() ? ex.getClass().getSimpleName() : message;
    }

    private static final class ProxyHttpException extends IOException {
        final int code;
        final String reason;
        final String body;

        ProxyHttpException(int code, String reason, String body) {
            super(body);
            this.code = code;
            this.reason = reason;
            this.body = body;
        }
    }

    private static final class Request {
        final String method;
        final String target;
        final String version;
        final List<String> headers;

        Request(String method, String target, String version, List<String> headers) {
            this.method = method;
            this.target = target;
            this.version = version;
            this.headers = headers;
        }
    }
}
