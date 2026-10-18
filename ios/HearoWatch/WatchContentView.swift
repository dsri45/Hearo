import SwiftUI

struct WatchContentView: View {
    @ObservedObject var alerts: WatchAlertStore

    var body: some View {
        let hasAlert = alerts.latestTitle != nil

        VStack(spacing: 10) {
            Image(systemName: "ear")
                .font(.system(size: 26))
                .foregroundStyle(hasAlert ? .red : .blue)
            Text("LATEST ALERT")
                .font(.caption2.weight(.semibold))
                .foregroundStyle(hasAlert ? .red : .secondary)
            if let title = alerts.latestTitle {
                Text(title)
                    .font(.headline)
                    .foregroundStyle(.red)
                    .multilineTextAlignment(.center)
                    .lineLimit(3)
                    .minimumScaleFactor(0.75)
                if alerts.latestTimestamp > 0 {
                    Text(Date(timeIntervalSince1970: alerts.latestTimestamp), style: .time)
                        .font(.caption2)
                        .foregroundStyle(.secondary)
                }
            } else {
                Text("Waiting for alerts")
                    .font(.headline)
                    .multilineTextAlignment(.center)
            }
        }
        .padding(.horizontal, 8)
    }
}
