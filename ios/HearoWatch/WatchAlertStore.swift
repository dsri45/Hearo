import Combine
import Foundation
import WatchConnectivity
import WatchKit

final class WatchAlertStore: NSObject, ObservableObject, WCSessionDelegate {
    static let shared = WatchAlertStore()

    @Published private(set) var latestTitle = UserDefaults.standard.string(forKey: "hearo.watch.latest.title")
    @Published private(set) var latestTimestamp = UserDefaults.standard.double(forKey: "hearo.watch.latest.timestamp")

    private let session = WCSession.default

    private override init() {
        super.init()
        guard WCSession.isSupported() else { return }
        session.delegate = self
        session.activate()
    }

    func session(
        _ session: WCSession,
        activationDidCompleteWith activationState: WCSessionActivationState,
        error: Error?
    ) {}

    func sessionReachabilityDidChange(_ session: WCSession) {}

    func session(_ session: WCSession, didReceiveMessage message: [String: Any]) {
        receive(message, playHaptic: true)
    }

    func session(_ session: WCSession, didReceiveUserInfo userInfo: [String: Any] = [:]) {
        receive(userInfo, playHaptic: true)
    }

    private func receive(_ payload: [String: Any], playHaptic: Bool) {
        guard let title = payload["title"] as? String, !title.isEmpty else { return }
        let timestamp = payload["timestamp"] as? Double ?? Date().timeIntervalSince1970

        DispatchQueue.main.async {
            self.latestTitle = title
            self.latestTimestamp = timestamp
            UserDefaults.standard.set(title, forKey: "hearo.watch.latest.title")
            UserDefaults.standard.set(timestamp, forKey: "hearo.watch.latest.timestamp")
            if playHaptic && WKApplication.shared().applicationState == .active {
                WKInterfaceDevice.current().play(.notification)
            }
        }
    }
}
