import SwiftUI
import Combine

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
        apiClient = APIClient(baseURL: serverURL, authToken: token)

        Task {
            do {
                let metrics: MetricsResponse = try await apiClient!.getMetrics()
                await MainActor.run {
                    self.isConnected = metrics.ok
                    self.isConnecting = false
                    if metrics.ok {
                        self.startHealthCheck()
                    }
                }
            } catch {
                await MainActor.run {
                    self.isConnected = false
                    self.isConnecting = false
                    self.lastError = "Cannot reach server"
                }
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
            self?.checkHealth()
        }
    }

    private func checkHealth() {
        guard let client = apiClient else { return }
        Task {
            do {
                let _: MetricsResponse = try await client.getMetrics()
                await MainActor.run { self.isConnected = true }
            } catch {
                await MainActor.run { self.isConnected = false }
            }
        }
    }
}
