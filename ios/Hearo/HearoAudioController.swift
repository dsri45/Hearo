import AVFoundation
import Foundation
import SoundAnalysis

@MainActor
final class HearoAudioController {
    private static let relevantSoundTerms = [
        "alarm clock",
        "baby crying", "baby cry", "infant cry",
        "car horn", "vehicle horn", "honking",
        "doorbell", "door bell", "door chime",
        "knock",
        "screaming", "scream",
        "telephone", "phone ringing"
    ]

    var onTranscript: ((String) -> Void)?
    var onStatus: ((String) -> Void)?
    var onSound: ((String, Double) async -> Void)?

    private let engine = AVAudioEngine()
    private var socket: URLSessionWebSocketTask?
    private var streamTask: Task<Void, Never>?
    private var audioFrameContinuation: AsyncStream<Data>.Continuation?
    private var analyzer: SNAudioStreamAnalyzer?
    private var soundObserver: SoundResultsObserver?
    private var converter: AVAudioConverter?
    private var framePosition: AVAudioFramePosition = 0
    private var stopped = true

    static func knownSoundLabels() throws -> [String] {
        let request = try SNClassifySoundRequest(classifierIdentifier: .version1)
        return request.knownClassifications
            .filter { label in
                let normalized = label.lowercased()
                    .replacingOccurrences(of: "_", with: " ")
                    .replacingOccurrences(of: "-", with: " ")
                guard !normalized.contains("siren"),
                      !normalized.contains("bark"),
                      !normalized.contains("dog") else { return false }
                return relevantSoundTerms.contains { normalized.contains($0) }
            }
            .sorted { $0.localizedCaseInsensitiveCompare($1) == .orderedAscending }
    }

    func start(api: HearoAPI) async throws {
        guard stopped else { return }
        try await requestMicrophonePermission()

        let audioSession = AVAudioSession.sharedInstance()
        try audioSession.setCategory(.playAndRecord, mode: .measurement, options: [.allowBluetooth])
        try audioSession.setPreferredSampleRate(16_000)
        try audioSession.setActive(true)
        guard let builtInMicrophone = audioSession.availableInputs?.first(where: { $0.portType == .builtInMic }) else {
            try? audioSession.setActive(false)
            throw HearoAPIError.server("The iPhone microphone is unavailable.")
        }
        try audioSession.setPreferredInput(builtInMicrophone)

        let input = engine.inputNode
        let inputFormat = input.outputFormat(forBus: 0)
        guard let outputFormat = AVAudioFormat(
            commonFormat: .pcmFormatInt16,
            sampleRate: 16_000,
            channels: 1,
            interleaved: false
        ), let audioConverter = AVAudioConverter(from: inputFormat, to: outputFormat) else {
            throw HearoAPIError.server("Could not convert microphone audio to the backend format.")
        }
        converter = audioConverter
        let streamAnalyzer = SNAudioStreamAnalyzer(format: inputFormat)
        let observer = SoundResultsObserver { [weak self] label, confidence in
            Task { @MainActor [weak self] in
                await self?.onSound?(label, confidence)
            }
        }
        let request = try SNClassifySoundRequest(classifierIdentifier: .version1)
        try streamAnalyzer.add(request, withObserver: observer)
        analyzer = streamAnalyzer
        soundObserver = observer
        framePosition = 0
        let (audioFrames, continuation) = AsyncStream<Data>.makeStream(
            bufferingPolicy: .bufferingNewest(32)
        )
        audioFrameContinuation = continuation

        input.installTap(onBus: 0, bufferSize: 2048, format: inputFormat) { [weak self] buffer, _ in
            guard let self else { return }
            self.analyzer?.analyze(buffer, atAudioFramePosition: self.framePosition)
            self.framePosition += AVAudioFramePosition(buffer.frameLength)
            guard let pcm = self.convert(buffer) else { return }
            self.audioFrameContinuation?.yield(pcm)
        }

        do {
            engine.prepare()
            try engine.start()
            stopped = false
            onStatus?("Listening · iPhone microphone · Connecting audio stream…")
            streamTask = Task { [weak self] in
                await self?.maintainAudioStream(api: api, frames: audioFrames)
            }
        } catch {
            input.removeTap(onBus: 0)
            audioFrameContinuation?.finish()
            audioFrameContinuation = nil
            analyzer = nil
            soundObserver = nil
            converter = nil
            try? audioSession.setActive(false)
            throw error
        }
    }

    func stop() async {
        guard !stopped else { return }
        stopped = true
        engine.inputNode.removeTap(onBus: 0)
        engine.stop()
        analyzer?.completeAnalysis()
        analyzer?.removeAllRequests()
        analyzer = nil
        soundObserver = nil
        converter = nil
        if let socket {
            let data = (try? JSONSerialization.data(withJSONObject: ["type": "stop"])) ?? Data("{}".utf8)
            try? await socket.send(.string(String(decoding: data, as: UTF8.self)))
            // Give Deepgram time to return its final utterance before the backend
            // receives the Rest Mode stop request and builds the recap.
            try? await Task.sleep(for: .milliseconds(1200))
        }
        audioFrameContinuation?.finish()
        audioFrameContinuation = nil
        streamTask?.cancel()
        streamTask = nil
        socket?.cancel(with: .normalClosure, reason: nil)
        socket = nil
        try? AVAudioSession.sharedInstance().setActive(false)
        onStatus?("Microphone is off.")
        onTranscript?("")
    }

    private func convert(_ input: AVAudioPCMBuffer) -> Data? {
        guard let converter, let outputFormat = converter.outputFormat as AVAudioFormat? else { return nil }
        let capacity = AVAudioFrameCount(ceil(Double(input.frameLength) * outputFormat.sampleRate / input.format.sampleRate)) + 64
        guard let output = AVAudioPCMBuffer(pcmFormat: outputFormat, frameCapacity: capacity) else { return nil }
        var suppliedInput = false
        var conversionError: NSError?
        let status = converter.convert(to: output, error: &conversionError) { _, inputStatus in
            if suppliedInput {
                inputStatus.pointee = .noDataNow
                return nil
            }
            suppliedInput = true
            inputStatus.pointee = .haveData
            return input
        }
        guard status != .error, output.frameLength > 0, let samples = output.int16ChannelData?[0] else { return nil }
        return Data(bytes: samples, count: Int(output.frameLength) * MemoryLayout<Int16>.size)
    }

    private func maintainAudioStream(api: HearoAPI, frames: AsyncStream<Data>) async {
        while !Task.isCancelled && !stopped {
            do {
                let task = URLSession.shared.webSocketTask(with: try api.audioSocketURL())
                socket = task
                task.resume()
                let startMessage: [String: Any] = [
                    "type": "start",
                    "encoding": "linear16",
                    "sample_rate": 16000,
                    "channels": 1
                ]
                let startData = try JSONSerialization.data(withJSONObject: startMessage)
                try await task.send(.string(String(decoding: startData, as: UTF8.self)))

                guard case .string(let readyText) = try await task.receive(),
                      let readyData = readyText.data(using: .utf8),
                      let ready = try JSONSerialization.jsonObject(with: readyData) as? [String: Any] else {
                    throw HearoAPIError.server("The audio service did not become ready.")
                }
                if let error = ready["detail"] as? String, ready["type"] as? String == "error" {
                    throw HearoAPIError.server(error)
                }
                onStatus?("Listening · iPhone microphone · Audio stream connected · Apple Sound Analysis active")

                try await runConnectedStream(task, frames: frames)
                if !stopped && !Task.isCancelled {
                    throw HearoAPIError.server("The audio stream ended unexpectedly.")
                }
            } catch {
                socket?.cancel(with: .goingAway, reason: nil)
                socket = nil
                guard !stopped && !Task.isCancelled else { break }
                onStatus?("Audio stream disconnected. Reconnecting…")
                try? await Task.sleep(for: .seconds(1))
            }
        }
    }

    private func runConnectedStream(
        _ task: URLSessionWebSocketTask,
        frames: AsyncStream<Data>
    ) async throws {
        try await withThrowingTaskGroup(of: Void.self) { group in
            group.addTask { [weak self] in
                guard let self else { return }
                try await self.sendAudioFrames(frames, to: task)
            }
            group.addTask { [weak self] in
                guard let self else { return }
                try await self.receiveTranscripts(from: task)
            }
            defer { group.cancelAll() }
            _ = try await group.next()
        }
    }

    private func sendAudioFrames(_ frames: AsyncStream<Data>, to task: URLSessionWebSocketTask) async throws {
        for await frame in frames {
            try Task.checkCancellation()
            try await task.send(.data(frame))
        }
    }

    private func receiveTranscripts(from task: URLSessionWebSocketTask) async throws {
        while !Task.isCancelled {
            let message: URLSessionWebSocketTask.Message
            do {
                message = try await task.receive()
            } catch {
                if Task.isCancelled { return }
                throw error
            }
            guard case .string(let value) = message,
                  let data = value.data(using: .utf8),
                  let object = try JSONSerialization.jsonObject(with: data) as? [String: Any] else { continue }
            if object["type"] as? String == "transcript" {
                onTranscript?(object["text"] as? String ?? "")
            } else if object["type"] as? String == "error" {
                throw HearoAPIError.server(object["detail"] as? String ?? "Audio stream error")
            }
        }
    }

    private func requestMicrophonePermission() async throws {
        let session = AVAudioSession.sharedInstance()
        switch session.recordPermission {
        case .granted: return
        case .denied:
            throw HearoAPIError.server("Enable microphone access for Hearo in iPhone Settings.")
        case .undetermined:
            let allowed = await withCheckedContinuation { continuation in
                session.requestRecordPermission { continuation.resume(returning: $0) }
            }
            guard allowed else { throw HearoAPIError.server("Microphone access is required to listen.") }
        @unknown default:
            throw HearoAPIError.server("Could not check microphone permission.")
        }
    }
}

private final class SoundResultsObserver: NSObject, SNResultsObserving {
    private let onClassification: (String, Double) -> Void

    init(onClassification: @escaping (String, Double) -> Void) {
        self.onClassification = onClassification
    }

    func request(_ request: SNRequest, didProduce result: SNResult) {
        guard let result = result as? SNClassificationResult else { return }
        for classification in result.classifications.prefix(3) {
            onClassification(classification.identifier, Double(classification.confidence))
        }
    }

    func request(_ request: SNRequest, didFailWithError error: Error) {
        NSLog("Hearo Sound Analysis failed: %@", error.localizedDescription)
    }

    func requestDidComplete(_ request: SNRequest) {}
}
