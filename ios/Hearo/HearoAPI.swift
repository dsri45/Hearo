import Foundation

enum HearoAPIError: LocalizedError {
    case invalidBaseURL
    case server(String)
    case unexpectedResponse

    var errorDescription: String? {
        switch self {
        case .invalidBaseURL: return "Enter a valid backend URL in Profile."
        case .server(let message): return message
        case .unexpectedResponse: return "Hearo received an unexpected response from the backend."
        }
    }
}

final class HearoAPI {
    var baseURL: String
    private let session: URLSession

    init(baseURL: String, session: URLSession = .shared) {
        self.baseURL = baseURL
        self.session = session
    }

    func get<T: Decodable>(_ path: String) async throws -> T {
        try await request(path, method: "GET", body: Optional<Data>.none)
    }

    func send<Body: Encodable, Response: Decodable>(
        _ path: String,
        method: String,
        body: Body
    ) async throws -> Response {
        let data = try JSONEncoder.hearo.encode(body)
        return try await request(path, method: method, body: data)
    }

    func send<Response: Decodable>(_ path: String, method: String) async throws -> Response {
        try await request(path, method: method, body: Optional<Data>.none)
    }

    func clearAlerts() async throws -> ClearAlertsResponse {
        try await send("/alerts", method: "DELETE")
    }

    func alertsSocketURL() throws -> URL {
        guard var components = URLComponents(string: baseURL.trimmingCharacters(in: .whitespacesAndNewlines)) else {
            throw HearoAPIError.invalidBaseURL
        }
        components.scheme = components.scheme == "https" ? "wss" : "ws"
        components.path = "/ws/alerts"
        components.query = nil
        components.fragment = nil
        guard let url = components.url else { throw HearoAPIError.invalidBaseURL }
        return url
    }

    func audioSocketURL() throws -> URL {
        guard var components = URLComponents(string: baseURL.trimmingCharacters(in: .whitespacesAndNewlines)) else {
            throw HearoAPIError.invalidBaseURL
        }
        components.scheme = components.scheme == "https" ? "wss" : "ws"
        components.path = "/ws/audio"
        components.query = nil
        components.fragment = nil
        guard let url = components.url else { throw HearoAPIError.invalidBaseURL }
        return url
    }

    func postSound(_ event: SoundEventRequest) async throws {
        let _ = try await requestData(
            "/events/sound",
            method: "POST",
            body: try JSONEncoder.hearo.encode(event)
        )
    }

    private func request<Response: Decodable>(
        _ path: String,
        method: String,
        body: Data?
    ) async throws -> Response {
        let data = try await requestData(path, method: method, body: body)
        return try JSONDecoder.hearo.decode(Response.self, from: data)
    }

    private func requestData(_ path: String, method: String, body: Data?) async throws -> Data {
        let rawBase = baseURL.trimmingCharacters(in: .whitespacesAndNewlines)
        guard let root = URL(string: rawBase), let url = URL(string: path, relativeTo: root)?.absoluteURL else {
            throw HearoAPIError.invalidBaseURL
        }
        var request = URLRequest(url: url)
        request.httpMethod = method
        request.timeoutInterval = 20
        if let body {
            request.httpBody = body
            request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        }
        let (data, response) = try await session.data(for: request)
        guard let http = response as? HTTPURLResponse else { throw HearoAPIError.unexpectedResponse }
        guard (200..<300).contains(http.statusCode) else {
            let payload = (try? JSONSerialization.jsonObject(with: data)) as? [String: Any]
            throw HearoAPIError.server(payload?["detail"] as? String ?? "Backend request failed (\(http.statusCode)).")
        }
        return data
    }
}
