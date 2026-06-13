import SwiftUI

struct SettingsView: View {
    @EnvironmentObject var connection: ConnectionManager
    @EnvironmentObject var appState: AppState

    @State private var serverURL: String = ""
    @State private var username: String = ""
    @State private var password: String = ""
    @State private var status: String = ""
    @State private var isLoading: Bool = false

    var body: some View {
        Form {
            Section("Server Connection") {
                TextField("Server URL", text: $serverURL, prompt: Text("https://home.yourdomain.com"))
                    .textFieldStyle(.roundedBorder)

                HStack {
                    Circle()
                        .fill(connection.isConnected ? .green : .red)
                        .frame(width: 8, height: 8)
                    Text(connection.isConnected ? "Connected" : "Not connected")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }
            }

            Section("Login") {
                TextField("Username", text: $username)
                    .textFieldStyle(.roundedBorder)
                SecureField("Password", text: $password)
                    .textFieldStyle(.roundedBorder)

                Button(action: login) {
                    if isLoading {
                        ProgressView()
                            .controlSize(.small)
                    } else {
                        Text("Connect & Login")
                    }
                }
                .buttonStyle(.borderedProminent)
                .disabled(serverURL.isEmpty || username.isEmpty || password.isEmpty || isLoading)

                if !status.isEmpty {
                    Text(status)
                        .font(.caption)
                        .foregroundStyle(status.contains("Error") ? .red : .green)
                }
            }

            if connection.isConnected {
                Section("Status") {
                    LabeledContent("User", value: appState.username)
                    LabeledContent("Server", value: appState.serverURL)
                    Button("Disconnect") {
                        connection.disconnect()
                        appState.authToken = ""
                        appState.isAuthenticated = false
                        status = ""
                    }
                    .foregroundStyle(.red)
                }
            }
        }
        .formStyle(.grouped)
        .frame(width: 400, height: 350)
        .onAppear {
            NSApplication.shared.setActivationPolicy(.regular)
            NSApplication.shared.activate(ignoringOtherApps: true)
            serverURL = appState.serverURL
            username = appState.username
        }
        .onDisappear {
            NSApplication.shared.setActivationPolicy(.accessory)
        }
    }

    private func login() {
        isLoading = true
        status = ""
        let url = serverURL.trimmingCharacters(in: .whitespacesAndNewlines)

        Task {
            let client = APIClient(baseURL: url)
            do {
                let response = try await client.login(username: username, password: password)
                await MainActor.run {
                    if response.ok, let data = response.data {
                        appState.serverURL = url
                        appState.authToken = data.token
                        appState.username = data.user.username
                        appState.isAuthenticated = true
                        connection.connect(serverURL: url, token: data.token)
                        status = "Connected!"
                        password = ""
                    } else {
                        status = "Error: Invalid credentials"
                    }
                    isLoading = false
                }
            } catch {
                await MainActor.run {
                    status = "Error: Cannot reach server"
                    isLoading = false
                }
            }
        }
    }
}
