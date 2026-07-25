import Foundation
import OSLog

@MainActor
final class FileTransferActivityMonitor: ObservableObject {
    @Published private(set) var activities: [FileTransferActivity] = []

    private var pollingTask: Task<Void, Never>?
    private let logger = Logger(subsystem: "uk.co.petershomenet.homeos", category: "transfer-monitor")

    init() {
        start()
    }

    func start() {
        guard pollingTask == nil else { return }
        pollingTask = Task { [weak self] in
            while !Task.isCancelled {
                self?.refresh()
                try? await Task.sleep(for: .milliseconds(400))
            }
        }
        logger.info("Transfer progress monitor started")
    }

    func stop() {
        pollingTask?.cancel()
        pollingTask = nil
        activities.removeAll()
    }

    func receive(_ event: HomeOSTransferProgressBridge.Event) {
        switch event.phase {
        case .started, .updated:
            let activity = FileTransferActivity(
                id: event.identifier,
                filename: event.filename,
                kind: event.kind,
                fractionCompleted: event.fractionCompleted
            )
            if let index = activities.firstIndex(where: { $0.id == event.identifier }) {
                activities[index] = activity
            } else {
                activities.append(activity)
            }
        case .finished:
            activities.removeAll { $0.id == event.identifier }
        }
    }

    private func refresh() {
        let updatedActivities = HomeOSTransferProgressBridge.loadActiveEvents().map { event in
            FileTransferActivity(
                id: event.identifier,
                filename: event.filename,
                kind: event.kind,
                fractionCompleted: event.fractionCompleted
            )
        }
        if updatedActivities != activities {
            activities = updatedActivities
            logger.debug("Active transfers changed: \(updatedActivities.count, privacy: .public)")
        }
    }
}
