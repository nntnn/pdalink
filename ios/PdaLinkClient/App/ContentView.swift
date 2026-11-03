import PdaLinkCore
import SwiftUI
import UIKit

struct ContentView: View {
    @StateObject private var model = ConnectionViewModel()

    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(spacing: 16) {
                    statusCard
                    connectionCard
                    actions
                    limitations
                }
                .padding()
            }
            .background(Color(uiColor: .systemGroupedBackground))
            .navigationTitle("PdaLink Client")
            .sheet(isPresented: $model.isScannerPresented) {
                NavigationStack {
                    QRScannerView { result in
                        model.isScannerPresented = false
                        switch result {
                        case let .success(value):
                            model.acceptConnectionURI(value)
                        case let .failure(error):
                            model.present(error)
                        }
                    }
                    .ignoresSafeArea(edges: .bottom)
                    .navigationTitle("Scan host QR")
                    .navigationBarTitleDisplayMode(.inline)
                    .toolbar {
                        ToolbarItem(placement: .cancellationAction) {
                            Button("Cancel") {
                                model.isScannerPresented = false
                            }
                        }
                    }
                }
            }
            .alert("PdaLink could not connect", isPresented: $model.isShowingError) {
                Button("OK", role: .cancel) {}
            } message: {
                Text(model.errorMessage)
            }
        }
    }

    private var statusCard: some View {
        VStack(alignment: .leading, spacing: 12) {
            HStack(spacing: 10) {
                Image(systemName: model.statusSymbol)
                    .font(.title2)
                    .foregroundStyle(model.statusColor)
                    .accessibilityHidden(true)
                VStack(alignment: .leading, spacing: 2) {
                    Text("Status")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                    Text(model.statusTitle)
                        .font(.headline)
                }
                Spacer()
                if model.isBusy {
                    ProgressView()
                        .accessibilityLabel("Connection in progress")
                }
            }

            if !model.statusDetail.isEmpty {
                Text(model.statusDetail)
                    .font(.subheadline)
                    .foregroundStyle(.secondary)
                    .fixedSize(horizontal: false, vertical: true)
            }
        }
        .cardStyle()
        .accessibilityElement(children: .combine)
    }

    @ViewBuilder
    private var connectionCard: some View {
        if let connection = model.connection {
            VStack(alignment: .leading, spacing: 12) {
                HStack {
                    Label("Host connection", systemImage: "wifi.router")
                        .font(.headline)
                    Spacer()
                    if !model.isBusy && !model.isConnected {
                        Button("Clear") {
                            model.clearConnection()
                        }
                        .font(.subheadline)
                    }
                }
                Divider()
                DetailRow(label: "Network", value: connection.ssid)
                DetailRow(label: "Transport", value: connection.transport.displayName)
                DetailRow(label: "Security", value: connection.security.displayName)
                DetailRow(label: "Proxy", value: connection.proxyEndpoint)
            }
            .cardStyle()
            .privacySensitive()
        } else {
            VStack(alignment: .leading, spacing: 10) {
                Label("No host selected", systemImage: "qrcode.viewfinder")
                    .font(.headline)
                Text("Open Connection QR on the Android PdaLink host, then scan it here. The Wi-Fi password is kept only in memory while this app is running.")
                    .font(.subheadline)
                    .foregroundStyle(.secondary)
                    .fixedSize(horizontal: false, vertical: true)
            }
            .cardStyle()
        }
    }

    private var actions: some View {
        VStack(spacing: 10) {
            HStack(spacing: 10) {
                Button {
                    model.isScannerPresented = true
                } label: {
                    Label("Scan QR", systemImage: "qrcode.viewfinder")
                        .frame(maxWidth: .infinity)
                }
                .buttonStyle(.bordered)
                .disabled(model.isBusy || model.isConnected)

                Button {
                    guard let value = UIPasteboard.general.string else {
                        model.present(ConnectionViewModelError.clipboardEmpty)
                        return
                    }
                    model.acceptConnectionURI(value)
                } label: {
                    Label("Paste", systemImage: "doc.on.clipboard")
                        .frame(maxWidth: .infinity)
                }
                .buttonStyle(.bordered)
                .disabled(model.isBusy || model.isConnected)
            }

            if model.isConnected || model.isBusy {
                Button(role: .destructive) {
                    model.disconnect()
                } label: {
                    Text("Disconnect")
                        .frame(maxWidth: .infinity)
                }
                .buttonStyle(.borderedProminent)
                .disabled(model.phase == .disconnecting)
            } else {
                Button {
                    model.connect()
                } label: {
                    Text("Connect")
                        .frame(maxWidth: .infinity)
                }
                .buttonStyle(.borderedProminent)
                .disabled(model.connection == nil)
            }
        }
    }

    private var limitations: some View {
        VStack(alignment: .leading, spacing: 8) {
            Label("Current tunnel scope", systemImage: "info.circle")
                .font(.subheadline.weight(.semibold))
            Text("IPv4 TCP and virtual DNS use the Android HTTP CONNECT proxy. Generic UDP, QUIC, ICMP, and IPv6 are not guaranteed by the current PdaLink proxy contract.")
                .font(.footnote)
                .foregroundStyle(.secondary)
                .fixedSize(horizontal: false, vertical: true)
        }
        .padding(.horizontal, 4)
    }
}

private struct DetailRow: View {
    let label: String
    let value: String

    var body: some View {
        HStack(alignment: .firstTextBaseline, spacing: 12) {
            Text(label)
                .foregroundStyle(.secondary)
            Spacer(minLength: 12)
            Text(value)
                .multilineTextAlignment(.trailing)
                .textSelection(.enabled)
        }
        .font(.subheadline)
        .accessibilityElement(children: .combine)
    }
}

private extension View {
    func cardStyle() -> some View {
        self
            .padding(16)
            .frame(maxWidth: .infinity, alignment: .leading)
            .background(Color(uiColor: .secondarySystemGroupedBackground), in: RoundedRectangle(cornerRadius: 16))
    }
}

private extension PdaLinkConnection.Transport {
    var displayName: String {
        switch self {
        case .localHotspot: "Local Hotspot"
        case .wifiDirect: "Wi-Fi Direct"
        }
    }
}

private extension PdaLinkConnection.SecurityMode {
    var displayName: String {
        switch self {
        case .wpa2: "WPA2"
        case .wpa3: "WPA3"
        case .transition: "WPA2/WPA3"
        }
    }
}
