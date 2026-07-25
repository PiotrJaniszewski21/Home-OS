import SwiftUI

@MainActor
final class AppState: ObservableObject {
    @AppStorage("serverURL") var serverURL: String = ""
    @AppStorage("localServerURL") var localServerURL: String = ""
    @AppStorage("preferLocalServer") var preferLocalServer: Bool = true
    @Published var authToken: String = ""
    @AppStorage("username") var username: String = ""
    @AppStorage("userRole") var userRole: String = ""
    @AppStorage("notificationsEnabled") var notificationsEnabled: Bool = false

    @Published var activeServerURL: String = ""
    @Published var activeConnectionKind: String = ""
    @Published var metrics: MetricsData?
    @Published var storage: StorageData?
    @Published var statusMessage: String?

    var isConfigured: Bool {
        !serverURL.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty && !authToken.isEmpty
    }

    var activeConnectionDescription: String {
        guard !activeServerURL.isEmpty else { return serverURL }
        guard !activeConnectionKind.isEmpty else { return activeServerURL }
        return "\(activeConnectionKind): \(activeServerURL)"
    }

    func saveSession(serverURL: String, localServerURL: String, preferLocalServer: Bool, token: String, user: UserInfo) {
        if self.serverURL != serverURL || username != user.username {
            HomeOSFileProviderIdentityStore.shared.clear()
            HomeOSFileProviderSnapshotStore.shared.clear()
        }
        self.serverURL = serverURL.trimmingCharacters(in: .whitespacesAndNewlines)
        self.localServerURL = localServerURL.trimmingCharacters(in: .whitespacesAndNewlines)
        self.preferLocalServer = preferLocalServer
        self.authToken = token
        self.username = user.username
        self.userRole = user.role
        saveSharedSettings()
    }

    func saveSharedSettings() {
        HomeOSSharedSettings.save(
            serverURL: serverURL,
            localServerURL: localServerURL,
            preferLocalServer: preferLocalServer,
            authToken: authToken,
            username: username
        )
    }

    func restoreSharedSessionIfNeeded() {
        if isConfigured {
            saveSharedSettings()
            return
        }

        let sharedSettings = HomeOSSharedSettings.load()
        guard sharedSettings.isConfigured else { return }

        serverURL = sharedSettings.serverURL
        localServerURL = sharedSettings.localServerURL
        preferLocalServer = sharedSettings.preferLocalServer
        authToken = sharedSettings.authToken
        username = sharedSettings.username
    }

    func clearSession() {
        authToken = ""
        username = ""
        userRole = ""
        activeServerURL = ""
        activeConnectionKind = ""
        metrics = nil
        storage = nil
        statusMessage = nil
        HomeOSSharedSettings.clear()
        HomeOSFileProviderIdentityStore.shared.clear()
        HomeOSFileProviderSnapshotStore.shared.clear()
    }
}
