import Foundation

struct SoundPreset: Codable, Identifiable, Hashable {
    let id: String
    let title: String
    let description: String
    let appleClassifications: [String]
}

struct SoundPresetsResponse: Codable {
    let presets: [SoundPreset]
}

struct AlertItem: Codable, Identifiable, Hashable {
    let id: String
    let category: String
    let title: String
    let detail: String
    let confidence: Double
    let timestamp: Double
}

struct AlertsResponse: Codable {
    let alerts: [AlertItem]
}

struct RestRecap: Codable {
    let title: String
    let overview: String
    let importantEvents: [String]
    let conversationsAndAnnouncements: [String]
    let environmentalSounds: [String]
}

struct RestSession: Codable, Identifiable {
    let id: String
    let status: String
    let startedAt: Double
    let endedAt: Double?
    let watchItem: String?
    let eventCount: Int
    let recap: RestRecap?
}

struct RestModeResponse: Codable {
    let active: RestSession?
    let latest: RestSession?
}

struct WatchlistItem: Codable, Identifiable, Hashable {
    let id: String
    let text: String
    let kind: String
    let presetSounds: [String]
    let enabled: Bool
    let createdAt: Double
    let expiresAt: Double?

    var isName: Bool { kind == "name" }
    var isSound: Bool { isName || !presetSounds.isEmpty }
}

struct WatchlistResponse: Codable {
    let items: [WatchlistItem]
}

struct WatchlistCreate: Codable {
    let text: String
    let kind: String
    let presetSounds: [String]
    let expiresAt: Double?
}

struct ToggleRequest: Codable {
    let enabled: Bool
}

struct ClearAlertsResponse: Codable {
    let cleared: Int
}

struct SoundEventRequest: Codable {
    let label: String
    let confidence: Double
    let source: String = "apple_sound_analysis"
}

enum ListenState {
    case standby
    case listening
    case paused
}

extension JSONDecoder {
    static var hearo: JSONDecoder {
        let decoder = JSONDecoder()
        decoder.keyDecodingStrategy = .convertFromSnakeCase
        return decoder
    }
}

extension JSONEncoder {
    static var hearo: JSONEncoder {
        let encoder = JSONEncoder()
        encoder.keyEncodingStrategy = .convertToSnakeCase
        return encoder
    }
}
