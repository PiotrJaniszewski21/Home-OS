import Foundation
import OSLog

enum AppLog {
    private static let subsystem = Bundle.main.bundleIdentifier ?? "dev.homeos.mac"

    static let connection = Logger(subsystem: subsystem, category: "Connection")
    static let files = Logger(subsystem: subsystem, category: "Files")
    static let notifications = Logger(subsystem: subsystem, category: "Notifications")
}
