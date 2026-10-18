import SwiftUI

private enum HearoColors {
    static let background = Color(red: 0.10, green: 0.10, blue: 0.09)
    static let surface = Color(red: 0.15, green: 0.14, blue: 0.13)
    static let accent = Color(red: 0.48, green: 0.64, blue: 1.0)
    static let secondary = Color(red: 0.71, green: 0.68, blue: 0.64)
}

struct ContentView: View {
    @ObservedObject var model: HearoModel

    var body: some View {
        TabView {
            ListenView(model: model)
                .tabItem { Label("Listen", systemImage: "ear") }
            WatchListView(model: model)
                .tabItem { Label("Watch List", systemImage: "list.bullet") }
            ProfileView(model: model)
                .tabItem { Label("Profile", systemImage: "person.crop.circle") }
        }
        .tint(HearoColors.accent)
        .preferredColorScheme(.dark)
        .task {
            await model.load()
            model.connect()
            WatchAlertBridge.shared.activate()
            WatchAlertBridge.shared.requestNotificationAuthorization()
            model.monitorHearingDeviceRoute()
        }
        .alert("Hearo", isPresented: Binding(
            get: { model.errorMessage != nil },
            set: { if !$0 { model.errorMessage = nil } }
        )) {
            Button("OK", role: .cancel) { model.errorMessage = nil }
        } message: {
            Text(model.errorMessage ?? "")
        }
    }
}

private struct ListenView: View {
    @ObservedObject var model: HearoModel

    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(spacing: 18) {
                    VStack(spacing: 10) {
                        Image(systemName: model.isListening ? "ear.and.waveform" : "ear")
                            .font(.system(size: 50, weight: .light))
                            .foregroundStyle(HearoColors.accent)
                            .frame(width: 112, height: 112)
                            .background(Circle().stroke(HearoColors.accent.opacity(model.isListening ? 0.8 : 0.35), lineWidth: 2))
                        Text(model.activeSession == nil ? "Standby" : (model.isListening ? "Rest Mode" : "Starting Rest Mode"))
                            .font(.largeTitle.weight(.semibold))
                        Text(statusDetail)
                            .font(.subheadline)
                            .foregroundStyle(HearoColors.secondary)
                            .multilineTextAlignment(.center)
                        Button(action: mainAction) {
                            HStack(spacing: 9) {
                                if model.isBusy { ProgressView().tint(.black) }
                                Text(buttonTitle).font(.headline)
                            }
                            .frame(maxWidth: .infinity, minHeight: 58)
                        }
                        .buttonStyle(.borderedProminent)
                        .tint(model.isListening ? Color.gray : HearoColors.accent)
                        .foregroundStyle(.black)
                        .disabled(model.isBusy)
                        Text(model.microphoneStatus)
                            .font(.caption)
                            .foregroundStyle(HearoColors.secondary)
                        Text(model.hearingDeviceStatus)
                            .font(.caption2)
                            .foregroundStyle(HearoColors.secondary)
                        if !model.liveTranscript.isEmpty {
                            Text(model.liveTranscript)
                                .font(.body)
                                .frame(maxWidth: .infinity, alignment: .leading)
                                .padding(14)
                                .background(HearoColors.surface, in: RoundedRectangle(cornerRadius: 14))
                        }
                    }
                    .padding(.horizontal, 20)
                    .padding(.top, 14)

                    VStack(alignment: .leading, spacing: 12) {
                        HStack(alignment: .top) {
                            VStack(alignment: .leading, spacing: 4) {
                                Text("Alerts").font(.title3.weight(.semibold))
                                Text("Events Hearo detected for you.")
                                    .font(.caption)
                                    .foregroundStyle(HearoColors.secondary)
                            }
                            Spacer()
                            Button("Clear") { Task { await model.clearAlerts() } }
                                .font(.subheadline.weight(.semibold))
                                .disabled(model.alerts.isEmpty)
                        }
                        if model.alerts.isEmpty {
                            Text("No alerts yet. Hearo will show them here when it detects something on your Watch List.")
                                .font(.subheadline)
                                .foregroundStyle(HearoColors.secondary)
                        } else {
                            ForEach(model.alerts.prefix(20)) { alert in
                                AlertRow(alert: alert)
                            }
                        }
                    }
                    .padding(18)
                    .background(HearoColors.surface, in: RoundedRectangle(cornerRadius: 18))
                    .padding(.horizontal, 16)

                }
                .padding(.bottom, 24)
            }
            .background(HearoColors.background.ignoresSafeArea())
            .navigationTitle("Hearo")
            .navigationBarTitleDisplayMode(.inline)
        }
    }

    private var statusDetail: String {
        guard let session = model.activeSession else { return "Ready when you take your hearing aids out." }
        let date = Date(timeIntervalSince1970: session.startedAt).formatted(date: .omitted, time: .shortened)
        return "Since \(date) · \(session.eventCount) things heard"
    }

    private var buttonTitle: String {
        if model.activeSession == nil { return "Start Rest Mode" }
        return model.isListening ? "I put my aids back in" : "Resume listening"
    }

    private func mainAction() {
        Task {
            if model.activeSession != nil && model.isListening {
                await model.finishRestMode()
            } else {
                await model.startOrResume()
            }
        }
    }
}

private struct AlertRow: View {
    let alert: AlertItem

    var body: some View {
        VStack(alignment: .leading, spacing: 4) {
            Text(alert.title).font(.headline)
            Text(alert.detail)
                .font(.subheadline)
                .foregroundStyle(HearoColors.secondary)
                .fixedSize(horizontal: false, vertical: true)
            Text("\(Date(timeIntervalSince1970: alert.timestamp).formatted(date: .omitted, time: .shortened)) · \(Int(alert.confidence * 100))% sure")
                .font(.caption2)
                .foregroundStyle(.secondary)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(13)
        .background(HearoColors.background, in: RoundedRectangle(cornerRadius: 12))
    }
}

private struct WatchListView: View {
    @ObservedObject var model: HearoModel
    @State private var mode = 0
    @State private var showingSounds = false
    @State private var showingGeneric = false

    private var visibleItems: [WatchlistItem] {
        model.watchlist.filter { mode == 0 ? $0.isSound : !$0.isSound }
    }

    var body: some View {
        NavigationStack {
            VStack(spacing: 12) {
                Picker("Watch List mode", selection: $mode) {
                    Text("Sounds").tag(0)
                    Text("Generic").tag(1)
                }
                .pickerStyle(.segmented)
                HStack(spacing: 10) {
                    Button(mode == 0 ? "＋ Add sounds" : "＋ Add generic item") {
                        if mode == 0 { showingSounds = true } else { showingGeneric = true }
                    }
                    .buttonStyle(.borderedProminent)
                    .frame(maxWidth: .infinity)
                }
                if visibleItems.isEmpty {
                    ContentUnavailableView(
                        mode == 0 ? "No sounds saved" : "No generic items saved",
                        systemImage: mode == 0 ? "waveform" : "text.magnifyingglass",
                        description: Text("Choose what Hearo should listen for.")
                    )
                    Spacer()
                } else {
                    List {
                        ForEach(visibleItems) { item in
                            WatchlistRow(item: item, model: model)
                                .listRowBackground(HearoColors.surface)
                        }
                    }
                    .listStyle(.insetGrouped)
                }
            }
            .padding(.horizontal, 16)
            .padding(.top, 12)
            .background(HearoColors.background.ignoresSafeArea())
            .navigationTitle("Watch List")
            .sheet(isPresented: $showingSounds) {
                AddSoundsSheet(model: model)
            }
            .sheet(isPresented: $showingGeneric) {
                AddGenericSheet(model: model)
            }
        }
    }
}

private struct WatchlistRow: View {
    let item: WatchlistItem
    @ObservedObject var model: HearoModel

    private var subtitle: String {
        if item.isName { return "Name · Profile name" }
        if item.presetSounds.isEmpty { return "Generic" }
        return item.presetSounds.map(model.soundTitle).joined(separator: ", ")
    }

    var body: some View {
        HStack(spacing: 12) {
            VStack(alignment: .leading, spacing: 4) {
                Text(item.isName ? "Name (\(item.text))" : item.text).font(.headline)
                Text(subtitle).font(.caption).foregroundStyle(HearoColors.secondary)
            }
            Spacer()
            Toggle(item.text, isOn: Binding(
                get: { item.enabled },
                set: { _ in Task { await model.toggle(item) } }
            ))
            .labelsHidden()
            .tint(HearoColors.accent)
            Button(role: .destructive) { Task { await model.remove(item) } } label: {
                Image(systemName: "trash")
            }
            .buttonStyle(.borderless)
        }
        .padding(.vertical, 5)
    }
}

private struct AddSoundsSheet: View {
    @ObservedObject var model: HearoModel
    @Environment(\.dismiss) private var dismiss
    @State private var selected = Set<String>()
    @State private var includeName = false
    @State private var search = ""

    private var visibleLabels: [String] {
        guard !search.isEmpty else { return model.classifierLabels }
        return model.classifierLabels.filter { $0.localizedCaseInsensitiveContains(search) }
    }

    var body: some View {
        NavigationStack {
            List {
                Section("Speech alert") {
                    Toggle("My name (\(model.displayFirstName))", isOn: $includeName)
                    Text("Hearo uses the recognized sentence as the alert summary when your name is heard.")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }
                Section("Important sounds") {
                    Text("Choose alerts you may need to notice without your hearing aids.")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                    TextField("Search sounds", text: $search)
                    ForEach(visibleLabels, id: \.self) { label in
                        Toggle(isOn: Binding(
                            get: { selected.contains(label) },
                            set: { value in
                                if value { selected.insert(label) } else { selected.remove(label) }
                            }
                        )) {
                            Text(label.replacingOccurrences(of: "_", with: " ").capitalized)
                        }
                    }
                    if model.classifierLabels.isEmpty {
                        Text("Apple’s sound labels are unavailable. Open this app on an iPhone with SoundAnalysis support.")
                            .font(.caption)
                            .foregroundStyle(.secondary)
                    }
                }
            }
            .navigationTitle("Add sounds")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) { Button("Cancel") { dismiss() } }
                ToolbarItem(placement: .confirmationAction) {
                    Button("Save") {
                        Task {
                            await model.addSounds(selected, includeName: includeName)
                            dismiss()
                        }
                    }
                    .disabled(selected.isEmpty && !includeName)
                }
            }
        }
        .preferredColorScheme(.dark)
    }
}

private struct AddGenericSheet: View {
    @ObservedObject var model: HearoModel
    @Environment(\.dismiss) private var dismiss
    @State private var text = ""

    var body: some View {
        NavigationStack {
            Form {
                Section("What should Hearo listen for?") {
                    TextField("Flight, order number, announcement…", text: $text, axis: .vertical)
                        .lineLimit(3...6)
                }
                Text("Gemini compares the live transcript with this description.")
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }
            .navigationTitle("Add generic item")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) { Button("Cancel") { dismiss() } }
                ToolbarItem(placement: .confirmationAction) {
                    Button("Save") {
                        Task { await model.addGeneric(text.trimmingCharacters(in: .whitespacesAndNewlines)); dismiss() }
                    }
                    .disabled(text.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
                }
            }
        }
        .preferredColorScheme(.dark)
    }
}

private struct ProfileView: View {
    @ObservedObject var model: HearoModel

    var body: some View {
        NavigationStack {
            Form {
                Section {
                    HStack {
                        Spacer()
                        VStack(spacing: 8) {
                            Image(systemName: "person.fill")
                                .font(.system(size: 34))
                                .foregroundStyle(.white)
                                .frame(width: 88, height: 88)
                                .background(LinearGradient(colors: [HearoColors.accent, Color.indigo], startPoint: .topLeading, endPoint: .bottomTrailing), in: Circle())
                            Text(model.profileName).font(.headline)
                            Text(model.profileEmail).font(.caption).foregroundStyle(.secondary)
                        }
                        Spacer()
                    }
                    .listRowBackground(Color.clear)
                }
                Section("Profile") {
                    LabeledContent("Name", value: model.profileName)
                    LabeledContent("Email", value: model.profileEmail)
                    LabeledContent("Phone", value: model.profilePhone)
                    Text("Profile details are placeholders for now. Name alerts use \(model.displayFirstName).")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }
                Section("Backend connection") {
                    TextField("http://192.168.1.20:8000", text: $model.baseURL)
                        .textInputAutocapitalization(.never)
                        .keyboardType(.URL)
                        .autocorrectionDisabled()
                    Text("On your iPhone, use your Mac’s local network address in place of localhost. Keep the backend running on the same Wi-Fi network.")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }
            }
            .scrollContentBackground(.hidden)
            .background(HearoColors.background)
            .navigationTitle("Profile")
        }
    }
}
