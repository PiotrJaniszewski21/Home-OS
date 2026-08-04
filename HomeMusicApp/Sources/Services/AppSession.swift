import Foundation

@MainActor
final class AppSession: ObservableObject {
    @Published private(set) var isSignedIn = false
    @Published var serverText = "https://petershomenet.co.uk"
    @Published var errorMessage: String?
    @Published var isWorking = false

    private(set) var client: APIClient?

    init() {
        if let server = CredentialStore.load(account: "server"),
           let token = CredentialStore.load(account: "token"),
           let url = Self.validServerURL(server) {
            serverText = server
            client = APIClient(baseURL: url, token: token)
            isSignedIn = true
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
            request.setValue("application/json", forHTTPHeaderField: "Content-Type")
            request.httpBody = try JSONSerialization.data(withJSONObject: [
                "username": username,
                "password": password,
            ])
            let (data, response) = try await URLSession.shared.data(for: request)
            guard let http = response as? HTTPURLResponse, http.statusCode == 200 else {
                throw APIError.http((response as? HTTPURLResponse)?.statusCode ?? 0)
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

    func signOut() {
        CredentialStore.clear()
        client = nil
        isSignedIn = false
    }

    private static func validServerURL(_ value: String) -> URL? {
        guard let url = URL(string: value.trimmingCharacters(in: .whitespacesAndNewlines)),
              url.scheme == "https", url.host != nil else { return nil }
        return url
    }
}
