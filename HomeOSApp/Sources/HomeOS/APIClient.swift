import Foundation

final class APIClient: Sendable {
    private let baseURL: String
    private let authToken: String

    init(baseURL: String, authToken: String = "") {
        self.baseURL = baseURL.trimmingCharacters(in: CharacterSet(charactersIn: "/"))
        self.authToken = authToken
    }

    // MARK: - Auth

    func login(username: String, password: String) async throws -> LoginResponse {
        let body: [String: String] = ["username": username, "password": password]
        return try await post("/api/login", body: body)
    }

    // MARK: - Files

    func listDirectory(path: String) async throws -> FileListResponse {
        return try await get("/files\(path)", accept: "application/json")
    }

    func downloadFile(path: String) async throws -> Data {
        let url = URL(string: "\(baseURL)/files\(path)?download")!
        var request = URLRequest(url: url)
        addAuth(&request)
        let (data, _) = try await URLSession.shared.data(for: request)
        return data
    }

    func searchFiles(query: String) async throws -> SearchResponse {
        return try await get("/api/files/search?q=\(query.addingPercentEncoding(withAllowedCharacters: .urlQueryAllowed) ?? query)")
    }

    // MARK: - Monitor

    func getMetrics() async throws -> MetricsResponse {
        return try await get("/api/monitor/metrics")
    }

    // MARK: - Storage

    func getStorageInfo() async throws -> StorageResponse {
        return try await get("/storage", accept: "application/json")
    }

    // MARK: - AI

    func sendAIMessage(message: String, history: [[String: String]]) async throws -> AIResponse {
        let body: [String: Any] = ["message": message, "history": history]
        return try await post("/api/ai/chat", body: body)
    }

    // MARK: - HTTP Helpers

    private func get<T: Decodable>(_ path: String, accept: String = "application/json") async throws -> T {
        let url = URL(string: "\(baseURL)\(path)")!
        var request = URLRequest(url: url)
        request.setValue(accept, forHTTPHeaderField: "Accept")
        addAuth(&request)

        let (data, response) = try await URLSession.shared.data(for: request)
        guard let http = response as? HTTPURLResponse, http.statusCode == 200 else {
            throw APIError.requestFailed
        }
        return try JSONDecoder().decode(T.self, from: data)
    }

    private func post<T: Decodable>(_ path: String, body: Any) async throws -> T {
        let url = URL(string: "\(baseURL)\(path)")!
        var request = URLRequest(url: url)
        request.httpMethod = "POST"
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.httpBody = try JSONSerialization.data(withJSONObject: body)
        addAuth(&request)

        let (data, response) = try await URLSession.shared.data(for: request)
        guard let http = response as? HTTPURLResponse, (200...299).contains(http.statusCode) else {
            throw APIError.requestFailed
        }
        return try JSONDecoder().decode(T.self, from: data)
    }

    private func addAuth(_ request: inout URLRequest) {
        if !authToken.isEmpty {
            request.setValue("Bearer \(authToken)", forHTTPHeaderField: "Authorization")
        }
    }
}

enum APIError: Error {
    case requestFailed
    case unauthorized
    case notFound
}
