import Foundation

final class APIClient: Sendable {
    private let baseURL: URL
    private let authToken: String
    private let session: URLSession
    private let trustsLocalSelfSignedCertificates: Bool
    private let userAgent = "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_0) AppleWebKit/605.1.15 (KHTML, like Gecko) HomeOSMac/1.0 Safari/605.1.15"

    init(
        baseURL: String,
        authToken: String = "",
        session: URLSession = .shared,
        trustsLocalSelfSignedCertificates: Bool = false
    ) throws {
        let trimmed = baseURL.trimmingCharacters(in: .whitespacesAndNewlines).trimmingCharacters(in: CharacterSet(charactersIn: "/"))
        guard let url = URL(string: trimmed), url.scheme?.lowercased() == "https", url.host != nil else {
            throw APIError.invalidURL
        }
        self.baseURL = url
        self.authToken = authToken
        self.session = session
        self.trustsLocalSelfSignedCertificates = trustsLocalSelfSignedCertificates
    }

    var isAuthenticated: Bool {
        !authToken.isEmpty
    }

    func login(username: String, password: String) async throws -> LoginResponse {
        try await postJSON("/api/login", body: ["username": username, "password": password])
    }

    func getHealth() async throws -> HealthResponse {
        try await get("/health")
    }

    func listDirectory(path: String, recursive: Bool = false) async throws -> FileListResponse {
        let queryItems = recursive ? [URLQueryItem(name: "recursive", value: "1")] : []
        return try await get(
            "/files\(encodedFilePath(path))",
            accept: "application/json",
            queryItems: queryItems
        )
    }

    func downloadFile(path: String, onProgress: (@Sendable (Double?) async -> Void)? = nil) async throws -> Data {
        let temporaryURL = try await downloadFileToTemporaryURL(path: path, onProgress: onProgress)
        defer { try? FileManager.default.removeItem(at: temporaryURL) }
        return try Data(contentsOf: temporaryURL)
    }

    func downloadFileToTemporaryURL(path: String, onProgress: (@Sendable (Double?) async -> Void)? = nil) async throws -> URL {
        let url = makeURL(path: "/files\(encodedFilePath(path))", queryItems: [URLQueryItem(name: "download", value: nil)])
        var request = URLRequest(url: url)
        addCommonHeaders(to: &request)
        addAuth(to: &request)

        let (temporaryURL, response): (URL, URLResponse)
        if let onProgress {
            (temporaryURL, response) = try await DownloadProgressSession.download(
                request: request,
                configuration: session.configuration,
                trustsLocalSelfSignedCertificates: trustsLocalSelfSignedCertificates,
                onProgress: onProgress
            )
        } else {
            (temporaryURL, response) = try await session.download(for: request)
        }
        do {
            try validateDownload(response: response, fileURL: temporaryURL)
        } catch {
            try? FileManager.default.removeItem(at: temporaryURL)
            throw error
        }
        return temporaryURL
    }

    func searchFiles(query: String) async throws -> SearchResponse {
        try await get("/api/files/search", queryItems: [URLQueryItem(name: "q", value: query)])
    }

    func createDirectory(path: String) async throws -> BasicResponse {
        try await postJSON("/api/files/mkdir", body: ["path": path])
    }

    func rename(path: String, newName: String) async throws -> BasicResponse {
        try await postJSON("/api/files/rename", body: ["path": path, "new_name": newName])
    }

    func move(sourcePath: String, destinationPath: String) async throws -> BasicResponse {
        try await postJSON("/api/files/move", body: ["src": sourcePath, "dest": destinationPath])
    }

    func copy(sourcePath: String, destinationPath: String) async throws -> BasicResponse {
        try await postJSON("/api/files/copy", body: ["src": sourcePath, "dest": destinationPath])
    }

    func delete(path: String) async throws -> BasicResponse {
        try await postJSON("/api/files/delete", body: ["path": path])
    }

    func upload(
        fileURL: URL,
        to destinationPath: String,
        filename: String? = nil,
        onProgress: (@Sendable (Double?) async -> Void)? = nil
    ) async throws -> FileEntry? {
        let boundary = "Boundary-\(UUID().uuidString)"
        var request = URLRequest(url: makeURL(path: "/api/files/upload"))
        request.httpMethod = "POST"
        addCommonHeaders(to: &request)
        request.setValue("multipart/form-data; boundary=\(boundary)", forHTTPHeaderField: "Content-Type")
        request.setValue("application/json", forHTTPHeaderField: "Accept")
        addAuth(to: &request)

        let body = try MultipartFormFile.create(
            destinationPath: destinationPath,
            fileURL: fileURL,
            requestedFilename: filename,
            boundary: boundary
        )
        defer { try? FileManager.default.removeItem(at: body.url) }
        request.setValue(String(body.contentLength), forHTTPHeaderField: "Content-Length")
        let (data, response): (Data, URLResponse)
        if let onProgress {
            (data, response) = try await UploadProgressSession.upload(
                request: request,
                bodyFile: body.url,
                configuration: session.configuration,
                trustsLocalSelfSignedCertificates: trustsLocalSelfSignedCertificates,
                onProgress: onProgress
            )
        } else {
            (data, response) = try await session.upload(for: request, fromFile: body.url)
        }
        try validate(response: response, data: data)
        let uploadResponse = try decode(UploadResponse.self, from: data)
        guard uploadResponse.ok else {
            throw APIError.requestFailed(uploadResponse.error ?? "Upload failed.")
        }
        return uploadResponse.data?.uploaded.first
    }

    func getMetrics() async throws -> MetricsResponse {
        try await get("/api/monitor/metrics")
    }

    func getStorageInfo() async throws -> StorageResponse {
        try await get("/storage", accept: "application/json")
    }

    func getConnectionInfo() async throws -> ConnectionInfoResponse {
        try await get("/api/network/connection-info")
    }

    func getNetworkSpeed() async throws -> NetworkSpeedResponse {
        try await get("/api/network/speed", cachePolicy: .reloadIgnoringLocalCacheData)
    }

    func authenticatedGet<T: Decodable>(
        _ path: String,
        queryItems: [URLQueryItem] = []
    ) async throws -> T {
        try await get(path, queryItems: queryItems)
    }

    func authenticatedSend<T: Decodable, Body: Encodable>(
        _ path: String,
        method: String,
        body: Body
    ) async throws -> T {
        var request = URLRequest(url: makeURL(path: path))
        request.httpMethod = method
        addCommonHeaders(to: &request)
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.setValue("application/json", forHTTPHeaderField: "Accept")
        request.httpBody = try JSONEncoder().encode(body)
        addAuth(to: &request)
        let (data, response) = try await session.data(for: request)
        try validate(response: response, data: data)
        return try decode(T.self, from: data)
    }

    func authenticatedRequest<T: Decodable>(
        _ path: String,
        method: String
    ) async throws -> T {
        var request = URLRequest(url: makeURL(path: path))
        request.httpMethod = method
        addCommonHeaders(to: &request)
        request.setValue("application/json", forHTTPHeaderField: "Accept")
        addAuth(to: &request)
        let (data, response) = try await session.data(for: request)
        try validate(response: response, data: data)
        return try decode(T.self, from: data)
    }

    func authenticatedURL(path: String, queryItems: [URLQueryItem] = []) -> URL {
        makeURL(path: path, queryItems: queryItems)
    }

    func resolvedAuthenticatedURL(_ value: String) -> URL? {
        URL(string: value, relativeTo: baseURL)?.absoluteURL
    }

    private func get<T: Decodable>(
        _ path: String,
        accept: String = "application/json",
        queryItems: [URLQueryItem] = [],
        cachePolicy: URLRequest.CachePolicy = .useProtocolCachePolicy
    ) async throws -> T {
        var request = URLRequest(url: makeURL(path: path, queryItems: queryItems), cachePolicy: cachePolicy)
        addCommonHeaders(to: &request)
        request.setValue(accept, forHTTPHeaderField: "Accept")
        addAuth(to: &request)
        let (data, response) = try await session.data(for: request)
        try validate(response: response, data: data)
        return try decode(T.self, from: data)
    }

    private func postJSON<T: Decodable>(_ path: String, body: Any) async throws -> T {
        var request = URLRequest(url: makeURL(path: path))
        request.httpMethod = "POST"
        addCommonHeaders(to: &request)
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.setValue("application/json", forHTTPHeaderField: "Accept")
        request.httpBody = try JSONSerialization.data(withJSONObject: body)
        addAuth(to: &request)
        let (data, response) = try await session.data(for: request)
        try validate(response: response, data: data)
        return try decode(T.self, from: data)
    }

    private func makeURL(path: String, queryItems: [URLQueryItem] = []) -> URL {
        var components = URLComponents(url: baseURL, resolvingAgainstBaseURL: false)!
        let basePath = components.percentEncodedPath.trimmingCharacters(in: CharacterSet(charactersIn: "/"))
        let requestPath = path.hasPrefix("/") ? String(path.dropFirst()) : path
        components.percentEncodedPath = "/" + ([basePath, requestPath].filter { !$0.isEmpty }.joined(separator: "/"))
        components.queryItems = queryItems.isEmpty ? nil : queryItems
        return components.url!
    }

    private func encodedFilePath(_ path: String) -> String {
        let normalized = path == "/" ? "" : path
        return normalized.addingPercentEncoding(withAllowedCharacters: .urlPathAllowed) ?? normalized
    }

    private func addAuth(to request: inout URLRequest) {
        guard !authToken.isEmpty else { return }
        request.setValue("Bearer \(authToken)", forHTTPHeaderField: "Authorization")
    }

    private func addCommonHeaders(to request: inout URLRequest) {
        request.setValue(userAgent, forHTTPHeaderField: "User-Agent")
        request.setValue("XMLHttpRequest", forHTTPHeaderField: "X-Requested-With")
    }

    private func validate(response: URLResponse, data: Data) throws {
        guard let http = response as? HTTPURLResponse else { throw APIError.requestFailed("No HTTP response") }
        guard (200...299).contains(http.statusCode) else {
            if let serverError = try? decode(ServerError.self, from: data), let message = serverError.error {
                throw APIError.requestFailed(message)
            }
            let body = String(data: data, encoding: .utf8)?
                .replacingOccurrences(of: "\n", with: " ")
                .trimmingCharacters(in: .whitespacesAndNewlines)
            let snippet = body.map { String($0.prefix(120)) }.flatMap { $0.isEmpty ? nil : $0 }
            throw APIError.requestFailed(snippet.map { "HTTP \(http.statusCode): \($0)" } ?? "HTTP \(http.statusCode)")
        }
    }

    private func validateDownload(response: URLResponse, fileURL: URL) throws {
        let prefix = try readPrefix(of: fileURL, byteCount: 512)
        try validate(response: response, data: prefix)
        guard let http = response as? HTTPURLResponse else { return }
        let body = String(data: prefix, encoding: .utf8) ?? ""
        let returnedHTML = http.mimeType?.localizedCaseInsensitiveContains("text/html") == true
            || body.localizedCaseInsensitiveContains("<!doctype html")
            || body.localizedCaseInsensitiveContains("<html")
        if returnedHTML || http.url?.path.localizedCaseInsensitiveContains("/login") == true {
            throw APIError.requestFailed("Server returned a sign-in page instead of the requested file. Sign in again.")
        }
    }

    private func readPrefix(of url: URL, byteCount: Int) throws -> Data {
        let handle = try FileHandle(forReadingFrom: url)
        defer { try? handle.close() }
        return try handle.read(upToCount: byteCount) ?? Data()
    }

    private func decode<T: Decodable>(_ type: T.Type, from data: Data) throws -> T {
        do {
            return try JSONDecoder().decode(T.self, from: data)
        } catch {
            if let body = String(data: data.prefix(240), encoding: .utf8) {
                let compactBody = body
                    .replacingOccurrences(of: "\n", with: " ")
                    .replacingOccurrences(of: "\t", with: " ")
                    .trimmingCharacters(in: .whitespacesAndNewlines)
                if compactBody.localizedCaseInsensitiveContains("<html")
                    || compactBody.localizedCaseInsensitiveContains("<!doctype html") {
                    throw APIError.requestFailed("Server returned HTML instead of JSON. Sign in again.")
                }
            }
            throw APIError.decodingFailed(error.localizedDescription)
        }
    }

}

private struct ServerError: Decodable {
    let error: String?
}

enum APIError: LocalizedError {
    case invalidURL
    case requestFailed(String)
    case decodingFailed(String)

    var errorDescription: String? {
        switch self {
        case .invalidURL:
            "Enter a valid https:// server URL."
        case .requestFailed(let message):
            message
        case .decodingFailed(let message):
            "Could not read server response: \(message)"
        }
    }
}
