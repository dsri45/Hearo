import Foundation
import SwiftUI
import AVFoundation

@MainActor
final class HearoModel: ObservableObject {
    @Published var alerts: [AlertItem] = []
    @Published var watchlist: [WatchlistItem] = []
    @Published var presets: [SoundPreset] = []
    @Published var classifierLabels: [String] = []
    @Published var activeSession: RestSession?
    @Published var latestSession: RestSession?
    @Published var liveTranscript = ""
    @Published var microphoneStatus = "Microphone is off."
    @Published var hearingDeviceStatus = "Checking connected audio devices…"
    @Published var errorMessage: String?
    @Published var isBusy = false
    @Published var profileName = {
        let key = "hearo.profile.name"
        let saved = UserDefaults.standard.string(forKey: key)
        if saved == "Jordan Davis" {
            UserDefaults.standard.set("Shiloh Dawson", forKey: key)
            return "Shiloh Dawson"
        }
        return saved ?? "Shiloh Dawson"
    }()
    @Published var profileEmail = {
        let key = "hearo.profile.email"
        let saved = UserDefaults.standard.string(forKey: key)
        if saved == "jordan.davis@example.com" {
            UserDefaults.standard.set("shiloh.dawson@example.com", forKey: key)
            return "shiloh.dawson@example.com"
        }
        return saved ?? "shiloh.dawson@example.com"
    }()
    @Published var profilePhone = UserDefaults.standard.string(forKey: "hearo.profile.phone") ?? "(555) 010-0142"
    @Published var baseURL: String {
        didSet {
            UserDefaults.standard.set(baseURL, forKey: "hearo.backend.url")
            api.baseURL = baseURL
        }
    }

    private let api: HearoAPI
    private let audio = HearoAudioController()
    private var alertTask: Task<Void, Never>?
    private var refreshTask: Task<Void, Never>?
    private var lastSoundAt: [String: Date] = [:]
    private var audioRouteObserver: NSObjectProtocol?
    private var audioInputsObserver: NSObjectProtocol?
    private var hearingDeviceConnected: Bool?

    init() {
        let url = UserDefaults.standard.string(forKey: "hearo.backend.url") ?? "http://10.2.140.221:8000"
        baseURL = url
        api = HearoAPI(baseURL: url)
        audio.onTranscript = { [weak self] text in self?.liveTranscript = text }
        audio.onStatus = { [weak self] status in self?.microphoneStatus = status }
        audio.onSound = { [weak self] label, confidence in
            await self?.handleSound(label: label, confidence: confidence)
        }
    }

    var isListening: Bool { activeSession != nil && audioIsRunning }
    @Published private var audioIsRunning = false

    var displayFirstName: String {
        profileName.split(whereSeparator: \.isWhitespace).first.map(String.init) ?? "Shiloh"
    }

    func load() async {
        do {
            async let rest: RestModeResponse = api.get("/rest-mode")
            async let list: WatchlistResponse = api.get("/watchlist")
            async let alertList: AlertsResponse = api.get("/alerts")
            async let soundList: SoundPresetsResponse = api.get("/sound-presets")
            let (restValue, listValue, alertValue, soundValue) = try await (rest, list, alertList, soundList)
            activeSession = restValue.active
            latestSession = restValue.latest
            watchlist = listValue.items
            alerts = alertValue.alerts
            presets = soundValue.presets
            if classifierLabels.isEmpty {
                classifierLabels = (try? HearoAudioController.knownSoundLabels()) ?? []
            }
            errorMessage = nil
        } catch {
            errorMessage = error.localizedDescription
        }
    }

    func connect() {
        alertTask?.cancel()
        refreshTask?.cancel()
        alertTask = Task { [weak self] in await self?.alertConnectionLoop() }
        refreshTask = Task { [weak self] in
            while !Task.isCancelled {
                try? await Task.sleep(for: .seconds(5))
                guard !Task.isCancelled else { return }
                await self?.load()
            }
        }
    }

    func monitorHearingDeviceRoute() {
        guard audioRouteObserver == nil else { return }
        let session = AVAudioSession.sharedInstance()
        let connected = Self.hasPersonalAudioRoute(session)
        hearingDeviceConnected = connected
        hearingDeviceStatus = connected
            ? "AirPods or hearing device connected · Standby"
            : "No AirPods or hearing device · Rest Mode starting"

        audioRouteObserver = NotificationCenter.default.addObserver(
            forName: AVAudioSession.routeChangeNotification,
            object: session,
            queue: .main
        ) { [weak self] notification in
            Task { @MainActor [weak self] in
                await self?.handleHearingDeviceRouteChange(notification)
            }
        }
        if #available(iOS 26.0, *) {
            audioInputsObserver = NotificationCenter.default.addObserver(
                forName: AVAudioSession.availableInputsChangeNotification,
                object: session,
                queue: .main
            ) { [weak self] notification in
                Task { @MainActor [weak self] in
                    await self?.handleHearingDeviceRouteChange(notification)
                }
            }
        }

        // If Hearo launches while the AirPods are already disconnected, treat
        // that as the same state as removing them after launch.
        if !connected {
            Task { await startOrResume() }
        }
    }

    private func handleHearingDeviceRouteChange(_ notification: Notification) async {
        if notification.name == AVAudioSession.routeChangeNotification {
            guard let rawReason = notification.userInfo?[AVAudioSessionRouteChangeReasonKey] as? UInt,
                  let reason = AVAudioSession.RouteChangeReason(rawValue: rawReason),
                  reason == .newDeviceAvailable || reason == .oldDeviceUnavailable else {
                return
            }
        } else if #available(iOS 26.0, *),
                  notification.name == AVAudioSession.availableInputsChangeNotification {
            // availableInputs is empty when the app's input session is inactive;
            // that transition is not evidence that the AirPods disconnected.
            guard AVAudioSession.sharedInstance().availableInputs != nil else { return }
        } else {
            return
        }
        let session = AVAudioSession.sharedInstance()
        let connected = Self.hasPersonalAudioRoute(session)
        guard connected != hearingDeviceConnected else { return }
        hearingDeviceConnected = connected

        if connected {
            hearingDeviceStatus = "AirPods or hearing device connected · Ending Rest Mode"
            if activeSession != nil { await finishRestMode() }
        } else {
            hearingDeviceStatus = "AirPods or hearing device disconnected · Starting Rest Mode"
            if activeSession == nil { await startOrResume() }
        }
    }

    private static func hasPersonalAudioRoute(_ session: AVAudioSession) -> Bool {
        let hasHeadphoneOutput = session.currentRoute.outputs.contains { output in
            switch output.portType {
            case .bluetoothA2DP, .bluetoothHFP, .bluetoothLE, .headphones:
                return true
            default:
                return false
            }
        }
        let hasBluetoothMic = session.availableInputs?.contains { $0.portType == .bluetoothHFP } ?? false
        return hasHeadphoneOutput || hasBluetoothMic
    }

    func startOrResume() async {
        isBusy = true
        defer { isBusy = false }
        do {
            errorMessage = nil
            if activeSession == nil {
                activeSession = try await api.send("/rest-mode/start", method: "POST")
            }
            try await audio.start(api: api)
            audioIsRunning = true
            liveTranscript = ""
        } catch {
            errorMessage = error.localizedDescription
            if activeSession != nil && !audioIsRunning {
                let _: RestSession? = try? await api.send("/rest-mode/stop", method: "POST")
                activeSession = nil
                await load()
            }
        }
    }

    func finishRestMode() async {
        guard activeSession != nil else { return }
        isBusy = true
        defer { isBusy = false }
        await audio.stop()
        audioIsRunning = false
        do {
            let ended: RestSession = try await api.send("/rest-mode/stop", method: "POST")
            activeSession = nil
            latestSession = ended
            liveTranscript = ""
            await load()
        } catch {
            errorMessage = error.localizedDescription
        }
    }

    func clearAlerts() async {
        do {
            _ = try await api.clearAlerts()
            alerts = []
            errorMessage = nil
        } catch {
            errorMessage = error.localizedDescription
        }
    }

    func addSounds(_ ids: Set<String>, includeName: Bool) async {
        do {
            let existingIDs = Set(watchlist.flatMap(\.presetSounds))
            let selectedExisting = watchlist.filter { !$0.enabled && !$0.presetSounds.isEmpty && !Set($0.presetSounds).isDisjoint(with: ids) }
            for item in selectedExisting { await toggle(item) }
            let newIDs = ids.subtracting(existingIDs)
            if !newIDs.isEmpty {
                let item = WatchlistCreate(text: "", kind: "any", presetSounds: newIDs.sorted(), expiresAt: nil)
                let _: WatchlistItem = try await api.send("/watchlist", method: "POST", body: item)
            }
            let existingName = watchlist.first { $0.kind == "name" && $0.text.caseInsensitiveCompare(displayFirstName) == .orderedSame }
            if includeName, let existingName {
                if !existingName.enabled { await toggle(existingName) }
            } else if includeName {
                let nameItem = WatchlistCreate(text: displayFirstName, kind: "name", presetSounds: [], expiresAt: nil)
                let _: WatchlistItem = try await api.send("/watchlist", method: "POST", body: nameItem)
            }
            await load()
        } catch {
            errorMessage = error.localizedDescription
        }
    }

    func addGeneric(_ text: String) async {
        do {
            let item = WatchlistCreate(text: text, kind: "any", presetSounds: [], expiresAt: nil)
            let _: WatchlistItem = try await api.send("/watchlist", method: "POST", body: item)
            await load()
        } catch {
            errorMessage = error.localizedDescription
        }
    }

    func toggle(_ item: WatchlistItem) async {
        do {
            let _: WatchlistUpdateResponse = try await api.send(
                "/watchlist/\(item.id)",
                method: "PATCH",
                body: ToggleRequest(enabled: !item.enabled)
            )
            await load()
        } catch {
            errorMessage = error.localizedDescription
        }
    }

    func remove(_ item: WatchlistItem) async {
        do {
            let _: WatchlistUpdateResponse = try await api.send("/watchlist/\(item.id)", method: "DELETE")
            await load()
        } catch {
            errorMessage = error.localizedDescription
        }
    }

    func soundTitle(_ identifier: String) -> String {
        if let preset = presets.first(where: { $0.id == identifier }) { return preset.title }
        let label = classifierLabels.first { normalize($0) == normalize(identifier) } ?? identifier
        return label.replacingOccurrences(of: "_", with: " ").capitalized
    }

    private func handleSound(label: String, confidence: Double) async {
        guard confidence >= 0.45 else { return }
        let normalized = normalize(label)
        let selectedIDs = Set(watchlist.filter { $0.enabled }.flatMap(\.presetSounds))
        let matches = selectedIDs.contains { normalize($0) == normalized } || presets.filter { selectedIDs.contains($0.id) }.contains { preset in
            normalize(preset.id) == normalized || preset.appleClassifications.contains { normalize($0) == normalized }
        }
        guard matches else { return }
        let now = Date()
        if let last = lastSoundAt[normalized], now.timeIntervalSince(last) < 8 { return }
        lastSoundAt[normalized] = now
        do {
            try await api.postSound(SoundEventRequest(label: label, confidence: confidence))
        } catch {
            errorMessage = error.localizedDescription
        }
    }

    private func normalize(_ label: String) -> String {
        label.lowercased().replacingOccurrences(of: "_", with: " ").replacingOccurrences(of: "-", with: " ")
    }

    private func alertConnectionLoop() async {
        while !Task.isCancelled {
            do {
                let task = URLSession.shared.webSocketTask(with: try api.alertsSocketURL())
                task.resume()
                while !Task.isCancelled {
                    let message = try await task.receive()
                    guard case .string(let text) = message, let data = text.data(using: .utf8) else { continue }
                    let alert = try JSONDecoder.hearo.decode(AlertItem.self, from: data)
                    if !alerts.contains(where: { $0.id == alert.id && $0.timestamp == alert.timestamp }) {
                        alerts.insert(alert, at: 0)
                        alerts = Array(alerts.prefix(100))
                        WatchAlertBridge.shared.deliver(alert)
                    }
                }
                task.cancel(with: .goingAway, reason: nil)
            } catch {
                if !Task.isCancelled {
                    try? await Task.sleep(for: .seconds(2))
                    await load()
                }
            }
        }
    }
}

struct WatchlistUpdateResponse: Decodable {
    let success: Bool
}
