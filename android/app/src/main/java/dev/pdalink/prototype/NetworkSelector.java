package dev.pdalink.prototype;

import android.content.Context;
import android.net.ConnectivityManager;
import android.net.Network;
import android.net.NetworkCapabilities;
import android.net.NetworkRequest;

import java.io.Closeable;
import java.io.IOException;
import java.net.ConnectException;
import java.net.InetAddress;
import java.net.InetSocketAddress;
import java.net.Socket;
import java.net.SocketTimeoutException;

final class NetworkSelector implements Closeable {
    interface StatusSink {
        void status(String line);
    }

    /**
     * Kept below the Linux/Windows TUN CONNECT timeout (10 s) so a phone-side
     * socket never outlives the client that is waiting for it.
     */
    private static final int CONNECT_TIMEOUT_MS = 8_000;

    private final ConnectivityManager connectivityManager;
    private final StatusSink statusSink;
    private ConnectivityManager.NetworkCallback callback;
    private volatile Network cellularNetwork;
    private volatile String egress = "default fallback";

    NetworkSelector(Context context, StatusSink statusSink) {
        this.connectivityManager = (ConnectivityManager) context.getSystemService(Context.CONNECTIVITY_SERVICE);
        this.statusSink = statusSink;
    }

    void start() {
        NetworkRequest request = new NetworkRequest.Builder()
                .addCapability(NetworkCapabilities.NET_CAPABILITY_INTERNET)
                .addTransportType(NetworkCapabilities.TRANSPORT_CELLULAR)
                .build();
        callback = new ConnectivityManager.NetworkCallback() {
            @Override
            public void onAvailable(Network network) {
                cellularNetwork = network;
                statusSink.status("Cellular network available for outbound proxy sockets");
            }

            @Override
            public void onLost(Network network) {
                if (network.equals(cellularNetwork)) {
                    cellularNetwork = null;
                    statusSink.status("Cellular network lost; falling back to default network");
                }
            }
        };
        try {
            connectivityManager.requestNetwork(request, callback);
            statusSink.status("Requested cellular network for proxy egress");
        } catch (RuntimeException ex) {
            statusSink.status("Cellular request failed; using default network: " + ex.getMessage());
        }
    }

    Socket openSocket(String host, int port) throws IOException {
        if (DestinationPolicy.isLocalOnlyLiteral(host)) {
            throw new DestinationPolicy.BlockedDestinationException(host);
        }
        Network network = cellularNetwork;
        try {
            Socket socket = openWithNetwork(network, host, port);
            egress = "cellular";
            return socket;
        } catch (IOException ex) {
            if (network != null && !shouldRetryOnDefaultNetwork(ex)) {
                // The cellular path is up and the target itself refused or did
                // not answer. Retrying on another network would only double
                // the time a dead destination holds this proxy session.
                throw ex;
            }
            statusSink.status("Cellular egress failed; using default network: " + ex.getMessage());
        }
        egress = "default fallback";
        if (cellularNetwork == null) {
            statusSink.status("No active cellular network; using default egress");
        }
        return openWithDefaultNetwork(host, port);
    }

    static boolean shouldRetryOnDefaultNetwork(IOException ex) {
        return !(ex instanceof DestinationPolicy.BlockedDestinationException
                || ex instanceof SocketTimeoutException
                || ex instanceof ConnectException);
    }

    String getEgress() {
        return egress == null ? "default fallback" : egress;
    }

    private Socket openWithNetwork(Network network, String host, int port) throws IOException {
        if (network == null) {
            throw new IOException("No active cellular network");
        }
        InetAddress[] addresses = network.getAllByName(host);
        IOException last = null;
        boolean blocked = false;
        for (InetAddress address : addresses) {
            if (DestinationPolicy.isLocalOnly(address)) {
                blocked = true;
                continue;
            }
            Socket socket = network.getSocketFactory().createSocket();
            try {
                socket.connect(new InetSocketAddress(address, port), CONNECT_TIMEOUT_MS);
                return socket;
            } catch (IOException ex) {
                last = ex;
                try {
                    socket.close();
                } catch (IOException ignored) {
                }
            }
        }
        if (last == null && blocked) {
            throw new DestinationPolicy.BlockedDestinationException(host);
        }
        throw last == null ? new IOException("No resolved address for " + host) : last;
    }

    private Socket openWithDefaultNetwork(String host, int port) throws IOException {
        IOException last = null;
        InetAddress[] addresses = InetAddress.getAllByName(host);
        boolean blocked = false;
        for (InetAddress address : addresses) {
            if (DestinationPolicy.isLocalOnly(address)) {
                blocked = true;
                continue;
            }
            Socket socket = new Socket();
            try {
                socket.connect(new InetSocketAddress(address, port), CONNECT_TIMEOUT_MS);
                return socket;
            } catch (IOException ex) {
                last = ex;
                try {
                    socket.close();
                } catch (IOException ignored) {
                }
            }
        }
        if (last != null) {
            throw last;
        }
        if (blocked) {
            throw new DestinationPolicy.BlockedDestinationException(host);
        }
        throw new IOException("No resolved address for " + host);
    }

    @Override
    public void close() {
        if (callback != null) {
            try {
                connectivityManager.unregisterNetworkCallback(callback);
            } catch (RuntimeException ignored) {
            }
            callback = null;
        }
    }
}
