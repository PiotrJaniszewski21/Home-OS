import SwiftUI

struct SettingsView: View {
    @Environment(\.dismiss) private var dismiss
    @EnvironmentObject private var session: AppSession
    @AppStorage("enableGlobalAmbientLights") private var enableGlobalAmbientLights = true

    @State private var showingPerformanceLogs = false

    var body: some View {
        NavigationStack {
            Form {
                Section(header: Text("Visual Effects")) {
                    Toggle(isOn: $enableGlobalAmbientLights) {
                        VStack(alignment: .leading, spacing: 4) {
                            Text("Global Beat Ambient Lights")
                                .font(.body.weight(.medium))
                            Text("Sync soft floating ambient lights across all app screens to the song's real beat")
                                .font(.caption)
                                .foregroundStyle(.secondary)
                        }
                    }
                    .tint(.homeMusicRed)
                }

                Section(header: Text("Diagnostics & Performance")) {
                    Button {
                        showingPerformanceLogs = true
                    } label: {
                        HStack {
                            Label("Performance & Timing Logs", systemImage: "gauge.with.dots.needle.bottom.50percent")
                            Spacer()
                            Image(systemName: "chevron.right").font(.caption).foregroundStyle(.secondary)
                        }
                    }
                }

                Section(header: Text("Account & Server")) {
                    HStack {
                        Text("Server Address")
                        Spacer()
                        Text("192.168.0.8")
                            .foregroundStyle(.secondary)
                    }
                    Button("Sign Out", role: .destructive) {
                        dismiss()
                        session.signOut()
                    }
                }
            }
            .navigationTitle("Settings")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .topBarTrailing) {
                    Button("Done") { dismiss() }
                        .fontWeight(.bold)
                }
            }
            .sheet(isPresented: $showingPerformanceLogs) {
                PerformanceLogsView()
            }
        }
    }
}
