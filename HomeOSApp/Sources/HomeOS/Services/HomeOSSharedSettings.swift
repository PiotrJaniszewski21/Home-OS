import Foundation
import OSLog
import Security

private let sharedSettingsLogger = Logger(subsystem: "uk.co.petershomenet.homeos", category: "SharedSettings")

struct HomeOSSharedSettings: Codable, Equatable, Sendable {
    static let appGroupIdentifier = "group.uk.co.petershomenet.homeos"
    static let keychainAccessGroupIdentifier = "7S5APW4X6P.uk.co.petershomenet.homeos"
    private static let legacySuiteNames = [
        "7S5APW4X6P.uk.co.petershomenet.homeos",
        "7S5APW4X6P.group.uk.co.petershomenet.homeos",
        "group.com.piotrjaniszewski.homeos",
    ]

    let serverURL: String
    let localServerURL: String
    let preferLocalServer: Bool
    let authToken: String
    let username: String

    var isConfigured: Bool {
        !serverURL.isEmpty && !authToken.isEmpty
    }

    init(
        serverURL: String,
        localServerURL: String,
        preferLocalServer: Bool,
        authToken: String,
        username: String
    ) {
        self.serverURL = serverURL
        self.localServerURL = localServerURL
        self.preferLocalServer = preferLocalServer
        self.authToken = authToken
        self.username = username
    }

    static func load(defaults: UserDefaults? = appGroupDefaults) -> HomeOSSharedSettings {
        let primaryDefaults = defaults
        if let settings = SharedSettingsKeychain.load(), settings.hasStoredValues {
            migrateIfNeeded(settings, primaryDefaults: primaryDefaults)
            logLoaded(settings, source: "keychain")
            return settings
        }

        for candidate in defaultsCandidates(primary: primaryDefaults) {
            let settings = HomeOSSharedSettings(defaults: candidate.defaults)
            guard settings.hasStoredValues else { continue }
            migrateIfNeeded(settings, primaryDefaults: primaryDefaults)
            SharedSettingsKeychain.save(settings)
            logLoaded(settings, source: candidate.label)
            return settings
        }

        sharedSettingsLogger.info("No shared settings found")
        return HomeOSSharedSettings.empty
    }

    static func save(
        serverURL: String,
        localServerURL: String,
        preferLocalServer: Bool,
        authToken: String,
        username: String,
        defaults: UserDefaults? = appGroupDefaults
    ) {
        let settings = HomeOSSharedSettings(
            serverURL: serverURL.trimmingCharacters(in: .whitespacesAndNewlines),
            localServerURL: localServerURL.trimmingCharacters(in: .whitespacesAndNewlines),
            preferLocalServer: preferLocalServer,
            authToken: authToken,
            username: username
        )

        if let defaults = defaults {
            let synchronized = settings.write(to: defaults)
            sharedSettingsLogger.info("Saved shared settings to app-group defaults, synchronized: \(synchronized, privacy: .public), tokenLength: \(settings.authToken.count, privacy: .public)")
        }

        SharedSettingsKeychain.save(settings)
    }

    static func clear(defaults: UserDefaults? = appGroupDefaults) {
        for candidate in defaultsCandidates(primary: defaults) {
            for key in Key.all {
                candidate.defaults.removeObject(forKey: key)
            }
            let synchronized = candidate.defaults.synchronize()
            sharedSettingsLogger.info("Cleared shared settings from \(candidate.label, privacy: .public), synchronized: \(synchronized, privacy: .public)")
        }

        SharedSettingsKeychain.clear()
    }

    private static var empty: HomeOSSharedSettings {
        HomeOSSharedSettings(
            serverURL: "",
            localServerURL: "",
            preferLocalServer: true,
            authToken: "",
            username: ""
        )
    }

    private var hasStoredValues: Bool {
        !serverURL.isEmpty || !localServerURL.isEmpty || !authToken.isEmpty || !username.isEmpty
    }

    private init(defaults: UserDefaults) {
        self.init(
            serverURL: defaults.string(forKey: Key.serverURL) ?? "",
            localServerURL: defaults.string(forKey: Key.localServerURL) ?? "",
            preferLocalServer: defaults.object(forKey: Key.preferLocalServer) as? Bool ?? true,
            authToken: defaults.string(forKey: Key.authToken) ?? "",
            username: defaults.string(forKey: Key.username) ?? ""
        )
    }

    @discardableResult
    private func write(to defaults: UserDefaults) -> Bool {
        defaults.set(serverURL, forKey: Key.serverURL)
        defaults.set(localServerURL, forKey: Key.localServerURL)
        defaults.set(preferLocalServer, forKey: Key.preferLocalServer)
        defaults.set(authToken, forKey: Key.authToken)
        defaults.set(username, forKey: Key.username)
        return defaults.synchronize()
    }

    private static var appGroupDefaults: UserDefaults? {
        return UserDefaults(suiteName: appGroupIdentifier)
    }

    private static func defaultsCandidates(primary: UserDefaults?) -> [(label: String, defaults: UserDefaults)] {
        var candidates: [(label: String, defaults: UserDefaults)] = []
        var suiteNames = Set<String>()

        if let primary {
            candidates.append(("app-group defaults", primary))
        }

        for suiteName in [appGroupIdentifier] + legacySuiteNames {
            guard suiteNames.insert(suiteName).inserted else { continue }
            if let defaults = UserDefaults(suiteName: suiteName) {
                candidates.append(("defaults suite \(suiteName)", defaults))
            }
        }

        candidates.append(("standard defaults", .standard))
        return candidates
    }

    private static func migrateIfNeeded(_ settings: HomeOSSharedSettings, primaryDefaults: UserDefaults?) {
        guard let primaryDefaults else { return }
        let primarySettings = HomeOSSharedSettings(defaults: primaryDefaults)
        guard primarySettings != settings else { return }
        let synchronized = settings.write(to: primaryDefaults)
        sharedSettingsLogger.info("Migrated shared settings to app-group defaults, synchronized: \(synchronized, privacy: .public), tokenLength: \(settings.authToken.count, privacy: .public)")
    }

    private static func logLoaded(_ settings: HomeOSSharedSettings, source: String) {
        sharedSettingsLogger.info("Loaded shared settings from \(source, privacy: .public), configured: \(settings.isConfigured, privacy: .public), tokenLength: \(settings.authToken.count, privacy: .public), preferLocal: \(settings.preferLocalServer, privacy: .public)")
    }

    private enum Key {
        static let serverURL = "serverURL"
        static let localServerURL = "localServerURL"
        static let preferLocalServer = "preferLocalServer"
        static let authToken = "authToken"
        static let username = "username"
        static let all = [serverURL, localServerURL, preferLocalServer, authToken, username]
    }
}

private enum SharedSettingsKeychain {
    private static let service = "uk.co.petershomenet.homeos.shared-settings"
    private static let account = "settings"
    private static let configuredAccessGroup = "HomeOSKeychainAccessGroup"
    private static let fallbackAccessGroup = HomeOSSharedSettings.keychainAccessGroupIdentifier

    static func load() -> HomeOSSharedSettings? {
        for accessGroup in accessGroups {
            var query = baseQuery(accessGroup: accessGroup)
            query[kSecReturnData as String] = true
            query[kSecMatchLimit as String] = kSecMatchLimitOne

            var item: CFTypeRef?
            let status = SecItemCopyMatching(query as CFDictionary, &item)
            guard status != errSecItemNotFound else { continue }
            guard status == errSecSuccess, let data = item as? Data else {
                sharedSettingsLogger.warning("Keychain shared settings load failed from \(accessGroupLabel(accessGroup), privacy: .public), status: \(Int(status), privacy: .public)")
                continue
            }
            do {
                let settings = try JSONDecoder().decode(HomeOSSharedSettings.self, from: data)
                sharedSettingsLogger.info("Keychain shared settings loaded from \(accessGroupLabel(accessGroup), privacy: .public), tokenLength: \(settings.authToken.count, privacy: .public)")
                return settings
            } catch {
                sharedSettingsLogger.warning("Keychain shared settings decode failed from \(accessGroupLabel(accessGroup), privacy: .public): \(error.localizedDescription, privacy: .public)")
            }
        }

        return nil
    }

    static func save(_ settings: HomeOSSharedSettings) {
        guard let data = try? JSONEncoder().encode(settings) else { return }

        var didSave = false
        for accessGroup in accessGroups {
            let status = save(data: data, accessGroup: accessGroup)
            if status == errSecSuccess {
                didSave = true
                sharedSettingsLogger.info("Saved shared settings to keychain \(accessGroupLabel(accessGroup), privacy: .public), tokenLength: \(settings.authToken.count, privacy: .public)")
            } else {
                sharedSettingsLogger.warning("Keychain shared settings save failed for \(accessGroupLabel(accessGroup), privacy: .public), status: \(Int(status), privacy: .public)")
            }
        }

        if !didSave {
            sharedSettingsLogger.error("Could not save shared settings to any keychain access group")
        }
    }

    static func clear() {
        for accessGroup in accessGroups {
            let status = SecItemDelete(baseQuery(accessGroup: accessGroup) as CFDictionary)
            if status == errSecSuccess || status == errSecItemNotFound {
                sharedSettingsLogger.info("Cleared keychain shared settings from \(accessGroupLabel(accessGroup), privacy: .public), status: \(Int(status), privacy: .public)")
            } else {
                sharedSettingsLogger.warning("Keychain shared settings clear failed for \(accessGroupLabel(accessGroup), privacy: .public), status: \(Int(status), privacy: .public)")
            }
        }
    }

    private static func save(data: Data, accessGroup: String?) -> OSStatus {
        let query = baseQuery(accessGroup: accessGroup)
        let update: [String: Any] = [
            kSecValueData as String: data,
        ]
        let updateStatus = SecItemUpdate(query as CFDictionary, update as CFDictionary)

        guard updateStatus == errSecItemNotFound else {
            return updateStatus
        }

        var add = query
        add[kSecValueData as String] = data
        add[kSecAttrAccessible as String] = kSecAttrAccessibleAfterFirstUnlockThisDeviceOnly
        return SecItemAdd(add as CFDictionary, nil)
    }

    private static func baseQuery(accessGroup: String?) -> [String: Any] {
        var query: [String: Any] = [
            kSecClass as String: kSecClassGenericPassword,
            kSecAttrService as String: service,
            kSecAttrAccount as String: account,
        ]

        if let accessGroup = accessGroup {
            query[kSecAttrAccessGroup as String] = accessGroup
        }

        return query
    }

    private static var accessGroups: [String?] {
        var groups: [String?] = []
        var seen = Set<String>()

        func append(_ group: String?) {
            guard let group else {
                groups.append(nil)
                return
            }

            guard seen.insert(group).inserted else { return }
            groups.append(group)
        }

        if let configured = Bundle.main.object(forInfoDictionaryKey: configuredAccessGroup) as? String {
            let trimmed = configured.trimmingCharacters(in: .whitespacesAndNewlines)
            if !trimmed.isEmpty && !trimmed.contains("$(") {
                append(trimmed)
            }
        }

        append(fallbackAccessGroup)
        append(nil)
        return groups
    }

    private static func accessGroupLabel(_ accessGroup: String?) -> String {
        guard let accessGroup else { return "default-app-keychain" }
        if accessGroup == fallbackAccessGroup {
            return "shared-access-group"
        }
        return "configured-access-group"
    }
}
