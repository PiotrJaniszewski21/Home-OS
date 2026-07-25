import Foundation
import OSLog
import Security

enum HomeOSTransferProgressBridge {
    enum Phase: String, Sendable {
        case started
        case updated
        case finished
    }

    struct Event: Equatable, Sendable {
        let identifier: UUID
        let kind: FileTransferActivity.Kind
        let filename: String
        let fractionCompleted: Double
        let phase: Phase
    }

    static let progressIdentifierKey = ProgressUserInfoKey("uk.co.petershomenet.homeos.transfer-identifier")

    private static let logger = Logger(subsystem: "uk.co.petershomenet.homeos", category: "transfer-bridge")
    private static let lock = NSLock()
    nonisolated(unsafe) private static var activeRecords: [UUID: Record] = [:]

    static func reset() {
        lock.withLock {
            activeRecords.removeAll()
            TransferProgressKeychain.save([])
        }
        logger.debug("Transfer progress store reset")
    }

    static func postStarted(identifier: UUID, kind: FileTransferActivity.Kind, filename: String) {
        updateRecord(
            Event(identifier: identifier, kind: kind, filename: filename, fractionCompleted: 0, phase: .started)
        )
    }

    static func postUpdated(progress: Progress, fractionCompleted: Double) {
        guard let event = event(progress: progress, fractionCompleted: fractionCompleted, phase: .updated) else { return }
        updateRecord(event)
    }

    static func postFinished(progress: Progress) {
        guard let event = event(progress: progress, fractionCompleted: 1, phase: .finished) else { return }
        lock.withLock {
            activeRecords[event.identifier] = nil
            TransferProgressKeychain.save(Array(activeRecords.values))
        }
        logger.debug("Transfer finished: \(rawValue(event.kind), privacy: .public)")
    }

    static func loadActiveEvents() -> [Event] {
        let staleCutoff = Date().addingTimeInterval(-120)
        return TransferProgressKeychain.load()
            .filter { $0.updatedAt >= staleCutoff }
            .map { record in
                Event(
                    identifier: record.identifier,
                    kind: kind(rawValue: record.kind) ?? .download,
                    filename: record.filename,
                    fractionCompleted: min(max(record.fractionCompleted, 0), 1),
                    phase: .updated
                )
            }
    }

    private static func updateRecord(_ event: Event) {
        let record = Record(
            identifier: event.identifier,
            kind: rawValue(event.kind),
            filename: String(event.filename.prefix(255)),
            fractionCompleted: min(max(event.fractionCompleted, 0), 1),
            updatedAt: Date()
        )
        lock.withLock {
            activeRecords[event.identifier] = record
            TransferProgressKeychain.save(Array(activeRecords.values))
        }
        logger.debug("Transfer progress stored: \(rawValue(event.kind), privacy: .public) \(event.fractionCompleted, privacy: .public)")
    }

    private static func event(progress: Progress, fractionCompleted: Double, phase: Phase) -> Event? {
        guard
            let rawIdentifier = progress.userInfo[progressIdentifierKey] as? String,
            let identifier = UUID(uuidString: rawIdentifier),
            let kind = transferKind(progress.fileOperationKind),
            let filename = progress.fileURL?.lastPathComponent,
            !filename.isEmpty
        else { return nil }

        return Event(
            identifier: identifier,
            kind: kind,
            filename: filename,
            fractionCompleted: min(max(fractionCompleted, 0), 1),
            phase: phase
        )
    }

    private static func transferKind(_ operation: Progress.FileOperationKind?) -> FileTransferActivity.Kind? {
        switch operation {
        case .uploading:
            .upload
        case .downloading, .receiving, .decompressingAfterDownloading:
            .download
        default:
            nil
        }
    }

    private static func rawValue(_ kind: FileTransferActivity.Kind) -> String {
        switch kind {
        case .upload: "upload"
        case .download: "download"
        }
    }

    private static func kind(rawValue: String) -> FileTransferActivity.Kind? {
        switch rawValue {
        case "upload": .upload
        case "download": .download
        default: nil
        }
    }

    fileprivate struct Record: Codable, Sendable {
        let identifier: UUID
        let kind: String
        let filename: String
        let fractionCompleted: Double
        let updatedAt: Date
    }
}

private enum TransferProgressKeychain {
    private static let service = "uk.co.petershomenet.homeos.transfer-progress"
    private static let account = "active-transfers"
    private static let configuredAccessGroup = "HomeOSKeychainAccessGroup"
    private static let fallbackAccessGroup = HomeOSSharedSettings.keychainAccessGroupIdentifier
    private static let logger = Logger(subsystem: "uk.co.petershomenet.homeos", category: "transfer-keychain")

    static func load() -> [HomeOSTransferProgressBridge.Record] {
        for accessGroup in accessGroups {
            var query = baseQuery(accessGroup: accessGroup)
            query[kSecReturnData as String] = true
            query[kSecMatchLimit as String] = kSecMatchLimitOne

            var item: CFTypeRef?
            let status = SecItemCopyMatching(query as CFDictionary, &item)
            guard status != errSecItemNotFound else { continue }
            guard status == errSecSuccess, let data = item as? Data else {
                logger.error("Transfer keychain load failed, status: \(Int(status), privacy: .public)")
                continue
            }
            if let records = try? JSONDecoder().decode([HomeOSTransferProgressBridge.Record].self, from: data) {
                if !records.isEmpty {
                    logger.debug("Loaded active transfer records: \(records.count, privacy: .public)")
                }
                return records
            }
        }
        return []
    }

    static func save(_ records: [HomeOSTransferProgressBridge.Record]) {
        guard let data = try? JSONEncoder().encode(records) else { return }
        for accessGroup in accessGroups {
            let query = baseQuery(accessGroup: accessGroup)
            let update = [kSecValueData as String: data]
            let updateStatus = SecItemUpdate(query as CFDictionary, update as CFDictionary)
            guard updateStatus == errSecItemNotFound else {
                if updateStatus != errSecSuccess {
                    logger.error("Transfer keychain update failed, status: \(Int(updateStatus), privacy: .public)")
                }
                continue
            }

            var add = query
            add[kSecValueData as String] = data
            add[kSecAttrAccessible as String] = kSecAttrAccessibleAfterFirstUnlockThisDeviceOnly
            let addStatus = SecItemAdd(add as CFDictionary, nil)
            if addStatus != errSecSuccess {
                logger.error("Transfer keychain add failed, status: \(Int(addStatus), privacy: .public)")
            }
        }
    }

    private static func baseQuery(accessGroup: String?) -> [String: Any] {
        var query: [String: Any] = [
            kSecClass as String: kSecClassGenericPassword,
            kSecAttrService as String: service,
            kSecAttrAccount as String: account,
        ]
        if let accessGroup {
            query[kSecAttrAccessGroup as String] = accessGroup
        }
        return query
    }

    private static var accessGroups: [String?] {
        var groups: [String?] = []
        if let configured = Bundle.main.object(forInfoDictionaryKey: configuredAccessGroup) as? String {
            let trimmed = configured.trimmingCharacters(in: .whitespacesAndNewlines)
            if !trimmed.isEmpty, !trimmed.contains("$(") {
                groups.append(trimmed)
            }
        }
        if !groups.contains(where: { $0 == fallbackAccessGroup }) {
            groups.append(fallbackAccessGroup)
        }
        return groups
    }
}

private extension NSLock {
    func withLock<T>(_ body: () -> T) -> T {
        lock()
        defer { unlock() }
        return body()
    }
}
