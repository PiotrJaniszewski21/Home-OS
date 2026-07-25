import SwiftUI

struct HomeOSMusicSettingsView: View {
    @EnvironmentObject private var session: HomeOSMusicSession
    @State private var serverURL = ""
    @State private var localServerURL = ""
    @State private var preferLocal = true

    var body: some View {
        Form {
            Section("Connection") {
                TextField("Home OS URL", text: $serverURL)
                TextField("Local URL", text: $localServerURL)
                Toggle("Prefer local connection", isOn: $preferLocal)
                LabeledContent("Current route", value: session.connectionDescription)
            }

            Section {
                Button("Save and Reconnect") {
                    Task {
                        await session.reconnect(
                            serverURL: serverURL,
                            localServerURL: localServerURL,
                            preferLocal: preferLocal
                        )
                    }
                }
                .buttonStyle(.borderedProminent)

                Button("Sign Out", role: .destructive) {
                    session.signOut()
                }
            }

            if let error = session.errorMessage, !error.isEmpty {
                Section("Connection Error") {
                    Text(error)
                        .foregroundStyle(.red)
                }
            }
        }
        .formStyle(.grouped)
        .frame(width: 540, height: 360)
        .onAppear {
            serverURL = session.serverURL
            localServerURL = session.localServerURL
            preferLocal = session.preferLocalServer
        }
    }
}
