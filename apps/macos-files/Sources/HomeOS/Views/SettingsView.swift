import SwiftUI

struct SettingsView: View {
    @EnvironmentObject private var appState: AppState
    @EnvironmentObject private var connection: ConnectionManager
    @EnvironmentObject private var fileProviderDomain: FileProviderDomainService

    @State private var serverURL = ""
    @State private var localServerURL = ""
    @State private var preferLocalServer = true
    @State private var username = ""
    @State private var password = ""
    @State private var status = ""
    @State private var isLoading = false
    @State private var localTestStatus = ""
    @State private var isTestingLocalURL = false
    @State private var notificationStatus = NotificationAuthorizationState.notRequested

    var body: some View {
        Form {
            Section("Server") {
                TextField("Domain URL", text: $serverURL, prompt: Text("https://home.example.com"))
                    .textFieldStyle(.roundedBorder)
                TextField("Local URL", text: $localServerURL, prompt: Text("Auto-discovered, or enter https://192.168.1.20"))
                    .textFieldStyle(.roundedBorder)
                HStack {
                    Button {
                        Task { await testLocalURL() }
                    } label: {
                        if isTestingLocalURL {
                            ProgressView()
                                .controlSize(.small)
                        } else {
                            Text("Test Local URL")
                        }
                    }
                    .buttonStyle(.bordered)
                    .disabled(localServerURL.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty || isTestingLocalURL)

                    if !localTestStatus.isEmpty {
                        Text(localTestStatus)
                            .font(.caption)
                            .foregroundStyle(localTestStatus.hasPrefix("Local server reachable") ? .green : .orange)
                            .textSelection(.enabled)
                    }
                }
                Toggle("Use local network when reachable", isOn: $preferLocalServer)
                LabeledContent("Status") {
                    if connection.isConnecting {
                        Label("Checking…", systemImage: "arrow.triangle.2.circlepath")
                            .foregroundStyle(.orange)
                    } else {
                        Label(connection.isConnected ? "Connected" : "Disconnected", systemImage: connection.isConnected ? "checkmark.circle.fill" : "xmark.circle.fill")
                            .foregroundStyle(connection.isConnected ? .green : .secondary)
                    }
                }
                if appState.isConfigured {
                    ConnectionRouteIndicator(
                        isConnecting: connection.isConnecting,
                        isConnected: connection.isConnected,
                        activeKind: appState.activeConnectionKind,
                        activeURL: appState.activeServerURL,
                        domainURL: appState.serverURL
                    )
                }
                if let error = connection.lastError, !error.isEmpty {
                    Text(error)
                        .font(.caption)
                        .foregroundStyle(.orange)
                        .textSelection(.enabled)
                }
            }

            Section("Login") {
                TextField("Username", text: $username)
                    .textFieldStyle(.roundedBorder)
                SecureField("Password", text: $password)
                    .textFieldStyle(.roundedBorder)

                Button {
                    Task { await login() }
                } label: {
                    if isLoading { ProgressView().controlSize(.small) }
                    else { Text("Connect & Login") }
                }
                .buttonStyle(.borderedProminent)
                .disabled(serverURL.isEmpty || username.isEmpty || password.isEmpty || isLoading)

                if !status.isEmpty {
                    Text(status)
                        .font(.caption)
                        .foregroundStyle(status.hasPrefix("Error") ? .red : .green)
                }
            }

            if appState.isConfigured {
                Section("Notifications") {
                    Toggle("Transfer and connection notifications", isOn: $appState.notificationsEnabled)
                        .onChange(of: appState.notificationsEnabled) { _, enabled in
                            guard enabled else { return }
                            Task { await enableNotifications() }
                        }

                    Text(notificationDescription)
                        .font(.caption)
                        .foregroundStyle(notificationStatus == .denied ? .orange : .secondary)
                }

                Section("Finder Folder") {
                    LabeledContent("Home OS Drive") {
                        VStack(alignment: .trailing, spacing: 4) {
                            Label(fileProviderDomain.state.title, systemImage: fileProviderIcon)
                                .foregroundStyle(fileProviderColor)
                            if let detail = fileProviderDomain.state.detail {
                                Text(detail)
                                    .font(.caption)
                                    .foregroundStyle(.secondary)
                                    .multilineTextAlignment(.trailing)
                                    .textSelection(.enabled)
                            }
                        }
                    }

                    Text("The Desktop item is only a shortcut to the Home OS Finder drive. Files remain managed by the iCloud-like File Provider instead of a separate Desktop sync folder.")
                        .font(.caption)
                        .foregroundStyle(.secondary)

                    Grid(alignment: .leading, horizontalSpacing: 12, verticalSpacing: 10) {
                        GridRow {
                        Button("Enable Finder Integration") {
                            Task { await fileProviderDomain.install(using: appState, requestDesktopShortcutAccess: true) }
                        }
                        .buttonStyle(.borderedProminent)
                        .disabled(!appState.isConfigured || fileProviderDomain.state == .installing)

                        Button("Refresh Finder Folder") {
                            Task {
                                appState.saveSharedSettings()
                                await fileProviderDomain.refreshRemoteChanges(
                                    using: connection.client,
                                    username: appState.username
                                )
                            }
                        }
                        .buttonStyle(.bordered)
                        .disabled(!appState.isConfigured || fileProviderDomain.state == .installing)
                        }

                        GridRow {
                        Button("Open in Finder") {
                            Task { await fileProviderDomain.openInFinder(using: appState) }
                        }
                        .buttonStyle(.bordered)
                        .disabled(!appState.isConfigured || fileProviderDomain.state == .installing)

                        Button("Create Desktop Shortcut") {
                            Task { await fileProviderDomain.createDesktopShortcut(using: appState) }
                        }
                        .buttonStyle(.bordered)
                        .disabled(!appState.isConfigured || fileProviderDomain.state == .installing)
                        }

                        GridRow {
                        Button("Open macOS Settings") {
                            fileProviderDomain.openFileProviderSettings()
                        }
                        .buttonStyle(.bordered)

                        Button("Reset Finder Integration") {
                            Task { await fileProviderDomain.reset(using: appState, requestDesktopShortcutAccess: true) }
                        }
                        .buttonStyle(.bordered)
                        .disabled(!appState.isConfigured || fileProviderDomain.state == .installing)
                        }

                        GridRow {
                        Button("Remove Finder Integration", role: .destructive) {
                            Task { await fileProviderDomain.remove() }
                        }
                        .buttonStyle(.bordered)
                        .disabled(fileProviderDomain.state == .installing)
                            Color.clear
                        }
                    }
                }

                Section("Account") {
                    LabeledContent("User", value: appState.username)
                    LabeledContent("Role", value: appState.userRole)
                    Button("Disconnect", role: .destructive) {
                        connection.disconnect()
                        appState.clearSession()
                        password = ""
                        status = "Disconnected."
                    }
                }
            }
        }
        .formStyle(.grouped)
        .frame(width: 620, height: 620)
        .onAppear {
            appState.restoreSharedSessionIfNeeded()
            serverURL = appState.serverURL
            localServerURL = appState.localServerURL
            preferLocalServer = appState.preferLocalServer
            username = appState.username
            Task {
                connection.restoreSession(from: appState)
                await fileProviderDomain.ensureInstalled(using: appState)
                notificationStatus = await AppNotificationService.shared.authorizationState()
                if notificationStatus == .denied {
                    appState.notificationsEnabled = false
                }
            }
        }
    }

    private var notificationDescription: String {
        switch notificationStatus {
        case .notRequested:
            "Get notified when transfers finish or an established server connection is lost."
        case .enabled:
            "Notifications are enabled for completed transfers, failures, and connection warnings."
        case .denied:
            "Notifications are blocked in macOS System Settings."
        }
    }

    private func enableNotifications() async {
        let granted = await AppNotificationService.shared.requestAuthorization()
        notificationStatus = await AppNotificationService.shared.authorizationState()
        if !granted {
            appState.notificationsEnabled = false
        }
    }

    private func login() async {
        isLoading = true
        status = ""
        do {
            let (response, endpoint) = try await connection.login(
                domainURL: serverURL,
                localURL: localServerURL,
                preferLocal: preferLocalServer,
                username: username,
                password: password
            )
            if response.ok, let data = response.data {
                appState.saveSession(
                    serverURL: serverURL,
                    localServerURL: localServerURL,
                    preferLocalServer: preferLocalServer,
                    token: data.token,
                    user: data.user
                )
                appState.activeServerURL = endpoint.url
                appState.activeConnectionKind = endpoint.kind.rawValue
                connection.connect(appState: appState, token: data.token)
                await fileProviderDomain.ensureInstalled(using: appState)
                password = ""
                status = "Connected via \(endpoint.displayName)."
            } else {
                status = "Error: \(response.error ?? "Invalid credentials")"
            }
        } catch {
            status = "Error: \(error.localizedDescription)"
        }
        isLoading = false
    }

    private func testLocalURL() async {
        isTestingLocalURL = true
        localTestStatus = ""
        do {
            let endpoint = try await connection.testLocalServerURL(localServerURL)
            localServerURL = endpoint.url
            localTestStatus = "Local server reachable: \(endpoint.url)"
            if appState.isConfigured {
                appState.localServerURL = endpoint.url
                appState.preferLocalServer = preferLocalServer
                appState.saveSharedSettings()
                connection.connect(appState: appState)
                try? await fileProviderDomain.signalRootChanged()
            }
        } catch {
            localTestStatus = "Local test failed: \(error.localizedDescription)"
        }
        isTestingLocalURL = false
    }

    private var fileProviderIcon: String {
        switch fileProviderDomain.state {
        case .enabled:
            "externaldrive.fill.badge.checkmark"
        case .installing:
            "arrow.triangle.2.circlepath"
        case .failed, .unavailable:
            "externaldrive.fill.badge.xmark"
        case .disabled:
            "externaldrive"
        }
    }

    private var fileProviderColor: Color {
        switch fileProviderDomain.state {
        case .enabled:
            .green
        case .installing:
            .orange
        case .failed, .unavailable:
            .red
        case .disabled:
            .secondary
        }
    }
}

private struct ConnectionRouteIndicator: View {
    let isConnecting: Bool
    let isConnected: Bool
    let activeKind: String
    let activeURL: String
    let domainURL: String

    var body: some View {
        LabeledContent("Connection route") {
            VStack(alignment: .trailing, spacing: 4) {
                Label(title, systemImage: systemImage)
                    .foregroundStyle(color)
                Text(displayURL)
                    .font(.caption)
                    .foregroundStyle(.secondary)
                    .lineLimit(1)
                    .textSelection(.enabled)
            }
        }
    }

    private var title: String {
        if isConnecting { return "Checking route…" }
        guard isConnected else { return "Not connected" }
        if activeKind == ConnectionEndpoint.Kind.local.rawValue { return "Connected locally" }
        if activeKind == ConnectionEndpoint.Kind.remote.rawValue { return "Connected through domain" }
        return "Connected"
    }

    private var systemImage: String {
        if isConnecting { return "arrow.triangle.2.circlepath" }
        guard isConnected else { return "wifi.slash" }
        if activeKind == ConnectionEndpoint.Kind.local.rawValue { return "house.and.flag.fill" }
        if activeKind == ConnectionEndpoint.Kind.remote.rawValue { return "globe" }
        return "checkmark.circle.fill"
    }

    private var color: Color {
        if isConnecting { return .orange }
        guard isConnected else { return .secondary }
        if activeKind == ConnectionEndpoint.Kind.local.rawValue { return .green }
        if activeKind == ConnectionEndpoint.Kind.remote.rawValue { return .blue }
        return .green
    }

    private var displayURL: String {
        if !activeURL.isEmpty { return activeURL }
        if !domainURL.isEmpty { return domainURL }
        return "No active server"
    }
}
