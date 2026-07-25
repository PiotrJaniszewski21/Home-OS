import SwiftUI

struct MainWindowView: View {
    @EnvironmentObject var connection: ConnectionManager
    @EnvironmentObject var appState: AppState
    @State private var selectedTab: Tab = .files

    enum Tab {
        case files, dashboard
    }

    var body: some View {
        if !connection.isConnected {
            VStack(spacing: 16) {
                Image(systemName: "externaldrive.badge.xmark")
                    .font(.system(size: 40))
                    .foregroundStyle(.secondary)
                Text("Not connected")
                    .font(.title2)
                Text("Open Settings to connect to your server")
                    .foregroundStyle(.secondary)
                Button("Settings") {
                    NSApp.sendAction(Selector(("showSettingsWindow:")), to: nil, from: nil)
                }
                .buttonStyle(.borderedProminent)
            }
            .frame(minWidth: 600, minHeight: 400)
        } else {
            NavigationSplitView {
                List(selection: $selectedTab) {
                    Label("Files", systemImage: "folder")
                        .tag(Tab.files)
                    Label("Dashboard", systemImage: "gauge.medium")
                        .tag(Tab.dashboard)
                }
                .listStyle(.sidebar)
                .frame(minWidth: 150)
            } detail: {
                switch selectedTab {
                case .files:
                    FileBrowserView()
                case .dashboard:
                    DashboardView()
                }
            }
            .frame(minWidth: 700, minHeight: 500)
        }
    }
}
