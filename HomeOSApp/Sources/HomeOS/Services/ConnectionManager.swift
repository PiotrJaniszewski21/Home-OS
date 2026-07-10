import Foundation
import SwiftUI

@MainActor
final class ConnectionManager: ObservableObject {
    @Published var isConnected = false
    @Published var isConnecting = false
    @Published var lastError: String?
    @Published var lastHealthCheck: Date?
    @Published private(set) var activeEndpoint: ConnectionEndpoint?

    private(set) var client: APIClient?
    private var healthTask: Task<Void, Never>?
    private let endpointResolver = ConnectionEndpointResolver()
    private let apiSession: URLSession
    private let localAPISession: URLSession
    private let discoverySession: URLSession
    private let localDiscoverySession: URLSession
    private let localCertificateTrustDelegate: LocalCertificateTrustDelegate
    private var lastLocalDiscoveryAttempt: Date?

    init(
        apiSession: URLSession = URLSession(configuration: ConnectionManager.apiConfiguration()),
        localAPISession: URLSession? = nil,
        discoverySession: URLSession = URLSession(configuration: ConnectionManager.discoveryConfiguration()),
        localDiscoverySession: URLSession? = nil
    ) {
        let localCertificateTrustDelegate = LocalCertificateTrustDelegate()
        self.apiSession = apiSession
        self.localAPISession = localAPISession ?? URLSession(
            configuration: ConnectionManager.apiConfiguration(),
            delegate: localCertificateTrustDelegate,
            delegateQueue: nil
        )
        self.discoverySession = discoverySession
        self.localDiscoverySession = localDiscoverySession ?? URLSession(
            configuration: ConnectionManager.discoveryConfiguration(),
            delegate: localCertificateTrustDelegate,
            delegateQueue: nil
        )
        self.localCertificateTrustDelegate = localCertificateTrustDelegate
    }

    func restoreSession(from appState: AppState) {
        appState.restoreSharedSessionIfNeeded()
        guard appState.isConfigured else { return }
        if client == nil {
            connect(appState: appState)
        }
    }

    func login(
        domainURL: String,
        localURL: String,
        preferLocal: Bool,
        username: String,
        password: String
    ) async throws -> (LoginResponse, ConnectionEndpoint) {
        let candidates = ConnectionEndpointResolver.candidates(domainURL: domainURL, localURL: localURL, preferLocal: preferLocal)
        guard !candidates.isEmpty else { throw APIError.invalidURL }

        var failures: [EndpointResolutionFailure] = []
        for endpoint in candidates {
            do {
                let client = try apiClient(for: endpoint)
                let response = try await client.login(username: username, password: password)
                if response.ok {
                    return (response, endpoint)
                }
                let message = response.error ?? "Invalid credentials"
                failures.append(EndpointResolutionFailure(endpoint: endpoint, message: message))
                if message.localizedCaseInsensitiveContains("credential") || message.localizedCaseInsensitiveContains("password") {
                    break
                }
            } catch {
                failures.append(EndpointResolutionFailure(endpoint: endpoint, message: error.localizedDescription))
            }
        }

        throw APIError.requestFailed(Self.failureSummary(failures))
    }

    func connect(appState: AppState, token: String? = nil) {
        isConnecting = true
        lastError = nil
        AppLog.connection.info("Connection requested, local preference: \(appState.preferLocalServer, privacy: .public)")

        Task { [weak self] in
            guard let self else { return }
            let didConnect = await resolveAndApplyEndpoint(appState: appState, token: token ?? appState.authToken, updateStatusMessage: true)
            if didConnect {
                startHealthCheck(appState: appState)
                await refreshDashboard(appState: appState, shouldResolveEndpoint: false)
            }
        }
    }

    func disconnect() {
        AppLog.connection.info("Disconnected by user or app state reset")
        healthTask?.cancel()
        healthTask = nil
        client = nil
        activeEndpoint = nil
        isConnected = false
        isConnecting = false
        lastError = nil
    }

    func refreshDashboard(appState: AppState) async {
        await refreshDashboard(appState: appState, shouldResolveEndpoint: true)
    }

    func testLocalServerURL(_ localURL: String) async throws -> ConnectionEndpoint {
        guard let normalizedURL = ConnectionEndpointResolver.normalizedURL(localURL) else {
            throw APIError.invalidURL
        }

        let endpoint = ConnectionEndpoint(kind: .local, url: normalizedURL)
        let probeClient = try APIClient(
            baseURL: endpoint.url,
            session: localDiscoverySession,
            trustsLocalSelfSignedCertificates: true
        )
        let response = try await probeClient.getHealth()
        guard response.status.localizedCaseInsensitiveCompare("healthy") == .orderedSame else {
            throw APIError.requestFailed("Server responded, but did not report healthy.")
        }
        return endpoint
    }

    private func refreshDashboard(appState: AppState, shouldResolveEndpoint: Bool) async {
        if shouldResolveEndpoint {
            await resolveAndApplyEndpoint(appState: appState, token: appState.authToken, updateStatusMessage: true)
        }
        guard let client else { return }
        do {
            let metricsResponse = try await client.getMetrics()
            appState.metrics = metricsResponse.data
            isConnected = metricsResponse.ok
            isConnecting = false
            lastHealthCheck = Date()
            lastError = nil
            AppLog.connection.info("Dashboard metrics refresh succeeded")
        } catch {
            isConnected = appState.isConfigured
            isConnecting = false
            lastError = error.localizedDescription
            AppLog.connection.error("Dashboard metrics refresh failed: \(error.localizedDescription, privacy: .public)")
            return
        }

        do {
            let storageResponse = try await client.getStorageInfo()
            appState.storage = storageResponse.data
            AppLog.connection.info("Storage refresh succeeded")
        } catch {
            appState.storage = nil
            lastError = "Connected, but storage status failed: \(error.localizedDescription)"
            AppLog.connection.error("Storage refresh failed: \(error.localizedDescription, privacy: .public)")
        }
    }

    private func refreshConnectionState(appState: AppState) async {
        await resolveAndApplyEndpoint(appState: appState, token: appState.authToken, updateStatusMessage: true)
    }

    private func startHealthCheck(appState: AppState) {
        healthTask?.cancel()
        healthTask = Task { [weak self] in
            while !Task.isCancelled {
                try? await Task.sleep(for: .seconds(30))
                await self?.refreshConnectionState(appState: appState)
            }
        }
    }

    @discardableResult
    private func resolveAndApplyEndpoint(appState: AppState, token: String, updateStatusMessage: Bool) async -> Bool {
        guard !token.isEmpty else { return false }
        let candidates = ConnectionEndpointResolver.candidates(
            domainURL: appState.serverURL,
            localURL: appState.localServerURL,
            preferLocal: appState.preferLocalServer
        )
        guard !candidates.isEmpty else {
            client = nil
            activeEndpoint = nil
            appState.activeServerURL = ""
            appState.activeConnectionKind = ""
            isConnected = false
            isConnecting = false
            lastError = "No valid server URL is configured."
            return false
        }

        isConnecting = client == nil
        let previousEndpoint = activeEndpoint
        let result = await endpointResolver.resolve(candidates: candidates) { [apiSession, localAPISession] endpoint in
            let session = endpoint.kind == .local ? localAPISession : apiSession
            let probeClient = try APIClient(
                baseURL: endpoint.url,
                authToken: token,
                session: session,
                trustsLocalSelfSignedCertificates: endpoint.kind == .local
            )
            let response = try await probeClient.getMetrics()
            return response.ok
        }

        guard let endpoint = result.endpoint else {
            client = nil
            activeEndpoint = nil
            appState.activeServerURL = ""
            appState.activeConnectionKind = ""
            isConnected = false
            isConnecting = false
            lastError = Self.failureSummary(result.failures)
            AppLog.connection.error("All connection endpoints failed: \(self.lastError ?? "unknown", privacy: .public)")
            return false
        }

        do {
            if previousEndpoint != endpoint || client == nil {
                client = try apiClient(for: endpoint, token: token)
                activeEndpoint = endpoint
                appState.activeServerURL = endpoint.url
                appState.activeConnectionKind = endpoint.kind.rawValue
                AppLog.connection.info("Active endpoint selected: \(endpoint.displayName, privacy: .public)")
                if updateStatusMessage, let previousEndpoint, previousEndpoint != endpoint {
                    appState.statusMessage = "Switched to \(endpoint.displayName)."
                }
            }
            isConnected = true
            isConnecting = false
            lastHealthCheck = Date()
            lastError = nil
            if endpoint.kind == .remote, let discoveredEndpoint = await discoverLocalEndpoint(appState: appState, token: token, sourceEndpoint: endpoint) {
                client = try apiClient(for: discoveredEndpoint, token: token)
                activeEndpoint = discoveredEndpoint
                appState.activeServerURL = discoveredEndpoint.url
                appState.activeConnectionKind = discoveredEndpoint.kind.rawValue
                appState.localServerURL = discoveredEndpoint.url
                AppLog.connection.info("Discovered and selected local endpoint: \(discoveredEndpoint.displayName, privacy: .public)")
                if updateStatusMessage {
                    appState.statusMessage = "Using local network connection: \(discoveredEndpoint.displayName)."
                }
            }
            return true
        } catch {
            client = nil
            activeEndpoint = nil
            appState.activeServerURL = ""
            appState.activeConnectionKind = ""
            isConnected = false
            isConnecting = false
            lastError = error.localizedDescription
            AppLog.connection.error("Connection client creation failed: \(error.localizedDescription, privacy: .public)")
            return false
        }
    }

    private func discoverLocalEndpoint(appState: AppState, token: String, sourceEndpoint: ConnectionEndpoint) async -> ConnectionEndpoint? {
        guard appState.preferLocalServer, shouldAttemptLocalDiscovery(appState: appState) else { return nil }
        lastLocalDiscoveryAttempt = Date()

        do {
            let sourceClient = try apiClient(for: sourceEndpoint, token: token)
            let response = try await sourceClient.getConnectionInfo()
            guard response.ok, let localURLs = response.data?.localURLs, !localURLs.isEmpty else { return nil }

            let candidates = ConnectionEndpointResolver.localCandidates(localURLs)
            guard !candidates.isEmpty else { return nil }

            let result = await endpointResolver.resolve(candidates: candidates) { [localDiscoverySession] endpoint in
                let probeClient = try APIClient(
                    baseURL: endpoint.url,
                    authToken: token,
                    session: localDiscoverySession,
                    trustsLocalSelfSignedCertificates: true
                )
                let response = try await probeClient.getMetrics()
                return response.ok
            }

            if let endpoint = result.endpoint {
                return endpoint
            }

            if !result.failures.isEmpty {
                AppLog.connection.info("Local discovery found candidates, but none responded yet")
            }
        } catch {
            AppLog.connection.info("Local endpoint discovery skipped: \(error.localizedDescription, privacy: .public)")
        }
        return nil
    }

    private func shouldAttemptLocalDiscovery(appState: AppState) -> Bool {
        if appState.localServerURL.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
            return true
        }
        guard let lastLocalDiscoveryAttempt else { return true }
        return Date().timeIntervalSince(lastLocalDiscoveryAttempt) > 300
    }

    private func apiClient(for endpoint: ConnectionEndpoint, token: String = "") throws -> APIClient {
        try APIClient(
            baseURL: endpoint.url,
            authToken: token,
            session: apiSession(for: endpoint),
            trustsLocalSelfSignedCertificates: endpoint.kind == .local
        )
    }

    private func apiSession(for endpoint: ConnectionEndpoint) -> URLSession {
        endpoint.kind == .local ? localAPISession : apiSession
    }

    private static func failureSummary(_ failures: [EndpointResolutionFailure]) -> String {
        guard !failures.isEmpty else { return "No server endpoints could be reached." }
        return failures
            .map { "\($0.endpoint.displayName): \($0.message)" }
            .joined(separator: "\n")
    }

    nonisolated private static func apiConfiguration() -> URLSessionConfiguration {
        let configuration = URLSessionConfiguration.default
        configuration.timeoutIntervalForRequest = 4
        configuration.timeoutIntervalForResource = 8
        configuration.waitsForConnectivity = false
        return configuration
    }

    nonisolated private static func discoveryConfiguration() -> URLSessionConfiguration {
        let configuration = URLSessionConfiguration.ephemeral
        configuration.timeoutIntervalForRequest = 1.25
        configuration.timeoutIntervalForResource = 2
        configuration.waitsForConnectivity = false
        return configuration
    }
}
