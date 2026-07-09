package dev.pdalink.prototype;

import android.content.Context;
import android.net.ConnectivityManager;
import android.net.Network;
import android.net.NetworkCapabilities;
import android.net.NetworkRequest;

import java.io.Closeable;
import java.io.IOException;
import java.net.InetAddress;
import java.net.InetSocketAddress;
import java.net.Socket;

final class NetworkSelector implements Closeable {
    interface StatusSink {
        void status(String line);
    }

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
        Network network = cellularNetwork;
        try {
            Socket socket = openWithNetwork(network, host, port);
            egress = "cellular";
            return socket;
        } catch (IOException ex) {
            statusSink.status("Cellular egress failed; using default network: " + ex.getMessage());
        }
        egress = "default fallback";
        if (cellularNetwork == null) {
            statusSink.status("No active cellular network; using default egress");
        }
        return openWithDefaultNetwork(host, port);
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
        for (InetAddress address : addresses) {
            Socket socket = network.getSocketFactory().createSocket();
            try {
                socket.connect(new InetSocketAddress(address, port), 15_000);
                return socket;
            } catch (IOException ex) {
                last = ex;
                try {
                    socket.close();
                } catch (IOException ignored) {
                }
            }
        }
        throw last == null ? new IOException("No resolved address for " + host) : last;
    }

    private Socket openWithDefaultNetwork(String host, int port) throws IOException {
        IOException last = null;
        InetAddress[] addresses = InetAddress.getAllByName(host);
        for (InetAddress address : addresses) {
            Socket socket = new Socket();
            try {
                socket.connect(new InetSocketAddress(address, port), 15_000);
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
