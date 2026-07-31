import AppKit
import SwiftUI

@main
struct HomeOSApp: App {
    @NSApplicationDelegateAdaptor(AppDelegate.self) private var appDelegate
    @StateObject private var appState = AppState()
    @StateObject private var connectionManager = ConnectionManager()
    @StateObject private var fileProviderDomain = FileProviderDomainService()
    @StateObject private var transferActivityMonitor = FileTransferActivityMonitor()
    @State private var didHandleLaunchArguments = false

    var body: some Scene {
        MenuBarExtra {
            MenuBarView()
                .environmentObject(appState)
                .environmentObject(connectionManager)
                .environmentObject(fileProviderDomain)
        } label: {
            Image(nsImage: menuBarStatusImage)
                .help(menuBarStatusDescription)
                .task(id: appState.authToken) {
                    guard !Self.isRunningTests else { return }
                    connectionManager.restoreSession(from: appState)
                    let handledLaunchArguments = await handleLaunchArgumentsIfNeeded()
                    if !handledLaunchArguments {
                        await fileProviderDomain.ensureInstalled(using: appState)
                    }
                    transferActivityMonitor.start()
                    await connectionManager.refreshDashboard(appState: appState)
                    await fileProviderDomain.refreshRemoteChanges(
                        using: connectionManager.client,
                        username: appState.username
                    )
                }
                .task(id: appState.authToken) {
                    guard !Self.isRunningTests else { return }
                    await monitorFileProviderChanges()
                }
        }
        .menuBarExtraStyle(.window)

        WindowGroup("HomeOS", id: "main") {
            MainWindowView()
                .environmentObject(appState)
                .environmentObject(connectionManager)
                .environmentObject(fileProviderDomain)
                .frame(minWidth: 980, minHeight: 680)
        }
        .commands {
            HomeOSCommands(
                appState: appState,
                connection: connectionManager,
                fileProviderDomain: fileProviderDomain
            )
        }

        Settings {
            SettingsView()
                .environmentObject(appState)
                .environmentObject(connectionManager)
                .environmentObject(fileProviderDomain)
        }
    }

    private var menuBarStatusDescription: String {
        let uploads = transferActivities(for: .upload)
        let downloads = transferActivities(for: .download)
        if !uploads.isEmpty, !downloads.isEmpty {
            return "Home OS uploading and downloading files"
        }
        if !uploads.isEmpty { return "Home OS uploading files" }
        if !downloads.isEmpty { return "Home OS downloading files" }
        guard connectionManager.isConnected else { return "Home OS disconnected" }
        return appState.activeConnectionKind == ConnectionEndpoint.Kind.local.rawValue
            ? "Home OS connected locally"
            : "Home OS connected through domain"
    }

    private static var isRunningTests: Bool {
        ProcessInfo.processInfo.environment["XCTestConfigurationFilePath"] != nil
    }

    private var menuBarStatusImage: NSImage {
        let uploadProgress = aggregateProgress(for: .upload)
        let downloadProgress = aggregateProgress(for: .download)
        if uploadProgress != nil || downloadProgress != nil {
            return transferProgressImage(upload: uploadProgress, download: downloadProgress)
        }

        let color: NSColor
        if !connectionManager.isConnected {
            color = .systemRed
        } else if appState.activeConnectionKind == ConnectionEndpoint.Kind.local.rawValue {
            color = .systemGreen
        } else {
            color = .systemBlue
        }

        let image = NSImage(size: NSSize(width: 12, height: 12))
        image.lockFocus()
        color.setFill()
        NSBezierPath(ovalIn: NSRect(x: 2, y: 2, width: 8, height: 8)).fill()
        image.unlockFocus()
        image.isTemplate = false
        return image
    }

    private func transferActivities(for kind: FileTransferActivity.Kind) -> [FileTransferActivity] {
        transferActivityMonitor.activities.filter { $0.kind == kind }
    }

    private func aggregateProgress(for kind: FileTransferActivity.Kind) -> Double? {
        let activities = transferActivities(for: kind)
        guard !activities.isEmpty else { return nil }
        return activities.map(\.clampedFractionCompleted).reduce(0, +) / Double(activities.count)
    }

    private func transferProgressImage(upload: Double?, download: Double?) -> NSImage {
        let bothDirections = upload != nil && download != nil
        let size = NSSize(width: 28, height: bothDirections ? 16 : 12)
        let image = NSImage(size: size)
        image.lockFocus()

        if let upload {
            drawTransferBar(
                progress: upload,
                symbol: "↑",
                color: .systemBlue,
                y: bothDirections ? 9 : 4,
                imageWidth: size.width
            )
        }
        if let download {
            drawTransferBar(
                progress: download,
                symbol: "↓",
                color: .systemGreen,
                y: bothDirections ? 2 : 4,
                imageWidth: size.width
            )
        }

        image.unlockFocus()
        image.isTemplate = false
        return image
    }

    private func drawTransferBar(progress: Double, symbol: String, color: NSColor, y: CGFloat, imageWidth: CGFloat) {
        let attributes: [NSAttributedString.Key: Any] = [
            .font: NSFont.systemFont(ofSize: 8, weight: .bold),
            .foregroundColor: color,
        ]
        (symbol as NSString).draw(in: NSRect(x: 0, y: y - 2, width: 8, height: 8), withAttributes: attributes)

        let trackRect = NSRect(x: 9, y: y, width: imageWidth - 10, height: 4)
        NSColor.tertiaryLabelColor.withAlphaComponent(0.35).setFill()
        NSBezierPath(roundedRect: trackRect, xRadius: 2, yRadius: 2).fill()

        let completedWidth = trackRect.width * min(max(progress, 0), 1)
        guard completedWidth > 0 else { return }
        color.setFill()
        NSBezierPath(
            roundedRect: NSRect(x: trackRect.minX, y: trackRect.minY, width: completedWidth, height: trackRect.height),
            xRadius: 2,
            yRadius: 2
        ).fill()
    }

    @MainActor
    private func monitorFileProviderChanges() async {
        guard appState.isConfigured else { return }

        while !Task.isCancelled {
            if fileProviderDomain.state == .enabled {
                await fileProviderDomain.refreshRemoteChanges(
                    using: connectionManager.client,
                    username: appState.username
                )
            }

            do {
                try await Task.sleep(for: .seconds(10))
            } catch {
                return
            }
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
        if !Self.isRunningTests, Self.shouldTerminateDuplicateInstance() {
            NSApp.terminate(nil)
            return
        }

        AppIcon.install()
        NSApp.setActivationPolicy(.accessory)
    }

    static func shouldTerminateDuplicateInstance(
        currentPID: pid_t = ProcessInfo.processInfo.processIdentifier,
        runningPIDs: [pid_t]? = nil
    ) -> Bool {
        let matchingPIDs: [pid_t]
        if let runningPIDs {
            matchingPIDs = runningPIDs
        } else {
            guard let bundleIdentifier = Bundle.main.bundleIdentifier else { return false }
            matchingPIDs = NSRunningApplication
                .runningApplications(withBundleIdentifier: bundleIdentifier)
                .filter { !$0.isTerminated }
                .map(\.processIdentifier)
        }

        guard let primaryPID = matchingPIDs.min() else { return false }
        return currentPID != primaryPID
    }

    private static var isRunningTests: Bool {
        let environment = ProcessInfo.processInfo.environment
        return environment.keys.contains { $0.hasPrefix("XCTest") || $0 == "XCInjectBundleInto" }
            || Bundle.allBundles.contains { $0.bundlePath.hasSuffix(".xctest") }
    }
}
