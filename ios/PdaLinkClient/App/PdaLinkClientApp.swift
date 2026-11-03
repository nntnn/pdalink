import SwiftUI

@main
struct PdaLinkClientApp: App {
    @Environment(\.scenePhase) private var scenePhase

    var body: some Scene {
        WindowGroup {
            ZStack {
                ContentView()
                    .opacity(scenePhase == .active ? 1 : 0)

                if scenePhase != .active {
                    Color(uiColor: .systemBackground)
                        .ignoresSafeArea()
                    VStack(spacing: 12) {
                        Image(systemName: "lock.shield")
                            .font(.system(size: 34))
                        Text("PdaLink Client")
                            .font(.headline)
                    }
                    .foregroundStyle(.secondary)
                }
            }
        }
    }
}
