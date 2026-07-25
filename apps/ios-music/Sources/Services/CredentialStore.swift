import Foundation
import Security

enum CredentialStore {
    private static let service = "uk.co.petershomenet.homemusic"

    static func save(_ value: String, account: String) throws {
#if targetEnvironment(macCatalyst)
        try CatalystCredentialStore.save(value, account: account)
#else
        let data = Data(value.utf8)
        let query = query(account: account)
        var insert = query
        insert[kSecValueData as String] = data
        insert[kSecAttrAccessible as String] = kSecAttrAccessibleAfterFirstUnlockThisDeviceOnly
        let status = SecItemAdd(insert as CFDictionary, nil)
        if status == errSecSuccess {
            return
        }
        guard status == errSecDuplicateItem else {
            throw KeychainError.status(operation: "save credentials", status: status)
        }

        var update: [String: Any] = [kSecValueData as String: data]
        update[kSecAttrAccessible as String] = kSecAttrAccessibleAfterFirstUnlockThisDeviceOnly
        let updateStatus = SecItemUpdate(query as CFDictionary, update as CFDictionary)
        guard updateStatus == errSecSuccess else {
            throw KeychainError.status(operation: "update credentials", status: updateStatus)
        }
#endif
    }

    static func load(account: String) throws -> String? {
#if targetEnvironment(macCatalyst)
        return try CatalystCredentialStore.load(account: account)
#else
        var query = query(account: account)
        query[kSecReturnData as String] = true
        query[kSecMatchLimit as String] = kSecMatchLimitOne
        var result: CFTypeRef?
        let status = SecItemCopyMatching(query as CFDictionary, &result)
        if status == errSecItemNotFound {
            return nil
        }
        guard status == errSecSuccess else {
            throw KeychainError.status(operation: "read credentials", status: status)
        }
        guard let data = result as? Data else {
            throw KeychainError.invalidData
        }
        return String(data: data, encoding: .utf8)
#endif
    }

    static func clear() throws {
#if targetEnvironment(macCatalyst)
        try CatalystCredentialStore.clear()
#else
        let status = SecItemDelete([
            kSecClass as String: kSecClassGenericPassword,
            kSecAttrService as String: service,
        ] as CFDictionary)
        guard status == errSecSuccess || status == errSecItemNotFound else {
            throw KeychainError.status(operation: "remove credentials", status: status)
        }
#endif
    }

    private static func query(account: String) -> [String: Any] {
        [
            kSecClass as String: kSecClassGenericPassword,
            kSecAttrService as String: service,
            kSecAttrAccount as String: account,
        ]
    }
}

#if targetEnvironment(macCatalyst)
private enum CatalystCredentialStore {
    private static let allowedAccounts = Set(["server", "token"])
    private static let fileManager = FileManager.default

    private static var directoryURL: URL {
        fileManager.urls(for: .applicationSupportDirectory, in: .userDomainMask)[0]
            .appending(path: "HomeMusic", directoryHint: .isDirectory)
    }

    private static var fileURL: URL {
        directoryURL.appending(path: "credentials.json", directoryHint: .notDirectory)
    }

    static func save(_ value: String, account: String) throws {
        guard allowedAccounts.contains(account) else {
            throw CredentialFileError.invalidAccount
        }
        var credentials = try read()
        credentials[account] = value
        try write(credentials)
    }

    static func load(account: String) throws -> String? {
        guard allowedAccounts.contains(account) else {
            throw CredentialFileError.invalidAccount
        }
        return try read()[account]
    }

    static func clear() throws {
        guard fileManager.fileExists(atPath: fileURL.path) else { return }
        do {
            try fileManager.removeItem(at: fileURL)
        } catch {
            throw CredentialFileError.fileOperation("remove credentials", error)
        }
    }

    private static func read() throws -> [String: String] {
        guard fileManager.fileExists(atPath: fileURL.path) else { return [:] }
        do {
            let data = try Data(contentsOf: fileURL)
            return try JSONDecoder().decode([String: String].self, from: data)
        } catch {
            throw CredentialFileError.fileOperation("read credentials", error)
        }
    }

    private static func write(_ credentials: [String: String]) throws {
        do {
            try fileManager.createDirectory(
                at: directoryURL,
                withIntermediateDirectories: true,
                attributes: [.posixPermissions: 0o700]
            )
            let data = try JSONEncoder().encode(credentials)
            try data.write(to: fileURL, options: .atomic)
            try fileManager.setAttributes(
                [.posixPermissions: 0o600],
                ofItemAtPath: fileURL.path
            )
        } catch {
            throw CredentialFileError.fileOperation("save credentials", error)
        }
    }
}

private enum CredentialFileError: LocalizedError {
    case invalidAccount
    case fileOperation(String, Error)

    var errorDescription: String? {
        switch self {
        case .invalidAccount:
            return "HomeMusic rejected an unknown credential type."
        case let .fileOperation(operation, error):
            return "HomeMusic couldn’t \(operation) locally (\(error.localizedDescription))."
        }
    }
}
#endif

enum KeychainError: LocalizedError {
    case invalidData
    case status(operation: String, status: OSStatus)

    var errorDescription: String? {
        switch self {
        case .invalidData:
            return "HomeMusic found damaged sign-in credentials. Sign in again to replace them."
        case let .status(operation, status):
            let detail = SecCopyErrorMessageString(status, nil) as String?
                ?? "Unknown Keychain error"
            return "HomeMusic couldn’t \(operation) securely (\(status): \(detail))."
        }
    }
}
