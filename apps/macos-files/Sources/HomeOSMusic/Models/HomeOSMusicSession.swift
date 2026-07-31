import Foundation
import SwiftUI

@MainActor
final class HomeOSMusicSession: ObservableObject {
    @Published private(set) var client: APIClient?
    @Published private(set) var activeEndpoint: ConnectionEndpoint?
    @Published private(set) var isConnected = false
    @Published private(set) var isConnecting = false
    @Published var errorMessage: String?

    @AppStorage("musicServerURL") var serverURL = ""
    @AppStorage("musicLocalServerURL") var localServerURL = ""
    @AppStorage("musicPreferLocalServer") var preferLocalServer = true
    @AppStorage("musicUsername") var username = ""

    private var authToken = ""
    private var healthTask: Task<Void, Never>?
    private let resolver = ConnectionEndpointResolver()
    private let trustDelegate: LocalCertificateTrustDelegate
    private let remoteSession: URLSession
    private let localSession: URLSession

    init() {
        let delegate = LocalCertificateTrustDelegate()
        trustDelegate = delegate
        remoteSession = URLSession(
            configuration: Self.sessionConfiguration(),
            delegate: delegate,
            delegateQueue: nil
        )
        localSession = URLSession(
            configuration: Self.sessionConfiguration(),
            delegate: delegate,
            delegateQueue: nil
        )
    }

    var isConfigured: Bool {
        !serverURL.isEmpty && !authToken.isEmpty
    }

    var connectionDescription: String {
        activeEndpoint?.displayName ?? "Disconnected"
    }

    func restoreAndConnect() async {
        let shared = HomeOSSharedSettings.load()
        if shared.isConfigured {
            serverURL = shared.serverURL
            localServerURL = shared.localServerURL
            preferLocalServer = shared.preferLocalServer
            username = shared.username
            authToken = shared.authToken
        }
        guard isConfigured else { return }
        await connect()
    }

    func signIn(
        serverURL: String,
        localServerURL: String,
        preferLocal: Bool,
        username: String,
        password: String
    ) async -> Bool {
        isConnecting = true
        errorMessage = nil
        defer { isConnecting = false }

        let candidates = ConnectionEndpointResolver.candidates(
            domainURL: serverURL,
            localURL: localServerURL,
            preferLocal: preferLocal
        )
        guard !candidates.isEmpty else {
            errorMessage = "Enter a valid HTTPS Home OS address."
            return false
        }

        var failures: [String] = []
        for endpoint in candidates {
            do {
                let candidate = try makeClient(endpoint: endpoint, token: "")
                let response = try await candidate.login(username: username, password: password)
                guard response.ok, let data = response.data else {
                    failures.append(response.error ?? "Invalid credentials")
                    continue
                }
                self.serverURL = serverURL.trimmingCharacters(in: .whitespacesAndNewlines)
                self.localServerURL = ConnectionEndpointResolver.normalizedURL(localServerURL)
                    ?? localServerURL.trimmingCharacters(in: .whitespacesAndNewlines)
                self.preferLocalServer = preferLocal
                self.username = data.user.username
                authToken = data.token
                HomeOSSharedSettings.save(
                    serverURL: self.serverURL,
                    localServerURL: self.localServerURL,
                    preferLocalServer: preferLocal,
                    authToken: data.token,
                    username: data.user.username
                )
                client = try makeClient(endpoint: endpoint, token: data.token)
                activeEndpoint = endpoint
                isConnected = true
                startHealthChecks()
                return true
            } catch {
                failures.append("\(endpoint.displayName): \(error.localizedDescription)")
            }
        }
        errorMessage = failures.joined(separator: "\n")
        return false
    }

    func connect() async {
        guard !authToken.isEmpty else { return }
        isConnecting = true
        defer { isConnecting = false }
        let candidates = ConnectionEndpointResolver.candidates(
            domainURL: serverURL,
            localURL: localServerURL,
            preferLocal: preferLocalServer
        )
        let token = authToken
        let remoteSession = remoteSession
        let localSession = localSession
        let result = await resolver.resolve(candidates: candidates) { endpoint in
            let candidate = try APIClient(
                baseURL: endpoint.url,
                authToken: token,
                session: endpoint.kind == .local ? localSession : remoteSession,
                trustsLocalSelfSignedCertificates: endpoint.kind == .local
            )
            _ = try await candidate.musicPlaylists()
            return true
        }
        guard let endpoint = result.endpoint else {
            client = nil
            activeEndpoint = nil
            isConnected = false
            errorMessage = result.failures
                .map { "\($0.endpoint.displayName): \($0.message)" }
                .joined(separator: "\n")
            return
        }
        do {
            client = try makeClient(endpoint: endpoint, token: authToken)
            activeEndpoint = endpoint
            isConnected = true
            errorMessage = nil
            startHealthChecks()
        } catch {
            client = nil
            activeEndpoint = nil
            isConnected = false
            errorMessage = error.localizedDescription
        }
    }

    func reconnect(
        serverURL: String,
        localServerURL: String,
        preferLocal: Bool
    ) async {
        self.serverURL = serverURL.trimmingCharacters(in: .whitespacesAndNewlines)
        self.localServerURL = localServerURL.trimmingCharacters(in: .whitespacesAndNewlines)
        preferLocalServer = preferLocal
        guard !authToken.isEmpty else { return }
        HomeOSSharedSettings.save(
            serverURL: self.serverURL,
            localServerURL: self.localServerURL,
            preferLocalServer: preferLocal,
            authToken: authToken,
            username: username
        )
        await connect()
    }

    func signOut() {
        healthTask?.cancel()
        healthTask = nil
        authToken = ""
        client = nil
        activeEndpoint = nil
        isConnected = false
        errorMessage = nil
        HomeOSSharedSettings.clear()
    }

    private func startHealthChecks() {
        healthTask?.cancel()
        healthTask = Task { [weak self] in
            while !Task.isCancelled {
                try? await Task.sleep(for: .seconds(30))
                guard !Task.isCancelled else { return }
                await self?.connect()
            }
        }
    }

    private func makeClient(endpoint: ConnectionEndpoint, token: String) throws -> APIClient {
        try APIClient(
            baseURL: endpoint.url,
            authToken: token,
            session: endpoint.kind == .local ? localSession : remoteSession,
            trustsLocalSelfSignedCertificates: endpoint.kind == .local
        )
    }

    private static func sessionConfiguration() -> URLSessionConfiguration {
        let configuration = URLSessionConfiguration.default
        configuration.timeoutIntervalForRequest = 20
        configuration.timeoutIntervalForResource = 60
        configuration.waitsForConnectivity = false
        return configuration
    }
}
