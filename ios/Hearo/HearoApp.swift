import SwiftUI

@main
struct HearoApp: App {
    @StateObject private var model = HearoModel()

    var body: some Scene {
        WindowGroup {
            ContentView(model: model)
        }
    }
}
