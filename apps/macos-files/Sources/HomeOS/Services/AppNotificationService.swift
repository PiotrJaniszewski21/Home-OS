import Foundation
import UserNotifications

enum NotificationAuthorizationState: Equatable {
    case notRequested
    case enabled
    case denied
}

actor AppNotificationService {
    static let shared = AppNotificationService()

    private let center = UNUserNotificationCenter.current()

    func authorizationState() async -> NotificationAuthorizationState {
        let settings = await center.notificationSettings()
        switch settings.authorizationStatus {
        case .authorized, .provisional, .ephemeral:
            return .enabled
        case .denied:
            return .denied
        case .notDetermined:
            return .notRequested
        @unknown default:
            return .denied
        }
    }

    func requestAuthorization() async -> Bool {
        do {
            return try await center.requestAuthorization(options: [.alert, .sound])
        } catch {
            AppLog.notifications.error("Notification authorization failed: \(error.localizedDescription, privacy: .public)")
            return false
        }
    }

    func notifyTransfer(title: String, message: String, isFailure: Bool = false) async {
        await send(
            identifier: "\(isFailure ? "transfer-failure" : "transfer")-\(UUID().uuidString)",
            title: title,
            message: message,
            sound: .default
        )
    }

    func notifyConnectionWarning(_ message: String) async {
        await send(
            identifier: "connection-warning",
            title: "Home OS Disconnected",
            message: message,
            sound: .default
        )
    }

    private func send(identifier: String, title: String, message: String, sound: UNNotificationSound) async {
        guard await authorizationState() == .enabled else { return }

        let content = UNMutableNotificationContent()
        content.title = title
        content.body = message
        content.sound = sound

        do {
            try await center.add(UNNotificationRequest(identifier: identifier, content: content, trigger: nil))
        } catch {
            AppLog.notifications.error("Notification delivery failed: \(error.localizedDescription, privacy: .public)")
        }
    }
}
