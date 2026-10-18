import SwiftUI

@main
struct HearoWatchApp: App {
    @StateObject private var alerts = WatchAlertStore.shared

    var body: some Scene {
        WindowGroup {
            WatchContentView(alerts: alerts)
        }
    }
}
