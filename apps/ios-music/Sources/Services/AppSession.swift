import Foundation

@MainActor
final class AppSession: ObservableObject {
    @Published private(set) var isSignedIn = false
    @Published var serverText = "https://petershomenet.co.uk"
    @Published var errorMessage: String?
    @Published var isWorking = false
    @Published var connectionMessage: String?

    private(set) var client: APIClient?

    init() {
        do {
            if let server = try CredentialStore.load(account: "server"),
               let token = try CredentialStore.load(account: "token"),
               let url = Self.validServerURL(server) {
                serverText = url.absoluteString
                if server != url.absoluteString {
                    try CredentialStore.save(url.absoluteString, account: "server")
                }
                client = APIClient(baseURL: url, token: token)
                isSignedIn = true
            }
        } catch {
            errorMessage = error.localizedDescription
        }
    }

    func signIn(username: String, password: String) async {
        guard let serverURL = Self.validServerURL(serverText) else {
            errorMessage = "Enter a valid HTTPS Home OS address."
            return
        }
        isWorking = true
        defer { isWorking = false }
        do {
            var request = URLRequest(url: serverURL.appending(path: "/api/login"))
            request.httpMethod = "POST"
            request.timeoutInterval = 20
            request.setValue("application/json", forHTTPHeaderField: "Accept")
            request.setValue("application/json", forHTTPHeaderField: "Content-Type")
            request.httpBody = try JSONSerialization.data(withJSONObject: [
                "username": username.trimmingCharacters(in: .whitespacesAndNewlines),
                "password": password,
            ])
            let (data, response): (Data, URLResponse)
            do {
                (data, response) = try await NetworkSession.shared.data(for: request)
            } catch let error as URLError {
                throw APIError.network(error)
            }
            guard let http = response as? HTTPURLResponse else {
                throw APIError.invalidResponse
            }
            guard http.statusCode == 200 else {
                throw APIError.response(status: http.statusCode, data: data)
            }
            let payload = try JSONDecoder().decode(APIEnvelope<LoginPayload>.self, from: data).data
            try CredentialStore.save(serverURL.absoluteString, account: "server")
            try CredentialStore.save(payload.token, account: "token")
            client = APIClient(baseURL: serverURL, token: payload.token)
            errorMessage = nil
            isSignedIn = true
        } catch {
            errorMessage = error.localizedDescription
        }
    }

    func testConnection() async {
        guard let serverURL = Self.validServerURL(serverText) else {
            connectionMessage = "Enter a valid HTTPS Home OS address."
            return
        }
        isWorking = true
        connectionMessage = "Testing…"
        defer { isWorking = false }
        do {
            var request = URLRequest(url: serverURL.appending(path: "/health"))
            request.timeoutInterval = 15
            request.cachePolicy = .reloadIgnoringLocalAndRemoteCacheData
            let (_, response) = try await NetworkSession.shared.data(for: request)
            guard let http = response as? HTTPURLResponse else {
                throw APIError.invalidResponse
            }
            guard (200..<300).contains(http.statusCode) else {
                throw APIError.http(http.statusCode, nil)
            }
            connectionMessage = "Connected to Home OS."
        } catch let error as URLError {
            connectionMessage = APIError.network(error).localizedDescription
        } catch {
            connectionMessage = error.localizedDescription
        }
    }

    func signOut() {
        do {
            try CredentialStore.clear()
            errorMessage = nil
        } catch {
            errorMessage = error.localizedDescription
        }
        client = nil
        isSignedIn = false
    }

    private static func validServerURL(_ value: String) -> URL? {
        guard var components = URLComponents(
            string: value.trimmingCharacters(in: .whitespacesAndNewlines)
        ), components.scheme?.lowercased() == "https", components.host != nil else {
            return nil
        }
        if components.port == 4443 {
            components.port = nil
        }
        components.path = ""
        components.query = nil
        components.fragment = nil
        return components.url
    }
}
