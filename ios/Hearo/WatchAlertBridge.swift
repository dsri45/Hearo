import Foundation
import UserNotifications
import WatchConnectivity

final class WatchAlertBridge: NSObject, WCSessionDelegate {
    static let shared = WatchAlertBridge()

    private let session = WCSession.default
    private let notificationCenter = UNUserNotificationCenter.current()
    private var pendingWatchAlerts: [[String: Any]] = []

    private override init() {
        super.init()
    }

    func activate() {
        guard WCSession.isSupported() else { return }
        session.delegate = self
        session.activate()
    }

    func requestNotificationAuthorization() {
        notificationCenter.requestAuthorization(options: [.alert, .badge, .sound]) { _, _ in }
    }

    func deliver(_ alert: AlertItem) {
        let payload: [String: Any] = [
            "id": alert.id,
            "title": alert.title,
            "timestamp": alert.timestamp,
        ]
        scheduleWatchForwardableNotification(for: alert)

        guard WCSession.isSupported() else { return }
        guard session.activationState == .activated else {
            pendingWatchAlerts.append(payload)
            return
        }
        sendToWatch(payload)
    }

    private func sendToWatch(_ payload: [String: Any]) {
        if session.isReachable {
            session.sendMessage(payload, replyHandler: nil) { [weak self] _ in
                self?.session.transferUserInfo(payload)
            }
        } else {
            session.transferUserInfo(payload)
        }
    }

    private func scheduleWatchForwardableNotification(for alert: AlertItem) {
        let content = UNMutableNotificationContent()
        content.title = alert.title
        content.body = ""
        content.sound = .default
        content.threadIdentifier = "com.hearo.alerts"
        let identifier = "\(alert.id)-\(Int(alert.timestamp * 1_000))"
        notificationCenter.add(UNNotificationRequest(
            identifier: identifier,
            content: content,
            trigger: nil
        ))
    }

    func session(
        _ session: WCSession,
        activationDidCompleteWith activationState: WCSessionActivationState,
        error: Error?
    ) {
        guard activationState == .activated, !pendingWatchAlerts.isEmpty else { return }
        let queued = pendingWatchAlerts
        pendingWatchAlerts.removeAll()
        queued.forEach(sendToWatch)
    }

    func sessionDidBecomeInactive(_ session: WCSession) {}

    func sessionDidDeactivate(_ session: WCSession) {
        session.activate()
    }
}
