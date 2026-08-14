import Foundation
import OSLog
import Combine

struct PerformanceLogEntry: Identifiable, Codable {
    let id: UUID
    let timestamp: Date
    let category: String
    let message: String
    let durationMs: Double?
    
    init(category: String, message: String, durationMs: Double? = nil) {
        self.id = UUID()
        self.timestamp = Date()
        self.category = category
        self.message = message
        self.durationMs = durationMs
    }

    var formattedTimestamp: String {
        let formatter = DateFormatter()
        formatter.dateFormat = "HH:mm:ss.SSS"
        return formatter.string(from: timestamp)
    }

    var displayText: String {
        let durStr = durationMs.map { String(format: " (%.1fms)", $0) } ?? ""
        return "[\(formattedTimestamp)] [\(category)] \(message)\(durStr)"
    }
}

@MainActor
final class PerformanceLogger: ObservableObject {
    static let shared = PerformanceLogger()
    
    private let logger = Logger(subsystem: "uk.co.petershomenet.homemusic", category: "Performance")
    
    @Published private(set) var logs: [PerformanceLogEntry] = []
    
    private init() {}
    
    func log(_ category: String, _ message: String, durationMs: Double? = nil) {
        let entry = PerformanceLogEntry(category: category, message: message, durationMs: durationMs)
        logs.append(entry)
        if logs.count > 300 {
            logs.removeFirst(logs.count - 300)
        }
        print("⚡ [PERF-LOG] \(entry.displayText)")
        logger.info("\(entry.displayText, privacy: .public)")
    }
    
    func clear() {
        logs.removeAll()
    }
}
