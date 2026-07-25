import SwiftUI

struct HomeOSMusicLoginView: View {
    @EnvironmentObject private var session: HomeOSMusicSession
    @State private var serverURL = ""
    @State private var localServerURL = ""
    @State private var preferLocal = true
    @State private var username = ""
    @State private var password = ""

    var body: some View {
        VStack(spacing: 24) {
            ZStack {
                RoundedRectangle(cornerRadius: 22, style: .continuous)
                    .fill(
                        LinearGradient(
                            colors: [.pink, .purple],
                            startPoint: .topLeading,
                            endPoint: .bottomTrailing
                        )
                    )
                Image(systemName: "music.note.house.fill")
                    .font(.system(size: 58))
                    .foregroundStyle(.white)
            }
            .frame(width: 112, height: 112)

            VStack(spacing: 6) {
                Text("HomeOS-Music")
                    .font(.largeTitle.bold())
                Text("Sign in to stream your HomeMusic library.")
                    .foregroundStyle(.secondary)
            }

            Form {
                TextField("Home OS URL", text: $serverURL, prompt: Text("https://example.com"))
                TextField("Local URL", text: $localServerURL, prompt: Text("https://192.168.1.20:4443"))
                Toggle("Prefer local connection", isOn: $preferLocal)
                TextField("Username", text: $username)
                SecureField("Password", text: $password)
            }
            .formStyle(.grouped)
            .frame(width: 480)

            if let error = session.errorMessage, !error.isEmpty {
                Text(error)
                    .font(.callout)
                    .foregroundStyle(.red)
                    .multilineTextAlignment(.center)
                    .frame(maxWidth: 480)
            }

            Button {
                Task {
                    _ = await session.signIn(
                        serverURL: serverURL,
                        localServerURL: localServerURL,
                        preferLocal: preferLocal,
                        username: username,
                        password: password
                    )
                }
            } label: {
                if session.isConnecting {
                    ProgressView()
                        .controlSize(.small)
                        .frame(width: 160)
                } else {
                    Text("Sign In")
                        .frame(width: 160)
                }
            }
            .buttonStyle(.borderedProminent)
            .tint(Color.homeOSMusicAccent)
            .disabled(
                serverURL.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
                    || username.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
                    || password.isEmpty
                    || session.isConnecting
            )
        }
        .padding(40)
        .onAppear {
            serverURL = session.serverURL
            localServerURL = session.localServerURL
            preferLocal = session.preferLocalServer
            username = session.username
        }
    }
}
