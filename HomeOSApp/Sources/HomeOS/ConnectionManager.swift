import SwiftUI
import Combine

@MainActor
class ConnectionManager: ObservableObject {
    @Published var isConnected: Bool = false
    @Published var isConnecting: Bool = false
    @Published var lastError: String?

    private var apiClient: APIClient?
    private var healthTimer: Timer?

    var client: APIClient? { apiClient }

    func connect(serverURL: String, token: String) {
        isConnecting = true
        lastError = nil
        let newClient = APIClient(baseURL: serverURL, authToken: token)
        apiClient = newClient

        Task {
            do {
                let metrics: MetricsResponse = try await newClient.getMetrics()
                self.isConnected = metrics.ok
                self.isConnecting = false
                if metrics.ok {
                    self.startHealthCheck()
                }
            } catch {
                self.isConnected = false
                self.isConnecting = false
                self.lastError = "Cannot reach server"
            }
        }
    }

    func disconnect() {
        healthTimer?.invalidate()
        healthTimer = nil
        isConnected = false
        apiClient = nil
    }

    private func startHealthCheck() {
        healthTimer?.invalidate()
        healthTimer = Timer.scheduledTimer(withTimeInterval: 30, repeats: true) { [weak self] _ in
            Task { @MainActor in
                self?.checkHealth()
            }
        }
    }

    private func checkHealth() {
        guard let client = apiClient else { return }
        Task {
            do {
                let _: MetricsResponse = try await client.getMetrics()
                self.isConnected = true
            } catch {
                self.isConnected = false
            }
        }
    }
}
