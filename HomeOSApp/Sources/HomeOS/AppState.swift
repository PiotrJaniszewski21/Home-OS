import SwiftUI

@MainActor
class AppState: ObservableObject {
    @AppStorage("serverURL") var serverURL: String = ""
    @AppStorage("authToken") var authToken: String = ""
    @AppStorage("username") var username: String = ""

    @Published var isAuthenticated: Bool = false
    @Published var storageInfo: StorageInfo?
    @Published var systemMetrics: SystemMetrics?

    var isConfigured: Bool {
        !serverURL.isEmpty
    }
}

struct StorageInfo {
    let totalGB: Double
    let usedGB: Double
    let freeGB: Double
    var percentUsed: Double { totalGB > 0 ? (usedGB / totalGB) * 100 : 0 }
}

struct SystemMetrics {
    let cpuPercent: Double
    let memoryPercent: Double
    let memoryUsedGB: Double
    let memoryTotalGB: Double
    let uptime: String
}
