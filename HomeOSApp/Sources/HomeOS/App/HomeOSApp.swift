import AppKit
import SwiftUI

@main
struct HomeOSApp: App {
    @NSApplicationDelegateAdaptor(AppDelegate.self) private var appDelegate
    @StateObject private var appState = AppState()
    @StateObject private var connectionManager = ConnectionManager()
    @StateObject private var fileProviderDomain = FileProviderDomainService()
    @State private var didHandleLaunchArguments = false
    @Environment(\.openWindow) private var openWindow

    var body: some Scene {
        MenuBarExtra {
            MenuBarView()
                .environmentObject(appState)
                .environmentObject(connectionManager)
                .environmentObject(fileProviderDomain)
        } label: {
            Image(systemName: connectionManager.isConnected ? "externaldrive.fill.badge.checkmark" : "externaldrive.fill.badge.xmark")
                .task(id: appState.authToken) {
                    connectionManager.restoreSession(from: appState)
                    let handledLaunchArguments = await handleLaunchArgumentsIfNeeded()
                    if !handledLaunchArguments {
                        await fileProviderDomain.ensureInstalled(using: appState)
                    }
                    await connectionManager.refreshDashboard(appState: appState)
                }
        }
        .menuBarExtraStyle(.window)

        WindowGroup("Home OS", id: "main") {
            MainWindowView()
                .environmentObject(appState)
                .environmentObject(connectionManager)
                .environmentObject(fileProviderDomain)
                .frame(minWidth: 980, minHeight: 680)
        }
        .commands {
            CommandGroup(replacing: .newItem) {
                Button("Open Home OS") {
                    AppWindow.openMainWindow(openWindow: openWindow)
                }
                .keyboardShortcut("0", modifiers: [.command])
            }
        }

        Settings {
            SettingsView()
                .environmentObject(appState)
                .environmentObject(connectionManager)
                .environmentObject(fileProviderDomain)
        }
    }

    @MainActor
    private func handleLaunchArgumentsIfNeeded() async -> Bool {
        guard !didHandleLaunchArguments else { return false }
        didHandleLaunchArguments = true

        let arguments = Set(CommandLine.arguments.dropFirst())
        let shouldRefreshSharedSettings = arguments.contains("--refresh-shared-settings-from-env")
        let shouldResetFileProvider = arguments.contains("--reset-file-provider-domain")
        guard shouldRefreshSharedSettings || shouldResetFileProvider else {
            return false
        }

        if shouldRefreshSharedSettings {
            do {
                try await refreshSharedSettingsFromEnvironment()
            } catch {
                appState.statusMessage = "Could not refresh shared settings: \(error.localizedDescription)"
                fputs("HomeOS shared settings refresh failed: \(error.localizedDescription)\n", stderr)
                if arguments.contains("--quit-after-refresh") || arguments.contains("--quit-after-reset") {
                    NSApp.terminate(nil)
                }
                return true
            }
        }

        if shouldResetFileProvider {
            await fileProviderDomain.reset(using: appState)
        } else {
            await fileProviderDomain.ensureInstalled(using: appState)
        }

        if arguments.contains("--quit-after-refresh") || arguments.contains("--quit-after-reset") {
            NSApp.terminate(nil)
        }

        return true
    }

    @MainActor
    private func refreshSharedSettingsFromEnvironment() async throws {
        let environment = ProcessInfo.processInfo.environment
        let serverURL = environment["HOMEOS_SERVER_URL"] ?? appState.serverURL
        let localServerURL = environment["HOMEOS_LOCAL_URL"] ?? appState.localServerURL
        let username = environment["HOMEOS_USERNAME"] ?? appState.username
        let password = environment["HOMEOS_PASSWORD"] ?? ""
        let preferLocalServer = environment["HOMEOS_PREFER_LOCAL"].map { value in
            !["0", "false", "no"].contains(value.trimmingCharacters(in: .whitespacesAndNewlines).lowercased())
        } ?? appState.preferLocalServer

        guard !serverURL.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty else {
            throw APIError.invalidURL
        }
        guard !username.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty, !password.isEmpty else {
            throw APIError.requestFailed("Set HOMEOS_USERNAME and HOMEOS_PASSWORD before refreshing shared settings.")
        }

        let (response, endpoint) = try await connectionManager.login(
            domainURL: serverURL,
            localURL: localServerURL,
            preferLocal: preferLocalServer,
            username: username,
            password: password
        )
        guard response.ok, let data = response.data else {
            throw APIError.requestFailed(response.error ?? "Invalid credentials")
        }

        HomeOSSharedSettings.clear()
        appState.saveSession(
            serverURL: serverURL,
            localServerURL: localServerURL,
            preferLocalServer: preferLocalServer,
            token: data.token,
            user: data.user
        )
        appState.activeServerURL = endpoint.url
        appState.activeConnectionKind = endpoint.kind.rawValue
        connectionManager.connect(appState: appState, token: data.token)
        fputs("HomeOS shared settings refreshed via \(endpoint.displayName)\n", stderr)
    }
}

final class AppDelegate: NSObject, NSApplicationDelegate {
    func applicationDidFinishLaunching(_ notification: Notification) {
        NSApp.setActivationPolicy(.accessory)
    }
}
